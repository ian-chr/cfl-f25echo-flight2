"""Tiny client used by the sim/fakebin/* executables to talk to the
FakeSystemd control socket (sim/supervisor.py). Kept dependency-free
(stdlib only) since fakebin scripts run as `#!/usr/bin/env python3`
subprocesses, not necessarily under the sim venv.
"""
from __future__ import annotations

import json
import socket

from sim.config import CONTROL_SOCK_PATH


def send_control(cmd: str, unit: str = "", timeout: float = 5.0) -> dict:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(CONTROL_SOCK_PATH)
        s.sendall((json.dumps({"cmd": cmd, "unit": unit}) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        if not buf:
            return {"stdout": "", "exit": 1, "error": "no response from fake systemd"}
        return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
    except OSError as e:
        return {"stdout": "", "exit": 1, "error": f"fake systemd unreachable: {e}"}
