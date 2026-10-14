"""Session files: raw sensor data, detected strokes and button markers as CSV."""

import csv
import datetime as dt
from pathlib import Path
from typing import Dict, List

from .detect import Sample, Stroke

STROKE_FIELDS = [
    "tijd", "sensor", "speler", "slag", "zekerheid", "piek_graden_per_s", "snelheid_kmh",
    "raak", "schok_g", "duur_ms", "boven_meetbereik", "t_sensor_s",
]


def stroke_row(stroke: Stroke, player: str) -> Dict[str, object]:
    return {
        "tijd": dt.datetime.now().isoformat(timespec="milliseconds"),
        "sensor": stroke.sensor,
        "speler": player,
        "slag": stroke.kind,
        "zekerheid": round(stroke.confidence, 2),
        "piek_graden_per_s": round(stroke.peak_dps),
        "snelheid_kmh": round(stroke.speed_kmh, 1),
        "raak": int(stroke.hit),
        "schok_g": round(stroke.jolt_g, 1),
        "duur_ms": round(stroke.duration_s * 1000),
        "boven_meetbereik": int(stroke.saturated),
        "t_sensor_s": round(stroke.t_peak, 3),
    }


class Session:
    def __init__(self, root: Path, save_raw: bool = True):
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.dir = Path(root) / stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        self.save_raw = save_raw
        self._raw: Dict[str, tuple] = {}
        self._strokes_file = open(self.dir / "slagen.csv", "w", newline="", buffering=1)
        self._strokes = csv.DictWriter(self._strokes_file, fieldnames=STROKE_FIELDS)
        self._strokes.writeheader()
        self._markers_file = open(self.dir / "markeringen.csv", "w", newline="", buffering=1)
        self._markers = csv.writer(self._markers_file)
        self._markers.writerow(["tijd", "sensor", "t_sensor_s"])

    def add_samples(self, sensor: str, samples: List[Sample]) -> None:
        if not self.save_raw:
            return
        if sensor not in self._raw:
            f = open(self.dir / f"ruw_{sensor}.csv", "w", newline="")
            w = csv.writer(f)
            w.writerow(["t_sensor_s", "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps"])
            self._raw[sensor] = (f, w)
        _, w = self._raw[sensor]
        for s in samples:
            w.writerow([f"{s.t:.4f}", *(f"{v:.3f}" for v in s.accel), *(f"{v:.1f}" for v in s.gyro)])

    def add_stroke(self, stroke: Stroke, player: str) -> Dict[str, object]:
        row = stroke_row(stroke, player)
        self._strokes.writerow(row)
        return row

    def add_marker(self, sensor: str, t_sensor: float) -> None:
        self._markers.writerow([dt.datetime.now().isoformat(timespec="milliseconds"), sensor, f"{t_sensor:.3f}"])

    def close(self) -> None:
        for f, _ in self._raw.values():
            f.close()
        self._strokes_file.close()
        self._markers_file.close()
