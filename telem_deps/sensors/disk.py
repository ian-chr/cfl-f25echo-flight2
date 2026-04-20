import os

def read_disk_used_free_kb(path="/"):
    try:
        s = os.statvfs(path)
        total_kb = (s.f_blocks * s.f_frsize) // 1024
        free_kb  = (s.f_bavail * s.f_frsize) // 1024
        return (total_kb - free_kb, free_kb)
    except Exception:
        return (None, None)