import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
try:
 import yaml
except ImportError:
 yaml=None

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
P = WORK / 'profiles'
SUPPORT = WORK / 'scripts/lab-mode'

def module(name, path):
 spec=importlib.util.spec_from_file_location(name,path)
 result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
 return result
cli=module('labmode',WORK/'scripts/lab-mode.py')
remote=module('labremote',WORK/'scripts/lab_mode_remote.py')
M=json.loads((SUPPORT/'manifest.json').read_text())


def report(valid=10, packets=2, invalid=0, debug=True):
 return {'instance':'RUNNING','links':[
  {'name':'radio-in','status':'OK','extra':{'Valid frames':str(valid),'Invalid frames':str(invalid)}},
  {'name':'radio-in.vc1','status':'OK','dataInCount':str(packets)},
  {'name':'radio-out','status':'OK'}, {'name':'debug-in','disabled':debug}]}


def proof():
 return dict(fsw_running=True,output_executed=True,started='start-1',
  fortytwo_running=True,fortytwo_started='42-start',truth_running=True,truth_started='truth-start',truth=[
  'Data point: Truth 42 Data Point: UTC Year-DayOfYear-Time: 2025-293(10/20)T17:47:36.789990 Pos: -6496854, -1184535, 1516136 Vel: 2198, -4525, 5792',
  'Data point: Truth 42 Data Point: UTC Year-DayOfYear-Time: 2025-293(10/20)T17:47:36.890009 Pos: -6496656, -1184942, 1516657 Vel: 2199, -4525, 5791'])


class Health(unittest.TestCase):
 def test_valid(self):
  self.assertEqual(cli.validate_health(report(),report(980,190),proof()),dict(valid=970,invalid=0,packets=188))
 def test_no_packets(self):
  with self.assertRaises(RuntimeError):cli.validate_health(report(),report(980,2),proof())
 def test_invalid_frames(self):
  with self.assertRaises(RuntimeError):cli.validate_health(report(),report(980,190,1),proof())
 def test_debug_path_is_not_radio_proof(self):
  with self.assertRaises(RuntimeError):cli.validate_health(report(debug=False),report(980,190),proof())
 def test_nan_and_zero_and_invalid_dates(self):
  for old,new in [('2025-293','0000-293'),('-6496854, -1184535, 1516136','0, 0, 0'),('1516136','nan')]:
   bad=proof();bad['truth'][0]=bad['truth'][0].replace(old,new)
   with self.subTest(new=new),self.assertRaises(RuntimeError):cli.validate_health(report(),report(980,190),bad)
 def test_frozen_truth(self):
  bad=proof();bad['truth'][1]=bad['truth'][1].replace('36.890009','36.789990')
  with self.assertRaises(RuntimeError):cli.validate_health(report(),report(980,190),bad)
 def test_command_submission_is_not_execution(self):
  bad=proof();bad['output_executed']=False
  with self.assertRaises(RuntimeError):cli.validate_health(report(),report(980,190),bad)
 def test_stopped_42_or_adapter_is_not_accepted(self):
  for key in ('fortytwo_running','truth_running'):
   bad=proof();bad[key]=False
   with self.subTest(key=key),self.assertRaises(RuntimeError):cli.validate_health(report(),report(980,190),bad)
 def test_backwards_simulation_time_is_rejected(self):
  bad=proof();bad['truth'].reverse()
  with self.assertRaisesRegex(RuntimeError,'moved backwards'):cli.validate_health(report(),report(980,190),bad)


