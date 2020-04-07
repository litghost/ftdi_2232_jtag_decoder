from enum import Enum
from .registers import ShiftRegister
from .dr_states import DrState

class ArmDapJtagModel(object):
    """ Model ARM DAP JTAG TAP.

    Documentation can be found in:
      Table 3-209: JTAG-DP register summary from ARM DDI0480F
      Table 2-6: DPv2 address map from ARM IHI0031C
      Section 6.2: Selecting and accessing an AP from ARM IHI0031C

    Parameters
    ----------
    dr_cb : Callable, arguments are DrState Enum and value of DR register.
        This callback will be invoked whenever the DRUPDATE JTAG state is entered
        with the state of the IR register in the DrState Enum and the current
        value stored in the DR shift register.

        The DR shift register will be the correct width based on the value of
        DrState.

    initial_will_enable : bool
        If true, then the ARM DAP will initialize upon entering the RESET JTAG
        state, otherwise it will remain disabled.  In the disabled state, the
        IR is permanently setting to BYPASS.

    verbose : bool
        If true, some prints to stdout will be enabled indicating the behavior
        of the ARM DAP.

    """
    def __init__(self, dr_cb, initial_will_enable=False, verbose=False):
        self.ir = ShiftRegister(4)
        self.dr = None
        self.dr_cb = dr_cb
        self.verbose = verbose

        # DAP states in BYPASS until enabled
        self.dap_state = DrState.BYPASS

        self.will_enable = initial_will_enable
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
        if self.verbose:
            print('ARM DAP IR = 0x01')
        self.ir.load(0x01)

    def capture_dr(self):
        """ DR capture state has been entered. """
        if self.verbose:
            print('ARM DAP state = {}'.format(self.dap_state))

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

        if self.verbose:
            print('ARM DAP IR = 0x{:01x}, state = {}'.format(ir, self.dap_state))

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
