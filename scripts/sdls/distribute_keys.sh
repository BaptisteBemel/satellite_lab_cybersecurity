#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."
umask 077
ssh_config=$(mktemp)
trap 'rm -f -- "$ssh_config"' EXIT
vagrant ssh-config mc --host mc > "$ssh_config"
vagrant ssh-config sat --host sat >> "$ssh_config"
ssh_opts=(-T -o BatchMode=yes -F "$ssh_config")

session=$(ssh "${ssh_opts[@]}" mc 'readlink -e /home/vagrant/sdls-secrets/current')
if [[ ! "$session" =~ ^/home/vagrant/sdls-secrets/run-[[:alnum:]]+$ ]]; then
    echo "ERROR: invalid MC key session." >&2
    exit 1
fi

# Stream both files over SSH. Select the SAT session only after both keys arrive.
install_pair=$(cat <<'REMOTE'
set -eu
umask 077
root=/home/vagrant/sdls-secrets
mkdir -p "$root"
chmod 700 "$root"
stage=$(mktemp -d "$root/.incoming-XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT
tar -xf - -C "$stage" -- tc.key tm.key
for key in tc.key tm.key; do
    test -f "$stage/$key" && test ! -L "$stage/$key" || exit 1
    test "$(wc -c < "$stage/$key")" -eq 32
    chmod 600 "$stage/$key"
done

if [ -d "$session" ]; then
    # A retry can reselect a complete session, but cannot change its keys.
    cmp -s "$stage/tc.key" "$session/tc.key"
    cmp -s "$stage/tm.key" "$session/tm.key"
else
    mv -T -- "$stage" "$session"
fi
ln -sfnT "$session" "$root/current"
REMOTE
)
ssh "${ssh_opts[@]}" mc "tar -C '$session' -cf - tc.key tm.key" |
    ssh "${ssh_opts[@]}" sat "session='$session'; $install_pair"
printf 'SDLS session selected on SAT: %s\n' "$session"
