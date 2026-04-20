#from variables import RECEIVE_QUEUE

START_SYNC_0 = 0xAB
START_SYNC_1 = 0xCD
RAP_HEAD_LENGTH = 11
RAP_FOOT_LENGTH = 6

def int_from_bytes(b, byteorder='little', signed=False):
    return int.from_bytes(bytes(b), byteorder=byteorder, signed=signed)


def segment(arr, offset, length):
    return arr[offset:offset + length]

def checksum(data):
    dlen = len(data)
    s0, s1 = 0xff, 0xff
    i = 0
    while dlen > 0:
        tlen = dlen
        if dlen > 21:
            tlen = 21

        dlen -= tlen
        while True:
            s0 += int(data[i])
            i += 1
            s1 += s0
            tlen -= 1
            if tlen == 0:
                break
        s0 = (s0 & 0xff) + (s0 >> 8)
        s1 = (s1 & 0xff) + (s1 >> 8)

    s0 = (s0 & 0xff) + (s0 >> 8)
    s1 = (s1 & 0xff) + (s1 >> 8)
    return [s0, s1]

def decode_rap(packet):
    packet, rap, pid, sid, rap_type = process_incoming_bytes(packet)
    if not rap:
        return None, None, None, None, None

    return packet, rap, pid, sid, rap_type

def process_incoming_bytes(packet):
    #global RECEIVE_QUEUE

    # These are AB and CD
    sync = bytearray([START_SYNC_0, START_SYNC_1])
    sync_index = packet.find(sync)
    # Make sure packet starts with ABCD
    if sync_index < 0:
        print('sync characters not found in packet ' + packet.hex())
        return None, None, None, None, None
    packet = packet[sync_index:]

    # Extracts RAP_Header
    # Check RAXRadioPacketFormats.pdf for exact meaning of each RAP byte
    header = segment(packet, 0, RAP_HEAD_LENGTH)

    pid = int_from_bytes(header[2:4])  # pylint: disable=unused-variable
    sid = int_from_bytes(header[4:6], byteorder='big')
    flag = header[6]
    rap_length = int_from_bytes(header[7:9])

    """
    while len(packet) < rap_length:
        if RECEIVE_QUEUE.empty():
            sleep(0.01)
        else:
            next_packet, _ = RECEIVE_QUEUE.get()
            packet = packet + next_packet
    """
    # Header checksum is placed in the header
    header_checksum = segment(header, 9, 2)
    # Comparing the received header checksum to the calculated header checksum 
    try:
        check = bytes(checksum(header[0:RAP_HEAD_LENGTH - 2]))
        if check != header_checksum:
            raise RuntimeError()
    except RuntimeError:
        print(' '.join([
            'header checksum failed', 'header:',
            header.hex(), 'expected:',
            header_checksum.hex(), 'received:',
            check.hex()
        ]))
        return None, None, None, None, None

    # Data is from after the RAP header (11 bytes) to before the RAP footer (6 bytes)
    data = segment(packet, RAP_HEAD_LENGTH, rap_length - RAP_HEAD_LENGTH - RAP_FOOT_LENGTH)
    # RAP footer starts after the data section and extends RAP_FOOT_LENGTH (6 bytes) long
    footer = segment(packet, rap_length - RAP_FOOT_LENGTH, RAP_FOOT_LENGTH)

    # Data checksoom is placed in footer
    data_checksum = segment(footer, 0, 2)

    # Comparing the received data checksum to the calculated data checksum 
    try:
        check = bytes(checksum(data))
        if check != data_checksum:
            raise RuntimeError()
    except RuntimeError:
        print(' '.join(
            ['data checksum failed', 'data:',
             data.hex(), 'expected:',
             data_checksum.hex(), 'received:',
             check.hex()]))
        return None, None, None, None, None

    # Returns original RAP packet, data field, pid, sid, and the flag
    return packet, data, pid, sid, flag
