#!/usr/bin/env python3

import argparse
from datetime import datetime
from scapy.all import sniff, ARP


def now():
    """Return current UTC time as a formatted string."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_expected(values):
    """
    Parse --expect arguments into a dictionary {IP: MAC}.
    Raises SystemExit if the format is invalid.
    """
    expected = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"Invalid --expect value: {item}. Use IP=MAC")
        ip, mac = item.split("=", 1)
        expected[ip.strip()] = mac.strip().lower()
    return expected


def main():
    parser = argparse.ArgumentParser(description="Detect ARP spoofing by monitoring IP-to-MAC changes")
    parser.add_argument("--iface", default="eth1", help="Interface to sniff")
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        help="Expected mapping IP=MAC. Example: --expect 192.168.10.30=08:00:27:aa:bb:cc",
    )
    args = parser.parse_args()

    expected = parse_expected(args.expect)
    seen = {}  # Tracks the last known MAC for each IP to detect flapping

    print(f"ARP spoof detector started on {args.iface}")
    if expected:
        print("Expected mappings:")
        for ip, mac in expected.items():
            print(f"    {ip} -> {mac}")
    else:
        print("No expected mappings provided. The script will alert on MAC changes only.")

    def handle(pkt):
        """
        Callback for each captured packet.
        Checks ARP requests (op=1) and replies (op=2) to detect IP-MAC changes.
        """
        if ARP not in pkt:
            return

        arp = pkt[ARP]

        # Filter ARP requests and replies (op=1 is request, op=2 is reply).
        # Gratuitous ARP (op=1 with src IP equal to target IP) is also caught.
        if arp.op not in (1, 2):
            return

        src_ip = arp.psrc
        src_mac = arp.hwsrc.lower()

        # Ignore invalid source IPs
        if src_ip == "0.0.0.0":
            return

        # Check against expected mappings
        if src_ip in expected and expected[src_ip] != src_mac:
            print(f"[ALERT] {now()} Expected {src_ip} -> {expected[src_ip]}, but saw {src_mac}")

        # Check for MAC flapping (change from previously seen MAC)
        if src_ip in seen and seen[src_ip] != src_mac:
            print(f"[ALERT] {now()} MAC change for {src_ip}: {seen[src_ip]} -> {src_mac}")

        seen[src_ip] = src_mac

    sniff(iface=args.iface, filter="arp", prn=handle, store=False)


if __name__ == "__main__":
    main()
