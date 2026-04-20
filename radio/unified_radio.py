#!/usr/bin/env python3
"""
StratoSat ECHO – Unified Radio Service
--------------------------------------
Single process that:

  • Sends periodic CFL telemetry beacons
  • Listens for CAP commands from GS
  • Sends ACKs
  • Downlinks images as DAP packets

Owns the *only* RFM9x instance so there is no SPI / radio contention.
Supports systemd watchdog.
"""

import os
import time
from datetime import datetime

import board
import busio
from digitalio import DigitalInOut
import adafruit_rfm9x

from systemd import daemon

from lora_gnu_radio.CFLTXRX.encode import encode_rap
from lora_gnu_radio.CFLTXRX.decode import decode_rap
from lora_gnu_radio.CFLTXRX.telemetry_beacon import build_beacon

# ============================================================
# CONSTANTS / CONFIG
# ============================================================
LORA_FREQ_MHZ = 437.45

# CFL flags
BEACON_FLAG = 0x03
CAP_FLAG    = 0x02
DAP_FLAG    = 0x01
ACK_FLAG    = 0x05

CMD_DOWNLOAD_DAP = 1

# Beacon config
BEACON_PERIOD = 5.0   # seconds

# DAP / image config
MAX_DAP_DATA = 200     # chunk size in bytes (must fit RAP+DAP)
THUMB_DIR = "/home/f25-echo2/echo_fsw/data/camera/photos/thumb"
FULL_DIR = "/home/f25-echo2/echo_fsw/data/camera/photos/full"


# ============================================================
# UTILITIES
# ============================================================
def get_latest_thumb():
    """Return full path to latest *_thumb.jpg file, or None."""
    try:
        files = sorted(
            [f for f in os.listdir(THUMB_DIR) if f.endswith("_thumb.jpg")],
            reverse=True,
        )
    except FileNotFoundError:
        print(f"[RADIO] Thumbnail directory not found: {THUMB_DIR}")
        return None

    if not files:
        return None

    return os.path.join(THUMB_DIR, files[0])


def load_file_bytes(path):
    """Load JPEG bytes and trim any junk after FFD9."""
    d = open(path, "rb").read()
    end = d.rfind(b"\xff\xd9")
    if end != -1:
        d = d[: end + 2]
    return d


def build_ack(pid, cmd, refnum):
    """
    ACK packet structure:
        pid (1 byte) + cmd (2 bytes LE) + refnum (2 bytes LE)
    Total: 5 bytes
    """
    return (
        pid.to_bytes(1, "little")
        + cmd.to_bytes(2, "little")
        + refnum.to_bytes(2, "little")
    )


def build_dap_packet(pid, file_num, part, total, chunk):
    """
    DAP packet structure (NO CHECKSUM - matching ground station):

        pid:      1 byte
        length:   2 bytes LE (9 + len(chunk))
        file_num: 2 bytes LE
        part:     2 bytes LE
        total:    2 bytes LE
        data:     binary chunk
    """
    part_length = 9 + len(chunk)

    dap_packet = (
        pid.to_bytes(1, "little")
        + part_length.to_bytes(2, "little")
        + file_num.to_bytes(2, "little")
        + part.to_bytes(2, "little")
        + total.to_bytes(2, "little")
        + chunk
    )

    if part < 3:
        print(
            f"[DEBUG] DAP packet {part}: pid={pid}, len={part_length}, "
            f"file={file_num}, part={part}/{total-1}, chunk_size={len(chunk)}"
        )
        print(f"[DEBUG]   Total DAP size: {len(dap_packet)} bytes")
        print(
            f"[DEBUG]   First 16 bytes of chunk: "
            f"{chunk[:16].hex() if len(chunk) >= 16 else chunk.hex()}"
        )

    if part == total - 1:
        print(
            f"[DEBUG] Last part ({part}) chunk end: "
            f"{chunk[-16:].hex() if len(chunk) >= 16 else chunk.hex()}"
        )

    return dap_packet


# ============================================================
# RADIO INIT
# ============================================================
def init_radio():
    CS = DigitalInOut(board.CE1)
    RESET = DigitalInOut(board.D25)
    spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

    rfm9x = adafruit_rfm9x.RFM9x(spi, CS, RESET, LORA_FREQ_MHZ)
    rfm9x.tx_power = 23

    print(f"[RADIO] LoRa initialized @ {LORA_FREQ_MHZ} MHz")
    print("[RADIO] Using default LoRa parameters")
    if hasattr(rfm9x, "spreading_factor"):
        print(f"[RADIO]   SF: {rfm9x.spreading_factor}")
    if hasattr(rfm9x, "signal_bandwidth"):
        print(f"[RADIO]   BW: {rfm9x.signal_bandwidth}")
    if hasattr(rfm9x, "coding_rate"):
        print(f"[RADIO]   CR: {rfm9x.coding_rate}")

    return rfm9x


