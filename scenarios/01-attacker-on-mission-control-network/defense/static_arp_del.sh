#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <iface> <ip>"
    echo "Example: $0 eth1 192.168.10.30"
    exit 1
fi

IFACE="$1"
IP="$2"

entry=$(ip neigh show to "$IP" dev "$IFACE" nud permanent)
if [[ -n "$entry" ]]; then
    sudo ip neigh del "$IP" dev "$IFACE"
    echo "Removed the static entry for $IP on $IFACE"
else
    echo "No static entry for $IP on $IFACE"
fi
ip neigh show to "$IP" dev "$IFACE"
