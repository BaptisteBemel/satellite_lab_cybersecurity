#!/usr/bin/env bash
set -euo pipefail

# Flush the entire FORWARD chain to revert any drop rules added during the lab
# Caution: this also removes unrelated FORWARD rules if present.
echo "Flushing FORWARD chain"
sudo iptables -F FORWARD

# Display current rules for verification
sudo iptables -L FORWARD -n --line-numbers
