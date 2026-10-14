"""Bluetooth protocol of the Thingy:53 tennis firmware (firmware/thingy53_tennis)."""

import struct
from dataclasses import dataclass
from typing import List, Tuple


def _uuid(n: int) -> str:
    return f"7e51{n:04x}-0c1a-4b7e-9a35-54656e6e6973"


SERVICE_UUID = _uuid(1)
DATA_UUID = _uuid(2)
EVENT_UUID = _uuid(3)
CONTROL_UUID = _uuid(4)
INFO_UUID = _uuid(5)

NAME_PREFIX = "Tennis-"

ACCEL_LSB_PER_G = 32768 / 16
GYRO_LSB_PER_DPS = 32768 / 2000

HEADER = struct.Struct("<BBHIH")  # type, n, seq, t_us of first sample, rate_hz
SAMPLE = struct.Struct("<6h")  # ax ay az gx gy gz
INFO = struct.Struct("<BHBH")  # proto, rate_hz, accel_range_g, gyro_range_dps
EVENT = struct.Struct("<BBI")  # type, code, t_us

PACKET_TYPE_DATA = 1
EVENT_BUTTON = 1

Vec3 = Tuple[float, float, float]


@dataclass
class Packet:
    seq: int
    t_us: int
    rate_hz: int
    samples: List[Tuple[Vec3, Vec3]]  # (accel in g, gyro in deg/s)


@dataclass
class Info:
    proto: int
    rate_hz: int
    accel_range_g: int
    gyro_range_dps: int


def decode_packet(data: bytes) -> Packet:
    ptype, n, seq, t_us, rate = HEADER.unpack_from(data, 0)
    if ptype != PACKET_TYPE_DATA:
        raise ValueError(f"unknown packet type {ptype}")
    if len(data) < HEADER.size + n * SAMPLE.size:
        raise ValueError("packet too short")
    samples = []
    for i in range(n):
        v = SAMPLE.unpack_from(data, HEADER.size + i * SAMPLE.size)
        accel = tuple(x / ACCEL_LSB_PER_G for x in v[:3])
        gyro = tuple(x / GYRO_LSB_PER_DPS for x in v[3:])
        samples.append((accel, gyro))
    return Packet(seq, t_us, rate, samples)


def encode_packet(seq: int, t_us: int, rate_hz: int, samples) -> bytes:
    """Inverse of decode_packet; used by the simulator and tests."""

    def q(v, scale):
        return max(-32767, min(32767, round(v * scale)))

    out = bytearray(HEADER.pack(PACKET_TYPE_DATA, len(samples), seq & 0xFFFF, t_us & 0xFFFFFFFF, rate_hz))
    for accel, gyro in samples:
        out += SAMPLE.pack(*(q(a, ACCEL_LSB_PER_G) for a in accel), *(q(g, GYRO_LSB_PER_DPS) for g in gyro))
    return bytes(out)


def decode_info(data: bytes) -> Info:
    return Info(*INFO.unpack_from(data, 0))


def decode_event(data: bytes) -> "tuple[int, int]":
    _type, code, t_us = EVENT.unpack_from(data, 0)
    return code, t_us


def cmd_identify() -> bytes:
    return b"\x01"


def cmd_set_rate(rate_hz: int) -> bytes:
    if rate_hz not in (50, 100, 200, 400):
        raise ValueError("rate must be 50, 100, 200 or 400 Hz")
    return struct.pack("<BH", 2, rate_hz)


class SampleClock:
    """Turns packet timestamps into continuous seconds and counts lost packets."""

    def __init__(self):
        self._last_t_us = None
        self._wraps = 0
        self._last_seq = None
        self.lost_packets = 0

    def sample_times(self, packet: Packet) -> List[float]:
        if self._last_t_us is not None and packet.t_us < self._last_t_us - 2**31:
            self._wraps += 1
        self._last_t_us = packet.t_us
        if self._last_seq is not None:
            gap = (packet.seq - self._last_seq - 1) & 0xFFFF
            if gap < 1000:
                self.lost_packets += gap
        self._last_seq = packet.seq
        t0 = (packet.t_us + self._wraps * 2**32) / 1e6
        dt = 1.0 / packet.rate_hz
        return [t0 + i * dt for i in range(len(packet.samples))]
