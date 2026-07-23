#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <iface> <ip>"
    echo "Example: $0 eth1 192.168.10.30"
    exit 1
fi

IFACE="$1"
IP="$2"

echo "Removing static ARP entry for $IP on $IFACE"

sudo ip neigh del "$IP" dev "$IFACE" || true

echo "Current neighbor table:"
ip neigh show
