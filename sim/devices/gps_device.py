"""Virtual u-blox NMEA-over-I2C GPS module (the M9N wired at 0x42).

Implements the same "bytes available" + streaming-register protocol
that telem_deps/sensors/gps.py's GPSWorker polls:

    read_byte_data(0xFD) -> bytes-available LSB
    read_byte_data(0xFE) -> bytes-available MSB
    read_i2c_block_data(0xFF, n) -> next n bytes of the NMEA stream

NMEA sentences are regenerated roughly once per real second (matching
real receiver output cadence) using whatever the flight world says
mission time is *at that instant* -- so the data rate looks like real
hardware even when the mission clock itself is time-compressed.
"""
from __future__ import annotations

import threading
import time

BYTES_AVAIL_LSB = 0xFD
BYTES_AVAIL_MSB = 0xFE
DATA_STREAM_REG = 0xFF

REFILL_PERIOD_S = 1.0
COLD_START_FIX_DELAY_S = 3.0  # real seconds before the very first fix, like real hardware


def _nmea_checksum(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def _sentence(talker_body: str) -> bytes:
    return f"${talker_body}*{_nmea_checksum(talker_body)}\r\n".encode("ascii")


def _lat_to_nmea(lat_deg: float):
    hemi = "N" if lat_deg >= 0 else "S"
    lat = abs(lat_deg)
    deg = int(lat)
    minutes = (lat - deg) * 60.0
    return f"{deg:02d}{minutes:07.4f}", hemi


def _lon_to_nmea(lon_deg: float):
    hemi = "E" if lon_deg >= 0 else "W"
    lon = abs(lon_deg)
    deg = int(lon)
    minutes = (lon - deg) * 60.0
    return f"{deg:03d}{minutes:07.4f}", hemi


class UbloxGpsVirtualDevice:
    def __init__(self, world):
        self.world = world
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._last_refill = 0.0
        self._start_wall = time.monotonic()

    def _has_fix(self) -> bool:
        return (time.monotonic() - self._start_wall) >= COLD_START_FIX_DELAY_S

    def _build_sentences(self) -> bytes:
        s = self.world.now()
        now = time.gmtime(time.time())
        hhmmss = time.strftime("%H%M%S", now) + ".00"
        ddmmyy = time.strftime("%d%m%y", now)

        has_fix = self._has_fix() and s.sats_used > 0
        out = bytearray()

        if has_fix:
            lat_str, lat_h = _lat_to_nmea(s.lat_deg)
            lon_str, lon_h = _lon_to_nmea(s.lon_deg)
            speed_kn = s.ground_speed_mps / 0.514444
            rmc = (
                f"GPRMC,{hhmmss},A,{lat_str},{lat_h},{lon_str},{lon_h},"
                f"{speed_kn:.2f},{s.heading_deg:.1f},{ddmmyy},,,A"
            )
            gga = (
                f"GPGGA,{hhmmss},{lat_str},{lat_h},{lon_str},{lon_h},"
                f"{s.fix_quality},{s.sats_used:02d},1.0,{s.alt_m:.1f},M,0.0,M,,"
            )
        else:
            rmc = f"GPRMC,{hhmmss},V,,,,,,,{ddmmyy},,,N"
            gga = f"GPGGA,{hhmmss},,,,,{0},{0:02d},99.9,,M,,M,,"

        gsv = f"GPGSV,1,1,{s.sats_in_view:02d}"

        out += _sentence(rmc)
        out += _sentence(gga)
        out += _sentence(gsv)
        return bytes(out)

    def _maybe_refill(self) -> None:
        now = time.monotonic()
        if now - self._last_refill >= REFILL_PERIOD_S:
            self._last_refill = now
            self._buf.extend(self._build_sentences())

    # ------------------------------------------------------------------
    def read_byte_data(self, reg: int) -> int:
        with self._lock:
            self._maybe_refill()
            n = len(self._buf)
            if reg == BYTES_AVAIL_LSB:
                return n & 0xFF
            if reg == BYTES_AVAIL_MSB:
                return (n >> 8) & 0xFF
            return 0

    def read_i2c_block_data(self, reg: int, length: int):
        with self._lock:
            if reg != DATA_STREAM_REG:
                return [0] * length
            take = min(length, len(self._buf))
            chunk = bytes(self._buf[:take])
            del self._buf[:take]
            # Pad with 0xFF like real u-blox modules do when you ask for
            # more bytes than are actually buffered.
            return list(chunk) + [0xFF] * (length - take)
