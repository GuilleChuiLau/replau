#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/replau_payment_proof_review"
ENV_FILE="/etc/replau-payment-proof-review.env"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "1) Applying SQL payment-proof upgrade..."
sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi < add_payment_proof_flow.sql

echo "2) Installing review UI into $APP_DIR..."
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER:$USER" "$APP_DIR"
cp replau_payment_proof_review.py requirements.txt replau-payment-proof-review.service test_payment_proof_flow.sh "$APP_DIR/"

cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x replau_payment_proof_review.py test_payment_proof_flow.sh

if [ ! -f "$ENV_FILE" ]; then
  cat <<'EOF' | sudo tee "$ENV_FILE" >/dev/null
POSTGREST_BASE_URL=http://127.0.0.1:3000
APP_HOST=127.0.0.1
APP_PORT=8795
REQUEST_TIMEOUT=10
REQUIRE_REVIEW_TOKEN=true
REVIEW_TOKEN=CHANGE_ME_TO_LONG_RANDOM_TOKEN
EOF
fi

sudo chmod 600 "$ENV_FILE"
sudo chown root:root "$ENV_FILE"

sudo cp "$APP_DIR/replau-payment-proof-review.service" /etc/systemd/system/replau-payment-proof-review.service
sudo systemctl daemon-reload
sudo systemctl enable replau-payment-proof-review
sudo systemctl restart replau-payment-proof-review

echo
echo "Installed Payment Proof Review UI."
echo "Open: http://127.0.0.1:8795"
echo
echo "Test:"
echo "curl http://127.0.0.1:8795/health | jq"
