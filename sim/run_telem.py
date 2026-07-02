#!/usr/bin/env python3
"""Launch telem_deps/app.py (the real FSW telemetry loop) against the
simulator instead of real hardware. Mirrors the repo's own run_telem.py,
which already monkeypatches config.OUTPUT_CSV for local dev -- this just
extends that pattern to every other hardcoded path plus the HAL swap."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "sim", "fake_modules"))
sys.path.insert(0, REPO_ROOT)

from sim.patch_paths import patch_all_fsw_paths

FSW_HOME = os.environ.get("SIM_FSW_HOME", os.path.join(REPO_ROOT, "sim", "state", "fsw_home"))
patch_all_fsw_paths(FSW_HOME)

from telem_deps import app

if __name__ == "__main__":
    app.main()
