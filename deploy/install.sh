#!/bin/bash
# Run on the server as root, from anywhere: bash /opt/packet-social-web/deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

command -v ssh-keygen >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq openssh-client; }
id packet >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin packet
install -d -o packet -g packet -m 750 /var/lib/packet
install -d -m 755 /var/www/packet
install -m 644 index.html /var/www/packet/index.html
install -m 644 deploy/packet.service /etc/systemd/system/packet.service
install -m 644 deploy/packet-backup.service /etc/systemd/system/packet-backup.service
install -m 644 deploy/packet-backup.timer /etc/systemd/system/packet-backup.timer
install -m 644 deploy/Caddyfile /etc/caddy/Caddyfile

caddy validate --config /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable packet.service packet-backup.timer
systemctl restart packet.service
systemctl start packet-backup.timer
systemctl reload caddy
sleep 2
systemctl is-active packet.service caddy packet-backup.timer
curl -s http://127.0.0.1:8080/status
