"""Builds the FakeSystemd supervisor and registers the five real Echo FSW
units against sim launchers, mirroring systemd/*.service one-for-one:

    echo-incr-boot.service  -> handled inline at harness startup (see below)
    echo-telem.service      -> sim/run_telem.py   (telem_deps/app.py)
    echo-radio.service      -> sim/run_radio.py   (radio/unified_radio.py)
    echo-watchdog.service   -> sim/run_watchdog.py (watchdog/watchdog.py)
    apogee-video.service    -> oneshot rpicam-vid, triggered on demand

echo-incr-boot.service's *entire* job is "increment boot_count.txt before
echo-telem starts." Its real ExecStart is a bash script with the Pi's
`/home/f25-echo2/...` path hardcoded into a shell variable (not something
Python-side path-patching can reach), so rather than faking a bash
executable too, the harness just performs the equivalent increment
directly -- once at startup, and again on every simulated reboot via
sim/supervisor.py's `_bump_boot_count` (which needs the exact same file
path, wired through the `SIM_BOOT_COUNT_FILE` env var below).
"""
from __future__ import annotations

import os
import sys

from sim.config import get_config, env_for_subprocess
from sim.supervisor import FakeSystemd, UnitSpec

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKEBIN_DIR = os.path.join(REPO_ROOT, "sim", "fakebin")


def default_fsw_home() -> str:
    return os.environ.get("SIM_FSW_HOME", os.path.join(REPO_ROOT, "sim", "state", "fsw_home"))


def boot_count_path(fsw_home: str) -> str:
    return os.path.join(fsw_home, "data", "telemetry", "boot_count.txt")


def bump_boot_count(fsw_home: str) -> int:
    path = boot_count_path(fsw_home)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            n = int(f.read().strip() or "0")
    except (OSError, ValueError):
        n = 0
    n += 1
    with open(path, "w") as f:
        f.write(f"{n}\n")
    return n


def _unit_env(fsw_home: str) -> dict:
    env = env_for_subprocess()
    env["PATH"] = FAKEBIN_DIR + os.pathsep + env.get("PATH", "")
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["SIM_FSW_HOME"] = fsw_home
    return env


def build_supervisor(fsw_home: str | None = None) -> FakeSystemd:
    cfg = get_config()
    fsw_home = fsw_home or default_fsw_home()
    os.makedirs(fsw_home, exist_ok=True)

    # So sim/supervisor.py's reboot handler bumps the same file this
    # module bumps at startup.
    os.environ["SIM_BOOT_COUNT_FILE"] = boot_count_path(fsw_home)

    sup = FakeSystemd(active_target="flight.target")
    env = _unit_env(fsw_home)
    py = sys.executable

    sup.register_unit(UnitSpec(
        name="echo-telem.service",
        argv=[py, os.path.join(REPO_ROOT, "sim", "run_telem.py")],
        cwd=fsw_home, env=env, unit_type="notify", watchdog_sec=30.0,
    ))
    sup.register_unit(UnitSpec(
        name="echo-radio.service",
        argv=[py, os.path.join(REPO_ROOT, "sim", "run_radio.py")],
        cwd=fsw_home, env=env, unit_type="notify", watchdog_sec=30.0,
    ))
    sup.register_unit(UnitSpec(
        name="echo-watchdog.service",
        argv=[py, os.path.join(REPO_ROOT, "sim", "run_watchdog.py")],
        cwd=fsw_home, env=env, unit_type="notify", watchdog_sec=10.0,
    ))

    # cwd=fsw_home (not REPO_ROOT) is deliberate defense-in-depth: if a
    # path patch in sim/patch_paths.py were ever missed, telem_deps.config's
    # *relative* default ("data/telemetry/telemetry.csv") would resolve
    # under fsw_home instead of silently writing into this repo's own
    # tracked data/ directory.
    video_out = os.path.join(fsw_home, "data", "camera", "videos", "apogee_video.h264")
    sup.register_unit(UnitSpec(
        name="apogee-video.service",
        argv=["rpicam-vid", "-t", "30000", "-o", video_out,
              "--width", "1920", "--height", "1080", "--framerate", "30", "--nopreview"],
        cwd=fsw_home, env=env, unit_type="oneshot", restart_on_failure=False,
        enabled_at_boot=False,
    ))

    return sup


def start_flight_stack(sup: FakeSystemd) -> None:
    """Starts the three long-running units, in the same order systemd's
    After=/Requires= graph would (apogee-video stays dormant until
    app.py's apogee trigger calls `systemctl start` on it)."""
    sup.start_unit("echo-telem.service")
    sup.start_unit("echo-radio.service")
    sup.start_unit("echo-watchdog.service")
