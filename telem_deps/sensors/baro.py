"""
baro.py
Reads temperature, pressure, humidity, and gas resistance from BME680.
Auto-detects 0x77 or 0x76.
"""

import board
import busio
import adafruit_bme680

# One-time detection cache
_bme = None
_bme_addr_attempted = False

def _init_bme():
    """
    Try to initialize BME680 at 0x77, then 0x76.
    Returns an adafruit_bme680.Adafruit_BME680_I2C instance or None.
    """
    global _bme, _bme_addr_attempted
    if _bme is not None:
        return _bme  # already initialized

    if _bme_addr_attempted:
        return None  # already failed

    _bme_addr_attempted = True

    try:
        i2c = busio.I2C(board.SCL, board.SDA)

        # Try 0x77 first (expected address)
        try:
            _bme = adafruit_bme680.Adafruit_BME680_I2C(i2c, address=0x77)
            print("[BARO] BME680 detected at 0x77")
            return _bme
        except Exception:
            pass

        # Try 0x76 second (common alternate address)
        try:
            _bme = adafruit_bme680.Adafruit_BME680_I2C(i2c, address=0x76)
            print("[BARO] BME680 detected at 0x76")
            return _bme
        except Exception:
            pass

        print("[BARO] No BME680 found on 0x76 or 0x77")
        return None

    except Exception as e:
        print(f"[BARO] I2C Init failed: {e}")
        return None


def read_baro_data():
    """
    Reads BME680 environmental data.

    Returns:
        (temperature_C, pressure_hPa, humidity_pct, gas_ohms)
    """
    global _bme
    if _bme is None:
        _bme = _init_bme()
        if _bme is None:
            return None, None, None, None

    try:
        temperature = _bme.temperature
        pressure = _bme.pressure
        humidity = _bme.humidity
        gas = _bme.gas
        return temperature, pressure, humidity, gas
    except Exception as e:
        print(f"[BARO] Read failed: {e}")
        return None, None, None, None