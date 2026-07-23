#!/usr/bin/env bash
set -euo pipefail

# Check for required binary
command -v tcpdump >/dev/null 2>&1 || { echo "tcpdump not found"; exit 1; }

IFACE="${1:-eth1}"
OUT="${2:-/vagrant/captures/defense_capture.pcap}"
DURATION="${3:-15}"

MC_IP="${MC_IP:-192.168.10.20}"
GS_MC_IP="${GS_MC_IP:-192.168.10.30}"
NOS3_IP="${NOS3_IP:-192.168.20.10}"

echo "Starting defense capture on $IFACE (${DURATION}s) -> ${OUT}"
echo "Targets: MC=$MC_IP GS=$GS_MC_IP NOS3=$NOS3_IP"

mkdir -p "$(dirname "$OUT")"

# Important:
# Do not capture only 'udp port 6011' here.
# The NOS3 radio telemetry datagrams are larger than the Ethernet MTU and are
# fragmented at the IPv4 layer. A BPF filter on UDP ports only captures the
# first fragment, because later fragments do not contain the UDP header.
#
# Capturing by host keeps all IPv4 fragments, so tshark can later reassemble
# the full UDP datagram and extract the CCSDS CADU payload.
FILTER="arp or host ${MC_IP} or host ${GS_MC_IP} or host ${NOS3_IP}"

echo "Filter: $FILTER"

set +e
timeout --foreground "${DURATION}s" sudo tcpdump -ni "$IFACE" -s 0 \
  "$FILTER" \
  -w "$OUT"

STATUS=$?
set -e

if [ "$STATUS" -eq 124 ]; then
    echo "Capture finished after ${DURATION}s"
    exit 0
fi

exit "$STATUS"
