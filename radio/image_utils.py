# radio/image_utils.py

import os
import math
from typing import List, Tuple

from lora_gnu_radio.CFLTXRX.encode import encode_rap
from lora_gnu_radio.CFLTXRX.decode import decode_rap  # might be handy later

from .constants import (
    RAP_FLAG_DAP,
    MAX_DAP_CHUNK,
)

# --------- helpers to find images ----------

def list_thumbs(thumb_dir: str) -> List[str]:
    """Return sorted list of thumbnail jpg paths (oldest -> newest)."""
    if not os.path.isdir(thumb_dir):
        return []
    files = [
        os.path.join(thumb_dir, f)
        for f in os.listdir(thumb_dir)
        if f.lower().endswith(".jpg")
    ]
    # sort by mtime
    files.sort(key=lambda p: os.path.getmtime(p))
    return files


def latest_thumb(thumb_dir: str) -> str | None:
    """Return newest thumbnail filename, or None if none exist."""
    files = list_thumbs(thumb_dir)
    return files[-1] if files else None


# --------- DAP building ----------

def build_dap_payload(
    file_num: int,
    part_index: int,
    total_parts: int,
    chunk: bytes,
    pid: int = 0,
) -> bytes:
    """
    Build the DATA field for a DAP packet, following:
        PID (2, LE)
        LENGTH (2, LE) = total bytes including this header (PID..DATA)
        FILE_NUM (2, LE)
        FILE_PART (2, LE)
        TOTAL_PARTS (2, LE)
        DATA (N bytes)

    Note: RAP header/footer & checksums are handled by encode_rap().
    """
    # bytes from FILE_NUM through TOTAL_PARTS + DATA:
    payload_meta_len = 2 + 2 + 2  # FILE_NUM, FILE_PART, TOTAL_PARTS
    total_len = 2 + 2 + payload_meta_len + len(chunk)  # PID + LENGTH + meta + data

    b = bytearray()
    b += int(pid).to_bytes(2, "little", signed=False)
    b += int(total_len).to_bytes(2, "little", signed=False)
    b += int(file_num).to_bytes(2, "little", signed=False)
    b += int(part_index).to_bytes(2, "little", signed=False)
    b += int(total_parts).to_bytes(2, "little", signed=False)
    b += chunk
    return bytes(b)


def chunk_file(path: str, max_chunk: int = MAX_DAP_CHUNK) -> List[bytes]:
    """Read entire file and split into <=max_chunk-sized chunks."""
    with open(path, "rb") as f:
        data = f.read()
    return [data[i:i + max_chunk] for i in range(0, len(data), max_chunk)]


def send_file_as_daps(
    rfm9x,
    file_path: str,
    file_num: int,
    rap_pid: int = 0,
    flag: int = RAP_FLAG_DAP,
    inter_packet_delay: float = 0.25,
) -> Tuple[int, int]:
    """
    Split `file_path` into chunks and send as DAP RAP packets over LoRa.

    Returns (num_parts, total_bytes_sent).
    """
    import time

    chunks = chunk_file(file_path)
    total_parts = len(chunks)
    total_bytes = 0

    print(f"[RADIO] Downlinking file '{file_path}' as DAPs "
          f"(file_num={file_num}, parts={total_parts})")

    for idx, chunk in enumerate(chunks):
        if len(chunk) > MAX_DAP_CHUNK:
            # should never happen
            raise RuntimeError("Chunk exceeds MAX_DAP_CHUNK")

        dap_data = build_dap_payload(
            file_num=file_num,
            part_index=idx,
            total_parts=total_parts,
            chunk=chunk,
            pid=rap_pid,
        )

        rap_packet = encode_rap(flag, dap_data)
        rfm9x.send(rap_packet)
        total_bytes += len(rap_packet)

        print(f"[RADIO] Sent DAP part {idx+1}/{total_parts} "
              f"({len(chunk)}B payload, {len(rap_packet)}B over RF)")
        time.sleep(inter_packet_delay)

    return total_parts, total_bytes