# ============================================================
# BEACON LOGIC
# ============================================================
def maybe_send_beacon(rfm9x, last_beacon_time, beacon_count):
    """Send a beacon if BEACON_PERIOD has elapsed."""
    now = time.time()
    if now - last_beacon_time < BEACON_PERIOD:
        return last_beacon_time, beacon_count

    try:
        beacon = build_beacon()  # 105-byte CFL beacon
        if len(beacon) != 105:
            print(f"[WARN] Beacon size {len(beacon)} != 105 bytes")

        rap_packet = encode_rap(BEACON_FLAG, beacon)
        
        print("[DEBUG] RAP Beacon Packet:",
            rap_packet.hex(),
            "len=", len(rap_packet))

        rfm9x.send(rap_packet)
        print(f"[BEACON] Sent #{beacon_count} ({len(rap_packet)} bytes)")
        beacon_count += 1
    except Exception as e:
        print(f"[ERR] Beacon send failed: {e}")

    return now, beacon_count


# ============================================================
# IMAGE DOWNLINK LOGIC
# ============================================================
def handle_cap_and_downlink(rfm9x, rap_data, pid, sid, flag):
    """
    Handle one CAP (download DAP) command from GS:
      • parse CAP
      • send ACK
      • send requested DAP packets
    """
    # Decode CAP payload
    try:
        cap_pid = rap_data[0]
        length = int.from_bytes(rap_data[1:3], "little")
        refnum = int.from_bytes(rap_data[3:5], "little")
        ex_time = int.from_bytes(rap_data[5:9], "little")
        cmd = int.from_bytes(rap_data[9:11], "little")
        args = rap_data[11 : length - 2]  # strip checksum
    except Exception as e:
        print(f"[RADIO] CAP parse error: {e}")
        return

    print(f"[RADIO] Received CAP cmd={cmd}, ref={refnum}, args={args}")

    if cmd != CMD_DOWNLOAD_DAP:
        print("[RADIO] Unknown CMD – ignoring")
        return

    # Parse arguments: "filename,ALL" or "filename,3,8,9"
    try:
        arg_str = args.decode("utf-8")
        parts = arg_str.split(",")
        requested_fname = parts[0]
        part_list = ",".join(parts[1:])
    except Exception as e:
        print(f"[RADIO] Failed to parse args: {e}")
        return

    # Always downlink the latest thumbnail. FULL-res downlinks were removed
    # because at MAX_DAP_DATA=200 bytes/chunk and the configured inter-packet
    # delay, a single 3 MB full-res image would take ~2 hours of airtime. The
    # thumbnail is now 1024x768 @ Q85 (~150-250 KB), which is a reasonable
    # downlink payload that still gives the ground station good imagery.
    if "FULL" in arg_str.upper():
        print("[RADIO] GS requested FULL; FULL downlink is disabled in this "
              "build. Sending thumbnail instead.")

    latest = get_latest_thumb()

    if latest is None:
        print("[RADIO] No images available to downlink!")
        return

    print(
        f"[RADIO] GS requested '{requested_fname}' → sending thumbnail: {latest}"
    )

    try:
        data = load_file_bytes(latest)
        print(f"[DEBUG] File loaded, size: {len(data)} bytes")
        print(f"[DEBUG] First 32 bytes: {data[:32].hex()}")
        print(f"[DEBUG] Bytes 200–232: {data[200:232].hex()}")
        print(f"[DEBUG] Last 32 bytes: {data[-32:].hex()}")

        if data[:2] != b"\xff\xd8":
            print("[RADIO] WARNING: no JPEG SOI marker (FFD8) at start")
        if data[-2:] != b"\xff\xd9":
            print("[RADIO] WARNING: no JPEG EOI marker (FFD9) at end")
    except Exception as e:
        print(f"[RADIO] Failed to load image: {e}")
        return

    # Chop into chunks
    chunks = [data[i : i + MAX_DAP_DATA] for i in range(0, len(data), MAX_DAP_DATA)]
    total_parts = len(chunks)

    # Determine which parts to send
    clean_parts = [
        p.strip()
        for p in part_list.split(",")
        if p.strip() and p.strip().upper() != "FULL"
    ]

    # ALL → send all parts
    if "ALL" in (p.upper() for p in clean_parts):
        request_parts = list(range(total_parts))
    else:
        try:
            request_parts = [int(p) for p in clean_parts]
        except Exception as e:
            print(f"[RADIO] Failed to parse part list: {e}")
            return

    print(f"[RADIO] Image size: {len(data)} bytes, {total_parts} parts")
    print(
        f"[RADIO] Sending {len(request_parts)} parts: "
        f"{request_parts[:10]}{'...' if len(request_parts) > 10 else ''}"
    )

    # Send ACK
    # ---------------------------------------------
    try:
        # CFL ACK payload:
        #   pid (1 byte)   = 1 (FCPU)
        #   cmd (2 bytes)  = 1 (DOWNLOAD_DAP_FILES)
        #   refnum (2B LE) = CAP ref
        ack = build_ack(pid=1, cmd=CMD_DOWNLOAD_DAP, refnum=refnum)

        # Wrap in RAP – NO extra kwargs, encode_rap ONLY takes (flag, data)
        rap_ack = encode_rap(ACK_FLAG, ack)

        rfm9x.send(rap_ack)
        print("[RADIO] ACK sent")
    except Exception as e:
        print(f"[RADIO] Failed to send ACK: {e}")
        return

    time.sleep(0.1)  # give GS time to process ACK

    # Send DAP packets
    sent_count = 0
    for part in request_parts:
        # Keep watchdog happy during long downlink
        daemon.notify("WATCHDOG=1")

        if part >= total_parts:
            print(f"[RADIO] Skipping invalid part {part} (max: {total_parts - 1})")
            continue

        try:
            chunk = chunks[part]
            dap = build_dap_packet(
                pid=1,
                file_num=0,
                part=part,
                total=total_parts,
                chunk=chunk,
            )
            rap_dap = encode_rap(DAP_FLAG, dap)
            rfm9x.send(rap_dap)
            sent_count += 1

            if sent_count % 10 == 0:
                print(
                    f"[RADIO] Sent {sent_count}/{len(request_parts)} DAP packets..."
                )

            time.sleep(0.05)  # 50 ms between packets (per ICD)
        except Exception as e:
            print(f"[RADIO] Failed sending DAP part {part}: {e}")

    print(f"[RADIO] ✓ Finished sending {sent_count} DAP packets")
    print(f"[RADIO] Expected file size on GS: {len(data)} bytes\n")


