#!/usr/bin/env bash
set -euo pipefail

# lab-mode.py owns the running session after the first mode switch.
if [ -f /home/vagrant/.satlab-mode/managed ]; then
  echo "Lab mode already managed: use python3 scripts/lab-mode.py clear|sdls on the host."
  exit 0
fi

echo "Installing NOS3 headless service"

cat > /etc/systemd/system/nos3-headless.service <<'EOF'
[Unit]
Description=Start the NOS3 simulator containers
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
User=vagrant
Group=vagrant
WorkingDirectory=/home/vagrant/nos3
ExecStart=/home/vagrant/nos3/scripts/headless_launch.sh

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
# Prepare fresh SDLS keys and rebuild the FSW before the first launch.
systemctl disable --now nos3-headless.service
echo "NOS3 service installed. Prepare the SDLS keys and rebuild the FSW before starting it."
