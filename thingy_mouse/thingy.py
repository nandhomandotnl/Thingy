"""Bluetooth LE connection to a Nordic Thingy:52 (stock firmware).

Protocol reference: https://nordicsemiconductor.github.io/Nordic-Thingy52-FW/documentation/firmware_architecture.html
"""

import asyncio
import logging
import struct
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

log = logging.getLogger(__name__)


def thingy_uuid(short: int) -> str:
    return f"ef68{short:04x}-9b35-4933-9b10-52ffa9740042"


CONFIG_SERVICE = thingy_uuid(0x0100)
LED_CHAR = thingy_uuid(0x0301)
BUTTON_CHAR = thingy_uuid(0x0302)
MOTION_CONFIG_CHAR = thingy_uuid(0x0401)
TAP_CHAR = thingy_uuid(0x0402)
RAW_DATA_CHAR = thingy_uuid(0x0406)

# Motion config: step interval (ms), temperature comp interval (ms),
# magnetometer comp interval (ms), motion processing frequency (Hz), wake on motion.
MOTION_CONFIG_FORMAT = "<HHHHB"

# LED colours for the "breathe" mode of the stock firmware.
LED_RED, LED_GREEN, LED_YELLOW, LED_BLUE, LED_PURPLE, LED_CYAN, LED_WHITE = range(1, 8)


def parse_raw_motion(data: bytes):
    """Returns (accel in g, gyro in deg/s, compass in uT) as 3-tuples."""
    v = struct.unpack("<9h", data[:18])
    accel = tuple(x / 1024.0 for x in v[0:3])  # Q6.10
    gyro = tuple(x / 32.0 for x in v[3:6])  # Q11.5
    compass = tuple(x / 16.0 for x in v[6:9])  # Q12.4
    return accel, gyro, compass


class Thingy:
    def __init__(
        self,
        address: Optional[str],
        motion_hz: int,
        on_gyro: Callable,
        on_button: Callable[[bool], None],
        on_tap: Callable[[int, int], None],
        on_connection: Callable[[bool], None] = lambda connected: None,
    ):
        self.address = address
        self.motion_hz = motion_hz
        self.on_gyro = on_gyro
        self.on_button = on_button
        self.on_tap = on_tap
        self.on_connection = on_connection
        self.client: Optional[BleakClient] = None

    async def find(self, timeout: float = 15.0):
        if self.address:
            log.info("Looking for Thingy at %s ...", self.address)
            return await BleakScanner.find_device_by_address(self.address, timeout=timeout)
        log.info("Scanning for a Thingy:52 ...")
        return await BleakScanner.find_device_by_filter(
            lambda d, ad: CONFIG_SERVICE in [u.lower() for u in ad.service_uuids]
            or (d.name or "").lower().startswith("thingy"),
            timeout=timeout,
        )

    async def run_forever(self) -> None:
        """Connect, stream, and reconnect whenever the link drops."""
        while True:
            try:
                await self._session()
            except (BleakError, asyncio.TimeoutError, OSError) as e:
                log.warning("Bluetooth error: %s", e)
            self.client = None
            self.on_connection(False)
            await asyncio.sleep(2)

    async def _session(self) -> None:
        device = await self.find()
        if device is None:
            log.info("No Thingy found; is it switched on and not connected to the phone app?")
            return
        disconnected = asyncio.Event()
        async with BleakClient(device, disconnected_callback=lambda _: disconnected.set()) as client:
            self.client = client
            log.info("Connected to %s (%s)", device.name, device.address)
            await self._configure_motion(client)
            await client.start_notify(RAW_DATA_CHAR, self._raw_handler)
            await client.start_notify(BUTTON_CHAR, lambda _, d: self.on_button(d[0] == 1))
            await client.start_notify(TAP_CHAR, lambda _, d: self.on_tap(d[0], d[1]))
            self.on_connection(True)
            await disconnected.wait()
            log.warning("Thingy disconnected")

    async def _configure_motion(self, client: BleakClient) -> None:
        current = await client.read_gatt_char(MOTION_CONFIG_CHAR)
        step, temp, mag, _freq, wake = struct.unpack(MOTION_CONFIG_FORMAT, current[:9])
        new = struct.pack(MOTION_CONFIG_FORMAT, step, temp, mag, self.motion_hz, wake)
        await client.write_gatt_char(MOTION_CONFIG_CHAR, new, response=True)
        log.info("Motion processing frequency set to %d Hz", self.motion_hz)

    def _raw_handler(self, _char, data: bytearray) -> None:
        _accel, gyro, _compass = parse_raw_motion(bytes(data))
        self.on_gyro(gyro)

    async def set_led_constant(self, r: int, g: int, b: int) -> None:
        await self._write_led(bytes([1, r, g, b]))

    async def set_led_breathe(self, colour: int, intensity: int = 20, delay_ms: int = 1500) -> None:
        await self._write_led(struct.pack("<BBBH", 2, colour, intensity, delay_ms))

    async def _write_led(self, payload: bytes) -> None:
        if self.client is None or not self.client.is_connected:
            return
        try:
            await self.client.write_gatt_char(LED_CHAR, payload, response=True)
        except BleakError as e:
            log.debug("LED write failed: %s", e)