class Observation(unittest.TestCase):
 def sample(self,stamp):
  value=proof()
  # Two consecutive publications may carry exactly the same 42 timestamp.
  value['truth']=[line.replace('17:47:36.789990',stamp).replace('17:47:36.890009',stamp) for line in value['truth']]
  return value
 def invoke(self,samples):
  iterator=iter(samples);self.calls=[];self.count=0
  def call(role,action):
   self.calls.append((role,action))
   if action=='evidence':return next(iterator)
   self.count+=1;return report(100*self.count,20*self.count)
  fake=type('Remote',(),{'call':staticmethod(call)})()
  return cli.check(fake)
 def test_aged_out_output_event_requires_the_same_verified_fsw_start(self):
  samples = [self.sample(t) for t in ('17:47:36.100000', '17:47:46.100000')]
  for sample in samples:
   sample['output_executed'] = False
  with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'STATE', Path(temp)), patch.object(cli.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
   last = Path(temp) / 'last.json'
   for phase, start in [('checking', samples[0]['started']), ('verified', 'previous-start')]:
    last.write_text(json.dumps({'phase': phase, 'fsw_started': start}))
    with self.assertRaisesRegex(RuntimeError, 'TO_ENABLE_OUTPUT'):
     self.invoke(samples)
   last.write_text(json.dumps({'phase': 'verified', 'fsw_started': samples[0]['started']}))
   self.invoke(samples)

 def test_duplicate_adjacent_samples_pass_when_time_advances_between_observations(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)),patch.object(cli.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
   result,evidence=self.invoke([self.sample('17:47:36.100000'),self.sample('17:47:46.100000')])
   self.assertEqual(result,dict(valid=100,invalid=0,packets=20))
   self.assertNotEqual(evidence['truth'][0].split(' Pos:')[0],evidence['truth'][1].split(' Pos:')[0])
   sleep.assert_called_once_with(10)
   self.assertEqual(self.calls,[('mc','metrics'),('sat','evidence'),('mc','metrics'),('sat','evidence')])
   data=json.loads((Path(temp)/'health.json').read_text())
   self.assertEqual(data['compared_truth'],evidence['truth'])
   self.assertEqual((Path(temp)/'health.json').stat().st_mode&0o777,0o600)
 def test_buffered_logs_get_a_bounded_retry(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)),patch.object(cli.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
   self.invoke([self.sample('17:47:36.100000'),self.sample('17:47:36.100000'),self.sample('17:47:46.100000')])
   self.assertEqual(sleep.call_count,2)
 def test_truly_frozen_time_still_fails_and_keeps_the_compared_samples(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)),patch.object(cli.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaisesRegex(RuntimeError,"did not advance.*Diagnostics"):
    self.invoke([self.sample('17:47:36.100000') for _ in range(5)])
   self.assertEqual(sleep.call_count,4)
   data=json.loads((Path(temp)/'health.json').read_text())
   self.assertEqual(data['observation'],4)
   self.assertEqual(data['compared_truth'][0],data['compared_truth'][1])
 def test_restarted_42_is_rejected_even_if_time_advances(self):
  first,second=self.sample('17:47:36.100000'),self.sample('17:47:46.100000')
  second['fortytwo_started']='42-new'
  with tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)),patch.object(cli.time,'sleep'),contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaisesRegex(RuntimeError,'restarted'):self.invoke([first,second])
 def test_missing_initial_logs_require_another_observation(self):
  first=self.sample('17:47:36.100000');first['truth']=[]
  with tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)),patch.object(cli.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
   self.invoke([first,self.sample('17:47:36.100000'),self.sample('17:47:46.100000')])
   self.assertEqual(sleep.call_count,2)
 def test_missing_logs_have_a_bounded_failure_and_diagnostic(self):
  empty=self.sample('17:47:36.100000');empty['truth']=[]
  with tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)),patch.object(cli.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaisesRegex(RuntimeError,'Recent 42 data.*Diagnostics'):self.invoke([empty]*5)
   self.assertEqual(sleep.call_count,4)
   self.assertTrue((Path(temp)/'health.json').exists())
 def test_stopped_42_does_not_get_buffering_retries(self):
  first,second=self.sample('17:47:36.100000'),self.sample('17:47:36.100000')
  second['fortytwo_running']=False
  with tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)),patch.object(cli.time,'sleep') as sleep,contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaisesRegex(RuntimeError,"stopped"):self.invoke([first,second])
   self.assertEqual(sleep.call_count,1)


