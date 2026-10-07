import struct
import sys
import types

# thingy.py imports bleak at module level; stub it so tests run without Bluetooth.
for name in ("bleak", "bleak.exc"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["bleak"].BleakClient = sys.modules["bleak"].BleakScanner = object
sys.modules["bleak.exc"].BleakError = Exception

from thingy_mouse.thingy import parse_raw_motion, thingy_uuid  # noqa: E402


def test_uuid():
    assert thingy_uuid(0x0406) == "ef680406-9b35-4933-9b10-52ffa9740042"


def test_parse_raw_motion():
    data = struct.pack("<9h", 1024, 0, -512, 32, -64, 320, 16, 0, 0)
    accel, gyro, compass = parse_raw_motion(data)
    assert accel == (1.0, 0.0, -0.5)
    assert gyro == (1.0, -2.0, 10.0)
    assert compass == (1.0, 0.0, 0.0)
