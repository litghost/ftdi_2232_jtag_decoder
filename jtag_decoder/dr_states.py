""" Registry of JTAG TAP instruction states. """

from enum import Enum


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

