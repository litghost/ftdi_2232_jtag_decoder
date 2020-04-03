import argparse
from collections import namedtuple, deque
from enum import Enum
import json


FTDI_MAX_PACKET_SIZE = 512

# All debugging statements, other than DRSHIFT and IRSHIFT.
DEBUG_JTAG_SIM = False

# If DEBUG_JTAG_SIM is True, prints DRSHIFT transitions as well
DEBUG_JTAG_SIM_DRSHIFT = False

# If DEBUG_JTAG_SIM is True, prints IRSHIFT transitions as well
DEBUG_JTAG_SIM_IRSHIFT = False

PRINT_BITSTREAM = False

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
    DISABLE_RCLK = 0x97
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

FtdiCommand = namedtuple('FtdiCommand', 'type flags command_frame reply_frame opcode length data reply')


class DecodeError(RuntimeError):
    """ Raised when a decoding error is found. """
    def __init__(self, msg, commands, last_byte=None):
        RuntimeError.__init__(self, msg)
        self.commands = commands
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
            raise DecodeError('Bitwise clocking should only clock 7 or less bits, found {}'.format(number_of_bits), commands=None, last_byte=byte)

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
        self.insert_boundry = set()
        self.pop_index = 0
        self.frames = {}
        self.begin_to_frame = {}
        self.frame = None

    def __len__(self):
        assert self.pop_index >= 0 and self.pop_index <= len(self.buf)
        return len(self.buf) - self.pop_index

    def extend(self, iterable, frame=None):
        original_index = len(self.buf)
        self.buf.extend(iterable)
        self.insert_boundry.add(len(self.buf))
        if frame is not None:
            self.frames[frame] = (original_index, len(self.buf))
            self.begin_to_frame[original_index] = frame

    def popleft(self):
        assert self.pop_index >= 0 and self.pop_index <= len(self.buf)
        if self.pop_index < len(self.buf):
            ret = self.buf[self.pop_index]

            # Update self.frame
            if self.pop_index == 0:
                self.frame = self.begin_to_frame[self.pop_index]

                b, e = self.frames[self.frame]
                assert self.pop_index == b
            else:
                b, e = self.frames[self.frame]
                assert self.pop_index >= b
                if self.pop_index >= e:
                    self.frame = self.begin_to_frame[self.pop_index]

                    b, e = self.frames[self.frame]
                    assert self.pop_index == b

            self.pop_index += 1
            return ret
        else:
            raise IndexError()

    def at_boundry(self):
        return self.pop_index in self.insert_boundry

    def current_frame(self):
        return self.frame

    def frame_of(self, idx):
        for frame, (begin, end) in self.frames.items():
            if idx >= begin and idx < end:
                return frame

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

        if reply is not None:
            reply_frame = ftdi_replies.current_frame()
        else:
            reply_frame = None

        ftdi_commands.append(FtdiCommand(
            type=command_type,
            opcode=command_opcode,
            command_frame=ftdi_bytes.current_frame(),
            reply_frame=reply_frame,
            flags=flags,
            length=length,
            data=data,
            reply=reply))

    while len(ftdi_bytes):
        byte = ftdi_bytes.popleft()

        if byte == 0xaa:
            reply = [ftdi_replies.popleft() for _ in range(2)]
            add_command(FtdiCommandType.UNKNOWN, byte, reply=reply)
        elif byte == 0xab:
            reply = [ftdi_replies.popleft() for _ in range(2)]
            add_command(FtdiCommandType.UNKNOWN, byte, reply=reply)
        elif byte == FtdiCommandType.DISABLE_RCLK.value:
            add_command(FtdiCommandType.DISABLE_RCLK, byte)
        elif byte & FtdiCommandType.CLOCK_TMS.value != 0:
            if byte & FtdiCommandType.CLOCK_TDI.value != 0:
                raise DecodeError('When clocking TMS, cannot clock TDI?', ftdi_commands, last_byte=byte)

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
                raise DecodeError('When clocking TDI, cannot clock TMS?', ftdi_commands, last_byte=byte)

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
                    raise DecodeError('Bitwise clocking should only clock 7 or less bits, found {}'.format(length), ftdi_commands, last_byte=byte)

                reply = [ftdi_replies.popleft()]
            else:
                length = ftdi_bytes.popleft()
                length |= ftdi_bytes.popleft() << 8
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
            length |= ftdi_bytes.popleft() << 8
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
            if not ftdi_replies.at_boundry():
                raise DecodeError('Should have a RX boundry in reply data?',
                        ftdi_commands, last_byte=byte)

            add_command(FtdiCommandType.FLUSH, byte)
        elif byte == FtdiCommandType.DISABLE_DIV_BY_5.value:
            add_command(FtdiCommandType.DISABLE_DIV_BY_5, byte)
        else:
            raise DecodeError('Unknown byte {}'.format(hex(byte)), ftdi_commands, last_byte=byte)

    if len(ftdi_replies) != 0:
        raise DecodeError('Leftover RX data, leftover = {}.'.format(len(ftdi_replies)), ftdi_commands)

    return ftdi_commands


class JtagState(Enum):
  RESET = 0
  RUN_IDLE = 1
  DRSELECT = 2
  DRCAPTURE = 3
  DRSHIFT = 4
  DREXIT1 = 5
  DRPAUSE = 6
  DREXIT2 = 7
  DRUPDATE = 9
  IRSELECT = 10
  IRCAPTURE = 11
  IRSHIFT = 12
  IREXIT1 = 13
  IRPAUSE = 14
  IREXIT2 = 15
  IRUPDATE = 16


