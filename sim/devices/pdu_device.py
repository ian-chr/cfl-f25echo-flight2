"""Virtual ADS7828 8-channel ADC, as wired on the Eddy PDU (Rev A).

telem_deps/sensors/eddy_pdu.py reads channel words and applies voltage
dividers, shunt-resistor current scaling, and an LM20 temperature
formula to recover physical rail values. This device does the exact
algebraic inverse of the *current* (post-bugfix) scaling in that file,
so a healthy decode round-trips back to plausible battery/rail
telemetry sourced from the shared flight world.

Two instances are registered: chip "0" at 0x48 (channels 0-7) and chip
"1" at 0x49 (only channels 5/6 are wired there per the schematic).
"""
from __future__ import annotations

import hashlib


def _swap16(x: int) -> int:
    return ((x & 0xFF) << 8) | ((x >> 8) & 0xFF)


def _volts_to_counts(v: float) -> int:
    counts = int(round(v * 4095.0 / 2.5))
    return max(0, min(4095, counts))


def _jitter(mission_t: float, salt: int, amplitude: float) -> float:
    h = int(hashlib.md5(f"{round(mission_t, 2)}:{salt}".encode()).hexdigest()[:8], 16)
    return ((h / 0xFFFFFFFF) * 2.0 - 1.0) * amplitude


class ADS7828VirtualDevice:
    def __init__(self, world, chip: str):
        self.world = world
        self.chip = chip  # "0" (0x48) or "1" (0x49)

    def _channel_volts(self, channel: int) -> float:
        s = self.world.now()
        vbatt_raw = s.batt_voltage
        ibatt_raw = max(0.0, s.batt_current)
        t = s.mission_t

        if self.chip == "0":
            targets = {
                0: (ibatt_raw * 0.35) * (0.012 * 100.0),                       # I_5V0
                1: (5.05 + _jitter(t, 1, 0.02)) / ((100_000 + 33_200) / 33_200),  # V_5V0
                2: (ibatt_raw * 0.45) * (0.050 * 100.0),                       # I_3V3
                3: (3.30 + _jitter(t, 3, 0.02)) / ((100_000 + 10_000) / 10_000),  # V_3V3
                4: 1.866 - (32.0 + _jitter(t, 4, 1.0)) * 0.01169,              # REGT_5V0
                5: vbatt_raw / ((100_000 + 12_000) / 12_000),                  # V_RAW_BATT
                6: ibatt_raw * (0.012 * 100.0),                                # I_RAW_BATT
                7: 1.866 - (30.0 + _jitter(t, 7, 1.0)) * 0.01169,              # REGT_3V3
            }
        else:
            targets = {
                5: vbatt_raw / ((100_000 + 12_000) / 12_000),                 # V_BATT
                6: (ibatt_raw * 0.6) * (0.012 * 100.0),                        # I_BATT
            }
        return targets.get(channel, 0.0)

    def read_word_data(self, cmd: int) -> int:
        channel = (cmd >> 4) & 0x07
        v = self._channel_volts(channel)
        counts = _volts_to_counts(v)
        return _swap16(counts)
