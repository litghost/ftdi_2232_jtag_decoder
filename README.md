# ftdi\_2232\_jtag\_decoder
Decodes USB wireshark data into JTAG command layer (and higher)

## Basic usage (USB PCAP to JTAG)

Use wireshark to export parsed FTDI frames from a USB capture to JSON
(File -> Export Packet Dissections -> As JSON ...).  `usb_jtag_decoder.py`
takes input as `--json_pcap <input file>`. `usb_jtag_decoder.py` can generate
the following output:
 - `--ftdi_commands <output JSON>`
 - `--openocd_script <output TCL>`

This decoder assumes FTDI chip has the JTAG interface on interface A, with
pin 0 as TCK, pin 1 as TDI, pin 2 as TDO, and pin 3 as TMS.

### USB capture on Linux

Recent versions of Wireshark (3.2.1+) can directly capture from USB if enabled. See
https://wiki.wireshark.org/CaptureSetup/USB

Wireshark needs to know which USB bus the JTAG programmer is connected too.
`lsusb` can be used to both reports device names and their attached bus.

### USB capture on Windows

USB Pcap (https://desowin.org/usbpcap/) can be used to generate Wireshark
pcanp files.  Simply install and run USBPcapCMD and follow the prompts.
Select the USB device that represents the FTDI programmer.  After the capture
is complete, use a recent version of Wireshark (3.2.1+) to open the file and
output the decoded frames per the earlier instructions.
