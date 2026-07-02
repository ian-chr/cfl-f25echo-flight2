"""Fallback shims for the three Linux-only `/proc` and `/sys` paths that
telem_deps/sensors/cpu.py and memory.py read directly:

    /sys/class/thermal/thermal_zone0/temp
    /proc/stat
    /proc/meminfo

These are opened with a *literal path string inline in the call*, not a
module-level constant, so there's no attribute to monkeypatch the way
sim/patch_paths.py does for everything else. Rather than edit that FSW
source, this narrowly wraps `builtins.open` -- and only ever intercepts
these three exact paths, and only when the real file doesn't exist (i.e.
only on a non-Linux dev host). On the actual Pi (or any Linux box) these
paths are real, so this shim never engages and is a pure no-op.

Without this, telem_deps/app.py itself is unaffected (cpu.py/memory.py
already catch their own errors and return None), but
lora_gnu_radio/CFLTXRX/telemetry_beacon.py's build_beacon() treats a
blank Mem/Disk field as fatal for the *entire* beacon -- see the
"Data conversion error in beacon builder" failure mode this fixes.
"""
from __future__ import annotations

import builtins
import io
import os
import time

_REAL_OPEN = builtins.open
_INSTALLED = False

THERMAL_PATH = "/sys/class/thermal/thermal_zone0/temp"
STAT_PATH = "/proc/stat"
MEMINFO_PATH = "/proc/meminfo"


def _fake_thermal() -> str:
    jitter = 1500 * ((int(time.time()) % 7) - 3) / 3.0
    return str(int(45000 + jitter))


def _fake_stat() -> str:
    # cpu.py reads jiffies-style cumulative counters twice, 0.5s apart,
    # and diffs them -- so these must be monotonically increasing in
    # real time, not fixed, or every load reading would come out as 0%.
    total = int(time.time() * 100)
    idle = int(total * 0.72)
    user = total - idle
    return f"cpu  {user} 0 0 {idle} 0 0 0 0 0 0\n"


def _fake_meminfo() -> str:
    return "MemTotal:        8000000 kB\nMemAvailable:    5200000 kB\n"


_FAKE_FILES = {
    THERMAL_PATH: _fake_thermal,
    STAT_PATH: _fake_stat,
    MEMINFO_PATH: _fake_meminfo,
}


def _patched_open(file, mode="r", *args, **kwargs):
    if isinstance(file, str) and file in _FAKE_FILES and not os.path.exists(file):
        return io.StringIO(_FAKE_FILES[file]())
    return _REAL_OPEN(file, mode, *args, **kwargs)


def install_host_stat_shims() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    builtins.open = _patched_open
    _INSTALLED = True