class Profiles(unittest.TestCase):
 @unittest.skipIf(yaml is None, 'PyYAML required for YAML schema checks')
 def test_pairs(self):
  for mode in ('clear','sdls'):
   config=yaml.safe_load((P/mode/'yamcs.nos3.yaml').read_text())
   links={v['name']:v for v in config['dataLinks']}
   self.assertEqual(links['radio-in']['initialBytesToStrip'],4)
   self.assertEqual(links['radio-in']['frameLength'],1786)
   for name in ('radio-in','radio-out'):self.assertIs(links[name]['enabledAtStartup'],True)
   self.assertIs(links['debug-in']['enabledAtStartup'],False)
   bootstrap=yaml.safe_load((P/mode/'yamcs.bootstrap.yaml').read_text())
   self.assertTrue(all(not v['enabledAtStartup'] for v in bootstrap['dataLinks'] if v['name'] in ('radio-in','radio-out','debug-in')))
   if mode=='clear':
    self.assertNotIn('encryption',links['radio-in']);self.assertNotIn('encryption',links['radio-out'])
   else:
    self.assertEqual(links['radio-in']['encryption'][0]['spi'],5)
    self.assertEqual(links['radio-out']['encryption'][0]['spi'],4)
   tool=(P/mode/'toolchain-amd64-nos3.cmake').read_text()
   self.assertIn('set(CI_TRANSPORT '+('udp_tf_clear' if mode=='clear' else 'udp_tf')+')',tool)
   self.assertIn('-DLAB_'+mode.upper()+'_MODE',tool)
 def test_profile_hashes(self):
  with patch.object(remote,'PROFILES',P):remote.manifest()
 def patched(self,item):
  source={'to-clear.patch':FIXTURES/'to_custom.c','io-clear.patch':FIXTURES/'tm_sdlp.c','tc-strict.patch':FIXTURES/'crypto_tc.c','fsw-once.patch':FIXTURES/'fsw_respawn.sh'}[item['patch']]
  with tempfile.TemporaryDirectory() as directory:
   dest=Path(directory)/item['path'];dest.parent.mkdir(parents=True);dest.write_bytes(source.read_bytes())
   self.assertEqual(remote.digest(dest),item['base'])
   subprocess.run(['git','init','-q',directory],check=True)
   subprocess.run(['git','-C',directory,'apply','--check',str(SUPPORT/'patches'/item['patch'])],check=True)
   subprocess.run(['git','-C',directory,'apply',str(SUPPORT/'patches'/item['patch'])],check=True)
   self.assertEqual(remote.digest(dest),item['patched'])
   subprocess.run(['git','-C',directory,'apply','--reverse','--check',str(SUPPORT/'patches'/item['patch'])],check=True)
   return dest.read_text()
 def test_all_native_patches_apply_to_pinned_sources(self):
  for item in M['patches']:
   with self.subTest(item=item['repo']):self.patched(item)
 def compile(self,code,defines,flags=()):
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'test.c';exe=Path(d)/'test';src.write_text(code)
   subprocess.run(['cc','-std=c99',*flags,'-I'+str(Path(__file__).parent),*['-D'+x for x in defines],str(src),'-o',str(exe)],check=True,capture_output=True)
   subprocess.run([str(exe)],check=True,capture_output=True)
 def test_strict_tc_guard_real_function(self):
  text=self.patched(M['patches'][2])
  function=text[text.index('uint32_t Crypto_TC_Sanity_Validations('):text.index('/**\n * @brief Function: Crypto_TC_Get_Ciper_Mode_TCP')]
  code='''#include <stdint.h>
#include <assert.h>
#define CRYPTO_LIB_SUCCESS 0
#define CRYPTO_LIB_ERR_SA_NOT_OPERATIONAL -57
typedef struct {} SecurityAssociation_t;
typedef struct {struct {unsigned spi;} tc_sec_header;} TC_t;
static int calls;
static int get(unsigned spi,SecurityAssociation_t **sa){calls++;return 0;}
static int crypto_tc_validate_sa(SecurityAssociation_t *sa){return 0;}
static void log_error(unsigned v){}
static struct {int (*sa_get_from_spi)(unsigned,SecurityAssociation_t **);} sa_struct={get},*sa_if=&sa_struct;
static struct {void (*mc_log)(unsigned);} mc_struct={log_error},*mc_if=&mc_struct;
'''+function+'''
int main(void){TC_t tc;SecurityAssociation_t *sa=0;for(int i=0;i<64;i++){tc.tc_sec_header.spi=i;calls=0;int value=(int)Crypto_TC_Sanity_Validations(&tc,&sa);
#ifdef LAB_SDLS_MODE
assert(value==(i==4?0:-57));assert(calls==(i==4?1:0));
#else
assert(value==0);assert(calls==1);
#endif
}return 0;}
'''
  self.compile(code,['LAB_SDLS_MODE']);self.compile(code,['LAB_CLEAR_MODE'])
 def test_fsw_launcher_runs_once_and_returns_core_exit_status(self):
  text=self.patched(M['patches'][3])
  with tempfile.TemporaryDirectory() as directory:
   d=Path(directory);launch=d/'scripts/fsw/fsw_respawn.sh';launch.parent.mkdir(parents=True)
   marker=d/'once';marker.write_text('once')
   launch.write_text(text.replace('/home/vagrant/sdls-secrets/lab-mode-fsw-once',str(marker)))
   binary=d/'bin/core-cpu1';binary.parent.mkdir()
   binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "'+str(d/'calls')+'"\nexit 7\n');binary.chmod(0o755)
   (d/'scripts/env.sh').write_text('FSW_DIR="'+str(binary.parent)+'"\n')
   result=subprocess.run(['bash',str(launch)],timeout=2)
   self.assertEqual(result.returncode,7)
   self.assertEqual((d/'calls').read_text().splitlines(),['-R PO'])
 def test_clear_frame_layout_real_io_fragment(self):
  text=self.patched(M['patches'][1])
  part=text[text.index('#ifndef LAB_CLEAR_MODE\n    // Need SA'):text.index('    if (pChannelConfig->ocfFlag == true)')]
  code='''#include <stdint.h>
#include <stdio.h>
#include <assert.h>
#define KRED ""
#define RESET ""
#define CRYPTO_LIB_SUCCESS 0
#define CRYPTO_LIB_ERR_NO_INIT -1
typedef uint16_t uint16;
static int calls;
static struct {int scId;} global={3},*pGlobalConfig=&global;
static struct {int vcId;} channel={1},*pChannelConfig=&channel;
static int get(int a,uint16 b,uint16 c,int d,void **p){calls++;*p=(void*)1;return 0;}
static struct {int (*sa_get_operational_sa_from_gvcid)(int,uint16,uint16,int,void**);} sa_struct={get},*sa_if=&sa_struct;
static int Crypto_Get_Security_Header_Length(void*p){return 14;}
static int Crypto_Get_Security_Trailer_Length(void*p){return 16;}
int main(void){int iStatus=0,dataFieldLength=1786,dataFieldOffset=6,sdlsSecurityHeaderLength=0,sdlsSecurityTrailerLength=0;void *sa_ptr=0;
'''+part+'''
#ifdef LAB_CLEAR_MODE
assert(dataFieldOffset==6);assert(dataFieldLength==1780);assert(calls==0);
#else
assert(dataFieldOffset==20);assert(dataFieldLength==1750);assert(calls==1);
#endif
return 0;end_of_function:return 9;}
'''
  self.compile(code,['LAB_CLEAR_MODE']);self.compile(code,['LAB_SDLS_MODE'])
 def test_full_io_init_with_unused_warnings_as_errors(self):
  text=self.patched(M['patches'][1])
  start=text.index('int32 TM_SDLP_InitChannel(')
  function=text[start:text.index('\n}\n',start)+3]
  code='#include "io_init_stubs.h"\n'+function+'''
int main(void) {
    TM_SDLP_FrameInfo_t frame = {0};
    TM_SDLP_GlobalConfig_t global = {.frameLength=1786, .scId=3, .hasErrCtrl=false};
    TM_SDLP_ChannelConfig_t channel = {.secHdrLength=0, .fshFlag=false, .ocfFlag=false, .vcId=1, .overflowSize=64, .dataType=0};
    uint8 data[1786], overflow[64];
    assert(TM_SDLP_InitChannel(&frame, data, overflow, &global, &channel)==0);
#ifdef LAB_CLEAR_MODE
    assert(frame.dataFieldOffset==6 && frame.dataFieldLength==1780 && test_sa_calls==0);
#else
    assert(frame.dataFieldOffset==20 && frame.dataFieldLength==1750 && test_sa_calls==1);
#endif
    assert(frame.isInitialized);
    return 0;
}
'''
  for mode in ('LAB_CLEAR_MODE','LAB_SDLS_MODE'):
   for debug in ([],['TM_DEBUG']):
    with self.subTest(mode=mode,debug=bool(debug)):
     self.compile(code,[mode,*debug],flags=('-Wall','-Wextra','-Werror'))
  # The same full-function harness must catch the original v1 defect.
  with tempfile.TemporaryDirectory() as directory:
   old=Path(directory)/M['patches'][1]['path'];old.parent.mkdir(parents=True)
   old.write_bytes((FIXTURES/'tm_sdlp.c').read_bytes())
   subprocess.run(['git','init','-q',directory],check=True)
   subprocess.run(['git','-C',directory,'apply',str(FIXTURES/'io-clear-v1.patch')],check=True)
   legacy=old.read_text();start=legacy.index('int32 TM_SDLP_InitChannel(')
   legacy_function=legacy[start:legacy.index('\n}\n',start)+3]
   with self.assertRaises(subprocess.CalledProcessError) as caught:
    self.compile(code.replace(function,legacy_function),['LAB_CLEAR_MODE'],flags=('-Wall','-Wextra','-Werror'))
   for variable in (b'sa_ptr',b'sdlsSecurityHeaderLength',b'sdlsSecurityTrailerLength'):
    self.assertIn(variable,caught.exception.stderr)
 def test_io_upgrade_from_v1(self):
  item=M['patches'][1]
  with tempfile.TemporaryDirectory() as directory:
   dest=Path(directory)/item['path'];dest.parent.mkdir(parents=True);dest.write_bytes((FIXTURES/'tm_sdlp.c').read_bytes())
   subprocess.run(['git','init','-q',directory],check=True)
   subprocess.run(['git','-C',directory,'apply',str(FIXTURES/'io-clear-v1.patch')],check=True)
   self.assertIn(remote.digest(dest),item['previous'])
   patchfile=SUPPORT/'patches'/item['previous'][remote.digest(dest)]
   subprocess.run(['git','-C',directory,'apply',str(patchfile)],check=True)
   self.assertEqual(remote.digest(dest),item['patched'])
 def test_clear_to_bypasses_crypto(self):
  text=self.patched(M['patches'][0]);a=text.index('#ifndef LAB_CLEAR_MODE\n    /* Perform SDLS */');b=text.index('    /* Synchronize frame into CADU */',a)
  code='''#include <stdint.h>
#include <assert.h>
#define TO_SUCCESS 0
static int calls;
static int Crypto_TM_ApplySecurity(uint8_t*p,int len){calls++;return 0;}
int main(void){int iStatus=0;struct {uint8_t frame[1786];} frame,*pFrameInfo=&frame;
'''+text[a:b]+'''
#ifdef LAB_CLEAR_MODE
assert(calls==0);
#else
assert(calls==1);
#endif
return 0;end_of_function:return 9;}
'''
  self.compile(code,['LAB_CLEAR_MODE']);self.compile(code,['LAB_SDLS_MODE'])


