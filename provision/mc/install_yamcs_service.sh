#!/usr/bin/env bash
set -euo pipefail

echo "Installing Yamcs NOS3 service"

echo "Building Yamcs web frontend"
cd /home/vagrant/yamcs-nos3/yamcs-web/src/main/webapp
sudo -u vagrant npm install
sudo -u vagrant npm run build

echo "Building Yamcs NOS3"
cd /home/vagrant/yamcs-nos3
sudo -u vagrant mvn clean install -DskipTests

if [ -f /vagrant/nos3-components.tar.gz ]; then
  echo "Extracting NOS3 components"
  cd /home/vagrant
  tar -xzf /vagrant/nos3-components.tar.gz
  chown -R vagrant:vagrant /home/vagrant/components
else
  echo "[!] /vagrant/nos3-components.tar.gz not found"
  echo "[!] Yamcs NOS3 may fail because COMPONENT_DIR will be missing"
fi

cat > /etc/systemd/system/yamcs-nos3.service <<'EOF'
[Unit]
Description=Yamcs NOS3 Mission Control
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=vagrant
Group=vagrant
WorkingDirectory=/home/vagrant/yamcs-nos3/nos3
Environment=HOME=/home/vagrant
Environment=JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
Environment=COMPONENT_DIR=/home/vagrant/components
ExecStart=/usr/bin/mvn yamcs:run
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable yamcs-nos3.service
systemctl restart yamcs-nos3.service

echo "Waiting for Yamcs API"
for i in {1..60}; do
  if curl -fsS "http://127.0.0.1:8090/api/instances/nos3" >/dev/null 2>&1; then
    echo "Yamcs API is ready"
    break
  fi
  sleep 2
done

echo "Sending TO_ENABLE_OUTPUT"
curl -fsS -X POST \
  "http://127.0.0.1:8090/api/processors/nos3/realtime/commands//CFS/CMD/TO_ENABLE_OUTPUT" \
  -H "Content-Type: application/json" \
  -d '{
    "args": {
      "DEST_IP": "radio-sim",
      "DEST_PORT": 5011
    }
  }'

sleep 2

echo "Sending TO_ENABLE_ALL"
curl -fsS -X POST \
  "http://127.0.0.1:8090/api/processors/nos3/realtime/commands//CFS/CMD/TO_ENABLE_ALL" \
  -H "Content-Type: application/json" \
  -d '{
    "args": {
      "DEST_IP": "radio-sim",
      "DEST_PORT": 5011
    }
  }'

echo "Yamcs service status"
systemctl --no-pager --full status yamcs-nos3.service || true