# ============================================================
# MAIN LOOP
# ============================================================
def main():
    print("[RADIO] Unified radio service starting...")
    rfm9x = init_radio()

    beacon_count = 0
    last_beacon_time = time.time()

    # Tell systemd we're ready
    daemon.notify("READY=1")

    while True:
        try:
            # Pet the watchdog each loop
            daemon.notify("WATCHDOG=1")

            # -------------------------------------------------
            # 1) Maybe send a beacon (if not in the middle of
            #    a CAP / downlink).
            #    Beacons + DAPs interleaving is OK per ICD.
            # -------------------------------------------------
            last_beacon_time, beacon_count = maybe_send_beacon(
                rfm9x, last_beacon_time, beacon_count
            )

            # -------------------------------------------------
            # 2) Listen for packets from GS
            # -------------------------------------------------
            packet = rfm9x.receive(timeout=0.2)
            if packet is None:
                continue

            # Ignore obviously empty packets
            if all(b == 0x00 for b in packet):
                # This was causing 'sync characters not found ... 000000'
                # inside decode_rap previously.
                continue

            try:
                rap, rap_data, pid, sid, flag = decode_rap(packet)
                print(f"[DEBUG] Received RAP: sid={sid}, pid={pid}, flag={flag}, len={len(rap_data)}")
            except Exception as e:
                print(f"[RADIO] RAP decode failed: {e}, raw=", packet.hex())
                continue
            # We only care about CAP (download file command)
            if flag != CAP_FLAG:
                # Could add debug print here if needed
                continue

            # Handle the CAP and downlink the image
            handle_cap_and_downlink(rfm9x, rap_data, pid, sid, flag)

        except Exception as e:
            print(f"[ERR] Main loop error: {e}")
            time.sleep(1.0)  # avoid crash loop


if __name__ == "__main__":
    main()