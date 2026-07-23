Vagrant.configure("2") do |config|

  DEBIAN_BOX = "debian/bookworm64"
  NOS3_BOX   = "ubuntu/jammy64"
  KALI_BOX   = "kalilinux/rolling"

  MC_LAN    = "MC-LAN"
  SPACE_LAN = "SPACE-LAN"

  MC_IP       = "192.168.10.20"
  GS_MC_IP    = "192.168.10.30"
  KALI_IP     = "192.168.10.40"
  NOS3_IP     = "192.168.20.10"
  GS_SPACE_IP = "192.168.20.30"

  # Keep the default NAT interface for apt/git/internet access.
  # Lab traffic uses VirtualBox internal networks:
  #
  # MC-LAN:
  #   MC   192.168.10.20
  #   GS   192.168.10.30
  #   Kali 192.168.10.40
  #
  # SPACE-LAN:
  #   NOS3 192.168.20.10
  #   GS   192.168.20.30

  # -------------------------
  # Common DNS helper
  # -------------------------
  DNS_FIX = <<-SHELL
    set +e

    DEFAULT_IFACE="$(ip route show default 2>/dev/null | awk '{print $5; exit}')"

    if command -v resolvectl >/dev/null 2>&1 && [ -n "$DEFAULT_IFACE" ]; then
      resolvectl set-dns "$DEFAULT_IFACE" 8.8.8.8 1.1.1.1
      resolvectl set-domain "$DEFAULT_IFACE" ~.

      if ! getent hosts deb.debian.org >/dev/null 2>&1 && \
         ! getent hosts archive.ubuntu.com >/dev/null 2>&1 && \
         ! getent hosts deb.kali.org >/dev/null 2>&1; then
        echo "[!] resolvectl DNS did not resolve package hosts, falling back to /etc/resolv.conf"
        rm -f /etc/resolv.conf
        cat > /etc/resolv.conf <<'EOF'
nameserver 8.8.8.8
nameserver 1.1.1.1
EOF
      fi
    else
      if [ -L /etc/resolv.conf ] || [ -f /etc/resolv.conf ]; then
        cp /etc/resolv.conf /etc/resolv.conf.bak 2>/dev/null || true
        rm -f /etc/resolv.conf
      fi

      cat > /etc/resolv.conf <<'EOF'
nameserver 8.8.8.8
nameserver 1.1.1.1
EOF
    fi

    set -e
  SHELL

  # -------------------------
  # Common tools
  # -------------------------
  COMMON_TOOLS = <<-SHELL
    set -e
    export DEBIAN_FRONTEND=noninteractive

    #{DNS_FIX}

    echo "wireshark-common wireshark-common/install-setuid boolean false" | debconf-set-selections || true

    apt-get update
    apt-get install -y \
      curl \
      ca-certificates \
      gnupg \
      git \
      jq \
      iproute2 \
      iputils-ping \
      net-tools \
      tcpdump \
      tshark \
      python3 \
      python3-pip \
      python3-venv
  SHELL

  # -------------------------
  # Helper function for git clone as vagrant user
  # -------------------------
  GIT_CLONE_AS_VAGRANT = <<-SHELL
    git_clone_as_vagrant() {
      local repo="$1"
      local dest="$2"
      local branch="${3:-}"
      local branch_opt=""

      if [ -n "$branch" ]; then
        branch_opt="--branch $branch --single-branch"
      fi

      if [ ! -d "$dest/.git" ]; then
        sudo -u vagrant -H git clone $branch_opt "$repo" "$dest"
      else
        echo "[+] Repository already exists at $dest, skipping clone/pull"
      fi
    }
  SHELL

  # =========================
  # NOS3 / SAT VM
  # =========================
  config.vm.define "nos3" do |nos3|
    nos3.vm.box = NOS3_BOX
    nos3.vm.hostname = "nos3-vm"

    nos3.vm.network "private_network",
      ip: NOS3_IP,
      netmask: "255.255.255.0",
      virtualbox__intnet: SPACE_LAN

    nos3.vm.provider "virtualbox" do |vb|
      vb.name = "nos3-vm"
      vb.memory = 8192
      vb.cpus = 4
    end

    # Network and DNS applied at every boot
    nos3.vm.provision "shell", run: "always", privileged: true, inline: <<-SHELL
      set -e

      #{DNS_FIX}

      # Wait for the GS gateway to be reachable
      for i in $(seq 1 10); do
        if ping -c 1 -W 1 #{GS_SPACE_IP} >/dev/null 2>&1; then
          break
        fi
        echo "Waiting for GS gateway (#{GS_SPACE_IP})... ($i/10)"
        sleep 2
      done

      ip route replace 192.168.10.0/24 via #{GS_SPACE_IP}

      echo "[+] NOS3 routing table"
      ip route
      echo "[+] NOS3 addresses"
      ip -br addr
    SHELL

    # NOS3 installation applied when the VM is first created
    nos3.vm.provision "shell", privileged: true, inline: <<-SHELL
      set -e
      export DEBIAN_FRONTEND=noninteractive

      #{COMMON_TOOLS}
      #{GIT_CLONE_AS_VAGRANT}

      apt-get install -y \
        make \
        build-essential \
        cmake \
        unzip \
        rsync \
        software-properties-common \
        lsb-release

      # Install Docker CE
      install -m 0755 -d /etc/apt/keyrings

      if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
          | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      fi

      chmod a+r /etc/apt/keyrings/docker.gpg

      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu jammy stable" \
        > /etc/apt/sources.list.d/docker.list

      apt-get update
      apt-get install -y \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin

      usermod -aG docker vagrant

      # Prevent Docker JSON logs from filling the disk during NOS3 runs
      mkdir -p /etc/docker
      cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
