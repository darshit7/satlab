"""The CRCs of the framings we model, each tested against its catalogue check value."""


def crc16_x25(data: bytes) -> int:
    """AX.25 frame check sequence: CRC-16/X-25 (poly 0x1021 reflected, init and xorout 0xFFFF).

    Sent least significant byte first. Check value 0x906E.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def crc16_ccitt_false(data: bytes) -> int:
    """CCSDS packet error control: CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection).

    Sent most significant byte first. Check value 0x29B1.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc
