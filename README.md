# ftdi\_2232\_jtag\_decoder
Decodes USB wireshark data into JTAG command layer (and higher)

## Basic usage (USB PCAP to JTAG)

Use wireshark to export parsed FTDI frames from a USB capture to JSON
(File -> Export Packet Dissections -> As JSON ...).  `usb_jtag_decoder.py`
takes input as `--json_pcap <input file>`. `usb_jtag_decoder.py` can generate
the following output:
 - `--ftdi_commands <output JSON>`
 - `--jtag_state_transitions <output JSON>`

This decoder assumes FTDI chip has the JTAG interface on interface A, with
pin 0 as TCK, pin 1 as TDI, pin 2 as TDO, and pin 3 as TMS.
