# radio/constants.py

# RAP flags (from the presentation)
RAP_FLAG_TAP   = 0x00
RAP_FLAG_DAP   = 0x01
RAP_FLAG_CAP   = 0x02
RAP_FLAG_BEACON= 0x03
RAP_FLAG_EAP   = 0x04
RAP_FLAG_ACK   = 0x05

# ==== MISSION-SPECIFIC COMMAND CODES (FILL FROM YOUR UPLINK ICD) ====
# The values here are placeholders. Replace with the real CMD codes.

# Request: downlink latest thumbnail image (all parts)
CMD_REQ_LATEST_THUMB_ALL = 0x10   # TODO: replace with ICD value

# Request: downlink specific thumbnail by filename (ARGS = UTF-8 bytes)
CMD_REQ_THUMB_BY_NAME    = 0x11   # TODO

# Request: downlink specific parts of a file
# ARGS example: [file_num (u16 LE), num_ranges (u8), then (start_part,u16 LE, end_part,u16 LE)*]
CMD_REQ_THUMB_PARTS      = 0x12   # TODO

# You can add more commands as needed:
# - change beacon period
# - pause camera, etc.

# === DAP settings ===

# Max DATA bytes *inside* RAP (from slides)
MAX_RAP_DATA_BYTES = 235

# DAP header inside DATA: PID(2) + LENGTH(2) + FILE_NUM(2) + FILE_PART(2) + TOTAL_PARTS(2) = 10 bytes
DAP_HEADER_BYTES = 10

# We choose a safe chunk size for the file payload:
MAX_DAP_CHUNK = MAX_RAP_DATA_BYTES - DAP_HEADER_BYTES  # 225, but feel free to lower if nervous

# Base where camera thumbs live (matches your current system)
THUMB_DIR = "/home/f25-echo/echo_fsw/data/camera/photos/thumb"

# Optional: fixed file number for “latest thumbnail” request
FILE_NUM_LATEST_THUMB = 1