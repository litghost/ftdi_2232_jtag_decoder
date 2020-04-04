from enum import Enum

class JtagState(Enum):
  """ Set of JTAG states. """
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


# JTAG state transition table, maps:
#  (current JtagState, TMS value) -> next JtagState
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


class JtagFsm(object):
    def __init__(self, jtag_model, print_transitions=False, print_dr_shift=False, print_ir_shift=False):
        """ JTAG finite state machine.

        Simulates a JTAG chain bit by bit when method clock is invoked.
        Following transitions are emitted to the provide jtag_model:

         - jtag_model.reset() when JtagState.RESET is entered
         - jtag_model.run_idle() when JtagState.RUN_IDLE is entered
         - jtag_model.shift_dr(tdi) when JtagState.DRSHIFT is entered
           Return from jtag_model.shift_dr method is returned as the TDO
           state.
         - jtag_model.update_dr() when JtagState.DRSHIFT is entered
         - jtag_model.capture_dr() when JtagState.DRCAPTURE is entered
         - jtag_model.shift_ir(tdi) when JtagState.IRSHIFT is entered
           Return from jtag_model.shift_ir method is returned as the TDO
           state.
         - jtag_model.update_ir() when JtagState.IRSHIFT is entered
         - jtag_model.capture_ir() when JtagState.IRCAPTURE is entered

        Optional debug prints can be enabled with:
         print_transitions - Print all state transitions, except for DRSHIFT
                             and IRSHIFT.
         print_dr_shift - Print DRSHIFT state transitions (if print_transitions
                          is set)
         print_ir_shift - Print IRSHIFT state transitions (if print_transitions
                          is set)

        """
        self.state = JtagState.RESET
        self.jtag_model = jtag_model
        self.last_tdo = 1
        self.pins_locked = True
        self.print_transitions = print_transitions
        self.print_dr_shift = print_dr_shift
        self.print_ir_shift = print_ir_shift

    def get_state(self):
        return self.state

    def lock(self):
        self.pins_locked = True

    def unlock(self):
        self.pins_locked = False

    def clock(self, tdi, tms):
        assert not self.pins_locked
        next_state = JTAG_STATE_TABLE[self.state, tms]
        if self.print_transitions:
            if self.state == JtagState.DRSHIFT and self.print_dr_shift:
                print(self.state, tdi)
            elif self.state == JtagState.DRSHIFT and self.print_ir_shift:
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
