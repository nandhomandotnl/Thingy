"""Stroke detection from a stream of racket IMU samples.

A stroke is a burst of fast racket rotation: the gyroscope magnitude rises above
``start_dps``, peaks above ``min_peak_dps`` and drops below ``end_dps`` again. A ball
hit shows up as a sharp jolt in the accelerometer during the swing.
"""

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional, Tuple

Vec3 = Tuple[float, float, float]


@dataclass
class Sample:
    t: float  # seconds (sensor clock)
    accel: Vec3  # g
    gyro: Vec3  # deg/s


@dataclass
class DetectorSettings:
    start_dps: float = 250.0
    end_dps: float = 150.0
    min_peak_dps: float = 500.0
    quiet_s: float = 0.08
    max_duration_s: float = 1.5
    pre_s: float = 0.4
    refractory_s: float = 0.3
    # Distance from the rotation centre to the sweet spot, used to turn rotation speed
    # into an (approximate) racket head speed.
    radius_m: float = 1.0
    # Accelerometer jolt (change in g between two samples) that counts as a ball hit.
    hit_jolt_g: float = 3.0
    gyro_limit_dps: float = 2000.0


@dataclass
class Stroke:
    sensor: str
    t_start: float
    t_peak: float
    duration_s: float
    peak_dps: float
    speed_kmh: float
    jolt_g: float
    hit: bool
    saturated: bool
    axis: Vec3  # unit rotation axis at the peak (sensor frame)
    gravity_before: Vec3  # unit "up" direction just before the swing (sensor frame)
    kind: str = "slag"
    confidence: float = 0.0
    samples: List[Sample] = field(default_factory=list, repr=False)

    def features(self) -> List[float]:
        return feature_vector(self.axis, self.gravity_before)


def feature_vector(axis: Vec3, gravity_before: Vec3) -> List[float]:
    """Direction of rotation plus how the racket was held before the swing."""
    return list(axis) + [0.5 * g for g in gravity_before]


def _norm(v) -> float:
    return math.sqrt(sum(x * x for x in v))


def _unit(v) -> Vec3:
    n = _norm(v)
    return tuple(x / n for x in v) if n > 1e-9 else (0.0, 0.0, 0.0)


class StrokeDetector:
    def __init__(self, sensor: str, settings: Optional[DetectorSettings] = None,
                 on_stroke: Callable[[Stroke], None] = lambda s: None):
        self.sensor = sensor
        self.s = settings or DetectorSettings()
        self.on_stroke = on_stroke
        self._pre: Deque[Sample] = deque()
        self._window: List[Sample] = []
        self._active = False
        self._quiet_since: Optional[float] = None
        self._blocked_until = -math.inf

    def add(self, sample: Sample) -> Optional[Stroke]:
        mag = _norm(sample.gyro)
        if not self._active:
            self._pre.append(sample)
            while self._pre and sample.t - self._pre[0].t > self.s.pre_s:
                self._pre.popleft()
            if mag >= self.s.start_dps and sample.t >= self._blocked_until:
                self._active = True
                self._window = list(self._pre)
                self._quiet_since = None
            return None

        self._window.append(sample)
        if mag < self.s.end_dps:
            if self._quiet_since is None:
                self._quiet_since = sample.t
        else:
            self._quiet_since = None

        start_t = self._window[0].t
        quiet_long_enough = self._quiet_since is not None and sample.t - self._quiet_since >= self.s.quiet_s
        if quiet_long_enough or sample.t - start_t > self.s.max_duration_s:
            return self._finish(sample.t)
        return None

    def _finish(self, now: float) -> Optional[Stroke]:
        window, self._window = self._window, []
        self._active = False
        self._pre.clear()
        self._blocked_until = now + self.s.refractory_s

        mags = [_norm(x.gyro) for x in window]
        i_peak = max(range(len(window)), key=mags.__getitem__)
        peak = mags[i_peak]
        if peak < self.s.min_peak_dps:
            return None

        swing = [x for x, m in zip(window, mags) if m >= self.s.start_dps]
        before = [x for x, m in zip(window, mags) if x.t < swing[0].t] or window[:1]
        gravity = _unit([sum(x.accel[k] for x in before) / len(before) for k in range(3)])
        jolt = max((_norm([a - b for a, b in zip(window[i].accel, window[i - 1].accel)])
                    for i in range(1, len(window))), default=0.0)

        stroke = Stroke(
            sensor=self.sensor,
            t_start=swing[0].t,
            t_peak=window[i_peak].t,
            duration_s=swing[-1].t - swing[0].t,
            peak_dps=peak,
            speed_kmh=math.radians(peak) * self.s.radius_m * 3.6,
            jolt_g=jolt,
            hit=jolt >= self.s.hit_jolt_g,
            saturated=any(abs(g) >= self.s.gyro_limit_dps * 0.995 for x in window for g in x.gyro),
            axis=_unit(window[i_peak].gyro),
            gravity_before=gravity,
            samples=window,
        )
        self.on_stroke(stroke)
        return stroke
