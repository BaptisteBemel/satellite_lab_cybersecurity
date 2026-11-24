# Baseline communication

**Mode:** `clear` for the reference used by scenario 01. Repeat in a separate `sdls` session to establish a reference for a protected scenario.

**Machines:** MC, GS and SAT. Run `bash verification.sh --baseline` for these three machines. The default invocation also checks Kali.

**Goal:** follow one legitimate command from Yamcs to cFS and verify that fresh radio telemetry reaches mission control.

## Prepare

Run the host commands from the project root. With the lab running, check the active profile:

```bash
python3 scripts/lab-mode.py status
```

If a profile change is needed, use the [mode guide](../../profiles/README.md). Selecting a mode starts a new session. Record the mode and the start time of this run.

## Check the downlink

On the host:

```bash
python3 scripts/lab-mode.py check
```

In Yamcs, open **Links** and take two readings ten seconds apart. Record the differences in valid and invalid frames on `radio-in` and in received packets on `radio-in.vc1`. Look for increasing valid frames and packets, with no increase in invalid frames. Confirm that a radio telemetry parameter receives a new sample. The separate Truth42 link can keep updating while the radio path is down.

## Check the uplink

In Yamcs Commanding, send `/CFS/CMD/CF_NOOP` once and record its command-history entry. From the host, inspect the onboard events for that time window:

```bash
vagrant ssh sat -c 'docker logs --timestamps --since 1m sc_1_nos_fsw 2>&1 | grep -F "CF: No-Op received"'
```

Match one fresh event to the single submission. Record the host or guest wall-clock window separately from Yamcs mission time. Keep the command-history entry, the onboard event and both downlink readings together.

## Optional observations

The [capture helper](capture_defense.sh) records normal traffic. The [capture summary](analyze_pcap.py) lists observed flows. The [TM extractor](extract_tm_from_pcap.py) extracts the radio telemetry frames. The [Truth42 reader](truth42_reader.py) reads the separate simulation feed. Store run outputs under `captures/00-baseline/`. The shared [neighbor check](../common/check_neighbors.sh) shows interface addresses and ARP entries.