class FakeRemote:
 def __init__(self,fail=None):self.calls=[];self.fail=fail;self.keys=0;self.active='sdls';self.metrics=0;self.observations=0
 def call(self,role,action,*args,stream=False):
  self.calls.append((role,action,*args))
  if action==self.fail:raise RuntimeError('injected failure')
  if action=='configure':self.active=args[0]
  if action=='keys':self.keys+=1;return {'session':f'/home/vagrant/sdls-secrets/run-test{self.keys}'}
  if action=='reset-session':assert self.keys>0
  if action=='metrics':self.metrics+=1;return report(100*self.metrics,20*self.metrics)
  if action=='evidence':
   self.observations+=1;value=proof()
   value['truth']=[line.replace('17:47:',f'17:{47+self.observations:02}:') for line in value['truth']]
   return value
  if action=='record':return {'config_hash':role+'hash','verified_at':'now'}
  return {}
 def transfer_keys(self):self.calls.append(('host','transfer'))


class Status(unittest.TestCase):
 def test_stopped_simulator_invalidates_the_verified_session(self):
  sat = {'containers': {'sc_1_nos_fsw': {'started': 'start', 'running': True}},
         'config_hash': 'sat-hash', 'stopped_simulators': []}
  mc = {'service': 'active', 'config_hash': 'mc-hash'}
  last = {'phase': 'verified', 'fsw_started': 'start',
          'sat_config': 'sat-hash', 'mc_config': 'mc-hash'}
  with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'STATE', Path(temp)):
   (Path(temp) / 'last.json').write_text(json.dumps(last))
   for stopped in ([], ['sc_1_fortytwo']):
    sat['stopped_simulators'] = stopped
    output = io.StringIO()
    guest = Mock()
    guest.call.side_effect = [sat, mc]
    with contextlib.redirect_stdout(output):
     cli.status(guest, as_json=True)
    self.assertEqual(guest.call.call_args_list[0].args, ('sat', 'inspect-stopped-sims'))
    self.assertEqual(json.loads(output.getvalue())['last_verified_session_still_running'], not stopped)


