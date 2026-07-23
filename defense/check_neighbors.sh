#!/usr/bin/env bash
set -euo pipefail

# Quick diagnostic script to inspect the ARP cache (neighbor table)
# on the defense machine. Useful to detect ARP spoofing effects.

echo "Hostname:"
hostname

echo
echo "Interfaces:"
ip -br addr

echo
echo "Neighbor table:"
ip neigh show

echo
echo "Interesting neighbors (satcom IPs):"
ip neigh show | grep -E '192\.168\.10\.20|192\.168\.10\.30|192\.168\.20\.10|192\.168\.20\.30' || true
