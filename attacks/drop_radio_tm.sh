#!/usr/bin/env bash
set -euo pipefail

# Insert a FORWARD drop rule for NOS3 radio telemetry (UDP/6011)
echo "Blocking radio telemetry on port 6011"
sudo iptables -I FORWARD -p udp --dport 6011 -j DROP

# Display current rules for verification
sudo iptables -L FORWARD -n --line-numbers
