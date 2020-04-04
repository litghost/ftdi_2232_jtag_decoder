import argparse
import json
from jtag_decoder.jtag_fsm import JtagFsm
from jtag_decoder.jtag_sim import run_ftdi_command
from jtag_decoder.ftdi_decoder import FtdiCommandType, DecodeError, decode_commands
from jtag_decoder.buffer import Buffer
from jtag_decoder.arm_jtag_models import ArmDebugModel
from jtag_decoder.zynq_usp_mpsoc_jtag_models import ZynqJtagModel, DapOutputGroupers
from jtag_decoder.dr_states import DrState
from jtag_decoder.utils import bits_to_bytes


# It appears that if more than FTDI_MAX_PACKET_SIZE is returned in a reply,
# the modem status is repeated. followed by the data bytes.
#
# This should've been handled in Wireshark, but the decoder likely has a bug.
FTDI_MAX_PACKET_SIZE = 512

# All debugging statements, other than DRSHIFT and IRSHIFT.
DEBUG_JTAG_SIM = False

# If DEBUG_JTAG_SIM is True, prints DRSHIFT transitions as well
DEBUG_JTAG_SIM_DRSHIFT = False

# If DEBUG_JTAG_SIM is True, prints IRSHIFT transitions as well
DEBUG_JTAG_SIM_IRSHIFT = False

# Bitstreams are big and generate a lot of output, so by default they are
# not printed.  Set PRINT_BITSTREAM to True to dump the bitstream, if found.
PRINT_BITSTREAM = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json_pcap', required=True, help='Input JSON PCAP data')
    parser.add_argument('--ftdi_commands', help='Output of FTDI commands')
    parser.add_argument('--openocd_script', help='Output of OpenOCD script', required=True)
    parser.add_argument('--dap_enabled_at_start', help='Set if in the capture, the ARM DAP was already enabled', action='store_true')

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

        jtag_model = ZynqJtagModel(
                ps_ir_cb=ps_ir_callback,
                ps_dr_cb=ps_dr_callback,
                dap_dr_cb=dap_callback,
                initial_will_enable=args.dap_enabled_at_start,
                verbose=DEBUG_JTAG_SIM)
        jtag_fsm = JtagFsm(
                jtag_model.model(),
                print_transitions=DEBUG_JTAG_SIM,
                print_dr_shift=DEBUG_JTAG_SIM_DRSHIFT,
                print_ir_shift=DEBUG_JTAG_SIM_IRSHIFT)

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
