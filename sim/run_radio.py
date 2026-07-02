#!/usr/bin/env python3
"""Launch radio/unified_radio.py (the real FSW radio service) against the
simulated RFM9x + virtual RF medium instead of real SPI hardware."""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "sim", "fake_modules"))
sys.path.insert(0, REPO_ROOT)

from sim.patch_paths import patch_all_fsw_paths

FSW_HOME = os.environ.get("SIM_FSW_HOME", os.path.join(REPO_ROOT, "sim", "state", "fsw_home"))
patch_all_fsw_paths(FSW_HOME)

from radio import unified_radio

if __name__ == "__main__":
    unified_radio.main()
