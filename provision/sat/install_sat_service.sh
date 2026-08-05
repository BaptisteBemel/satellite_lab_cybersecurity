#!/usr/bin/env bash
set -euo pipefail

echo "Installing NOS3 headless service"

cat > /usr/local/bin/start-nos3-headless.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cd /home/vagrant/nos3

echo "Starting NOS3 headless from $(pwd)"

# Try the common NOS3 headless launch scripts first.
HEADLESS_SCRIPT="$(find /home/vagrant/nos3 -maxdepth 5 -type f -iname '*headless*' -perm -111 2>/dev/null | head -n 1 || true)"

if [ -n "$HEADLESS_SCRIPT" ]; then
  echo "Found headless script: $HEADLESS_SCRIPT"
  exec "$HEADLESS_SCRIPT"
fi

# Fallbacks if the exact headless script name differs.
if [ -x "./nos3.sh" ]; then
  echo "Falling back to ./nos3.sh run"
  exec ./nos3.sh run
fi

if make -n launch >/dev/null 2>&1; then
  echo "Falling back to make launch"
  exec make launch
fi

echo "[!] No NOS3 headless launch command found"
echo "[!] Check the exact manual command and update /usr/local/bin/start-nos3-headless.sh"
exit 1
EOF

chmod +x /usr/local/bin/start-nos3-headless.sh

cat > /etc/systemd/system/nos3-headless.service <<'EOF'
[Unit]
Description=NOS3 Headless Simulator
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
Type=simple
User=vagrant
Group=vagrant
WorkingDirectory=/home/vagrant/nos3
ExecStart=/usr/local/bin/start-nos3-headless.sh
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nos3-headless.service
systemctl restart nos3-headless.service

echo "NOS3 service status"
systemctl --no-pager --full status nos3-headless.service || true
