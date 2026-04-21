#!/usr/bin/env python3
"""
Echo FSW Watchdog Supervisor (Unified-Radio Edition)

- Runs under systemd with Type=notify + WatchdogSec
- Monitors critical services:
    - echo-telem.service
    - echo-radio.service
- If a service is not active, attempts restart.
- If repeated failures occur, escalates:
    - optional system reboot (ENABLE_REBOOT_ON_ESCALATION)

This replaces the old design which watched:
    - echo-beacon.service  (REMOVED)
    - echo-image-downlink.service (REMOVED)
"""

import subprocess
import sys
import time

# Make the repo root importable when running from the systemd unit.
sys.path.insert(0, "/home/f25-echo2/echo_fsw")

from telem_deps.util.sd_notify import notify as sd_notify

# ============================================================
# CRITICAL SERVICES IN NEW ARCHITECTURE
# ============================================================
CRITICAL_SERVICES = [
    "echo-telem.service",
    "echo-radio.service",     # unified radio replaces beacon + image_downlink
]

# ============================================================
# PARAMETERS
# ============================================================
CHECK_PERIOD = 5.0

MAX_RESTARTS_PER_SERVICE = 5
ESCALATION_WINDOW_SEC = 300  # 5 minutes

ENABLE_REBOOT_ON_ESCALATION = True


# ============================================================
# SYSTEMCTL HELPERS
# ============================================================
def systemctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def is_active(unit: str) -> bool:
    r = systemctl("is-active", unit)
    return r.stdout.strip() == "active"


def is_failed(unit: str) -> bool:
    r = systemctl("is-failed", unit)
    return r.stdout.strip() == "failed"


def restart_unit(unit: str) -> bool:
    print(f"[WD] Restarting {unit} ...")
    r = systemctl("restart", unit)
    if r.returncode != 0:
        print(f"[WD] ERROR: Failed to restart {unit}: {r.stderr.strip()}")
        return False
    return True


def reboot_system():
    print("[WD] ESCALATION: Rebooting system due to repeated failures.")
    time.sleep(2.0)
    subprocess.run(["systemctl", "reboot"])


# ============================================================
# MAIN LOOP
# ============================================================
def main():
    print("[WD] Echo watchdog supervisor starting (Unified-Radio).")
    sd_notify("READY=1")

    restart_history = {u: [] for u in CRITICAL_SERVICES}

    last_check = 0.0

    while True:
        sd_notify("WATCHDOG=1")

        now = time.time()
        if now - last_check >= CHECK_PERIOD:
            last_check = now

            for unit in CRITICAL_SERVICES:
                active = is_active(unit)
                failed = is_failed(unit)

                if active and not failed:
                    continue

                print(f"[WD] WARNING: {unit} unhealthy (active={active}, failed={failed})")

                # Attempt restart
                if restart_unit(unit):
                    restart_history[unit].append(now)

                    cutoff = now - ESCALATION_WINDOW_SEC
                    restart_history[unit] = [
                        t for t in restart_history[unit] if t >= cutoff
                    ]

                    if len(restart_history[unit]) >= MAX_RESTARTS_PER_SERVICE:
                        print(
                            f"[WD] ESCALATION: {unit} restarted "
                            f"{len(restart_history[unit])} times in last "
                            f"{ESCALATION_WINDOW_SEC}s"
                        )
                        if ENABLE_REBOOT_ON_ESCALATION:
                            reboot_system()
                        else:
                            print("[WD] Escalation logged only (reboot disabled).")

                else:
                    print(f"[WD] CRITICAL: Could not restart {unit}")
                    if ENABLE_REBOOT_ON_ESCALATION:
                        reboot_system()

        time.sleep(1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[WD] Stopped by user.")