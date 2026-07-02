"""Fake `digitalio` module (Blinka stand-in). Only used to construct the
RFM9x CS/RESET pins in radio/unified_radio.py; our fake adafruit_rfm9x
never actually toggles them, so this just has to not blow up."""


class Direction:
    INPUT = "input"
    OUTPUT = "output"


class Pull:
    UP = "up"
    DOWN = "down"


class DigitalInOut:
    def __init__(self, pin):
        self.pin = pin
        self.direction = Direction.INPUT
        self._value = False
        self.pull = None

    def switch_to_output(self, value=False, **kwargs):
        self.direction = Direction.OUTPUT
        self._value = value

    def switch_to_input(self, pull=None, **kwargs):
        self.direction = Direction.INPUT
        self.pull = pull

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = v

    def deinit(self):
        pass