# JtagState, TMS -> JtagState
JTAG_STATE_TABLE = {
        (JtagState.RESET, 1): JtagState.RESET,
        (JtagState.RESET, 0): JtagState.RUN_IDLE,
        (JtagState.RUN_IDLE, 0): JtagState.RUN_IDLE,
        (JtagState.RUN_IDLE, 1): JtagState.DRSELECT,
        # DR chain
        (JtagState.DRSELECT, 0): JtagState.DRCAPTURE,
        (JtagState.DRSELECT, 1): JtagState.IRSELECT,
        (JtagState.DRCAPTURE, 0): JtagState.DRSHIFT,
        (JtagState.DRCAPTURE, 1): JtagState.DREXIT1,
        (JtagState.DRSHIFT, 0): JtagState.DRSHIFT,
        (JtagState.DRSHIFT, 1): JtagState.DREXIT1,
        (JtagState.DREXIT1, 0): JtagState.DRPAUSE,
        (JtagState.DREXIT1, 1): JtagState.DRUPDATE,
        (JtagState.DRPAUSE, 0): JtagState.DRPAUSE,
        (JtagState.DRPAUSE, 1): JtagState.DREXIT2,
        (JtagState.DREXIT2, 0): JtagState.DRSHIFT,
        (JtagState.DREXIT2, 1): JtagState.DRUPDATE,
        (JtagState.DRUPDATE, 0): JtagState.RUN_IDLE,
        (JtagState.DRUPDATE, 1): JtagState.DRSELECT,
        # IR chain
        (JtagState.IRSELECT, 0): JtagState.IRCAPTURE,
        (JtagState.IRSELECT, 1): JtagState.RESET,
        (JtagState.IRCAPTURE, 0): JtagState.IRSHIFT,
        (JtagState.IRCAPTURE, 1): JtagState.IREXIT1,
        (JtagState.IRSHIFT, 0): JtagState.IRSHIFT,
        (JtagState.IRSHIFT, 1): JtagState.IREXIT1,
        (JtagState.IREXIT1, 0): JtagState.IRPAUSE,
        (JtagState.IREXIT1, 1): JtagState.IRUPDATE,
        (JtagState.IRPAUSE, 0): JtagState.IRPAUSE,
        (JtagState.IRPAUSE, 1): JtagState.IREXIT2,
        (JtagState.IREXIT2, 0): JtagState.IRSHIFT,
        (JtagState.IREXIT2, 1): JtagState.IRUPDATE,
        (JtagState.IRUPDATE, 0): JtagState.RUN_IDLE,
        (JtagState.IRUPDATE, 1): JtagState.DRSELECT,
        }

class ShiftRegister(object):
    def __init__(self, width):
        self.data = deque()
        self.width = width

        for _ in range(width):
            self.data.append(0)

    def shift(self, di):
        do = self.data.popleft()
        self.data.append(int(di))
        return do

    def load(self, data):
        for idx in range(self.width):
            self.data[idx] = 1 if (data & (1 << idx)) != 0 else 0

    def read(self):
        data = 0
        for idx, bit in enumerate(self.data):
            if bit:
                data |= (1 << idx)

        return data


class SinkRegister(object):
    """ Represents a DR that simply sinks the clocked in data forever.

    The CFG_IN register uses this type to forward the input bitstream directly
    to the configuration engine.
    """
    def __init__(self):
        self.data = []

    def shift(self, di):
        self.data.append(di)
        return 0

    def read(self):
        return self.data


class DrState(Enum):
    BYPASS = 0
    IDCODE = 1
    JTAG_CTRL = 2
    JTAG_STATUS = 3
    ABORT = 4
    DPACC = 5
    APACC = 6
    PS_IDCODE_DEVICE_ID = 7
    PMU_MDM = 8
    ERROR_STATUS = 9
    IP_DISABLE = 10
    UNKNOWN_STATE_9FF = 11
    USER1 = 12
    USER2 = 13
    USER3 = 14
    USER4 = 15
    CFG_OUT = 16
    CFG_IN = 17
    JPROGRAM = 18
    ISC_NOOP = 19
    FUSE_DNA = 20
    JSTART = 21


class UscaleDapModel(object):
    def __init__(self, dr_cb):
        self.ir = ShiftRegister(4)
        self.dr = None
        self.dr_cb = dr_cb

        # DAP states in BYPASS until enabled
        self.dap_state = DrState.BYPASS

        self.will_enable = True
        self.enabled = False

    def shift_dr(self, tdi):
        """ DR shift state has been entered, tdi is state of TDI pin, return state of TDO pin """
        return self.dr.shift(tdi)

    def shift_ir(self, tdi):
        """ IR shift state has been entered, tdi is state of TDI pin, return state of TDO pin """
        return self.ir.shift(tdi)

    def update_dr(self):
        """ DR update state has been entered. """
        dr = self.dr.read()
        self.dr_cb(self.dap_state, dr)

    def capture_ir(self):
        """ IR update state has been entered. """
        if DEBUG_JTAG_SIM:
            print('ARM DAP TAP IR = 0x01')
        self.ir.load(0x01)

    def capture_dr(self):
        """ DR capture state has been entered. """
        if DEBUG_JTAG_SIM:
            print('DAP TAP state = {}'.format(self.dap_state))
        if self.dap_state == DrState.BYPASS:
            self.dr = ShiftRegister(1)
            self.dr.load(0x0)
        elif self.dap_state == DrState.IDCODE:
            self.dr = ShiftRegister(32)
            self.dr.load(0x5ba00477)
        elif self.dap_state == DrState.ABORT:
            self.dr = ShiftRegister(35)
        elif self.dap_state == DrState.DPACC:
            self.dr = ShiftRegister(35)
        elif self.dap_state == DrState.APACC:
            self.dr = ShiftRegister(35)
        else:
            assert False, self.dap_state

    def update_ir(self):
        """ IR capture state has been entered. """
        ir = self.ir.read()
        if self.enable:
            if ir == 0b1000:
                self.dap_state = DrState.ABORT
            elif ir == 0b1010:
                self.dap_state = DrState.DPACC
            elif ir == 0b1011:
                # APACC
                self.dap_state = DrState.APACC
            elif ir == 0b1110:
                # IDCODE
                self.dap_state = DrState.IDCODE
            elif ir == 0b1111:
                self.dap_state = DrState.BYPASS
            else:
                assert False, hex(ir)
        else:
            self.dap_state = DrState.BYPASS

        if DEBUG_JTAG_SIM:
            print('ARM TAP IR = 0x{:01x}, state = {}'.format(ir, self.dap_state))

    def reset(self):
        if self.will_enable:
            self.enable = True
            self.dap_state = DrState.IDCODE
        else:
            self.enable = False
            self.dap_state = DrState.BYPASS

    def set_enable(self, enable):
        self.will_enable = enable

    def run_idle(self):
        """ Run-test/idle state has been entered. """
        pass


