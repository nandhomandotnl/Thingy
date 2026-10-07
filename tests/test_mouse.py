import struct

from thingy_mouse.mouse import BUTTON_LEFT, MouseOutput, build_reports


def test_single_report():
    assert build_reports(BUTTON_LEFT, 5, -3, 1) == [struct.pack("<Bbbb", 1, 5, -3, 1)]


def test_large_moves_are_split():
    reports = build_reports(0, 300, -130)
    xs = [struct.unpack("<Bbbb", r)[1] for r in reports]
    ys = [struct.unpack("<Bbbb", r)[2] for r in reports]
    assert sum(xs) == 300 and sum(ys) == -130
    assert all(-127 <= v <= 127 for v in xs + ys)


class Recorder(MouseOutput):
    def __init__(self):
        super().__init__()
        self.sent = []

    def _send(self, dx, dy, wheel):
        self.sent.append((self.buttons, dx, dy))


def test_press_release_and_drag():
    m = Recorder()
    m.press(BUTTON_LEFT)
    m.move(4, 0)
    m.release(BUTTON_LEFT)
    m.move(0, 0)  # no-op
    assert m.sent == [(1, 0, 0), (1, 4, 0), (0, 0, 0)]
