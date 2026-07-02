"""Replay a real (or bench-test) flight CSV as a flight profile.

Reads telem_deps/io/csvlog.py's CORE_HEADER telemetry CSV plus the
companion GPS and RM3100 mag CSVs, and re-derives a FlightState timeline
from them by linear interpolation. Any column that's missing or blank
(e.g. the GPS CSV before first fix) is forward-filled from the last
known value so a virtual sensor never sees a hole in the middle of a
flight it's replaying.
"""
from __future__ import annotations

import bisect
import csv
import os
from typing import Dict, List, Optional, Tuple

from sim.world import FlightProfile, FlightState

STANDARD_ATM_EXPONENT = 0.1903

# Mirrors telem_deps/io/csvlog.py CORE_HEADER and the GPS/MAG headers
# app.py writes -- used as a fallback column order for CSVs that were
# appended to without ever going through maybe_write_header().
CORE_HEADER_COLUMNS = (
    "UnixTime,PacketCount,numResets,"
    "GyroX,GyroY,GyroZ,AccelX,AccelY,AccelZ,"
    "MagX,MagY,MagZ,"
    "TMP1,TMP2,BME_Temp,BME_Pressure,BME_Humidity,"
    "MemUsed,MemFree,DiskUsed,DiskFree,CPULoad,CPUTemp,"
    "BattRaw_V,BattRaw_I,V3V3,I3V3,V5V0,I5V0,VBatt,IBatt,"
    "RegTemp3V3,RegTemp5V0"
).split(",")
GPS_HEADER_COLUMNS = "timestamp,rtc_datetime,lat,lon,alt_m,speed_mps,sats_used,sats_in_view".split(",")
MAG_HEADER_COLUMNS = "timestamp,rm_x,rm_y,rm_z".split(",")


