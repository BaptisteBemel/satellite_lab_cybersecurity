#!/usr/bin/env bash
set -euo pipefail

# The exercise rules have no owner tag. Keep the pre-run count of each exact rule.
rule_counts() {
    sudo iptables -w 5 -S FORWARD | awk '
        {
            sub(/ -m udp /, " ")
            if ($0 == "-A FORWARD -p udp --dport 6011 -j DROP") tm++
            if ($0 == "-A FORWARD -p udp --dport 8010 -j DROP") tc++
        }
        END { print tm+0, tc+0 }
    '
}

remove_excess() {
    local port=$1 count=$2 original=$3
    if (( count < original )); then
        echo "Fewer matching rules than before on UDP $port. Check FORWARD manually." >&2
        incomplete=1
    fi
    while (( count > original )); do
        sudo iptables -w 5 -D FORWARD -p udp --dport "$port" -j DROP
        count=$((count - 1))
    done
}

if [[ $# == 1 && $1 == --snapshot ]]; then
    forwarding=$(sysctl -n net.ipv4.ip_forward)
    counts=$(rule_counts)
    printf '%s %s\n' "$forwarding" "$counts"
elif [[ $# == 2 && $1 == --restore && $2 =~ ^[01]\ (0|[1-9][0-9]*)\ (0|[1-9][0-9]*)$ ]]; then
    read -r forwarding original_tm original_tc <<< "$2"
    counts=$(rule_counts)
    read -r tm tc <<< "$counts"
    incomplete=0
    remove_excess 6011 "$tm" "$original_tm"
    remove_excess 8010 "$tc" "$original_tc"
    sudo sysctl -w "net.ipv4.ip_forward=$forwarding"
    exit "$incomplete"
else
    echo "Usage: $0 --snapshot | --restore \"<snapshot>\"" >&2
    exit 2
fi
