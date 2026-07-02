"""Fake `adafruit_icm20x` (ICM-20948 9-axis IMU driver).

telem_deps/sensors/imu.py only touches `.acceleration` (m/s^2),
`.gyro` (deg/s), `.magnetic` (uT), and assigns `.accelerometer_range` /
`.gyro_range`. Values come straight from the shared flight world.
"""
from sim.world import get_world


class AccelRange:
    RANGE_2G = 0
    RANGE_4G = 1
    RANGE_8G = 2
    RANGE_16G = 3


class GyroRange:
    RANGE_250_DPS = 0
    RANGE_500_DPS = 1
    RANGE_1000_DPS = 2
    RANGE_2000_DPS = 3


class ICM20948:
    def __init__(self, i2c, address=0x69):
        self._world = get_world()
        self.address = address
        self._accel_range = AccelRange.RANGE_16G
        self._gyro_range = GyroRange.RANGE_2000_DPS

    @property
    def accelerometer_range(self):
        return self._accel_range

    @accelerometer_range.setter
    def accelerometer_range(self, v):
        self._accel_range = v

    @property
    def gyro_range(self):
        return self._gyro_range

    @gyro_range.setter
    def gyro_range(self, v):
        self._gyro_range = v

    @property
    def acceleration(self):
        return self._world.now().accel_body_mps2

    @property
    def gyro(self):
        return self._world.now().gyro_dps

    @property
    def magnetic(self):
        return self._world.now().mag_ut
