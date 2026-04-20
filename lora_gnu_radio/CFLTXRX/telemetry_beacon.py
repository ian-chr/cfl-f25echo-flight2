#!/usr/bin/env python3
import os
import struct
import time

# -------------------------------------------------------------------
# CSV PATHS
# -------------------------------------------------------------------
TELEM_CSV = "/home/f25-echo/echo_fsw/data/telemetry/telemetry.csv"
GPS_CSV   = "/home/f25-echo/echo_fsw/data/gps/gps_nmea_data.csv"
MAG_CSV   = "/home/f25-echo/echo_fsw/data/mag/mag_data.csv"

# -------------------------------------------------------------------
# CONSTANTS
# -------------------------------------------------------------------
# IMU / SENSOR SCALING
GYRO_LSB_PER_DPS  = 32768.0 / 2000.0
ACC_LSB_PER_G     = 32768.0 / 16.0
MAG_LSB_PER_UT    = 1.0 / 0.15
TMP_LSB_PER_C     = 16.0
RM3100_LSB_PER_UT = 75.0

# PDU ADC SCALING (REVERSE ENGINEERING)
# The Decoder expects 0-4095 counts and applies the hardware formula.
# We must take Physics Values -> and turn them back into Counts.
ADC_REF_V    = 2.5
ADC_MAX_CNT  = 4095.0
COUNTS_PER_V = ADC_MAX_CNT / ADC_REF_V

# -------------------------------------------------------------------
# TYPE HELPERS
# -------------------------------------------------------------------
def u16(x):
    v = int(round(float(x)))
    return max(0, min(65535, v))

def s16(x):
    v = int(round(float(x)))
    return max(-32768, min(32767, v))

def u32(x):
    v = int(round(float(x)))
    return max(0, min(4294967295, v))

def s32(x):
    v = int(round(float(x)))
    return max(-2147483648, min(2147483647, v))

# -------------------------------------------------------------------
# FILE READER (ROBUST)
# -------------------------------------------------------------------
def read_last_valid(path, min_cols):
    """
    Reads the last valid line from a CSV.
    - Handles large files (reads only tail).
    - Handles partial writes/race conditions (validates column count).
    - Returns the split list of strings, or None.
    """
    if not os.path.exists(path):
        return None
    
    try:
        # Read only the last 4KB
        with open(path, "r") as f:
            f.seek(0, 2) # Seek to end
            fsize = f.tell()
            f.seek(max(fsize - 4096, 0), 0)
            lines = f.readlines()
    except Exception:
        return None

    # Iterate backwards (newest to oldest)
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln: 
            continue
            
        parts = ln.split(',')
        
        # Check 1: Column Count
        if len(parts) < min_cols:
            continue

        # Check 2: Data Integrity (Try parsing the timestamp)
        # This ensures we don't read a half-written line
        try:
            float(parts[0]) # Timestamp check
            return parts
        except ValueError:
            continue
            
    return None

