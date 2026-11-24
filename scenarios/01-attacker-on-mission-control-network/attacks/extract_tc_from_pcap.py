#!/usr/bin/env python3

import argparse
import csv
import hashlib
import shutil
import subprocess
from pathlib import Path


def parse_ccsds_primary_header(payload):
    """
    Parse the CCSDS primary header (6 bytes) from a TC packet.
    
    CCSDS primary header structure (big-endian):
    - Word 0 (bytes 0-1):
      - bits 15-13: version (should be 0 for CCSDS)
      - bit 12:    packet_type (0=TM, 1=TC)
      - bit 11:    sec_hdr_flag (0=no secondary header, 1=present)
      - bits 10-0: APID (Application Process ID)
    - Word 1 (bytes 2-3):
      - bits 15-14: seq_flags (0=continuing, 1=first, 2=last, 3=unsegmented)
      - bits 13-0:  seq_count (increments per APID)
    - Word 2 (bytes 4-5):
      - packet_data_length (actual data length = this field + 1)
    """
    if len(payload) < 6:
        return None

    word0 = int.from_bytes(payload[0:2], "big")
    word1 = int.from_bytes(payload[2:4], "big")
    word2 = int.from_bytes(payload[4:6], "big")

    # Extract fields from word0
    version = (word0 >> 13) & 0x07          # 3 bits
    packet_type = (word0 >> 12) & 0x01      # 1 bit (1 = TC)
    sec_hdr_flag = (word0 >> 11) & 0x01     # 1 bit
    apid = word0 & 0x07FF                   # 11 bits

    # Extract fields from word1
    seq_flags = (word1 >> 14) & 0x03        # 2 bits
    seq_count = word1 & 0x3FFF              # 14 bits

    # word2: packet data length (number of bytes after primary header - 1)
    packet_data_length = word2
    total_packet_length = 6 + packet_data_length + 1

    return {
        "version": version,
        "packet_type": packet_type,
        "sec_hdr_flag": sec_hdr_flag,
        "apid": apid,
        "seq_flags": seq_flags,
        "seq_count": seq_count,
        "packet_data_length": packet_data_length,
        "total_packet_length": total_packet_length,
    }


def parse_possible_cfs_command(payload):
    """
    Parse the cFS (core Flight System) secondary header.
    
    cFS command format (after the 6-byte CCSDS primary header):
    - byte 6: function code (identifies the specific command within the APID)
    - byte 7: checksum
    
    cFS checksum validation: XOR of all bytes in the packet (including primary
    header, secondary header, and data) must equal 0xFF. The checksum byte
    (offset 7) is set to make this true.
    """
    if len(payload) < 8:
        return {
            "function_code": "",
            "checksum": "",
            "xor_all_bytes": "",
            "checksum_valid_like_cfs": "",
        }

    xor_value = 0
    for byte in payload:
        xor_value ^= byte

    return {
        "function_code": payload[6],
        "checksum": payload[7],
        "xor_all_bytes": xor_value,
        "checksum_valid_like_cfs": xor_value == 0xFF,
    }


