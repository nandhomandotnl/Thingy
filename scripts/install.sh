#!/bin/bash
# Installs thingy-mouse on a Raspberry Pi (Raspberry Pi OS Bookworm or newer). Run with sudo.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo ./scripts/install.sh" >&2
    exit 1
fi

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
INSTALL_DIR=/opt/thingy-mouse

echo "==> Installing packages"
apt-get update
apt-get install -y python3-venv python3-pip bluez

echo "==> Enabling USB gadget mode (dwc2)"
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt
if ! grep -q '^dtoverlay=dwc2' "$CONFIG"; then
    printf '\n[all]\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$CONFIG"
    NEED_REBOOT=1
fi
grep -qx 'libcomposite' /etc/modules || echo libcomposite >> /etc/modules

echo "==> Installing Python app into $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install "$REPO_DIR"
install -m 755 "$REPO_DIR/scripts/usb_gadget.sh" "$INSTALL_DIR/usb_gadget.sh"
[ -f /etc/default/thingy-mouse ] || install -m 644 "$REPO_DIR/systemd/thingy-mouse.default" /etc/default/thingy-mouse

echo "==> Installing systemd services"
install -m 644 "$REPO_DIR/systemd/thingy-usb-gadget.service" /etc/systemd/system/
install -m 644 "$REPO_DIR/systemd/thingy-mouse.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable thingy-usb-gadget.service thingy-mouse.service

if [ "${NEED_REBOOT:-0}" = 1 ]; then
    echo "==> Done. Reboot now (sudo reboot) to activate USB gadget mode."
else
    systemctl restart thingy-usb-gadget.service thingy-mouse.service
    echo "==> Done. Follow the logs with: journalctl -u thingy-mouse -f"
fi
