import argparse
from collections import namedtuple
from enum import Enum
import json

HEX_DISPLAY = True

class FtdiCommandType(Enum):
    # Command type is unknown
    UNKNOWN = 0
    # Clocking data on TDI
    CLOCK_TDI = 0x10
    # Capturing data on TDO
    CLOCK_TDO = 0x20
    # Clocking data on TMS
    CLOCK_TMS = 0x40
    # Configure low octet of GPIO's
    SET_GPIO_LOW_BYTE = 0x80
    # Get line state of low octet of GPIO's
    GET_GPIO_LOW_BYTE = 0x81
    # Configure high octet of GPIO's
    SET_GPIO_HIGH_BYTE = 0x82
    # Get line state of high octet of GPIO's
    GET_GPIO_HIGH_BYTE = 0x83
    DISABLE_LOOPBACK = 0x85
    # Set FTDI clock rate by setting the clock divider
    SET_DIVISOR = 0x86
    FLUSH = 0x87
    DISABLE_DIV_BY_5 = 0x8a
    # Run clock without changing TDI/TDO/TMS
    CLOCK_NO_DATA = 0x8f


class FtdiFlags(Enum):
    # Data changes on negative edge
    NEG_EDGE_OUT = 0x1
    # Operation length is in bits
    BITWISE = 0x2
    # Data is captured on negative edge
    NEG_EDGE_IN = 0x4
    # Data is sent LSB first
    LSB_FIRST = 0x8
    # Is TDI held high or low when clocking bits?
    TDI_HIGH = 0x80

FtdiCommand = namedtuple('FtdiCommand', 'type flags opcode length data reply')


class DecodeError(RuntimeError):
    """ Raised when a decoding error is found. """
    def __init__(self, msg, last_byte=None):
        RuntimeError.__init__(self, msg)
        self.last_byte = last_byte

    def get_last_byte(self):
        return self.last_byte


def get_write_flags(byte):
    """ Get command flags for write from command byte. Returns list of FtdiFlags. """
    flags = []
    for flag in [FtdiFlags.NEG_EDGE_OUT, FtdiFlags.BITWISE, FtdiFlags.NEG_EDGE_IN, FtdiFlags.LSB_FIRST]:
        if byte & flag.value != 0:
            flags.append(flag)

    return flags


def read_data(byte, ftdi_bytes, ftdi_replies):
    """ Get written data, and returned reply (if any). """
    if byte & FtdiFlags.BITWISE.value != 0:
        # Bitwise write
        number_of_bits = ftdi_bytes.popleft() + 1
        if number_of_bits > 7:
            raise DecodeError('Bitwise clocking should only clock 7 or less bits, found {}'.format(number_of_bits))

        data = [ftdi_bytes.popleft()]
        reply = None

        if byte & FtdiCommandType.CLOCK_TDO.value != 0:
            reply = [ftdi_replies.popleft()]

        return number_of_bits, data, reply
    else:
        # Byte write
        number_of_bytes = ftdi_bytes.popleft()
        number_of_bytes |= ftdi_bytes.popleft() << 8
        number_of_bytes += 1
        data = [ftdi_bytes.popleft() for _ in range(number_of_bytes)]
        reply = None

        if byte & FtdiCommandType.CLOCK_TDO.value != 0:
            reply = [ftdi_replies.popleft() for _ in range(number_of_bytes)]

        return number_of_bytes, data, reply


class Buffer(object):
    """ deque like object that doesn't discard data on pop.

    This is useful for rewinding failed decodes.

    """
    def __init__(self):
        self.buf = []
        self.pop_index = 0

    def __len__(self):
        assert self.pop_index >= 0 and self.pop_index <= len(self.buf)
        return len(self.buf) - self.pop_index

    def extend(self, iterable):
        self.buf.extend(iterable)

    def popleft(self):
        assert self.pop_index >= 0 and self.pop_index <= len(self.buf)
        if self.pop_index < len(self.buf):
            ret = self.buf[self.pop_index]
            self.pop_index += 1
            return ret
        else:
            raise IndexError()

    def get_context(self, C=10):
        """ Yield bytes before and after the current byte for context.

        Useful for debugging decode failures.

        """
        first_idx = max(self.pop_index - C, 0)
        delta = self.pop_index - first_idx
        for idx, data in enumerate(self.buf[first_idx:self.pop_index+C]):
            yield -delta + idx, data


