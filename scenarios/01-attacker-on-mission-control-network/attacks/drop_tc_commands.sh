#!/usr/bin/env bash
set -euo pipefail

# Insert a FORWARD drop rule for Yamcs TC commands (UDP/8010)
echo "Blocking TC command traffic on port 8010"
sudo iptables -I FORWARD -p udp --dport 8010 -j DROP

# Display current rules for verification
sudo iptables -L FORWARD -n --line-numbers
