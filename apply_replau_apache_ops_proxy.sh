#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONF_SRC="${SCRIPT_DIR}/replau_apache_ops_proxy.conf"
CONF_DST="/etc/apache2/conf-available/replau-ops-proxy.conf"
HTPASSWD_FILE="/etc/apache2/.replau-ops.htpasswd"

sudo a2enmod proxy proxy_http headers rewrite
if [[ ! -f "${HTPASSWD_FILE}" ]]; then
    echo "Missing ${HTPASSWD_FILE}; create it with: sudo htpasswd -c ${HTPASSWD_FILE} memo" >&2
    exit 1
fi
sudo chown root:www-data "${HTPASSWD_FILE}"
sudo chmod 0640 "${HTPASSWD_FILE}"
sudo install -m 0644 -o root -g root "$CONF_SRC" "$CONF_DST"
sudo a2enconf replau-ops-proxy
sudo apache2ctl configtest
sudo systemctl reload apache2

echo "Replau Apache ops proxy enabled."
TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
if [[ -n "${TAILSCALE_IP}" ]]; then
    echo "Try: http://${TAILSCALE_IP}/dashboard"
    echo "Try: http://${TAILSCALE_IP}/picking"
    echo "Try: http://${TAILSCALE_IP}/delivery"
    echo "Try: http://${TAILSCALE_IP}/kitchen/"
else
    echo "Tailscale IPv4 address unavailable; local URL: http://127.0.0.1/dashboard"
fi
