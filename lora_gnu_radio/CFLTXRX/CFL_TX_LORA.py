# Imports
import time
import busio
import board
import adafruit_rfm9x
from encode import encode_rap
from digitalio import DigitalInOut, Direction, Pull

# LoRa setup
CS = DigitalInOut(board.CE1) # init CS pin for SPI
RESET = DigitalInOut(board.D25) # init RESET pin for the RFM9x module
spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO) # init SPI
rfm9x = adafruit_rfm9x.RFM9x(spi, CS, RESET, 437.2) # init object for the radio

# LoRa settings
# rfm9x.tx_power = 23 # TX power in dBm (23 dBm = 0.2 W)
# rfm9x.signal_bandwidth = 62500 # high bandwidth => high data rate and low range
# rfm9x.coding_rate = 6
# rfm9x.spreading_factor = 8
# rfm9x.enable_crc = True

print(rfm9x.tx_power)
print(rfm9x.signal_bandwidth)
print(rfm9x.coding_rate)
print(rfm9x.spreading_factor)

# Your transmissions will be in the form of a RAP packet. This is the flag for a beacon, as seen
#   in RAXRadioPacketFormat.pdf
BEACON_FLAG = 0x03

def send():
    # Keeping track of number of beacons sent
    count = 0
    while True:
        # Only current RAP type is beacon
        # Future iterations may send other types of packets

        # Example beacon
        beacon = bytearray(b'\x53\x74\x72\x61\x74\x6f\x53\x61\x74')

        # Adding RAP packet wrapper
        rap = encode_rap(BEACON_FLAG, beacon)

        # Send packet
        rfm9x.send(rap)
        print(count)
        count += 1

        # Beacon frequency
        time.sleep(10)

send()