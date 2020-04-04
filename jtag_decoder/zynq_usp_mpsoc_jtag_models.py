from .registers import ShiftRegister, SinkRegister
from .dr_states import DrState
from .jtag_models import JtagChain
from .arm_jtag_models import ArmDebugCommand, ArmDpRegister, ArmMemApModel, ArmJtagApModel, ArmDapJtagModel

class ZynqPsJtagModel(object):
    def __init__(self, dap_model, dr_cb, ir_cb, verbose=False):
        self.dap_model = dap_model
        self.ir = ShiftRegister(12)
        self.captured_ir = None
        self.dr = None
        self.dr_state = DrState.IDCODE
        self.ir_cb = ir_cb
        self.dr_cb = dr_cb
        self.verbose = verbose

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

        if self.verbose:
            print('PS TAP IR = 0x01')
        self.ir.load(0x51)

    def capture_dr(self):
        """ DR capture state has been entered. """
        if self.verbose:
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
        if self.verbose:
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

        if self.verbose:
            print('PS TAP IR = 0x{:03x}, state = {}'.format(raw_ir, self.dr_state))

    def reset(self):
        self.dr_state = DrState.IDCODE
        self.captured_ir = None

    def run_idle(self):
        """ Run-test/idle state has been entered. """
        pass


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

class ZynqJtagModel(object):
    def __init__(self, ps_ir_cb, ps_dr_cb, dap_dr_cb, initial_will_enable=False, verbose=False):
        self.dap_model = ArmDapJtagModel(
                dr_cb=dap_dr_cb,
                initial_will_enable=initial_will_enable,
                verbose=verbose)
        self.ps_model = ZynqPsJtagModel(dap_model=self.dap_model, ir_cb=ps_ir_cb, dr_cb=ps_dr_cb, verbose=verbose)
        self.jtag_model = JtagChain(models=[self.ps_model, self.dap_model])

    def model(self):
        return self.jtag_model
