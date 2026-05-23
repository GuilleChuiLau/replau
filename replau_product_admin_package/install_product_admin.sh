#!/usr/bin/env bash
set -euo pipefail
APP_DIR="/opt/replau_product_admin"
ENV_FILE="/etc/replau-product-admin.env"
echo "Installing Replau Product Admin UI into $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER:$USER" "$APP_DIR"
cp replau_product_admin.py requirements.txt replau-product-admin.service test_product_admin.sh "$APP_DIR/"
cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x replau_product_admin.py test_product_admin.sh
if [ ! -f "$ENV_FILE" ]; then
  cat <<'EOF' | sudo tee "$ENV_FILE" >/dev/null
POSTGREST_BASE_URL=http://127.0.0.1:3000
ADMIN_HOST=127.0.0.1
ADMIN_PORT=8794
REQUEST_TIMEOUT=10
REQUIRE_ADMIN_TOKEN=true
ADMIN_TOKEN=CHANGE_ME_TO_LONG_RANDOM_TOKEN
PRODUCTS_ENDPOINT=productos
PRICES_ENDPOINT=producto_precios
DEFAULT_MONEDA=PEN
DEFAULT_UNIDAD=UNIDAD
EOF
fi
sudo chmod 600 "$ENV_FILE"
sudo chown root:root "$ENV_FILE"
sudo cp "$APP_DIR/replau-product-admin.service" /etc/systemd/system/replau-product-admin.service
sudo systemctl daemon-reload
sudo systemctl enable replau-product-admin
sudo systemctl restart replau-product-admin
echo
echo "Installed."
echo "Product Admin UI: http://127.0.0.1:8794"
echo "Run: sudo systemctl status replau-product-admin --no-pager"
echo "Run: curl http://127.0.0.1:8794/health | jq"
