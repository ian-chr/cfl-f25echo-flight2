from os import _exit
from decode import decode_rap
from socket import create_connection

SYNC_CHARS = bytearray(b'\xAB\xCD')

### This is a combination of tcp.py, manage.py, and CFL_RX.py

def listen():
    try:
        while True:
            sock = create_connection(('127.0.0.1', 11600)) #12600 or 11600?

            while True:
                packet = sock.recv(4096)
                if not packet:
                    break
                sync_index = packet.find(SYNC_CHARS)
                if sync_index < 0:
                    continue

                try:

                    print(packet)

                    # Remove first 4 bytes (RadioHead header that Adafruit LoRas use)
                    packet_clean = packet[4:]
                    
                    # Decode RAP packet
                    packet_clean, rap, pid, sid, rap_type = decode_rap(packet_clean) # from manage.py

                    print(f"Packet from sid {sid}:")
                    print(f"Received {len(packet_clean)} bytes")
                    print(packet_clean.hex(), '\n\n')

                    # # CFL test craft
                    # if sid == 91:
                    #     with open("/home/mxl/CFL/cflinstructors/GroundStation/uhf-receive/CFL/flightPrograms/cfl_test_craft_beacons.txt", 'a') as f:
                    #         f.write(packet_clean.hex() + '\n\n')

                    # # Team Mike
                    # if sid == 13:
                    #     with open("/home/mxl/CFL/cflinstructors/GroundStation/uhf-receive/CFL/flightPrograms/team_mike_beacons.txt", 'a') as f:
                    #         f.write(packet_clean.hex() + '\n\n')

                    # # Team November
                    # if sid == 14:
                    #     with open("/home/mxl/CFL/cflinstructors/GroundStation/uhf-receive/CFL/flightPrograms/team_november_beacons.txt", 'a') as f:
                    #         f.write(packet_clean.hex() + '\n\n')

                    # # Team Oscar
                    # if sid == 15:
                    #     with open("/home/mxl/CFL/cflinstructors/GroundStation/uhf-receive/CFL/flightPrograms/team_oscar_beacons.txt", 'a') as f:
                    #         f.write(packet_clean.hex() + '\n\n')

                    # # Team Papa
                    # if sid == 16:
                    #     with open("/home/mxl/CFL/cflinstructors/GroundStation/uhf-receive/CFL/flightPrograms/team_papa_beacons.txt", 'a') as f:
                    #         f.write(packet_clean.hex() + '\n\n')

                    # # Team Quebec
                    # if sid == 17:
                    #     with open("/home/mxl/CFL/cflinstructors/GroundStation/uhf-receive/CFL/flightPrograms/team_quebec_beacons.txt", 'a') as f:
                    #         f.write(packet_clean.hex() + '\n\n')

                    # FTU
                    if sid == 18:
                        with open("FTU_beacons.txt", 'a') as f:
                            f.write(packet_clean.hex() + '\n\n')

                
                except Exception as e:
                    print(e)
                    pass
                

    except Exception as e:
        print(e)
        pass
        #_exit(1)

listen()
