Vagrant.configure("2") do |config|

  DEBIAN_BOX = "debian/bookworm64"
  SAT_BOX   = "ubuntu/jammy64"
  KALI_BOX   = "kalilinux/rolling"

  MC_LAN    = "MC-LAN"
  SPACE_LAN = "SPACE-LAN"

  MC_IP       = "192.168.10.20"
  GS_MC_IP    = "192.168.10.30"
  KALI_IP     = "192.168.10.40"
  SAT_IP     = "192.168.20.10"
  GS_SPACE_IP = "192.168.20.30"

  MC_MAC       = "080027C6214C"
  GS_MC_MAC    = "080027CB87AD"
  KALI_MAC     = "0800273452CF"
  SAT_MAC      = "0800276F217F"
  GS_SPACE_MAC = "080027F963DD"

  # Keep the default NAT interface for apt/git/internet access.
  # Lab traffic uses VirtualBox internal networks:
  #
  # MC-LAN:
  #   MC   192.168.10.20
  #   GS   192.168.10.30
  #   Kali 192.168.10.40
  #
  # SPACE-LAN:
  #   SAT 192.168.20.10
  #   GS   192.168.20.30

  # -------------------------
  # Common DNS helper
  # -------------------------
  DNS_FIX = <<-SHELL
    set +e

    DEFAULT_IFACE="$(ip route show default 2>/dev/null | awk '{print $5; exit}')"

    if command -v resolvectl >/dev/null 2>&1 && [ -n "$DEFAULT_IFACE" ]; then
      resolvectl dns "$DEFAULT_IFACE" 8.8.8.8 1.1.1.1
      resolvectl domain "$DEFAULT_IFACE" ~.

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
      procps \
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
        echo "Repository already exists at $dest, skipping clone/pull"
      fi
    }
  SHELL

  # =========================
  # SAT VM
  # =========================
  config.vm.define "sat" do |sat|
    sat.vm.box = SAT_BOX
    sat.vm.hostname = "sat-vm"

    sat.vm.network "private_network",
      ip: SAT_IP,
      netmask: "255.255.255.0",
      virtualbox__intnet: SPACE_LAN,
      mac: SAT_MAC

    sat.vm.provider "virtualbox" do |vb|
      vb.name = "sat-vm"
      vb.memory = 8192
      vb.cpus = 4
    end

    # Network and DNS applied at every boot
    sat.vm.provision "shell", run: "always", privileged: true, inline: <<-SHELL
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

      echo "Sat routing table"
      ip route
      echo "Sat addresses"
      ip -br addr
    SHELL

    # Sat installation applied when the VM is first created
    sat.vm.provision "shell", privileged: true, inline: <<-SHELL
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

      # Prevent Docker JSON logs from filling the disk during sat runs
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
        "https://github.com/BaptisteBemel/nos34yamcs_cli" \
        "/home/vagrant/nos3"

      cd /home/vagrant/nos3
      sudo -u vagrant -H git submodule update --init --recursive

      chown -R vagrant:vagrant /home/vagrant/nos3
    SHELL

    sat.vm.provision "shell",
      privileged: true,
      path: "provision/sat/build_sat.sh"

    sat.vm.provision "shell",
      privileged: true,
      path: "provision/sat/install_sat_service.sh"
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
      virtualbox__intnet: MC_LAN,
      mac: GS_MC_MAC

    gs.vm.network "private_network",
      ip: GS_SPACE_IP,
      netmask: "255.255.255.0",
      virtualbox__intnet: SPACE_LAN,
      mac: GS_SPACE_MAC

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

      echo "GS routing table"
      ip route
      echo "GS addresses"
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
      virtualbox__intnet: MC_LAN,
      mac: MC_MAC

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

      echo "MC routing table"
      ip route
      echo "MC addresses"
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
      git_clone_as_vagrant \
        "https://github.com/BaptisteBemel/yamcs4nos3.git" \
        "/home/vagrant/yamcs-nos3"

      mkdir -p /storage/yamcs-data
      chown -R vagrant:vagrant /storage/yamcs-data
      chown -R vagrant:vagrant /home/vagrant/yamcs-nos3

      # Build Yamcs backend once
      if [ ! -f /home/vagrant/yamcs-nos3/.vagrant_maven_build_done ]; then
        cd /home/vagrant/yamcs-nos3
        sudo -u vagrant -H mvn clean install -DskipTests
        sudo -u vagrant -H touch /home/vagrant/yamcs-nos3/.vagrant_maven_build_done
      else
        echo "Yamcs backend already built, skipping Maven build"
      fi

      chown -R vagrant:vagrant /home/vagrant/yamcs-nos3
    SHELL

    mc.vm.provision "shell",
      privileged: true,
      path: "provision/mc/install_yamcs_service.sh"
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
      virtualbox__intnet: MC_LAN,
      mac: KALI_MAC 

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

      echo "Kali routing table"
      ip route
      echo "Kali addresses"
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


      # Persist Kali lab network with NetworkManager.
  # Direct "ip addr add" works manually, but Kali/NetworkManager may remove it
  # during boot/provisioning. This creates a persistent NM profile for eth1.
  kali.vm.provision "shell", run: "always", privileged: true, inline: <<-SHELL
    set -e

    echo "Persisting Kali lab network on eth1"

    if command -v nmcli >/dev/null 2>&1; then
      nmcli connection delete satlab-eth1 >/dev/null 2>&1 || true

      nmcli connection add \
        type ethernet \
        ifname eth1 \
        con-name satlab-eth1 \
        ipv4.method manual \
        ipv4.addresses 192.168.10.40/24 \
        ipv4.never-default yes \
        ipv6.method ignore \
        connection.autoconnect yes

      nmcli connection modify satlab-eth1 \
        +ipv4.routes "192.168.20.0/24 192.168.10.30"

      nmcli connection up satlab-eth1
    else
      ip link set eth1 up
      ip addr flush dev eth1
      ip addr add 192.168.10.40/24 dev eth1
      ip route replace 192.168.20.0/24 via 192.168.10.30 dev eth1
    fi

    echo "Final Kali addresses"
    ip -br addr

    echo "Final Kali routing table"
    ip route
  SHELL
  end
end
