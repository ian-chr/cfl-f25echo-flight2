import os
import glob
import shutil

PHOTO_BASE = "/home/f25-echo/echo_fsw/data/camera/photos"
MAX_GB = 3.0  # max allowed total size
MAX_BYTES = MAX_GB * 1024 * 1024 * 1024

def get_total_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            total += os.path.getsize(fp)
    return total

def cleanup_oldest_if_needed():
    size = get_total_size(PHOTO_BASE)
    if size <= MAX_BYTES:
        return False  # nothing deleted

    # gather all jpg files
    files = sorted(
        glob.glob(f"{PHOTO_BASE}/**/*.jpg", recursive=True),
        key=lambda p: os.path.getmtime(p)
    )

    # delete oldest until below limit
    deleted = []
    for f in files:
        if get_total_size(PHOTO_BASE) <= MAX_BYTES:
            break
        deleted.append(f)
        try:
            os.remove(f)
        except:
            pass

    return deleted