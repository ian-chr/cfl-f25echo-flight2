from hashlib import sha1

# Used in hash function for authentication
KEY_BYTES = bytearray([0x56, 0xAF, 0x55, 0x1a, 0x30, 0x56, 0xf0, 0x13, 0xfa, 0x85, 0x82, 0x14, 0xac, 0x91, 0x81, 0x77, 0x00])

# RAP sync bytes
START_SYNC_0 = 0xAB
START_SYNC_1 = 0xCD

# RAP header and footer lengths
RAP_HEAD_LENGTH = 11
RAP_FOOT_LENGTH = 6

# Your payload's spacecraft ID
PID = 495
SID = 32

def checksum(data):
    # This calculates the Fletcher-16 checksum of your passed bytearray

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

def int_to_bytes(n, size, byteorder='little'):
    # Convert integers to byets

    return bytes(int(n).to_bytes(size, byteorder=byteorder))

def encode_rap(flag, data):
    # Wraps the passed data with RAP header and footers. The final returned packet is the 
    #   variable buffer

    # Total packet length
    length = RAP_HEAD_LENGTH + len(data) + RAP_FOOT_LENGTH
    
    # Initiate buffer variable as a bytearray to be built with our data
    buffer = bytearray(length)

    # Setting up RAP header
    buffer[0] = START_SYNC_0
    buffer[1] = START_SYNC_1

    pidArray = int_to_bytes(PID, 2)
    buffer[2] = pidArray[0]
    buffer[3] = pidArray[1]

    sidArray = int_to_bytes(SID, 2, 'big')
    buffer[4] = sidArray[0]
    buffer[5] = sidArray[1]

    buffer[6] = flag

    lenArray = int_to_bytes(length, 2)
    buffer[7] = lenArray[0]
    buffer[8] = lenArray[1]

    header_checksum_0, header_checksum_1 = bytes(checksum(buffer[0:(RAP_HEAD_LENGTH - 2)]))
    buffer[9] = header_checksum_0
    buffer[10] = header_checksum_1

    # Adding data bytes
    for i, b in enumerate(data):
        buffer[RAP_HEAD_LENGTH + i] = b

    # Calculating checksum and setting up RAP footer
    data_checksum_0, data_checksum_1 = bytes(checksum(data))
    buffer[RAP_HEAD_LENGTH + len(data)] = data_checksum_0
    buffer[RAP_HEAD_LENGTH + len(data) + 1] = data_checksum_1

    hmac = calculate_hmac(data, flag, header_checksum_0, header_checksum_1, data_checksum_0, data_checksum_1)

    for i in range(4):
        buffer[RAP_HEAD_LENGTH + len(data) + 2 + i] = hmac[i]

    return buffer


def calculate_hmac(data, flag, header_checksum_0, header_checksum_1, data_checksum_0, data_checksum_1):
    # Calculaate HMAC 
    
    md = sha1()
    md2 = sha1()

    opad = bytearray(64)
    ipad = bytearray(64)

    for i in range(64):
        opad[i] = 0x5c
        ipad[i] = 0x36

    for i, b in enumerate(KEY_BYTES):
        opad[i] ^= b
        ipad[i] ^= b

    md.update(ipad)
    md.update(int_to_bytes(START_SYNC_0, 1))
    md.update(int_to_bytes(START_SYNC_1, 1))
    md.update(int_to_bytes(PID, 2))
    md.update(int_to_bytes(SID, 2))
    md.update(int_to_bytes(flag, 1))
    md.update(int_to_bytes(RAP_HEAD_LENGTH + len(data) + RAP_FOOT_LENGTH, 2))
    md.update(int_to_bytes(header_checksum_0, 1))
    md.update(int_to_bytes(header_checksum_1, 1))
    md.update(bytearray(data))
    md.update(int_to_bytes(data_checksum_0, 1))
    md.update(int_to_bytes(data_checksum_1, 1))

    raw_hmac = bytearray(md.digest())

    md2.update(opad)
    new_hmac = raw_hmac[0:4]
    for x in range(0, 4):
        raw_hmac[x] = new_hmac[3 - x]
    raw_hmac = raw_hmac[0:4]

    md2.update(raw_hmac)
    hmac = md2.digest()
    hmac = hmac[0:4]

    return list(reversed(hmac))
