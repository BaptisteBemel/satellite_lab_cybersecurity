#!/usr/bin/env python3
"""Run on SAT: build native CryptoLib with the selected session keys."""

import os
from pathlib import Path
import re
import signal
import subprocess

nos3 = Path("/home/vagrant/nos3")
keys_dir = Path("/home/vagrant/sdls-secrets/current").resolve(strict=True)
source = nos3 / "components/cryptolib/src/key/internal/key_interface_internal.template.c"

# Require the already-generated native SDLS receiver configuration.
toolchain = nos3 / "cfg/build/nos3_defs/toolchain-amd64-nos3.cmake"
if not re.search(r"^\s*set\(CI_TRANSPORT\s+udp_tf\s*\)", toolchain.read_text(), re.M):
    raise SystemExit("ERROR: run make config with CI_TRANSPORT=udp_tf first.")

# Preserve the exact source and metadata, including any existing local edits.
original = source.read_bytes()
original_stat = source.stat()
configured = original.decode("utf-8")

# Replace existing key values only; keep CryptoLib's native implementation.
for slot, filename in ((4, "tc.key"), (9, "tm.key")):
    key = (keys_dir / filename).read_bytes()
    if len(key) != 32:
        raise SystemExit(f"ERROR: {filename} must contain exactly 32 bytes.")

    for index, value in enumerate(key):
        pattern = (
            rf"(key_ring\[{slot}\]\.value\[{index}\]\s*=\s*)"
            rf"0x[0-9A-Fa-f]+(\s*;)"
        )
        configured, count = re.subn(
            pattern,
            lambda match: f"{match[1]}0x{value:02X}{match[2]}",
            configured,
        )
        if count != 1:
            raise SystemExit(f"ERROR: unexpected source layout for key slot {slot}.")

    # Both selected native slots must already be active 256-bit keys.
    for field, expected in (("key_len", "32"), ("key_state", "KEY_ACTIVE")):
        pattern = rf"key_ring\[{slot}\]\.{field}\s*=\s*{expected}\s*;"
        if len(re.findall(pattern, configured)) != 1:
            raise SystemExit(f"ERROR: unexpected {field} for key slot {slot}.")

def interrupted(signum, _frame):
    raise SystemExit(128 + signum)


# Let the finally block restore the source on a normal SSH hangup or termination.
for signum in (signal.SIGHUP, signal.SIGTERM):
    signal.signal(signum, interrupted)

# Keep secret values private while compiling, and never print the source.
try:
    source.chmod(0o600)
    source.write_bytes(configured.encode("utf-8"))
    print("Building FSW with the selected SDLS session keys...", flush=True)
    subprocess.run(
        ["script", "-qefc", "make fsw", "/dev/null"],
        cwd=nos3,
        check=True,
    )
except BaseException:
    # The NOS3 build runs in this named container. Stop it before restoring
    # the source so a surviving build cannot read a different key template.
    subprocess.run(
        ["docker", "stop", "-t", "10", "nos_build_fsw"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    raise
finally:
    # Restore the source on success or a normal build failure/interruption.
    source.write_bytes(original)
    source.chmod(original_stat.st_mode & 0o7777)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    print("Original key source restored.", flush=True)

print("FSW build completed. The running containers have not been restarted.")
