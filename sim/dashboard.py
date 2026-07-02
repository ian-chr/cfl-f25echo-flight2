#!/usr/bin/env python3
"""Live mission dashboard: a small stdlib-only HTTP+SSE server backing a
Leaflet ground-track map, a Chart.js altitude/vspeed chart, live telemetry
gauges, and a Three.js 3D attitude cube.

Two independent data feeds, on purpose:

  /stream            Tails the FSW's own telemetry/GPS CSVs (the same
                      files echo-telem.service is writing) and joins them
                      by shared UnixTime. This is exactly the
                      *downlinked-equivalent* data a real ground station
                      would see, at the FSW's real ~5s sample cadence --
                      drives the map, chart, and gauges.
  /attitude_stream    Samples sim.world.get_world() directly at 20 Hz for
                      a smooth-looking attitude cube -- a ground-truth IMU
                      probe (like a bench-test scope), not something the
                      FSW downlinked. Requires this process's SIM_* env
                      vars to match the running sim's (see
                      sim.config.load_active_config_into_env), since
                      FlightState is a pure function of mission time.

Attitude (roll/pitch/yaw) itself is a dashboard-side *estimate*
(accelerometer for roll/pitch, tilt-compensated magnetometer for yaw) in
both feeds -- the real FSW never computes attitude, it just logs raw
accel/gyro/mag.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
STATIC_DIR = os.path.join(REPO_ROOT, "sim", "dashboard_static")

STANDARD_ATM_EXPONENT = 0.1903
ATTITUDE_HZ_PERIOD = 0.05  # 20 Hz -- the 3D cube's ground-truth IMU probe rate


def baro_alt_m(pressure_hpa: float) -> float:
    """Same formula telem_deps/app.py itself uses for its baro-based
    apogee trigger, so the dashboard's altitude fallback (used whenever
    GPS has no fix) matches what the FSW's own logic is acting on."""
    try:
        return 44330.0 * (1.0 - (pressure_hpa / 1013.25) ** STANDARD_ATM_EXPONENT)
    except (ValueError, ZeroDivisionError):
        return 0.0


def estimate_attitude(accel, mag):
    """Tilt-compensated-compass attitude estimate: roll/pitch from the
    accelerometer (valid under the quasi-static assumption that specific
    force is dominated by gravity), yaw from the magnetometer corrected
    for that tilt. Degrees, right-handed, body frame."""
    ax, ay, az = accel
    mx, my, mz = mag
    try:
        roll = math.atan2(ay, az)
        pitch = math.atan2(-ax, math.hypot(ay, az))
    except ValueError:
        return 0.0, 0.0, 0.0

    cos_r, sin_r = math.cos(roll), math.sin(roll)
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    mx2 = mx * cos_p + mz * sin_p
    my2 = mx * sin_r * sin_p + my * cos_r - mz * sin_r * cos_p
    yaw = math.atan2(-my2, mx2) if (mx2 or my2) else 0.0

    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)