def decode_commands(ftdi_bytes, ftdi_replies):
    """ Attempt to decode commands from ftdi_bytes, paired with replies. """

    ftdi_commands = []
    def add_command(command_type, command_opcode, flags=None, length=None, data=None, reply=None):
        if length is not None:
            #assert len(data) == length
            #if reply is not None:
            #    assert len(reply) == length
            pass

        if HEX_DISPLAY:
            if data is not None:
                data = tuple(hex(b) for b in data)
            if reply is not None:
                reply = tuple(hex(b) for b in reply)

        ftdi_commands.append(FtdiCommand(
            type=command_type,
            opcode=command_opcode,
            flags=flags,
            length=length,
            data=data,
            reply=reply))

    while len(ftdi_bytes):
        byte = ftdi_bytes.popleft()

        if byte == 0xaa:
            reply = [ftdi_bytes.popleft() for _ in range(2)]
            add_command(FtdiCommandType.UNKNOWN, byte, reply=reply)
        elif byte == 0xab:
            reply = [ftdi_bytes.popleft() for _ in range(2)]
            add_command(FtdiCommandType.UNKNOWN, byte, reply=reply)
        elif byte & FtdiCommandType.CLOCK_TMS.value != 0:
            if byte & FtdiCommandType.CLOCK_TDI.value != 0:
                raise DecodeError('When clocking TMS, cannot clock TDI?')

            flags = get_write_flags(byte)
            length, data, reply = read_data(byte, ftdi_bytes, ftdi_replies)
            add_command(
                    command_type=FtdiCommandType.CLOCK_TMS,
                    command_opcode=byte,
                    flags=flags,
                    length=length,
                    data=data,
                    reply=reply,
                    )
        elif byte & FtdiCommandType.CLOCK_TDI.value != 0:
            if byte & FtdiCommandType.CLOCK_TMS.value != 0:
                raise DecodeError('When clocking TDI, cannot clock TMS?')

            flags = get_write_flags(byte)
            length, data, reply = read_data(byte, ftdi_bytes, ftdi_replies)
            add_command(
                    command_type=FtdiCommandType.CLOCK_TDI,
                    command_opcode=byte,
                    flags=flags,
                    length=length,
                    data=data,
                    reply=reply,
                    )
        elif byte & FtdiCommandType.CLOCK_TDO.value != 0:
            # These shouldn't been detected in the previous elif blocks
            assert byte & FtdiCommandType.CLOCK_TMS.value == 0, hex(byte)
            assert byte & FtdiCommandType.CLOCK_TDI.value == 0, hex(byte)

            flags = get_write_flags(byte)
            length = 0
            if byte & FtdiFlags.BITWISE.value != 0:
                length = ftdi_bytes.popleft() + 1

                if length > 7:
                    raise DecodeError('Bitwise clocking should only clock 7 or less bits, found {}'.format(length))

                reply = [ftdi_replies.popleft()]
            else:
                length = ftdi_bytes.popleft()
                length = ftdi_bytes.popleft() << 8
                length += 1
                reply = [ftdi_replies.popleft() for _ in range(length)]

            add_command(
                    command_type=FtdiCommandType.CLOCK_TDO,
                    command_opcode=byte,
                    flags=flags,
                    length=length,
                    reply=reply,
                    )
        elif byte == FtdiCommandType.CLOCK_NO_DATA.value:
            flags = []
            length = ftdi_bytes.popleft()
            length = ftdi_bytes.popleft() << 8
            length += 1

            add_command(
                    command_type=FtdiCommandType.CLOCK_NO_DATA,
                    command_opcode=byte,
                    flags=flags,
                    length=length,
                    )
        elif byte == FtdiCommandType.SET_GPIO_LOW_BYTE.value:
            data = [ftdi_bytes.popleft() for _ in range(2)]
            add_command(FtdiCommandType.SET_GPIO_LOW_BYTE, byte, data=data)
        elif byte == FtdiCommandType.GET_GPIO_LOW_BYTE.value:
            reply = [ftdi_replies.popleft()]
            add_command(FtdiCommandType.GET_GPIO_LOW_BYTE, byte, reply=reply)
        elif byte == FtdiCommandType.SET_GPIO_HIGH_BYTE.value:
            data = [ftdi_bytes.popleft() for _ in range(2)]
            add_command(FtdiCommandType.SET_GPIO_HIGH_BYTE, byte, data=data)
        elif byte == FtdiCommandType.GET_GPIO_HIGH_BYTE.value:
            reply = [ftdi_replies.popleft()]
            add_command(FtdiCommandType.GET_GPIO_HIGH_BYTE, byte, reply=reply)
        elif byte == FtdiCommandType.DISABLE_LOOPBACK.value:
            add_command(FtdiCommandType.DISABLE_LOOPBACK, byte)
        elif byte == FtdiCommandType.SET_DIVISOR.value:
            data = ftdi_bytes.popleft()
            data |= ftdi_bytes.popleft() << 8
            add_command(FtdiCommandType.SET_DIVISOR, byte, data=[data])
        elif byte == FtdiCommandType.FLUSH.value:
            add_command(FtdiCommandType.FLUSH, byte)
        elif byte == FtdiCommandType.DISABLE_DIV_BY_5.value:
            add_command(FtdiCommandType.DISABLE_DIV_BY_5, byte)
        else:
            raise DecodeError('Unknown byte {}'.format(hex(byte)))

    return ftdi_commands


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json_pcap')

    args = parser.parse_args()

    ftdi_bytes = Buffer()
    ftdi_replies = Buffer()
    print('Loading data')
    with open(args.json_pcap) as f:
        for cap in json.load(f):
            protocol = cap.get('_source', {}).get('layers', {}).get('frame', {}).get('frame.protocols')
            if protocol != 'usb:ftdift':
                continue

            tx_data = cap.get('_source', {}).get('layers', {}).get('ftdift', {}).get('ftdift.if_a_tx_payload')
            if tx_data is not None:
                ftdi_bytes.extend(int(byte, 16) for byte in tx_data.split(':'))

            rx_data = cap.get('_source', {}).get('layers', {}).get('ftdift', {}).get('ftdift.if_a_rx_payload')
            if rx_data is not None:
                ftdi_replies.extend(int(byte, 16) for byte in rx_data.split(':'))

    print('Parsing data')
    try:
        ftdi_commands = decode_commands(ftdi_bytes, ftdi_replies)
    except DecodeError as e:
        print('Failed decode at:', hex(e.get_last_byte()))
        print('Context:')
        C = 50
        for offset, context_bytes in ftdi_bytes.get_context(C=C):
            print(offset+1, hex(context_bytes))
        raise

    #assert len(ftdi_replies) == 0, len(ftdi_replies)
    print('Leftover reply bytes', len(ftdi_replies))

    for idx, cmd in enumerate(ftdi_commands):
        print(cmd)


if __name__ == "__main__":
    main()
