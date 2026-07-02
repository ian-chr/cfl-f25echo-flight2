"""Virtual RV-8803 RTC.

telem_deps/sensors/rtc.py reads 7 BCD registers (sec, min, hour, date,
month, year, weekday) directly over I2C. This device reports real UTC
wall-clock time (GPS/RTC drift-sync logic in app.py is about comparing
clocks, not about compressing mission time, so the RTC always ticks in
real seconds) offset by a configurable drift.

The drift is stored in a small state file rather than an in-process
attribute because the RTC is "written" by `hwclock --set --date ...`,
which the FSW invokes as a *separate* subprocess (sim/fakebin/hwclock) --
it has no way to reach back into the telemetry process's memory, but it
can update this file, which we re-read on every access.
"""
from __future__ import annotations

import json
import os
import time

from sim.config import SIM_STATE_DIR

RTC_STATE_PATH = os.path.join(SIM_STATE_DIR, "rtc_offset.json")


def read_offset_sec() -> float:
    try:
        with open(RTC_STATE_PATH) as f:
            return float(json.load(f).get("offset_sec", 0.0))
    except Exception:
        return 0.0


def write_offset_sec(offset_sec: float) -> None:
    os.makedirs(SIM_STATE_DIR, exist_ok=True)
    tmp = RTC_STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"offset_sec": offset_sec}, f)
    os.replace(tmp, RTC_STATE_PATH)


def _bcd(v: int) -> int:
    return ((v // 10) << 4) | (v % 10)


class RV8803VirtualDevice:
    def __init__(self, world, drift_sec: float = 0.0):
        self.world = world
        self._initial_drift = drift_sec

    def read_i2c_block_data(self, reg: int, length: int):
        offset = self._initial_drift + read_offset_sec()
        now = time.gmtime(time.time() + offset)
        regs = [
            _bcd(now.tm_sec) & 0x7F,
            _bcd(now.tm_min) & 0x7F,
            _bcd(now.tm_hour) & 0x3F,
            _bcd(now.tm_mday) & 0x3F,
            _bcd(now.tm_mon) & 0x1F,
            _bcd(now.tm_year % 100),
            0,
        ]
        return regs[:max(0, length)]
