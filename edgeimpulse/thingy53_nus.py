"""Lees accelerometer (en gyroscoop) van een Thingy:53 met de Edge Impulse-firmware via NUS.

De firmware (https://github.com/edgeimpulse/firmware-nordic-thingy53) stuurt geen live
stroom, maar neemt op verzoek een meting op en stuurt die daarna in één keer terug:

  1. wij sturen een JSON "sample"-verzoek naar de RX-karakteristiek (afgesloten met \\n)
  2. de Thingy meet `length` ms en stuurt een JSON-bericht met de meting als
     base64-gecodeerde CBOR in het veld "body", gevolgd door {"sampleFinished": true}

Gebruik:  python thingy53_nus.py [--sensor Accelerometer] [--length 2000] [--freq 100]
"""

import argparse
import asyncio
import base64
import io
import json
import re
from dataclasses import dataclass
from typing import List, Optional

import cbor2
from bleak import BleakClient, BleakScanner

# De Edge Impulse-firmware gebruikt het NUS-protocol met eigen UUID's (zie src/ble/nus.h).
NUS_RX = "e2a00002-ec31-4ec3-a97a-1c34d87e9878"  # wij schrijven
NUS_TX = "e2a00003-ec31-4ec3-a97a-1c34d87e9878"  # Thingy notificeert


@dataclass
class ImuSample:
    t: float  # tijd sinds start (s)
    acc_x: float  # m/s2
    acc_y: float
    acc_z: float
    gyro_x: Optional[float] = None  # alleen als de firmware een gyroscoop meestuurt
    gyro_y: Optional[float] = None
    gyro_z: Optional[float] = None


# Asnamen in de CBOR-data -> veldnamen in ImuSample
AXES = {"accX": "acc_x", "accY": "acc_y", "accZ": "acc_z",
        "gyrX": "gyro_x", "gyrY": "gyro_y", "gyrZ": "gyro_z"}


def parse_samples(body_b64: str) -> List[ImuSample]:
    data = cbor2.CBORDecoder(io.BytesIO(base64.b64decode(body_b64))).decode()
    payload = data["payload"]
    names = [s["name"] for s in payload["sensors"]]
    dt = payload["interval_ms"] / 1000
    samples = []
    for i, row in enumerate(payload["values"]):
        fields = {AXES[n]: v for n, v in zip(names, row) if n in AXES}
        samples.append(ImuSample(t=i * dt, **fields))
    return samples


async def read_imu(sensor: str, length_ms: int, freq_hz: float) -> List[ImuSample]:
    device = await BleakScanner.find_device_by_name("EdgeImpulse", timeout=10)
    if device is None:
        raise RuntimeError("Geen Thingy:53 met naam 'EdgeImpulse' gevonden")

    received = bytearray()
    async with BleakClient(device) as client:
        await client.start_notify(NUS_TX, lambda _, data: received.extend(data))
        await asyncio.sleep(2)  # de Thingy stuurt eerst een "hello"-bericht
        print(received.decode(errors="replace"))
        received.clear()

        request = {"type": "ws", "direction": "rx", "message": {"sample": {
            "label": "test", "length": length_ms, "interval": 1000 / freq_hz,
            "hmacKey": "", "path": "/api/training/data", "sensor": sensor}}}
        msg = (json.dumps(request) + "\n").encode()
        chunk = client.services.get_characteristic(NUS_RX).max_write_without_response_size
        for i in range(0, len(msg), chunk):
            await client.write_gatt_char(NUS_RX, msg[i:i + chunk], response=False)

        print(f"Meten: {length_ms} ms op {freq_hz} Hz ...")
        for _ in range(int(length_ms / 1000 + 30) * 10):
            if b'"sampleFinished":true' in received:
                break
            await asyncio.sleep(0.1)
        else:
            raise TimeoutError("Geen meting ontvangen")

    body = re.search(rb'"body":"([A-Za-z0-9+/=]*)"', received)
    if body is None:
        raise RuntimeError("Geen meetdata in het antwoord:\n" + received.decode(errors="replace"))
    return parse_samples(body.group(1).decode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sensor", default="Accelerometer", help="sensornaam uit het hello-bericht")
    parser.add_argument("--length", type=int, default=2000, help="meetduur in ms")
    parser.add_argument("--freq", type=float, default=100, help="frequentie in Hz (20, 62.5 of 100)")
    args = parser.parse_args()

    for s in asyncio.run(read_imu(args.sensor, args.length, args.freq)):
        print(s)