# ===================================================================
# BUILD BEACON
# ===================================================================
def build_beacon():
    # 1. Load CSV Data
    t = read_last_valid(TELEM_CSV, 33)
    g = read_last_valid(GPS_CSV, 8)
    m = read_last_valid(MAG_CSV, 4)

    # 2. Validate Telemetry (Critical)
    if t is None:
        # We prefer to fail loudly here so the radio service knows to retry
        raise RuntimeError("telemetry.csv missing or insufficient columns")

    # 3. Handle Optional Sensors
    if g is None: g = ["0"] * 8
    if m is None: m = ["0"] * 4

    # --- PARSE TELEMETRY ---
    try:
        unix_time  = u32(t[0])
        pkt_count  = u16(t[1])
        boot_count = u16(t[2])

        # IMU (Standard Calibration)
        gyro_x = s16(float(t[3]) * GYRO_LSB_PER_DPS)
        gyro_y = s16(float(t[4]) * GYRO_LSB_PER_DPS)
        gyro_z = s16(float(t[5]) * GYRO_LSB_PER_DPS)

        gyro_x = s16(float(t[3]) * GYRO_LSB_PER_DPS)
        gyro_y = s16(float(t[4]) * GYRO_LSB_PER_DPS)
        gyro_z = s16(float(t[5]) * GYRO_LSB_PER_DPS)

        accel_x = s16((float(t[6]) / 9.80665) * ACC_LSB_PER_G)
        accel_y = s16((float(t[7]) / 9.80665) * ACC_LSB_PER_G)
        accel_z = s16((float(t[8]) / 9.80665) * ACC_LSB_PER_G)

        mag_x_imu = s16(float(t[9]) * MAG_LSB_PER_UT)
        mag_y_imu = s16(float(t[10]) * MAG_LSB_PER_UT)
        mag_z_imu = s16(float(t[11]) * MAG_LSB_PER_UT)

        # Environment
        tmp1 = s16(float(t[12]) * TMP_LSB_PER_C)
        tmp2 = s16(float(t[13]) * TMP_LSB_PER_C)
        bme_temp  = s16(float(t[14]) * 100)
        bme_press = u32(float(t[15]) * 100)
        bme_hum   = u16(float(t[16]) * 100)

        # System Stats
        mem_used   = u32(t[17])
        mem_free   = u32(t[18])
        disk_used  = u32(t[19])
        disk_free  = u32(t[20])
        cpu_load   = u16(float(t[21]) * 100)
        cpu_temp   = s16(float(t[22]) * 10)

        # --- PDU REVERSE ENGINEERING ---
        # Input: Calibrated Physics Float (Volts/Amps/C)
        # Output: Raw ADC Count (u16)
        
        # Inputs
        val_batt_v    = float(t[23])
        val_batt_i    = float(t[24])
        val_v3v3      = float(t[25])
        val_i3v3      = float(t[26])
        val_v5v0      = float(t[27])
        val_i5v0      = float(t[28])
        val_vbatt_reg = float(t[29])
        val_ibatt_reg = float(t[30])
        val_reg_t3    = float(t[31])
        val_reg_t5    = float(t[32])

        # Voltage Dividers: Raw = (V_phys * (R2/(R1+R2))) * CountsPerV
        # Batt Raw (100k/12k)
        batt_raw_v = u16((val_batt_v * (12.0/112.0)) * COUNTS_PER_V)
        # 3V3 (100k/60.4k)
        v3v3       = u16((val_v3v3 * (60.4/160.4)) * COUNTS_PER_V)
        # 5V0 (100k/33.2k)
        v5v0       = u16((val_v5v0 * (33.2/133.2)) * COUNTS_PER_V)
        # VBatt Reg (100k/12k)
        vbatt      = u16((val_vbatt_reg * (12.0/112.0)) * COUNTS_PER_V)

        # Currents: Raw = (I_phys * Rsense * Gain) * CountsPerV
        # Batt Raw (12mOhm, 100x)
        batt_raw_i = u16((val_batt_i * 0.012 * 100.0) * COUNTS_PER_V)
        # 3V3 (5mOhm, 100x)
        i3v3       = u16((val_i3v3 * 0.005 * 100.0) * COUNTS_PER_V)
        # 5V0 (12mOhm, 100x)
        i5v0       = u16((val_i5v0 * 0.012 * 100.0) * COUNTS_PER_V)
        # VBatt Reg (12mOhm, 100x)
        ibatt      = u16((val_ibatt_reg * 0.012 * 100.0) * COUNTS_PER_V)

        # Temperatures (LM20): Raw = (1.866 - T) * CountsPerV / 0.01169 (Simplified)
        # V_pin = 1.866 - (T * 0.01169)
        v_pin_t3 = 1.866 - (val_reg_t3 * 0.01169)
        v_pin_t5 = 1.866 - (val_reg_t5 * 0.01169)
        reg_3v3  = u16(v_pin_t3 * COUNTS_PER_V)
        reg_5v0  = u16(v_pin_t5 * COUNTS_PER_V)

    except Exception as e:
        # If a float conversion fails, we must catch it
        print(f"DEBUG: Data conversion error: {e}")
        raise RuntimeError("Data conversion error in beacon builder")

    # --- PARSE GPS ---
    try:
        gps_lat  = s32(float(g[2]) * 1e7)
        gps_lon  = s32(float(g[3]) * 1e7)
        gps_alt  = u32(float(g[4]) * 100000)
        gps_vel  = u16(float(g[5]) * 1000)
        gps_sats = u16(float(g[6]))
    except:
        gps_lat = gps_lon = gps_alt = gps_vel = gps_sats = 0

    # --- PARSE MAG (RM3100) ---
    try:
        rm_x = s32(float(m[1]) * RM3100_LSB_PER_UT)
        rm_y = s32(float(m[2]) * RM3100_LSB_PER_UT)
        rm_z = s32(float(m[3]) * RM3100_LSB_PER_UT)
    except:
        rm_x = rm_y = rm_z = 0

    # 4. PACK
    beacon = struct.pack(
        "<IHH"              # time, pkt, boots
        "hhh hhh hhh"       # gyro, accel, imu mag
        "hh"                # TMP1 TMP2
        "h I H"             # bme
        "I I I I"           # mem/disk
        "H h"               # cpu load/temp
        "H H H H H H H H"   # PDU (All u16 now)
        "h h"               # reg temps (kept as h for safety, but are pos counts)
        "i i I H B"         # GPS
        "i i i",            # RM3100
        unix_time, pkt_count, boot_count,
        gyro_x, gyro_y, gyro_z,
        accel_x, accel_y, accel_z,
        mag_x_imu, mag_y_imu, mag_z_imu,
        tmp1, tmp2,
        bme_temp, bme_press, bme_hum,
        mem_used, mem_free, disk_used, disk_free,
        cpu_load, cpu_temp,
        batt_raw_v, batt_raw_i, v3v3, i3v3, v5v0, i5v0, vbatt, ibatt,
        reg_3v3, reg_5v0,
        gps_lat, gps_lon, gps_alt, gps_vel, gps_sats,
        rm_x, rm_y, rm_z
    )

    if len(beacon) != 105:
        raise RuntimeError(f"Beacon wrong size: {len(beacon)} bytes")

    return beacon