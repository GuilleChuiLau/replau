#!/usr/bin/env bash
set -euo pipefail

PRODUCT_ENV=${PRODUCT_ENV:-"$HOME/.config/replau/product.env"}
PRODUCT_ADMIN_URL=${PRODUCT_ADMIN_URL:-http://127.0.0.1:8794}
ASSET_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets/products"

if [[ ! -r $PRODUCT_ENV ]]; then
  echo "Product environment file not readable: $PRODUCT_ENV" >&2
  exit 1
fi

ADMIN_TOKEN=$(sed -n 's/^ADMIN_TOKEN=//p' "$PRODUCT_ENV" | head -n1)
if [[ -z $ADMIN_TOKEN ]]; then
  echo "ADMIN_TOKEN is missing from $PRODUCT_ENV" >&2
  exit 1
fi

uploaded=0
for image in "$ASSET_DIR"/product-*.webp; do
  [[ -e $image ]] || continue
  id=${image##*/product-}
  id=${id%.webp}
  if [[ ! $id =~ ^[0-9]+$ ]]; then
    echo "Skipping unexpected asset name: $image" >&2
    continue
  fi
  status=$(curl -sS -o /dev/null -w '%{http_code}' \
    -X POST \
    -H "X-Admin-Token: $ADMIN_TOKEN" \
    -F "image=@${image};type=image/webp" \
    "$PRODUCT_ADMIN_URL/product/$id/image")
  if [[ $status != 303 ]]; then
    echo "Image upload failed for product $id (HTTP $status)" >&2
    exit 1
  fi
  uploaded=$((uploaded + 1))
done

unset ADMIN_TOKEN
echo "PRODUCT_IMAGES_INSTALLED=$uploaded"
