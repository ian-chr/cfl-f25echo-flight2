import time
import requests
import datetime
from os import _exit
from datetime import datetime
from decode import decode_rap
from socket import create_connection

SYNC_CHARS = bytearray(b'\xAB\xCD')

### API settings

api_url_midas = "https://cfl.engin.umich.edu/api/v1/packet/"
api_token = '8uA-G6iETPR-9wWWBHJoyO32alF8Nx4nO9V6-uuky7k=|1721836428|daniilv@umich.edu'
cookies_midas = {
    '_oauth2_proxy': api_token
}

# This is a copycat function from server.py
def push_to_server(timestamp: int, packet: bytes, pid: int, sid: int,
                   rap_type: int, received: bool):
    data = {
        'data':
            packet.hex(),
        'flag':
            rap_type,
        'pid':
            pid,
        'sid':
            sid,
        'received':
            received,
        'recorded_at':
            datetime.utcfromtimestamp(timestamp / 1000.0).isoformat('T') + 'Z',
    }
    
    # Push beacon to MIDAS (make an API request)
    requests.post(api_url_midas, json=data, cookies=cookies_midas)

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
                
                timestamp = int(datetime.now().timestamp() * 1e3) # from tcp.py

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


                    # Push beacon to MIDAS if this is a telemetry beacon (and not an image thumbnail, etc)
                    if (rap is not None) :
                        push_to_server(timestamp, packet_clean, pid, sid, rap_type, True)
                
                except Exception as e:
                    print(e)
                    pass
                

    except Exception as e:
        print(e)
        pass
        #_exit(1)

listen()
