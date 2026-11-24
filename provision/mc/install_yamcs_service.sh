#!/usr/bin/env bash
set -euo pipefail

# A selected runtime profile is managed by scripts/lab-mode.py.
if [ -f /home/vagrant/.satlab-mode/managed ]; then
  echo "Lab mode already managed: use python3 scripts/lab-mode.py clear|sdls on the host."
  exit 0
fi

echo "Installing Yamcs NOS3 service"

provision_dir=/vagrant/provision/mc
revision_file="$provision_dir/YAMCS_REVISION.txt"
bundle="$provision_dir/yamcs-history.bundle"
patch_file="$provision_dir/yamcs.patch"
components=/vagrant/nos3-components.tar.gz
yamcs=/home/vagrant/yamcs-nos3

if [[ ! -f "$revision_file" || ! -r "$revision_file" ]]; then
  echo "ERROR: missing or unreadable $revision_file" >&2
  exit 1
fi
yamcs_revision=$(cat "$revision_file")
if [[ ! "$yamcs_revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: expected a full Yamcs commit hash in $revision_file" >&2
  exit 1
fi
if [[ ! -s "$components" ]]; then
  echo "ERROR: missing component archive $components. Provision SAT first or restore its export." >&2
  exit 1
fi

as_vagrant() {
  sudo -u vagrant -H "$@"
}

cd "$yamcs"
# The local bundle supplies the pinned merge when the clone lacks it.
if ! as_vagrant git cat-file -e "${yamcs_revision}^{commit}" 2>/dev/null; then
  if [[ ! -s "$bundle" ]]; then
    echo "ERROR: this clone lacks $yamcs_revision. Restore $bundle or fetch the pinned commit." >&2
    exit 1
  fi
  as_vagrant git bundle verify "$bundle"
  as_vagrant git fetch --no-tags "$bundle" HEAD
fi
if [[ $(as_vagrant git rev-parse HEAD) != "$yamcs_revision" ]]; then
  as_vagrant git checkout --detach "$yamcs_revision"
fi

if as_vagrant git apply --reverse --check "$patch_file" 2>/dev/null; then
  echo "Already applied: yamcs.patch"
else
  as_vagrant git apply --check "$patch_file"
  as_vagrant git apply "$patch_file"
fi

echo "Building Yamcs web frontend"
cd "$yamcs/yamcs-web/src/main/webapp"
# Install the frontend dependencies from the lockfile at the pinned Yamcs revision.
as_vagrant npm ci
as_vagrant npm run build

echo "Building Yamcs NOS3"
cd "$yamcs"
as_vagrant mvn clean install -DskipTests

echo "Extracting NOS3 components"
as_vagrant tar -xzf "$components" -C /home/vagrant

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

# The first SDLS session needs its keys before the radio links start.
systemctl disable --now yamcs-nos3.service
echo "Yamcs service installed. Start it after preparing the SDLS session."
