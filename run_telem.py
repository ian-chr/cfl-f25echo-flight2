#!/usr/bin/env python3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from telem_deps import app, config as cfg

# Force CSV path under repo-root/data/telemetry
cfg.OUTPUT_CSV = str(REPO_ROOT / "data" / "telemetry" / "telemetry.csv")

if __name__ == "__main__":
    app.main()