#!/usr/bin/env python3

import argparse
import hashlib
import shutil
import socket
import subprocess
import time
from pathlib import Path

# CCSDS Attached Sync Marker (ASM) for NOS3 radio TM frames.
# This 4-byte sequence (0x1A 0xCF 0xFC 0x1D) precedes every valid CADU
# (Channel Access Data Unit) transmitted over the radio link.
# It is used as a synchronization pattern and as a sanity check to
# ensure we are processing valid telemetry frames.
ASM = bytes.fromhex("1acffc1d")


def extract_udp_payloads_from_pcap(pcap_path, dport, require_asm=True):
    """
    Extract UDP payloads from a pcap file using tshark.
    
    The 'ip.defragment:TRUE' option is critical: without it, tshark only
    processes the first fragment of a fragmented IPv4 datagram. The NOS3
    radio TM datagrams are often fragmented at the IP level (due to their
    size), so this setting ensures we get the complete reassembled payload.
    
    The 'udp.length' field is the total UDP datagram length, which includes
    the 8-byte UDP header. The actual payload length is udp.length - 8.
    
    If require_asm is True, only payloads starting with the ASM are kept.
    This filters out non-TM traffic that might share the same UDP port.
    """
    cmd = [
        "tshark",
        "-2",
        "-r", str(pcap_path),
        "-o", "ip.defragment:TRUE",
        "-Y", f"udp.dstport == {dport}",
        "-T", "fields",
        "-e", "frame.number",
        "-e", "ip.src",
        "-e", "ip.dst",
        "-e", "udp.srcport",
        "-e", "udp.dstport",
        "-e", "udp.length",
        "-e", "udp.payload",
    ]

    result = subprocess.run(cmd, check=True, capture_output=True, text=True)

    payloads = []

    for line in result.stdout.splitlines():
        parts = line.split("\t")

        if len(parts) != 7:
            continue

        frame_number, ip_src, ip_dst, sport, dport_seen, udp_length, payload_hex = parts

        if not payload_hex:
            continue

        payload = bytes.fromhex(payload_hex.replace(":", ""))

        # ASM check: a valid CADU starts with the 4-byte sync marker.
        # If we miss the ASM, the payload is likely a fragment or corrupted.
        if require_asm and not payload.startswith(ASM):
            continue

        payloads.append({
            "source": f"pcap_frame_{frame_number}",
            "ip_src": ip_src,
            "ip_dst": ip_dst,
            "sport": sport,
            "dport": dport_seen,
            "udp_length": udp_length,
            "payload": payload,
        })

    return payloads


def load_cadus_from_directory(directory, require_asm=True):
    """
    Load CADU payloads from a directory containing .bin files.
    
    This is used when payloads have already been extracted and stored
    as individual binary files (e.g., by the extract_tc_from_pcap.py script).
    Each .bin file is read and checked for the ASM marker.
    
    The udp_length field is estimated as len(payload) + 8 because the UDP
    header adds 8 bytes. This is used only for logging/display purposes.
    """
    payloads = []

    for path in sorted(Path(directory).glob("*.bin")):
        payload = path.read_bytes()

        if require_asm and not payload.startswith(ASM):
            continue

        payloads.append({
            "source": str(path),
            "ip_src": "",
            "ip_dst": "",
            "sport": "",
            "dport": "",
            # UDP length = UDP header (8) + payload length
            "udp_length": str(len(payload) + 8),
            "payload": payload,
        })

    return payloads


def deduplicate_payloads(payloads):
    """
    Remove duplicate payloads based on SHA-256 of the raw bytes.
    
    In a typical capture, the same CADU may appear multiple times due to
    retransmissions or because it was captured on multiple interfaces.
    Deduplication reduces the replay set to unique frames only.
    """
    seen = set()
    deduped = []

    for item in payloads:
        digest = hashlib.sha256(item["payload"]).hexdigest()

        if digest in seen:
            continue

        seen.add(digest)
        deduped.append(item)

    return deduped


def main():
    parser = argparse.ArgumentParser(
        description="Replay reassembled NOS3 CCSDS radio TM CADUs to Yamcs"
    )
    parser.add_argument("input", help="Input pcap file or directory containing CADU .bin files")
    parser.add_argument("--src-dport", type=int, default=6011, help="UDP destination port to extract from pcap")
    parser.add_argument("--dst-ip", default="192.168.10.20", help="Yamcs destination IP")
    parser.add_argument("--dst-port", type=int, default=6011, help="Yamcs destination UDP port")
    parser.add_argument("--delay", type=float, default=0.05, help="Delay between replayed CADUs")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of CADUs to replay, 0 = no limit")
    parser.add_argument("--loop", action="store_true", help="Replay forever")
    parser.add_argument("--no-dedup", action="store_true", help="Do not remove duplicate payloads")
    parser.add_argument("--allow-no-asm", action="store_true", help="Replay payloads even if ASM is missing")
    args = parser.parse_args()

    if not shutil.which("tshark"):
        raise SystemExit("tshark not found in PATH")

    input_path = Path(args.input)
    require_asm = not args.allow_no_asm

    if input_path.is_dir():
        payloads = load_cadus_from_directory(input_path, require_asm=require_asm)
    else:
        payloads = extract_udp_payloads_from_pcap(
            input_path,
            dport=args.src_dport,
            require_asm=require_asm,
        )

    if not payloads:
        raise SystemExit("No replayable CADU payloads found")

    original_count = len(payloads)

    if not args.no_dedup:
        payloads = deduplicate_payloads(payloads)

    if args.limit > 0:
        payloads = payloads[:args.limit]

    print(f"Loaded payloads: {original_count}")
    print(f"Payloads after filtering/dedup/limit: {len(payloads)}")
    print(f"Replaying to {args.dst_ip}:{args.dst_port}")
    print(f"Delay: {args.delay}s")

    # Create a raw UDP socket. No IP header manipulation needed; the OS
    # handles routing and encapsulation.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    while True:
        for item in payloads:
            payload = item["payload"]

            print(
                f"Replay {item['source']} "
                f"len={len(payload)} "
                f"udp_length={item['udp_length']}"
            )

            # Send the raw payload as a UDP datagram. The OS adds the UDP
            # header and IP headers automatically.
            sock.sendto(payload, (args.dst_ip, args.dst_port))
            time.sleep(args.delay)

        if not args.loop:
            break

    print("Replay complete")


if __name__ == "__main__":
    main()
