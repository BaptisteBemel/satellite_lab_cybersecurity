#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-eth1}"
OUT="${2:-/vagrant/captures/tc_commands.pcap}"
DURATION="${3:-20}"

MC_IP="${MC_IP:-192.168.10.20}"
GS_MC_IP="${GS_MC_IP:-192.168.10.30}"
NOS3_IP="${NOS3_IP:-192.168.20.10}"

echo "Starting TC command capture on $IFACE (${DURATION}s) -> ${OUT}"
echo "Targets: MC=$MC_IP GS=$GS_MC_IP NOS3=$NOS3_IP"

mkdir -p "$(dirname "$OUT")"

# Capture by host, not only 'udp port 8010', to maintain consistency with the
# satcom capture and to avoid missing fragments if larger TC packets appear.
FILTER="host ${MC_IP} or host ${GS_MC_IP} or host ${NOS3_IP}"

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
