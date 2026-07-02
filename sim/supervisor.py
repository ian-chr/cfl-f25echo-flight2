"""Fake systemd: a tiny process supervisor that plays the role systemd
plays for the real deployment -- Restart=on-failure, WatchdogSec=,
is-active/is-failed/restart/reboot -- entirely in userspace, entirely
local to this repo, and incapable of touching the real host.

Why this exists: watchdog/watchdog.py and telem_deps/app.py both shell
out to `systemctl`/`sudo hwclock`/etc. Rather than editing those files
to call some sim-specific API, sim/fakebin/systemctl (a real executable
placed early on PATH) forwards those exact subprocess calls to this
supervisor over a local control socket. The FSW source is untouched.

Two layers, matching the real deployment:
  1. This supervisor = "systemd": restarts a unit if its process exits,
     or if a Type=notify unit stops petting its WatchdogSec heartbeat.
  2. watchdog/watchdog.py (a *managed unit itself*) additionally polls
     echo-telem/echo-radio via `systemctl is-active/is-failed` and
     escalates to `systemctl reboot` -- exactly like the real Pi.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sim.config import CONTROL_SOCK_PATH, SIM_RUNTIME_DIR

NOTIFY_SOCK_DIR = os.path.join(SIM_RUNTIME_DIR, "notify_sockets")


@dataclass
class UnitSpec:
    name: str
    argv: List[str]
    cwd: str
    env: Dict[str, str]
    unit_type: str = "notify"          # "notify" | "oneshot" | "simple"
    watchdog_sec: Optional[float] = None
    restart_on_failure: bool = True
    restart_sec: float = 5.0
    enabled_at_boot: bool = True        # mirrors `systemctl enable`; apogee-video
                                        # is only ever started on demand


@dataclass
class UnitState:
    proc: Optional[subprocess.Popen] = None
    status: str = "inactive"           # "active" | "failed" | "inactive"
    last_heartbeat: float = 0.0
    intentionally_stopped: bool = False
    last_restart_attempt: float = 0.0


class FakeSystemd:
    def __init__(self, active_target: str = "flight.target"):
        self.active_target = active_target
        self.units: Dict[str, UnitSpec] = {}
        self.state: Dict[str, UnitState] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()

        os.makedirs(SIM_RUNTIME_DIR, exist_ok=True)
        os.makedirs(NOTIFY_SOCK_DIR, exist_ok=True)
        if os.path.exists(CONTROL_SOCK_PATH):
            os.remove(CONTROL_SOCK_PATH)

        self._ctrl_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._ctrl_sock.bind(CONTROL_SOCK_PATH)
        self._ctrl_sock.listen(8)

        self._selector = selectors.DefaultSelector()
        self._notify_socks: Dict[str, socket.socket] = {}

        self._threads: List[threading.Thread] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_unit(self, spec: UnitSpec) -> None:
        notify_path = os.path.join(NOTIFY_SOCK_DIR, f"{spec.name}.sock")
        if os.path.exists(notify_path):
            os.remove(notify_path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(notify_path)
        sock.setblocking(False)

        with self._lock:
            self.units[spec.name] = spec
            self.state[spec.name] = UnitState()
            self._notify_socks[spec.name] = sock
            self._selector.register(sock, selectors.EVENT_READ, data=spec.name)

    def notify_socket_path(self, unit_name: str) -> str:
        return os.path.join(NOTIFY_SOCK_DIR, f"{unit_name}.sock")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        t1 = threading.Thread(target=self._control_loop, daemon=True)
        t2 = threading.Thread(target=self._monitor_loop, daemon=True)
        t3 = threading.Thread(target=self._notify_loop, daemon=True)
        for t in (t1, t2, t3):
            t.start()
            self._threads.append(t)

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            for name in list(self.units):
                self._stop_unit_locked(name, mark="inactive")
        try:
            self._ctrl_sock.close()
        except OSError:
            pass
        try:
            os.remove(CONTROL_SOCK_PATH)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Unit control (also called directly by sim/cli.py, not just the
    # control socket -- same code path either way)
    # ------------------------------------------------------------------
    def start_unit(self, name: str) -> bool:
        with self._lock:
            spec = self.units.get(name)
            if spec is None:
                return False
            st = self.state[name]
            if st.proc is not None and st.proc.poll() is None:
                return True  # already running

            env = dict(os.environ)
            env.update(spec.env)
            env["NOTIFY_SOCKET"] = self.notify_socket_path(name)

            if spec.unit_type == "oneshot":
                # systemctl start on a oneshot blocks until ExecStart exits,
                # exactly like real systemd -- this is what surfaces the
                # apogee-video/camera-cycle race noted in CHANGES.md.
                result = subprocess.run(spec.argv, cwd=spec.cwd, env=env)
                st.status = "active" if result.returncode == 0 else "failed"
                return result.returncode == 0

            proc = subprocess.Popen(spec.argv, cwd=spec.cwd, env=env)
            st.proc = proc
            st.status = "active"
            st.intentionally_stopped = False
            st.last_heartbeat = time.time()
            return True

    def stop_unit(self, name: str) -> bool:
        with self._lock:
            return self._stop_unit_locked(name, mark="inactive", intentional=True)

    def _stop_unit_locked(self, name: str, mark: str, intentional: bool = False) -> bool:
        st = self.state.get(name)
        if st is None:
            return False
        if st.proc is not None:
            try:
                st.proc.terminate()
                st.proc.wait(timeout=3)
            except Exception:
                try:
                    st.proc.kill()
                except Exception:
                    pass
        st.proc = None
        st.status = mark
        st.intentionally_stopped = intentional
        return True

    def restart_unit(self, name: str) -> bool:
        with self._lock:
            self._stop_unit_locked(name, mark="inactive")
        return self.start_unit(name)

    def is_active(self, name: str) -> str:
        with self._lock:
            st = self.state.get(name)
            return st.status if st else "inactive"

    def is_failed(self, name: str) -> bool:
        with self._lock:
            st = self.state.get(name)
            return bool(st and st.status == "failed")

    def reboot(self) -> None:
        """Simulates a hard Pi reboot: every managed unit (including the
        watchdog itself) goes down and comes back up fresh. Runs after a
        short delay on a background thread so the systemctl invocation
        that triggered this can return to its caller first -- otherwise
        we'd kill the watchdog process before its own `systemctl reboot`
        subprocess call got a response."""

        def _do_reboot():
            time.sleep(0.5)
            print("[fake-systemd] REBOOT: stopping all units...")
            with self._lock:
                for name in list(self.units):
                    self._stop_unit_locked(name, mark="inactive")
            self._bump_boot_count()
            time.sleep(0.5)
            print("[fake-systemd] REBOOT: booting all units...")
            with self._lock:
                for name, spec in list(self.units.items()):
                    if spec.enabled_at_boot:
                        self.start_unit(name)

        threading.Thread(target=_do_reboot, daemon=True).start()

    def _bump_boot_count(self) -> None:
        path = os.environ.get("SIM_BOOT_COUNT_FILE")
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                with open(path) as f:
                    n = int(f.read().strip() or "0")
            except (OSError, ValueError):
                n = 0
            with open(path, "w") as f:
                f.write(str(n + 1) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------
    def _notify_loop(self) -> None:
        while not self._stop.is_set():
            events = self._selector.select(timeout=0.5)
            for key, _mask in events:
                name = key.data
                sock = key.fileobj
                try:
                    while True:
                        data, _ = sock.recvfrom(256)
                        with self._lock:
                            st = self.state.get(name)
                            if st:
                                st.last_heartbeat = time.time()
                                if b"READY=1" in data and st.status != "failed":
                                    st.status = "active"
                except BlockingIOError:
                    pass

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(1.0)
            now = time.time()
            with self._lock:
                for name, spec in list(self.units.items()):
                    st = self.state[name]
                    if st.proc is None:
                        continue

                    exited = st.proc.poll() is not None
                    hung = (
                        spec.watchdog_sec
                        and st.status == "active"
                        and (now - st.last_heartbeat) > spec.watchdog_sec
                    )

                    if hung and not exited:
                        print(f"[fake-systemd] {name}: missed WatchdogSec heartbeat, killing")
                        try:
                            st.proc.kill()
                        except Exception:
                            pass
                        exited = True

                    if exited and not st.intentionally_stopped:
                        st.status = "failed"
                        st.proc = None
                        if spec.restart_on_failure and (now - st.last_restart_attempt) > spec.restart_sec:
                            st.last_restart_attempt = now
                            print(f"[fake-systemd] {name}: restarting (Restart=on-failure)")
                            self.start_unit(name)

    def _control_loop(self) -> None:
        self._ctrl_sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = self._ctrl_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle_conn(conn)
            except Exception as e:
                print(f"[fake-systemd] control conn error: {e}")
            finally:
                conn.close()

    def _handle_conn(self, conn: socket.socket) -> None:
        conn.settimeout(2.0)
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        req = json.loads(line.decode("utf-8"))
        resp = self._dispatch(req)
        conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

    def _dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd")
        unit = req.get("unit", "")

        if cmd == "is-active":
            status = self.is_active(unit)
            return {"stdout": status, "exit": 0 if status == "active" else 3}
        if cmd == "is-failed":
            failed = self.is_failed(unit)
            return {"stdout": "failed" if failed else "active", "exit": 0 if failed else 1}
        if cmd == "restart":
            ok = self.restart_unit(unit)
            return {"stdout": "", "exit": 0 if ok else 1}
        if cmd == "start":
            ok = self.start_unit(unit)
            return {"stdout": "", "exit": 0 if ok else 1}
        if cmd == "stop":
            ok = self.stop_unit(unit)
            return {"stdout": "", "exit": 0 if ok else 1}
        if cmd == "reboot":
            self.reboot()
            return {"stdout": "", "exit": 0}
        if cmd == "get-default":
            return {"stdout": self.active_target, "exit": 0}
        if cmd == "list-units-targets":
            return {"stdout": f"{self.active_target} loaded active active", "exit": 0}
        if cmd == "daemon-reload":
            return {"stdout": "", "exit": 0}
        if cmd == "status":
            with self._lock:
                snap = {
                    n: {"status": st.status, "pid": st.proc.pid if st.proc else None}
                    for n, st in self.state.items()
                }
            return {"stdout": json.dumps(snap), "exit": 0}
        if cmd == "pid":
            with self._lock:
                st = self.state.get(unit)
                pid = st.proc.pid if (st and st.proc) else None
            return {"stdout": str(pid) if pid else "", "exit": 0 if pid else 1}
        return {"stdout": f"unknown command {cmd!r}", "exit": 1}