class UscalePsModel(object):
    def __init__(self, dap_model, dr_cb, ir_cb):
        self.dap_model = dap_model
        self.ir = ShiftRegister(12)
        self.captured_ir = None
        self.dr = None
        self.dr_state = DrState.IDCODE
        self.ir_cb = ir_cb
        self.dr_cb = dr_cb

    def shift_dr(self, tdi):
        """ DR shift state has been entered, tdi is state of TDI pin, return state of TDO pin """
        return self.dr.shift(tdi)

    def shift_ir(self, tdi):
        """ IR shift state has been entered, tdi is state of TDI pin, return state of TDO pin """
        return self.ir.shift(tdi)

    def update_dr(self):
        """ DR update state has been entered. """
        dr = self.dr.read()

        if self.dr_state == DrState.JTAG_CTRL:
            if dr & 0x2:
                self.dap_model.set_enable(True)

        self.dr_cb(self.dr_state, self.captured_ir, dr)

    def capture_ir(self):
        """ IR update state has been entered. """

        if DEBUG_JTAG_SIM:
            print('PS TAP IR = 0x01')
        self.ir.load(0x51)

    def capture_dr(self):
        """ DR capture state has been entered. """
        if DEBUG_JTAG_SIM:
            print('PS TAP state = {}'.format(self.dr_state))

        if self.dr_state == DrState.BYPASS:
            self.dr = ShiftRegister(1)
            self.dr.load(0x1)
        elif self.dr_state == DrState.IDCODE:
            self.dr = ShiftRegister(32)
            self.dr.load(0x14710093)
        elif self.dr_state == DrState.JTAG_CTRL:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.JTAG_STATUS:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.PS_IDCODE_DEVICE_ID:
            # This appears to never be read, so emit ir_cb
            self.ir_cb(DrState.PS_IDCODE_DEVICE_ID)
            self.dr = ShiftRegister(64)
            self.dr.load((0x14710093 << 32) | 0x0)
        elif self.dr_state == DrState.IP_DISABLE:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.USER1:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.USER2:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.USER3:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.USER4:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.CFG_OUT:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.CFG_IN:
            self.dr = SinkRegister()
        elif self.dr_state == DrState.JPROGRAM:
            # No DRCAPTURE with JPROGRAM
            assert False, self.dr_state
        elif self.dr_state == DrState.JSTART:
            # No DRCAPTURE with JPROGRAM
            assert False, self.dr_state
        elif self.dr_state == DrState.ISC_NOOP:
            # No DRCAPTURE with ISC_NOOP
            assert False, self.dr_state
        elif self.dr_state == DrState.PMU_MDM:
            self.dr = ShiftRegister(32)
        elif self.dr_state == DrState.ERROR_STATUS:
            self.dr = ShiftRegister(121)
        elif self.dr_state == DrState.FUSE_DNA:
            self.dr = ShiftRegister(96)
        else:
            assert False, self.dr_state

    def update_ir(self):
        """ IR update state has been entered. """
        raw_ir = self.ir.read()
        if DEBUG_JTAG_SIM:
            print('PS TAP, raw IR = 0x{:03x} High 6: 0x{:02x} Low 6: 0x{:02x}'.format(
                raw_ir, (raw_ir >> 6) & 0x3f, raw_ir & 0x3f))

        self.captured_ir = raw_ir
        ps_ir = (raw_ir >> 6) & 0x3f
        pl_ir = raw_ir & 0x3f

        if ps_ir == 0x9 and pl_ir == 0x9:
            self.dr_state = DrState.PS_IDCODE_DEVICE_ID
        elif ps_ir == 0x3f and pl_ir == 0x3f:
            self.dr_state = DrState.BYPASS
        elif ps_ir == 0x19 and pl_ir == 0x3f:
            self.dr_state = DrState.IP_DISABLE
        elif ps_ir == 0x27 and pl_ir == 0x3f:
            # This state is entered in the IR, but DRCAPTURE is never entered
            # with this IR.
            self.dr_state = DrState.UNKNOWN_STATE_9FF
            self.ir_cb(DrState.UNKNOWN_STATE_9FF)
        elif ps_ir == 0x24:
            # PL in control
            # Table 6-3 UltraScale FPGA Boundary-Scan Instructions from UG570
            # Page 97
            if pl_ir == 0b000010:
                self.dr_state = DrState.USER1
            elif pl_ir == 0b000011:
                self.dr_state = DrState.USER2
            elif pl_ir == 0b000100:
                self.dr_state = DrState.CFG_OUT
            elif pl_ir == 0b000101:
                self.dr_state = DrState.CFG_IN
            elif pl_ir == 0b001011:
                self.dr_state = DrState.JPROGRAM

                # Because there is no DRCAPTURE for JSTART, emit ir_cb
                self.ir_cb(DrState.JPROGRAM)
            elif pl_ir == 0b001100:
                self.dr_state = DrState.JSTART

                # Because there is no DRCAPTURE for JSTART, emit ir_cb
                self.ir_cb(DrState.JSTART)
            elif pl_ir == 0b010100:
                self.dr_state = DrState.ISC_NOOP

                # Because there is no DRCAPTURE for JSTART, emit ir_cb
                self.ir_cb(DrState.ISC_NOOP)
            elif pl_ir == 0b100010:
                self.dr_state = DrState.USER3
            elif pl_ir == 0b100011:
                self.dr_state = DrState.USER4
            elif pl_ir == 0b110010:
                # Table 8-3 eFUSE-Related JTAG Instructions from UG570
                # Page 133
                self.dr_state = DrState.FUSE_DNA
            else:
                assert False, hex(pl_ir)
        elif pl_ir == 0x24:
            # PS in control
            # Table 39-4 PS TAP Controller Instructions
            if ps_ir == 0x03:
                self.dr_state = DrState.PMU_MDM
            elif ps_ir == 0x19:
                self.dr_state = DrState.IP_DISABLE
            elif ps_ir == 0x1f:
                self.dr_state = DrState.JTAG_STATUS
            elif ps_ir == 0x20:
                self.dr_state = DrState.JTAG_CTRL
            elif ps_ir == 0x3e:
                self.dr_state = DrState.ERROR_STATUS
            else:
                assert False, hex(ps_ir)
        else:
            assert False, (hex(raw_ir), hex(ps_ir), hex(pl_ir))

        if DEBUG_JTAG_SIM:
            print('PS TAP IR = 0x{:03x}, state = {}'.format(raw_ir, self.dr_state))

    def reset(self):
        self.dr_state = DrState.IDCODE
        self.captured_ir = None

    def run_idle(self):
        """ Run-test/idle state has been entered. """
        pass


