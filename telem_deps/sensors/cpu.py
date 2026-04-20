import os
import time

def read_cpu_temp_c():
    """Return CPU temperature in °C."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None

def read_cpu_load_pct(sample_time=0.5):
    """Return the system-wide CPU utilization percentage from /proc/stat."""
    try:
        with open("/proc/stat") as f:
            fields = list(map(int, f.readline().split()[1:]))
            idle1, total1 = fields[3], sum(fields)
        time.sleep(sample_time)
        with open("/proc/stat") as f:
            fields = list(map(int, f.readline().split()[1:]))
            idle2, total2 = fields[3], sum(fields)
        diff_idle = idle2 - idle1
        diff_total = total2 - total1
        if diff_total <= 0:
            return 0.0
        return round(100 * (1 - diff_idle / diff_total), 1)
    except Exception as e:
        print("CPU load read failed:", e)
        return None