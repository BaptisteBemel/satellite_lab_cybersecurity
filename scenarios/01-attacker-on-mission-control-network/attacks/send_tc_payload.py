#!/usr/bin/env python3

import argparse
import socket
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Send a raw TC payload to NOS3/cFS")
    parser.add_argument("payload", help="TC payload .bin file")
    parser.add_argument("--dst-ip", default="192.168.20.10", help="NOS3/cFS destination IP")
    parser.add_argument("--dst-port", type=int, default=5012, help="NOS3/cFS UDP destination port")
    parser.add_argument("--count", type=int, default=1, help="Number of sends")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between sends")
    args = parser.parse_args()

    payload_path = Path(args.payload)
    payload = payload_path.read_bytes()

    print(f"Payload: {payload_path}")
    print(f"Payload length: {len(payload)} bytes")
    print(f"Destination: {args.dst_ip}:{args.dst_port}")
    print(f"Count: {args.count}")

    # Create a UDP socket. The OS will add the UDP and IP headers.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    for i in range(args.count):
        print(f"Sending {i + 1}/{args.count}")
        sock.sendto(payload, (args.dst_ip, args.dst_port))
        time.sleep(args.delay)

    print("Done")


if __name__ == "__main__":
    main()