class JtagBypassModel(object):
    def __init__(self):
        pass

    def shift_dr(self, tdi):
        """ DR shift state has been entered, tdi is state of TDI pin, return state of TDO pin """
        return tdi

    def shift_ir(self, tdi):
        """ IR shift state has been entered, tdi is state of TDI pin, return state of TDO pin """
        return tdi

    def update_dr(self):
        """ DR update state has been entered. """
        pass

    def update_ir(self):
        """ IR update state has been entered. """
        pass

    def capture_dr(self):
        """ DR capture state has been entered. """
        pass

    def capture_ir(self):
        """ IR capture state has been entered. """
        pass

    def reset(self):
        """ Reset state has been entered. """
        pass

    def run_idle(self):
        """ Run-test/idle state has been entered. """
        pass


class JtagChain(object):
    def __init__(self, models):
        assert len(models) > 0
        self.models = models

    def shift_dr(self, tdi):
        for model in self.models:
            tdo = model.shift_dr(tdi)
            # Chain to next part.
            tdi = tdo

        return tdo

    def shift_ir(self, tdi):
        for model in self.models:
            tdo = model.shift_ir(tdi)
            # Chain to next part.
            tdi = tdo

        return tdo

    def update_dr(self):
        """ DR update state has been entered. """
        for model in self.models:
            model.update_dr()

    def update_ir(self):
        """ DR update state has been entered. """
        for model in self.models:
            model.update_ir()

    def capture_dr(self):
        """ DR update state has been entered. """
        for model in self.models:
            model.capture_dr()

    def capture_ir(self):
        """ DR update state has been entered. """
        for model in self.models:
            model.capture_ir()

    def reset(self):
        """ Reset state has been entered. """
        for model in self.models:
            model.reset()

    def run_idle(self):
        """ Run-test/idle state has been entered. """
        for model in self.models:
            model.run_idle()


class JtagFsm(object):
    def __init__(self, jtag_model):
        self.state = JtagState.RESET
        self.jtag_model = jtag_model
        self.last_tdo = 1
        self.pins_locked = True

    def get_state(self):
        return self.state

    def lock(self):
        self.pins_locked = True

    def unlock(self):
        self.pins_locked = False

    def clock(self, tdi, tms):
        assert not self.pins_locked
        next_state = JTAG_STATE_TABLE[self.state, tms]
        if DEBUG_JTAG_SIM:
            if self.state == JtagState.DRSHIFT and DEBUG_JTAG_SIM_DRSHIFT:
                print(self.state, tdi)
            elif self.state == JtagState.DRSHIFT and DEBUG_JTAG_SIM_IRSHIFT:
                print(self.state, tdi)
            else:
                print(self.state, tdi)


        if self.state == JtagState.RESET:
            self.jtag_model.reset()
        elif self.state == JtagState.RUN_IDLE:
            self.jtag_model.run_idle()
        elif self.state == JtagState.DRSHIFT:
            self.last_tdo = self.jtag_model.shift_dr(tdi)
        elif self.state == JtagState.DRUPDATE:
            self.jtag_model.update_dr()
        elif self.state == JtagState.DRCAPTURE:
            self.jtag_model.capture_dr()
        elif self.state == JtagState.IRSHIFT:
            self.last_tdo = self.jtag_model.shift_ir(tdi)
        elif self.state == JtagState.IRUPDATE:
            self.jtag_model.update_ir()
        elif self.state == JtagState.IRCAPTURE:
            self.jtag_model.capture_ir()

        self.state = next_state

        return self.last_tdo

TCK = 0
TDI = 1
TDO = 2
TMS = 3

def bits_to_bytes(in_bits):
    itr = iter(in_bits)
    bit_offset = 0
    byte = 0
    while True:
        try:
            bit = next(itr)
            if bit:
                byte |= 1 << bit_offset

            bit_offset += 1
            if bit_offset == 8:
                yield byte
                bit_offset = 0
                byte = 0
        except StopIteration:
            if bit_offset > 0:
                yield byte
            break

def run_ftdi_command(command, jtag_fsm):
    output = []

    if command.type == FtdiCommandType.CLOCK_TMS:
        assert FtdiFlags.LSB_FIRST in command.flags
        assert FtdiFlags.NEG_EDGE_OUT in command.flags
        assert FtdiFlags.BITWISE in command.flags

        reading = (command.opcode & FtdiCommandType.CLOCK_TDO.value != 0)
        if reading:
            assert FtdiFlags.NEG_EDGE_IN in command.flags

        assert command.length <= 7
        tdi = 1 if command.data[0] & 0x80 != 0 else 0
        for bit in range(command.length):
            tms = 1 if (command.data[0] & (1 << bit)) != 0 else 0
            tdo = jtag_fsm.clock(tdi=tdi, tms=tms)
            if reading:
                output.append(tdo)
    elif command.type == FtdiCommandType.CLOCK_TDI:
        assert FtdiFlags.LSB_FIRST in command.flags
        assert FtdiFlags.NEG_EDGE_OUT in command.flags

        reading = (command.opcode & FtdiCommandType.CLOCK_TDO.value != 0)
        if reading:
            assert FtdiFlags.NEG_EDGE_IN in command.flags

        # TMS is always low when clocking data?
        tms = 0

        if FtdiFlags.BITWISE in command.flags:
            assert command.length <= 7

            for bit in range(command.length):
                tdi = 1 if (command.data[0] & (1 << bit)) != 0 else 0
                tdo = jtag_fsm.clock(tdi=tdi, tms=tms)

                if reading:
                    output.append(tdo)
        else:
            for byte in command.data:
                for bit in range(8):
                    tdi = 1 if (byte & (1 << bit)) != 0 else 0
                    tdo = jtag_fsm.clock(tdi=tdi, tms=tms)

                    if reading:
                        output.append(tdo)
    elif command.type == FtdiCommandType.CLOCK_TDO:
        assert FtdiFlags.LSB_FIRST in command.flags
        assert FtdiFlags.NEG_EDGE_IN in command.flags

        # TDI is 1 when clocking TDO?
        tdi = 1

        # TMS is always low when clocking data?
        tms = 0

        if FtdiFlags.BITWISE in command.flags:
            assert command.length <= 7

            for bit in range(command.length):
                tdo = jtag_fsm.clock(tdi=tdi, tms=tms)
                output.append(tdo)
        else:
            for _ in range(command.length):
                for _ in range(8):
                    tdo = jtag_fsm.clock(tdi=tdi, tms=tms)
                    output.append(tdo)

    elif command.type == FtdiCommandType.SET_GPIO_LOW_BYTE:
        data, direction = command.data

        # Inputs
        if direction == 0:
            assert jtag_fsm.get_state() in [JtagState.RUN_IDLE, JtagState.RESET]
            jtag_fsm.lock()
            return

        assert (direction & (1 << TCK)) != 0, (hex(data), hex(direction))
        assert (direction & (1 << TDI)) != 0, (hex(data), hex(direction))
        assert (direction & (1 << TMS)) != 0, (hex(data), hex(direction))

        # Outputs
        assert (direction & (1 << TDO)) == 0, (hex(data), hex(direction))

        assert (data & (1 << TCK)) == 0, (hex(data), hex(direction))
        assert (data & (1 << TDI)) == 0, (hex(data), hex(direction))
        assert (data & (1 << TMS)) != 0, (hex(data), hex(direction))
        jtag_fsm.unlock()
    elif command.type == FtdiCommandType.CLOCK_NO_DATA:
        pass

    return tuple(bits_to_bytes(output))

