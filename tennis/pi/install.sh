#!/bin/bash
# Installs tennis-tracker on a Raspberry Pi (Raspberry Pi OS Bookworm or newer). Run with sudo.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo ./pi/install.sh" >&2
    exit 1
fi

SRC=$(cd "$(dirname "$0")/.." && pwd)
APP=/opt/tennis-tracker
DATA=/var/lib/tennis-tracker

apt-get update
apt-get install -y python3-venv bluez

python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --upgrade pip
"$APP/venv/bin/pip" install "$SRC"

mkdir -p "$DATA"
[ -f /etc/default/tennis-tracker ] || install -m 644 "$SRC/pi/tennis-tracker.default" /etc/default/tennis-tracker
install -m 644 "$SRC/pi/tennis-tracker.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable tennis-tracker.service

echo
echo "Klaar. Volgende stappen:"
echo "  1. Zoek je sensor:       sudo $APP/venv/bin/tennis-tracker scan"
echo "  2. Kalibreer (eenmalig): cd $DATA && sudo $APP/venv/bin/tennis-tracker calibrate --sensor Tennis-XXXX"
echo "  3. Pas /etc/default/tennis-tracker aan (sensor, speler, laptop-adres)"
echo "  4. Start:                sudo systemctl restart tennis-tracker && journalctl -u tennis-tracker -f"
