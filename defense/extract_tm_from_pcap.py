#!/usr/bin/env python3

import argparse
import csv
import hashlib
import shutil
import subprocess
from pathlib import Path

# CCSDS Attached Sync Marker for NOS3 radio TM frames.
# A valid CADU starts with these 4 bytes.
ASM = bytes.fromhex("1acffc1d")


def run_tshark(pcap: str, port: int):
    """
    Use tshark instead of Scapy because tshark can reassemble fragmented IPv4 datagrams.
    tshark options:
      - -r: read file
      - -o ip.defragment:TRUE: reassemble fragmented IP datagrams (critical for NOS3 TM)
      - -Y: display filter (only UDP dst port)
      - -T fields -e ...: extract specific fields as tab-separated values
    Output fields: frame.number, ip.src, ip.dst, udp.length, udp.payload
    """
    cmd = [
        "tshark",
        "-r", pcap,
        "-o", "ip.defragment:TRUE",
        "-Y", f"udp.dstport == {port}",
        "-T", "fields",
        "-e", "frame.number",
        "-e", "ip.src",
        "-e", "ip.dst",
        "-e", "udp.length",
        "-e", "udp.payload",
    ]

    result = subprocess.run(cmd, check=True, capture_output=True, text=True)

    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 5:
            continue

        frame_number, ip_src, ip_dst, udp_length, udp_payload_hex = parts

        if not udp_payload_hex:
            continue

        try:
            # tshark outputs hex with colon separators (e.g., "01:02:03")
            payload = bytes.fromhex(udp_payload_hex.replace(":", ""))
        except ValueError:
            continue

        yield {
            "frame_number": frame_number,
            "ip_src": ip_src,
            "ip_dst": ip_dst,
            "udp_length": udp_length,
            "payload": payload,
        }


