import os
import json
import subprocess
from datetime import datetime

EVENT_LOG_PATH = "/home/f25-echo/echo_fsw/data/logs/watchdog.log"

# Caches
_CACHED_DEFAULT_TARGET = None

def _get_default_target():
    """Return the system's default boot target."""
    global _CACHED_DEFAULT_TARGET

    if _CACHED_DEFAULT_TARGET is not None:
        return _CACHED_DEFAULT_TARGET

    try:
        result = subprocess.run(
            ["systemctl", "get-default"],
            capture_output=True,
            text=True,
            timeout=1
        )
        if result.returncode == 0:
            _CACHED_DEFAULT_TARGET = result.stdout.strip()
        else:
            _CACHED_DEFAULT_TARGET = "unknown"
    except Exception:
        _CACHED_DEFAULT_TARGET = "error"

    return _CACHED_DEFAULT_TARGET


def _get_active_target():
    """
    Detect which systemd target is currently active (e.g., ground.target or flight.target).
    """
    try:
        # This lists all active targets — the one that isn’t basic.target / paths.target
        result = subprocess.run(
            ["systemctl", "list-units", "--type=target", "--state=active", "--no-legend"],
            capture_output=True,
            text=True,
            timeout=1
        )
        if result.returncode != 0:
            return "unknown"

        lines = [
            ln.split()[0]
            for ln in result.stdout.splitlines()
            if ln.strip()
        ]

        # Ignore standard always-on targets
        ignore = {
            "basic.target",
            "sockets.target",
            "timers.target",
            "paths.target",
            "sysinit.target",
            "multi-user.target",  # unless you want to see this as 'ground'
        }

        for t in lines:
            if t not in ignore:
                return t

        return "multi-user.target"  # fallback if nothing special
    except Exception:
        return "error"


def log_event(event: str, data=None):
    try:
        os.makedirs(os.path.dirname(EVENT_LOG_PATH), exist_ok=True)
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        entry = {
            "ts": ts,
            "event": event,
            "default_target": _get_default_target(),
            "active_target": _get_active_target(),
        }

        if data is not None:
            entry["data"] = data

        with open(EVENT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")

    except Exception as e:
        print("[EVENT-LOGGER] Write failed:", e)