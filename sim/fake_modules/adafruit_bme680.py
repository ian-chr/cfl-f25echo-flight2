"""Fake `adafruit_bme680` (BME680 environmental sensor driver).

telem_deps/sensors/baro.py tries address 0x77 first, then 0x76 -- both
constructors succeed here so the 0x77 branch is always taken, matching
the documented expected wiring.
"""
from sim.world import get_world


class Adafruit_BME680_I2C:
    def __init__(self, i2c, address=0x77, debug=False):
        self._world = get_world()
        self.address = address
        self.sea_level_pressure = 1013.25

    @property
    def temperature(self):
        return self._world.now().temp_c

    @property
    def pressure(self):
        return self._world.now().pressure_hpa

    @property
    def humidity(self):
        return self._world.now().humidity_pct

    @property
    def gas(self):
        return self._world.now().gas_ohms
