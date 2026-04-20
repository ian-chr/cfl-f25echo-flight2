# radio/protocol.py
"""
Minimal CAP, ACK, DAP encode/decode routines for the flight computer.
We do NOT import cap.py or dap.py (GS only).
"""

from typing import Tuple


# ------------------------------
# Fletcher-16 checksum
# ------------------------------
def fletcher16(data: bytes) -> bytes:
    sum1 = 0xff
    sum2 = 0xff

    i = 0
    length = len(data)
    while length:
        tlen = min(length, 20)
        length -= tlen
        while tlen:
            sum1 = (sum1 + data[i]) & 0xffff
            sum2 = (sum2 + sum1) & 0xffff
            i += 1
            tlen -= 1

        sum1 = (sum1 & 0xff) + (sum1 >> 8)
        sum2 = (sum2 & 0xff) + (sum2 >> 8)

    sum1 = (sum1 & 0xff) + (sum1 >> 8)
    sum2 = (sum2 & 0xff) + (sum2 >> 8)

    return bytes([sum1 & 0xff, sum2 & 0xff])


# ============================================================
#             CAP PACKET FORMAT (flight-side)
# ============================================================

# All following matches the slide deck and RAX docs

def decode_cap(cap_bytes: bytes) -> Tuple[int, int, int, int, int, bytes]:
    """
    Decode a CAP packet from RAP data.
    Format:
      PID          1 byte
      LENGTH       2 bytes LE
      REF_NUM      2 bytes LE
      EX_TIME      4 bytes LE
      CMD          2 bytes LE
      ARGS         variable
      CHECKSUM     2 bytes
    """
    pid = cap_bytes[0]
    length = int.from_bytes(cap_bytes[1:3], "little")
    ref_num = int.from_bytes(cap_bytes[3:5], "little")
    ex_time = int.from_bytes(cap_bytes[5:9], "little")
    cmd = int.from_bytes(cap_bytes[9:11], "little")

    args_end = length - 2
    args = cap_bytes[11:args_end]

    recv_checksum = cap_bytes[args_end:length]
    calc_checksum = fletcher16(cap_bytes[:args_end])

    if recv_checksum != calc_checksum:
        raise ValueError("CAP checksum mismatch")

    return pid, length, ref_num, ex_time, cmd, args


def encode_ack(pid: int, cmd: int, ref_num: int) -> bytes:
    """
    ACK format:
      PID (1)
      CMD (2 LE)
      REF_NUM (2 LE)
    """
    return (
        pid.to_bytes(1, "little") +
        cmd.to_bytes(2, "little") +
        ref_num.to_bytes(2, "little")
    )


# ============================================================
#             DAP PACKETS (flight-side)
# ============================================================

def encode_dap(pid: int,
               file_num: int,
               file_part: int,
               total_parts: int,
               data: bytes) -> bytes:
    """
    DAP Format:
      PID           1 byte
      LENGTH        2 bytes LE
      FILE_NUM      2 bytes LE
      FILE_PART     2 bytes LE
      TOTAL_PARTS   2 bytes LE
      DATA          variable
      CHECKSUM      2 bytes
    """

    header = (
        pid.to_bytes(1, "little") +
        (0).to_bytes(2, "little") +   # placeholder length
        file_num.to_bytes(2, "little") +
        file_part.to_bytes(2, "little") +
        total_parts.to_bytes(2, "little")
    )

    core = header + data
    checksum = fletcher16(core)

    total_length = len(core) + 2

    # rebuild with correct length
    packet = (
        pid.to_bytes(1, "little") +
        total_length.to_bytes(2, "little") +
        file_num.to_bytes(2, "little") +
        file_part.to_bytes(2, "little") +
        total_parts.to_bytes(2, "little") +
        data +
        checksum
    )

    return packet