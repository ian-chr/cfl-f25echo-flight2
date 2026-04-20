#!/usr/bin/env python3
"""
Eddy PDU Reader (Rev A)
Reads both ADS7828 ADCs at 0x48 and 0x49 and computes:
- Batt Raw Voltage / Current
- 3v3 Voltage / Current
- 5v0 Voltage / Current
- VBATT Voltage / Current
- Regulator temperatures (3v3 / 5v0)
"""

from smbus2 import SMBus

# Addresses
ADDR_0 = 0x48   # ADC_0 → ADC_7
ADDR_1 = 0x49   # ADC_8 → ADC_15

BASE_CMD = 0b10001100  # SD=1, PD=11

# --------------------------------------------------------------------
# Channel mapping per Rev A schematic
# --------------------------------------------------------------------
ADC_I_5V0       = 0   # ADC_0  @0x48
ADC_V_5V0       = 1   # ADC_1  @0x48
ADC_I_3V3       = 2   # ADC_2  @0x48
ADC_V_3V3       = 3   # ADC_3  @0x48
ADC_REGT_5V0    = 4   # ADC_4  @0x48
ADC_V_RAW_BATT  = 5   # ADC_5  @0x48
ADC_I_RAW_BATT  = 6   # ADC_6  @0x48
ADC_REGT_3V3    = 7   # ADC_7  @0x48

ADC_V_BATT      = 13  # ADC_13 @0x49
ADC_I_BATT      = 14  # ADC_14 @0x49

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _ads_read(bus, addr, channel):
    cmd = BASE_CMD | (channel << 4)
    raw = bus.read_word_data(addr, cmd)
    raw = ((raw & 0xFF) << 8) | (raw >> 8)
    v = raw * (2.5 / 4095.0)
    return raw, v

def _lm20_temp_c(v):
    # LM20: Vout = 1.866V – 11.69mV/°C·T
    # Solve T = (1.866 - Vout) / 0.01169
    return (1.866 - v) / 0.01169

def _divider(v_adc, r1, r2):
    return v_adc * (r1 + r2) / r2

def _current(v_sense, r_sense, gain):
    return v_sense / (r_sense * gain)

# --------------------------------------------------------------------
# Main reader
# --------------------------------------------------------------------
def read_eddy_pdu(bus: SMBus):
    adc = {}

    # Read all relevant channels
    def read(ch):
        if ch < 8:
            return _ads_read(bus, ADDR_0, ch)
        else:
            return _ads_read(bus, ADDR_1, ch - 8)

    # Raw reads
    _, v_i_5v0     = read(ADC_I_5V0)
    _, v_v_5v0     = read(ADC_V_5V0)
    _, v_i_3v3     = read(ADC_I_3V3)
    _, v_v_3v3     = read(ADC_V_3V3)
    _, v_regt_5v0  = read(ADC_REGT_5V0)
    _, v_vbraw     = read(ADC_V_RAW_BATT)
    _, v_ibraw     = read(ADC_I_RAW_BATT)
    _, v_regt_3v3  = read(ADC_REGT_3V3)
    _, v_vbatt     = read(ADC_V_BATT)
    _, v_ibatt     = read(ADC_I_BATT)

    # Scale voltages (100k / 12k divider)
    batt_raw_v = _divider(v_vbraw, 100_000, 12_000)
    batt_v     = _divider(v_vbatt, 100_000, 12_000)

    # Regulated rails
    # FIX: 3V3 Divider is likely 100k / 10k (Ratio ~11), not 100k/60.4k
    v3v3_v = _divider(v_v_3v3, 100_000, 10_000)
    v5v0_v = _divider(v_v_5v0, 100_000, 33_200)

    # Scale currents
    # 5V0: 12 mΩ, gain=100
    i_5v0 = _current(v_i_5v0, 0.012, 100)

    # FIX: 3V3 Shunt is 50 mΩ (0.050), not 5 mΩ (0.005)
    i_3v3 = _current(v_i_3v3, 0.050, 100)

    # Batt raw: 12 mΩ, gain=100
    ib_raw = _current(v_ibraw, 0.012, 100)

    # Batt regulated path
    i_batt = _current(v_ibatt, 0.012, 100)

    # Temperatures
    t_reg_3v3 = _lm20_temp_c(v_regt_3v3)
    t_reg_5v0 = _lm20_temp_c(v_regt_5v0)

    return {
        "VBATT_RAW_V": batt_raw_v,
        "IBATT_RAW_A": ib_raw,
        "V3V3_V": v3v3_v,
        "I3V3_A": i_3v3,
        "V5V0_V": v5v0_v,
        "I5V0_A": i_5v0,
        "VBATT_V": batt_v,
        "IBATT_A": i_batt,
        "REGT_3V3_C": t_reg_3v3,
        "REGT_5V0_C": t_reg_5v0,
    }