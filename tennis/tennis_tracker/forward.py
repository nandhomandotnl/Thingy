"""Send strokes from each Pi to one central computer over the network (UDP + JSON)."""

import asyncio
import csv
import json
import logging
import socket
from pathlib import Path
from typing import Callable, Dict

from .storage import STROKE_FIELDS

log = logging.getLogger(__name__)


def parse_host_port(value: str, default_port: int = 5005) -> "tuple[str, int]":
    host, _, port = value.rpartition(":")
    if not host:
        return value, default_port
    return host, int(port)


class Forwarder:
    def __init__(self, target: str):
        self.addr = parse_host_port(target)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, row: Dict[str, object]) -> None:
        try:
            self.sock.sendto(json.dumps({"type": "slag", **row}).encode(), self.addr)
        except OSError as e:
            log.warning("Doorsturen naar %s:%d mislukt: %s", *self.addr, e)


class _CollectorProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_row: Callable[[Dict[str, object]], None]):
        self.on_row = on_row

    def datagram_received(self, data, addr):
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if msg.get("type") == "slag":
            msg.pop("type")
            self.on_row(msg)


async def collect(listen: str, out_file: Path, on_row: Callable[[Dict[str, object]], None]) -> None:
    host, port = parse_host_port(listen)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    new = not out_file.exists()
    with open(out_file, "a", newline="", buffering=1) as f:
        writer = csv.DictWriter(f, fieldnames=STROKE_FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()

        def handle(row):
            writer.writerow(row)
            on_row(row)

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: _CollectorProtocol(handle), local_addr=(host, port))
        log.info("Wacht op slagen op %s:%d, opslaan in %s", host, port, out_file)
        try:
            await asyncio.Event().wait()
        finally:
            transport.close()
