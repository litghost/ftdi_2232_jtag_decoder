from .ftdi_decoder import FtdiCommandType, FtdiFlags
from .jtag_fsm import JtagState
from .utils import bits_to_bytes

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

    return tuple(bits_to_bytes(output))

