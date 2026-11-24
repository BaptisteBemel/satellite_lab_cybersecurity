#!/usr/bin/env bash
set -euo pipefail

# A selected runtime profile is managed by scripts/lab-mode.py.
if [ -f /home/vagrant/.satlab-mode/managed ]; then
  echo "Lab mode already managed: use python3 scripts/lab-mode.py clear|sdls on the host."
  exit 0
fi

echo "Building NOS3"

cd /home/vagrant/nos3

# Git and the NOS3 build run as vagrant.
chown -R vagrant:vagrant /home/vagrant/nos3

provision_dir=/vagrant/provision/sat
revision_file="$provision_dir/NOS3_REVISION.txt"
if [[ ! -f "$revision_file" || ! -r "$revision_file" ]]; then
  echo "ERROR: missing or unreadable $revision_file" >&2
  exit 1
fi

nos3_revision=$(cat "$revision_file")
if [[ ! "$nos3_revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: expected a full NOS3 commit hash in $revision_file" >&2
  exit 1
fi

if [ "$(sudo -u vagrant -H git rev-parse HEAD)" != "$nos3_revision" ]; then
  rm -f .vagrant_make_prep_done .vagrant_make_done
  sudo -u vagrant -H git checkout --detach "$nos3_revision"
fi

# A submodule checkout can change build inputs even when NOS3 stays at the same commit.
if [[ -f .vagrant_make_done || -f .vagrant_make_prep_done ]]; then
  submodule_status=$(sudo -u vagrant -H git submodule status --recursive)
  if grep -Eq '^[-+U]' <<< "$submodule_status"; then
    rm -f .vagrant_make_prep_done .vagrant_make_done
  fi
fi

# Fetch the submodules recorded by the selected NOS3 commit.
submodule_attempt=1
submodule_delay=30
until sudo -u vagrant -H env \
    GIT_TERMINAL_PROMPT=0 \
    GIT_TRACE=/home/vagrant/nos3-submodules-git.log \
    GIT_TRACE_CURL=/home/vagrant/nos3-submodules-http.log \
    GIT_TRACE_CURL_NO_DATA=1 \
    git submodule update --init --recursive; do
  if [ "$submodule_attempt" -ge 4 ]; then
    echo "ERROR: could not fetch NOS3 submodules after four attempts." >&2
    exit 1
  fi
  echo "Retrying NOS3 submodules in ${submodule_delay}s..." >&2
  sleep "$submodule_delay"
  submodule_attempt=$((submodule_attempt + 1))
  submodule_delay=$((submodule_delay * 2))
done

apply_sdls_patch() {
  local tree=$1 patch_file=$2
  test -s "$patch_file"
  if sudo -u vagrant -H git -C "$tree" apply --reverse --check "$patch_file" 2>/dev/null; then
    echo "Already applied: $(basename "$patch_file")"
  else
    sudo -u vagrant -H git -C "$tree" apply --check "$patch_file"
    rm -f .vagrant_make_done
    sudo -u vagrant -H git -C "$tree" apply "$patch_file"
  fi
}

apply_sdls_patch . "$provision_dir/nos3.patch"
apply_sdls_patch components/cryptolib "$provision_dir/cryptolib.patch"
apply_sdls_patch components/generic_radio "$provision_dir/radio.patch"

# CI builds from its own submodule. NOS3 keeps the clear transport in mission-overrides.
clear_source=mission-overrides/ci/udp_tf_clear
clear_target=fsw/apps/ci/fsw/examples/udp_tf_clear
clear_files=(ci_custom.c ci_platform_cfg.h MISSION_ci_types.h)

for clear_file in "${clear_files[@]}"; do
  if [ ! -f "$clear_source/$clear_file" ]; then
    echo "ERROR: missing clear CI source: $clear_source/$clear_file" >&2
    exit 1
  fi
done

sudo -u vagrant mkdir -p "$clear_target"

for clear_file in "${clear_files[@]}"; do
  if ! cmp -s "$clear_source/$clear_file" "$clear_target/$clear_file"; then
    rm -f .vagrant_make_done

    sudo -u vagrant install -m 0644 \
      "$clear_source/$clear_file" \
      "$clear_target/$clear_file"
  fi
done

if [ ! -f .vagrant_make_prep_done ]; then
  echo "Running make prep"

  # NOS3 expects a terminal during preparation and compilation.
  sudo -iu vagrant script -qefc \
    'cd /home/vagrant/nos3 && make prep' \
    /dev/null

  sudo -u vagrant touch .vagrant_make_prep_done
else
  echo "make prep already done, skipping"
fi

# The pinned 42 TCP sender must handle temporary backpressure and partial sends.
# This stage runs before the first launch; managed labs use lab-mode repair-42.
sudo -iu vagrant python3 /vagrant/scripts/lab_mode_remote.py sat build-42

if [ ! -f .vagrant_make_done ]; then
  echo "Running make config and make"

  sudo -iu vagrant script -qefc \
    'cd /home/vagrant/nos3 && make config && make' \
    /dev/null

  sudo -u vagrant touch .vagrant_make_done
else
  echo "make already done, skipping"
fi

# MC reads this archive from the shared folder. Publish it only after tar succeeds.
echo "Exporting NOS3 components"
archive=/vagrant/nos3-components.tar.gz
temporary_archive=$(mktemp "$archive.XXXXXXXX")
trap 'rm -f -- "$temporary_archive"' EXIT
tar -czf "$temporary_archive" components
chmod 0644 "$temporary_archive"
mv -f -- "$temporary_archive" "$archive"
ls -lh "$archive"
