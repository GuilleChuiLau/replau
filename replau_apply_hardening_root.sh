#!/usr/bin/env bash
set -euo pipefail

# Applies prepared Replau hardening changes that require root/systemd access.
# Safe intent:
# - Run new admin/ops services as guill instead of root.
# - Enable token requirements for localhost admin dashboards.
# - Reload/restart only affected Replau services.

install -m 0644 /opt/replau_ops/replau-health-dashboard.service /etc/systemd/system/replau-health-dashboard.service
install -m 0644 /opt/replau_ops/replau-stuck-monitor.service /etc/systemd/system/replau-stuck-monitor.service
install -m 0644 /opt/replau_product_admin/replau-product-admin.service /etc/systemd/system/replau-product-admin.service
install -m 0644 /opt/replau_payment_proof_review/replau-payment-proof-review.service /etc/systemd/system/replau-payment-proof-review.service

python3 - <<'PY'
from pathlib import Path
import secrets

files = [
    (Path('/etc/replau-ops.env'), 'REQUIRE_OPS_TOKEN', 'OPS_TOKEN'),
    (Path('/etc/replau-product-admin.env'), 'REQUIRE_ADMIN_TOKEN', 'ADMIN_TOKEN'),
    (Path('/etc/replau-payment-proof-review.env'), 'REQUIRE_REVIEW_TOKEN', 'REVIEW_TOKEN'),
]
for path, require_key, token_key in files:
    text = path.read_text() if path.exists() else ''
    lines = text.splitlines()
    found_require = False
    found_token = False
    out = []
    for line in lines:
        if line.startswith(require_key + '='):
            out.append(require_key + '=true')
            found_require = True
        elif line.startswith(token_key + '='):
            current = line.split('=', 1)[1].strip()
            if not current or current.startswith('CHANGE_ME'):
                current = secrets.token_urlsafe(32)
            out.append(token_key + '=' + current)
            found_token = True
        else:
            out.append(line)
    if not found_require:
        out.append(require_key + '=true')
    if not found_token:
        out.append(token_key + '=' + secrets.token_urlsafe(32))
    path.write_text('\n'.join(out) + '\n')
PY

chown root:root /etc/replau-ops.env /etc/replau-product-admin.env /etc/replau-payment-proof-review.env
chmod 600 /etc/replau-ops.env /etc/replau-product-admin.env /etc/replau-payment-proof-review.env

chown -R guill:guill /opt/replau_ops /opt/replau_product_admin /opt/replau_payment_proof_review

systemctl daemon-reload
systemctl restart replau-health-dashboard.service replau-product-admin.service replau-payment-proof-review.service
systemctl restart replau-stuck-monitor.timer replau-daily-backup.timer

systemctl --no-pager --full status replau-health-dashboard.service replau-product-admin.service replau-payment-proof-review.service | sed -n '1,120p'

printf '\nUnauthenticated HTTP status checks (should be 401 when token enforcement is active):\n'
for p in 8793 8794 8795; do
  printf '%s ' "$p"
  curl -sS -m 5 -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:${p}/" || true
done
