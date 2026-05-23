#!/usr/bin/env python3

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


ALLOWED_TYPES = {"GRANELES", "CONDIMENTOS", "INSUMOS", "TERMINADO", "OTRO"}
ALLOWED_CURRENCIES = {"PEN", "USD", "EUR"}

REQUIRED_COLUMNS = ["cdg_prod", "nombre", "tipo_producto"]

BOOLEAN_COLUMNS = {
    "controla_lote",
    "controla_vencimiento",
    "active",
    "pack_is_default",
}

NUMERIC_COLUMNS = {
    "stock_minimo",
    "pack_factor",
    "precio",
}


def clean_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_bool(value: str, default: Optional[bool] = None) -> Optional[bool]:
    value = clean_text(value).lower()
    if value == "":
        return default

    if value in {"true", "t", "1", "yes", "y", "si", "sí", "s"}:
        return True

    if value in {"false", "f", "0", "no", "n"}:
        return False

    raise ValueError(f"Invalid boolean value: {value}")


def parse_decimal(value: str) -> Optional[float]:
    value = clean_text(value)
    if value == "":
        return None

    # Accept both 12.50 and 12,50 from Spanish-style spreadsheets.
    value = value.replace(",", ".")

    try:
        return float(Decimal(value))
    except InvalidOperation:
        raise ValueError(f"Invalid numeric value: {value}")


def normalize_row(row: Dict[str, str], row_number: int) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}

    for key, value in row.items():
        if key is None:
            continue

        clean_key = clean_text(key)
        if clean_key == "":
            continue

        normalized[clean_key] = clean_text(value)

    for col in REQUIRED_COLUMNS:
        if clean_text(normalized.get(col, "")) == "":
            raise ValueError(f"Row {row_number}: required column '{col}' is empty")

    normalized["cdg_prod"] = normalized["cdg_prod"].upper()
    normalized["tipo_producto"] = normalized["tipo_producto"].upper()

    if normalized["tipo_producto"] not in ALLOWED_TYPES:
        raise ValueError(
            f"Row {row_number}: tipo_producto must be one of {sorted(ALLOWED_TYPES)}"
        )

    if clean_text(normalized.get("unidad_medida", "")):
        normalized["unidad_medida"] = normalized["unidad_medida"].upper()

    if clean_text(normalized.get("pack_default", "")):
        normalized["pack_default"] = normalized["pack_default"].upper()

    if clean_text(normalized.get("pack_name", "")):
        normalized["pack_name"] = normalized["pack_name"].upper()

    if clean_text(normalized.get("envase_default", "")):
        normalized["envase_default"] = normalized["envase_default"].upper()

    if clean_text(normalized.get("precio_unidad", "")):
        normalized["precio_unidad"] = normalized["precio_unidad"].upper()

    if clean_text(normalized.get("moneda", "")):
        normalized["moneda"] = normalized["moneda"].upper()
        if normalized["moneda"] not in ALLOWED_CURRENCIES:
            raise ValueError(
                f"Row {row_number}: moneda must be one of {sorted(ALLOWED_CURRENCIES)}"
            )

    for col in BOOLEAN_COLUMNS:
        if col in normalized:
            default = True
            parsed = parse_bool(normalized[col], default=None)
            if parsed is None:
                normalized.pop(col, None)
            else:
                normalized[col] = parsed

    for col in NUMERIC_COLUMNS:
        if col in normalized:
            parsed = parse_decimal(normalized[col])
            if parsed is None:
                normalized.pop(col, None)
            else:
                normalized[col] = parsed

    # Remove empty optional values to keep JSON clean.
    empty_keys = [k for k, v in normalized.items() if v == ""]
    for k in empty_keys:
        normalized.pop(k, None)

    return normalized


def read_csv(path: Path, delimiter: str = ",") -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)

        if not reader.fieldnames:
            raise ValueError("CSV has no header row")

        missing_columns = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing_columns:
            raise ValueError(f"CSV is missing required columns: {missing_columns}")

        for row_number, row in enumerate(reader, start=2):
            try:
                rows.append(normalize_row(row, row_number))
            except Exception as exc:
                errors.append(str(exc))

    if errors:
        message = "CSV validation failed:\n" + "\n".join(errors)
        raise ValueError(message)

    return rows


def chunked(items: List[Dict[str, Any]], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def call_postgrest(base_url: str, rows: List[Dict[str, Any]], timeout: int = 60):
    url = base_url.rstrip("/") + "/rpc/bulk_upsert_productos"

    response = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json={"p_items": rows},
        timeout=timeout,
    )

    if not response.ok:
        raise RuntimeError(
            f"PostgREST error HTTP {response.status_code}:\n{response.text}"
        )

    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bulk insert/update products into PostgreSQL through PostgREST."
    )

    parser.add_argument(
        "csv_file",
        help="Path to the CSV file to import.",
    )

    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:3000",
        help="PostgREST base URL. Default: http://127.0.0.1:3000",
    )

    parser.add_argument(
        "--delimiter",
        default=",",
        help="CSV delimiter. Use ';' if your Excel exports semicolon-separated CSV.",
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per API call. Default: 500",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print JSON preview without inserting.",
    )

    args = parser.parse_args()

    try:
        rows = read_csv(Path(args.csv_file), delimiter=args.delimiter)
        print(f"CSV valid. Rows loaded: {len(rows)}")

        if args.dry_run:
            preview = rows[:5]
            print("Preview of first rows:")
            print(json.dumps(preview, indent=2, ensure_ascii=False))
            print("Dry run complete. No data was inserted.")
            return 0

        total_products = 0
        total_packs = 0
        total_prices = 0
        all_errors = []

        for batch_number, batch in enumerate(chunked(rows, args.batch_size), start=1):
            print(f"Sending batch {batch_number} with {len(batch)} row(s)...")
            result = call_postgrest(args.base_url, batch)

            total_products += int(result.get("products_processed", 0))
            total_packs += int(result.get("packs_processed", 0))
            total_prices += int(result.get("prices_processed", 0))
            all_errors.extend(result.get("errors", []))

            print(json.dumps(result, indent=2, ensure_ascii=False))

        print("\nImport finished.")
        print(f"Products processed: {total_products}")
        print(f"Packs processed: {total_packs}")
        print(f"Prices processed: {total_prices}")
        print(f"Errors: {len(all_errors)}")

        if all_errors:
            print("\nErrors:")
            print(json.dumps(all_errors, indent=2, ensure_ascii=False))
            return 2

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
