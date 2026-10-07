"""Glue between the Thingy (input) and the mouse output."""

import asyncio
import logging
import time

from .motion import AirMouse, MotionSettings
from .mouse import BUTTON_LEFT, BUTTON_RIGHT, MouseOutput

log = logging.getLogger(__name__)


class ThingyMouseApp:
    """Controls:
    - button: left mouse button (hold to drag)
    - double tap on the Thingy: right click
    - triple tap: pause / resume the cursor (resuming re-calibrates, keep it still)
    """

    def __init__(self, mouse: MouseOutput, settings: MotionSettings, click_freeze_ms: int = 150):
        self.mouse = mouse
        self.air = AirMouse(settings)
        self.click_freeze_s = click_freeze_ms / 1000.0
        self.paused = False
        self.thingy = None  # set by main(), used for LED feedback
        self._frozen_until = 0.0
        self._was_calibrated = False

    # --- callbacks from the Thingy ---

    def on_connection(self, connected: bool) -> None:
        if connected:
            log.info("Keep the Thingy still for a moment while the gyro calibrates ...")
            self.air.recalibrate()
            self._was_calibrated = False
            self._led_calibrating()
        else:
            self.mouse.release_all()

    def on_gyro(self, gyro) -> None:
        dx, dy = self.air.update(gyro)
        if self.air.calibrated and not self._was_calibrated:
            self._was_calibrated = True
            log.info("Calibrated (bias %.2f, %.2f, %.2f deg/s). Ready!", *self.air.bias)
            self._led_status()
        if self.paused or time.monotonic() < self._frozen_until:
            return
        self.mouse.move(dx, dy)

    def on_button(self, pressed: bool) -> None:
        # Pressing the button nudges the device; freeze the cursor briefly so clicks land where aimed.
        self._freeze()
        if pressed:
            self.mouse.press(BUTTON_LEFT)
        else:
            self.mouse.release(BUTTON_LEFT)

    def on_tap(self, direction: int, count: int) -> None:
        log.debug("tap direction=%d count=%d", direction, count)
        if count == 2 and not self.paused:
            self._freeze()
            self.mouse.click(BUTTON_RIGHT)
        elif count >= 3:
            self.toggle_pause()

    # --- helpers ---

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        log.info("Paused" if self.paused else "Resumed")
        if self.paused:
            self.mouse.release_all()
            self._led_status()
        else:
            self.air.recalibrate()
            self._was_calibrated = False
            self._led_calibrating()

    def _freeze(self) -> None:
        self._frozen_until = time.monotonic() + self.click_freeze_s
        self.air.reset_motion()

    def _led(self, coro_factory) -> None:
        if self.thingy is not None:
            asyncio.get_running_loop().create_task(coro_factory(self.thingy))

    def _led_calibrating(self) -> None:
        from .thingy import LED_YELLOW
        self._led(lambda t: t.set_led_breathe(LED_YELLOW, 30, 500))

    def _led_status(self) -> None:
        from .thingy import LED_BLUE
        if self.paused:
            self._led(lambda t: t.set_led_breathe(LED_BLUE, 20, 2000))
        else:
            self._led(lambda t: t.set_led_constant(0, 40, 0))
