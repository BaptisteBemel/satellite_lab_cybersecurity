#!/usr/bin/env python3

import argparse
from pathlib import Path


def xor_all(data):
    """Compute XOR of all bytes in the data."""
    value = 0
    for byte in data:
        value ^= byte
    return value


def recompute_cfs_checksum(packet):
    """
    Recompute the cFS checksum to make the packet valid.
    
    cFS validation rule: XOR of all bytes in the packet (primary header,
    secondary header, and data) must equal 0xFF.
    
    The checksum byte is located at offset 7 (the last byte of the secondary
    header). To recompute it:
    1. Set the checksum byte to 0.
    2. Compute XOR of all bytes.
    3. Set checksum = XOR_all_bytes ^ 0xFF.
    """
    if len(packet) < 8:
        raise ValueError("Packet too short for cFS command checksum")

    packet[7] = 0x00
    packet[7] = xor_all(packet) ^ 0xFF


def set_sequence_count(packet, seq_count):
    """
    Set the CCSDS sequence count in the primary header.
    
    The sequence count is stored in bits 13-0 of word 1 (bytes 2-3).
    Bits 15-14 are reserved for sequence flags and must be preserved.
    """
    if len(packet) < 4:
        raise ValueError("Packet too short for CCSDS sequence header")

    word1 = int.from_bytes(packet[2:4], "big")
    seq_flags = word1 & 0xC000          # Preserve bits 15-14
    new_word1 = seq_flags | (seq_count & 0x3FFF)  # Set bits 13-0
    packet[2:4] = new_word1.to_bytes(2, "big")


def print_packet_summary(packet):
    """Display the key fields of a TC packet for verification."""
    if len(packet) < 8:
        print("[!] Packet too short for full summary")
        return

    word0 = int.from_bytes(packet[0:2], "big")
    word1 = int.from_bytes(packet[2:4], "big")
    word2 = int.from_bytes(packet[4:6], "big")

    version = (word0 >> 13) & 0x07
    packet_type = (word0 >> 12) & 0x01
    sec_hdr_flag = (word0 >> 11) & 0x01
    apid = word0 & 0x07FF
    seq_flags = (word1 >> 14) & 0x03
    seq_count = word1 & 0x3FFF
    packet_data_length = word2
    total_packet_length = 6 + packet_data_length + 1

    function_code = packet[6]
    checksum = packet[7]
    xor_value = xor_all(packet)

    print("Packet summary")
    print(f"    length: {len(packet)}")
    print(f"    CCSDS version: {version}")
    print(f"    CCSDS type: {packet_type}")
    print(f"    secondary header flag: {sec_hdr_flag}")
    print(f"    APID: {apid}")
    print(f"    sequence flags: {seq_flags}")
    print(f"    sequence count: {seq_count}")
    print(f"    packet data length field: {packet_data_length}")
    print(f"    expected total length from header: {total_packet_length}")
    print(f"    function code: {function_code}")
    print(f"    checksum byte: 0x{checksum:02x}")
    print(f"    XOR(all bytes): 0x{xor_value:02x}")
    print(f"    checksum valid like cFS: {xor_value == 0xFF}")


def main():
    parser = argparse.ArgumentParser(description="Forge a cFS-like TC from a captured template")
    parser.add_argument("template", help="Input captured TC payload .bin")
    parser.add_argument("-o", "--output", required=True, help="Output forged TC payload .bin")
    parser.add_argument("--seq-count", type=int, help="Set CCSDS sequence count")
    parser.add_argument("--function-code", type=int, help="Set cFS function code byte")
    parser.add_argument("--no-checksum", action="store_true", help="Do not recompute cFS checksum")
    args = parser.parse_args()

    packet = bytearray(Path(args.template).read_bytes())

    print("Original template:")
    print_packet_summary(packet)

    if args.seq_count is not None:
        print(f"Setting sequence count to {args.seq_count}")
        set_sequence_count(packet, args.seq_count)

    if args.function_code is not None:
        if len(packet) < 8:
            raise SystemExit("Packet too short to set function code")
        print(f"Setting function code to {args.function_code}")
        packet[6] = args.function_code & 0xFF

    if not args.no_checksum:
        print("Recomputing cFS-like checksum")
        recompute_cfs_checksum(packet)

    print("Forged packet:")
    print_packet_summary(packet)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(packet)

    print(f"Wrote forged TC: {output_path}")


if __name__ == "__main__":
    main()
