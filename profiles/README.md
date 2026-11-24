# Clear and SDLS profiles

Run the mode commands on the host from the lab project directory. Both profiles use the same radio, ports and virtual channels. They differ in how CI receives telecommands and how TO and Yamcs handle transfer frames.

| Profile | Telecommands | Telemetry |
| --- | --- | --- |
| clear | CI uses `udp_tf_clear` to extract the cFS packet from the transfer frame. | TO and IO_LIB use the full frame data field without reserving space for SDLS. |
| sdls | CI uses `udp_tf` and accepts SPI 4 with AES-256-GCM. | TO uses SPI 5 on VC 1 with a 12-byte IV and a 16-byte authentication tag. |

Each directory contains three files.

- `toolchain-amd64-nos3.cmake` selects the CI transport and the build flag for the flight software (FSW).
- `yamcs.bootstrap.yaml` starts Yamcs with both radio links disabled while the switch prepares the session.
- `yamcs.nos3.yaml` starts the radio links in normal operation. In both modes Yamcs strips the four-byte attached sync marker. The debug input stays disabled so the radio telemetry check uses `radio-in.vc1`.

## First installation

Bring up GS for routing, provision SAT, then provision MC. SAT builds NOS3 at the commit in `provision/sat/NOS3_REVISION.txt`. That commit records the submodule revisions. `NOS3_SUBMODULES.txt` lists the versions for manual comparison.

SAT exports `nos3-components.tar.gz` in the shared project directory. MC extracts it into `/home/vagrant/components` for the Yamcs mission database. Keep the export if you intend to rebuild MC alone. Once a mode switch has marked SAT as managed, provisioning SAT skips the build and export.

MC builds the commit in `provision/mc/YAMCS_REVISION.txt` and applies `yamcs.patch`. If the Yamcs clone lacks that commit, MC fetches it from `provision/mc/yamcs-history.bundle`. The bundle is a local prerequisite in that case. A fresh clone of the lab omits both the bundle and the generated component archive.

Both services are installed with automatic startup disabled. Prepare the initial keys and FSW build, then launch the lab before switching profiles. The switch expects an existing FSW container and running 42, NOS Engine, truth42, radio and time driver containers. Wait for the headless launcher to finish.

## Switch profiles

```bash
python3 scripts/lab-mode.py status
python3 scripts/lab-mode.py clear --plan
python3 scripts/lab-mode.py clear
python3 scripts/lab-mode.py sdls
python3 scripts/lab-mode.py check
```

`--plan` runs the preflight checks. Each switch starts a fresh FSW session, including when selecting the current profile again. Allow several minutes for the build.

1. Check revisions, configuration hashes, Docker mounts and routes through GS. Expected values are in `scripts/lab-mode/manifest.json`. An unknown local change stops the switch so it can be reviewed.
2. Stop Yamcs and the FSW. Back up the files to be replaced under `/home/vagrant/.satlab-mode/` on each guest. Apply the common patches and select the profile.
3. For SDLS, generate separate 32-byte TC and TM keys on MC and transfer them to SAT over SSH. Keys stay under `/home/vagrant/sdls-secrets`. The build restores the CryptoLib key template afterwards.
4. Run `make config`, build the FSW and check the CI, TO, IO_LIB and CryptoLib options. Start Yamcs with the bootstrap configuration. For a fresh SDLS key pair, initialize the Yamcs counters and verify TC = 1 and TM = 0. The reset concerns the `yamcs.sdls` memento entry only.
5. Publish the runtime configuration and enable the radio links. Start the FSW and wait for CI on UDP 5010. Restart the radio simulator so it resolves the current FSW address, then check its sockets and DNS.
6. Send `TO_ENABLE_OUTPUT` for `radio-sim:5011` and `TO_ENABLE_ALL`. Check the FSW event for the first command, increasing valid frames and VC 1 packets, and zero additional invalid frames. The two 42 samples must show advancing simulation time and consistent position data. Repeated publications are allowed during the bounded wait.

## Recovery

For a new SDLS FSW session, run the SDLS switch again to prepare fresh keys and counters. Restarting Yamcs alone reuses its stored counters. The FSW container uses `restart=no`, and its managed launcher runs the flight binary once.

If a switch fails after stopping the services, inspect `python3 scripts/lab-mode.py status --json` and `.lab-mode/last.json` on the host. Build output is in `.lab-mode/build-*.log`. The last telemetry and 42 measurements are in `.lab-mode/health.json`. Correct the reported error before retrying the switch.

If 42 exits with `Error writing to socket in WriteToSocket`, run these commands from the host:

```bash
python3 scripts/lab-mode.py repair-42 --plan
python3 scripts/lab-mode.py repair-42
```

Repair checks the pinned 42 sources, backs up the source and binary under `/home/vagrant/.satlab-mode/42/<transaction>/` and rebuilds using the existing container image. It restarts NOS Engine, 42 and its clients, waits for the configured TCP connections, then starts the clock. The dynamics simulation restarts from the beginning. FSW and Yamcs stay stopped until you select a profile. Repair leaves the SDLS keys and counters in place.

The same socket fix is built on a fresh SAT after `make prep`. It handles partial writes and temporary backpressure. A permanent write error or an expired 30-second frame deadline reports `LAB42_SEND_V1`, the local port, the peer and `errno`.

## Validation

Run the local suite from the project root:

```bash
python3 tests/test_lab_modes.py
```

The tests use Python, Git and a C compiler. Install PyYAML to include the YAML structure check. The suite covers CI filtering, TO and IO_LIB frame handling, 42 socket handling, the profile round trip, radio reconnection, interrupted builds and session counter handling.

On running VMs, switch to clear, switch back to SDLS and check `status`. Record the FSW event for a legitimate command and the SDLS rejection evidence separately. The standalone `check` samples telemetry for at least ten seconds and may wait up to forty seconds for fresh 42 data. It sends no telecommands.