class Workflow(unittest.TestCase):
 def invoke(self,remote,mode,plan=False):
  with patch.object(cli.time,'sleep'),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
   cli.switch(remote,mode,plan=plan)
 def test_full_round_trip_new_keys_only_for_sdls(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)):
   fake=FakeRemote()
   for mode in ('sdls','clear','sdls'):self.invoke(fake,mode)
   self.assertEqual(fake.keys,2)
   self.assertEqual(len([x for x in fake.calls if x[1]=='reset-session']),2)
   self.assertEqual(fake.active,'sdls')
   self.assertEqual(json.loads((Path(temp)/'last.json').read_text())['phase'],'verified')
   for i,call in enumerate(fake.calls):
    if call[1]=='configure':
     prior=fake.calls[:i];self.assertIn(('mc','stop'),prior);self.assertIn(('sat','stop'),prior)
    if call[1]=='enable-output':
     self.assertEqual(fake.calls[i-1],('sat','start-fsw'))
 def test_plan_does_not_mutate(self):
  fake=FakeRemote();self.invoke(fake,'clear',plan=True)
  self.assertEqual(fake.calls,[('sat','inspect'),('mc','inspect')])
 def test_failure_stops_both_and_records_failed_phase(self):
  for action in ('configure','keys','build','start-yamcs','reset-session','publish-yaml','enable-links','start-fsw','enable-output','metrics'):
   with self.subTest(action=action),tempfile.TemporaryDirectory() as temp,patch.object(cli,'STATE',Path(temp)):
    fake=FakeRemote(action)
    with self.assertRaises(RuntimeError):self.invoke(fake,'sdls')
    self.assertEqual(fake.calls[-2:],[('sat','stop'),('mc','stop')])
    self.assertEqual(json.loads((Path(temp)/'last.json').read_text())['phase'],'failed')


class RadioReconnect(unittest.TestCase):
 def container(self,name,pid,started,ip):
  return {'Name':'/'+name,'State':{'Running':True,'Pid':pid,'StartedAt':started},
          'NetworkSettings':{'Networks':{'sc_1':{'IPAddress':ip}}}}
 def setUp(self):
  self.fsw=self.container(remote.FSW,101,'fsw-new','172.18.0.19')
  self.radio=self.container(remote.RADIO,202,'radio-new','172.18.0.20')
  self.current={remote.FSW:self.fsw,remote.RADIO:self.radio}
 def test_fsw_receiver_precedes_radio_restart(self):
  calls=[];polls=iter([set(),{5010}])
  def ports(c):
   calls.append('fsw-socket-check');return next(polls)
  def reconnect(c):
   calls.append('reconnect-radio');return {'radio_started':'radio-new'}
  with patch.object(remote,'run',side_effect=lambda args,**kw:calls.append(tuple(args))),patch.object(remote,'containers',return_value=self.current),patch.object(remote,'udp_ports',side_effect=ports),patch.object(remote,'reconnect_radio',side_effect=reconnect),patch.object(remote.time,'sleep'):
   result=remote.start_fsw()
  self.assertEqual(calls,[('docker','start',remote.FSW),'fsw-socket-check','fsw-socket-check','reconnect-radio'])
  self.assertEqual(result,{'fsw_started':'fsw-new','radio_started':'radio-new'})
 def test_radio_is_not_restarted_before_fsw_receiver(self):
  with patch.object(remote,'udp_ports',return_value=set()),patch.object(remote,'run') as run:
   with self.assertRaisesRegex(RuntimeError,'5010'):remote.reconnect_radio(self.fsw)
   run.assert_not_called()
 def test_waits_for_radio_ports_and_current_dns(self):
  calls=[];ports=iter([{5010},{5011,5014,7011,7012},{8010,5011,5014,7011,7012},{8010,5011,5014,7011,7012}])
  answers=iter(['172.18.0.8 STREAM nos-fsw\n','172.18.0.19 STREAM nos-fsw\n172.18.0.19 DGRAM\n'])
  def run(args,**kwargs):
   calls.append(args)
   return subprocess.CompletedProcess(args,0,next(answers) if args[:2]==['docker','exec'] else '', '')
  with patch.object(remote,'containers',return_value=self.current),patch.object(remote,'udp_ports',side_effect=lambda c:next(ports)),patch.object(remote,'run',side_effect=run),patch.object(remote.time,'sleep') as sleep:
   result=remote.reconnect_radio(self.fsw)
  self.assertEqual(calls[0],['docker','restart','-t','10',remote.RADIO])
  self.assertEqual(sum(c[:2]==['docker','exec'] for c in calls),2)
  self.assertEqual(sleep.call_count,2)
  self.assertEqual(result,{'radio_started':'radio-new','fsw_addresses':['172.18.0.19']})
 def test_fsw_restart_during_radio_start_is_refused(self):
  replacement=self.container(remote.FSW,103,'unexpected-restart','172.18.0.19')
  with patch.object(remote,'udp_ports',return_value={5010}),patch.object(remote,'run'),patch.object(remote,'containers',return_value={**self.current,remote.FSW:replacement}):
   with self.assertRaisesRegex(RuntimeError,'FSW.*restarted'):remote.reconnect_radio(self.fsw)
 def test_missing_listener_or_wrong_dns_cannot_pass(self):
  for ready in (False,True):
   ports=iter([{5010},{8010,5011,5014,7011,7012} if ready else {5014}])
   with self.subTest(ready=ready),patch.object(remote,'udp_ports',side_effect=lambda c:next(ports)),patch.object(remote,'containers',return_value=self.current),patch.object(remote,'run',return_value=subprocess.CompletedProcess([],0,'172.18.0.8 STREAM nos-fsw\n','')),patch.object(remote.time,'sleep'),patch.object(remote.time,'monotonic',side_effect=[0,1,91]):
    with self.assertRaisesRegex(RuntimeError,'Radio unavailable'):remote.reconnect_radio(self.fsw)
 def test_ports_require_running_container_and_successful_nsenter(self):
  with patch.object(remote,'run',return_value=subprocess.CompletedProcess([],0,'UNCONN 0 0 172.18.0.19:5010 0.0.0.0:*\n','')) as run:
   self.assertEqual(remote.udp_ports(self.fsw),{5010})
   self.assertEqual(run.call_args.args[0],['sudo','-n','nsenter','-t','101','-n','ss','-lun'])
   run.return_value.returncode=1
   self.assertEqual(remote.udp_ports(self.fsw),set())
   self.fsw['State']['Running']=False
   self.assertEqual(remote.udp_ports(self.fsw),set())
   self.assertEqual(run.call_count,2)


