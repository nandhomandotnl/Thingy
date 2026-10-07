from thingy_mouse.motion import AirMouse, MotionSettings


def make(**kw):
    s = MotionSettings(calibration_samples=10, smoothing=0.0, acceleration=0.0, **kw)
    return AirMouse(s)


def test_calibration_removes_bias():
    air = make()
    for _ in range(10):
        assert air.update((1.0, -2.0, 0.5)) == (0, 0)
    assert air.calibrated
    assert air.bias == [1.0, -2.0, 0.5]
    # Same biased reading while still => no movement.
    assert air.update((1.0, -2.0, 0.5)) == (0, 0)


def test_yaw_moves_horizontally_and_pitch_vertically():
    air = make(sensitivity=10.0, deadzone_dps=0.0)
    for _ in range(10):
        air.update((0, 0, 0))
    total_x = total_y = 0
    for _ in range(100):  # 1 s of turning left at 30 deg/s
        dx, dy = air.update((0, 0, 30.0))
        total_x += dx
        total_y += dy
    assert total_y == 0
    assert -301 <= total_x <= -299  # 30 deg * 10 counts/deg, inverted: turning left goes left


def test_deadzone_suppresses_jitter():
    air = make(deadzone_dps=3.0)
    for _ in range(10):
        air.update((0, 0, 0))
    for i in range(200):
        assert air.update((2.0 * (-1) ** i, 0, 2.5)) == (0, 0)


def test_fractional_movement_accumulates():
    air = make(sensitivity=1.0, deadzone_dps=0.0)
    for _ in range(10):
        air.update((0, 0, 0))
    total = sum(air.update((-10.0, 0, 0))[1] for _ in range(100))  # 10 deg -> 10 counts
    assert total in (9, 10)


def test_bias_tracks_slow_drift_when_still():
    air = make()
    for _ in range(10):
        air.update((0, 0, 0))
    for _ in range(2000):
        air.update((0, 0, 1.5))
    assert abs(air.bias[2] - 1.5) < 0.05
