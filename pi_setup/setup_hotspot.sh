#!/bin/bash
# ============================================================
# SOLAR PI HOTSPOT SETUP
# ============================================================
# This script turns the Raspberry Pi into a WiFi access point.
# After running, your Mac can connect to the Pi's own WiFi
# and SSH directly — no router needed.
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
echo "======================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo:"
    echo "  sudo bash setup_hotspot.sh"
    exit 1
fi

SSID="SolarPi"
PASS="suntrack26"
PI_IP="10.42.0.1"
INTERFACE="wlan0"

echo "[1/5] Installing hotspot packages..."
apt-get update -qq
apt-get install -y hostapd dnsmasq

echo "[2/5] Stopping services during setup..."
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

echo "[3/5] Configuring static IP..."
# Backup and configure dhcpcd
cp /etc/dhcpcd.conf /etc/dhcpcd.conf.backup.$(date +%s)
if ! grep -q "# SOLAR HOTSPOT CONFIG" /etc/dhcpcd.conf; then
    cat >> /etc/dhcpcd.conf << EOF

# SOLAR HOTSPOT CONFIG
interface $INTERFACE
    static ip_address=$PI_IP/24
    nohook wpa_supplicant
EOF
fi

echo "[4/5] Configuring hostapd (WiFi access point)..."
cat > /etc/hostapd/hostapd.conf << EOF
interface=$INTERFACE
driver=nl80211
ssid=$SSID
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=$PASS
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
EOF

# Point hostapd to config
sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd 2>/dev/null || true

echo "[5/5] Configuring DHCP server..."
cp /etc/dnsmasq.conf /etc/dnsmasq.conf.backup.$(date +%s) 2>/dev/null || true
cat > /etc/dnsmasq.conf << EOF
interface=$INTERFACE
dhcp-range=10.42.0.10,10.42.0.50,255.255.255.0,24h
domain=solar.local
address=/solarpi.local/$PI_IP
EOF

echo ""
echo "Enabling services..."
systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq

echo ""
echo "Restarting networking..."
systemctl restart dhcpcd
sleep 2
systemctl start hostapd
systemctl start dnsmasq

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
echo "  NOTE: The Pi is no longer on Solar2."
echo "  To undo, run: sudo bash undo_hotspot.sh"
echo "======================================"
