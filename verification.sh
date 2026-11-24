#!/usr/bin/env bash
set -euo pipefail

include_kali=true
case "${1:-}" in
    "") ;;
    --baseline) include_kali=false ;;
    *) echo "Usage: bash verification.sh [--baseline]" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
    echo "Usage: bash verification.sh [--baseline]" >&2
    exit 2
fi

check() {
    local name=$1
    shift
    if "$@"; then
        printf '[PASS] %s\n' "$name"
    else
        printf '[FAIL] %s\n' "$name" >&2
        exit 1
    fi
}

check "MC to SAT" vagrant ssh mc -c 'ping -c 2 -W 2 192.168.20.10'
check "SAT to MC" vagrant ssh sat -c 'ping -c 2 -W 2 192.168.10.20'
if "$include_kali"; then
    check "Kali to SAT" vagrant ssh kali -c 'ping -c 2 -W 2 192.168.20.10'
fi
check "Yamcs instance API" curl -fsS --max-time 10 -o /dev/null \
    'http://127.0.0.1:8090/api/instances/nos3'

check "Radio telemetry arrives on MC" vagrant ssh mc -c \
    "sudo -n timeout 12 tcpdump -nn -i any -c 5 'src host 192.168.20.10 and udp dst port 6011'"

# Docker uses the SAT clock. Yamcs can timestamp the submission in mission time.
since=$(vagrant ssh sat -c 'date -u +%Y-%m-%dT%H:%M:%S.%NZ' | tr -d '\r')
if [[ ! "$since" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{9}Z$ ]]; then
    echo "Could not read the SAT clock." >&2
    exit 1
fi
check "CF_NOOP submitted" vagrant ssh mc -c \
    "curl -fsS --max-time 10 -o /dev/null -H 'Content-Type: application/json' \
    -d '{\"args\":{}}' http://127.0.0.1:8090/api/processors/nos3/realtime/commands//CFS/CMD/CF_NOOP"

check "CF_NOOP received onboard" vagrant ssh sat -c "
    for attempt in {1..10}; do
        if docker logs --timestamps --since '$since' sc_1_nos_fsw 2>&1 |
            grep -F 'CF: No-Op received'; then
            exit 0
        fi
        sleep 1
    done
    exit 1"