class _CsvTailer:
    """Incrementally reads newly-appended, complete CSV lines from a file
    that another process is actively writing to. Assumes the file was
    created with a proper header row (true for anything sim/patch_paths.py
    points the FSW at)."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None
        self._header: list[str] | None = None
        self._buf = ""

    def _ensure_open(self) -> bool:
        if self._fh is not None:
            return True
        if not os.path.exists(self.path):
            return False
        self._fh = open(self.path, "r", newline="")
        header_line = self._fh.readline()
        if not header_line.endswith("\n"):
            # header not fully flushed yet -- try again next poll
            self._fh.close()
            self._fh = None
            return False
        self._header = next(csv.reader(io.StringIO(header_line)))
        return True

    def poll(self) -> list[dict]:
        if not self._ensure_open():
            return []
        self._buf += self._fh.read()
        rows = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if not line.strip():
                continue
            values = next(csv.reader(io.StringIO(line)))
            row = dict(zip(self._header, values))
            rows.append(row)
        return rows


def _to_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class MissionState:
    """Joins telem + GPS rows by their shared UnixTime (app.py writes both
    from the same `ts` each loop iteration) and republishes one combined,
    browser-ready dict per tick to every connected dashboard client."""

    def __init__(self, fsw_home: str):
        self.telem_tail = _CsvTailer(os.path.join(fsw_home, "data", "telemetry", "telemetry.csv"))
        self.gps_tail = _CsvTailer(os.path.join(fsw_home, "data", "gps", "gps_nmea_data.csv"))
        self._gps_by_ts: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self.history: list[dict] = []
        self.max_history = 2000

    def subscribe(self) -> queue.Queue:
        q = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
            backlog = list(self.history[-self.max_history:])
        for row in backlog:
            q.put(row)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _publish(self, combined: dict) -> None:
        with self._lock:
            self.history.append(combined)
            if len(self.history) > self.max_history:
                self.history.pop(0)
            subs = list(self._subscribers)
        for q in subs:
            q.put(combined)

    def poll_once(self) -> None:
        for g in self.gps_tail.poll():
            self._gps_by_ts[g.get("timestamp", "")] = g

        for t in self.telem_tail.poll():
            ts = t.get("UnixTime", "")
            g = self._gps_by_ts.pop(ts, {})

            accel = (_to_float(t.get("AccelX"), 0.0), _to_float(t.get("AccelY"), 0.0), _to_float(t.get("AccelZ"), 9.80665))
            mag = (_to_float(t.get("MagX"), 1.0), _to_float(t.get("MagY"), 0.0), _to_float(t.get("MagZ"), 0.0))
            roll, pitch, yaw = estimate_attitude(accel, mag)

            pressure = _to_float(t.get("BME_Pressure"))
            lat = _to_float(g.get("lat"))
            lon = _to_float(g.get("lon"))
            gps_alt = _to_float(g.get("alt_m"))
            alt_m = gps_alt if gps_alt else (baro_alt_m(pressure) if pressure else 0.0)

            combined = {
                "unix_time": _to_float(t.get("UnixTime"), 0),
                "packet_count": int(_to_float(t.get("PacketCount"), 0)),
                "boot_count": int(_to_float(t.get("numResets"), 0)),
                "lat": lat, "lon": lon, "has_fix": lat is not None and lon is not None,
                "alt_m": alt_m,
                "alt_source": "gps" if gps_alt else "baro",
                "speed_mps": _to_float(g.get("speed_mps")),
                "sats": int(_to_float(g.get("sats_used"), 0)),
                "accel_mps2": accel, "gyro_dps": (
                    _to_float(t.get("GyroX"), 0.0), _to_float(t.get("GyroY"), 0.0), _to_float(t.get("GyroZ"), 0.0)
                ),
                "mag_ut": mag,
                "attitude_deg": {"roll": roll, "pitch": pitch, "yaw": yaw},
                "bme_temp_c": _to_float(t.get("BME_Temp")),
                "bme_pressure_hpa": pressure,
                "bme_humidity_pct": _to_float(t.get("BME_Humidity")),
                "cpu_temp_c": _to_float(t.get("CPUTemp")),
                "cpu_load_pct": _to_float(t.get("CPULoad")),
                "vbatt_v": _to_float(t.get("VBatt")),
                "ibatt_a": _to_float(t.get("IBatt")),
                "tmp1_c": _to_float(t.get("TMP1")),
                "tmp2_c": _to_float(t.get("TMP2")),
            }
            self._publish(combined)


MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css", ".json": "application/json"}


def make_handler(state: MissionState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep stdout quiet; this runs alongside the sim's own logs

        def do_GET(self):
            if self.path == "/" or self.path == "":
                self._serve_file("index.html")
            elif self.path == "/stream":
                self._serve_stream()
            elif self.path == "/attitude_stream":
                self._serve_attitude_stream()
            elif self.path.startswith("/static/"):
                self._serve_file(self.path[len("/static/"):])
            else:
                self.send_error(404)

        def _serve_file(self, rel_path: str):
            safe = os.path.normpath(rel_path).lstrip(os.sep)
            full = os.path.join(STATIC_DIR, safe)
            if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
                self.send_error(404)
                return
            ext = os.path.splitext(full)[1]
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
            self.end_headers()
            with open(full, "rb") as f:
                self.wfile.write(f.read())

        def _serve_stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q = state.subscribe()
            try:
                while True:
                    try:
                        row = q.get(timeout=15.0)
                        payload = f"data: {json.dumps(row)}\n\n"
                    except queue.Empty:
                        payload = ": keepalive\n\n"
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                state.unsubscribe(q)

        def _serve_attitude_stream(self):
            # Ground-truth IMU probe: sampled directly from the live flight
            # world at a fixed high rate, independent of the FSW's own
            # ~5s downlink cadence -- like plugging a scope into the IMU
            # breakout during a bench test, not a claim about telemetered
            # data. Requires this process's SIM_* env vars to match the
            # running sim's (see sim/config.py load_active_config_into_env).
            from sim.world import get_world
            world = get_world()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            try:
                while True:
                    s = world.now()
                    roll, pitch, yaw = estimate_attitude(s.accel_body_mps2, s.mag_ut)
                    payload = {
                        "mission_t": s.mission_t,
                        "phase": s.phase,
                        "attitude_deg": {"roll": roll, "pitch": pitch, "yaw": yaw},
                        "gyro_dps": s.gyro_dps,
                    }
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(ATTITUDE_HZ_PERIOD)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def run(fsw_home: str, port: int, poll_interval: float = 0.5, open_browser: bool = False) -> None:
    state = MissionState(fsw_home)

    def _poll_loop():
        while True:
            state.poll_once()
            time.sleep(poll_interval)

    threading.Thread(target=_poll_loop, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))
    url = f"http://127.0.0.1:{port}/"
    print(f"[dashboard] serving {url} (tailing {fsw_home})")

    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fsw-home", default=os.path.join(REPO_ROOT, "sim", "state", "fsw_home"))
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--open-browser", action="store_true")
    args = ap.parse_args()
    run(args.fsw_home, args.port, open_browser=args.open_browser)


if __name__ == "__main__":
    main()
