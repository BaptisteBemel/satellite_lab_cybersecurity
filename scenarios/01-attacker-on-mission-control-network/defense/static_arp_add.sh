#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <iface> <ip> <mac>"
    echo "Example: $0 eth1 192.168.10.30 08:00:27:aa:bb:cc"
    exit 1
fi

IFACE="$1"
IP="$2"
MAC="$3"

echo "Adding static ARP entry on $IFACE: $IP -> $MAC"
echo "The static mapping stays in effect until it is removed or the interface resets."

sudo ip neigh replace "$IP" lladdr "$MAC" dev "$IFACE" nud permanent

echo "Current neighbor entry:"
ip neigh show to "$IP" dev "$IFACE"
