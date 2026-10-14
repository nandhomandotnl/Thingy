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


def test_desktop_mouse_with_fake_pynput(monkeypatch):
    import sys
    import types

    calls = []

    class Controller:
        def press(self, b):
            calls.append(("press", b))

        def release(self, b):
            calls.append(("release", b))

        def move(self, dx, dy):
            calls.append(("move", dx, dy))

        def scroll(self, x, y):
            calls.append(("scroll", y))

    mod = types.ModuleType("pynput.mouse")
    mod.Controller = Controller
    mod.Button = types.SimpleNamespace(left="L", right="R", middle="M")
    monkeypatch.setitem(sys.modules, "pynput", types.ModuleType("pynput"))
    monkeypatch.setitem(sys.modules, "pynput.mouse", mod)

    from thingy_mouse.mouse import BUTTON_RIGHT, create_output

    m = create_output("desktop")
    m.press(BUTTON_LEFT)
    m.move(3, -2)
    m.release(BUTTON_LEFT)
    m.click(BUTTON_RIGHT)
    assert calls == [("press", "L"), ("move", 3, -2), ("release", "L"), ("press", "R"), ("release", "R")]
