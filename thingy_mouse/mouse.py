"""Mouse outputs: USB HID gadget (Pi -> laptop), uinput (local test) and print (debug)."""

import errno
import logging
import os
import struct

log = logging.getLogger(__name__)

BUTTON_LEFT = 0x01
BUTTON_RIGHT = 0x02
BUTTON_MIDDLE = 0x04

# Must match the report descriptor written by scripts/usb_gadget.sh:
# 1 byte buttons, then signed 8-bit X, Y and wheel.
REPORT_FORMAT = "<Bbbb"


def _clamp8(v: int) -> int:
    return max(-127, min(127, v))


def build_reports(buttons: int, dx: int, dy: int, wheel: int = 0) -> "list[bytes]":
    """Split a movement into as many 4-byte HID reports as needed (each axis is int8)."""
    reports = []
    while True:
        sx, sy, sw = _clamp8(dx), _clamp8(dy), _clamp8(wheel)
        reports.append(struct.pack(REPORT_FORMAT, buttons & 0x07, sx, sy, sw))
        dx, dy, wheel = dx - sx, dy - sy, wheel - sw
        if not (dx or dy or wheel):
            return reports


class MouseOutput:
    def __init__(self):
        self.buttons = 0

    def move(self, dx: int, dy: int, wheel: int = 0) -> None:
        if dx or dy or wheel:
            self._send(dx, dy, wheel)

    def press(self, button: int) -> None:
        self.buttons |= button
        self._send(0, 0, 0)

    def release(self, button: int) -> None:
        self.buttons &= ~button
        self._send(0, 0, 0)

    def click(self, button: int) -> None:
        self.press(button)
        self.release(button)

    def release_all(self) -> None:
        if self.buttons:
            self.buttons = 0
            self._send(0, 0, 0)

    def _send(self, dx: int, dy: int, wheel: int) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class HidGadgetMouse(MouseOutput):
    """Writes reports to /dev/hidgN, created by scripts/usb_gadget.sh."""

    def __init__(self, path: str = "/dev/hidg0"):
        super().__init__()
        self.path = path
        self.fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        self._warned = False

    def _send(self, dx, dy, wheel):
        for report in build_reports(self.buttons, dx, dy, wheel):
            try:
                os.write(self.fd, report)
                self._warned = False
            except OSError as e:
                # EAGAIN/ESHUTDOWN: the laptop is not connected or not polling; drop it.
                if e.errno not in (errno.EAGAIN, errno.ESHUTDOWN, errno.EPIPE):
                    raise
                if not self._warned:
                    log.warning("USB host is not reading reports (%s); is the cable plugged in?", e)
                    self._warned = True
                return

    def close(self):
        self.release_all()
        os.close(self.fd)


class UinputMouse(MouseOutput):
    """Virtual mouse on the Pi itself; handy for testing with a screen on the Pi."""

    def __init__(self):
        super().__init__()
        from evdev import UInput, ecodes as e

        self._e = e
        self.ui = UInput(
            {
                e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
            },
            name="Thingy:52 mouse",
        )
        self._sent_buttons = 0

    def _send(self, dx, dy, wheel):
        e = self._e
        for bit, code in ((BUTTON_LEFT, e.BTN_LEFT), (BUTTON_RIGHT, e.BTN_RIGHT), (BUTTON_MIDDLE, e.BTN_MIDDLE)):
            if (self.buttons ^ self._sent_buttons) & bit:
                self.ui.write(e.EV_KEY, code, 1 if self.buttons & bit else 0)
        self._sent_buttons = self.buttons
        if dx:
            self.ui.write(e.EV_REL, e.REL_X, dx)
        if dy:
            self.ui.write(e.EV_REL, e.REL_Y, dy)
        if wheel:
            self.ui.write(e.EV_REL, e.REL_WHEEL, wheel)
        self.ui.syn()

    def close(self):
        self.release_all()
        self.ui.close()


class PrintMouse(MouseOutput):
    """Logs what would be sent; for trying things out without USB."""

    def _send(self, dx, dy, wheel):
        print(f"buttons={self.buttons:03b} dx={dx:+4d} dy={dy:+4d} wheel={wheel:+d}", flush=True)


def create_output(kind: str, hid_path: str = "/dev/hidg0") -> MouseOutput:
    if kind == "hid":
        return HidGadgetMouse(hid_path)
    if kind == "uinput":
        return UinputMouse()
    if kind == "print":
        return PrintMouse()
    raise ValueError(f"unknown output {kind!r}")
