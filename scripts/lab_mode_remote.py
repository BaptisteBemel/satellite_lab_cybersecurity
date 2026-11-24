#!/usr/bin/env python3
"""Guest-side operations for this pinned NOS3/Yamcs laboratory. Python stdlib only."""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
PROFILES = PROJECT / "profiles"
MODE_SUPPORT = PROJECT / "scripts/lab-mode"
NOS3 = Path("/home/vagrant/nos3")
YAMCS = Path("/home/vagrant/yamcs-nos3")
STATE = Path("/home/vagrant/.satlab-mode")
KEYS = Path("/home/vagrant/sdls-secrets")
TOOL = Path("cfg/nos3_defs/toolchain-amd64-nos3.cmake")
YAML = Path("nos3/src/main/yamcs/etc/yamcs.nos3.yaml")
FSW = "sc_1_nos_fsw"
RADIO = "sc_1_radio_sim"
FORTYTWO = Path("/home/vagrant/.nos3/42")
CORE = ("sc_1_fortytwo", "sc_1_nos_engine_server", "sc_1_truth42sim",
        "sc_1_radio_sim", "nos_time_driver")


def run(args, *, check=True, capture=True, cwd=None):
    return subprocess.run([str(a) for a in args], check=check, cwd=cwd,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, text=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".lab-mode-tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def metadata(path, value):
    atomic(path, (json.dumps(value, indent=2) + "\n").encode())


def manifest():
    data = json.loads((MODE_SUPPORT / "manifest.json").read_text())
    for name, expected in data["files"].items():
        if digest(PROJECT / name) != expected:
            raise RuntimeError("Profile file differs from its recorded digest: " + name)
    return data


def api(path, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request("http://127.0.0.1:8090/api/" + path,
        data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def wait_instance(wanted="RUNNING", seconds=120):
    deadline = time.monotonic() + seconds
    last = "API unavailable"
    while time.monotonic() < deadline:
        try:
            last = api("instances/nos3").get("state")
            if last == wanted:
                return
            if last == "FAILED":
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(2)
    raise RuntimeError("Yamcs instance state: " + str(last))


def containers():
    ids = run(["docker", "ps", "-aq"]).stdout.split()
    return {c["Name"].lstrip("/"): c for c in
            json.loads(run(["docker", "inspect", *ids]).stdout)} if ids else {}


def yaml_mode():
    value = (YAMCS / YAML).read_text()
    if "encryptionSpi: 4" in value and "encryptionSpis:" in value:
        return "sdls"
    if not re.search(r"^\s*encryption:", value, re.M):
        return "clear"
    return "unknown"


def inspect(role, require_running=True):
    data = manifest()
    tree = NOS3 if role == "sat" else YAMCS
    rev = run(["git", "-C", tree, "rev-parse", "HEAD"]).stdout.strip()
    expected = data["nos3_revision" if role == "sat" else "yamcs_revision"]
    if rev != expected:
        raise RuntimeError("Unexpected revision on " + role + ": " + rev)
    result = {"role": role, "revision": rev}
    target = tree / (TOOL if role == "sat" else YAML)
    known = data["initial_toolchains"] if role == "sat" else [data["initial_yaml"]]
    filename = "toolchain-amd64-nos3.cmake" if role == "sat" else "yamcs.nos3.yaml"
    known += [digest(PROFILES / mode / filename) for mode in ("clear", "sdls")]
    if role == "mc":
        known += [digest(PROFILES / mode / "yamcs.bootstrap.yaml") for mode in ("clear", "sdls")]
    if digest(target) not in known:
        raise RuntimeError("Unrecognized local configuration: " + str(target))
    if role == "sat":
        text = target.read_text()
        match = re.search(r"^set\(CI_TRANSPORT\s+(\w+)\)", text, re.M)
        result.update(transport=match.group(1) if match else "unknown",
                      strict="-DLAB_SDLS_MODE" in text, config_hash=digest(target))
        for item in data["patches"]:
            repo = NOS3 / item["repo"]
            if run(["git", "-C", repo, "rev-parse", "HEAD"]).stdout.strip() != item["revision"]:
                raise RuntimeError("Unexpected submodule revision: " + item["repo"])
            if digest(repo / item["path"]) not in (item["base"], item["patched"], *item.get("previous", {})):
                raise RuntimeError("Unrecognized local changes: " + item["path"])
        for path, key in (("src/core/crypto_config.c", "crypto_config"),
                          ("src/sa/internal/sa_interface_inmemory.template.c", "crypto_sa"),
                          ("src/key/internal/key_interface_internal.template.c", "key_template")):
            if digest(NOS3 / "components/cryptolib" / path) != data[key]:
                raise RuntimeError("Unexpected CryptoLib configuration: " + path)
        cache = NOS3 / "fsw/build/amd64-nos3/default_cpu1/CMakeCache.txt"
        content = cache.read_text()
        if "SA_FILE:BOOL=OFF" not in content or "SA_INTERNAL:BOOL=ON" not in content:
            raise RuntimeError("Mode switching requires SA_FILE=OFF and SA_INTERNAL=ON")
        all_containers = containers()
        if FSW not in all_containers:
            raise RuntimeError("FSW container is missing. Start nos3-headless.service on SAT first.")
        stopped = []
        for name in CORE:
            if name not in all_containers:
                raise RuntimeError("Required container is missing: " + name)
            if not all_containers[name]["State"]["Running"]:
                stopped.append(name)
        if require_running and stopped:
            raise RuntimeError("Required simulator is stopped: " + ", ".join(stopped))
        result["stopped_simulators"] = stopped
        mounts = all_containers[FSW]["Mounts"]
        if not any(m["Type"] == "bind" and m["Source"] == str(NOS3) and
                   m["Destination"] == str(NOS3) for m in mounts):
            raise RuntimeError("The FSW must use the mounted NOS3 build directory")
        if not any(m["Type"] == "bind" and m["Source"] == str(KEYS) and
                   m["Destination"] == str(KEYS) for m in mounts):
            raise RuntimeError("The FSW is missing its private sdls-secrets mount")
        if all_containers[FSW]["HostConfig"]["RestartPolicy"]["Name"] not in ("", "no"):
            raise RuntimeError("The FSW requires Docker restart=no")
        if all_containers.get("nos_build_fsw", {}).get("State", {}).get("Running"):
            raise RuntimeError("An FSW build is already running")
        result["containers"] = {n: {"running": c["State"]["Running"],
            "started": c["State"]["StartedAt"]} for n, c in all_containers.items()}
        active = run(["systemctl", "is-active", "nos3-headless.service"], check=False).stdout.strip()
        if active in ("active", "activating"):
            raise RuntimeError("The NOS3 launcher is still running")
    else:
        fix = YAMCS / "yamcs-core/src/main/java/org/yamcs/security/sdls/SdlsMemento.java"
        if "return new HashSet<>(linkSpiToSeqNum.keySet());" not in fix.read_text():
            raise RuntimeError("The Yamcs SDLS counter persistence fix is missing")
        result.update(mode=yaml_mode(), config_hash=digest(target),
            service=run(["systemctl", "is-active", "yamcs-nos3.service"], check=False).stdout.strip())
    remote = "192.168.10.20" if role == "sat" else "192.168.20.10"
    gateway = "192.168.20.30" if role == "sat" else "192.168.10.30"
    route = run(["ip", "route", "get", remote]).stdout.strip()
    if "via " + gateway not in route:
        raise RuntimeError("Required lab route is missing: " + route)
    result["route"] = route
    return result


def lab_containers(current):
    return sorted(name for name in current if name.startswith("sc_1_") or
                  name in ("nos_time_driver", "nos_terminal", "nos_udp_terminal"))


def ipc_ports(path):
    rows = [line.split("!", 1)[0].strip() for line in Path(path).read_text().splitlines()]
    rows = [line for line in rows if line and not line.startswith(("<", "*"))]
    ports, offset = set(), 1
    for _ in range(int(rows[0])):
        mode, _, role, endpoint, blocking, _, prefixes = rows[offset:offset + 7]
        offset += 7 + int(prefixes)
        if mode in ("OFF", "WRITEFILE", "READFILE"):
            continue
        if mode not in ("TX", "RX", "TXRX") or role != "SERVER" or blocking != "FALSE":
            raise RuntimeError("Unrecognized 42 IPC configuration")
        host, port = endpoint.split()
        port = int(port)
        if host != "fortytwo" or not 0 < port < 65536 or port in ports:
            raise RuntimeError("Unexpected 42 IPC port")
        ports.add(port)
    if not ports or offset != len(rows):
        raise RuntimeError("Incomplete 42 IPC configuration")
    return ports


def preflight42(require_simulators=True):
    item = manifest()["fortytwo"]
    if run(["git", "-C", FORTYTWO, "rev-parse", "HEAD"]).stdout.strip() != item["revision"]:
        raise RuntimeError("Unexpected 42 revision")
    source, header = FORTYTWO / item["path"], FORTYTWO / item["header_target"]
    if source.is_symlink() or digest(source) not in (item["base"], item["patched"]):
        raise RuntimeError("Unrecognized 42 source file")
    if header.is_symlink() or (header.exists() and digest(header) != digest(MODE_SUPPORT / item["header"])):
        raise RuntimeError("Unrecognized local 42 header")
    changed = run(["git", "-C", FORTYTWO, "diff", "--name-only", "HEAD"]).stdout.splitlines()
    if any(name != item["path"] for name in changed):
        raise RuntimeError("Unexpected local changes in the 42 repository")
    binary = FORTYTWO / "42"
    if not binary.is_file() or binary.is_symlink() or not os.access(binary, os.X_OK):
        raise RuntimeError("The 42 binary is missing or invalid")
    current = containers()
    if current.get("nos_build_42", {}).get("State", {}).get("Running"):
        raise RuntimeError("A 42 build is already running")
    # Reuse the exact image ID of the existing runtime, never a moving tag.
    runtime = current.get("sc_1_fortytwo")
    if runtime:
        image = runtime["Image"]
        base = FORTYTWO.parent
        if not any(m["Type"] == "bind" and m["Source"] == str(base) and
                   m["Destination"] == str(base) for m in runtime["Mounts"]):
            raise RuntimeError("Unexpected 42 directory mount")
    else:
        match = re.search(r'^DBOX="([^"\n]+)"$', (NOS3 / "scripts/env.sh").read_text(), re.M)
        if not match:
            raise RuntimeError("NOS3 build image is missing")
        image = json.loads(run(["docker", "image", "inspect", match[1]]).stdout)[0]["Id"]
    ports = []
    if require_simulators:
        for name in (*CORE, "nos_terminal", "nos_udp_terminal"):
            if name not in current:
                raise RuntimeError("Required container is missing: " + name)
        ports = sorted(ipc_ports(FORTYTWO / "NOS3InOut/Inp_IPC.txt"))
    return {"revision": item["revision"], "image": image, "ports": ports}


def stop_simulators():
    assert_stopped("sat")
    names = lab_containers(containers())
    for group in (("nos_time_driver",), ("sc_1_fortytwo",),
                  tuple(n for n in names if n not in (FSW, "nos_time_driver", "sc_1_fortytwo", "sc_1_nos_engine_server")),
                  ("sc_1_nos_engine_server",)):
        selected = [name for name in group if name in names]
        if selected:
            run(["docker", "stop", "-t", "10", *selected])
    if any(c["State"]["Running"] for n, c in containers().items() if n in names):
        raise RuntimeError("Some lab containers are still running")
    return {"simulators": "stopped"}


def abort42():
    if containers().get("nos_build_42", {}).get("State", {}).get("Running"):
        run(["docker", "stop", "-t", "10", "nos_build_42"])
    return {"build_42_stopped": True}


def build42(transaction=None):
    info = preflight42(require_simulators=False)
    current = containers()
    if any(current[n]["State"]["Running"] for n in lab_containers(current)):
        raise RuntimeError("Stop the lab containers before rebuilding 42")
    transaction = transaction or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + os.urandom(4).hex()
    if not re.fullmatch(r"[0-9TZ-]+-[a-f0-9]{8}", transaction):
        raise ValueError("Invalid transaction ID")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    (STATE / "42").mkdir(mode=0o700, exist_ok=True)
    backup = STATE / "42" / transaction
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    item = manifest()["fortytwo"]
    source, header, binary = FORTYTWO / item["path"], FORTYTWO / item["header_target"], FORTYTWO / "42"
    shutil.copy2(source, backup / "TxRxIPC.c")
    shutil.copy2(binary, backup / "42")
    had_header = header.exists()
    if had_header:
        shutil.copy2(header, backup / "lab42_socket.h")
    metadata(backup / "before.json", {"source": digest(source), "binary": digest(binary), "image": info["image"]})
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)
    handlers = {number: signal.signal(number, interrupted) for number in (signal.SIGHUP, signal.SIGTERM)}
    try:
        if digest(source) != item["patched"]:
            patchfile = MODE_SUPPORT / item["patch"]
            run(["git", "-C", FORTYTWO, "apply", "--check", patchfile])
            run(["git", "-C", FORTYTWO, "apply", patchfile])
        atomic(header, (MODE_SUPPORT / item["header"]).read_bytes(), 0o644)
        if digest(source) != item["patched"]:
            raise RuntimeError("The patched 42 source differs from its recorded digest")
        # This Makefile does not track our new header. Recompile its one object
        # explicitly without make -B, which would invoke the Julia generators.
        (FORTYTWO / "Object/TxRxIPC.o").unlink(missing_ok=True)
        run(["docker", "run", "--rm", "--name", "nos_build_42", "--cpus=2",
             "-u", str(os.getuid()) + ":" + str(os.getgid()),
             "-v", str(NOS3) + ":" + str(NOS3),
             "-v", str(FORTYTWO.parent) + ":" + str(FORTYTWO.parent),
             "-w", FORTYTWO, info["image"], "make", "-j2"], capture=False)
        data = binary.read_bytes()
        if (digest(source) != item["patched"] or not data.startswith(b"\x7fELF") or
                b"LAB42_SEND_V1" not in data):
            raise RuntimeError("The 42 binary is missing the required socket fix")
        result = {"binary": digest(binary), "source": digest(source), "revision": item["revision"],
                  "image": info["image"], "backup": str(backup)}
        metadata(STATE / "42-installed.json", result)
        return result
    except BaseException:
        abort42()
        shutil.copy2(backup / "42", binary)
        shutil.copy2(backup / "TxRxIPC.c", source)
        if had_header:
            shutil.copy2(backup / "lab42_socket.h", header)
        else:
            header.unlink(missing_ok=True)
        raise
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)


def start_simulators():
    assert_stopped("sat")
    preflight42()
    current = containers()
    names = lab_containers(current)
    if any(current[n]["State"]["Running"] for n in names):
        raise RuntimeError("Stop all lab containers before restarting the simulators")
    run(["docker", "start", "sc_1_nos_engine_server"])
    run(["docker", "start", "sc_1_fortytwo"])
    clients = [n for n in names if n not in (FSW, "nos_time_driver", "sc_1_fortytwo", "sc_1_nos_engine_server")]
    if clients:
        run(["docker", "start", *clients])
    wanted = ipc_ports(FORTYTWO / "NOS3InOut/Inp_IPC.txt")
    missing = wanted
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        live = containers()
        for name in ("sc_1_fortytwo", "sc_1_nos_engine_server", *clients):
            if not live[name]["State"]["Running"]:
                raise RuntimeError("Container stopped during simulator startup: " + name)
        result = run(["sudo", "-n", "nsenter", "-t", str(live["sc_1_fortytwo"]["State"]["Pid"]),
                      "-n", "ss", "-Hnt", "state", "established"], check=False)
        established = {int(m[1]) for line in result.stdout.splitlines()
                       if (m := re.match(r"^(?:ESTAB\s+)?\d+\s+\d+\s+\S+:(\d+)\s", line))}
        missing = wanted - established
        if result.returncode == 0 and not missing:
            run(["docker", "start", "nos_time_driver"])
            return {"simulators": "started", "connected_42_ports": sorted(wanted), "fsw": "stopped"}
        time.sleep(2)
    raise RuntimeError("42 clients did not reconnect on these ports: " + str(sorted(missing)))


def assert_stopped(role):
    if role == "sat":
        if containers()[FSW]["State"]["Running"]:
            raise RuntimeError("Stop the FSW before changing its configuration")
    elif run(["systemctl", "is-active", "yamcs-nos3.service"], check=False).stdout.strip() in ("active", "activating"):
        raise RuntimeError("Stop Yamcs before changing its configuration")


def stop(role):
    if role == "sat":
        run(["docker", "stop", "-t", "15", FSW])
    else:
        run(["sudo", "-n", "systemctl", "stop", "yamcs-nos3.service"])
    assert_stopped(role)
    return {"stopped": role}


def abort_build():
    item = containers().get("nos_build_fsw")
    if item and item["State"]["Running"]:
        run(["docker", "stop", "-t", "10", "nos_build_fsw"])
    return {"build_stopped": True}


def configure(role, mode, transaction):
    inspect(role)
    assert_stopped(role)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = STATE / transaction
    backup.mkdir(mode=0o700, exist_ok=False)
    tree, source, name = ((NOS3, TOOL, "toolchain-amd64-nos3.cmake") if role == "sat"
                          else (YAMCS, YAML, "yamcs.bootstrap.yaml"))
    atomic(backup / "previous-config", (tree / source).read_bytes())
    # This marker prevents a later vagrant provision from replacing a selected mode.
    atomic(STATE / "managed", b"scripts/lab-mode.py\n")
    if role == "sat":
        for item in manifest()["patches"]:
            repo, patch = NOS3 / item["repo"], MODE_SUPPORT / "patches" / item["patch"]
            atomic(backup / item["patch"], (repo / item["path"]).read_bytes())
            current = digest(repo / item["path"])
            if current != item["patched"]:
                if current != item["base"]:
                    patch = MODE_SUPPORT / "patches" / item.get("previous", {})[current]
                run(["git", "-C", repo, "apply", "--check", patch])
                run(["git", "-C", repo, "apply", patch])
            if digest(repo / item["path"]) != item["patched"]:
                raise RuntimeError("The patched file differs from its recorded digest")
        KEYS.mkdir(mode=0o700, exist_ok=True)
        atomic(KEYS / "lab-mode-fsw-once", b"Run FSW once; prepare the next session explicitly.\n", 0o644)
    atomic(tree / source, (PROFILES / mode / name).read_bytes(), 0o644)
    return {"configured": role, "mode": mode, "backup": str(backup)}


def build(mode):
    assert_stopped("sat")
    run(["script", "-qefc", "make config", "/dev/null"], cwd=NOS3, capture=False)
    generated = NOS3 / "cfg/build/nos3_defs/toolchain-amd64-nos3.cmake"
    if generated.read_bytes() != (NOS3 / TOOL).read_bytes():
        raise RuntimeError("make config did not select the requested toolchain")
    if mode == "sdls":
        run(["python3", PROJECT / "scripts/sdls/build_fsw_with_keys.py"], capture=False)
    else:
        run(["script", "-qefc", "make fsw", "/dev/null"], cwd=NOS3, capture=False)
    cache = NOS3 / "fsw/build/amd64-nos3/default_cpu1/CMakeCache.txt"
    if "SA_FILE:BOOL=OFF" not in cache.read_text():
        raise RuntimeError("SA_FILE must be disabled")
    wanted, forbidden = ("LAB_" + mode.upper() + "_MODE",
                         "LAB_" + ("SDLS" if mode == "clear" else "CLEAR") + "_MODE")
    for aliases in (("ci",), ("to",), ("io_lib",), ("crypto", "cryptolib")):
        flags = [p for target in aliases for p in
                 (NOS3 / "fsw/build/amd64-nos3/default_cpu1").rglob(target + ".dir/flags.make")]
        if not flags or any(wanted not in p.read_text() or forbidden in p.read_text() for p in flags):
            raise RuntimeError("Unexpected compiler flags for " + "/".join(aliases))
    return {"built": mode}


def start_yamcs():
    run(["sudo", "-n", "systemctl", "start", "yamcs-nos3.service"])
    wait_instance()
    return {"yamcs": "RUNNING"}


def session_keys():
    assert_stopped("mc")
    result = run(["bash", PROJECT / "scripts/sdls/generate_keys.sh"])
    session = result.stdout.strip()
    if not re.fullmatch(r"/home/vagrant/sdls-secrets/run-[A-Za-z0-9]+", session):
        raise RuntimeError("Unexpected SDLS session path")
    atomic(Path(session) / "lab-mode-new-session", b"fresh key pair, no FSW started\n")
    return {"session": session}


def reset_session(session):
    # Called only after generating a fresh key pair, with the FSW still stopped.
    if str((KEYS / "current").resolve(strict=True)) != session:
        raise RuntimeError("The selected SDLS session changed")
    for name in ("tc", "tm"):
        if (Path(session) / (name + ".key")).stat().st_size != 32:
            raise RuntimeError("Invalid SDLS key length")
    fresh, used = Path(session) / "lab-mode-new-session", Path(session) / "lab-mode-initialized"
    if not fresh.is_file() or used.exists():
        raise RuntimeError("Counter initialization requires a fresh lab-mode key pair")
    links = {v["name"]: v for v in api("links/nos3").get("links", [])}
    if not all(links.get(name, {}).get("disabled") for name in ("radio-in", "radio-out")):
        raise RuntimeError("Disable the radio links before initializing the counters")
    # Claim before the first reset: a partial failure must never reuse this pair.
    pending = Path(session) / "lab-mode-initializing"
    fresh.rename(pending)
    api("instances/nos3:stop", "POST")
    wait_instance("OFFLINE")
    api("archive/nos3:executeSql", "POST", {
        "statement": "delete from memento where \"key\" = 'yamcs.sdls'"})
    api("instances/nos3:start", "POST")
    wait_instance()
    for link, spi, expected in (("radio-out", 4, 1), ("radio-in", 5, 0)):
        value = api(f"sdls/nos3/{link}/{spi}")
        if int.from_bytes(base64.b64decode(value["seq"]), "big") != expected:
            raise RuntimeError("Unexpected counter for a fresh session on link " + link)
    pending.rename(used)
    return {"session_reset": session}


def publish_yaml(mode):
    # Bootstrap config keeps the links stopped while counters are initialized.
    # The final config permits ordinary Yamcs restarts with persisted counters.
    if mode not in ("clear", "sdls"):
        raise ValueError("Invalid mode")
    if digest(YAMCS / YAML) != digest(PROFILES / mode / "yamcs.bootstrap.yaml"):
        raise RuntimeError("Unexpected Yamcs startup configuration")
    if mode == "sdls" and not ((KEYS / "current").resolve(strict=True) / "lab-mode-initialized").is_file():
        raise RuntimeError("The new SDLS session requires counter initialization")
    atomic(YAMCS / YAML, (PROFILES / mode / "yamcs.nos3.yaml").read_bytes(), 0o644)
    return {"runtime_configuration": mode}


def enable_links():
    api("links/nos3/debug-in:disable", "POST")
    for name in ("radio-in", "radio-out"):
        api("links/nos3/" + name + ":enable", "POST")
    return {"links": "enabled"}


def udp_ports(container):
    if not container["State"]["Running"]:
        return set()
    result = run(["sudo", "-n", "nsenter", "-t", str(container["State"]["Pid"]),
                  "-n", "ss", "-lun"], check=False)
    return {int(port) for port in re.findall(r":(\d+)\s", result.stdout)} if result.returncode == 0 else set()


def reconnect_radio(fsw):
    # The native radio resolves nos-fsw only once, before its forwarding loop.
    # A stopped FSW has no Docker DNS entry; a restart can also change its IP.
    # Recreate the radio's sockets only after the new FSW receiver is ready.
    if 5010 not in udp_ports(fsw):
        raise RuntimeError("The FSW must listen on UDP 5010 before the radio reconnects")
    run(["docker", "restart", "-t", "10", RADIO])
    deadline = time.monotonic() + 90
    last = "missing UDP ports"
    while time.monotonic() < deadline:
        current = containers()
        live_fsw, radio = current[FSW], current[RADIO]
        if (not live_fsw["State"]["Running"] or
                live_fsw["State"]["StartedAt"] != fsw["State"]["StartedAt"]):
            raise RuntimeError("The FSW stopped or restarted while reconnecting the radio")
        if not radio["State"]["Running"]:
            raise RuntimeError("The radio simulator stopped")
        missing = {8010, 5011, 5014, 7011, 7012} - udp_ports(radio)
        last = "missing UDP ports : " + ", ".join(str(port) for port in sorted(missing))
        if not missing:
            # Check DNS from inside the radio, using its own Docker resolver.
            networks = live_fsw["NetworkSettings"]["Networks"]
            expected = {network["IPAddress"] for name, network in networks.items()
                        if name in radio["NetworkSettings"]["Networks"] and network["IPAddress"]}
            result = run(["docker", "exec", RADIO, "getent", "ahostsv4", "nos-fsw"], check=False)
            addresses = {line.split()[0] for line in result.stdout.splitlines() if line.split()}
            if result.returncode == 0 and addresses and addresses.issubset(expected):
                return {"radio_started": radio["State"]["StartedAt"], "fsw_addresses": sorted(addresses)}
            last = ("nos-fsw DNS result in the radio container: " + str(sorted(addresses)) +
                    ", expected addresses: " + str(sorted(expected)))
        time.sleep(2)
    raise RuntimeError("Radio unavailable after restart: " + last)


def start_fsw():
    run(["docker", "start", FSW])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        c = containers()[FSW]
        if not c["State"]["Running"]:
            raise RuntimeError("The FSW container stopped")
        if 5010 in udp_ports(c):
            return {"fsw_started": c["State"]["StartedAt"], **reconnect_radio(c)}
        time.sleep(2)
    raise RuntimeError("The FSW TC receiver is not listening on UDP 5010")


def enable_output():
    root = "processors/nos3/realtime/commands//CFS/CMD/"
    api(root + "TO_ENABLE_OUTPUT", "POST", {"args": {"DEST_IP": "radio-sim", "DEST_PORT": 5011}})
    time.sleep(2)
    api(root + "TO_ENABLE_ALL", "POST", {"args": {}})
    return {"commands_submitted": ["TO_ENABLE_OUTPUT", "TO_ENABLE_ALL"]}


def metrics():
    result = {"instance": api("instances/nos3").get("state"),
              "links": api("links/nos3").get("links", [])}
    return result


def evidence():
    current = containers()
    c = current[FSW]
    logs = run(["docker", "logs", "--since", c["State"]["StartedAt"], "--tail", "2000", FSW], check=False)
    text = logs.stdout + logs.stderr
    truth = run(["docker", "logs", "--since", "30s", "--tail", "500", "sc_1_truth42sim"], check=False)
    points = [line for line in (truth.stdout + truth.stderr).splitlines() if "Data point:" in line]
    return {"fsw_running": c["State"]["Running"], "started": c["State"]["StartedAt"],
        "fortytwo_running": current["sc_1_fortytwo"]["State"]["Running"],
        "fortytwo_started": current["sc_1_fortytwo"]["State"]["StartedAt"],
        "truth_running": current["sc_1_truth42sim"]["State"]["Running"],
        "truth_started": current["sc_1_truth42sim"]["State"]["StartedAt"],
        "output_executed": "ENABLE OUTPUT CMD Succesful" in text,
        "truth": points[-2:]}


def record(role, mode, transaction):
    value = {"mode": mode, "transaction": transaction, "verified_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "config_hash": digest((NOS3 / TOOL) if role == "sat" else (YAMCS / YAML))}
    if role == "sat":
        value["fsw_started"] = containers()[FSW]["State"]["StartedAt"]
    metadata(STATE / "last.json", value)
    return value


def main():
    role, action, *args = sys.argv[1:]
    if role not in ("sat", "mc"):
        raise ValueError("Invalid VM role")
    if action in ("configure", "record"):
        mode, transaction = args
        if mode not in ("clear", "sdls") or not re.fullmatch(r"[0-9TZ-]+-[a-f0-9]{8}", transaction):
            raise ValueError("Invalid profile or transaction ID")
        value = globals()[action](role, mode, transaction)
    elif action == "inspect": value = inspect(role)
    elif action == "inspect-stopped-sims": value = inspect(role, require_running=False)
    elif action == "42-preflight": value = preflight42()
    elif action == "stop-sims": value = stop_simulators()
    elif action == "abort-42": value = abort42()
    elif action == "build-42": value = build42(args[0] if args else None)
    elif action == "start-sims": value = start_simulators()
    elif action == "stop": value = stop(role)
    elif action == "abort-build": value = abort_build()
    elif action == "assert-stopped": value = assert_stopped(role) or {"stopped": role}
    elif action == "build": value = build(args[0])
    elif action == "keys": value = session_keys()
    elif action == "reset-session": value = reset_session(args[0])
    elif action == "publish-yaml": value = publish_yaml(args[0])
    elif action == "start-yamcs": value = start_yamcs()
    elif action == "enable-links": value = enable_links()
    elif action == "start-fsw": value = start_fsw()
    elif action == "enable-output": value = enable_output()
    elif action == "metrics": value = metrics()
    elif action == "evidence": value = evidence()
    else: raise ValueError("Invalid action")
    print(json.dumps(value))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        # Never echo key files or source containing temporary session keys.
        print("Command failed: " + str(exc.cmd[0]), file=sys.stderr)
        if exc.stderr:
            print(exc.stderr[-2000:], file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