class ArmDebugCommand(Enum):
    # irscan ABORT ; drscan 35 <value>
    ABORT = 0
    # dap apreg <ap_num> <reg>
    READ_AP_REGISTER = 1
    # dap apreg <ap_num> <reg> <value>
    WRITE_AP_REGISTER = 2
    # dap dpreg <reg>
    READ_DP_REGISTER = 3
    # dap dpreg <reg> <value>
    WRITE_DP_REGISTER = 4

# DPACC address map
# Table 2-6 DPv2 address map from
# ARM Debug Interface Architecture Specification ADIv5.0 to ADIv5.2
# Page 2-43
class ArmDpRegister(Enum):
    DPIDR = 0x0
    CTRL_STAT = 0x4
    DLCR = 0x14
    TARGETID = 0x24
    DLPIDR = 0x34
    EVENTSTAT = 0x44
    SELECT = 0x8
    RDBUFF = 0xC

class ArmDebugModel():
    def __init__(self, callback):
        self.callback = callback
        self.apsel = None
        self.apbanksel = 0
        self.dpbanksel = 0

    def dr_access(self, dr_state, dr_value):
        if dr_state == DrState.ABORT:
            self.callback(command=ArmDebugCommand.ABORT, value=dr_value)
        elif dr_state == DrState.IDCODE:
            pass
        elif dr_state == DrState.BYPASS:
            pass
        elif dr_state == DrState.APACC or dr_state == DrState.DPACC:
            RnW = dr_value & 0x1 != 0
            A = ((dr_value >> 1) & 0x3) << 2
            datain = (dr_value >> 3) & 0xFFFFFFFF

            # DPACC address map
            # Table 2-6 DPv2 address map from
            # ARM Debug Interface Architecture Specification ADIv5.0 to ADIv5.2
            # Page 2-43
            if dr_state == DrState.DPACC and A == 0x0:
                # DPIDR is RO
                assert RnW == 1, (dr_state, hex(dr_value))
                self.callback(command=ArmDebugCommand.READ_DP_REGISTER, reg=A)
            elif dr_state == DrState.DPACC and A == 0x8:
                # SELECT is WO
                assert RnW == 0, (dr_state, hex(dr_value))
                self.apsel = (datain >> 24)
                self.dpbanksel = datain & 0xF
                self.apbanksel = (datain >> 4) & 0xF
            elif dr_state == DrState.DPACC and A == 0x4:
                dpreg = (self.dpbanksel << 4) | A
                if RnW:
                    # Read
                    self.callback(command=ArmDebugCommand.READ_DP_REGISTER, reg=dpreg)
                else:
                    # Write
                    self.callback(command=ArmDebugCommand.WRITE_DP_REGISTER, reg=dpreg, value=datain)
            elif dr_state == DrState.DPACC and A == 0xC:
                # RDBUFF is RO
                assert RnW == 1, (dr_state, hex(dr_value))
                self.callback(command=ArmDebugCommand.READ_DP_REGISTER, reg=A)
            elif dr_state == DrState.APACC:
                apreg = (self.apbanksel << 4) | A
                if RnW:
                    # Read
                    self.callback(command=ArmDebugCommand.READ_AP_REGISTER, ap_num=self.apsel, reg=apreg)
                else:
                    # Write
                    self.callback(command=ArmDebugCommand.WRITE_AP_REGISTER, ap_num=self.apsel, reg=apreg, value=datain)
            else:
                assert False, (dr_state, hex(dr_value))
        else:
            assert False, (dr_state, hex(dr_value))

# Table 7-6 Summary of Memory Access Port (MEM-AP) registers from IHI0031C
class ArmMemApRegister(Enum):
    CSW = 0x0
    TAR = 0x4
    TAR_HIGH = 0x8
    DRW = 0xC
    BD0 = 0x10
    BD1 = 0x14
    BD2 = 0x18
    BD3 = 0x1C
    MBT = 0x20
    BASE = 0xF0
    CFG = 0xF4
    BASE_HIGH = 0xF8
    IDR = 0xFC

# Table 7-1 Summary of AddrInc field values from IHI0031C
class ArmMemApAutoIncrement(Enum):
    Off = 0b00
    Single = 0b01
    Packed = 0b10

