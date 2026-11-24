# Attacker on the mission-control network

**Required mode:** `clear` throughout the seven steps.

**Machines:** MC (Yamcs), GS (gateway), SAT (cFS) and Kali on MC-LAN.

**Starting point:** complete [Baseline communication](../00-baseline/README.md) in the same mode. Save the valid radio traffic readings and a matching onboard No-Op event.

An attacker has access to the same network as mission control. This scenario follows one incident from interception through telemetry and command handling, loss of communication, detection and recovery. The operator observes Yamcs at mission control and the flight software aboard SAT.

## Learning goals

- Identify an unexpected change in the gateway or mission-control ARP entry.
- Distinguish receipt of a telemetry frame from delivery of a fresh sample to an operator.
- Distinguish receipt of a telecommand from execution by the onboard application.
- Explain the difference between a cFS command checksum and sender authentication.
- Assess what static ARP entries protect and verify recovery of both radio links.

## Walkthrough and evidence

Follow the steps in order. Record observations in separate wall-clock windows and label them with the machine and interface. Keep captures and logs for this run under `captures/01-attacker-on-mission-control-network/`.

| Step | What to observe | Evidence to keep |
| --- | --- | --- |
| 1. Intercept communications | Record the legitimate MC and GS MAC addresses directly on each VM. Compare the expected neighbor mappings with those observed during the interception exercise. | The two trusted mappings, the observed mappings and the time window of the capture. |
| 2. Replay telemetry | Compare traffic received on the radio link with packets decoded on VC1 and the generation or acquisition time of a selected parameter. | Link counter differences and the selected parameter's sample history. If the evidence only establishes frame arrival, record that limitation. |
| 3. Capture and replay a telecommand | Use one legitimate Yamcs No-Op as the reference. Count onboard events in separate windows for the original and the replay. | One Yamcs submission, the original onboard event and the event count in the replay window. Record whether a second execution occurred. |
| 4. Modify a telecommand | In the modified-command exercise the operation remains a No-Op. Compare the recorded command fields and correlate the test window with the onboard application log. | A field comparison and the application outcome. Explain which evidence addresses the checksum and which addresses sender authenticity. |
| 5. Interrupt communications | Examine downlink and uplink separately. For TM, track radio frames and VC1 samples. For TC, compare the command sent by Yamcs with the onboard event. Check each path again after recovery. | Before, during and after readings for each direction. Record auxiliary Truth42 or debug traffic separately. |
| 6. Detect and mitigate ARP poisoning | Compare the detector output with the trusted addresses. With static peer entries in place, observe the mappings during the repeat trial and verify legitimate traffic. | Detector alert, neighbor state at MC and GS and a fresh legitimate No-Op event. Static entries protect the local IP-to-MAC mapping. Command authentication is a separate property. |
| 7. Restore the baseline | Stop the exercise processes, remove only the filtering rules and static entries introduced by this run, and restore Kali's original forwarding state. Confirm neighbor resolution on MC and GS. | Repeat the baseline radio readings and legitimate No-Op check. Record recovery separately for each direction. |

The existing exercise files are in `attacks/`. Monitoring and static-neighbor utilities are in `defense/`. Use the shared [neighbor check](../common/check_neighbors.sh) for both the initial and final readings. Run [verification.sh](../../verification.sh) from the host project root for the connectivity check with Kali running. The script sends one legitimate No-Op and checks its onboard event. Record its time separately from the exercise.

## Operator checks

On MC and GS, record the interface addresses and then inspect each neighbor table:

```bash
ip -br addr
bash /vagrant/scenarios/common/check_neighbors.sh
```

On SAT, correlate the command windows with the flight software's wall-clock log. Set the interval to the two UTC timestamps recorded for the step:

```bash
docker logs --timestamps --since "$START_UTC" --until "$END_UTC" sc_1_nos_fsw 2>&1 |
    grep -F 'CF: No-Op received'
```

For step 6, run the [ARP monitor](defense/detect_arp_spoof.py) on MC, where provisioning installs Scapy. The [static neighbor helper](defense/static_arp_add.sh) is available on both MC and GS. The [removal helper](defense/static_arp_del.sh) takes the same interface and peer IP during recovery. Check the MAC against the trusted peer interface recorded before the exercise.

For step 7, finish with one new Yamcs No-Op after any connectivity script has finished. This gives recovery its own time window and avoids counting a command issued by the verification script.