def parse_tm_frame_header(tm_frame: bytes):
    """
    Parse the first 6 bytes of a CCSDS TM Transfer Frame (after stripping ASM).

    CCSDS TM Transfer Frame primary header structure (big-endian):
    - Byte 0-1 (16 bits):
      - bits 15-14: version (2 bits, should be 0 for CCSDS)
      - bits 13-4:  spacecraft ID (10 bits)
      - bits 3-1:   VCID (Virtual Channel ID, 3 bits)
      - bit 0:      OCF (Operational Control Field flag)
    - Byte 2: Master Channel Frame Count (MCFC)
    - Byte 3: Virtual Channel Frame Count (VCFC)
    - Byte 4-5 (16 bits):
      - bits 15-3: FHP (First Header Pointer, 13 bits)
      - bits 2-0:  reserved / unused
    """
    if len(tm_frame) < 6:
        return None

    first16 = int.from_bytes(tm_frame[0:2], "big")
    version = (first16 >> 14) & 0x03
    spacecraft_id = (first16 >> 4) & 0x03FF
    vcid = (first16 >> 1) & 0x07
    ocf = first16 & 0x01

    master_channel_frame_count = tm_frame[2]
    virtual_channel_frame_count = tm_frame[3]

    status = int.from_bytes(tm_frame[4:6], "big")
    first_header_pointer = status & 0x07FF

    return {
        "version": version,
        "spacecraft_id": spacecraft_id,
        "vcid": vcid,
        "ocf": ocf,
        "mcfc": master_channel_frame_count,
        "vcfc": virtual_channel_frame_count,
        "fhp": first_header_pointer,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract raw CCSDS TM CADUs from a pcap file"
    )

    parser.add_argument("pcap", help="Input pcap file")
    parser.add_argument("-o", "--output-dir", default="extracted_tm", help="Output directory")
    parser.add_argument("--port", type=int, default=6011, help="Yamcs radio TM UDP destination port")
    parser.add_argument("--dedup", action="store_true", help="Remove duplicate CADUs")
    parser.add_argument("--skip-idle", action="store_true", help="Skip idle TM frames with FHP 0x7FE")
    parser.add_argument("--strip-asm", action="store_true", help="Also write TM frames without the 4-byte ASM")

    args = parser.parse_args()

    if not shutil.which("tshark"):
        raise SystemExit("tshark not found in PATH")

    out_dir = Path(args.output_dir)
    cadu_dir = out_dir / "cadus"
    frame_dir = out_dir / "tm_frames_no_asm"

    cadu_dir.mkdir(parents=True, exist_ok=True)

    if args.strip_asm:
        frame_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = out_dir / "metadata.csv"

    seen_hashes = set()
    extracted = 0
    skipped_duplicates = 0
    skipped_no_asm = 0
    skipped_idle = 0

    with metadata_path.open("w", newline="") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "index",
                "pcap_frame",
                "ip_src",
                "ip_dst",
                "udp_length",
                "cadu_length",
                "has_asm",
                "spacecraft_id",
                "vcid",
                "mcfc",
                "vcfc",
                "fhp_hex",
                "idle",
                "sha256",
                "cadu_file",
                "tm_frame_file",
            ],
        )
        writer.writeheader()

        for item in run_tshark(args.pcap, args.port):
            payload = item["payload"]
            digest = hashlib.sha256(payload).hexdigest()

            if args.dedup and digest in seen_hashes:
                skipped_duplicates += 1
                continue

            seen_hashes.add(digest)

            # A valid CADU must start with the ASM (1A CF FC 1D)
            has_asm = payload.startswith(ASM)

            if not has_asm:
                skipped_no_asm += 1
                continue

            # Strip the 4-byte ASM to get the actual TM Transfer Frame
            tm_frame = payload[4:]
            header = parse_tm_frame_header(tm_frame)

            if header is None:
                continue

            # FHP = 0x7FE identifies an idle (fill) frame.
            # These contain no user data and are often skipped in analysis.
            idle = header["fhp"] == 0x7FE

            if args.skip_idle and idle:
                skipped_idle += 1
                continue

            extracted += 1

            # Store the full CADU (with ASM)
            cadu_file = cadu_dir / f"cadu_{extracted:06d}_pcapframe_{item['frame_number']}.bin"
            cadu_file.write_bytes(payload)

            tm_frame_file = ""

            # Optionally store the TM frame without ASM for further analysis
            if args.strip_asm:
                tm_frame_path = frame_dir / f"tmframe_{extracted:06d}_pcapframe_{item['frame_number']}.bin"
                tm_frame_path.write_bytes(tm_frame)
                tm_frame_file = str(tm_frame_path)

            writer.writerow(
                {
                    "index": extracted,
                    "pcap_frame": item["frame_number"],
                    "ip_src": item["ip_src"],
                    "ip_dst": item["ip_dst"],
                    "udp_length": item["udp_length"],
                    "cadu_length": len(payload),
                    "has_asm": has_asm,
                    "spacecraft_id": header["spacecraft_id"],
                    "vcid": header["vcid"],
                    "mcfc": header["mcfc"],
                    "vcfc": header["vcfc"],
                    "fhp_hex": f"0x{header['fhp']:03x}",
                    "idle": idle,
                    "sha256": digest,
                    "cadu_file": str(cadu_file),
                    "tm_frame_file": tm_frame_file,
                }
            )

    print("Extraction complete")
    print(f"Input pcap: {args.pcap}")
    print(f"UDP destination port: {args.port}")
    print(f"Output directory: {out_dir}")
    print(f"Extracted CADUs: {extracted}")
    print(f"Skipped duplicates: {skipped_duplicates}")
    print(f"Skipped packets without ASM: {skipped_no_asm}")
    print(f"Skipped idle frames: {skipped_idle}")
    print()
    print(f"CADUs: {cadu_dir}")
    if args.strip_asm:
        print(f"TM frames without ASM: {frame_dir}")
    print(f"Metadata: {metadata_path}")


if __name__ == "__main__":
    main()
