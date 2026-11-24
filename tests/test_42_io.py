"""Native Linux socket tests and guarded 42 repair/restart tests."""
import contextlib
import ctypes
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'profiles'
FIX = ROOT / 'tests/fixtures'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guest = load('repair_guest', 'lab_mode_remote.py')
host = load('repair_host', 'lab-mode.py')


class NativeSocket(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.compile('sender', '''#include "lab42_socket.h"
int lab_send(int fd, const char *p, size_t n, int ms) {return Lab42SendAll(fd,p,n,ms);}
''', shared=True)
        cls.lib = ctypes.CDLL(str(cls.root / 'sender.so'), use_errno=True)
        cls.lib.lab_send.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
        cls.lib.lab_send.restype = ctypes.c_int
        original = (FIX / '42_TxRxIPC.c').read_text()
        a = original.index('      Success = write(Socket,Msg,MsgLen);')
        b = original.index('      read(Socket,Ack,4);', a) + len('      read(Socket,Ack,4);')
        cls.compile('old', '''#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
int main(int argc,char **argv){(void)argc;int Socket=atoi(argv[1]),Success;
char Msg[]="next-frame",Ack[4];long MsgLen=sizeof(Msg);
''' + original[a:b] + '\nreturn 0;}\n')
        cls.compile('closed', '''#include "lab42_socket.h"
#include <stdlib.h>
int main(int argc,char **argv){(void)argc;int fd=atoi(argv[1]);
if(Lab42SendAll(fd,"abc",3,500)<0){int e=errno;Lab42ReportSendFailure(fd);return e==EPIPE?0:9;}
return 8;}
''')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def compile(cls, name, source, shared=False):
        src = cls.root / (name + '.c')
        src.write_text(source)
        flags = ['-shared', '-fPIC'] if shared else []
        target = cls.root / (name + ('.so' if shared else ''))
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wshadow',
                        '-I', str(ROOT / 'scripts/lab-mode/patches'), *flags, str(src), '-o', str(target)],
                       check=True, capture_output=True)

    def fill(self, sender):
        sender.setblocking(False)
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        data = bytearray()
        while True:
            try:
                n = sender.send(b'q' * 4096)
                data.extend(b'q' * n)
            except BlockingIOError:
                return bytes(data)

    def test_original_native_branch_exits_on_temporary_backpressure(self):
        a, b = socket.socketpair()
        with a, b:
            self.fill(a)
            result = subprocess.run([str(self.root / 'old'), str(a.fileno())],
                                    pass_fds=[a.fileno()], capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'Error writing to socket', result.stdout)

    def test_real_tcp_delayed_reader_receives_every_byte_in_order(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        receiver = socket.create_connection(listener.getsockname())
        sender, _ = listener.accept()
        listener.close()
        with sender, receiver:
            prefix = self.fill(sender)
            payload = bytes(range(256)) * 1024
            expected = prefix + payload
            received, failures = bytearray(), []
            def read():
                try:
                    time.sleep(0.05)
                    receiver.settimeout(5)
                    while len(received) < len(expected):
                        block = receiver.recv(65536)
                        if not block:
                            break
                        received.extend(block)
                except Exception as exc:
                    failures.append(exc)
            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            self.assertEqual(self.lib.lab_send(sender.fileno(), payload, len(payload), 5000), 0)
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
            self.assertFalse(failures)
            self.assertEqual(bytes(received), expected)
            self.assertFalse(sender.getblocking())

    def test_permanently_full_queue_times_out(self):
        a, b = socket.socketpair()
        with a, b:
            self.fill(a)
            started = time.monotonic()
            self.assertEqual(self.lib.lab_send(a.fileno(), b'next', 4, 50), -1)
            self.assertEqual(ctypes.get_errno(), errno.ETIMEDOUT)
            self.assertLess(time.monotonic() - started, 1.0)

    def test_closed_peer_is_diagnosed_without_sigpipe_exit(self):
        a, b = socket.socketpair()
        b.close()
        with a:
            result = subprocess.run([str(self.root / 'closed'), str(a.fileno())],
                                    pass_fds=[a.fileno()], capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn(b'LAB42_SEND_V1', result.stderr)
            self.assertIn(b'errno=32', result.stderr)

    def test_partial_send_and_interruption_keep_the_unsent_suffix(self):
        self.compile('interrupted', '''#include <assert.h>
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
static unsigned calls, polls, used; static char output[64];
static ssize_t mocked_send(int fd,const void *p,size_t n,int flags){
assert(fd==8);assert(flags==MSG_NOSIGNAL);calls++;
if(calls==2){errno=EINTR;return -1;} if(calls==3){errno=EAGAIN;return -1;}
size_t count=n>3?3:n;memcpy(output+used,p,count);used+=(unsigned)count;return (ssize_t)count;}
static int mocked_poll(struct pollfd *p,nfds_t n,int ms){assert(n==1&&ms>0&&p->fd==8);polls++;p->revents=POLLOUT;return 1;}
#define send mocked_send
#define poll mocked_poll
#include "lab42_socket.h"
int main(void){const char p[]="complete ordered frame";assert(Lab42SendAll(8,p,sizeof(p),1000)==0);
assert(used==sizeof(p));assert(memcmp(p,output,sizeof(p))==0);assert(polls==1);return 0;}
'''.replace('#include <assert.h>', '#define _POSIX_C_SOURCE 200809L\n#include <assert.h>'))
        subprocess.run([str(self.root / 'interrupted')], check=True, capture_output=True)


class RepairGuest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = self.root / '.nos3/42'
        self.tree.mkdir(parents=True)
        self.item = guest.manifest()['fortytwo']
        src = self.tree / self.item['path']
        src.parent.mkdir(parents=True)
        src.write_bytes((FIX / '42_TxRxIPC.c').read_bytes())
        (self.tree / '42').write_bytes(b'\x7fELF-old-binary')
        (self.tree / '42').chmod(0o755)
        ipc = self.tree / 'NOS3InOut/Inp_IPC.txt'
        ipc.parent.mkdir()
        ipc.write_bytes((FIX / '42_Inp_IPC.txt').read_bytes())
        subprocess.run(['git', 'init', '-q', str(self.tree)], check=True)
        self.original_run = guest.run
        self.current = {name: {'Name':'/'+name, 'State':{'Running':False, 'StartedAt':'old', 'Pid':100+i},
                       'Image':'sha256:existing-image', 'Mounts':[
                       {'Type':'bind','Source':str(self.tree.parent),'Destination':str(self.tree.parent)}]}
                       for i,name in enumerate((*guest.CORE, guest.FSW, 'nos_terminal', 'nos_udp_terminal'))}
        self.calls = []
        self.failure = None
        for name, value in [('FORTYTWO', self.tree), ('STATE', self.root/'state'), ('PROFILES', P),
                            ('containers', lambda:self.current), ('run', self.fake_run)]:
            p = patch.object(guest, name, value)
            p.start()
            self.addCleanup(p.stop)

    def fake_run(self, args, **kwargs):
        args = [str(x) for x in args]
        self.calls.append(args)
        if args[:2] == ['git','-C'] and 'rev-parse' in args:
            return subprocess.CompletedProcess(args, 0, self.item['revision']+'\n', '')
        if args[:2] == ['git','-C'] and 'diff' in args:
            return subprocess.CompletedProcess(args, 0, '', '')
        if args[:2] == ['docker','run']:
            (self.tree/'42').write_bytes(b'\x7fELF-LAB42_SEND_V1-new-binary')
            if self.failure:
                if callable(self.failure):
                    self.failure()
                raise self.failure
            return subprocess.CompletedProcess(args, 0, '', '')
        if args[:2] in (['docker','stop'],['docker','start']):
            start = args[1]=='start'
            names = args[2:] if start else args[4:]
            for name in names:
                self.current[name]['State']['Running'] = start
            return subprocess.CompletedProcess(args,0,'','')
        if args[0]=='sudo' and 'ss' in args:
            ports = guest.ipc_ports(self.tree/'NOS3InOut/Inp_IPC.txt')
            data = '\n'.join(f'0 0 172.18.0.2:{port} 172.18.0.3:50000' for port in ports)
            return subprocess.CompletedProcess(args,0,data,'')
        return self.original_run(args, **kwargs)

    def test_native_patch_and_rebuild_are_guarded_and_repeatable(self):
        for i in range(2):
            result = guest.build42('20261104T160000Z-'+str(i)*8)
            self.assertEqual(result['source'],self.item['patched'])
            self.assertEqual(result['image'],'sha256:existing-image')
            self.assertTrue((Path(result['backup'])/'42').is_file())
        build = next(c for c in self.calls if c[:2]==['docker','run'])
        self.assertEqual(build[-3:],['sha256:existing-image','make','-j2'])
        self.assertNotIn('-B',build)
        self.assertIn('--cpus=2',build)
        self.assertEqual((self.tree/'42').stat().st_mode&0o777,0o755)

    def test_failure_and_hangup_restore_source_and_binary(self):
        src = self.tree/self.item['path']
        original_handler = signal.getsignal(signal.SIGHUP)
        for i,(failure,kind) in enumerate(((subprocess.CalledProcessError(2,['docker']),subprocess.CalledProcessError),
                                         (lambda:os.kill(os.getpid(),signal.SIGHUP),SystemExit))):
            original, binary = src.read_bytes(), (self.tree/'42').read_bytes()
            self.failure = failure
            with self.subTest(failure=kind.__name__), self.assertRaises(kind):
                guest.build42('20261104T160001Z-'+str(i)*8)
            self.assertEqual(src.read_bytes(),original)
            self.assertEqual((self.tree/'42').read_bytes(),binary)
            self.assertFalse((self.tree/self.item['header_target']).exists())
        self.assertEqual(signal.getsignal(signal.SIGHUP),original_handler)

    def test_unknown_source_and_running_clients_prevent_repair(self):
        src = self.tree/self.item['path']
        original = src.read_bytes()
        src.write_bytes(original+b'local edit\n')
        with self.assertRaisesRegex(RuntimeError,'Unrecognized 42 source'):guest.build42()
        self.assertFalse((self.root/'state').exists())
        src.write_bytes(original)
        self.current['sc_1_truth42sim']['State']['Running']=True
        with self.assertRaisesRegex(RuntimeError,'Stop the lab containers'):guest.build42()
        self.assertEqual(src.read_bytes(),original)

    def test_clock_starts_only_after_all_42_connections(self):
        guest.start_simulators()
        starts = [c for c in self.calls if c[:2]==['docker','start']]
        self.assertEqual(starts[0],['docker','start','sc_1_nos_engine_server'])
        self.assertEqual(starts[1],['docker','start','sc_1_fortytwo'])
        self.assertEqual(starts[-1],['docker','start','nos_time_driver'])
        self.assertFalse(self.current[guest.FSW]['State']['Running'])
        self.assertEqual(len(guest.ipc_ports(self.tree/'NOS3InOut/Inp_IPC.txt')),16)
        self.assertNotIn(6008,guest.ipc_ports(self.tree/'NOS3InOut/Inp_IPC.txt'))

    def test_clock_stays_stopped_if_a_42_connection_is_missing(self):
        def run(args,**kwargs):
            if 'ss' in args:return subprocess.CompletedProcess(args,0,'0 0 172.18.0.2:4278 172.18.0.3:50000\n','')
            return self.fake_run(args,**kwargs)
        with patch.object(guest,'run',run),patch.object(guest.time,'sleep'),patch.object(guest.time,'monotonic',side_effect=[0,1,91]):
            with self.assertRaisesRegex(RuntimeError,'did not reconnect'):guest.start_simulators()
        self.assertFalse(self.current['nos_time_driver']['State']['Running'])

    def test_stop_order_preserves_unrelated_containers(self):
        self.current['unrelated']={'State':{'Running':True}}
        for name in guest.lab_containers(self.current):
            self.current[name]['State']['Running']=name!=guest.FSW
        guest.stop_simulators()
        stops=[c for c in self.calls if c[:2]==['docker','stop']]
        self.assertEqual(stops[0][-1],'nos_time_driver')
        self.assertEqual(stops[1][-1],'sc_1_fortytwo')
        self.assertEqual(stops[-1][-1],'sc_1_nos_engine_server')
        self.assertTrue(self.current['unrelated']['State']['Running'])


class RepairWorkflow(unittest.TestCase):
    def invoke(self, fail=None, plan=False):
        self.calls=[]
        def call(role,action,*args,**kwargs):
            self.calls.append((role,action))
            if action==fail:raise RuntimeError('injected failure')
            return {}
        fake=type('Remote',(),{'call':staticmethod(call)})()
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            host.repair42(fake,plan=plan)

    def test_plan_is_read_only(self):
        self.invoke(plan=True)
        self.assertEqual(self.calls,[('sat','inspect-stopped-sims'),('sat','42-preflight'),('mc','inspect')])

    def test_repair_orders_shutdown_build_and_reconnect_without_fsw_start(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(host,'STATE',Path(temp)):
            self.invoke()
            actions=[action for _,action in self.calls]
            self.assertLess(actions.index('stop-sims'),actions.index('build-42'))
            self.assertLess(actions.index('build-42'),actions.index('start-sims'))
            self.assertNotIn('start-fsw',actions)
            self.assertNotIn('start-yamcs',actions)
            self.assertEqual(json.loads((Path(temp)/'last.json').read_text())['phase'],'simulators-ready')

    def test_failed_repair_stops_the_lab(self):
        for action in ('stop-sims','build-42','start-sims'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as temp,patch.object(host,'STATE',Path(temp)):
                with self.assertRaises(RuntimeError):self.invoke(fail=action)
                self.assertEqual(self.calls[-4:],[('sat','abort-42'),('sat','stop'),('mc','stop'),('sat','stop-sims')])


if __name__=='__main__':
    unittest.main(verbosity=2)
