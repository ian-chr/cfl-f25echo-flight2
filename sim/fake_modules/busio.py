"""Fake `busio` module (Blinka stand-in).

`I2C(scl, sda)` hands back the same VirtualI2CBus everything else on
"bus 1" uses, wrapped to look like a Blinka I2C object (the
adafruit_bme680/adafruit_icm20x/rm3100 fakes only ever call
try_lock/unlock/writeto_then_readfrom, so that's all this needs).
`SPI(...)` is unused directly -- radio/unified_radio.py hands the SPI
object straight to our fake adafruit_rfm9x.RFM9x, which ignores it.
"""
from sim.devices.i2c_virtual_bus import get_bus


class I2C:
    def __init__(self, scl, sda, frequency=None):
        self._bus = get_bus(1)

    def try_lock(self):
        return True

    def unlock(self):
        pass

    def deinit(self):
        pass

    # Convenience passthroughs some drivers may use directly.
    def readfrom_into(self, address, buffer, *, start=0, end=None):
        end = len(buffer) if end is None else end
        data = self._bus.read_i2c_block_data(address, 0x00, end - start)
        for i, b in enumerate(data):
            buffer[start + i] = b

    def writeto(self, address, buffer, *, start=0, end=None):
        pass


class SPI:
    def __init__(self, clock, MOSI=None, MISO=None):
        self.clock = clock
        self.mosi = MOSI
        self.miso = MISO

    def try_lock(self):
        return True

    def unlock(self):
        pass

    def configure(self, **kwargs):
        pass
