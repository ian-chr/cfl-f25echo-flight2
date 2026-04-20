# I2C NMEA GPS worker (@0x42), robust partial-RMC-aware parser.
import threading, time
from typing import Dict, Any

try:
    from smbus import SMBus
    import pynmea2 as _pynmea2
except ImportError:
    SMBus = None
    _pynmea2 = None

BYTES_AVAIL_LSB = 0xFD
BYTES_AVAIL_MSB = 0xFE
DATA_STREAM_REG  = 0xFF


class GPSWorker:
    """
    Robust NMEA-over-I2C GPS Worker (u-blox M9N)
    - Handles partial RMC messages (time but missing date)
    - Accumulates time and date separately until full UTC available
    - Produces gps_dt_utc as soon as possible
    - Produces lat/lon/alt/sats/etc as before
    - Backwards compatible with your existing FSW
    """

    def __init__(self, bus_num=1, addr=0x42, poll_hz=25, chunk_max=32, verbose=True):
        self.bus_num = bus_num
        self.addr = addr
        self.period = 1.0 / float(poll_hz)
        self.chunk_max = int(chunk_max)
        self.verbose = verbose

        self._bus = None
        self._t = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._buf = bytearray()

        self._latest: Dict[str, Any] = {}
        self._lines_seen = 0
        self._last_rmc_ts = 0.0
        self._debug_lines_to_print = 5

        # NEW: partial RMC handling
        self._last_rmc_time = None   # "hhmmss"
        self._last_rmc_date = None   # "ddmmyy"

    def ready(self) -> bool:
        return self._lines_seen > 0

    def start(self):
        if SMBus is None:
            if self.verbose:
                print("GPS: install python3-smbus (or expose it to venv)")
            return

        try:
            self._bus = SMBus(self.bus_num)
        except Exception as e:
            if self.verbose:
                print(f"GPS: open i2c-{self.bus_num} failed: {e}")
            return

        self._stop.clear()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

        if self.verbose:
            print(f"GPS: started on i2c-{self.bus_num} addr 0x{self.addr:02X}")

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=1.0)
        try:
            if self._bus:
                self._bus.close()
        except Exception:
            pass

        if self.verbose:
            print("GPS: stopped")

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    # ----------------------------------------------------------------------
    # I2C helpers
    # ----------------------------------------------------------------------
    def _read_avail(self) -> tuple[int, bool]:
        try:
            lsb = self._bus.read_byte_data(self.addr, BYTES_AVAIL_LSB)
            msb = self._bus.read_byte_data(self.addr, BYTES_AVAIL_MSB)
            n = (msb << 8) | lsb
            overflow = bool(n & 0x8000)
            n &= 0x7FFF
            return n, overflow
        except Exception:
            return 0, False

    def _read_stream(self, n: int) -> bytes:
        out = bytearray()
        while n > 0:
            take = self.chunk_max if n >= self.chunk_max else n
            try:
                blk = self._bus.read_i2c_block_data(self.addr, DATA_STREAM_REG, take)
                out.extend(blk)
            except Exception:
                break
            n -= take
        return bytes(out)

    # ----------------------------------------------------------------------
    # NMEA parsing
    # ----------------------------------------------------------------------
    def _parse_one_line(self, s: str):
        # Debug first few lines
        if self._debug_lines_to_print > 0 and self.verbose:
            print("GPS line:", s)
            self._debug_lines_to_print -= 1

        # ------------------- pynmea2 (if available) ------------------------
        if _pynmea2 is not None:
            try:
                msg = _pynmea2.parse(s, check=True)
                st = getattr(msg, "sentence_type", "")

                if st == "RMC" and getattr(msg, "status", "V") == "A":
                    with self._lock:
                        self._latest.update({
                            "gps_lat": msg.latitude,
                            "gps_lon": msg.longitude,
                            "gps_speed_kn": getattr(msg, "spd_over_grnd", None),
                            "gps_course_deg": getattr(msg, "true_course", None),
                        })

                    # Only valid if both date+time present
                    if getattr(msg, "datestamp", None) and getattr(msg, "timestamp", None):
                        dt = msg.datetime
                        with self._lock:
                            self._latest["gps_dt_utc"] = dt.isoformat() + "Z"

                    self._last_rmc_ts = time.time()

                elif st == "GGA":
                    with self._lock:
                        self._latest.update({
                            "gps_fix_q": int(getattr(msg, "gps_qual", 0) or 0),
                            "gps_sats": int(getattr(msg, "num_sats", 0) or 0),
                            "gps_hdop": float(getattr(msg, "horizontal_dil", 0) or 0),
                            "gps_alt_m": float(getattr(msg, "altitude", 0) or 0),
                        })

                elif st == "GSV":
                    try:
                        sats_in_view = int(getattr(msg, "num_sv_in_view", 0) or 0)
                        with self._lock:
                            self._latest["gps_sats_in_view"] = sats_in_view
                    except Exception:
                        pass

            except Exception:
                pass

        # ------------------- NEW PARTIAL RMC HANDLING ----------------------
        if s.startswith("$") and ",RMC," in s:
            fields = s.split(",")

            if len(fields) > 2:
                time_field = fields[1]  # hhmmss(.sss)
                status = fields[2]

                if len(time_field) >= 6:
                    self._last_rmc_time = time_field[:6]

                if len(fields) >= 10 and len(fields[9]) == 6:
                    self._last_rmc_date = fields[9]

                # If both are known → build full datetime
                if self._last_rmc_time and self._last_rmc_date:
                    try:
                        hh = int(self._last_rmc_time[0:2])
                        mm = int(self._last_rmc_time[2:4])
                        ss = int(self._last_rmc_time[4:6])

                        dd = int(self._last_rmc_date[0:2])
                        mo = int(self._last_rmc_date[2:4])
                        yy_raw = int(self._last_rmc_date[4:6])
                        yy = 2000 + yy_raw if yy_raw < 80 else 1900 + yy_raw

                        dt = f"{yy:04d}-{mo:02d}-{dd:02d}T{hh:02d}:{mm:02d}:{ss:02d}Z"

                        with self._lock:
                            self._latest["gps_dt_utc"] = dt
                    except Exception:
                        pass

        # ------------------- Legacy fallback parsing -----------------------
        if s.startswith("$") and "*" in s and len(s) > 6:
            f = s.strip().split(",")
            t = f[0][3:6]

            if t == "RMC" and len(f) >= 12 and f[2] == "A":
                lat = self._latlon(f[3], f[4], True)
                lon = self._latlon(f[5], f[6], False)
                spd = self._to_float(f[7])
                crs = self._to_float(f[8])
                dt_iso = self._iso_from_rmc(f[1], f[9])

                with self._lock:
                    if lat is not None: self._latest["gps_lat"] = lat
                    if lon is not None: self._latest["gps_lon"] = lon
                    if spd is not None: self._latest["gps_speed_kn"] = spd
                    if crs is not None: self._latest["gps_course_deg"] = crs
                    if dt_iso: self._latest["gps_dt_utc"] = dt_iso

                self._last_rmc_ts = time.time()

            elif t == "GGA" and len(f) >= 15:
                q = self._to_int(f[6])
                sats = self._to_int(f[7])
                hdop = self._to_float(f[8])
                alt = self._to_float(f[9])

                with self._lock:
                    if q is not None: self._latest["gps_fix_q"] = q
                    if sats is not None: self._latest["gps_sats"] = sats
                    if hdop is not None: self._latest["gps_hdop"] = hdop
                    if alt is not None: self._latest["gps_alt_m"] = alt

            elif t == "GSV" and len(f) >= 4:
                sats_in_view = self._to_int(f[3])
                with self._lock:
                    if sats_in_view is not None:
                        self._latest["gps_sats_in_view"] = sats_in_view

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------
    @staticmethod
    def _to_float(x):
        try: return float(x)
        except Exception: return None

    @staticmethod
    def _to_int(x):
        try: return int(x)
        except Exception: return None

    @staticmethod
    def _latlon(val: str, hemi: str, is_lat: bool):
        if not val or not hemi:
            return None
        try:
            if is_lat:
                d = int(val[:2]); m = float(val[2:])
                sign = -1 if hemi.upper() == "S" else 1
            else:
                d = int(val[:3]); m = float(val[3:])
                sign = -1 if hemi.upper() == "W" else 1
            return sign * (d + m/60.0)
        except Exception:
            return None

    @staticmethod
    def _iso_from_rmc(t, d):
        try:
            if not t or not d: return ""
            hh, mi, ss = int(t[:2]), int(t[2:4]), int(t[4:6])
            dd, mm, yy = int(d[:2]), int(d[2:4]), int(d[4:6])
            yy = 2000 + yy if yy < 80 else 1900 + yy
            return f"{yy:04d}-{mm:02d}-{dd:02d}T{hh:02d}:{mi:02d}:{ss:02d}Z"
        except Exception:
            return ""

    # ----------------------------------------------------------------------
    # NMEA buffer management
    # ----------------------------------------------------------------------
    def _parse_lines_from_buf(self):
        while True:
            nl = self._buf.find(b'\n')
            if nl < 0:
                cr = self._buf.find(b'\r')
                if cr >= 0:
                    line = self._buf[:cr+1]; del self._buf[:cr+1]
                    s = line.decode("ascii", errors="ignore").strip()
                    if s:
                        self._lines_seen += 1
                        self._parse_one_line(s)
                break

            line = self._buf[:nl+1]; del self._buf[:nl+1]
            s = line.decode("ascii", errors="ignore").strip()
            if s:
                self._lines_seen += 1
                self._parse_one_line(s)

    def _loop(self):
        self._drain_once()
        next_t = time.monotonic()

        while not self._stop.is_set() and self._bus:
            try:
                n, of = self._read_avail()
                if n:
                    data = self._read_stream(n)
                    if data:
                        self._buf.extend(data)
                        self._parse_lines_from_buf()
            except Exception as e:
                if self.verbose:
                    print("GPS: loop exception:", e)

            next_t += self.period
            sleep_for = next_t - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_t = time.monotonic()

    def _drain_once(self):
        try:
            while True:
                n, _ = self._read_avail()
                if n == 0:
                    break
                self._buf.extend(self._read_stream(n))
            self._parse_lines_from_buf()
        except Exception:
            pass