class GuestConfiguration(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.nos=self.root/'nos3';self.yam=self.root/'yamcs'
  self.nos.mkdir();self.yam.mkdir()
  self.original_run=remote.run
  originals={'to-clear.patch':FIXTURES/'to_custom.c','io-clear.patch':FIXTURES/'tm_sdlp.c','tc-strict.patch':FIXTURES/'crypto_tc.c','fsw-once.patch':FIXTURES/'fsw_respawn.sh'}
  for item in M['patches']:
   repo=self.nos/item['repo'];dest=repo/item['path'];dest.parent.mkdir(parents=True,exist_ok=True)
   dest.write_bytes(originals[item['patch']].read_bytes());subprocess.run(['git','init','-q',str(repo)],check=True)
  test_manifest=json.loads(json.dumps(M))
  for name,key in (('src/core/crypto_config.c','crypto_config'),('src/sa/internal/sa_interface_inmemory.template.c','crypto_sa'),('src/key/internal/key_interface_internal.template.c','key_template')):
   f=self.nos/'components/cryptolib'/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(name+' fixture\n')
   test_manifest[key]=remote.digest(f)
  p=patch.object(remote,'manifest',lambda:test_manifest);p.start();self.addCleanup(p.stop)
  f=self.nos/remote.TOOL;f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes((P/'sdls/toolchain-amd64-nos3.cmake').read_bytes())
  f=self.nos/'fsw/build/amd64-nos3/default_cpu1/CMakeCache.txt';f.parent.mkdir(parents=True);f.write_text('SA_FILE:BOOL=OFF\nSA_INTERNAL:BOOL=ON\n')
  f=self.yam/remote.YAML;f.parent.mkdir(parents=True);f.write_bytes((P/'sdls/yamcs.nos3.yaml').read_bytes())
  f=self.yam/'yamcs-core/src/main/java/org/yamcs/security/sdls/SdlsMemento.java';f.parent.mkdir(parents=True);f.write_text('return new HashSet<>(linkSpiToSeqNum.keySet());')
  for name,value in [('NOS3',self.nos),('YAMCS',self.yam),('STATE',self.root/'state'),('PROFILES',P),('KEYS',self.root/'keys')]:
   p=patch.object(remote,name,value);p.start();self.addCleanup(p.stop)
  p=patch.object(remote,'run',self.fake_run);p.start();self.addCleanup(p.stop)
 def fake_run(self,args,**kwargs):
  args=[str(a) for a in args]
  if args[0]=='git' and 'rev-parse' in args:
   repo=args[2];value=M['yamcs_revision'] if repo==str(self.yam) else M['nos3_revision']
   for item in M['patches']:
    if repo==str(self.nos/item['repo']):value=item['revision']
   return subprocess.CompletedProcess(args,0,value+'\n','')
  if args[:2]==['ip','route']:
   return subprocess.CompletedProcess(args,0,'via 192.168.20.30 via 192.168.10.30\n','')
  if args[0]=='systemctl':return subprocess.CompletedProcess(args,3,'inactive\n','')
  if args[:2]==['docker','ps']:return subprocess.CompletedProcess(args,0,'id\n','')
  if args[:2]==['docker','inspect']:
   items=[dict(Name='/'+n,State=dict(Running=True,StartedAt='start',Pid=1),Mounts=[]) for n in remote.CORE]
   items.append(dict(Name='/'+remote.FSW,State=dict(Running=False,StartedAt='old',Pid=0),Mounts=[dict(Type='bind',Source=str(self.nos),Destination=str(self.nos)),dict(Type='bind',Source=str(self.root/'keys'),Destination=str(self.root/'keys'))],HostConfig={'RestartPolicy':{'Name':'no'}}))
   return subprocess.CompletedProcess(args,0,json.dumps(items),'')
  return self.original_run(args,**kwargs)
 def test_guest_files_round_trip_and_patches_idempotent(self):
  for i,mode in enumerate(('clear','sdls','clear')):
   with patch.object(remote,'STATE',self.root/'state-sat'):remote.configure('sat',mode,'transaction'+str(i))
   with patch.object(remote,'STATE',self.root/'state-mc'):remote.configure('mc',mode,'transaction'+str(i))
   self.assertEqual((self.nos/remote.TOOL).read_bytes(),(P/mode/'toolchain-amd64-nos3.cmake').read_bytes())
   self.assertEqual((self.yam/remote.YAML).read_bytes(),(P/mode/'yamcs.bootstrap.yaml').read_bytes())
   for item in M['patches']:self.assertEqual(remote.digest(self.nos/item['repo']/item['path']),item['patched'])
 def test_configure_migrates_existing_v1_io_source(self):
  item=M['patches'][1];repo=self.nos/item['repo'];dest=repo/item['path']
  subprocess.run(['git','-C',str(repo),'apply',str(FIXTURES/'io-clear-v1.patch')],check=True)
  self.assertIn(remote.digest(dest),item['previous'])
  remote.configure('sat','clear','io-upgrade')
  self.assertEqual(remote.digest(dest),item['patched'])
 def test_unknown_source_is_preserved_before_any_change(self):
  p=self.nos/M['patches'][0]['repo']/M['patches'][0]['path'];p.write_text('custom local change')
  with self.assertRaises(RuntimeError):remote.configure('sat','clear','test')
  self.assertEqual(p.read_text(),'custom local change');self.assertFalse((self.root/'state').exists())
 def test_sa_file_on_is_refused(self):
  (self.nos/'fsw/build/amd64-nos3/default_cpu1/CMakeCache.txt').write_text('SA_FILE:BOOL=ON\nSA_INTERNAL:BOOL=ON')
  with self.assertRaises(RuntimeError):remote.inspect('sat')


class NewSession(unittest.TestCase):
 def test_reset_targets_only_sdls_and_cannot_reuse_pair(self):
  import base64
  with tempfile.TemporaryDirectory() as temp:
   keys=Path(temp);session=keys/'run-test';session.mkdir()
   for name in ('tc','tm'):(session/(name+'.key')).write_bytes(bytes(32))
   (session/'lab-mode-new-session').write_text('fresh')
   (keys/'current').symlink_to(session)
   calls=[]
   def api(path,method='GET',body=None):
    calls.append((path,method,body))
    if path=='links/nos3':return {'links':[{'name':n,'disabled':True} for n in ('radio-in','radio-out')]}
    if path=='sdls/nos3/radio-out/4':return {'seq':base64.b64encode((1).to_bytes(12,'big')).decode()}
    if path=='sdls/nos3/radio-in/5':return {'seq':base64.b64encode(bytes(12)).decode()}
    return {}
   with patch.object(remote,'KEYS',keys),patch.object(remote,'api',api),patch.object(remote,'wait_instance'):
    remote.reset_session(str(session))
    deletes=[c for c in calls if c[0]=='archive/nos3:executeSql']
    self.assertEqual(deletes[0][2]['statement'],'delete from memento where "key" = \'yamcs.sdls\'')
    before=len(calls)
    with self.assertRaises(RuntimeError):remote.reset_session(str(session))
    self.assertEqual(len(calls),before)
 def test_failed_initialization_consumes_pair_without_marking_it_ready(self):
  with tempfile.TemporaryDirectory() as temp:
   keys = Path(temp)
   session = keys / 'run-test'
   session.mkdir()
   (keys / 'current').symlink_to(session)
   for name in ('tc', 'tm'):
    (session / (name + '.key')).write_bytes(bytes(32))
   (session / 'lab-mode-new-session').touch()
   links = {'links': [{'name': n, 'disabled': True} for n in ('radio-in', 'radio-out')]}
   with patch.object(remote, 'KEYS', keys), patch.object(remote, 'api',
        side_effect=[links, RuntimeError('Yamcs stopped responding')]) as api:
    with self.assertRaisesRegex(RuntimeError, 'Yamcs stopped responding'):
     remote.reset_session(str(session))
    self.assertFalse((session / 'lab-mode-initialized').exists())
    self.assertFalse((session / 'lab-mode-new-session').exists())
    with self.assertRaises(RuntimeError):
     remote.reset_session(str(session))
    self.assertEqual(api.call_count, 2)

 def test_old_or_unknown_pair_is_refused_without_api_call(self):
  with tempfile.TemporaryDirectory() as temp:
   keys=Path(temp);session=keys/'run-old';session.mkdir()
   for name in ('tc','tm'):(session/(name+'.key')).write_bytes(bytes(32))
   (keys/'current').symlink_to(session)
   with patch.object(remote,'KEYS',keys),patch.object(remote,'api') as api:
    with self.assertRaises(RuntimeError):remote.reset_session(str(session))
    api.assert_not_called()


class KeyBuild(unittest.TestCase):
 def test_restores_source_on_success_failure_and_hangup(self):
  import os, runpy, signal
  from pathlib import Path as RealPath
  script=WORK/'scripts/sdls/build_fsw_with_keys.py'
  for outcome in ('success','failure','hangup'):
   with self.subTest(outcome=outcome),tempfile.TemporaryDirectory() as directory:
    root=RealPath(directory);nos=root/'nos3';keys=root/'keys';keys.mkdir()
    for name,value in (('tc',b'\x73'*32),('tm',b'\xa9'*32)):(keys/(name+'.key')).write_bytes(value)
    source=nos/'components/cryptolib/src/key/internal/key_interface_internal.template.c';source.parent.mkdir(parents=True)
    original='\n'.join([f'key_ring[{slot}].value[{i}] = 0x00;' for slot in (4,9) for i in range(32)]+[f'key_ring[{slot}].{field} = {value};' for slot in (4,9) for field,value in (('key_len','32'),('key_state','KEY_ACTIVE'))]).encode()
    source.write_bytes(original);source.chmod(0o644);before=source.stat()
    config=nos/'cfg/build/nos3_defs/toolchain-amd64-nos3.cmake';config.parent.mkdir(parents=True);config.write_text('set(CI_TRANSPORT udp_tf)\n')
    def path(value):
     return nos if str(value)=='/home/vagrant/nos3' else keys if str(value)=='/home/vagrant/sdls-secrets/current' else RealPath(value)
    calls=[]
    def execute(args,**kwargs):
     calls.append(args)
     if args[0]=='script':
      self.assertIn(b'0x73',source.read_bytes());self.assertEqual(source.stat().st_mode&0o777,0o600)
      if outcome=='failure':raise subprocess.CalledProcessError(1,args)
      if outcome=='hangup':signal.getsignal(signal.SIGHUP)(signal.SIGHUP,None)
     return subprocess.CompletedProcess(args,0)
    handlers={sig:signal.getsignal(sig) for sig in (signal.SIGHUP,signal.SIGTERM)}
    try:
     with patch('pathlib.Path',path),patch('subprocess.run',execute),contextlib.redirect_stdout(io.StringIO()):
      if outcome=='success':runpy.run_path(str(script),run_name='__main__')
      else:
       with self.assertRaises((subprocess.CalledProcessError,SystemExit)):runpy.run_path(str(script),run_name='__main__')
    finally:
     for sig,handler in handlers.items():signal.signal(sig,handler)
    self.assertEqual(source.read_bytes(),original)
    self.assertEqual(source.stat().st_mtime_ns,before.st_mtime_ns)
    self.assertEqual(source.stat().st_mode&0o777,0o644)
    self.assertEqual(any(c[0]=='docker' for c in calls),outcome!='success')


class FinalConfiguration(unittest.TestCase):
 def test_clear_runtime_config_is_published_after_bootstrap(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);target=root/remote.YAML;target.parent.mkdir(parents=True)
   target.write_bytes((P/'clear/yamcs.bootstrap.yaml').read_bytes())
   with patch.object(remote,'YAMCS',root),patch.object(remote,'PROFILES',P):
    remote.publish_yaml('clear')
    self.assertEqual(target.read_bytes(),(P/'clear/yamcs.nos3.yaml').read_bytes())
 def test_sdls_runtime_config_requires_initialized_new_keys(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);target=root/remote.YAML;target.parent.mkdir(parents=True)
   target.write_bytes((P/'sdls/yamcs.bootstrap.yaml').read_bytes())
   keys=root/'keys';keys.mkdir();(keys/'current').symlink_to(keys)
   with patch.object(remote,'YAMCS',root),patch.object(remote,'PROFILES',P),patch.object(remote,'KEYS',keys):
    with self.assertRaises(RuntimeError):remote.publish_yaml('sdls')
    (keys/'lab-mode-initialized').write_text('used')
    remote.publish_yaml('sdls')
    self.assertEqual(target.read_bytes(),(P/'sdls/yamcs.nos3.yaml').read_bytes())


class BuildLogs(unittest.TestCase):
 def test_logs_keep_output_and_failure_status(self):
  import sys
  for code in (0,7):
   with self.subTest(code=code),tempfile.TemporaryDirectory() as directory,patch.object(cli,'STATE',Path(directory)):
    raw=io.BytesIO();console=io.TextIOWrapper(raw,encoding='utf-8')
    command=[sys.executable,'-c',"import sys;print('actual compiler diagnostic',flush=True);sys.exit("+str(code)+")"]
    with contextlib.redirect_stdout(console):
     if code:
      with self.assertRaises(subprocess.CalledProcessError) as caught:cli.stream_command(command)
      self.assertEqual(caught.exception.returncode,code)
     else:cli.stream_command(command)
    logs=list(Path(directory).glob('build-*.log'));self.assertEqual(len(logs),1)
    self.assertEqual(logs[0].read_bytes(),b'actual compiler diagnostic\n')
    self.assertEqual(logs[0].stat().st_mode&0o777,0o600)
    console.flush();self.assertIn(b'actual compiler diagnostic',raw.getvalue())
    console.detach()

def load_tests(loader, tests, pattern):
 tests.addTests(loader.loadTestsFromModule(module('native_42_tests',Path(__file__).with_name('test_42_io.py'))))
 return tests

if __name__=='__main__':unittest.main(verbosity=2)
