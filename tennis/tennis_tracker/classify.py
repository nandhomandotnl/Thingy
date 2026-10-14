"""Stroke type classification.

Every racket has the sensor mounted a little differently, so instead of hard-coded rules
the tracker learns from a short calibration: you hit a few strokes of each type and it
stores the average "signature" (rotation direction + racket orientation before the swing).
New strokes get the label of the closest signature.
"""

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .detect import Stroke

DEFAULT_KINDS = ["forehand", "backhand", "opslag"]
MIN_SIMILARITY = 0.6


def _unit(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-9 else v


def _cos(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(_unit(a), _unit(b)))


class StrokeClassifier:
    def __init__(self, centroids: Optional[Dict[str, List[float]]] = None):
        self.centroids = centroids or {}

    @property
    def trained(self) -> bool:
        return bool(self.centroids)

    @classmethod
    def train(cls, labelled: Dict[str, List[Stroke]]) -> "StrokeClassifier":
        centroids = {}
        for kind, strokes in labelled.items():
            if not strokes:
                continue
            feats = [_unit(s.features()) for s in strokes]
            centroids[kind] = _unit([sum(f[i] for f in feats) / len(feats) for i in range(len(feats[0]))])
        return cls(centroids)

    def classify(self, stroke: Stroke) -> Tuple[str, float]:
        if not self.centroids:
            return "slag", 0.0
        f = stroke.features()
        kind, sim = max(((k, _cos(f, c)) for k, c in self.centroids.items()), key=lambda kv: kv[1])
        if sim < MIN_SIMILARITY:
            return "onbekend", sim
        return kind, sim

    def apply(self, stroke: Stroke) -> Stroke:
        stroke.kind, stroke.confidence = self.classify(stroke)
        return stroke

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "centroids": self.centroids}, indent=2))

    @classmethod
    def load(cls, path: Path) -> "StrokeClassifier":
        data = json.loads(path.read_text())
        return cls(data["centroids"])


def calibration_path(directory: Path, sensor: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sensor)
    return directory / f"{safe}.json"


def load_for_sensor(directory: Path, sensor: str) -> StrokeClassifier:
    path = calibration_path(directory, sensor)
    return StrokeClassifier.load(path) if path.exists() else StrokeClassifier()
