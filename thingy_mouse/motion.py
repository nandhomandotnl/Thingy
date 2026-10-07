"""Turn Thingy:52 gyroscope samples into mouse movement ("air mouse").

The Thingy streams angular rates in degrees/second. Rotating the device left/right
(yaw) moves the cursor horizontally, tilting it up/down (pitch) moves it
vertically. Gyros drift a little, so we measure the bias while the device lies
still and keep refining it whenever it is not being moved.
"""

from dataclasses import dataclass
import math

AXES = {"x": 0, "y": 1, "z": 2}


@dataclass
class MotionSettings:
    sample_rate_hz: float = 100.0
    # Mouse counts per degree of rotation at slow speed.
    sensitivity: float = 12.0
    # Extra gain per 100 deg/s, so fast flicks travel further (pointer acceleration).
    acceleration: float = 0.6
    # Rates below this (deg/s) are ignored to kill jitter from a shaky hand.
    deadzone_dps: float = 2.0
    # Exponential smoothing factor, 0 = no smoothing, closer to 1 = smoother/laggier.
    smoothing: float = 0.3
    # Which gyro axis drives which cursor axis, and in which direction.
    x_axis: str = "z"
    y_axis: str = "x"
    invert_x: bool = True
    invert_y: bool = True
    # Bias calibration.
    calibration_samples: int = 100
    still_threshold_dps: float = 4.0
    still_samples: int = 50
    bias_adapt_rate: float = 0.02


class AirMouse:
    """Stateful converter from gyro samples (deg/s) to integer mouse deltas."""

    def __init__(self, settings: MotionSettings):
        if settings.x_axis not in AXES or settings.y_axis not in AXES:
            raise ValueError("axes must be one of x, y, z")
        self.s = settings
        self.bias = [0.0, 0.0, 0.0]
        self._cal_sum = [0.0, 0.0, 0.0]
        self._cal_count = 0
        self._still_count = 0
        self._smooth = [0.0, 0.0]
        self._remainder = [0.0, 0.0]

    @property
    def calibrated(self) -> bool:
        return self._cal_count >= self.s.calibration_samples

    def recalibrate(self) -> None:
        self._cal_sum = [0.0, 0.0, 0.0]
        self._cal_count = 0
        self._still_count = 0
        self.reset_motion()

    def reset_motion(self) -> None:
        self._smooth = [0.0, 0.0]
        self._remainder = [0.0, 0.0]

    def update(self, gyro: "tuple[float, float, float]") -> "tuple[int, int]":
        """Feed one gyro sample; returns the (dx, dy) to send to the host."""
        if not self.calibrated:
            self._calibrate(gyro)
            return 0, 0

        rates = [g - b for g, b in zip(gyro, self.bias)]
        self._track_bias(gyro, rates)

        dt = 1.0 / self.s.sample_rate_hz
        out = []
        for i, (axis, invert) in enumerate(
            ((self.s.x_axis, self.s.invert_x), (self.s.y_axis, self.s.invert_y))
        ):
            rate = rates[AXES[axis]]
            if invert:
                rate = -rate
            a = self.s.smoothing
            self._smooth[i] = a * self._smooth[i] + (1 - a) * rate
            counts = self._rate_to_counts(self._smooth[i], dt) + self._remainder[i]
            whole = math.trunc(counts)
            self._remainder[i] = counts - whole
            out.append(whole)
        return out[0], out[1]

    def _rate_to_counts(self, rate: float, dt: float) -> float:
        magnitude = abs(rate) - self.s.deadzone_dps
        if magnitude <= 0:
            return 0.0
        gain = self.s.sensitivity * (1.0 + self.s.acceleration * magnitude / 100.0)
        return math.copysign(magnitude * dt * gain, rate)

    def _calibrate(self, gyro) -> None:
        for i in range(3):
            self._cal_sum[i] += gyro[i]
        self._cal_count += 1
        if self.calibrated:
            self.bias = [v / self._cal_count for v in self._cal_sum]

    def _track_bias(self, gyro, rates) -> None:
        if all(abs(r) < self.s.still_threshold_dps for r in rates):
            self._still_count += 1
        else:
            self._still_count = 0
        if self._still_count >= self.s.still_samples:
            k = self.s.bias_adapt_rate
            self.bias = [(1 - k) * b + k * g for b, g in zip(self.bias, gyro)]
