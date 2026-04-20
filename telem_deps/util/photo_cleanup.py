"""
Photo storage monitor.

Flight duration is short enough that we can afford to keep every image.
Rather than auto-deleting old photos (which destroyed flight data on F25-Echo
Launch 1), this module just reports disk usage and warns if we cross a soft
cap. It never deletes anything.
"""

import os

PHOTO_BASE = "/home/f25-echo2/echo_fsw/data/camera/photos"
SOFT_CAP_GB = 30.0
SOFT_CAP_BYTES = SOFT_CAP_GB * 1024 * 1024 * 1024


def get_total_size(path):
    total = 0
    if not os.path.isdir(path):
        return 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def cleanup_oldest_if_needed():
    """
    Kept for API compatibility with camera_worker.py.

    No longer deletes anything. Returns a short warning list (list of dicts)
    when the soft cap is exceeded so camera_worker can log it, and an empty
    list otherwise.
    """
    size = get_total_size(PHOTO_BASE)
    if size <= SOFT_CAP_BYTES:
        return []

    return [{
        "warning": "photo_storage_over_soft_cap",
        "bytes": size,
        "soft_cap_bytes": int(SOFT_CAP_BYTES),
        "soft_cap_gb": SOFT_CAP_GB,
    }]
