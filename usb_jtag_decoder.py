import argparse
import json
from jtag_decoder.jtag_fsm import JtagFsm
from jtag_decoder.jtag_sim import run_ftdi_command
from jtag_decoder.ftdi_decoder import FtdiCommandType, DecodeError, decode_commands
from jtag_decoder.pcap_reader import pcap_json_reader


class DummyJtagModel(object):
    def __init__(self):
        pass

    def shift_dr(self, tdi):
        return 1

    def shift_ir(self, tdi):
        return 1

    def update_dr(self):
        pass

    def update_ir(self):
        pass

    def capture_dr(self):
        pass

    def capture_ir(self):
        pass

    def reset(self):
        pass

    def run_idle(self):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json_pcap', required=True, help='Input JSON PCAP data')
    parser.add_argument('--ftdi_commands', help='Output of FTDI commands')
    parser.add_argument('--print_transitions', action='store_true')
    parser.add_argument('--print_dr_shift', action='store_true')
    parser.add_argument('--print_ir_shift', action='store_true')

    args = parser.parse_args()

    print('Loading data')
    with open(args.json_pcap) as f:
        ftdi_bytes, ftdi_replies = pcap_json_reader(f)

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

    jtag_fsm = JtagFsm(
            DummyJtagModel(),
            print_transitions=args.print_transitions,
            print_dr_shift=args.print_dr_shift,
            print_ir_shift=args.print_ir_shift)

    print('Running JTAG simulation')
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
