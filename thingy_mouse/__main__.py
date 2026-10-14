import argparse
import asyncio
import logging

from .app import ThingyMouseApp
from .motion import MotionSettings
from .mouse import create_output


def parse_args(argv=None):
    d = MotionSettings()
    p = argparse.ArgumentParser(
        prog="thingy-mouse",
        description="Use a Nordic Thingy:52 as an air mouse via a Raspberry Pi.",
    )
    p.add_argument("--address", help="Bluetooth address of the Thingy (default: first one found)")
    p.add_argument("--output", choices=["hid", "desktop", "uinput", "print"], default="hid",
                   help="hid = Pi acts as USB mouse for the laptop (default), desktop = move the cursor of "
                        "this computer directly (no Pi), uinput = Linux virtual mouse, print = debug")
    p.add_argument("--hid-device", default="/dev/hidg0")
    p.add_argument("--rate", type=int, default=100, help="motion sample rate in Hz (5-200, default 100)")
    p.add_argument("--sensitivity", type=float, default=d.sensitivity, help="counts per degree")
    p.add_argument("--acceleration", type=float, default=d.acceleration)
    p.add_argument("--deadzone", type=float, default=d.deadzone_dps, help="deg/s ignored as hand jitter")
    p.add_argument("--smoothing", type=float, default=d.smoothing, help="0..0.95")
    p.add_argument("--x-axis", choices="xyz", default=d.x_axis, help="gyro axis that moves the cursor horizontally")
    p.add_argument("--y-axis", choices="xyz", default=d.y_axis, help="gyro axis that moves the cursor vertically")
    p.add_argument("--invert-x", action=argparse.BooleanOptionalAction, default=d.invert_x)
    p.add_argument("--invert-y", action=argparse.BooleanOptionalAction, default=d.invert_y)
    p.add_argument("--click-freeze-ms", type=int, default=150)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    if not 5 <= args.rate <= 200:
        p.error("--rate must be between 5 and 200")
    if not 0 <= args.smoothing < 1:
        p.error("--smoothing must be in [0, 1)")
    return args


async def run(args) -> None:
    from .thingy import Thingy  # imported here so --help works without bleak installed

    settings = MotionSettings(
        sample_rate_hz=args.rate,
        sensitivity=args.sensitivity,
        acceleration=args.acceleration,
        deadzone_dps=args.deadzone,
        smoothing=args.smoothing,
        x_axis=args.x_axis,
        y_axis=args.y_axis,
        invert_x=args.invert_x,
        invert_y=args.invert_y,
        calibration_samples=args.rate,  # ~1 second
    )
    mouse = create_output(args.output, args.hid_device)
    app = ThingyMouseApp(mouse, settings, args.click_freeze_ms)
    thingy = Thingy(
        address=args.address,
        motion_hz=args.rate,
        on_gyro=app.on_gyro,
        on_button=app.on_button,
        on_tap=app.on_tap,
        on_connection=app.on_connection,
    )
    app.thingy = thingy
    try:
        await thingy.run_forever()
    finally:
        mouse.close()


def main(argv=None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
