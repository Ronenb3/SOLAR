#!/bin/bash
# ============================================================
# SOLAR PI HOTSPOT SETUP (NetworkManager version)
# ============================================================
# For Raspberry Pi OS Bookworm and newer
#
# Network: SolarPi
# Password: suntrack26
# Pi IP:    10.42.0.1
#
# To SSH from Mac:  ssh techenergy3@10.42.0.1
# ============================================================

set -e

echo "======================================"
echo "  Solar Pi — WiFi Hotspot Setup"
echo "  (NetworkManager version)"
echo "======================================"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo:"
    echo "  sudo bash setup_hotspot_nm.sh"
    exit 1
fi

SSID="SolarPi"
PASS="suntrack26"
PI_IP="10.42.0.1"

echo "[1/3] Checking NetworkManager..."
if ! command -v nmcli &> /dev/null; then
    echo "Installing NetworkManager..."
    apt-get update -qq
    apt-get install -y network-manager
fi

echo "[2/3] Creating hotspot connection..."
# Delete old hotspot if it exists
nmcli connection delete SolarPi 2>/dev/null || true

# Create the hotspot
nmcli connection add \
    type wifi \
    ifname wlan0 \
    con-name SolarPi \
    autoconnect yes \
    ssid "$SSID" \
    wifi.mode ap \
    wifi.band bg \
    wifi.channel 7 \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$PASS" \
    ipv4.method shared \
    ipv4.addresses "$PI_IP/24"

echo "[3/3] Activating hotspot..."
nmcli connection up SolarPi

echo ""
echo "======================================"
echo "  HOTSPOT IS LIVE!"
echo "======================================"
echo ""
echo "  WiFi Network:  $SSID"
echo "  WiFi Password: $PASS"
echo "  Pi IP Address: $PI_IP"
echo ""
echo "  On your Mac:"
echo "    1. Connect to WiFi '$SSID'"
echo "    2. ssh techenergy3@$PI_IP"
echo ""
echo "  To undo: sudo nmcli connection delete SolarPi"
echo "======================================"
