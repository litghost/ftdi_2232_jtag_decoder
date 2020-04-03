import argparse
from collections import namedtuple, deque
from enum import Enum
import json

HEX_DISPLAY = False

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


        if HEX_DISPLAY:
            command_opcode = hex(command_opcode)
            if data is not None:
                data = tuple(hex(b) for b in data)
            if reply is not None:
                reply = tuple(hex(b) for b in reply)

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


class DrState(Enum):
    BYPASS = 0
    IDCODE = 1
    JTAG_CTRL = 2
    JTAG_STATUS = 3
    ABORT = 4
    DPACC = 5
    APACC = 6
    PS_IDCODE_DEVICE_ID = 7
    PMU_MBM = 8
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


class UscaleDapModel(object):
    def __init__(self, dap_cb):
        self.ir = ShiftRegister(4)
        self.dr = None
        self.dap_cb = dap_cb

        # DAP states in BYPASS until enabled
        self.dap_state = DrState.BYPASS

        self.will_enable = False
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
        self.dap_cb(self.dap_state, dr)

    def capture_ir(self):
        """ IR update state has been entered. """
        print('ARM DAP TAP IR = 0x01')
        self.ir.load(0x01)

    def capture_dr(self):
        """ DR capture state has been entered. """
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
            if ir == 0x1:
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
                # All other IR's are BYPASS
                print('ARM TAP IR *** UNKNOWN! ***, setting BYPASS')
                self.dap_state = DrState.BYPASS
        else:
            self.dap_state = DrState.BYPASS

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


class UscalePsModel(object):
    def __init__(self, dap_model, ps_callback):
        self.dap_model = dap_model
        self.ir = ShiftRegister(12)
        self.captured_ir = None
        self.dr = None
        self.dr_state = DrState.IDCODE
        self.ps_callback = ps_callback

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

        self.ps_callback(self.dr_state, self.capture_ir, dr)

    def capture_ir(self):
        """ IR update state has been entered. """
        print('PS TAP IR = 0x01')
        self.ir.load(0x51)

    def capture_dr(self):
        """ DR capture state has been entered. """
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
            # TODO: This is static!
            self.dr.load(0x6000467a)
        else:
            assert False, self.dr_state

    def update_ir(self):
        """ IR update state has been entered. """
        raw_ir = self.ir.read()
        print('PS TAP, raw IR = 0x{:03x} High 6: 0x{:02x} Low 6: 0x{:02x}'.format(
            raw_ir, (raw_ir >> 6) & 0x3f, raw_ir & 0x3f))
        self.captured_ir = raw_ir
        ir = (raw_ir >> 6) & 0x3f
        if False:
            pass
        #if ir == 0x00:
        #    # Reserved
        #    assert False, hex(ir)
        #elif ir == 0x03:
        #    # PMU_MDM
        #    assert False, hex(ir)
        #elif ir == 0x08:
        #    # USERCODE
        #    assert False, hex(ir)
        elif ir == 0x09:
            # IDCODE
            self.dr_state = DrState.IDCODE
        #elif ir == 0x0A:
        #    # HIGHZ
        #    assert False, hex(ir)
        #elif ir == 0x19:
        #    # IP_DISABLE
        #    assert False, hex(ir)
        elif ir == 0x1F:
            # JTAG_STATUS
            self.dr_state = DrState.JTAG_STATUS
        elif ir == 0x20:
            self.dr_state = DrState.JTAG_CTRL
        elif ir == 0x24:
            print('PS TAP, weird JTAG_CTRL???')
            self.dr_state = DrState.JTAG_CTRL
        #elif ir == 0x26:
        #    # EXTEST
        #    assert False, hex(ir)
        elif ir == 0x3F:
            self.dr_state = DrState.BYPASS
        else:
            self.dr_state = DrState.BYPASS

        print('PS TAP IR = 0x{:03x}, state = {}'.format(ir, self.dr_state))

    def reset(self):
        self.dr_state = DrState.IDCODE
        self.captured_ir = None


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
        print(self.state, tdi)

        if self.state == JtagState.RESET:
            self.jtag_model.reset()
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

    output_bytes = []
    itr = iter(output)
    bit_offset = 0
    byte = 0
    while True:
        try:
            bit = next(itr)
            if bit:
                byte |= 1 << bit_offset

            bit_offset += 1
            if bit_offset == 8:
                output_bytes.append(byte)
                bit_offset = 0
                byte = 0
        except StopIteration:
            if bit_offset > 0:
                output_bytes.append(byte)

            break

    return output_bytes

FTDI_MAX_PACKET_SIZE = 512

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json_pcap', required=True, help='Input JSON PCAP data')
    parser.add_argument('--ftdi_commands', help='Output of FTDI commands')

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

    def dap_callback(dr_state, dr_value):
        print('ARM DAP TAP state = {} DR = 0x{:09x}'.format(dr_state, dr_value))

    def ps_callback(dr_state, ir_value, dr_value):
        print('PS TAP state = {} DR = 0x{:08x}'.format(dr_state, dr_value))
        if ir_value is not None:
            print('PS TAP, raw IR = 0x{:03x} High 6: 0x{:02x} Low 6: 0x{:02x}'.format(
                ir_value,
                (ir_value >> 6) & 0x3f,
                ir_value & 0x3f))


    dap_model = UscaleDapModel(dap_callback)
    ps_model = UscalePsModel(dap_model, ps_callback)
    jtag_model = JtagChain(models=[ps_model, dap_model])
    jtag_fsm = JtagFsm(jtag_model)

    for idx, cmd in enumerate(ftdi_commands):
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
        if cmd.reply is not None:
            print('Real Reply(rf={: 8d}): {}'.format(cmd.reply_frame, ':'.join('{:02x}'.format(b) for b in cmd.reply)))
            print(' Sim Reply    {:8s} : {}'.format('', ':'.join('{:02x}'.format(b) for b in output)))


if __name__ == "__main__":
    main()
