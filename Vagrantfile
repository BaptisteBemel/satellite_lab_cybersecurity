Vagrant.configure("2") do |config|

  DEBIAN_BOX = "debian/bookworm64"
  NOS3_BOX   = "ubuntu/jammy64"
  KALI_BOX   = "kalilinux/rolling"

  # -------------------------
  # Common DNS helper
  # -------------------------
  DNS_FIX = <<-SHELL
    if [ -L /etc/resolv.conf ]; then
      rm -f /etc/resolv.conf || true
    fi
    echo "nameserver 8.8.8.8" > /etc/resolv.conf || true
  SHELL

  # =========================
  # NOS3 / SAT VM
  # =========================
  config.vm.define "nos3" do |nos3|
    nos3.vm.box = NOS3_BOX
    nos3.vm.hostname = "nos3-vm"

    nos3.vm.provider "virtualbox" do |vb|
      vb.name = "nos3-vm"
      vb.memory = 8192
      vb.cpus = 4
      vb.customize ["modifyvm", :id, "--nic2", "intnet", "--intnet2", "SPACE-LAN"]
    end

    nos3.vm.provision "shell", run: "always", inline: <<-SHELL
      #ip addr flush dev eth1 || true
      ip addr add 192.168.20.10/24 dev eth1
      ip link set eth1 up
      ip route replace 192.168.10.0/24 via 192.168.20.30
      #{DNS_FIX}
    SHELL

    nos3.vm.provision "shell", privileged: true, inline: <<-SHELL
      apt-get update
      apt-get install -y git curl ca-certificates gnupg make python3 python3-pip python3-venv

      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg

      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu jammy stable" \
        > /etc/apt/sources.list.d/docker.list

      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

      usermod -aG docker vagrant

      if [ ! -d /home/vagrant/nos3 ]; then
        sudo -u vagrant git clone https://github.com/nasa/nos3.git /home/vagrant/nos3
        cd /home/vagrant/nos3
        sudo -u vagrant git submodule update --init --recursive
      fi
    SHELL
  end

  # =========================
  # GS VM
  # =========================
  config.vm.define "gs" do |gs|
    gs.vm.box = DEBIAN_BOX
    gs.vm.hostname = "gs-vm"

    gs.vm.provider "virtualbox" do |vb|
      vb.name = "gs-vm"
      vb.memory = 2048
      vb.cpus = 1
      vb.customize ["modifyvm", :id, "--nic2", "intnet", "--intnet2", "MC-LAN"]
      vb.customize ["modifyvm", :id, "--nic3", "intnet", "--intnet3", "SPACE-LAN"]
    end

    gs.vm.provision "shell", run: "always", inline: <<-SHELL
      ip addr flush dev eth1 || true
      ip addr flush dev eth2 || true

      ip addr add 192.168.10.30/24 dev eth1
      ip addr add 192.168.20.30/24 dev eth2

      ip link set eth1 up
      ip link set eth2 up

      sysctl -w net.ipv4.ip_forward=1
      #{DNS_FIX}
    SHELL
  end

  # =========================
  # MC VM
  # =========================
  config.vm.define "mc" do |mc|
    mc.vm.box = DEBIAN_BOX
    mc.vm.hostname = "mc-vm"
    mc.vm.boot_timeout = 600

    mc.vm.network "forwarded_port",
      guest: 8090,
      host: 8090,
      auto_correct: false

    mc.vm.provider "virtualbox" do |vb|
      vb.name = "mc-vm"
      vb.memory = 6144
      vb.cpus = 4
      vb.customize ["modifyvm", :id, "--nic2", "intnet", "--intnet2", "MC-LAN"]
    end

    # Network configuration applied at every boot
    mc.vm.provision "shell", run: "always", inline: <<-SHELL
      ip addr flush dev eth1 || true
      ip addr add 192.168.10.20/24 dev eth1
      ip link set eth1 up

      ip route replace 192.168.20.0/24 via 192.168.10.30

      #{DNS_FIX}
    SHELL

    # Yamcs installation applied when the VM is first created
    mc.vm.provision "shell", privileged: true, inline: <<-SHELL
      set -e

      export DEBIAN_FRONTEND=noninteractive

      #{DNS_FIX}

      apt-get update

      apt-get install -y \
        git \
        curl \
        ca-certificates \
        gnupg \
        openjdk-17-jdk \
        maven \
        build-essential \
        unzip \
        rsync \
        tcpdump \
        tshark

      # Install Node.js 22 and npm
      curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
      apt-get install -y nodejs

      # Clone the NOS3-compatible Yamcs branch
      if [ ! -d /home/vagrant/yamcs-nos3/.git ]; then
        sudo -u vagrant -H git clone \
          --branch nos3-dev \
          --single-branch \
          https://github.com/nasa-itc/nos3_yamcs_master.git \
          /home/vagrant/yamcs-nos3
      else
        sudo -u vagrant -H git \
          -C /home/vagrant/yamcs-nos3 \
          pull --ff-only
      fi

      mkdir -p /storage/yamcs-data
      chown -R vagrant:vagrant /storage/yamcs-data
      chown -R vagrant:vagrant /home/vagrant/yamcs-nos3
    SHELL
  end

  # =========================
  # KALI VM
  # =========================
  config.vm.define "kali" do |kali|
    kali.vm.box = KALI_BOX
    kali.vm.hostname = "kali-vm"

    kali.vm.provider "virtualbox" do |vb|
      vb.name = "kali-vm"
      vb.memory = 4096
      vb.cpus = 2
      vb.customize ["modifyvm", :id, "--nic2", "intnet", "--intnet2", "MC-LAN"]
    end

    kali.vm.provision "shell", run: "always", inline: <<-SHELL
      #ip addr flush dev eth1 || true
      ip addr add 192.168.10.66/24 dev eth1
      ip link set eth1 up
      #ip route replace 192.168.20.0/24 via 192.168.10.30
      #{DNS_FIX}
    SHELL
  end

end
