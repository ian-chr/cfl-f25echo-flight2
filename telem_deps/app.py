import os
import time
import json
import subprocess
from datetime import datetime
from smbus import SMBus
from systemd import daemon

from telem_deps.util.eventlog import log_event
from .sensors.eddy_pdu import read_eddy_pdu

from .config import (
    SAMPLE_PERIOD_SECONDS, OUTPUT_CSV, MAX_CSV_BYTES, DISK_PATH,
    I2C_BUS_NUM, TMP102_ADDR, TMP102_TEMP_REG,
    TMP1022_ADDR, TMP1022_TEMP_REG,
    ENABLE_GPS, GPS_BUS, GPS_ADDR, GPS_POLL_HZ
)

from .sensors.cpu import read_cpu_temp_c, read_cpu_load_pct
from .sensors.memory import read_memory_used_free_kb
from .sensors.disk import read_disk_used_free_kb
from .sensors.tmp102 import read_tmp102_c
from .sensors.imu import read_imu_data
from .sensors.baro import read_baro_data
from .sensors.gps import GPSWorker
from .sensors.mag import read_magnetometer
from .sensors.rtc import read_rtc_time
from .sensors.camera_worker import CameraWorker

from .io import ensure_parent_folder, maybe_write_header, maybe_rotate_csv


# ---------------------------------------------------------------------
#   PATHS
# ---------------------------------------------------------------------

GPS_OUTPUT_CSV = "/home/f25-echo/echo_fsw/data/gps/gps_nmea_data.csv"
MAG_OUTPUT_CSV = "/home/f25-echo/echo_fsw/data/mag/mag_data.csv"

# ---------------------------------------------------------------------
#   SMALL HELPERS
# ---------------------------------------------------------------------

GPS_HEADER = (
    "timestamp,rtc_datetime,lat,lon,alt_m,speed_mps,"
    "sats_used,sats_in_view\n"
)
MAG_HEADER = "timestamp,rm_x,rm_y,rm_z\n"


def ensure_gps_csv():
    """Rotate GPS CSV if needed and ensure header exists."""
    maybe_rotate_csv(GPS_OUTPUT_CSV, MAX_CSV_BYTES)
    if (not os.path.exists(GPS_OUTPUT_CSV) or
            os.path.getsize(GPS_OUTPUT_CSV) == 0):
        with open(GPS_OUTPUT_CSV, "w") as g:
            g.write(GPS_HEADER)


def ensure_mag_csv():
    """Rotate MAG CSV if needed and ensure header exists."""
    maybe_rotate_csv(MAG_OUTPUT_CSV, MAX_CSV_BYTES)
    if (not os.path.exists(MAG_OUTPUT_CSV) or
            os.path.getsize(MAG_OUTPUT_CSV) == 0):
        with open(MAG_OUTPUT_CSV, "w") as m:
            m.write(MAG_HEADER)

def _fmt_int_or_blank(v):
    return "" if v is None else str(int(v))

def _fmt_float_or_blank(v, nd=3):
    return "" if v is None else f"{v:.{nd}f}"

def read_boot_count():
    try:
        base_dir = os.path.dirname(OUTPUT_CSV)
        path = os.path.join(base_dir, "boot_count.txt")
        with open(path, "r") as f:
            val = f.read().strip()
            return int(val)
    except Exception:
        return 0


# ---------------------------------------------------------------------
#   MAIN APPLICATION
# ---------------------------------------------------------------------

