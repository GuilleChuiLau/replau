#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

ENV_FILE="${REPLAU_DRIVER_ENV_FILE:-/etc/replau-driver-app.env}"
NGINX_SITE="${REPLAU_NGINX_SITE:-/etc/nginx/sites-available/replau-public-edge}"
APP_SERVICE="${REPLAU_DRIVER_SERVICE:-replau-driver-app}"
TOKEN_BYTES="${REPLAU_DRIVER_TOKEN_BYTES:-32}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing env file: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -f "${NGINX_SITE}" ]]; then
  echo "Missing nginx site: ${NGINX_SITE}" >&2
  exit 1
fi

echo "[1/5] Enabling driver admin token auth"
ADMIN_TOKEN_VALUE="$(
  python3 - "${ENV_FILE}" "${TOKEN_BYTES}" <<'PY'
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
token_bytes = int(sys.argv[2])
lines = path.read_text().splitlines()
values = {}
for line in lines:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()

token = values.get("ADMIN_TOKEN", "")
if not token or token == "change-me":
    token = secrets.token_urlsafe(token_bytes)

updates = {
    "REQUIRE_ADMIN_TOKEN": "true",
    "ADMIN_TOKEN": token,
}

seen = set()
out = []
for line in lines:
    if "=" not in line or line.lstrip().startswith("#"):
        out.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
print(token)
PY
)"
chmod 600 "${ENV_FILE}"

echo "[2/5] Patching nginx public edge for /driver and /api/driver only"
BACKUP="${NGINX_SITE}.bak-$(date +%Y%m%d-%H%M%S)-driver-app"
cp "${NGINX_SITE}" "${BACKUP}"

python3 - "${NGINX_SITE}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

upstream = """upstream replau_driver_app_public {
    server 127.0.0.1:8796 max_fails=3 fail_timeout=10s;
    keepalive 16;
}

"""

if "upstream replau_driver_app_public" not in text:
    marker = "server {\n"
    if marker not in text:
        raise SystemExit("Could not find server block marker")
    text = text.replace(marker, upstream + marker, 1)

locations = """    # Public driver onboarding app. Admin ops routes are intentionally not exposed.
    location = /driver {
        limit_req zone=replau_public burst=30 nodelay;
        limit_except GET POST { deny all; }
        proxy_pass http://replau_driver_app_public;
    }

    location ^~ /driver/ {
        limit_req zone=replau_public burst=30 nodelay;
        limit_except GET POST { deny all; }
        proxy_pass http://replau_driver_app_public;
    }

    location ^~ /api/driver {
        limit_req zone=replau_public burst=60 nodelay;
        limit_except GET POST { deny all; }
        proxy_pass http://replau_driver_app_public;
    }

"""

if "replau_driver_app_public" in text and "location ^~ /api/driver" not in text:
    marker = """    # Public menu and product media.
"""
    if marker not in text:
        raise SystemExit("Could not find public menu marker")
    text = text.replace(marker, locations + marker, 1)

path.write_text(text)
PY

echo "[3/5] Testing nginx config"
/usr/sbin/nginx -t

echo "[4/5] Restarting driver app and reloading nginx"
systemctl restart "${APP_SERVICE}"
systemctl reload nginx

echo "[5/5] Verifying local health"
curl -fsS http://127.0.0.1:8796/health
echo

echo
echo "Driver public route:"
echo "http://localhost/driver"
echo
echo "Local admin ops route:"
echo "http://127.0.0.1:8796/ops/drivers?token=${ADMIN_TOKEN_VALUE}"
echo
echo "Admin token:"
echo "${ADMIN_TOKEN_VALUE}"
echo
echo "nginx backup:"
echo "${BACKUP}"
