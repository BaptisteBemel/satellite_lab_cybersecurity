#!/usr/bin/env python3
"""Select and verify clear/SDLS on the running Vagrant laboratory."""
import argparse
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".lab-mode"


class TruthNotAdvancing(RuntimeError):
    pass


def stream_command(command):
    STATE.mkdir(mode=0o700, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logfile = STATE / ("build-" + stamp + "-" + uuid.uuid4().hex[:8] + ".log")
    fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    print("Build log: " + str(logfile), flush=True)
    with os.fdopen(fd, "wb") as log:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            try:
                while True:
                    block = process.stdout.read1(65536)
                    if not block:
                        break
                    log.write(block)
                    log.flush()
                    sys.stdout.buffer.write(block)
                    sys.stdout.buffer.flush()
                result = process.wait()
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
    if result:
        raise subprocess.CalledProcessError(result, command, stderr="Full log: " + str(logfile))


class Remote:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="satlab-mode-")
        self.config = Path(self.temp.name) / "ssh-config"
        chunks = []
        for name in ("mc", "sat"):
            result = subprocess.run(["vagrant", "ssh-config", name, "--host", name],
                cwd=ROOT, text=True, capture_output=True, check=True)
            chunks.append(result.stdout)
        self.config.write_text("\n".join(chunks))
        self.config.chmod(0o600)

    def close(self):
        self.temp.cleanup()

    def call(self, role, action, *args, stream=False):
        command = shlex.join(["python3", "/vagrant/scripts/lab_mode_remote.py", role, action, *args])
        ssh = ["ssh", "-T", "-F", str(self.config), "-o", "BatchMode=yes",
               "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
               "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=3", role, command]
        if stream:
            stream_command(ssh)
            return {}
        result = subprocess.run(ssh, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return json.loads(result.stdout)

    def transfer_keys(self):
        env = os.environ.copy()
        env.pop("SSH_AUTH_SOCK", None)
        env.pop("SSH_AGENT_PID", None)
        subprocess.run(["bash", str(ROOT / "scripts/sdls/distribute_keys.sh")],
                       cwd=ROOT, check=True, env=env)


def save(value, filename="last.json"):
    STATE.mkdir(mode=0o700, exist_ok=True)
    path = STATE / filename
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.chmod(0o600)
    os.replace(tmp, path)
    return path


def counters(report):
    links = {link["name"]: link for link in report["links"]}
    if report.get("instance") != "RUNNING":
        raise RuntimeError("The Yamcs instance is not RUNNING")
    parent, child = links.get("radio-in"), links.get("radio-in.vc1")
    if not parent or not child:
        raise RuntimeError("The radio-in or VC1 link is missing")
    for link in (parent, child, links.get("radio-out")):
        if not link or link.get("disabled") or link.get("status") != "OK":
            raise RuntimeError("A radio link is stopped or has failed")
    if not links.get("debug-in", {}).get("disabled"):
        raise RuntimeError("Disable debug-in before checking the radio path")
    extra = parent.get("extra", {})
    return {"valid": int(extra.get("Valid frames", 0)),
            "invalid": int(extra.get("Invalid frames", 0)),
            "packets": int(child.get("dataInCount", 0))}


def validate_health(before, after, evidence, *, verified_start=None):
    first, second = counters(before), counters(after)
    delta = {key: second[key] - first[key] for key in first}
    if delta["valid"] <= 0 or delta["packets"] <= 0:
        raise RuntimeError("Radio telemetry counters did not advance: " + str(delta))
    if delta["invalid"] != 0:
        raise RuntimeError("Invalid frames during the check: " + str(delta))
    # A long session can outlive the Docker log containing the startup event.
    known_session = bool(verified_start) and verified_start == evidence["started"]
    if not evidence["fsw_running"] or not (evidence["output_executed"] or known_session):
        raise RuntimeError("The FSW did not confirm execution of TO_ENABLE_OUTPUT")
    if not evidence["fortytwo_running"] or not evidence["truth_running"]:
        raise RuntimeError("42 or its truth42 adapter stopped")
    points = evidence["truth"]
    if len(points) < 2:
        raise RuntimeError("Recent 42 data is missing")
    times = []
    for point in points:
        match = re.search(r"Time:\s*(\d{4}[^ ]+) Pos:\s*([^V]+) Vel:", point)
        if not match or int(match[1][:4]) < 2000:
            raise RuntimeError("Invalid 42 simulation date")
        values = [float(v.strip()) for v in match[2].split(",")]
        if len(values) != 3 or not all(math.isfinite(v) for v in values) or not any(values):
            raise RuntimeError("Invalid 42 simulation position")
        if re.search(r"(?i)(?:nan|\binf\b)", point):
            raise RuntimeError("Non-finite value in the 42 simulation")
        stamp = re.fullmatch(r"(\d{4})-(\d{3})\(\d{2}/\d{2}\)T(\d{2}):(\d{2}):(\d{2}\.\d+)", match[1])
        if not stamp:
            raise RuntimeError("Unreadable 42 simulation timestamp")
        year, day, hour, minute = map(int, stamp.groups()[:4])
        second = float(stamp[5])
        if not (1 <= day <= 366 and hour < 24 and minute < 60 and second < 61):
            raise RuntimeError("Invalid 42 simulation timestamp")
        times.append((year, day, hour, minute, second))
    if times[0] == times[1]:
        raise TruthNotAdvancing("42 simulation time did not advance between samples")
    if times[1] < times[0]:
        raise RuntimeError("42 simulation time moved backwards during the check")
    return delta


def last_switch():
    path = STATE / "last.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def check(remote, seconds=10):
    last = last_switch()
    verified_start = last.get("fsw_started") if last.get("phase") == "verified" else None
    first = remote.call("mc", "metrics")
    counters(first)
    initial = remote.call("sat", "evidence")
    # Separate observations matter: consecutive log lines can repeat the latest
    # 42 snapshot. Allow bounded extra time for the simulator's buffered logs.
    for attempt in range(4):
        print(f"Checking telemetry for {seconds} seconds...", flush=True)
        time.sleep(seconds)
        second = remote.call("mc", "metrics")
        current = remote.call("sat", "evidence")
        evidence = dict(current)
        evidence["truth"] = initial["truth"][-1:] + current["truth"][-1:]
        diagnostic = save({"before": first, "after": second,
            "initial_evidence": initial, "final_evidence": current,
            "compared_truth": evidence["truth"], "previously_verified_fsw_start": verified_start,
            "observation": attempt + 1}, "health.json")
        try:
            for key in ("started", "fortytwo_started", "truth_started"):
                if initial[key] != current[key]:
                    raise RuntimeError("The FSW, 42, or truth42 restarted during the check")
            if not all(current[key] for key in ("fsw_running", "fortytwo_running", "truth_running")):
                raise RuntimeError("The FSW, 42, or truth42 stopped during the check")
            if not initial["truth"]:
                # The first logs may not have been flushed yet; establish a
                # baseline now, then require another, later observation.
                if attempt < 3:
                    if current["truth"]:
                        initial = current
                    print("Waiting for an initial 42 sample...", flush=True)
                    continue
            result = validate_health(first, second, evidence, verified_start=verified_start)
        except TruthNotAdvancing as exc:
            if attempt < 3:
                print("42 reported the same simulation time. Waiting for another sample...", flush=True)
                continue
            raise RuntimeError(str(exc) + ". Diagnostics: " + str(diagnostic)) from exc
        except Exception as exc:
            raise RuntimeError(str(exc) + ". Diagnostics: " + str(diagnostic)) from exc
        print("Health check passed: " + json.dumps(result), flush=True)
        return result, evidence


def status(remote, as_json=False):
    sat = remote.call("sat", "inspect-stopped-sims")
    mc = remote.call("mc", "inspect")
    last = last_switch()
    fsw = sat["containers"]["sc_1_nos_fsw"]
    verified = (last.get("phase") == "verified" and last.get("fsw_started") == fsw["started"]
        and fsw["running"] and not sat["stopped_simulators"] and mc["service"] == "active"
        and last.get("sat_config") == sat["config_hash"]
        and last.get("mc_config") == mc["config_hash"])
    if as_json:
        print(json.dumps({"sat": sat, "mc": mc, "last_switch": last,
                          "last_verified_session_still_running": verified}, indent=2))
    else:
        print("SAT configuration: CI=" + sat["transport"] +
              ", SPI 4 filter=" + ("configured" if sat["strict"] else "absent"))
        print("Yamcs configuration: " + mc["mode"] + ", service=" + mc["service"])
        print("FSW container: " + ("running" if fsw["running"] else "stopped"))
        if sat["stopped_simulators"]:
            print("Stopped simulators: " + ", ".join(sat["stopped_simulators"]))
        print("Last mode switch: " + last.get("mode", "none") + ", " + last.get("phase", "not recorded"))
        print("Verified session still running: " + ("yes" if verified else "no"))


def switch(remote, mode, *, plan=False):
    print("Checking revisions, configuration, routes, and simulators...", flush=True)
    remote.call("sat", "inspect")
    remote.call("mc", "inspect")
    if plan:
        print(f"Plan for {mode}: stop Yamcs and the FSW, apply the profile, "
              "prepare keys if needed, build the FSW, then start and check TC, TM and 42.")
        return
    transaction = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    state = {"transaction": transaction, "mode": mode, "phase": "starting"}
    save(state)
    try:
        print("Stopping Yamcs and the FSW...", flush=True)
        remote.call("mc", "stop")
        remote.call("sat", "stop")
        state["phase"] = "configuring"; save(state)
        remote.call("sat", "configure", mode, transaction)
        remote.call("mc", "configure", mode, transaction)
        session = None
        if mode == "sdls":
            print("Generating and transferring fresh SDLS keys...", flush=True)
            state["phase"] = "keys"; save(state)
            session = remote.call("mc", "keys")["session"]
            state["session"] = session; save(state)
            remote.transfer_keys()
        state["phase"] = "building"; save(state)
        print("Building the FSW...", flush=True)
        remote.call("sat", "build", mode, stream=True)
        remote.call("sat", "inspect")
        state["phase"] = "starting"; save(state)
        print("Starting Yamcs...", flush=True)
        remote.call("mc", "start-yamcs")
        if mode == "sdls":
            remote.call("sat", "assert-stopped")
            print("Initializing the new session counters...", flush=True)
            remote.call("mc", "reset-session", session)
        remote.call("mc", "publish-yaml", mode)
        remote.call("mc", "enable-links")
        print("Starting the FSW and reconnecting the radio simulator...", flush=True)
        remote.call("sat", "start-fsw")
        for attempt in range(6):
            remote.call("mc", "enable-output")
            time.sleep(3)
            if remote.call("sat", "evidence")["output_executed"]:
                break
        else:
            raise RuntimeError("The FSW did not execute TO_ENABLE_OUTPUT after startup")
        state["phase"] = "checking"; save(state)
        delta, evidence = check(remote)
        sat = remote.call("sat", "record", mode, transaction)
        mc = remote.call("mc", "record", mode, transaction)
        state.update(phase="verified", fsw_started=evidence["started"],
            sat_config=sat["config_hash"], mc_config=mc["config_hash"],
            delta=delta, verified_at=sat["verified_at"])
        save(state)
        print(f"The {mode} profile passed the functional checks.", flush=True)
    except BaseException:
        if state["phase"] == "building":
            try: remote.call("sat", "abort-build")
            except Exception: print("Could not stop the build on SAT", file=sys.stderr)
        state["failed_during"] = state["phase"]
        state["phase"] = "failed"
        print("Mode switch failed. Stopping the FSW and Yamcs.", file=sys.stderr)
        for role in ("sat", "mc"):
            try: remote.call(role, "stop")
            except Exception: print("Could not stop " + role, file=sys.stderr)
        save(state)
        raise


def repair42(remote, *, plan=False):
    print("Checking the 42 source and existing containers...", flush=True)
    remote.call("sat", "inspect-stopped-sims")
    remote.call("sat", "42-preflight")
    remote.call("mc", "inspect")
    if plan:
        print("Repair plan: stop the lab, back up and rebuild 42, "
              "reconnect its clients, then start the simulation clock. Select a mode to start the FSW and Yamcs.")
        return
    transaction = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    state = {"transaction": transaction, "action": "repair-42", "phase": "stopping-simulators"}
    save(state)
    try:
        print("Stopping Yamcs, the FSW, and the simulators...", flush=True)
        remote.call("mc", "stop")
        remote.call("sat", "stop")
        remote.call("sat", "stop-sims")
        state["phase"] = "building-42"; save(state)
        print("Backing up and rebuilding 42...", flush=True)
        remote.call("sat", "build-42", transaction, stream=True)
        state["phase"] = "reconnecting-simulators"; save(state)
        print("Reconnecting 42 and its clients before starting the clock...", flush=True)
        remote.call("sat", "start-sims")
        remote.call("sat", "inspect")
        state["phase"] = "simulators-ready"; save(state)
        print("42 has been rebuilt and the simulators have reconnected. "
              "Run python3 scripts/lab-mode.py clear or sdls to start the FSW and Yamcs.", flush=True)
    except BaseException:
        state["failed_during"] = state["phase"]
        state["phase"] = "failed"
        print("Repair failed. Stopping the lab containers and Yamcs.", file=sys.stderr)
        for role, action in (("sat", "abort-42"), ("sat", "stop"), ("mc", "stop"), ("sat", "stop-sims")):
            try: remote.call(role, action)
            except Exception: print("Cleanup failed: " + role + "/" + action, file=sys.stderr)
        save(state)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "check", "clear", "sdls", "repair-42"))
    parser.add_argument("--plan", action="store_true", help="Run preflight checks and show the plan")
    parser.add_argument("--json", action="store_true", help="Show detailed status as JSON")
    args = parser.parse_args()
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / "lock").open("w") as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError("Another lab-mode command is running")
        remote = Remote()
        try:
            if args.action == "status": status(remote, args.json)
            elif args.action == "check": check(remote)
            elif args.action == "repair-42": repair42(remote, plan=args.plan)
            else: switch(remote, args.action, plan=args.plan)
        finally: remote.close()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print("The command failed or was interrupted.", file=sys.stderr)
        if exc.stderr: print(exc.stderr[-3000:], file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
