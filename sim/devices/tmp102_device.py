"""Virtual TMP102 (and pin-compatible TMP1022) temperature sensor.

Encodes a Celsius value into the exact 12-bit-left-justified, two's
complement register format that telem_deps/sensors/tmp102.py decodes:

    raw = ((msb << 8) | lsb) >> 4
    if raw & 0x800: raw -= 1 << 12
    return raw * 0.0625
"""
from __future__ import annotations


class TMP102VirtualDevice:
    def __init__(self, world, offset_c: float = 0.0):
        self.world = world
        self.offset_c = offset_c

    def read_i2c_block_data(self, reg: int, length: int):
        temp_c = self.world.now().temp_c + self.offset_c
        raw12 = int(round(temp_c / 0.0625))
        raw12 = max(-2048, min(2047, raw12))
        if raw12 < 0:
            raw12 += 1 << 12
        word16 = (raw12 & 0xFFF) << 4
        msb = (word16 >> 8) & 0xFF
        lsb = word16 & 0xFF
        return [msb, lsb][:max(0, length)]
