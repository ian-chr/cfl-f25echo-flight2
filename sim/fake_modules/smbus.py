"""Fake `smbus` module.

telem_deps/app.py (`from smbus import SMBus`) and
telem_deps/sensors/rtc.py (`import smbus`) both talk to the shared
VirtualI2CBus registry instead of a real Linux i2c-dev handle.
"""
from sim.devices.i2c_virtual_bus import get_bus


class SMBus:
    def __new__(cls, bus_num=1):
        return get_bus(bus_num)
