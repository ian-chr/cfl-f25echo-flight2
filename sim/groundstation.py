#!/usr/bin/env python3
"""Virtual ground station for the Echo FSW simulator.

Joins the same virtual RF medium as the simulated flight radio
(radio/unified_radio.py running against sim/fake_modules/adafruit_rfm9x.py),
using the *real* RAP encode/decode routines from lora_gnu_radio/CFLTXRX
and radio/protocol.py -- so this exercises the flight software's actual
beacon/CAP/ACK/DAP protocol handling, not a mock of it.

Usage:
    python -m sim.groundstation                  # listen + auto-request
    python -m sim.groundstation --no-auto-request # listen only, type
                                                    # "get" + Enter to request
"""
from __future__ import annotations

import argparse
import os
import select
import struct
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from lora_gnu_radio.CFLTXRX.decode import decode_rap
from lora_gnu_radio.CFLTXRX.encode import encode_rap
from radio.protocol import fletcher16
from sim.devices.radio_medium import RadioMedium

BEACON_FLAG = 0x03
CAP_FLAG = 0x02
DAP_FLAG = 0x01
ACK_FLAG = 0x05
CMD_DOWNLOAD_DAP = 1

# Mirrors lora_gnu_radio/CFLTXRX/telemetry_beacon.py's struct.pack format
# and LSB-per-unit scale constants -- this is the exact inverse of how
# that module builds the 105-byte beacon, so decoding here is a
# self-consistent round trip through that one module (independent of
# whatever divider ratios telem_deps/sensors/eddy_pdu.py uses on the
# flight side -- the beacon already baked its own numbers in).
BEACON_FMT = "<IHHhhhhhhhhhhhhIHIIIIHhHHHHHHHHhhiiIHBiii"
assert struct.calcsize(BEACON_FMT) == 105

GYRO_LSB_PER_DPS = 32768.0 / 2000.0
ACC_LSB_PER_G = 32768.0 / 16.0
MAG_LSB_PER_UT = 1.0 / 0.15
TMP_LSB_PER_C = 16.0
RM3100_LSB_PER_UT = 75.0
COUNTS_PER_V = 4095.0 / 2.5


def decode_beacon(raw: bytes) -> dict:
    (
        unix_time, pkt_count, boot_count,
        gyro_x, gyro_y, gyro_z,
        accel_x, accel_y, accel_z,
        mag_x, mag_y, mag_z,
        tmp1, tmp2,
        bme_temp, bme_press, bme_hum,
        mem_used, mem_free, disk_used, disk_free,
        cpu_load, cpu_temp,
        batt_raw_v, batt_raw_i, v3v3, i3v3, v5v0, i5v0, vbatt, ibatt,
        reg_3v3, reg_5v0,
        gps_lat, gps_lon, gps_alt, gps_vel, gps_sats,
        rm_x, rm_y, rm_z,
    ) = struct.unpack(BEACON_FMT, raw)

    return {
        "unix_time": unix_time,
        "pkt_count": pkt_count,
        "boot_count": boot_count,
        "gyro_dps": (gyro_x / GYRO_LSB_PER_DPS, gyro_y / GYRO_LSB_PER_DPS, gyro_z / GYRO_LSB_PER_DPS),
        "accel_mps2": (
            accel_x / ACC_LSB_PER_G * 9.80665,
            accel_y / ACC_LSB_PER_G * 9.80665,
            accel_z / ACC_LSB_PER_G * 9.80665,
        ),
        "mag_ut": (mag_x / MAG_LSB_PER_UT, mag_y / MAG_LSB_PER_UT, mag_z / MAG_LSB_PER_UT),
        "tmp1_c": tmp1 / TMP_LSB_PER_C,
        "tmp2_c": tmp2 / TMP_LSB_PER_C,
        "bme_temp_c": bme_temp / 100.0,
        "bme_press_hpa": bme_press / 100.0,
        "bme_hum_pct": bme_hum / 100.0,
        "mem_used_kb": mem_used,
        "mem_free_kb": mem_free,
        "disk_used_kb": disk_used,
        "disk_free_kb": disk_free,
        "cpu_load_pct": cpu_load / 100.0,
        "cpu_temp_c": cpu_temp / 10.0,
        "batt_raw_v": (batt_raw_v / COUNTS_PER_V) * (112.0 / 12.0),
        "vbatt_v": (vbatt / COUNTS_PER_V) * (112.0 / 12.0),
        "gps_lat": gps_lat / 1e7,
        "gps_lon": gps_lon / 1e7,
        "gps_alt_m": gps_alt / 100000.0,
        "gps_vel_mps": gps_vel / 1000.0,
        "gps_sats": gps_sats,
        "rm3100_ut": (rm_x / RM3100_LSB_PER_UT, rm_y / RM3100_LSB_PER_UT, rm_z / RM3100_LSB_PER_UT),
    }


def build_cap(refnum: int, cmd: int, args: bytes) -> bytes:
    """CAP payload: pid(1) length(2 LE) refnum(2 LE) ex_time(4 LE) cmd(2 LE)
    args(var) checksum(2) -- exactly what
    radio/unified_radio.py:handle_cap_and_downlink parses."""
    pid = 1
    ex_time = int(time.time())
    header = (
        pid.to_bytes(1, "little")
        + (0).to_bytes(2, "little")  # placeholder length
        + refnum.to_bytes(2, "little")
        + ex_time.to_bytes(4, "little")
        + cmd.to_bytes(2, "little")
    )
    body = header + args
    length = len(body) + 2
    body = pid.to_bytes(1, "little") + length.to_bytes(2, "little") + body[3:]
    checksum = fletcher16(body)
    return body + checksum


