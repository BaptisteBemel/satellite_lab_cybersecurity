#!/usr/bin/env python3

import argparse
import hashlib
from collections import defaultdict, Counter
from scapy.all import rdpcap, ARP, IP, UDP, Raw


def main():
    parser = argparse.ArgumentParser(description="Analyze satcom defense PCAP")
    parser.add_argument("pcap", help="Input pcap")
    args = parser.parse_args()

    packets = rdpcap(args.pcap)

    # Store all MAC addresses seen per IP.
    # If an IP has > 1 MAC, it indicates potential ARP spoofing.
    arp_map = defaultdict(set)
    arp_replies = 0

    # Count UDP flows (src IP:port -> dst IP:port)
    udp_counts = Counter()

    # Count identical payloads per UDP destination port.
    # Multiple identical SHA-256 hashes for the same port suggests replay attacks.
    payload_hashes = Counter()

    first_ts = None
    last_ts = None

    for pkt in packets:
        ts = float(pkt.time)
        if first_ts is None:
            first_ts = ts
        last_ts = ts

        if ARP in pkt:
            arp = pkt[ARP]
            # arp.psrc = sender IP address, arp.hwsrc = sender MAC address
            if arp.psrc and arp.hwsrc:
                arp_map[arp.psrc].add(arp.hwsrc.lower())
            # op=2 indicates an ARP reply (or gratuitous ARP reply)
            if arp.op == 2:
                arp_replies += 1

        if IP in pkt and UDP in pkt:
            ip = pkt[IP]
            udp = pkt[UDP]
            flow = (ip.src, udp.sport, ip.dst, udp.dport)
            udp_counts[flow] += 1

            # For TM (6011) and TC (8010) ports, compute SHA-256 of the payload
            # to detect duplicate frames (replay attacks).
            if Raw in pkt and udp.dport in (6011, 8010):
                digest = hashlib.sha256(bytes(pkt[Raw].load)).hexdigest()
                payload_hashes[(udp.dport, digest)] += 1

    duration = 0 if first_ts is None or last_ts is None else last_ts - first_ts

    print("PCAP summary")
    print(f"Packets: {len(packets)}")
    print(f"Duration: {duration:.2f} seconds")
    print()

    print("ARP summary")
    print(f"ARP replies: {arp_replies}")
    for ip, macs in sorted(arp_map.items()):
        mac_list = ", ".join(sorted(macs))
        # Mark IPs that have more than one associated MAC (spoofing indicator)
        marker = "  <-- POSSIBLE SPOOFING" if len(macs) > 1 else ""
        print(f"{ip} -> {mac_list}{marker}")
    print()

    print("UDP flows")
    for flow, count in udp_counts.most_common():
        src, sport, dst, dport = flow
        if dport in (6011, 8010) or sport in (6011, 8010):
            print(f"{src}:{sport} -> {dst}:{dport}  packets={count}")
    print()

    print("Possible replay indicators")
    duplicated = [
        (port, digest, count)
        for (port, digest), count in payload_hashes.items()
        if count > 1
    ]

    if not duplicated:
        print("No duplicated UDP 6011/8010 payloads detected.")
    else:
        for port, digest, count in sorted(duplicated, key=lambda x: x[2], reverse=True)[:20]:
            print(f"UDP dport {port}: duplicate payload count={count}, sha256={digest[:16]}...")


if __name__ == "__main__":
    main()
