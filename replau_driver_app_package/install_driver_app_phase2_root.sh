#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${REPLAU_DRIVER_APP_DIR:-/opt/replau_driver_app}"
DB_NAME="${REPLAU_DB_NAME:-localapi}"
APP_USER="${REPLAU_APP_USER:-guill}"
APP_GROUP="${REPLAU_APP_GROUP:-guill}"

echo "[1/5] Applying driver app Phase 2 migration to ${DB_NAME}"
TMP_SQL="$(mktemp /tmp/replau_driver_app_phase2.XXXXXX.sql)"
trap 'rm -f "${TMP_SQL}"' EXIT
cp "${SRC_DIR}/add_driver_app_phase2.sql" "${TMP_SQL}"
chmod 644 "${TMP_SQL}"
runuser -u postgres -- psql -d "${DB_NAME}" -v ON_ERROR_STOP=1 -f "${TMP_SQL}"

echo "[2/5] Installing updated app file into ${APP_DIR}"
install -o "${APP_USER}" -g "${APP_GROUP}" -m 0644 "${SRC_DIR}/replau_driver_app.py" "${APP_DIR}/replau_driver_app.py"

echo "[3/5] Restarting replau-driver-app"
systemctl restart replau-driver-app

echo "[4/5] Reloading PostgREST schema cache"
runuser -u postgres -- psql -d "${DB_NAME}" -v ON_ERROR_STOP=1 -c "NOTIFY pgrst, 'reload schema';"
sleep 1

echo "[5/5] Verifying local health"
curl -fsS http://127.0.0.1:8797/health
echo

echo
echo "Driver dispatch admin route:"
echo "http://127.0.0.1:8797/ops/driver-dispatch"
echo
echo "Public driver route:"
echo "http://localhost/driver"