class GroundStation:
    def __init__(self, downloads_dir: str):
        self.medium = RadioMedium(name="groundstation")
        self.downloads_dir = downloads_dir
        os.makedirs(downloads_dir, exist_ok=True)
        self._refnum = 1
        self._dap_parts: dict = {}
        self._got_beacon = threading.Event()
        self._stop = threading.Event()

    def request_download(self, filename: str = "latest", parts: str = "ALL"):
        args = f"{filename},{parts}".encode("utf-8")
        cap = build_cap(self._refnum, CMD_DOWNLOAD_DAP, args)
        rap = encode_rap(CAP_FLAG, cap)
        self.medium.send(bytes(rap))
        print(f"[GS] -> CAP sent: ref={self._refnum} cmd=DOWNLOAD_DAP args={args!r}")
        self._refnum += 1

    def _handle_beacon(self, rap_data: bytes):
        try:
            b = decode_beacon(rap_data)
        except struct.error as e:
            print(f"[GS] beacon decode failed ({len(rap_data)} bytes): {e}")
            return
        self._got_beacon.set()
        print(
            f"[GS] BEACON  t={b['unix_time']} pkt={b['pkt_count']} boots={b['boot_count']}  "
            f"alt={b['gps_alt_m']:.1f}m  lat={b['gps_lat']:.5f} lon={b['gps_lon']:.5f}  "
            f"batt={b['vbatt_v']:.2f}V  cpu={b['cpu_temp_c']:.1f}C/{b['cpu_load_pct']:.0f}%  "
            f"bme={b['bme_temp_c']:.1f}C/{b['bme_press_hpa']:.1f}hPa"
        )

    def _handle_ack(self, rap_data: bytes):
        pid = rap_data[0]
        cmd = int.from_bytes(rap_data[1:3], "little")
        refnum = int.from_bytes(rap_data[3:5], "little")
        print(f"[GS] ACK received: pid={pid} cmd={cmd} ref={refnum}")

    def _handle_dap(self, rap_data: bytes):
        pid = rap_data[0]
        part_length = int.from_bytes(rap_data[1:3], "little")
        file_num = int.from_bytes(rap_data[3:5], "little")
        part = int.from_bytes(rap_data[5:7], "little")
        total = int.from_bytes(rap_data[7:9], "little")
        chunk = rap_data[9:part_length]

        parts = self._dap_parts.setdefault(file_num, {})
        parts[part] = chunk

        if len(parts) % 20 == 0 or len(parts) == total:
            print(f"[GS] DAP file={file_num} part {part + 1}/{total} ({len(parts)}/{total} received)")

        if len(parts) >= total and all(p in parts for p in range(total)):
            data = b"".join(parts[p] for p in range(total))
            out_path = os.path.join(self.downloads_dir, f"file_{file_num}_{int(time.time())}.jpg")
            with open(out_path, "wb") as f:
                f.write(data)
            print(f"[GS] Image reassembled: {out_path} ({len(data)} bytes)")
            del self._dap_parts[file_num]

    def run(self, auto_request_after: float | None = 5.0):
        print("[GS] Ground station listening on the virtual RF medium...")
        auto_fired = auto_request_after is None
        start = time.monotonic()

        while not self._stop.is_set():
            if not auto_fired and (time.monotonic() - start) >= auto_request_after:
                self.request_download("latest", "ALL")
                auto_fired = True

            raw = self.medium.receive(timeout=0.3)
            if raw is None:
                continue
            if all(b == 0 for b in raw):
                continue
            try:
                _packet, rap_data, pid, sid, flag = decode_rap(bytearray(raw))
            except Exception as e:
                print(f"[GS] RAP decode failed: {e}")
                continue
            if rap_data is None:
                continue

            if flag == BEACON_FLAG:
                self._handle_beacon(rap_data)
            elif flag == ACK_FLAG:
                self._handle_ack(rap_data)
            elif flag == DAP_FLAG:
                self._handle_dap(rap_data)

    def stop(self):
        self._stop.set()
        self.medium.close()


def _stdin_command_loop(gs: GroundStation):
    while not gs._stop.is_set():
        r, _, _ = select.select([sys.stdin], [], [], 0.3)
        if r:
            line = sys.stdin.readline().strip()
            if line in ("get", "download"):
                gs.request_download("latest", "ALL")
            elif line in ("quit", "exit"):
                gs.stop()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--downloads-dir", default=os.path.join(REPO_ROOT, "sim", "state", "gs_downloads"))
    ap.add_argument("--auto-request-after", type=float, default=5.0,
                     help="Seconds after starting to auto-send a CAP requesting the latest "
                          "thumbnail (default 5s). Use --no-auto-request to require typing 'get'.")
    ap.add_argument("--no-auto-request", action="store_true")
    args = ap.parse_args()

    gs = GroundStation(args.downloads_dir)
    auto_after = None if args.no_auto_request else args.auto_request_after

    t = threading.Thread(target=_stdin_command_loop, args=(gs,), daemon=True)
    t.start()

    try:
        gs.run(auto_request_after=auto_after)
    except KeyboardInterrupt:
        pass
    finally:
        gs.stop()


if __name__ == "__main__":
    main()
