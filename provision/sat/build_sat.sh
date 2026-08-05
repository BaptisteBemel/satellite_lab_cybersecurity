#!/usr/bin/env bash
set -euo pipefail

echo "Building NOS3"

cd /home/vagrant/nos3
chown -R vagrant:vagrant /home/vagrant/nos3

echo "Setting the traffic to unencrypted"
sudo -u vagrant sed -i 's/set(gsw_flag 1)/set(gsw_flag 0)/' /home/vagrant/nos3/components/generic_radio/sim/CMakeLists.txt

if [ ! -f /home/vagrant/nos3/.vagrant_make_prep_done ]; then
  echo "Running make prep"
  sudo -iu vagrant script -qefc 'cd /home/vagrant/nos3 && make prep' /dev/null
  sudo -u vagrant touch /home/vagrant/nos3/.vagrant_make_prep_done
else
  echo "make prep already done, skipping"
fi

if [ ! -f /home/vagrant/nos3/.vagrant_make_done ]; then
  echo "Running make"
  sudo -iu vagrant script -qefc 'cd /home/vagrant/nos3 && make' /dev/null
  sudo -u vagrant touch /home/vagrant/nos3/.vagrant_make_done
else
  echo "make already done, skipping"
fi

echo "Exporting NOS3 components"
cd /home/vagrant/nos3
tar -czf /vagrant/nos3-components.tar.gz components
ls -lh /vagrant/nos3-components.tar.gz
