import os
import time
import threading
import subprocess
from datetime import datetime
from PIL import Image

from telem_deps.util.eventlog import log_event
from telem_deps.util.photo_cleanup import cleanup_oldest_if_needed

# Updated paths
BASE_DIR  = "/home/f25-echo/echo_fsw/data/camera/photos"
FULL_DIR  = f"{BASE_DIR}/full"
THUMB_DIR = f"{BASE_DIR}/thumb"
VIDEO_DIR = "/home/f25-echo/echo_fsw/data/camera/videos"


class CameraWorker:
    """
    Takes a full-res picture every N seconds and saves:
      - full:  full-resolution image
      - thumb: optimized lower-resolution JPEG (for downlink)
    
    Also handles altitude-triggered video capture with camera locking
    to prevent conflicts between photo and video operations.
    """

    def __init__(self, period_seconds=10, altitude_callback=None):
        self.period = period_seconds
        self._stop = threading.Event()
        self._thread = None
        self.altitude_callback = altitude_callback  # Function that returns current altitude
        
        # Camera lock to prevent photo/video conflicts
        self._camera_lock = threading.Lock()
        
        # Track which altitude videos have been captured
        self._videos_captured = {
            27000: False,
            30000: False,
            33000: False,  # Original burst altitude
            36000: False   # Original float altitude
        }
        
        # Last altitude check for video triggers
        self._last_altitude = 0
        self._altitude_check_interval = 1.0  # Check altitude every second

        # Ensure directories exist
        os.makedirs(FULL_DIR, exist_ok=True)
        os.makedirs(THUMB_DIR, exist_ok=True)
        os.makedirs(VIDEO_DIR, exist_ok=True)

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        
        # Start altitude monitoring thread if callback provided
        if self.altitude_callback:
            self._altitude_thread = threading.Thread(target=self._altitude_loop, daemon=True)
            self._altitude_thread.start()
        
        log_event("CAMERA_START")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if hasattr(self, '_altitude_thread'):
            self._altitude_thread.join(timeout=1)

    def _loop(self):
        """Main photo capture loop"""
        while not self._stop.is_set():
            try:
                # Only capture if camera is not locked by video
                if self._camera_lock.acquire(blocking=False):
                    try:
                        self._capture()
                    finally:
                        self._camera_lock.release()
                else:
                    log_event("CAMERA_PHOTO_SKIPPED", {"reason": "camera_in_use"})
            except Exception as e:
                log_event("CAMERA_ERROR", {"error": str(e)})
            
            time.sleep(self.period)

    def _altitude_loop(self):
        """Monitor altitude and trigger videos at specific thresholds"""
        while not self._stop.is_set():
            try:
                if self.altitude_callback:
                    current_alt = self.altitude_callback()
                    
                    # Check each altitude threshold
                    for target_alt, captured in self._videos_captured.items():
                        # Trigger if we've crossed threshold and haven't captured yet
                        if not captured and current_alt >= target_alt and self._last_altitude < target_alt:
                            self._trigger_altitude_video(target_alt, current_alt)
                    
                    self._last_altitude = current_alt
                    
            except Exception as e:
                log_event("CAMERA_ALT_CHECK_ERROR", {"error": str(e)})
            
            time.sleep(self._altitude_check_interval)

    def _trigger_altitude_video(self, target_altitude, actual_altitude):
        """Capture video at altitude milestone"""
        log_event("CAMERA_VIDEO_TRIGGERED", {
            "target_alt_ft": target_altitude,
            "actual_alt_ft": actual_altitude
        })
        
        # Mark as captured BEFORE attempting (prevent retry spam)
        self._videos_captured[target_altitude] = True
        
        # Acquire camera lock (will block photo capture)
        with self._camera_lock:
            try:
                self._capture_video(
                    duration_seconds=15,
                    filename_prefix=f"alt_{target_altitude}ft"
                )
                log_event("CAMERA_VIDEO_SUCCESS", {
                    "altitude": target_altitude
                })
                
            except Exception as e:
                log_event("CAMERA_VIDEO_ERROR", {
                    "altitude": target_altitude,
                    "error": str(e)
                })
                # Don't retry - flag is already set, move on

    def _capture(self):
        """Capture full-res photo and generate optimized thumbnail"""
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        full_path  = f"{FULL_DIR}/img_{ts}.jpg"
        thumb_path_temp = f"{THUMB_DIR}/img_{ts}_thumb_temp.jpg"
        thumb_path = f"{THUMB_DIR}/img_{ts}_thumb.jpg"

        # ---- full-res capture ----
        cmd_full = [
            "rpicam-still",
            "--width",  "4656",
            "--height", "3496",
            "-o",      full_path,
            "--nopreview",
            "--timeout", "1000"  # 1 second timeout
        ]

        result = subprocess.run(cmd_full, capture_output=True, text=True)
        if result.returncode != 0:
            log_event("CAMERA_FULL_FAIL", {"stderr": result.stderr})
            return

        # ---- thumbnail generation ----
        cmd_thumb = [
            "rpicam-still",
            "--width",  "800",
            "--height", "600",
            "-o",      thumb_path_temp,
            "--nopreview",
            "--quality", "85",
            "--timeout", "1000"
        ]

        result = subprocess.run(cmd_thumb, capture_output=True, text=True)
        if result.returncode != 0:
            log_event("CAMERA_THUMB_FAIL", {"stderr": result.stderr})
            return
        
        # ---- Optimize thumbnail with PIL ----
        try:
            img = Image.open(thumb_path_temp)
            img.thumbnail((640, 480), Image.Resampling.LANCZOS)
            
            img.save(
                thumb_path,
                'JPEG',
                quality=75,
                optimize=True,
                progressive=True
            )
            
            original_size = os.path.getsize(thumb_path_temp)
            optimized_size = os.path.getsize(thumb_path)
            
            os.remove(thumb_path_temp)
            
            log_event("CAMERA_CAPTURE_OK", {
                "full": full_path,
                "thumb": thumb_path,
                "thumb_size_kb": round(optimized_size / 1024, 1),
                "compression_ratio": round(original_size / optimized_size, 2)
            })
            
        except Exception as e:
            log_event("CAMERA_OPTIMIZE_FAIL", {"error": str(e)})
            if os.path.exists(thumb_path_temp):
                os.rename(thumb_path_temp, thumb_path)
        
        # Cleanup old photos if needed
        deleted = cleanup_oldest_if_needed()
        if deleted:
            log_event("CAMERA_STORAGE_PURGE", {"deleted": deleted})

    def _capture_video(self, duration_seconds=15, filename_prefix="video"):
        """
        Capture video for specified duration.
        Camera lock should be held before calling this.
        """
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        video_path = f"{VIDEO_DIR}/{filename_prefix}_{ts}.h264"
        
        log_event("CAMERA_VIDEO_START", {
            "duration_sec": duration_seconds,
            "path": video_path
        })
        
        # Capture video with rpicam-vid
        cmd = [
            "rpicam-vid",
            "-t", str(duration_seconds * 1000),  # milliseconds
            "-o", video_path,
            "--width", "1920",
            "--height", "1080",
            "--framerate", "30",
            "--nopreview"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            log_event("CAMERA_VIDEO_FAIL", {"stderr": result.stderr})
            return
        
        # Get file size
        if os.path.exists(video_path):
            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            log_event("CAMERA_VIDEO_OK", {
                "path": video_path,
                "size_mb": round(size_mb, 2),
                "duration_sec": duration_seconds
            })
        else:
            log_event("CAMERA_VIDEO_MISSING", {"expected_path": video_path})

    def manual_video(self, duration_seconds=15, filename_prefix="manual"):
        """
        Manually trigger a video capture (for testing or manual trigger).
        This will acquire the camera lock and block photo capture.
        """
        log_event("CAMERA_MANUAL_VIDEO_REQUESTED", {"duration": duration_seconds})
        
        with self._camera_lock:
            self._capture_video(duration_seconds, filename_prefix)