class ArmMemApModel(object):
    def __init__(self):
        self.tar_low = None
        self.tar_high = 0x0
        self.width = None
        self.auto_increment = None

    def auto_increment_tar(self):
        assert self.tar_low is not None
        assert self.tar_high is not None
        assert self.width is not None
        assert self.auto_increment is not None

        if self.auto_increment == ArmMemApAutoIncrement.Off:
            return ''

        tar = (self.tar_high << 32) | self.tar_low

        if self.auto_increment == ArmMemApAutoIncrement.Single:
            tar += self.width
            msg = ', address auto-incremented by {}-bits'.format(self.width*8)
        elif self.auto_increment == ArmMemApAutoIncrement.Packed:
            raise NotImplementedError('Packed auto-increment not implemented')
        else:
            assert False, self.auto_increment

        self.tar_low = tar & 0xFFFFFFFF
        self.tar_high = (tar >> 32) & 0xFFFFFFFF

        return msg

    def read_banked(self, offset):
        assert self.tar_low is not None
        assert self.tar_high is not None
        assert self.width == 4
        assert offset in [0x0, 0x4, 0x8, 0xC]

        tar = (self.tar_high << 32) | self.tar_low
        address = tar & 0xFFFFFFF0 | offset
        if self.tar_high == 0:
            return 'Reading {}-bits from 0x{:08x}'.format(
                    self.width * 8, address
                    )
        else:
            return 'Reading {}-bits from 0x{:016x}'.format(
                    self.width * 8, address
                    )

    def write_banked(self, offset, value):
        assert self.tar_low is not None
        assert self.tar_high is not None
        assert self.width == 4
        assert offset in [0x0, 0x4, 0x8, 0xC]

        tar = (self.tar_high << 32) | self.tar_low
        address = tar & 0xFFFFFFF0 | offset
        if self.tar_high == 0:
            return 'Writing {}-bits from 0x{:08x} to 0x{:08x}'.format(
                    self.width * 8, address, value
                    )
        else:
            return 'Writing {}-bits from 0x{:016x} to 0x{:08x}'.format(
                    self.width * 8, address, value
                    )

    def read_register(self, reg):
        reg = ArmMemApRegister(reg)

        if reg == ArmMemApRegister.CSW:
            return 'Read MEM-AP CSW'
        elif reg == ArmMemApRegister.TAR:
            return 'Read MEM-AP TAR[31:0]'
        elif reg == ArmMemApRegister.TAR_HIGH:
            return 'Read MEM-AP TAR[63:32]'
        elif reg == ArmMemApRegister.DRW:
            assert self.tar_low is not None
            assert self.tar_high is not None
            assert self.width is not None
            assert self.auto_increment is not None

            if self.tar_high == 0:
                msg = 'Reading {}-bits from 0x{:08x}'.format(
                        self.width * 8, self.tar_low
                        )
            else:
                tar = (self.tar_high << 32) | self.tar_low

                msg = 'Reading {}-bits from 0x{:016x}'.format(
                        self.width * 8, tar
                        )

            msg += self.auto_increment_tar()
            return msg
        elif reg == ArmMemApRegister.BD0:
            return self.read_banked(0x0)
        elif reg == ArmMemApRegister.BD1:
            return self.read_banked(0x4)
        elif reg == ArmMemApRegister.BD2:
            return self.read_banked(0x8)
        elif reg == ArmMemApRegister.BD3:
            return self.read_banked(0xC)
        elif reg == ArmMemApRegister.MBT:
            raise NotImplementedError('MBT not implemented')
        elif reg == ArmMemApRegister.BASE:
            return 'Read MEM-AP BASE'
        elif reg == ArmMemApRegister.CFG:
            return 'Read MEM-AP CFG'
        elif reg == ArmMemApRegister.BASE_HIGH:
            return 'Read MEM-AP BASE_HIGH'
        elif reg == ArmMemApRegister.IDR:
            return 'Read MEM-AP IDR'
        else:
            assert False, reg

    def write_register(self, reg, value):
        reg = ArmMemApRegister(reg)

        if reg == ArmMemApRegister.CSW:
            # Table 7-2 Size field values when the MEM-AP supports different
            # access sizes from IHI0031C
            op_size = value & 0x7
            if op_size == 0b000:
                # 1 byte
                self.width = 1
            elif op_size == 0b001:
                # 2 bytes
                self.width = 2
            elif op_size == 0b010:
                # 4 bytes
                self.width = 4
            elif op_size == 0b011:
                # 8 bytes
                self.width = 8
            elif op_size == 0b100:
                # 16 bytes
                self.width = 16
            elif op_size == 0b101:
                # 32 bytes
                self.width = 32
            else:
                assert False, hex(op_size)

            self.auto_increment = ArmMemApAutoIncrement((value >> 4) & 0x3)

            mode = (value >> 8) & 0xF
            if mode != 0:
                raise NotImplementedError('Barrier support not implemented.')

        elif reg == ArmMemApRegister.TAR:
            self.tar_low = value
        elif reg == ArmMemApRegister.TAR_HIGH:
            self.tar_high = value
        elif reg == ArmMemApRegister.DRW:
            assert self.tar_low is not None
            assert self.tar_high is not None
            assert self.width is not None
            assert self.auto_increment is not None

            if self.tar_high == 0:
                msg = 'Writing {}-bits from 0x{:08x} to 0x{:08x}'.format(
                        self.width * 8, self.tar_low, value,
                        )
            else:
                tar = (self.tar_high << 32) | self.tar_low

                msg = 'Writing {}-bits from 0x{:016x} to 0x{:08x}'.format(
                        self.width * 8, tar, value
                        )

            msg += self.auto_increment_tar()

            return msg
        elif reg == ArmMemApRegister.BD0:
            return self.write_banked(0x0, value)
        elif reg == ArmMemApRegister.BD1:
            return self.write_banked(0x4, value)
        elif reg == ArmMemApRegister.BD2:
            return self.write_banked(0x8, value)
        elif reg == ArmMemApRegister.BD3:
            return self.write_banked(0xC, value)
        elif reg == ArmMemApRegister.MBT:
            raise NotImplementedError('MBT not implemented')
        elif reg == ArmMemApRegister.BASE:
            raise RuntimeError('Writing a RO register!')
        elif reg == ArmMemApRegister.CFG:
            raise RuntimeError('Writing a RO register!')
        elif reg == ArmMemApRegister.BASE_HIGH:
            raise RuntimeError('Writing a RO register!')
        elif reg == ArmMemApRegister.IDR:
            raise RuntimeError('Writing a RO register!')
        else:
            assert False, reg

# Table 7-6 Summary of Memory Access Port (MEM-AP) registers from IHI0031C
class ArmJtagApRegister(Enum):
    CSW = 0x0
    PSEL = 0x04
    PSTA = 0x08
    BxFIFO1 = 0x10
    BxFIFO2 = 0x14
    BxFIFO3 = 0x18
    BxFIFO4 = 0x1C
    IDR = 0xFC

class ArmJtagApModel(object):
    def __init__(self):
        pass

    def read_register(self, reg):
        reg = ArmJtagApRegister(reg)

        if reg == ArmJtagApRegister.CSW:
            return 'Read JTAG-AP CSW'
        elif reg == ArmJtagApRegister.IDR:
            return 'Read JTAG-AP IDR'
        else:
            raise NotImplementedError('Most of JTAG-AP not implemented')

    def write_register(self, reg, value):
        reg = ArmJtagApRegister(reg)

        if reg == ArmJtagApRegister.CSW:
            return 'Write JTAG-AP CSW = 0x{:08x}'.format(value)
        elif reg == ArmJtagApRegister.PSEL:
            return 'Write JTAG-AP PSEL = 0x{:08x}'.format(value)
        else:
            raise NotImplementedError('Most of JTAG-AP not implemented')