EOF

      systemctl restart docker || true

      git_clone_as_vagrant \
        "https://github.com/nasa/nos3.git" \
        "/home/vagrant/nos3"

      cd /home/vagrant/nos3
      sudo -u vagrant -H git submodule update --init --recursive

      chown -R vagrant:vagrant /home/vagrant/nos3
    SHELL
  end

  # =========================
  # GS VM
  # =========================
  config.vm.define "gs" do |gs|
    gs.vm.box = DEBIAN_BOX
    gs.vm.hostname = "gs-vm"

    gs.vm.network "private_network",
      ip: GS_MC_IP,
      netmask: "255.255.255.0",
      virtualbox__intnet: MC_LAN

    gs.vm.network "private_network",
      ip: GS_SPACE_IP,
      netmask: "255.255.255.0",
      virtualbox__intnet: SPACE_LAN

    gs.vm.provider "virtualbox" do |vb|
      vb.name = "gs-vm"
      vb.memory = 2048
      vb.cpus = 1
    end

    gs.vm.provision "shell", run: "always", privileged: true, inline: <<-SHELL
      set -e

      #{DNS_FIX}

      # Enable IPv4 forwarding persistently
      cat > /etc/sysctl.d/99-satlab-forwarding.conf <<'EOF'
net.ipv4.ip_forward=1
EOF

      sysctl -p /etc/sysctl.d/99-satlab-forwarding.conf || true
      sysctl -w net.ipv4.ip_forward=1

      echo "[+] GS routing table"
      ip route
      echo "[+] GS addresses"
      ip -br addr
    SHELL

    gs.vm.provision "shell", privileged: true, inline: <<-SHELL
      set -e
      export DEBIAN_FRONTEND=noninteractive

      #{COMMON_TOOLS}
    SHELL
  end

  # =========================
  # MC VM
  # =========================
  config.vm.define "mc" do |mc|
    mc.vm.box = DEBIAN_BOX
    mc.vm.hostname = "mc-vm"
    mc.vm.boot_timeout = 600

    mc.vm.network "private_network",
      ip: MC_IP,
      netmask: "255.255.255.0",
      virtualbox__intnet: MC_LAN

    mc.vm.network "forwarded_port",
      guest: 8090,
      host: 8090,
      auto_correct: false

    mc.vm.provider "virtualbox" do |vb|
      vb.name = "mc-vm"
      vb.memory = 6144
      vb.cpus = 4
    end

    # Network and DNS applied at every boot
    mc.vm.provision "shell", run: "always", privileged: true, inline: <<-SHELL
      set -e

      #{DNS_FIX}

      # Wait for the GS gateway to be reachable
      for i in $(seq 1 10); do
        if ping -c 1 -W 1 #{GS_MC_IP} >/dev/null 2>&1; then
          break
        fi
        echo "Waiting for GS gateway (#{GS_MC_IP})... ($i/10)"
        sleep 2
      done

      ip route replace 192.168.20.0/24 via #{GS_MC_IP}

      echo "[+] MC routing table"
      ip route
      echo "[+] MC addresses"
      ip -br addr
    SHELL

    # Yamcs installation applied when the VM is first created
    mc.vm.provision "shell", privileged: true, inline: <<-SHELL
      set -e
      export DEBIAN_FRONTEND=noninteractive

      #{COMMON_TOOLS}
      #{GIT_CLONE_AS_VAGRANT}

      apt-get install -y \
        openjdk-17-jdk \
        maven \
        build-essential \
        unzip \
        rsync

      # Install Node.js 22 and npm
      if [ ! -f /etc/apt/sources.list.d/nodesource.list ]; then
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
      fi

      apt-get install -y nodejs

      # Clone the patched NOS3-compatible Yamcs branch.
      # This should point to your fork that contains the UdpTmFrameLink / NOS3 config fixes.
      git_clone_as_vagrant \
        "https://github.com/BaptisteBemel/yamcs4nos3.git" \
        "/home/vagrant/yamcs-nos3" \
        "nos3-dev"

      mkdir -p /storage/yamcs-data
      chown -R vagrant:vagrant /storage/yamcs-data
      chown -R vagrant:vagrant /home/vagrant/yamcs-nos3

      # Build Yamcs backend once
      if [ ! -f /home/vagrant/yamcs-nos3/.vagrant_maven_build_done ]; then
        cd /home/vagrant/yamcs-nos3
        sudo -u vagrant -H mvn clean install -DskipTests
        sudo -u vagrant -H touch /home/vagrant/yamcs-nos3/.vagrant_maven_build_done
      else
        echo "[+] Yamcs backend already built, skipping Maven build"
      fi

      # Build Yamcs frontend if the repo contains a UI directory
      if [ -d /home/vagrant/yamcs-nos3/ui ]; then
        if [ ! -d /home/vagrant/yamcs-nos3/ui/node_modules ]; then
          cd /home/vagrant/yamcs-nos3/ui
          sudo -u vagrant -H npm install
          sudo -u vagrant -H npm run build
        else
          echo "[+] Yamcs frontend dependencies already installed, skipping npm install"
        fi
      else
        echo "[+] No Yamcs UI directory found, skipping frontend build"
      fi

      chown -R vagrant:vagrant /home/vagrant/yamcs-nos3
    SHELL
  end

  # =========================
  # KALI VM
  # =========================
  config.vm.define "kali" do |kali|
    kali.vm.box = KALI_BOX
    kali.vm.hostname = "kali-vm"

    kali.vm.network "private_network",
      ip: KALI_IP,
      netmask: "255.255.255.0",
      virtualbox__intnet: MC_LAN

    kali.vm.provider "virtualbox" do |vb|
      vb.name = "kali-vm"
      vb.memory = 4096
      vb.cpus = 2
    end

    # Network and DNS applied at every boot
    kali.vm.provision "shell", run: "always", privileged: true, inline: <<-SHELL
      set -e

      #{DNS_FIX}

      # Wait for the GS gateway to be reachable
      for i in $(seq 1 10); do
        if ping -c 1 -W 1 #{GS_MC_IP} >/dev/null 2>&1; then
          break
        fi
        echo "Waiting for GS gateway (#{GS_MC_IP})... ($i/10)"
        sleep 2
      done

      ip route replace 192.168.20.0/24 via #{GS_MC_IP}

      echo "[+] Kali routing table"
      ip route
      echo "[+] Kali addresses"
      ip -br addr
    SHELL

    # Attacker tooling
    kali.vm.provision "shell", privileged: true, inline: <<-SHELL
      set -e
      export DEBIAN_FRONTEND=noninteractive

      #{COMMON_TOOLS}

      apt-get install -y \
        dsniff \
        iptables \
        arping \
        nmap \
        netcat-openbsd
    SHELL
  end

end
