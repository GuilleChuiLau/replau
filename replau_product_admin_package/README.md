# Replau Product Admin UI

Adds a local product/admin management UI for your Replau WhatsApp order system.

## Barcode and scanner receiving

Apply `postgrest_local/add_scanner_picking.sql` and then
`postgrest_local/add_barcode_receiving.sql`. The protected `/barcodes` workspace
manages Code 128, Replau QR, EAN-13/GTIN-13, and GTIN-14 mappings with packaging
levels and units-per-scan factors. EAN/GTIN values are check-digit validated;
12/13-digit bodies receive a calculated check digit. Printable local SVG labels
do not use a hosted barcode service.

The protected `/receiving` workspace records immutable accepted/rejected scans
and shows base-unit conversion before stock changes. Only the explicit **Post
receiving to stock** action writes `RECEPCION` movements. Voiding a posted
receipt writes compensating `AJUSTE_NEGATIVO` movements and is blocked when that
stock has already been consumed. Supplier GS1 values must be registered as
printed; do not invent retail GTINs without an assigned GS1 company prefix.

## Procurement

Apply `postgrest_local/add_procurement_workflow.sql` after the barcode/receiving
migration. The protected `/procurement` workspace manages suppliers, terms,
lead times, supplier-product pack/price/MOQ mappings, low-stock draft POs, and
the versioned `BORRADOR → APROBADA → ENVIADA → PARCIAL/CERRADA` lifecycle.
Every approval, send, cancellation, and line addition is audited.

Scanner Receiving can be opened from an eligible PO. It rejects products not on
the order and quantities above the remaining balance. Posting creates the base
receipt/detail records so existing stock and PO triggers remain authoritative.
Voiding writes compensating movements, annuls the receipt, and recomputes PO
quantities; it is blocked after the received lot has been consumed.

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
