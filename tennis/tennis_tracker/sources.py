"""Where samples come from: a real Thingy:53 over Bluetooth, or a simulator for testing."""

import asyncio
import logging
import math
import random
from typing import Callable, List, Optional

from . import protocol
from .detect import Sample

log = logging.getLogger(__name__)

SamplesCallback = Callable[[str, List[Sample]], None]
MarkerCallback = Callable[[str, float], None]


async def scan(timeout: float = 8.0):
    """Returns [(name, address, rssi)] of tennis sensors nearby, strongest first."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    sensors = []
    for device, adv in found.values():
        name = adv.local_name or device.name or ""
        uuids = [u.lower() for u in adv.service_uuids]
        if name.startswith(protocol.NAME_PREFIX) or protocol.SERVICE_UUID in uuids:
            sensors.append((name or device.address, device.address, adv.rssi))
    return sorted(sensors, key=lambda s: -s[2])


class BleSensor:
    """Streams one Thingy:53 and keeps reconnecting when the link drops."""

    def __init__(self, name: str, address: str, on_samples: SamplesCallback,
                 on_marker: MarkerCallback, rate_hz: int = 200):
        self.name = name
        self.address = address
        self.on_samples = on_samples
        self.on_marker = on_marker
        self.rate_hz = rate_hz
        self.clock = protocol.SampleClock()
        self.client = None

    async def run_forever(self) -> None:
        while True:
            try:
                await self._session()
            except Exception as e:  # bleak raises a variety of OS specific errors
                log.warning("%s: Bluetooth fout: %s", self.name, e)
            self.client = None
            log.info("%s: opnieuw verbinden over 2 s ...", self.name)
            await asyncio.sleep(2)

    async def _session(self) -> None:
        from bleak import BleakClient, BleakScanner

        device = await BleakScanner.find_device_by_address(self.address, timeout=15)
        if device is None:
            log.info("%s niet gevonden; staat hij aan?", self.name)
            return
        disconnected = asyncio.Event()
        async with BleakClient(device, disconnected_callback=lambda _: disconnected.set()) as client:
            self.client = client
            info = protocol.decode_info(await client.read_gatt_char(protocol.INFO_UUID))
            if info.rate_hz != self.rate_hz:
                await client.write_gatt_char(protocol.CONTROL_UUID, protocol.cmd_set_rate(self.rate_hz), response=True)
            self.clock = protocol.SampleClock()
            await client.start_notify(protocol.EVENT_UUID, self._on_event)
            await client.start_notify(protocol.DATA_UUID, self._on_data)
            log.info("%s verbonden (%d Hz)", self.name, self.rate_hz)
            await disconnected.wait()
            log.warning("%s verbinding verbroken", self.name)

    def _on_data(self, _char, data: bytearray) -> None:
        try:
            packet = protocol.decode_packet(bytes(data))
        except ValueError as e:
            log.debug("bad packet: %s", e)
            return
        times = self.clock.sample_times(packet)
        self.on_samples(self.name, [Sample(t, a, g) for t, (a, g) in zip(times, packet.samples)])

    def _on_event(self, _char, data: bytearray) -> None:
        code, t_us = protocol.decode_event(bytes(data))
        if code == protocol.EVENT_BUTTON:
            self.on_marker(self.name, t_us / 1e6)

    async def identify(self) -> None:
        if self.client is not None:
            await self.client.write_gatt_char(protocol.CONTROL_UUID, protocol.cmd_identify(), response=True)


# Simulated stroke patterns in the sensor frame: rotation axis and "up" before the swing.
SIM_PATTERNS = {
    "forehand": ((0.0, 0.2, 1.0), (1.0, 0.0, 0.2)),
    "backhand": ((0.0, -0.2, -1.0), (1.0, 0.0, -0.2)),
    "opslag": ((0.9, 0.1, 0.3), (-0.3, 0.0, -1.0)),
}


class SimulatedSensor:
    """Fake racket that hits a stroke every few seconds; goes through the real packet codec."""

    def __init__(self, name: str, on_samples: SamplesCallback, on_marker: MarkerCallback,
                 rate_hz: int = 200, realtime: bool = True, duration_s: Optional[float] = None,
                 kinds: Optional[List[str]] = None, seed: Optional[int] = None):
        self.name = name
        self.on_samples = on_samples
        self.on_marker = on_marker
        self.rate_hz = rate_hz
        self.realtime = realtime
        self.duration_s = duration_s
        self.kinds = kinds or list(SIM_PATTERNS)
        self.rng = random.Random(seed)
        self.clock = protocol.SampleClock()
        self.strokes_played: List[str] = []

    def _stroke_at(self, t: float, plan):
        """Gyro (dps) and accel (g) contributions of the planned stroke at time t."""
        kind, t_peak, peak, hit = plan
        axis, _up = SIM_PATTERNS[kind]
        n = math.sqrt(sum(a * a for a in axis))
        shape = math.exp(-((t - t_peak) / 0.07) ** 2)
        gyro = tuple(a / n * peak * shape for a in axis)
        accel = (0.0, 0.0, 0.0)
        if hit and abs(t - t_peak) < 0.5 / self.rate_hz:
            accel = (0.0, 6.0, 0.0)
        return gyro, accel

    async def run_forever(self) -> None:
        dt = 1.0 / self.rate_hz
        t = 0.0
        seq = 0
        batch = []
        plan = None
        next_stroke = 1.0 + self.rng.uniform(0, 1)
        up = SIM_PATTERNS[self.kinds[0]][1]

        while self.duration_s is None or t < self.duration_s:
            if plan is None and t >= next_stroke:
                kind = self.rng.choice(self.kinds)
                plan = (kind, t + 0.25, self.rng.uniform(900, 1800), self.rng.random() < 0.85)
                up = SIM_PATTERNS[kind][1]
                self.strokes_played.append(kind)
            if plan is not None and t > plan[1] + 0.6:
                plan = None
                next_stroke = t + self.rng.uniform(1.5, 3.0)

            n = math.sqrt(sum(u * u for u in up))
            accel = [u / n + self.rng.gauss(0, 0.02) for u in up]
            gyro = [self.rng.gauss(0, 1.5) for _ in range(3)]
            if plan is not None:
                g, a = self._stroke_at(t, plan)
                gyro = [x + y for x, y in zip(gyro, g)]
                accel = [x + y for x, y in zip(accel, a)]
            batch.append((tuple(accel), tuple(gyro)))

            if len(batch) == 10:
                data = protocol.encode_packet(seq, int((t - dt * 9) * 1e6), self.rate_hz, batch)
                packet = protocol.decode_packet(data)
                times = self.clock.sample_times(packet)
                self.on_samples(self.name, [Sample(tt, a, g) for tt, (a, g) in zip(times, packet.samples)])
                batch = []
                seq += 1
                if self.realtime:
                    await asyncio.sleep(10 * dt)
                else:
                    await asyncio.sleep(0)
            t += dt

    async def identify(self) -> None:
        log.info("%s: (simulator) knippert wit", self.name)
