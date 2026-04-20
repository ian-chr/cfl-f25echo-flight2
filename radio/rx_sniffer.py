#!/usr/bin/env python3
"""
StratoSat ECHO – Simple LoRa RX Sniffer

Listens on 437.2 MHz and prints any packets it hears.
If the packet looks like RAP, it tries to decode and print SID/PID/FLAG.
"""

import time

import board
import busio
from digitalio import DigitalInOut
import adafruit_rfm9x

from lora_gnu_radio.CFLTXRX.decode import decode_rap

LORA_FREQ_MHZ = 437.2  # same as unified_radio


def init_radio():
    CS = DigitalInOut(board.CE1)
    RESET = DigitalInOut(board.D25)
    spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)

    rfm9x = adafruit_rfm9x.RFM9x(spi, CS, RESET, LORA_FREQ_MHZ)
    rfm9x.tx_power = 23

    print(f"[RADIO] LoRa RX sniffer @ {LORA_FREQ_MHZ} MHz")
    if hasattr(rfm9x, "spreading_factor"):
        print(f"[RADIO]   SF: {rfm9x.spreading_factor}")
    if hasattr(rfm9x, "signal_bandwidth"):
        print(f"[RADIO]   BW: {rfm9x.signal_bandwidth}")
    if hasattr(rfm9x, "coding_rate"):
        print(f"[RADIO]   CR: {rfm9x.coding_rate}")

    return rfm9x


def main():
    rfm9x = init_radio()
    print("[RX] Waiting for packets... (Ctrl+C to stop)\n")

    while True:
        try:
            packet = rfm9x.receive(timeout=5.0)

            if packet is None:
                print("[RX] 5s timeout – no packet")
                continue

            rssi = getattr(rfm9x, "last_rssi", None)
            snr = getattr(rfm9x, "last_snr", None)

            print("\n[RX] Raw packet:")
            print(f"  len={len(packet)} bytes")
            print(f"  hex={packet.hex()}")
            if rssi is not None:
                print(f"  RSSI={rssi} dBm")
            if snr is not None:
                print(f"  SNR={snr} dB")

            # Try to treat it as RAP
            if len(packet) >= 4:
                try:
                    rap, rap_data, pid, sid, flag = decode_rap(packet)
                    print("[RAP] Decoded RAP:")
                    print(f"  SID={sid}, PID={pid}, FLAG=0x{flag:02X}")
                    print(f"  RAP payload len={len(rap_data)}")
                    print(f"  RAP payload hex={rap_data.hex()}")
                except Exception as e:
                    print(f"[RAP] decode_rap failed: {e}")
        except KeyboardInterrupt:
            print("\n[RX] Stopped by user")
            break
        except Exception as e:
            print(f"[ERR] RX loop error: {e}")
            time.sleep(1.0)


if __name__ == "__main__":
    main()