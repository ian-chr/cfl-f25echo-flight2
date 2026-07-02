#!/usr/bin/env python3
"""Operator CLI for the Echo FSW simulator.

    python -m sim.cli run                 # start the full flight stack
    python -m sim.cli run --time-scale 60 --ground-station --dashboard
    python -m sim.cli status              # query a running sim
    python -m sim.cli crash echo-radio.service   # SIGKILL a unit's process
                                                   # (tests watchdog restart)
    python -m sim.cli reboot              # simulate a full Pi reboot
    python -m sim.cli start apogee-video.service  # trigger the one-shot
    python -m sim.cli groundstation        # run just the ground station
    python -m sim.cli dashboard            # run just the live mission dashboard
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class _Shutdown(Exception):
    pass


def _install_sigterm_handler():
    def _handler(signum, frame):
        raise _Shutdown()
    signal.signal(signal.SIGTERM, _handler)


def cmd_run(args) -> int:
    # A plain `kill <pid>` (SIGTERM) must still stop every unit's
    # subprocess -- otherwise they're orphaned and keep running (and, in
    # this FSW's case, keep appending to whatever CSV they were pointed
    # at) after the sim's own top-level process is gone.
    _install_sigterm_handler()

    if args.time_scale is not None:
        os.environ["SIM_TIME_SCALE"] = str(args.time_scale)
    if args.profile is not None:
        os.environ["SIM_PROFILE"] = args.profile
    if args.seed is not None:
        os.environ["SIM_SEED"] = str(args.seed)
    if args.rtc_drift is not None:
        os.environ["SIM_RTC_DRIFT_SEC"] = str(args.rtc_drift)
    if args.radio_loss is not None:
        os.environ["SIM_RADIO_LOSS_PCT"] = str(args.radio_loss)
    if args.ground_pre is not None:
        os.environ["SIM_GROUND_PRE_S"] = str(args.ground_pre)
    if args.fsw_home is not None:
        os.environ["SIM_FSW_HOME"] = args.fsw_home

    from sim.config import env_for_subprocess, get_config, write_active_config_file
    from sim.harness import bump_boot_count, build_supervisor, default_fsw_home, start_flight_stack

    fsw_home = args.fsw_home or default_fsw_home()
    boot_n = bump_boot_count(fsw_home)
    cfg = get_config()
    write_active_config_file()

    print("=" * 72)
    print("Echo FSW Simulator")
    print(f"  profile     : {cfg.profile}")
    print(f"  time scale  : {cfg.time_scale}x real time")
    print(f"  fsw home    : {fsw_home}")
    print(f"  boot count  : {boot_n}")
    print(f"  epoch (utc) : {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(cfg.epoch_unix))}")
    print("=" * 72)

    sup = build_supervisor(fsw_home)
    sup.start()
    start_flight_stack(sup)

    gs_proc = None
    if args.ground_station:
        gs_env = dict(os.environ)
        gs_env["PYTHONUNBUFFERED"] = "1"
        gs_proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO_ROOT, "sim", "groundstation.py")],
            cwd=REPO_ROOT, env=gs_env,
        )

    dash_proc = None
    if args.dashboard:
        # The dashboard's 3D attitude cube reads live ground-truth state
        # directly from sim.world (see sim/dashboard.py's /attitude_stream),
        # so it needs the *exact* same mission clock (epoch/scale/profile/
        # seed) as the flight stack -- env_for_subprocess(), not a plain
        # os.environ copy, is what guarantees that.
        dash_env = env_for_subprocess()
        dash_env["PYTHONUNBUFFERED"] = "1"
        dash_argv = [sys.executable, os.path.join(REPO_ROOT, "sim", "dashboard.py"),
                     "--fsw-home", fsw_home, "--port", str(args.dashboard_port)]
        if not args.no_open_browser:
            dash_argv.append("--open-browser")
        dash_proc = subprocess.Popen(dash_argv, cwd=REPO_ROOT, env=dash_env)
        print(f"  dashboard   : http://127.0.0.1:{args.dashboard_port}/")

    print("Flight stack running (echo-telem, echo-radio, echo-watchdog). Ctrl-C to stop.")
    print("In another terminal: python -m sim.cli status | crash <unit> | reboot | groundstation")
    try:
        while True:
            time.sleep(1.0)
    except (KeyboardInterrupt, _Shutdown):
        print("\nShutting down sim...")
    finally:
        if gs_proc is not None:
            gs_proc.terminate()
        if dash_proc is not None:
            dash_proc.terminate()
        sup.shutdown()
    return 0


def _control(cmd: str, unit: str = "") -> dict:
    from sim.fakebin_client import send_control
    return send_control(cmd, unit)


def cmd_status(_args) -> int:
    resp = _control("status")
    if resp.get("exit") != 0:
        print(f"No sim appears to be running ({resp.get('error', resp.get('stdout'))})", file=sys.stderr)
        return 1
    snap = json.loads(resp["stdout"])
    for name, info in snap.items():
        print(f"  {name:28s} {info['status']:10s} pid={info['pid']}")
    return 0


def cmd_reboot(_args) -> int:
    resp = _control("reboot")
    print("Reboot requested." if resp.get("exit") == 0 else f"Failed: {resp}")
    return resp.get("exit", 1)


def cmd_start(args) -> int:
    resp = _control("start", args.unit)
    print(resp.get("stdout", "") or ("ok" if resp.get("exit") == 0 else "failed"))
    return resp.get("exit", 1)


def cmd_stop(args) -> int:
    resp = _control("stop", args.unit)
    print("stopped" if resp.get("exit") == 0 else f"failed: {resp}")
    return resp.get("exit", 1)


def cmd_crash(args) -> int:
    resp = _control("pid", args.unit)
    if resp.get("exit") != 0 or not resp.get("stdout"):
        print(f"Could not find a running pid for {args.unit}: {resp}", file=sys.stderr)
        return 1
    pid = int(resp["stdout"])
    os.kill(pid, signal.SIGKILL)
    print(f"SIGKILL sent to {args.unit} (pid {pid}). Watch `status`/logs for the restart.")
    return 0


def cmd_groundstation(args) -> int:
    from sim import groundstation
    sys.argv = ["groundstation"] + (["--no-auto-request"] if args.no_auto_request else [])
    groundstation.main()
    return 0


def cmd_dashboard(args) -> int:
    from sim.config import load_active_config_into_env
    if load_active_config_into_env():
        print("Loaded mission clock (epoch/scale/profile/seed) from the running sim.")
    else:
        print("No active sim config found -- attitude cube will use this process's own "
              "defaults, which won't match a sim started elsewhere. Run `sim.cli run` first, "
              "or set SIM_* env vars to match it.", file=sys.stderr)

    from sim import dashboard
    from sim.harness import default_fsw_home
    dashboard.run(
        fsw_home=args.fsw_home or default_fsw_home(),
        port=args.port,
        open_browser=not args.no_open_browser,
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Start the full simulated flight stack in the foreground")
    p_run.add_argument("--time-scale", type=float, default=None, help="Mission-seconds per real second (default 1.0)")
    p_run.add_argument("--profile", choices=["synthetic", "replay"], default=None)
    p_run.add_argument("--seed", type=int, default=None)
    p_run.add_argument("--rtc-drift", type=float, default=None, help="Inject this many seconds of RTC drift")
    p_run.add_argument("--radio-loss", type=float, default=None, help="Simulated packet loss percentage")
    p_run.add_argument("--ground-pre", type=float, default=None, help="Seconds on the pad before ascent (synthetic profile)")
    p_run.add_argument("--fsw-home", default=None, help="Where sim'd FSW data/ lives (default sim/state/fsw_home)")
    p_run.add_argument("--ground-station", action="store_true", help="Also launch the virtual ground station")
    p_run.add_argument("--dashboard", action="store_true", help="Also launch the live mission dashboard (browser)")
    p_run.add_argument("--dashboard-port", type=int, default=8787)
    p_run.add_argument("--no-open-browser", action="store_true", help="Don't auto-open the dashboard in a browser")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="Show unit status for a running sim")
    p_status.set_defaults(func=cmd_status)

    p_reboot = sub.add_parser("reboot", help="Simulate a full Pi reboot (never touches the real host)")
    p_reboot.set_defaults(func=cmd_reboot)

    p_start = sub.add_parser("start", help="systemctl start <unit> (e.g. apogee-video.service)")
    p_start.add_argument("unit")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="systemctl stop <unit>")
    p_stop.add_argument("unit")
    p_stop.set_defaults(func=cmd_stop)

    p_crash = sub.add_parser("crash", help="SIGKILL a unit's process to test watchdog restart/escalation")
    p_crash.add_argument("unit")
    p_crash.set_defaults(func=cmd_crash)

    p_gs = sub.add_parser("groundstation", help="Run the virtual ground station in this terminal")
    p_gs.add_argument("--no-auto-request", action="store_true")
    p_gs.set_defaults(func=cmd_groundstation)

    p_dash = sub.add_parser("dashboard", help="Run the live mission dashboard against a running sim")
    p_dash.add_argument("--fsw-home", default=None)
    p_dash.add_argument("--port", type=int, default=8787)
    p_dash.add_argument("--no-open-browser", action="store_true")
    p_dash.set_defaults(func=cmd_dashboard)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
