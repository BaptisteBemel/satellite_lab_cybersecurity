#!/usr/bin/env bash

set -euo pipefail

PASS="[PASS]"
FAIL="[FAIL]"

check() {
    local name="$1"
    shift

    echo -n "$name ... "

    if "$@" >/dev/null 2>&1; then
        echo "$PASS"
    else
        echo "$FAIL"
        exit 1
    fi
}

echo "========== Connectivity =========="

check "MC -> SAT" \
    vagrant ssh mc -c "ping -c 2 192.168.20.10"

check "SAT -> MC" \
    vagrant ssh sat -c "ping -c 2 192.168.10.20"

check "KALI -> SAT" \
    vagrant ssh kali -c "ping -c 2 192.168.20.10"

echo
echo "========== YAMCS =========="

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8090/)

if [[ "$HTTP_CODE" == "200" ]]; then
    echo "YAMCS HTTP ............ $PASS"
else
    echo "YAMCS HTTP ............ $FAIL (HTTP $HTTP_CODE)"
    exit 1
fi

echo
echo "========== Telemetry =========="

TM_OUTPUT=$(mktemp)

if vagrant ssh mc -c \
    "sudo timeout 10 tcpdump -ni any 'udp dst port 6011' -c 5" \
    >"$TM_OUTPUT" 2>&1; then

    if grep -q "192.168.20.10.*6011" "$TM_OUTPUT"; then
        echo "TM -> YAMCS ........... $PASS"
    else
        echo "TM -> YAMCS ........... $FAIL"
        cat "$TM_OUTPUT"
        rm -f "$TM_OUTPUT"
        exit 1
    fi
else
    echo "TM -> YAMCS ........... $FAIL (timeout)"
    cat "$TM_OUTPUT"
    rm -f "$TM_OUTPUT"
    exit 1
fi

rm -f "$TM_OUTPUT"

echo
echo "========== Telecommand =========="

TC_OUTPUT=$(mktemp)

vagrant ssh sat -c \
    "sudo timeout 10 tcpdump -ni any 'udp dst port 8010' -c 1" \
    >"$TC_OUTPUT" 2>&1 &
TCPDUMP_PID=$!

sleep 1

echo "Sending CF_NOOP..."

vagrant ssh mc -c '
curl -fsS -X POST \
"http://127.0.0.1:8090/api/processors/nos3/realtime/commands//CFS/CMD/CF_NOOP" \
-H "Content-Type: application/json" \
-d '\''{
  "args": {
    "DEST_IP": "radio-sim",
    "DEST_PORT": 5011
  }
}'\'' >/dev/null
'

wait "$TCPDUMP_PID"

if grep -q "8010" "$TC_OUTPUT"; then
    echo "TC -> SAT ............. $PASS"
else
    echo "TC -> SAT ............. $FAIL"
    cat "$TC_OUTPUT"
    rm -f "$TC_OUTPUT"
    exit 1
fi

rm -f "$TC_OUTPUT"

echo
echo "================================="
echo "All checks passed."
echo "================================="
