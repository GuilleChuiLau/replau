# Replau Product Admin UI

Adds a local product/admin management UI for your Replau WhatsApp order system.

## URL

```text
http://127.0.0.1:8794
```

## Features

- View products
- Search products
- Filter active/inactive products
- Add product + price
- Edit product name/status
- Add new active prices
- Deactivate old active price for the same unit before inserting new price
- Bulk CSV import
- Health endpoint

## Install

Unzip package, then:

```bash
chmod +x install_product_admin.sh
./install_product_admin.sh
```

## Test

```bash
sudo systemctl status replau-product-admin --no-pager
curl http://127.0.0.1:8794/health | jq
```

Open:

```text
http://127.0.0.1:8794
```

## CSV format

```csv
cdg_prod,nombre,unidad,precio,moneda,active
HAMB001,HAMBURGUESA SIMPLE CON QUESO,UNIDAD,15.00,PEN,true
BEB001,COCA COLA MEDIANA,UNIDAD,7.00,PEN,true
```

## Config

```bash
sudo nano /etc/replau-product-admin.env
```

Default:

```ini
POSTGREST_BASE_URL=http://127.0.0.1:3000
ADMIN_HOST=127.0.0.1
ADMIN_PORT=8794
REQUIRE_ADMIN_TOKEN=true
PRODUCTS_ENDPOINT=productos
PRICES_ENDPOINT=producto_precios
DEFAULT_MONEDA=PEN
DEFAULT_UNIDAD=UNIDAD
```

If you expose this outside localhost, set:

```ini
REQUIRE_ADMIN_TOKEN=true
ADMIN_TOKEN=a-long-random-token
```

## Important

This app is intentionally conservative:
- It does not hard-delete products.
- It updates prices by deactivating old active prices for the same unit and inserting a new one.
- It depends on your existing PostgREST permissions for `productos` and `producto_precios`.
