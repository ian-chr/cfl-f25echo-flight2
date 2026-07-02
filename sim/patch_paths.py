"""Redirects the FSW's hardcoded `/home/f25-echo2/echo_fsw/...` path
constants to a local sim data directory.

Every one of these is a plain module-level string that its owning
module re-reads at call time (never captured into a closure at import
time), so reassigning the attribute on the already-imported module is
enough to redirect it -- no FSW source file changes needed. This is the
same trick run_telem.py already uses for `config.OUTPUT_CSV`; this
module just applies it everywhere else the same hardcoded-path pattern
shows up (radio, camera, eventlog, photo_cleanup).

Must be called *after* sim/fake_modules is on sys.path (these imports
pull in telem_deps.app -> sensors.imu/baro/mag/rtc, which import
board/busio/smbus) and before the target module's main()/worker runs.
"""
from __future__ import annotations

import os


def patch_all_fsw_paths(fsw_home: str) -> None:
    from sim.devices.host_stats import install_host_stat_shims
    install_host_stat_shims()

    for sub in ("data/telemetry", "data/gps", "data/mag", "data/logs",
                "data/camera/photos/full", "data/camera/photos/thumb", "data/camera/videos"):
        os.makedirs(os.path.join(fsw_home, sub), exist_ok=True)

    telem_csv = os.path.join(fsw_home, "data", "telemetry", "telemetry.csv")

    # config.OUTPUT_CSV alone is *not* enough: app.py does
    # `from .config import OUTPUT_CSV`, which binds its own independent
    # copy of the name into telem_deps.app's namespace at import time.
    # Reassigning telem_deps.config.OUTPUT_CSV afterwards does not change
    # what app.py's own code looks up -- both must be patched.
    from telem_deps import config as cfg
    cfg.OUTPUT_CSV = telem_csv

    import telem_deps.app as app
    app.OUTPUT_CSV = telem_csv
    app.GPS_OUTPUT_CSV = os.path.join(fsw_home, "data", "gps", "gps_nmea_data.csv")
    app.MAG_OUTPUT_CSV = os.path.join(fsw_home, "data", "mag", "mag_data.csv")

    import telem_deps.util.eventlog as eventlog
    eventlog.EVENT_LOG_PATH = os.path.join(fsw_home, "data", "logs", "watchdog.log")

    import telem_deps.util.photo_cleanup as photo_cleanup
    photo_cleanup.PHOTO_BASE = os.path.join(fsw_home, "data", "camera", "photos")

    import telem_deps.sensors.camera_worker as camera_worker
    camera_worker.BASE_DIR = os.path.join(fsw_home, "data", "camera", "photos")
    camera_worker.FULL_DIR = os.path.join(camera_worker.BASE_DIR, "full")
    camera_worker.THUMB_DIR = os.path.join(camera_worker.BASE_DIR, "thumb")
    camera_worker.VIDEO_DIR = os.path.join(fsw_home, "data", "camera", "videos")
    os.makedirs(camera_worker.FULL_DIR, exist_ok=True)
    os.makedirs(camera_worker.THUMB_DIR, exist_ok=True)
    os.makedirs(camera_worker.VIDEO_DIR, exist_ok=True)

    import radio.unified_radio as unified_radio
    unified_radio.THUMB_DIR = camera_worker.THUMB_DIR
    unified_radio.FULL_DIR = camera_worker.FULL_DIR

    # telemetry_beacon.py has its *own* independent (and stale -- still
    # "f25-echo", not "f25-echo2") copies of the telemetry/GPS/mag CSV
    # paths, separate from telem_deps.config.OUTPUT_CSV / app.py's
    # GPS_OUTPUT_CSV / MAG_OUTPUT_CSV patched above.
    import lora_gnu_radio.CFLTXRX.telemetry_beacon as telemetry_beacon
    telemetry_beacon.TELEM_CSV = cfg.OUTPUT_CSV
    telemetry_beacon.GPS_CSV = app.GPS_OUTPUT_CSV
    telemetry_beacon.MAG_CSV = app.MAG_OUTPUT_CSV
