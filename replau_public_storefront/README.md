# Replau Public Storefront

Customer-facing menu and cart for `orders.replau.com`. It reads active
`TERMINADO` products and prices from local PostgREST, builds a cart in the
browser, and opens WhatsApp with the exact multiline format accepted by the
Replau bridge.

The storefront groups products into customer-facing categories, supports
category filters and text search, and generates short descriptions from the
catalog code. Product images uploaded through Product Admin appear
automatically through the storefront's restricted image proxy; until an image
is uploaded, a category-specific icon is shown.

The generated food-styling set is stored under `assets/products/`. Restore or
refresh all Product Admin images with:

```bash
./install_generated_product_images.sh
```

The installer reads the local Product Admin token without printing or copying
it into the repository.

## Public boundary

Only the storefront, token-protected `/track/*`, and the tracking route API are
published through Cloudflare Tunnel. Product Admin, PostgREST, Logistics
dashboard, `/order/*`, picking, delivery controls, and other staff APIs must
remain private.

## Local service

1. Copy `.env.example` to `~/.config/replau/storefront.env` and review values.
2. Adjust paths in `replau-storefront.service` if the workspace or virtual
   environment location differs.
3. Install the user unit and start it:

   ```bash
   cp replau-storefront.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now replau-storefront.service
   curl http://127.0.0.1:8796/health
   ```

The service uses the existing Product Admin Python environment because it
already provides FastAPI, Uvicorn, and Requests.

## Cloudflare recovery

Install the official `cloudflared` client, authorize it with `cloudflared
tunnel login`, create a named tunnel, and copy
`cloudflared-config.example.yml` to `~/.cloudflared/config.yml`. Replace the
placeholder UUID and credential path, validate ingress, and route the hostname:

```bash
cloudflared tunnel ingress validate
cloudflared tunnel route dns --overwrite-dns replau-storefront orders.replau.com
```

Run the tunnel as a user service with the storefront as a dependency. Keep
`~/.cloudflared/cert.pem`, the tunnel credentials JSON, and all files under
`~/.config/replau/` out of Git.

Set these bridge values so WhatsApp shares the public menu and customer-safe
tracking link:

```dotenv
MENU_URL=https://orders.replau.com
PUBLIC_ORDER_BASE_URL=https://orders.replau.com
```

## Verification

```bash
python3 test_storefront.py
curl https://orders.replau.com/health
```

A generated WhatsApp message must contain the customer name on the first line
and one `quantity + product name` item per following line.
