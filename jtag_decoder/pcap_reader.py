from .buffer import Buffer
import json

# It appears that if more than FTDI_MAX_PACKET_SIZE is returned in a reply,
# the modem status is repeated. followed by the data bytes.
#
# This should've been handled in Wireshark, but the decoder likely has a bug.
FTDI_MAX_PACKET_SIZE = 512

def pcap_json_reader(fin):
    ftdi_bytes = Buffer()
    ftdi_replies = Buffer()

    for frame_idx, cap in enumerate(json.load(fin)):
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

    return ftdi_bytes, ftdi_replies
