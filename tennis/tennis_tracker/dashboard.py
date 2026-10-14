"""Live dashboard in the browser: a tiny local web server that pushes data to the page.

Uses only the Python standard library (no internet needed, works on the court).
The page receives Server-Sent Events from /events.
"""

import json
import logging
import queue
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Dict, List, Optional

from .detect import Sample

log = logging.getLogger(__name__)

DISPLAY_RATE_HZ = 50
PUBLISH_INTERVAL_S = 0.1


class Broadcaster:
    """Fans messages out to every connected browser; safe to call from any thread."""

    def __init__(self, history: int = 500):
        self._clients: List[queue.Queue] = []
        self._lock = threading.Lock()
        self._hello: Optional[dict] = None
        self._history: deque = deque(maxlen=history)

    def set_hello(self, msg: dict) -> None:
        self._hello = msg
        self.publish(msg, keep=False)

    def publish(self, msg: dict, keep: bool = False) -> None:
        data = json.dumps(msg, separators=(",", ":"))
        if keep:
            self._history.append(data)
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass  # slow browser: drop live data rather than stall the tracker

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2000)
        if self._hello:
            q.put_nowait(json.dumps(self._hello))
        for data in list(self._history):
            q.put_nowait(data)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)


class SampleThinner:
    """Reduces 200 Hz sensor data to ~50 Hz for the screen, keeping the peaks."""

    def __init__(self, broadcaster: Broadcaster, sensor: str):
        self.b = broadcaster
        self.sensor = sensor
        self._block: List[Sample] = []
        self._t: List[float] = []
        self._g: List[float] = []
        self._a: List[float] = []
        self._last_publish = 0.0

    def add(self, samples: List[Sample], rate_hz: int) -> None:
        per_point = max(1, round(rate_hz / DISPLAY_RATE_HZ))
        for s in samples:
            self._block.append(s)
            if len(self._block) >= per_point:
                g = max(sum(v * v for v in x.gyro) ** 0.5 for x in self._block)
                a = max(sum(v * v for v in x.accel) ** 0.5 for x in self._block)
                self._t.append(round(self._block[-1].t, 3))
                self._g.append(round(g))
                self._a.append(round(a, 2))
                self._block = []
        now = time.monotonic()
        if self._t and now - self._last_publish >= PUBLISH_INTERVAL_S:
            self.b.publish({"type": "samples", "sensor": self.sensor, "t": self._t, "g": self._g, "a": self._a})
            self._t, self._g, self._a = [], [], []
            self._last_publish = now


def _page() -> bytes:
    return resources.files("tennis_tracker").joinpath("dashboard.html").read_bytes()


def _make_handler(broadcaster: Broadcaster):
    page = _page()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the console for strokes
            log.debug("dashboard: " + fmt, *args)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            elif self.path == "/events":
                self._events()
            else:
                self.send_error(404)

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = broadcaster.subscribe()
            try:
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(f"data: {data}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                broadcaster.unsubscribe(q)

    return Handler


def start(broadcaster: Broadcaster, host: str = "127.0.0.1", port: int = 8000) -> str:
    """Starts the web server in a background thread; returns its URL."""
    handler = _make_handler(broadcaster)
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer((host, p), handler)
            break
        except OSError:
            continue
    else:
        raise OSError(f"geen vrije poort tussen {port} en {port + 19}")
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    return f"http://{shown}:{server.server_address[1]}/"


def hello_message(sensors: Dict[str, dict], title: str) -> dict:
    return {"type": "hello", "title": title, "sensors": [{"sensor": k, **v} for k, v in sensors.items()]}
