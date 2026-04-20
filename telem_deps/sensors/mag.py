# sensors/mag.py
"""RM3100 magnetometer reader."""

import time
import board
import busio
import rm3100

RM3100_ADDR = 0x20
_i2c = None
_mag = None

def _get_i2c():
    global _i2c
    if _i2c is None:
        _i2c = busio.I2C(board.SCL, board.SDA)
    return _i2c

def read_magnetometer():
    global _mag
    try:
        i2c = _get_i2c()
        if _mag is None:
            _mag = rm3100.RM3100_I2C(i2c, i2c_address=RM3100_ADDR)
        _mag.start_single_reading()
        time.sleep(_mag.measurement_time)
        mx, my, mz = _mag.get_next_reading()
        # convert if needed; the library also has property _mag.magnetic
        return mx, my, mz
    except Exception as e:
        print(f"[RM3100] Read failed: {e}")
        return (None, None, None)