"""Virtual RF medium: a loopback packet bus shared by every simulated
radio node on this machine (the flight radio process and any number of
ground-station processes).

Design: each node owns an ephemeral UDP socket and drops a small
"I'm here, reach me on this port" file into sim/state/radio_nodes/.
Sending a packet means looking up every other node's file and unicasting
to each one -- this sidesteps OS broadcast/multicast quirks (firewall
prompts, SO_REUSEPORT portability) while still behaving like a shared
RF channel: anyone can hear anyone, packets can be lost or delayed.
"""
from __future__ import annotations

import glob
import json
import os
import random
import socket
import time
import uuid

from sim.config import SIM_STATE_DIR, get_config

NODES_DIR = os.path.join(SIM_STATE_DIR, "radio_nodes")


class RadioMedium:
    def __init__(self, name: str):
        cfg = get_config()
        self.name = name
        self.loss_pct = cfg.radio_loss_pct
        self.latency_ms = cfg.radio_latency_ms

        os.makedirs(NODES_DIR, exist_ok=True)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._port = self._sock.getsockname()[1]

        self._id = f"{name}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._node_path = os.path.join(NODES_DIR, f"{self._id}.json")
        with open(self._node_path, "w") as f:
            json.dump({"name": name, "port": self._port}, f)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        try:
            os.remove(self._node_path)
        except OSError:
            pass

    def _peer_ports(self):
        ports = []
        for path in glob.glob(os.path.join(NODES_DIR, "*.json")):
            if path == self._node_path:
                continue
            try:
                with open(path) as f:
                    ports.append(json.load(f)["port"])
            except (OSError, ValueError, KeyError):
                continue
        return ports

    def send(self, data: bytes) -> None:
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)
        for port in self._peer_ports():
            if self.loss_pct > 0 and random.random() * 100 < self.loss_pct:
                continue
            try:
                self._sock.sendto(data, ("127.0.0.1", port))
            except OSError:
                pass

    def receive(self, timeout: float = 0.5):
        self._sock.settimeout(max(0.001, timeout))
        try:
            data, _addr = self._sock.recvfrom(8192)
            return data
        except socket.timeout:
            return None
        except OSError:
            return None
