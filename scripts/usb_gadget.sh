#!/bin/bash
# Turn the Raspberry Pi's USB OTG port into a USB mouse (HID gadget) -> creates /dev/hidg0.
# Needs dtoverlay=dwc2 in config.txt (scripts/install.sh sets that up). Run as root.
set -euo pipefail

G=/sys/kernel/config/usb_gadget/thingymouse

modprobe libcomposite
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config

if [ -d "$G" ]; then
    echo "Gadget already configured"
    exit 0
fi

mkdir -p "$G"
cd "$G"
echo 0x1d6b > idVendor   # Linux Foundation
echo 0x0104 > idProduct  # Multifunction Composite Gadget
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "thingy52mouse0001" > strings/0x409/serialnumber
echo "Thingy Mouse"      > strings/0x409/manufacturer
echo "Thingy:52 Air Mouse" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "Mouse" > configs/c.1/strings/0x409/configuration
echo 100 > configs/c.1/MaxPower

mkdir -p functions/hid.usb0
echo 2 > functions/hid.usb0/protocol   # mouse
echo 1 > functions/hid.usb0/subclass   # boot interface
echo 4 > functions/hid.usb0/report_length
# 3 buttons + relative X, Y, wheel (int8 each). Must match REPORT_FORMAT in thingy_mouse/mouse.py.
printf '\x05\x01\x09\x02\xa1\x01\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x03\x15\x00\x25\x01\x95\x03\x75\x01\x81\x02\x95\x01\x75\x05\x81\x03\x05\x01\x09\x30\x09\x31\x09\x38\x15\x81\x25\x7f\x75\x08\x95\x03\x81\x06\xc0\xc0' \
    > functions/hid.usb0/report_desc

ln -s functions/hid.usb0 configs/c.1/

UDC=$(ls /sys/class/udc | head -n1)
if [ -z "$UDC" ]; then
    echo "No USB device controller found. Is dtoverlay=dwc2 set and did you reboot?" >&2
    exit 1
fi
echo "$UDC" > UDC
echo "USB mouse gadget enabled on $UDC"
