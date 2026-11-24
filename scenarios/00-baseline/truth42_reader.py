#!/usr/bin/env python3
"""Decode NOS3 Truth42 UDP telemetry without Yamcs or COSMOS.

Live capture uses a Linux AF_PACKET socket and therefore requires root.
Classic PCAP files captured on Ethernet, Linux SLL or Linux SLL2 are also
supported without third-party Python packages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import socket
import struct
import sys
from pathlib import Path
from typing import BinaryIO, Iterator


TRUTH42 = struct.Struct(">6h38d")
TRUTH42_DATA_LENGTH = TRUTH42.size  # 316 bytes


class DecodeError(ValueError):
    """Raised when a packet is not a supported Truth42 datagram."""


def vector(values: tuple[float, ...], start: int, size: int = 3) -> list[float]:
    return list(values[start : start + size])


def ecef_to_geodetic(position_m: list[float]) -> dict[str, float]:
    """Return approximate WGS-84 latitude, longitude and altitude."""
    x, y, z = position_m
    a = 6_378_137.0
    flattening = 1.0 / 298.257223563
    e2 = flattening * (2.0 - flattening)
    horizontal = math.hypot(x, y)

    if horizontal == 0.0:
        latitude = math.copysign(math.pi / 2.0, z)
        longitude = 0.0
        altitude = abs(z) - a * math.sqrt(1.0 - e2)
    else:
        longitude = math.atan2(y, x)
        latitude = math.atan2(z, horizontal * (1.0 - e2))
        for _ in range(8):
            sin_latitude = math.sin(latitude)
            radius = a / math.sqrt(1.0 - e2 * sin_latitude * sin_latitude)
            altitude = horizontal / math.cos(latitude) - radius
            latitude = math.atan2(
                z,
                horizontal * (1.0 - e2 * radius / (radius + altitude)),
            )
        sin_latitude = math.sin(latitude)
        radius = a / math.sqrt(1.0 - e2 * sin_latitude * sin_latitude)
        altitude = horizontal / math.cos(latitude) - radius

    return {
        "latitude_deg": math.degrees(latitude),
        "longitude_deg": math.degrees(longitude),
        "altitude_m": altitude,
    }


def decode_truth42(payload: bytes) -> dict[str, object]:
    """Decode the 316 useful bytes produced by truth_42_sim."""
    if len(payload) < TRUTH42_DATA_LENGTH:
        raise DecodeError(
            f"Truth42 payload too short: {len(payload)} bytes "
            f"(expected at least {TRUTH42_DATA_LENGTH})"
        )

    trailing = payload[TRUTH42_DATA_LENGTH:]
    if trailing and any(trailing):
        raise DecodeError(
            f"unexpected non-zero data after byte {TRUTH42_DATA_LENGTH}"
        )

    year, doy, month, day, hour, minute, *numbers = TRUTH42.unpack_from(payload)
    values = tuple(numbers)

    result: dict[str, object] = {
        "time": {
            "year": year,
            "day_of_year": doy,
            "month": month,
            "day": day,
            "hour": hour,
            "minute": minute,
            "second": values[0],
        },
        "position_eci_m": vector(values, 1),
        "velocity_eci_m_s": vector(values, 4),
        "sun_vector_body": vector(values, 7),
        "magnetic_field_body_t": vector(values, 10),
        "angular_momentum_body_nms": vector(values, 13),
        "angular_velocity_body_rad_s": vector(values, 16),
        "quaternion_body_in_inertial": vector(values, 19, 4),
        "position_ecef_m": vector(values, 23),
        "velocity_ecef_m_s": vector(values, 26),
        "acceleration_body_m_s2": vector(values, 29),
        "gyro_body_rad_s": vector(values, 32),
        "reaction_wheel_momentum_nms": vector(values, 35),
        "wire_length": len(payload),
    }
    result["geodetic_wgs84"] = ecef_to_geodetic(result["position_ecef_m"])
    return result


def format_simulation_time(time_fields: dict[str, object]) -> str:
    second = float(time_fields["second"])
    return (
        f"{int(time_fields['year']):04d}-DOY{int(time_fields['day_of_year']):03d} "
        f"({int(time_fields['month']):02d}-{int(time_fields['day']):02d}) "
        f"{int(time_fields['hour']):02d}:{int(time_fields['minute']):02d}:{second:09.6f} UTC"
    )


def format_vector(values: list[float], scale: float = 1.0, digits: int = 6) -> str:
    return "[" + ", ".join(f"{value * scale:.{digits}f}" for value in values) + "]"


def print_pretty(
    decoded: dict[str, object],
    capture_time: float,
    source: tuple[str, int],
    destination: tuple[str, int],
) -> None:
    captured = dt.datetime.fromtimestamp(
        capture_time, tz=dt.timezone.utc
    ).isoformat(timespec="milliseconds")
    geodetic = decoded["geodetic_wgs84"]

    print(
        f"\n[{captured}] {source[0]}:{source[1]} -> "
        f"{destination[0]}:{destination[1]} "
        f"({decoded['wire_length']} UDP payload bytes)"
    )
    print(f"  Simulation time : {format_simulation_time(decoded['time'])}")
    print(
        "  ECI position km : "
        f"{format_vector(decoded['position_eci_m'], scale=0.001, digits=3)}"
    )
    print(
        "  ECI velocity m/s: "
        f"{format_vector(decoded['velocity_eci_m_s'], digits=3)}"
    )
    print(
        "  Geodetic WGS-84 : "
        f"lat={geodetic['latitude_deg']:.6f} deg, "
        f"lon={geodetic['longitude_deg']:.6f} deg, "
        f"alt={geodetic['altitude_m'] / 1000.0:.3f} km"
    )
    print(
        "  Quaternion      : "
        f"{format_vector(decoded['quaternion_body_in_inertial'], digits=7)}"
    )
    print(
        "  Angular velocity: "
        f"{format_vector(decoded['angular_velocity_body_rad_s'], digits=7)} rad/s"
    )
    print(
        "  Sun vector body : "
        f"{format_vector(decoded['sun_vector_body'], digits=7)}"
    )
    print(
        "  Magnetic field  : "
        f"{format_vector(decoded['magnetic_field_body_t'], scale=1e6, digits=3)} uT"
    )
    print(
        "  Body acceleration: "
        f"{format_vector(decoded['acceleration_body_m_s2'], digits=7)} m/s^2"
    )
    print(
        "  Reaction wheels : "
        f"{format_vector(decoded['reaction_wheel_momentum_nms'], digits=7)} Nms"
    )


def extract_udp_ipv4(frame: bytes, linktype: int) -> tuple[str, int, str, int, bytes] | None:
    """Extract an unfragmented UDP datagram from a supported link layer."""
    if linktype == 1:  # Ethernet
        if len(frame) < 14:
            return None
        offset = 14
        ethertype = struct.unpack_from("!H", frame, 12)[0]
        while ethertype in (0x8100, 0x88A8):
            if len(frame) < offset + 4:
                return None
            ethertype = struct.unpack_from("!H", frame, offset + 2)[0]
            offset += 4
        if ethertype != 0x0800:
            return None
    elif linktype == 113:  # Linux cooked capture v1
        if len(frame) < 16 or struct.unpack_from("!H", frame, 14)[0] != 0x0800:
            return None
        offset = 16
    elif linktype == 276:  # Linux cooked capture v2
        if len(frame) < 20 or struct.unpack_from("!H", frame, 0)[0] != 0x0800:
            return None
        offset = 20
    else:
        raise DecodeError(f"unsupported PCAP link type: {linktype}")

    if len(frame) < offset + 20 or frame[offset] >> 4 != 4:
        return None
    ihl = (frame[offset] & 0x0F) * 4
    if ihl < 20 or len(frame) < offset + ihl + 8 or frame[offset + 9] != 17:
        return None

    flags_fragment = struct.unpack_from("!H", frame, offset + 6)[0]
    if flags_fragment & 0x3FFF:  # Truth42 fits in one datagram; skip fragments.
        return None

    source_ip = socket.inet_ntoa(frame[offset + 12 : offset + 16])
    destination_ip = socket.inet_ntoa(frame[offset + 16 : offset + 20])
    udp_offset = offset + ihl
    source_port, destination_port, udp_length = struct.unpack_from(
        "!HHH", frame, udp_offset
    )
    if udp_length < 8 or len(frame) < udp_offset + udp_length:
        return None
    payload = frame[udp_offset + 8 : udp_offset + udp_length]
    return source_ip, source_port, destination_ip, destination_port, payload


def live_frames(interface: str) -> Iterator[tuple[float, bytes, int]]:
    if not hasattr(socket, "AF_PACKET"):
        raise RuntimeError("live capture is only supported on Linux")
    raw_socket = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
    try:
        # Avoid printing a second copy when Linux forwards an intercepted frame.
        raw_socket.setsockopt(263, 23, 1)  # SOL_PACKET, PACKET_IGNORE_OUTGOING
    except OSError:
        pass
    raw_socket.bind((interface, 0))
    try:
        while True:
            frame, _ = raw_socket.recvfrom(65_535)
            yield dt.datetime.now().timestamp(), frame, 1
    finally:
        raw_socket.close()


def pcap_frames(stream: BinaryIO) -> Iterator[tuple[float, bytes, int]]:
    header = stream.read(24)
    if len(header) != 24:
        raise DecodeError("invalid or truncated PCAP global header")

    magic = header[:4]
    formats = {
        b"\xd4\xc3\xb2\xa1": ("<", 1_000_000.0),
        b"\xa1\xb2\xc3\xd4": (">", 1_000_000.0),
        b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000.0),
        b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000.0),
    }
    if magic not in formats:
        raise DecodeError("unsupported capture format (classic PCAP required, not PCAPNG)")

    endian, timestamp_divisor = formats[magic]
    linktype = struct.unpack_from(endian + "I", header, 20)[0]
    packet_header = struct.Struct(endian + "IIII")

    while True:
        record = stream.read(packet_header.size)
        if not record:
            return
        if len(record) != packet_header.size:
            raise DecodeError("truncated PCAP packet header")
        seconds, fraction, included_length, _ = packet_header.unpack(record)
        if included_length > 16 * 1024 * 1024:
            raise DecodeError("implausibly large PCAP record")
        frame = stream.read(included_length)
        if len(frame) != included_length:
            raise DecodeError("truncated PCAP packet data")
        yield seconds + fraction / timestamp_divisor, frame, linktype


def json_record(
    decoded: dict[str, object],
    capture_time: float,
    source: tuple[str, int],
    destination: tuple[str, int],
) -> str:
    record = {
        "capture_time_utc": dt.datetime.fromtimestamp(
            capture_time, tz=dt.timezone.utc
        ).isoformat(),
        "source": {"ip": source[0], "port": source[1]},
        "destination": {"ip": destination[0], "port": destination[1]},
        **decoded,
    }
    return json.dumps(record, separators=(",", ":"), allow_nan=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read and decode NOS3 Truth42 UDP telemetry."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--interface", "-i", default="eth1", help="live interface (default: eth1)"
    )
    source.add_argument(
        "--pcap", type=Path, help="read a classic PCAP file instead of capturing live"
    )
    parser.add_argument("--port", type=int, default=5111, help="UDP destination port")
    parser.add_argument("--source-ip", help="only accept this IPv4 source")
    parser.add_argument("--destination-ip", help="only accept this IPv4 destination")
    parser.add_argument("--json", action="store_true", help="emit one JSON object per packet")
    parser.add_argument("--once", action="store_true", help="exit after one decoded packet")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.pcap:
        stream = args.pcap.open("rb")
        frames = pcap_frames(stream)
    else:
        stream = None
        frames = live_frames(args.interface)
        print(
            f"Listening on {args.interface} for UDP destination port {args.port}...",
            file=sys.stderr,
        )

    decoded_count = 0
    try:
        for capture_time, frame, linktype in frames:
            datagram = extract_udp_ipv4(frame, linktype)
            if datagram is None:
                continue
            source_ip, source_port, destination_ip, destination_port, payload = datagram
            if destination_port != args.port:
                continue
            if args.source_ip and source_ip != args.source_ip:
                continue
            if args.destination_ip and destination_ip != args.destination_ip:
                continue

            try:
                decoded = decode_truth42(payload)
            except DecodeError as error:
                print(
                    f"Ignored {source_ip}:{source_port} -> "
                    f"{destination_ip}:{destination_port}: {error}",
                    file=sys.stderr,
                )
                continue

            if args.json:
                print(
                    json_record(
                        decoded,
                        capture_time,
                        (source_ip, source_port),
                        (destination_ip, destination_port),
                    ),
                    flush=True,
                )
            else:
                print_pretty(
                    decoded,
                    capture_time,
                    (source_ip, source_port),
                    (destination_ip, destination_port),
                )
                sys.stdout.flush()

            decoded_count += 1
            if args.once:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if stream is not None:
            stream.close()

    if decoded_count == 0:
        print("No Truth42 packet decoded.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DecodeError, OSError, RuntimeError) as error:
        print(f"truth42_reader: {error}", file=sys.stderr)
        raise SystemExit(2)
