# Replau Bulk Product Import Tool

This tool imports products from a CSV file into your local PostgreSQL database through PostgREST.

It creates a PostgREST RPC endpoint:

```text
POST http://localhost:3000/rpc/bulk_upsert_productos
```

The import is an upsert by `cdg_prod`, so running the same CSV again updates existing products instead of duplicating them.

## Files

- `add_bulk_product_import.sql`
  Adds the PostgreSQL function `api.bulk_upsert_productos(jsonb)`.

- `import_products_csv.py`
  Python CLI tool that validates a CSV and calls PostgREST.

- `productos_template.csv`
  Example CSV template.

## CSV columns

Required:

```text
cdg_prod
nombre
tipo_producto
```

Allowed `tipo_producto` values:

```text
GRANELES
CONDIMENTOS
INSUMOS
TERMINADO
OTRO
```

Optional product columns:

```text
unidad_medida
pack_default
envase_default
stock_minimo
controla_lote
controla_vencimiento
active
```

Optional pack columns:

```text
pack_name
pack_factor
pack_is_default
```

Optional price columns:

```text
precio_unidad
precio
moneda
valid_from
valid_to
```

Allowed currency values:

```text
PEN
USD
EUR
```

## Installation on your WSL machine

Copy the files to your WSL folder, for example:

```bash
mkdir -p ~/postgrest-local/product_import_tool
cp add_bulk_product_import.sql import_products_csv.py productos_template.csv ~/postgrest-local/product_import_tool/
cd ~/postgrest-local/product_import_tool
```

Run the SQL script as PostgreSQL admin:

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 -d localapi < add_bulk_product_import.sql
```

Restart or reload PostgREST:

```bash
sudo systemctl restart postgrest
```

If you run PostgREST manually, stop it with `CTRL + C` and start it again.

## Python setup

```bash
cd ~/postgrest-local/product_import_tool

python3 -m venv .venv
source .venv/bin/activate

pip install requests
chmod +x import_products_csv.py
```

## Dry run

```bash
python import_products_csv.py productos_template.csv --dry-run
```

## Real import

```bash
python import_products_csv.py productos_template.csv
```

## If your CSV uses semicolons

Some Excel regional exports use `;` instead of `,`.

```bash
python import_products_csv.py productos_template.csv --delimiter ';' --dry-run
python import_products_csv.py productos_template.csv --delimiter ';'
```

## Verify in PostgREST

```bash
curl http://localhost:3000/productos | jq
curl http://localhost:3000/producto_packs | jq
curl http://localhost:3000/producto_precios | jq
```

## Verify a specific product

```bash
curl "http://localhost:3000/productos?cdg_prod=eq.COND003" | jq
```
