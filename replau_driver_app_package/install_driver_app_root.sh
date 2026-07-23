#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="/opt/replau_driver_app"
ENV_FILE="/etc/replau-driver-app.env"
SERVICE_FILE="/etc/systemd/system/replau-driver-app.service"
DB_NAME="${REPLAU_DB_NAME:-localapi}"
APP_USER="${REPLAU_APP_USER:-guill}"
APP_GROUP="${REPLAU_APP_GROUP:-guill}"

echo "[1/7] Applying database migration to ${DB_NAME}"
TMP_SQL="$(mktemp /tmp/replau_driver_app_phase1.XXXXXX.sql)"
trap 'rm -f "${TMP_SQL}"' EXIT
cp "${SRC_DIR}/add_driver_app_phase1.sql" "${TMP_SQL}"
chmod 644 "${TMP_SQL}"
runuser -u postgres -- psql -d "${DB_NAME}" -v ON_ERROR_STOP=1 -f "${TMP_SQL}"

echo "[2/7] Installing app files into ${APP_DIR}"
mkdir -p "${APP_DIR}"
cp "${SRC_DIR}/replau_driver_app.py" "${SRC_DIR}/requirements.txt" "${APP_DIR}/"
chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"

echo "[3/7] Creating/updating virtualenv"
runuser -u "${APP_USER}" -- python3 -m venv "${APP_DIR}/.venv"
runuser -u "${APP_USER}" -- "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "[4/7] Writing env file if missing"
if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${SRC_DIR}/.env.example" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
fi

echo "[5/7] Installing systemd unit"
cp "${SRC_DIR}/replau-driver-app.service" "${SERVICE_FILE}"
systemctl daemon-reload

echo "[6/7] Enabling and starting replau-driver-app"
systemctl enable --now replau-driver-app

echo "[7/7] Health check"
sleep 1
curl -fsS http://127.0.0.1:8797/health
echo
systemctl status replau-driver-app --no-pager
