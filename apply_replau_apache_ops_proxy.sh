#!/usr/bin/env bash
set -euo pipefail

CONF_SRC="/home/guill/codex/replau_apache_ops_proxy.conf"
CONF_DST="/etc/apache2/conf-available/replau-ops-proxy.conf"

sudo a2enmod proxy proxy_http headers rewrite
sudo install -m 0644 -o root -g root "$CONF_SRC" "$CONF_DST"
sudo a2enconf replau-ops-proxy
sudo apache2ctl configtest
sudo systemctl reload apache2

echo "Replau Apache ops proxy enabled."
echo "Try: http://100.94.36.88/dashboard"
echo "Try: http://100.94.36.88/picking"
echo "Try: http://100.94.36.88/delivery"
echo "Try: http://100.94.36.88/kitchen/"