def _to_float(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    v = v.strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _load_rows(path: str, fallback_header: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Load a telemetry/GPS/mag CSV as a list of dict rows.

    Some of the CSVs shipped in data/ were appended to without ever going
    through maybe_write_header() (e.g. a rotated file whose header line
    landed in the `.1` sibling), so the first line is numeric data, not a
    header. Detect that case and fall back to the known column order
    instead of silently reading zero usable rows.
    """
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", newline="") as f:
        lines = list(csv.reader(f))
    if not lines:
        return []

    first_cell = lines[0][0].strip() if lines[0] else ""
    looks_numeric = _to_float(first_cell) is not None
    if looks_numeric and fallback_header:
        header = fallback_header
        data_lines = lines
    else:
        header = lines[0]
        data_lines = lines[1:]

    rows = []
    for line in data_lines:
        rows.append({k: (line[i] if i < len(line) else "") for i, k in enumerate(header)})
    return rows


class _Series:
    """Sorted (t_rel, {field: float-or-None}) samples with forward-fill and
    linear interpolation between bracketing samples."""

    def __init__(self, rows: List[Dict[str, str]], ts_key: str, t0: float, fields: List[str]):
        parsed: List[Tuple[float, Dict[str, Optional[float]]]] = []
        last: Dict[str, Optional[float]] = {k: None for k in fields}
        for row in rows:
            ts = _to_float(row.get(ts_key))
            if ts is None:
                continue
            values = {}
            for k in fields:
                v = _to_float(row.get(k))
                if v is None:
                    v = last.get(k)
                else:
                    last[k] = v
                values[k] = v
            parsed.append((ts - t0, values))
        parsed.sort(key=lambda p: p[0])
        self._t = [p[0] for p in parsed]
        self._v = [p[1] for p in parsed]
        self.fields = fields

    @property
    def empty(self) -> bool:
        return not self._t

    @property
    def duration(self) -> float:
        return self._t[-1] if self._t else 0.0

    def sample(self, t: float) -> Dict[str, float]:
        if self.empty:
            return {k: 0.0 for k in self.fields}
        if t <= self._t[0]:
            return {k: (v if v is not None else 0.0) for k, v in self._v[0].items()}
        if t >= self._t[-1]:
            return {k: (v if v is not None else 0.0) for k, v in self._v[-1].items()}

        i = bisect.bisect_right(self._t, t) - 1
        i = max(0, min(i, len(self._t) - 2))
        t0, t1 = self._t[i], self._t[i + 1]
        v0, v1 = self._v[i], self._v[i + 1]
        frac = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)

        out = {}
        for k in self.fields:
            a, b = v0.get(k), v1.get(k)
            if a is None and b is None:
                out[k] = 0.0
            elif a is None:
                out[k] = b
            elif b is None:
                out[k] = a
            else:
                out[k] = a + (b - a) * frac
        return out


TELEM_FIELDS = [
    "GyroX", "GyroY", "GyroZ", "AccelX", "AccelY", "AccelZ",
    "MagX", "MagY", "MagZ", "BME_Temp", "BME_Pressure", "BME_Humidity",
    "VBatt", "IBatt",
]
GPS_FIELDS = ["lat", "lon", "alt_m", "speed_mps", "sats_used", "sats_in_view"]
MAG_FIELDS = ["rm_x", "rm_y", "rm_z"]


class CsvReplayProfile(FlightProfile):
    def __init__(self, telem_csv: str, gps_csv: str, mag_csv: str, loop: bool = True):
        self.loop = loop

        telem_rows = _load_rows(telem_csv, CORE_HEADER_COLUMNS)
        if not telem_rows:
            raise FileNotFoundError(
                f"SIM_PROFILE=replay but no usable rows in telem CSV: {telem_csv!r}"
            )
        t0 = _to_float(telem_rows[0].get("UnixTime")) or 0.0

        self._telem = _Series(telem_rows, "UnixTime", t0, TELEM_FIELDS)
        self._gps = _Series(_load_rows(gps_csv, GPS_HEADER_COLUMNS), "timestamp", t0, GPS_FIELDS)
        self._mag = _Series(_load_rows(mag_csv, MAG_HEADER_COLUMNS), "timestamp", t0, MAG_FIELDS)
        self._duration = self._telem.duration

    def duration(self) -> Optional[float]:
        return self._duration

    def _wrap(self, t: float) -> float:
        if self._duration <= 0:
            return 0.0
        if self.loop:
            return t % self._duration
        return min(t, self._duration)

    def state_at(self, t: float) -> FlightState:
        t = max(0.0, t)
        tw = self._wrap(t)

        tel = self._telem.sample(tw)
        gps = self._gps.sample(tw)
        mag = self._mag.sample(tw)

        pressure_hpa = tel["BME_Pressure"] or 1013.25
        alt_m = gps["alt_m"]
        if not alt_m:
            alt_m = 44330.0 * (1.0 - (pressure_hpa / 1013.25) ** STANDARD_ATM_EXPONENT)

        # crude vertical speed via a small finite difference
        eps = 1.0
        tel_next = self._telem.sample(self._wrap(tw + eps))
        gps_next = self._gps.sample(self._wrap(tw + eps))
        p_next = tel_next["BME_Pressure"] or pressure_hpa
        alt_next = gps_next["alt_m"] or (44330.0 * (1.0 - (p_next / 1013.25) ** STANDARD_ATM_EXPONENT))
        vspeed = (alt_next - alt_m) / eps

        phase = "ascent" if vspeed > 0.5 else ("descent" if vspeed < -0.5 else "ground_pre")

        return FlightState(
            mission_t=t,
            phase=phase,
            lat_deg=gps["lat"],
            lon_deg=gps["lon"],
            alt_m=alt_m,
            vspeed_mps=vspeed,
            ground_speed_mps=gps["speed_mps"],
            heading_deg=0.0,
            pressure_hpa=pressure_hpa,
            temp_c=tel["BME_Temp"] or 15.0,
            humidity_pct=tel["BME_Humidity"] or 30.0,
            gas_ohms=10000.0,
            accel_body_mps2=(tel["AccelX"], tel["AccelY"], tel["AccelZ"]),
            gyro_dps=(tel["GyroX"], tel["GyroY"], tel["GyroZ"]),
            mag_ut=(mag["rm_x"] or tel["MagX"], mag["rm_y"] or tel["MagY"], mag["rm_z"] or tel["MagZ"]),
            sats_used=int(gps["sats_used"] or 0),
            sats_in_view=int(gps["sats_in_view"] or 0),
            fix_quality=1 if (gps["sats_used"] or 0) > 0 else 0,
            batt_voltage=tel["VBatt"] or 7.5,
            batt_current=tel["IBatt"] or 0.9,
        )
