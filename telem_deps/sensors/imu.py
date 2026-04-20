"""
imu.py
Reads 9-axis IMU data (ICM-20948) over I2C.
Returns acceleration, gyroscope, and magnetometer tuples.
"""

import board
import busio
from adafruit_icm20x import ICM20948, AccelRange, GyroRange

_imu = None

def read_imu_data():
    global _imu
    try:
        if _imu is None:
            i2c = busio.I2C(board.SCL, board.SDA)
            _imu = ICM20948(i2c, address=0x69)
            
            # -----------------------------------------------------------
            # CONFIGURATION: DESATURATE SENSORS
            # -----------------------------------------------------------
            # Set Accelerometer to max range (+/- 16G) for rocketry
            _imu.accelerometer_range = AccelRange.RANGE_16G
            
            # Set Gyro to max range (+/- 2000 DPS) for high spin
            _imu.gyro_range = GyroRange.RANGE_2000_DPS
            
            # Optional: Set data rate higher if needed
            # _imu.accelerometer_data_rate = ...

        accel = _imu.acceleration
        gyro  = _imu.gyro
        mag   = _imu.magnetic
        
        return accel, gyro, mag
        
    except Exception as e:
        # Only print once per second or so to avoid spamming logs? 
        # For now, keep simple print
        print(f"[IMU] Read failed: {e}")
        return (None, None, None), (None, None, None), (None, None, None)