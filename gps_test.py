## Helpful script to test GPS test outside of run_telem.py script
## 
from smbus import SMBus

## simple consts used for testing
BUS = 1
ADDR = 0x42
LSB = 0xFD
MSB = 0xFE
DATA = 0xFF
CHUNK = 32  # safe SMBus block size

bus = SMBus(BUS)
buf = bytearray()

def avail():
    l = bus.read_byte_data(ADDR, LSB)
    h = bus.read_byte_data(ADDR, MSB)
    n = (h << 8) | l
    # mask overflow flag (bit 15). if set, we've lost data.
    overflow = bool(n & 0x8000)
    n &= 0x7FFF
    return n, overflow

# drain once to clear backlog
while True:
    n, of = avail()
    if n == 0:
        break
    while n > 0:
        take = CHUNK if n >= CHUNK else n
        buf.extend(bus.read_i2c_block_data(ADDR, DATA, take))
        n -= take

# now read and print a few clean NMEA lines
buf.clear()
lines_printed = 0
while lines_printed < 10:
    n, of = avail()
    if n:
        while n > 0:
            take = CHUNK if n >= CHUNK else n
            buf.extend(bus.read_i2c_block_data(ADDR, DATA, take))
            n -= take
        # split complete lines
        while True:
            i = buf.find(b'\n')
            if i < 0: break
            line = buf[:i+1]; del buf[:i+1]
            s = line.decode('ascii', errors='ignore').strip()
            if s.startswith('$'):
                print(s)
                lines_printed += 1
    # small pause is optional; the device is pushing data regardless
