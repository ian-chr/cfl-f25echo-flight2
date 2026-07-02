"""Virtual I2C bus.

A shared, address-keyed registry of virtual peripherals that the fake
`smbus`/`smbus2` modules (see sim/fake_modules/) dispatch onto. This is
the plug-and-play seam for I2C sensors: anything exposing the subset of
{read_byte_data, read_word_data, read_i2c_block_data, write_byte_data}
it needs can be register()-ed at an address, and nothing else in the
simulator -- not the FSW code, not other devices -- has to change.

One VirtualI2CBus per bus number is shared process-wide, because the
real FSW code opens several independent SMBus(1) handles (app.py,
gps.py, rtc.py, eddy_pdu.py's caller) that all address the same
physical wire -- they must see one consistent set of devices.
"""
from __future__ import annotations

import threading
from typing import Dict, Optional


class I2CError(OSError):
    def __init__(self, msg):
        super().__init__(16, msg)  # errno 16 = EBUSY, matches rtc.py's retry check


class VirtualI2CBus:
    def __init__(self, bus_num: int):
        self.bus_num = bus_num
        self._devices: Dict[int, object] = {}
        self._lock = threading.Lock()

    def register(self, addr: int, device: object) -> None:
        with self._lock:
            self._devices[addr] = device

    def unregister(self, addr: int) -> None:
        with self._lock:
            self._devices.pop(addr, None)

    def _dev(self, addr: int):
        with self._lock:
            dev = self._devices.get(addr)
        if dev is None:
            raise I2CError(f"[sim-i2c bus {self.bus_num}] no device at 0x{addr:02X}")
        return dev

    def read_byte_data(self, addr: int, reg: int) -> int:
        return self._dev(addr).read_byte_data(reg)

    def read_word_data(self, addr: int, reg: int) -> int:
        return self._dev(addr).read_word_data(reg)

    def read_i2c_block_data(self, addr: int, reg: int, length: int):
        return self._dev(addr).read_i2c_block_data(reg, length)

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        dev = self._dev(addr)
        write = getattr(dev, "write_byte_data", None)
        if write:
            write(reg, value)

    def close(self) -> None:
        pass

    # Context-manager support (some code does `with SMBus(1) as bus:`)
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


_REGISTRY: Dict[int, VirtualI2CBus] = {}
_REGISTRY_LOCK = threading.Lock()


def get_bus(bus_num: int = 1) -> VirtualI2CBus:
    with _REGISTRY_LOCK:
        bus = _REGISTRY.get(bus_num)
        if bus is None:
            bus = VirtualI2CBus(bus_num)
            _install_default_devices(bus)
            _REGISTRY[bus_num] = bus
        return bus


def _install_default_devices(bus: VirtualI2CBus) -> None:
    """Populate a freshly-created bus with the Echo FSW's known I2C
    peripherals. Add/remove entries here to change what's "plugged in" --
    every device is independent and none of them know about each other."""
    from sim.devices.tmp102_device import TMP102VirtualDevice
    from sim.devices.rtc_device import RV8803VirtualDevice
    from sim.devices.gps_device import UbloxGpsVirtualDevice
    from sim.devices.pdu_device import ADS7828VirtualDevice
    from sim.world import get_world
    from sim.config import get_config

    world = get_world()
    cfg = get_config()

    bus.register(0x4B, TMP102VirtualDevice(world, offset_c=2.0))    # TMP102 (internal)
    bus.register(0x4A, TMP102VirtualDevice(world, offset_c=-1.5))   # TMP1022 (external)
    bus.register(0x32, RV8803VirtualDevice(world, drift_sec=cfg.rtc_drift_sec))
    bus.register(0x42, UbloxGpsVirtualDevice(world))
    bus.register(0x48, ADS7828VirtualDevice(world, chip="0"))
    bus.register(0x49, ADS7828VirtualDevice(world, chip="1"))