class DapOutputGroupers(object):
    """ Outputs OpenOCD TCL to specified file and groups by result.

    The access point models in self.arm_aps do not always generate a "result",
    for example writing an MEM-AP TAR (target address) register is not really
    a result, it is just part of setting up the "real" transaction.

    This class groups TCL commands into actions, with a comment at the top of
    a command block indicating what the following commands are doing, and then
    the TCL follows.

    """
    def __init__(self, f):
        self.f = f
        self.lines = []
        self.ap_names = ["MEM-AP AXI", "MEM-AP Debug", "JTAG-AP"]
        self.arm_aps = [ArmMemApModel(), ArmMemApModel(), ArmJtagApModel()]

    def openocd_dap_callback(self, command, value=None, reg=None, ap_num=None):
        if command == ArmDebugCommand.ABORT:
            print('irscan $_CHIPNAME.tap [dap_ir ABORT]', file=self.f)
            print('drscan $_CHIPNAME.tap 35 0x{:09x}'.format(value+0), file=self.f)
            print(file=self.f)
        elif command == ArmDebugCommand.READ_AP_REGISTER:
            result = self.arm_aps[ap_num].read_register(reg)

            # Group lines until a result is produced (e.g. a memory
            # read or write).
            self.lines.append('set ap_reg_value [$_CHIPNAME.dap apreg {:d} 0x{:02x}]'.format(ap_num+0, reg+0))

            if result is not None:
                # We know what the DAP result was, add comment as header,
                # and write out lines
                print('# {}: {}'.format(self.ap_names[ap_num], result), file=self.f)
                for l in self.lines:
                    print(l, file=self.f)
                print(file=self.f)

                self.lines = []
        elif command == ArmDebugCommand.WRITE_AP_REGISTER:
            result = self.arm_aps[ap_num].write_register(reg, value)

            # Group lines until a result is produced (e.g. a memory
            # read or write).
            self.lines.append('$_CHIPNAME.dap apreg {:d} 0x{:02x} 0x{:08x}'.format(ap_num+0, reg+0, value+0))

            if result is not None:
                # We know what the DAP result was, add comment as header,
                # and write out lines
                print('# {}: {}'.format(self.ap_names[ap_num], result), file=self.f)
                for l in self.lines:
                    print(l, file=self.f)
                print(file=self.f)
        elif command == ArmDebugCommand.READ_DP_REGISTER:
            print('# Reading {}'.format(ArmDpRegister(reg).name), file=self.f)
            print('set dp_reg_value [$_CHIPNAME.dap dpreg 0x{:02x}]'.format(reg+0), file=self.f)
            print(file=self.f)
        elif command == ArmDebugCommand.WRITE_DP_REGISTER:
            print('# Writing {} = 0x{:08x}'.format(ArmDpRegister(reg).name, value+0), file=self.f)
            print('$_CHIPNAME.dap dpreg 0x{:02x} 0x{:08x}'.format(reg+0, value+0), file=self.f)
            print(file=self.f)
        else:
            assert False, (command, value, reg, ap_num)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json_pcap', required=True, help='Input JSON PCAP data')
    parser.add_argument('--ftdi_commands', help='Output of FTDI commands')
    parser.add_argument('--openocd_script', help='Output of OpenOCD script', required=True)

    args = parser.parse_args()

    ftdi_bytes = Buffer()
    ftdi_replies = Buffer()
    print('Loading data')
    with open(args.json_pcap) as f:
        for frame_idx, cap in enumerate(json.load(f)):
            protocol = cap.get('_source', {}).get('layers', {}).get('frame', {}).get('frame.protocols')
            if protocol != 'usb:ftdift':
                continue

            tx_data = cap.get('_source', {}).get('layers', {}).get('ftdift', {}).get('ftdift.if_a_tx_payload')
            if tx_data is not None:
                ftdi_bytes.extend((int(byte, 16) for byte in tx_data.split(':')), frame=frame_idx+1)

            rx_data = cap.get('_source', {}).get('layers', {}).get('ftdift', {}).get('ftdift.if_a_rx_payload')
            if rx_data is not None:

                in_data = tuple(int(byte, 16) for byte in rx_data.split(':'))
                idx = 0
                out_data = []
                while len(in_data) - idx >= FTDI_MAX_PACKET_SIZE:
                    out_data.extend(in_data[idx:idx+FTDI_MAX_PACKET_SIZE])
                    idx += FTDI_MAX_PACKET_SIZE
                    # Modem status is next two bytes, skip them
                    idx += 2

                if idx < len(in_data):
                    assert len(in_data) - idx < FTDI_MAX_PACKET_SIZE
                    out_data.extend(in_data[idx:])

                ftdi_replies.extend(out_data, frame=frame_idx+1)

    print('Parsing data')
    try:
        ftdi_commands = decode_commands(ftdi_bytes, ftdi_replies)
    except DecodeError as e:
        print('Failed decode at:', hex(e.get_last_byte()))
        print('Next TX frame', ftdi_bytes.current_frame())
        print('Next RX frame', ftdi_replies.current_frame())
        print('Context:')
        C = 50
        for offset, context_bytes in ftdi_bytes.get_context(C=C):
            print(offset+1, hex(context_bytes))

        # Find previous two flushes to get sufficient context
        if e.commands is not None:
            flush_count = 0
            for idx, cmd in enumerate(e.commands[::-1]):
                if cmd.type == FtdiCommandType.FLUSH:
                    flush_count += 1
                    if flush_count == 2:
                        break

            idx += 1

            print('Last {} commands (2 flushes backward):'.format(idx))
            for cmd in e.commands[-idx:]:
                print(cmd)
        raise

    if args.ftdi_commands:
        print('Writing FTDI commands to disk')
        with open(args.ftdi_commands, 'w') as f:
            outputs = []
            for cmd in ftdi_commands:
                o = cmd._asdict()
                o['type'] = cmd.type.name
                if cmd.flags is not None:
                    o['flags'] = [flag.name for flag in cmd.flags]

                for k in cmd._asdict():
                    if o[k] is None:
                        del o[k]

                outputs.append(o)

            json.dump(outputs, f, indent=2)


    with open(args.openocd_script, 'w') as f:

        dap_output = DapOutputGroupers(f)
        arm_debug_model = ArmDebugModel(dap_output.openocd_dap_callback)

        def dap_callback(dr_state, dr_value):
            arm_debug_model.dr_access(dr_state, dr_value)

        def ps_dr_callback(dr_state, ir_value, dr_value):
            if dr_state != DrState.CFG_IN and dr_state != DrState.BYPASS and dr_state != DrState.IDCODE:
                # DR for CFG_IN is the entire bitstream!
                # Don't print that!
                print('# PS/PL IR value = {} TAP state = {} DR = 0x{:08x}'.format('0x{:03x}'.format(ir_value) if ir_value is not None else ir_value, dr_state, dr_value), file=f)

            if dr_state == DrState.BYPASS:
                return
            elif dr_state == DrState.IDCODE:
                print('irscan $_CHIPNAME.ps [ps_ir IDCODE]', file=f)
                print('set idcode [drscan $_CHIPNAME.ps 32]', file=f)
            elif dr_state == DrState.PMU_MDM:
                print('irscan $_CHIPNAME.ps [ps_ir PMU_MDM]', file=f)
                print('set old_pmu_mdm [drscan $_CHIPNAME.ps 32 0x{:08x}]'.format(dr_value), file=f)
            elif dr_state == DrState.JTAG_STATUS:
                print('irscan $_CHIPNAME.ps [ps_ir JTAG_STATUS]', file=f)
                print('set jtag_status [drscan $_CHIPNAME.ps 32 0x{:08x}]'.format(dr_value), file=f)
            elif dr_state == DrState.JTAG_CTRL:
                print('irscan $_CHIPNAME.ps [ps_ir JTAG_CTRL]', file=f)
                print('set old_jtag_ctrl [drscan $_CHIPNAME.ps 32 0x{:08x}]'.format(dr_value), file=f)
            elif dr_state == DrState.ERROR_STATUS:
                print('irscan $_CHIPNAME.ps [ps_ir ERROR_STATUS]', file=f)
                print('set error_status [drscan $_CHIPNAME.ps 121 0x{:033x}]'.format(dr_value), file=f)
            elif dr_state == DrState.IP_DISABLE:
                print('irscan $_CHIPNAME.ps [ps_ir IP_DISABLE]', file=f)
                print('set ip_disable_value [drscan $_CHIPNAME.ps 32 0x{:08x}]'.format(dr_value), file=f)
            elif dr_state == DrState.USER1:
                print('irscan $_CHIPNAME.ps [pl_ir USER1]', file=f)
                print('set user1_value [drscan $_CHIPNAME.ps 32 0x{:08x}]'.format(dr_value), file=f)
            elif dr_state == DrState.USER2:
                print('irscan $_CHIPNAME.ps [pl_ir USER2]', file=f)
                print('set user2_value [drscan $_CHIPNAME.ps 32 0x{:08x}]'.format(dr_value), file=f)
            elif dr_state == DrState.USER3:
                print('irscan $_CHIPNAME.ps [pl_ir USER3]', file=f)
                print('set user3_value [drscan $_CHIPNAME.ps 32 0x{:08x}]'.format(dr_value), file=f)
            elif dr_state == DrState.FUSE_DNA:
                print('irscan $_CHIPNAME.ps [pl_ir FUSE_DNA]', file=f)
                print('set user3_value [drscan $_CHIPNAME.ps 96 0x{:024x}]'.format(dr_value), file=f)
            elif dr_state == DrState.CFG_OUT:
                print('irscan $_CHIPNAME.ps [pl_ir CFG_OUT]', file=f)
                print('set user3_value [drscan $_CHIPNAME.ps 32 0x{:08x}]'.format(dr_value), file=f)
            elif dr_state == DrState.CFG_IN:
                print('# PS/PL TAP state = {}'.format(dr_state), file=f)
                print('# Bitstream command bit len = {}'.format(len(dr_value)), file=f)
                print('pld load 0 xxx.bit', file=f)

                if PRINT_BITSTREAM:
                    for idx, byte in enumerate(bits_to_bytes(dr_value)):
                        if idx % 16 == 0:
                            print('{:04x}'.format(idx), end=' ')

                        print('{:02x}'.format(byte), end=' ')

                        if (idx % 16) == 15:
                            print()

            else:
                assert False, dr_state

            print(file=f)

        def ps_ir_callback(dr_state):
            print('# PL TAP state = {}'.format(dr_state.name), file=f)
            if dr_state == DrState.PS_IDCODE_DEVICE_ID:
                print('irscan $_CHIPNAME.ps 0x249', file=f)
            elif dr_state == DrState.UNKNOWN_STATE_9FF:
                print('irscan $_CHIPNAME.ps 0x9ff', file=f)
            else:
                print('irscan $_CHIPNAME.ps [pl_ir {}]'.format(dr_state.name), file=f)
            print(file=f)

        dap_model = UscaleDapModel(dr_cb=dap_callback)
        ps_model = UscalePsModel(dap_model, ir_cb=ps_ir_callback, dr_cb=ps_dr_callback)
        jtag_model = JtagChain(models=[ps_model, dap_model])
        jtag_fsm = JtagFsm(jtag_model)

        print('Running JTAG simulation')
        for idx, cmd in enumerate(ftdi_commands):
            if DEBUG_JTAG_SIM:
                print('{: 8d} {:24s} opcode=0x{:02x} cf={: 8d} l={}'.format(
                    idx,
                    cmd.type.name,
                    cmd.opcode,
                    cmd.command_frame,
                    cmd.length))

                if cmd.type == FtdiCommandType.FLUSH:
                    for _ in range(3):
                        print('*** FLUSH ***')
                if cmd.flags is not None:
                    print('Flags: [{}]'.format(', '.join(flag.name for flag in cmd.flags)))
                if cmd.data is not None:
                    print('Command: {}'.format(':'.join('{:02x}'.format(b) for b in cmd.data)))

            output = run_ftdi_command(cmd, jtag_fsm)

            if DEBUG_JTAG_SIM:
                if cmd.reply is not None:
                    print('Real Reply(rf={: 8d}): {}'.format(cmd.reply_frame, ':'.join('{:02x}'.format(b) for b in cmd.reply)))
                    print(' Sim Reply    {:8s} : {}'.format('', ':'.join('{:02x}'.format(b) for b in output)))


if __name__ == "__main__":
    main()
