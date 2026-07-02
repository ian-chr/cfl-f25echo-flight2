"""Fake `board` module (Blinka stand-in).

Importable in place of the real Adafruit Blinka `board` package so that
telem_deps/sensors/imu.py, baro.py, mag.py and radio/unified_radio.py can
be imported and run completely unmodified off real Raspberry Pi
hardware. The actual pin identities don't matter in sim -- every
consumer of these constants only ever passes them through to another
fake module (busio/digitalio) that ignores them.
"""


class _Pin:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<sim.board.Pin {self.name}>"


SCL = _Pin("SCL")
SDA = _Pin("SDA")
CE0 = _Pin("CE0")
CE1 = _Pin("CE1")
D25 = _Pin("D25")
SCK = _Pin("SCK")
MOSI = _Pin("MOSI")
MISO = _Pin("MISO")
