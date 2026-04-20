# Robust RV-8803 RTC reader with retry logic
import time
import smbus

RV8803_ADDR = 0x32

# Register addresses
REG_SEC   = 0x00
REG_MIN   = 0x01
REG_HOUR  = 0x02
REG_DATE  = 0x03
REG_MONTH = 0x04
REG_YEAR  = 0x05

class RTCTime:
    """Simple container for datetime-like formatting."""
    def __init__(self, y, mo, d, h, mi, s):
        self.year = y
        self.month = mo
        self.day = d
        self.hour = h
        self.minute = mi
        self.second = s

    def format(self):
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}T{self.hour:02d}:{self.minute:02d}:{self.second:02d}"

def _bcd_to_int(v):
    return (v >> 4) * 10 + (v & 0x0F)

def read_rtc_time(bus_num=1, max_retries=4, retry_delay=0.01):
    """
    Read RV8803 time with retry logic to avoid 'device busy'
    errors when the RTC is being written simultaneously.
    """
    bus = None
    try:
        bus = smbus.SMBus(bus_num)
    except Exception:
        return None

    for attempt in range(max_retries):
        try:
            raw = bus.read_i2c_block_data(RV8803_ADDR, 0x00, 7)

            sec   = _bcd_to_int(raw[REG_SEC]   & 0x7F)
            minute = _bcd_to_int(raw[REG_MIN]  & 0x7F)
            hour   = _bcd_to_int(raw[REG_HOUR] & 0x3F)
            day    = _bcd_to_int(raw[REG_DATE] & 0x3F)
            month  = _bcd_to_int(raw[REG_MONTH] & 0x1F)
            year   = 2000 + _bcd_to_int(raw[REG_YEAR])

            return RTCTime(year, month, day, hour, minute, sec)

        except OSError as e:
            if e.errno == 16:     # Device or resource busy
                time.sleep(retry_delay)
                continue
            return None

        except Exception:
            return None

    # Failed even after retries
    return None