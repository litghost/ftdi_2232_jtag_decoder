# ftdi\_2232\_jtag\_decoder
Decodes USB wireshark data into JTAG command layer (and higher)

## Basic usage (USB PCAP to JTAG)

Use wireshark to export parsed FTDI frames from a USB capture to JSON
(File -> Export Packet Dissections -> As JSON ...).  `usb_jtag_decoder.py`
takes input as `--json_pcap <input file>`. `usb_jtag_decoder.py` can generate
the following output:
 - Output decoded FTDI commands to JSON with `--ftdi_commands <output JSON>`
 - Print the state of the JTAG simulation with the following flags:
    - `--print_transitions` - Print JTAG transitions, except for DRSHIFT and
      IRSHIFT.
    - `--print_dr_shift` -- Print JTAG DRSHIFT transitions if
      `--print_transitions` is supplied.
    - `--print_ir_shift` -- Print JTAG IRSHIFT transitions if
      `--print_transitions` is supplied.

This decoder assumes FTDI chip has the JTAG interface on interface A, with
pin 0 as TCK, pin 1 as TDI, pin 2 as TDO, and pin 3 as TMS.

## JTAG models

Simply converting JTAG signals into JTAG state transitions is not immediately
useful on its own.  However to decode any further requires a particular JTAG
chain to be modelled.  This repository provides a simple model of the Zynq
UltraScale+ MPSoC JTAG chain.  This model can by invoking
`usb_jtag_zynq_mpsoc_decoder.py`.  The `--json_pcap` argument is the same as
before.  The output of the decoder is sent to the file specified by 
`--openocd_script`.   This output is an approximation of an OpenOCD script
issuing commands to the target.

## Notes on USB capture

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
