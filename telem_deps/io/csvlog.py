import os

# ---- Slimmed-down header matching current telemetry packet layout ----
CORE_HEADER = (
    "UnixTime,PacketCount,numResets,"
    "GyroX,GyroY,GyroZ,AccelX,AccelY,AccelZ,"
    "MagX,MagY,MagZ,"
    "TMP1,TMP2,BME_Temp,BME_Pressure,BME_Humidity,"
    "MemUsed,MemFree,DiskUsed,DiskFree,CPULoad,CPUTemp,"
    "BattRaw_V,BattRaw_I,"
    "V3V3,I3V3,"
    "V5V0,I5V0,"
    "VBatt,IBatt,"
    "RegTemp3V3,RegTemp5V0"
)

# ---- Legacy minimal headers (kept for old imports or tests) ----
BASE_HEADER = "ts_unix,cpu_temp_c,mem_used_kB,mem_free_kB,disk_used_kB,disk_free_kB,tmp102_c"
GPS_HEADER  = ",gps_lat,gps_lon,gps_alt_m,gps_vel_mps"

HEADER = CORE_HEADER  # main default header

def ensure_parent_folder(path):
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)

def maybe_write_header(csv_path, enable_gps=False, use_core_header=True):
    """
    Writes the correct CSV header if the file does not exist or is empty.
    The default `use_core_header=True` writes the slimmed-down CORE_HEADER
    for the main telemetry CSV. If False, falls back to BASE_HEADER for legacy use.
    """
    if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
        with open(csv_path, "a") as f:
            if use_core_header:
                f.write(CORE_HEADER + "\n")
            else:
                f.write(BASE_HEADER + (GPS_HEADER if enable_gps else "") + "\n")

def maybe_rotate_csv(csv_path, max_bytes):
    """Rotates the telemetry log file when it exceeds `max_bytes`."""
    if max_bytes <= 0:
        return
    try:
        if os.path.getsize(csv_path) >= max_bytes:
            os.replace(csv_path, csv_path + ".1")
    except FileNotFoundError:
        pass