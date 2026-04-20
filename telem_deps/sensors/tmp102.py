def read_tmp102_c(bus, addr: int, temp_reg: int):
    try:
        data = bus.read_i2c_block_data(addr, temp_reg, 2)
        msb, lsb = data[0], data[1]
        raw = ((msb << 8) | lsb) >> 4
        if raw & 0x800:
            raw -= 1 << 12
        return raw * 0.0625
    except Exception:
        return None