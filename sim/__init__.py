"""Echo FSW hardware-in-the-loop-free simulator.

This package lets the *real* flight software (telem_deps/app.py,
radio/unified_radio.py, watchdog/watchdog.py, camera_worker.py) run
unmodified on a laptop, with every hardware touchpoint (I2C sensors,
Blinka/SPI radio, the Pi camera stack, and systemd) replaced by a
plug-and-play virtual device that is driven by a shared "flight world"
model (see sim.world).

See sim/README.md for the architecture writeup and usage.
"""