def extract_udp_payloads(pcap, dport):
    """
    Extract UDP payloads from a pcap using tshark.
    
    The command forces IPv4 defragmentation to ensure that fragmented UDP
    datagrams are reassembled before extraction. This is critical because
    NOS3 TC packets can be fragmented at the IP level.
    
    Fields extracted: frame number, IP src/dst, UDP src/dst ports,
    UDP length (includes 8-byte UDP header), and UDP payload (hex).
    """
    cmd = [
        "tshark",
        "-2",
        "-r", str(pcap),
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

    for line in result.stdout.splitlines():
        parts = line.split("\t")

        if len(parts) != 7:
            continue

        frame_number, ip_src, ip_dst, sport, dport_seen, udp_length, payload_hex = parts

        if not payload_hex:
            continue

        # tshark outputs hex with colon separators (e.g., "01:02:03")
        payload = bytes.fromhex(payload_hex.replace(":", ""))

        yield {
            "frame_number": frame_number,
            "ip_src": ip_src,
            "ip_dst": ip_dst,
            "sport": sport,
            "dport": dport_seen,
            "udp_length": udp_length,
            "payload": payload,
        }


def main():
    parser = argparse.ArgumentParser(description="Extract Yamcs -> NOS3/cFS TC payloads from pcap")
    parser.add_argument("pcap", help="Input pcap")
    parser.add_argument("-o", "--output", default="/vagrant/captures/extracted_tc", help="Output directory")
    parser.add_argument("--dport", type=int, default=5012, help="UDP destination port")
    parser.add_argument("--dedup", action="store_true", help="Skip duplicate payloads")
    args = parser.parse_args()

    if not shutil.which("tshark"):
        raise SystemExit("tshark not found in PATH")

    out_dir = Path(args.output)
    payload_dir = out_dir / "payloads"
    metadata_path = out_dir / "metadata.csv"

    payload_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    seen = set()
    extracted = 0
    skipped_duplicates = 0

    for item in extract_udp_payloads(args.pcap, args.dport):
        payload = item["payload"]
        sha256 = hashlib.sha256(payload).hexdigest()

        # Deduplication uses SHA-256 of the full UDP payload.
        if args.dedup and sha256 in seen:
            skipped_duplicates += 1
            continue

        seen.add(sha256)
        extracted += 1

        # Store each payload as a separate binary file.
        # The filename includes the pcap frame number for traceability.
        payload_file = payload_dir / f"tc_{extracted:06d}_pcapframe_{item['frame_number']}.bin"
        payload_file.write_bytes(payload)

        ccsds = parse_ccsds_primary_header(payload)
        cfs = parse_possible_cfs_command(payload)

        if ccsds is None:
            ccsds = {
                "version": "",
                "packet_type": "",
                "sec_hdr_flag": "",
                "apid": "",
                "seq_flags": "",
                "seq_count": "",
                "packet_data_length": "",
                "total_packet_length": "",
            }

        rows.append({
            "index": extracted,
            "pcap_frame": item["frame_number"],
            "ip_src": item["ip_src"],
            "ip_dst": item["ip_dst"],
            "udp_srcport": item["sport"],
            "udp_dstport": item["dport"],
            "udp_length": item["udp_length"],
            "payload_length": len(payload),
            "ccsds_version": ccsds["version"],
            "ccsds_type": ccsds["packet_type"],
            "sec_hdr_flag": ccsds["sec_hdr_flag"],
            "apid": ccsds["apid"],
            "seq_flags": ccsds["seq_flags"],
            "seq_count": ccsds["seq_count"],
            "packet_data_length": ccsds["packet_data_length"],
            "total_packet_length": ccsds["total_packet_length"],
            "function_code": cfs["function_code"],
            "checksum": cfs["checksum"],
            "xor_all_bytes": cfs["xor_all_bytes"],
            "checksum_valid_like_cfs": cfs["checksum_valid_like_cfs"],
            "sha256": sha256,
            "payload_file": str(payload_file),
        })

    fieldnames = [
        "index",
        "pcap_frame",
        "ip_src",
        "ip_dst",
        "udp_srcport",
        "udp_dstport",
        "udp_length",
        "payload_length",
        "ccsds_version",
        "ccsds_type",
        "sec_hdr_flag",
        "apid",
        "seq_flags",
        "seq_count",
        "packet_data_length",
        "total_packet_length",
        "function_code",
        "checksum",
        "xor_all_bytes",
        "checksum_valid_like_cfs",
        "sha256",
        "payload_file",
    ]

    with metadata_path.open("w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print("TC extraction complete")
    print(f"Input pcap: {args.pcap}")
    print(f"UDP destination port: {args.dport}")
    print(f"Output directory: {out_dir}")
    print(f"Extracted TC payloads: {extracted}")
    print(f"Skipped duplicates: {skipped_duplicates}")
    print(f"Payloads: {payload_dir}")
    print(f"Metadata: {metadata_path}")


if __name__ == "__main__":
    main()