def main():
    # Tell systemd we started (for WatchdogSec)
    daemon.notify("READY=1")
    log_event("FSW_START", {"msg": "telemetry app starting"})

    ensure_parent_folder(OUTPUT_CSV)
    ensure_parent_folder(GPS_OUTPUT_CSV)
    ensure_parent_folder(MAG_OUTPUT_CSV)

    ensure_gps_csv()
    ensure_mag_csv()

    boot_count = read_boot_count()
    print(f"Boot count: {boot_count}")
    log_event("FSW_BOOT_COUNT", {"boot_count": boot_count})

    # -----------------------------------------------------------------
    #   GPS WORKER START
    # -----------------------------------------------------------------
    gpsw = None

    if ENABLE_GPS:
        gpsw = GPSWorker(
            bus_num=GPS_BUS,
            addr=GPS_ADDR,
            poll_hz=GPS_POLL_HZ,
            verbose=False
        )
        gpsw.start()

        t0 = time.monotonic()
        while (time.monotonic() - t0) < 0.5 and not gpsw.ready():
            time.sleep(0.05)

    # Core CSV header
    maybe_rotate_csv(OUTPUT_CSV, MAX_CSV_BYTES)
    maybe_write_header(OUTPUT_CSV, enable_gps=ENABLE_GPS)

    period = max(0.5, float(SAMPLE_PERIOD_SECONDS))
    next_time = time.monotonic()
    print(f"Logging every {period}s → {OUTPUT_CSV}")

    try:
        bus = SMBus(I2C_BUS_NUM)
    except FileNotFoundError:
        print("I2C bus not found. Enable I2C and reboot.")
        log_event("I2C_ERROR", {"msg": "I2C bus not found"})
        return

    packet_count = 0
    last_heartbeat_log = 0  # for periodic WATCHDOG_HEARTBEAT_OK logging

    # -----------------------------------------------------------------
    #   ALTITUDE CALLBACK FOR CAMERA
    # -----------------------------------------------------------------
    def get_current_altitude_ft():
        """Returns current altitude in feet for camera altitude triggers"""
        if not ENABLE_GPS or not gpsw:
            return 0
        
        try:
            fix = gpsw.snapshot()
            alt_m = fix.get("gps_alt_m")
            
            if alt_m is not None:
                # Convert meters to feet
                return alt_m * 3.28084
        except Exception:
            pass
        
        return 0

    # -----------------------------------------------------------------
    #   START CAMERA WITH ALTITUDE CALLBACK
    # -----------------------------------------------------------------
    cam = CameraWorker(
        period_seconds=10,
        altitude_callback=get_current_altitude_ft
    )
    cam.start()

    # -----------------------------------------------------------------
    #   MAIN TELEMETRY LOOP
    # -----------------------------------------------------------------
    try:
        while True:
            # Systemd watchdog heartbeat
            daemon.notify("WATCHDOG=1")
            packet_count += 1

            # Log a heartbeat event roughly every ~5 minutes
            if packet_count - last_heartbeat_log >= 60:  # 60 * 5s = 300s
                log_event("WATCHDOG_HEARTBEAT_OK", {"packet_count": packet_count})
                last_heartbeat_log = packet_count

            ts = int(time.time())

            # ----------------------------- Core sensors
            cpu_c = read_cpu_temp_c()
            cpu_load = read_cpu_load_pct()
            mem_used_kb, mem_free_kb = read_memory_used_free_kb()
            disk_used_kb, disk_free_kb = read_disk_used_free_kb(DISK_PATH)

            try:
                t_tmp102 = read_tmp102_c(bus, TMP102_ADDR, TMP102_TEMP_REG)
            except Exception:
                t_tmp102 = None

            try:
                t_tmp102_2 = read_tmp102_c(bus, TMP1022_ADDR, TMP1022_TEMP_REG)
            except Exception:
                t_tmp102_2 = None

            try:
                accel, gyro, mag = read_imu_data()
            except Exception:
                accel = gyro = mag = (None, None, None)

            try:
                t_baro, p_baro, h_baro, gas = read_baro_data()
            except Exception:
                t_baro = p_baro = h_baro = gas = None
            
            try:
                pdu = read_eddy_pdu(bus)
            except Exception:
                pdu = {
                    "VBATT_RAW_V": None,
                    "IBATT_RAW_A": None,
                    "V3V3_V": None,
                    "I3V3_A": None,
                    "V5V0_V": None,
                    "I5V0_A": None,
                    "VBATT_V": None,
                    "IBATT_A": None,
                    "REGT_3V3_C": None,
                    "REGT_5V0_C": None
                }

            # -----------------------------------------------------------------
            # GPS + RTC TIME & POSITION
            # -----------------------------------------------------------------
            lat = lon = alt = spd_mps = sats = sats_in_view = None
            rtc_str = ""

            if ENABLE_GPS and gpsw:
                fix = gpsw.snapshot()

                lat = fix.get("gps_lat")
                lon = fix.get("gps_lon")
                alt = fix.get("gps_alt_m")
                spd_kn = fix.get("gps_speed_kn")
                spd_mps = (float(spd_kn) * 0.514444) if spd_kn is not None else None
                sats = fix.get("gps_sats")
                sats_in_view = fix.get("gps_sats_in_view")

                # Always read RTC for logging
                rtc_dt = read_rtc_time()
                rtc_str = rtc_dt.format() if rtc_dt else ""

                # gps clock update
                gps_iso = fix.get("gps_dt_utc")
                if gps_iso and sats and sats >= 4:
                    try:
                        gps_dt = datetime.fromisoformat(gps_iso.replace("Z", ""))

                        if gps_dt.year > 2020:

                            # Read current RTC time
                            rtc_now = read_rtc_time()

                            if rtc_now:
                                rtc_dt = datetime(
                                    rtc_now.year, rtc_now.month, rtc_now.day,
                                    rtc_now.hour, rtc_now.minute, rtc_now.second
                                )
                            else:
                                rtc_dt = None

                            # Compute drift if RTC is valid
                            drift_ok = False
                            if rtc_dt:
                                drift = abs((gps_dt - rtc_dt).total_seconds())
                                drift_ok = drift > 2.0    # sync only if drift > 2 seconds

                            # Rate limit: sync at most once per 60 seconds
                            now_mono = time.monotonic()
                            last_sync_ts = globals().get("_last_time_sync", 0)
                            enough_time = (now_mono - last_sync_ts) > 60

                            if drift_ok and enough_time:
                                print(f"[TIME] Drift detected ({drift:.2f}s) → syncing RTC from GPS")

                                # 1. GPS → RTC
                                set_rtc_cmd = gps_dt.strftime("%Y-%m-%d %H:%M:%S")
                                subprocess.run(["sudo", "hwclock", "--set", "--date", set_rtc_cmd], check=False)
                                subprocess.run(["sudo", "hwclock", "-w"], check=False)

                                # 2. RTC → system
                                print("[TIME] Updating system clock from RTC...")
                                subprocess.run(["sudo", "hwclock", "-s"], check=False)

                                # Store last sync timestamp
                                globals()["_last_time_sync"] = now_mono

                    except Exception as e:
                        print("[TIME] GPS/RTC drift-sync error:", e)

            # -----------------------------------------------------------------
            # APOGEE VIDEO TRIGGER (GPS + Baro + Time fallback)
            # NOTE: This is separate from the altitude-triggered videos in camera_worker
            # -----------------------------------------------------------------

            # --- Get mission time since boot (seconds) ---
            mission_time = packet_count * SAMPLE_PERIOD_SECONDS

            # --- GPS data ---
            gps_alt = alt        # already parsed above
            gps_vspeed = spd_mps # m/s

            # --- Baro data ---
            baro_temp, baro_press, baro_hum, baro_gas = t_baro, p_baro, h_baro, gas

            # Convert baro pressure to altitude IF valid
            baro_alt = None
            if baro_press not in (None, "", "nan"):
                try:
                    # Standard atmosphere model
                    baro_alt = 44330.0 * (1.0 - (float(baro_press) / 1013.25) ** 0.1903)
                except Exception:
                    baro_alt = None

            # Track previous baro altitude to detect the turn at apogee
            prev_baro_alt = globals().get("_PREV_BARO_ALT")
            desc_detected = False
            if prev_baro_alt is not None and baro_alt is not None:
                if baro_alt < prev_baro_alt:  # descent detected
                    desc_detected = True
            globals()["_PREV_BARO_ALT"] = baro_alt  # update for next loop


            # ----------------- TRIGGER LOGIC -----------------
            if not globals().get("_APOGEE_VIDEO_TRIGGERED", False):

                trigger = False
                reason = ""

                # 1. GPS-based apogee (best case)
                if gps_alt not in (None, "", "nan") and gps_vspeed is not None:
                    if gps_alt > 25000 and abs(gps_vspeed) < 0.5:
                        trigger = True
                        reason = "GPS_APOGEE"

                # 2. Baro-based apogee (backup)
                elif baro_alt is not None and desc_detected:  # turning point detected
                    if baro_alt > 25000:
                        trigger = True
                        reason = "BARO_APOGEE"

                # 3. Time fallback (guaranteed capture)
                elif mission_time > 60 * 60:  # 60 minutes
                    trigger = True
                    reason = "TIME_FALLBACK"

                # ---- Execute trigger ----
                if trigger:
                    print(f"[APOGEE] Video trigger! Reason={reason}")
                    log_event("APOGEE_VIDEO_TRIGGER", {
                        "reason": reason,
                        "gps_alt": gps_alt,
                        "gps_vspeed": gps_vspeed,
                        "baro_alt": baro_alt,
                        "mission_time": mission_time
                    })

                    subprocess.run(
                        ["systemctl", "start", "apogee-video.service"],
                        check=False
                    )

                    globals()["_APOGEE_VIDEO_TRIGGERED"] = True

            # -----------------------------------------------------------------
            # CORE TELEMETRY CSV
            # -----------------------------------------------------------------
            fields = [
                str(ts),                   # Unix Time
                str(packet_count),         # Packet Count
                str(boot_count),           # numResets (boot count)
                _fmt_float_or_blank(gyro[0]),
                _fmt_float_or_blank(gyro[1]),
                _fmt_float_or_blank(gyro[2]),
                _fmt_float_or_blank(accel[0]),
                _fmt_float_or_blank(accel[1]),
                _fmt_float_or_blank(accel[2]),
                _fmt_float_or_blank(mag[0]),   # MagX
                _fmt_float_or_blank(mag[1]),   # MagY
                _fmt_float_or_blank(mag[2]),   # MagZ
                _fmt_float_or_blank(t_tmp102),    # TMP1
                _fmt_float_or_blank(t_tmp102_2),  # TMP2
                _fmt_float_or_blank(t_baro),      # BME Temp
                _fmt_float_or_blank(p_baro),      # BME Pressure
                _fmt_float_or_blank(h_baro),      # BME Humidity
                _fmt_int_or_blank(mem_used_kb),
                _fmt_int_or_blank(mem_free_kb),
                _fmt_int_or_blank(disk_used_kb),
                _fmt_int_or_blank(disk_free_kb),
                _fmt_float_or_blank(cpu_load, 1),
                _fmt_float_or_blank(cpu_c),
            ]

            fields.extend([
                _fmt_float_or_blank(pdu["VBATT_RAW_V"]),
                _fmt_float_or_blank(pdu["IBATT_RAW_A"]),
                _fmt_float_or_blank(pdu["V3V3_V"]),
                _fmt_float_or_blank(pdu["I3V3_A"]),
                _fmt_float_or_blank(pdu["V5V0_V"]),
                _fmt_float_or_blank(pdu["I5V0_A"]),
                _fmt_float_or_blank(pdu["VBATT_V"]),
                _fmt_float_or_blank(pdu["IBATT_A"]),
                _fmt_float_or_blank(pdu["REGT_3V3_C"]),
                _fmt_float_or_blank(pdu["REGT_5V0_C"]),
            ])

            row = ",".join(fields)
            print(row)
            with open(OUTPUT_CSV, "a") as f:
                f.write(row + "\n")

            # -----------------------------------------------------------------
            # MAG CSV
            # -----------------------------------------------------------------
            try:
                rm_x, rm_y, rm_z = read_magnetometer()
            except Exception:
                rm_x = rm_y = rm_z = (None, None, None)

            mag_fields = [
                str(ts),
                _fmt_float_or_blank(rm_x, 3),
                _fmt_float_or_blank(rm_y, 3),
                _fmt_float_or_blank(rm_z, 3),
            ]
            with open(MAG_OUTPUT_CSV, "a") as m:
                m.write(",".join(mag_fields) + "\n")

            # -----------------------------------------------------------------
            # GPS CSV
            # -----------------------------------------------------------------
            if ENABLE_GPS and gpsw:
                gps_fields = [
                    str(ts),
                    rtc_str,
                    _fmt_float_or_blank(lat, 6),
                    _fmt_float_or_blank(lon, 6),
                    _fmt_float_or_blank(alt, 2),
                    _fmt_float_or_blank(spd_mps, 3),
                    _fmt_int_or_blank(sats),
                    _fmt_int_or_blank(sats_in_view),
                ]
                print("[GPS] Writing GPS row:", gps_fields)
                with open(GPS_OUTPUT_CSV, "a") as g:
                    g.write(",".join(gps_fields) + "\n")

            # Rotate CSVs if too big
            maybe_rotate_csv(GPS_OUTPUT_CSV, MAX_CSV_BYTES)
            maybe_rotate_csv(MAG_OUTPUT_CSV, MAX_CSV_BYTES)
            maybe_rotate_csv(OUTPUT_CSV, MAX_CSV_BYTES)

            # Loop timing
            next_time += period
            sleep_for = next_time - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_time = time.monotonic()

    except KeyboardInterrupt:
        print("\nStopped by user.")
        log_event("FSW_STOP", {"reason": "KeyboardInterrupt"})
    finally:
        # Close I2C bus
        try:
            bus.close()
        except Exception:
            pass

        # Stop GPS worker if it was started
        if gpsw:
            gpsw.stop()

        # Stop camera worker ALWAYS
        try:
            cam.stop()
        except Exception:
            pass

        # Log safe shutdown
        log_event("FSW_EXIT", {"msg": "telemetry loop exited"})