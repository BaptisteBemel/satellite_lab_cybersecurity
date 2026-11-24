#!/usr/bin/env bash
set -euo pipefail

# Run on MC: keep secrets outside the repository and the /vagrant shared folder.
keys_root=${1:-/home/vagrant/sdls-secrets}

# Create private directories and files accessible only to the owner.
umask 077
mkdir -p -- "$keys_root"
keys_root=$(cd -- "$keys_root" && pwd -P)

# Create a new set of keys on each run without overwriting previous ones.
keys_dir=$(mktemp -d -- "$keys_root/run-XXXXXXXX")

# Generate two independent 256-bit keys.
openssl rand -out "$keys_dir/tc.key" 32
openssl rand -out "$keys_dir/tm.key" 32

# Select this session after both keys have been generated successfully.
ln -sfnT "$keys_dir" "$keys_root/current"

# Print only the directory path, never the keys themselves.
printf '%s\n' "$keys_dir"
