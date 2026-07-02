"""Fake `adafruit_rfm9x` (RFM9x LoRa transceiver driver).

radio/unified_radio.py only uses `.tx_power`, `.send(bytes)`, and
`.receive(timeout=...)` (plus `hasattr` checks on spreading_factor /
signal_bandwidth / coding_rate, which we simply don't define -- the
hasattr guards in unified_radio.py handle that gracefully). Packets go
out over the virtual RF medium instead of SPI.
"""
from sim.devices.radio_medium import RadioMedium


class RFM9x:
    def __init__(self, spi, cs, reset, frequency_mhz, *, baudrate=None, agc=False, crc=True):
        self.frequency_mhz = frequency_mhz
        self.tx_power = 13
        self.agc = agc
        self.crc = crc
        self._medium = RadioMedium(name="flight-radio")

    def send(self, data, *, keep_listening=False, destination=None, node=None, identifier=None, flags=None):
        self._medium.send(bytes(data))
        return True

    def receive(self, *, timeout=0.5, keep_listening=True, with_header=False, with_ack=False):
        data = self._medium.receive(timeout=timeout)
        if data is None:
            return None
        return bytearray(data)

    def deinit(self):
        self._medium.close()
