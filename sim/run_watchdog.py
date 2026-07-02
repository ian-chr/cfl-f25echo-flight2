#!/usr/bin/env python3
"""Launch watchdog/watchdog.py (the real FSW watchdog) against the fake
`systemctl` on PATH (see sim/fakebin/) instead of a real systemd."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# telem_deps/__init__.py eagerly does `from . import app, config`, so even
# though watchdog.py only wants telem_deps.util.sd_notify, importing it
# drags in app.py's board/busio/smbus imports too -- fake_modules has to
# be on sys.path here for exactly the same reason run_telem.py needs it.
sys.path.insert(0, os.path.join(REPO_ROOT, "sim", "fake_modules"))
sys.path.insert(0, REPO_ROOT)

from sim.patch_paths import patch_all_fsw_paths

FSW_HOME = os.environ.get("SIM_FSW_HOME", os.path.join(REPO_ROOT, "sim", "state", "fsw_home"))
patch_all_fsw_paths(FSW_HOME)

from watchdog import watchdog

if __name__ == "__main__":
    watchdog.main()
