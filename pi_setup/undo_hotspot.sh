#!/bin/bash
# Undo the hotspot setup and reconnect to Solar2 WiFi
set -e

echo "Reverting hotspot configuration..."

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo: sudo bash undo_hotspot.sh"
    exit 1
fi

systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true
systemctl disable hostapd 2>/dev/null || true
systemctl disable dnsmasq 2>/dev/null || true

# Remove hotspot config from dhcpcd
sed -i '/# SOLAR HOTSPOT CONFIG/,$ d' /etc/dhcpcd.conf

systemctl restart dhcpcd
systemctl restart wpa_supplicant

echo ""
echo "Done! The Pi should reconnect to Solar2 WiFi."
echo "You may need to reboot: sudo reboot"
