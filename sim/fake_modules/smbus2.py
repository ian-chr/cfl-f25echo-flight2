"""Fake `smbus2` module (telem_deps/sensors/eddy_pdu.py: `from smbus2 import SMBus`)."""
from sim.devices.i2c_virtual_bus import get_bus


class SMBus:
    def __new__(cls, bus_num=1):
        return get_bus(bus_num)


class i2c_msg:
    """Not used by eddy_pdu.py today, but smbus2 exposes it -- stub it out
    so `from smbus2 import i2c_msg` elsewhere doesn't hard-fail in sim."""

    @staticmethod
    def read(address, length):
        raise NotImplementedError("sim smbus2.i2c_msg is not implemented")

    @staticmethod
    def write(address, buf):
        raise NotImplementedError("sim smbus2.i2c_msg is not implemented")
