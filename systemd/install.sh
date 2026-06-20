#!/bin/bash
# Install and enable AutoTrader systemd services and timers.
# Run as root: sudo bash systemd/install.sh

set -e

SYSTEMD_DIR="/etc/systemd/system"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Create log directory
mkdir -p /home/ubuntu/AutoTrader/logs
chown ubuntu:ubuntu /home/ubuntu/AutoTrader/logs

# Copy service and timer files
for f in "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.timer; do
    echo "Installing $f"
    cp "$f" "$SYSTEMD_DIR/"
done

# Reload systemd
systemctl daemon-reload

# Enable and start timers
for timer in autotrader-pre-market autotrader-intraday autotrader-post-market; do
    systemctl enable "${timer}.timer"
    systemctl start "${timer}.timer"
    echo "✓ ${timer}.timer enabled and started"
done

echo ""
echo "Installation complete. Timer status:"
systemctl list-timers autotrader-* --no-pager
