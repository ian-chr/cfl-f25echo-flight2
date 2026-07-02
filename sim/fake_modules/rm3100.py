"""Fake `rm3100` (RM3100 precision magnetometer driver).

telem_deps/sensors/mag.py calls start_single_reading() then sleeps
`measurement_time` before get_next_reading() -- both are honored here
(with a tiny, harmless real sleep) so timing-sensitive callers behave
the same as they would against real hardware.
"""
from sim.world import get_world


class RM3100_I2C:
    def __init__(self, i2c, i2c_address=0x20):
        self._world = get_world()
        self.i2c_address = i2c_address
        self.measurement_time = 0.01

    def start_single_reading(self):
        pass

    def get_next_reading(self):
        mx, my, mz = self._world.now().mag_ut
        # RM3100 is a separate physical sensor from the IMU's onboard mag;
        # offset slightly so the two never read bit-for-bit identical.
        return mx + 0.3, my - 0.2, mz + 0.1

    @property
    def magnetic(self):
        return self.get_next_reading()
