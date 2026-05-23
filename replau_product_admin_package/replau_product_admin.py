#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from fastapi import FastAPI, Form, Header, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
ADMIN_HOST = os.environ.get("ADMIN_HOST", "127.0.0.1")
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "8794"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))

PRODUCTS_ENDPOINT = os.environ.get("PRODUCTS_ENDPOINT", "productos").strip("/")
PRICES_ENDPOINT = os.environ.get("PRICES_ENDPOINT", "producto_precios").strip("/")

REQUIRE_ADMIN_TOKEN = os.environ.get("REQUIRE_ADMIN_TOKEN", "false").lower() == "true"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

DEFAULT_MONEDA = os.environ.get("DEFAULT_MONEDA", "PEN")
DEFAULT_UNIDAD = os.environ.get("DEFAULT_UNIDAD", "UNIDAD")
PRODUCT_IMAGE_DIR = Path(os.environ.get("REPLAU_PRODUCT_IMAGE_DIR", "/home/guill/.openclaw/workspace/replau_product_images"))
PRODUCT_IMAGE_META_PATH = Path(os.environ.get("REPLAU_PRODUCT_IMAGE_META_PATH", "/home/guill/.openclaw/workspace/replau_product_images.json"))
RECIPE_COST_PATH = Path(os.environ.get("REPLAU_RECIPE_COST_PATH", "/home/guill/.openclaw/workspace/replau_recipe_costs.json"))
MENU_TITLE = os.environ.get("REPLAU_MENU_TITLE", "Replau Burger Menu")
MAX_IMAGE_BYTES = int(os.environ.get("REPLAU_MAX_PRODUCT_IMAGE_BYTES", str(5 * 1024 * 1024)))

INGREDIENT_COST_ENDPOINT = os.environ.get("INGREDIENT_COST_ENDPOINT", "ingredientes_costeo").strip("/")
RECIPE_COST_ENDPOINT = os.environ.get("RECIPE_COST_ENDPOINT", "recetas_costeo").strip("/")
RECIPE_INGREDIENT_COST_ENDPOINT = os.environ.get("RECIPE_INGREDIENT_COST_ENDPOINT", "receta_ingredientes_costeo").strip("/")
LOW_STOCK_UNIT_THRESHOLD = float(os.environ.get("REPLAU_LOW_STOCK_UNIT_THRESHOLD", "10"))
LOW_STOCK_KG_THRESHOLD = float(os.environ.get("REPLAU_LOW_STOCK_KG_THRESHOLD", "1"))

app = FastAPI(title="Replau Product Admin UI", version="1.0.0")
PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media/products", StaticFiles(directory=str(PRODUCT_IMAGE_DIR)), name="product_images")


def esc(v: Any) -> str:
    if v is None:
        return ""
    return html.escape(str(v))


def check_auth(request: Request, x_admin_token: Optional[str] = None) -> None:
    if not REQUIRE_ADMIN_TOKEN:
        return
    query_token = request.query_params.get("token")
    if x_admin_token == ADMIN_TOKEN or query_token == ADMIN_TOKEN:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing admin token")


def token_query(request: Request) -> str:
    """Preserve query-token auth across links, forms, and redirects."""
    token = request.query_params.get("token")
    if REQUIRE_ADMIN_TOKEN and token == ADMIN_TOKEN:
        return "?token=" + quote(token, safe="")
    return ""


def with_token(path: str, request: Request) -> str:
    tq = token_query(request)
    if not tq:
        return path
    sep = "&" if "?" in path else "?"
    return path + sep + tq[1:]


def proc_env_token(script_name: str, env_name: str) -> str:
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes().decode("utf-8", "ignore")
            if script_name not in cmdline:
                continue
            for item in (proc / "environ").read_bytes().split(b"\0"):
                if item.startswith((env_name + "=").encode()):
                    token = item.decode("utf-8", "ignore").split("=", 1)[1].strip()
                    if token:
                        return token
        except Exception:
            continue
    return ""


def local_service_url(base: str, path: str = "", script_name: str = "", token_env: str = "") -> str:
    suffix = "/" + path.strip("/") if path.strip("/") else "/"
    url = base.rstrip("/") + suffix
    if script_name and token_env:
        token = proc_env_token(script_name, token_env)
        if token:
            url += "?token=" + quote(token, safe="")
    return url


def erp_nav(auth_query: str = "") -> str:
    return f"""
      <div class="erp-nav" aria-label="Replau ERP navigation">
        <a href="{esc(local_service_url("http://127.0.0.1:8793", script_name="replau_health_dashboard.py", token_env="OPS_TOKEN"))}">Ops</a>
        <a href="http://127.0.0.1:8790/dashboard">Logistics</a>
        <a href="http://127.0.0.1:8791/">Kitchen</a>
        <a href="{esc(local_service_url("http://127.0.0.1:8795", script_name="replau_payment_proof_review.py", token_env="REVIEW_TOKEN"))}">Payments</a>
        <a href="/{auth_query}">Products</a>
        <a href="/recipes{auth_query}">Recipes</a>
        <a href="/costs{auth_query}">Costs</a>
        <a href="/menu" target="_blank">Public Menu</a>
      </div>
    """


def pg_url(path: str) -> str:
    if path.startswith("/"):
        return POSTGREST_BASE_URL + path
    return POSTGREST_BASE_URL + "/" + path


def pg_get(path: str) -> Any:
    r = requests.get(pg_url(path), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def pg_post(path: str, payload: Dict[str, Any]) -> Any:
    r = requests.post(
        pg_url(path),
        json=payload,
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def pg_patch(path: str, payload: Dict[str, Any]) -> Any:
    r = requests.patch(
        pg_url(path),
        json=payload,
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def pg_delete(path: str) -> Any:
    r = requests.delete(
        pg_url(path),
        headers={"Prefer": "return=representation"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def get_columns(endpoint: str, fallback: List[str]) -> List[str]:
    try:
        rows = pg_get(f"/{endpoint}?limit=1")
        if rows and isinstance(rows, list):
            return list(rows[0].keys())
    except Exception:
        pass
    return fallback


def product_columns() -> List[str]:
    return get_columns(PRODUCTS_ENDPOINT, ["id", "cdg_prod", "nombre", "active", "created_at", "updated_at"])


def price_columns() -> List[str]:
    return get_columns(PRICES_ENDPOINT, ["id", "producto_id", "unidad", "precio", "moneda", "active", "valid_from", "valid_to", "created_at", "updated_at"])


def first_existing(columns: List[str], candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in columns:
            return c
    return None


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    s = str(value or "").strip().lower()
    return s in {"1", "true", "yes", "si", "sí", "activo", "active", "on"}


def load_product_images() -> Dict[str, Any]:
    try:
        if PRODUCT_IMAGE_META_PATH.exists():
            data = json.loads(PRODUCT_IMAGE_META_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                entries = data.get("entries")
                return entries if isinstance(entries, dict) else data
    except Exception:
        pass
    return {}


def save_product_images(entries: Dict[str, Any]) -> None:
    PRODUCT_IMAGE_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": date.today().isoformat(), "entries": entries}
    PRODUCT_IMAGE_META_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def product_image_entry(product_id: Any) -> Optional[Dict[str, Any]]:
    entry = load_product_images().get(str(product_id))
    return entry if isinstance(entry, dict) else None


def product_image_url(product_id: Any) -> str:
    entry = product_image_entry(product_id)
    if not entry:
        return ""
    url = str(entry.get("url") or "").strip()
    return url if url else ""


def product_image_html(product_id: Any, alt: str, size: str = "thumb") -> str:
    url = product_image_url(product_id)
    if not url:
        return '<span class="muted">No image</span>'
    cls = "product-thumb" if size == "thumb" else "product-photo"
    return f'<img class="{cls}" src="{esc(url)}" alt="{esc(alt)}">'


def safe_image_extension(upload: UploadFile, data: bytes) -> str:
    content_type = (upload.content_type or "").lower()
    filename = (upload.filename or "").lower()
    if content_type == "image/png" or filename.endswith(".png"):
        return ".png"
    if content_type in {"image/jpeg", "image/jpg"} or filename.endswith((".jpg", ".jpeg")):
        return ".jpg"
    if content_type == "image/webp" or filename.endswith(".webp"):
        return ".webp"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return ".webp"
    raise HTTPException(status_code=400, detail="Only PNG, JPG, and WEBP images are supported")


def save_product_image(product_id: int, product_code: str, upload: UploadFile, data: bytes) -> Dict[str, Any]:
    if not data:
        raise HTTPException(status_code=400, detail="Image file is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail=f"Image is larger than {MAX_IMAGE_BYTES} bytes")
    ext = safe_image_extension(upload, data)
    digest = hashlib.sha256(data).hexdigest()[:16]
    safe_code = re.sub(r"[^A-Za-z0-9_-]+", "-", product_code).strip("-") or f"product-{product_id}"
    filename = f"{product_id}-{safe_code}-{digest}{ext}"
    PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = PRODUCT_IMAGE_DIR / filename
    path.write_bytes(data)
    entry = {
        "filename": filename,
        "path": str(path),
        "url": f"/media/products/{quote(filename, safe='')}",
        "original_filename": upload.filename or "",
        "content_type": upload.content_type or "",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "updated_at": date.today().isoformat(),
    }
    entries = load_product_images()
    old = entries.get(str(product_id))
    entries[str(product_id)] = entry
    save_product_images(entries)
    old_path = Path(str((old or {}).get("path") or ""))
    if old_path.exists() and old_path != path and old_path.parent == PRODUCT_IMAGE_DIR:
        try:
            old_path.unlink()
        except Exception:
            pass
    return entry


def get_product_id(row: Optional[Dict[str, Any]]) -> Any:
    if not row:
        return None
    return row.get("id") or row.get("producto_id")


def get_product_code(row: Dict[str, Any], columns: List[str]) -> str:
    c = first_existing(columns, ["cdg_prod", "codigo", "sku", "code", "producto_codigo"])
    return str(row.get(c) or "") if c else ""


def get_product_name(row: Dict[str, Any], columns: List[str]) -> str:
    c = first_existing(columns, ["nombre", "producto_nombre", "descripcion", "name", "producto"])
    return str(row.get(c) or "") if c else ""


def get_product_active(row: Dict[str, Any], columns: List[str]) -> bool:
    if "active" in columns:
        return bool(row.get("active"))
    if "activo" in columns:
        return bool(row.get("activo"))
    if "estado" in columns:
        return str(row.get("estado") or "").upper() in {"ACTIVO", "ACTIVE"}
    return True


def load_recipe_costs_json() -> Dict[str, Any]:
    try:
        if RECIPE_COST_PATH.exists():
            data = json.loads(RECIPE_COST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("ingredients", {})
                data.setdefault("recipes", {})
                return data
    except Exception:
        pass
    return {"updated_at": date.today().isoformat(), "ingredients": {}, "recipes": {}}


def save_recipe_costs(data: Dict[str, Any]) -> None:
    RECIPE_COST_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = date.today().isoformat()
    RECIPE_COST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def normalize_cost_ingredient(row: Dict[str, Any]) -> Dict[str, Any]:
    iid = str(row.get("id") or "")
    return {
        "id": iid,
        "name": row.get("nombre") or "",
        "provider_id": row.get("proveedor_id"),
        "cost_per_kg": float(row.get("costo_kg") or 0),
        "currency": row.get("moneda") or DEFAULT_MONEDA,
        "stk_in_kg": float(row.get("stk_in") or 0),
        "stk_out_kg": float(row.get("stk_out") or 0),
        "stk_act_kg": float(row.get("stk_act") or ((row.get("stk_in") or 0) - (row.get("stk_out") or 0))),
        "active": normalize_bool(row.get("active", True)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def normalize_cost_recipe(row: Dict[str, Any]) -> Dict[str, Any]:
    rid = str(row.get("id") or "")
    return {
        "id": rid,
        "name": row.get("nombre") or "",
        "product_id": row.get("producto_id"),
        "yield_units": float(row.get("rendimiento_unidades") or 1),
        "active": normalize_bool(row.get("active", True)),
        "ingredients": [],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def load_recipe_costs() -> Dict[str, Any]:
    try:
        ingredient_rows = pg_get(f"/{INGREDIENT_COST_ENDPOINT}?active=eq.true&order=nombre.asc&limit=1000")
        recipe_rows = pg_get(f"/{RECIPE_COST_ENDPOINT}?active=eq.true&order=nombre.asc&limit=1000")
        line_rows = pg_get(f"/{RECIPE_INGREDIENT_COST_ENDPOINT}?order=id.asc&limit=5000")
    except Exception:
        return load_recipe_costs_json()

    ingredients = {
        str(row.get("id")): normalize_cost_ingredient(row)
        for row in ingredient_rows
        if row.get("id") is not None
    }
    recipes = {
        str(row.get("id")): normalize_cost_recipe(row)
        for row in recipe_rows
        if row.get("id") is not None
    }
    for line in line_rows:
        rid = str(line.get("receta_id") or "")
        if rid not in recipes:
            continue
        recipes[rid].setdefault("ingredients", []).append({
            "id": str(line.get("id") or ""),
            "ingredient_id": str(line.get("ingrediente_id") or ""),
            "quantity_g": float(line.get("cantidad_g") or 0),
        })
    return {
        "updated_at": date.today().isoformat(),
        "storage": "postgres",
        "ingredients": ingredients,
        "recipes": recipes,
    }


def next_cost_id(entries: Dict[str, Any]) -> str:
    ids = []
    for key in entries:
        try:
            ids.append(int(key))
        except Exception:
            pass
    return str((max(ids) if ids else 0) + 1)


def parse_positive_float(value: Any, field: str) -> float:
    try:
        out = float(str(value).strip().replace(",", "."))
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field} must be a number")
    if out <= 0:
        raise HTTPException(status_code=400, detail=f"{field} must be greater than zero")
    return out


def parse_nonnegative_float(value: Any, field: str) -> float:
    try:
        out = float(str(value or "0").strip().replace(",", "."))
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field} must be a number")
    if out < 0:
        raise HTTPException(status_code=400, detail=f"{field} cannot be negative")
    return out


def ingredient_stock(ingredient: Dict[str, Any]) -> Dict[str, float]:
    stk_in = float(ingredient.get("stk_in_kg") or 0)
    stk_out = float(ingredient.get("stk_out_kg") or 0)
    return {"stk_in_kg": stk_in, "stk_out_kg": stk_out, "stk_act_kg": stk_in - stk_out}


def provider_lookup() -> Dict[str, str]:
    try:
        rows = pg_get("/proveedores?order=nombre.asc&limit=500")
    except Exception:
        rows = []
    return {str(row.get("id")): str(row.get("nombre") or "") for row in rows}


def provider_options(selected: Any = "") -> str:
    try:
        rows = pg_get("/proveedores?active=eq.true&order=nombre.asc&limit=500")
    except Exception:
        rows = []
    options = ['<option value="">No provider</option>']
    selected_s = str(selected or "")
    for row in rows:
        pid = str(row.get("id") or "")
        label = f"{row.get('id')} · {row.get('nombre')}"
        options.append(f'<option value="{esc(pid)}" {"selected" if pid == selected_s else ""}>{esc(label)}</option>')
    return "".join(options)


def product_options(selected: Any = "", require_existing: bool = False) -> str:
    try:
        rows = get_products("", "true")
        cols = product_columns()
    except Exception:
        rows, cols = [], []
    placeholder = "Choose an active product" if require_existing else "No linked product"
    options = [f'<option value="">{placeholder}</option>']
    selected_s = str(selected or "")
    for row in rows:
        pid = str(get_product_id(row) or "")
        label = f"{pid} · {get_product_code(row, cols)} · {get_product_name(row, cols)}"
        options.append(f'<option value="{esc(pid)}" {"selected" if pid == selected_s else ""}>{esc(label)}</option>')
    return "".join(options)


def product_lookup() -> Dict[str, str]:
    try:
        rows = get_products("", "all")
        cols = product_columns()
    except Exception:
        return {}
    out = {}
    for row in rows:
        pid = str(get_product_id(row) or "")
        if not pid:
            continue
        code = get_product_code(row, cols)
        name = get_product_name(row, cols)
        out[pid] = " · ".join(part for part in [code, name] if part) or pid
    return out


def active_product_id_or_400(product_id: Any) -> int:
    raw = str(product_id or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Recipe must be linked to an active product")
    try:
        pid = int(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid product id")
    rows = get_products("", "true")
    active_ids = {str(get_product_id(row) or "") for row in rows}
    if str(pid) not in active_ids:
        raise HTTPException(status_code=400, detail="Product does not exist or is not active")
    return pid


def recipe_product_label(recipe: Dict[str, Any], products: Optional[Dict[str, str]] = None) -> str:
    product_id = recipe.get("product_id")
    if not product_id:
        return "No linked product"
    lookup = products if products is not None else product_lookup()
    return lookup.get(str(product_id), f"Product {product_id}")


def recipe_line_id(line: Dict[str, Any]) -> str:
    return str(line.get("id") or "")


def recipe_cost_summary(recipe: Dict[str, Any], ingredients: Dict[str, Any]) -> Dict[str, Any]:
    total = 0.0
    lines = []
    for line in recipe.get("ingredients", []):
        ing = ingredients.get(str(line.get("ingredient_id")))
        if not ing:
            continue
        qty_g = float(line.get("quantity_g") or 0)
        cost_per_kg = float(ing.get("cost_per_kg") or 0)
        line_cost = (qty_g / 1000.0) * cost_per_kg
        total += line_cost
        lines.append({**line, "ingredient": ing, "line_cost": line_cost})
    yield_units = float(recipe.get("yield_units") or 1)
    return {
        "total_cost": total,
        "cost_per_unit": total / yield_units if yield_units > 0 else total,
        "lines": lines,
    }


def recipe_name(recipe: Dict[str, Any]) -> str:
    return str(recipe.get("name") or "Unnamed recipe")


def recipe_product_status(recipe: Dict[str, Any], ingredients: Dict[str, Any], products: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    limiting_units: Optional[float] = None
    limiting_ingredient = ""
    ingredient_risks = []
    yield_units = float(recipe.get("yield_units") or 1)

    for line in recipe.get("ingredients", []):
        ing = ingredients.get(str(line.get("ingredient_id")))
        if not ing:
            continue
        stock = ingredient_stock(ing)
        qty_g = float(line.get("quantity_g") or 0)
        grams_per_unit = qty_g / yield_units if yield_units > 0 else qty_g
        if grams_per_unit <= 0:
            continue
        units_remaining = (stock["stk_act_kg"] * 1000.0) / grams_per_unit
        if limiting_units is None or units_remaining < limiting_units:
            limiting_units = units_remaining
            limiting_ingredient = str(ing.get("name") or "")
        if units_remaining <= LOW_STOCK_UNIT_THRESHOLD:
            ingredient_risks.append({
                "ingredient_id": str(line.get("ingredient_id") or ""),
                "ingredient_name": str(ing.get("name") or ""),
                "stock_kg": round(stock["stk_act_kg"], 3),
                "grams_per_unit": round(grams_per_unit, 3),
                "units_remaining": round(max(units_remaining, 0), 1),
            })

    if limiting_units is None:
        status = "unknown"
    elif limiting_units <= 0:
        status = "out"
    elif limiting_units <= LOW_STOCK_UNIT_THRESHOLD:
        status = "low"
    else:
        status = "ok"

    return {
        "recipe_id": str(recipe.get("id") or ""),
        "recipe_name": recipe_name(recipe),
        "product_id": recipe.get("product_id"),
        "product_label": recipe_product_label(recipe, products),
        "status": status,
        "units_remaining": round(max(limiting_units or 0, 0), 1) if limiting_units is not None else None,
        "limiting_ingredient": limiting_ingredient,
        "ingredient_risks": ingredient_risks,
    }


def low_stock_alerts(data: Dict[str, Any], products: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    ingredients = data.get("ingredients", {})
    recipes = data.get("recipes", {})
    products = products if products is not None else product_lookup()

    recipe_statuses = [
        recipe_product_status(recipe, ingredients, products)
        for recipe in recipes.values()
    ]

    at_risk_by_ingredient: Dict[str, List[Dict[str, Any]]] = {}
    for status in recipe_statuses:
        if status["status"] not in {"out", "low"}:
            continue
        for risk in status.get("ingredient_risks", []):
            at_risk_by_ingredient.setdefault(risk["ingredient_id"], []).append({
                "recipe_id": status["recipe_id"],
                "recipe_name": status["recipe_name"],
                "product_id": status["product_id"],
                "product_label": status["product_label"],
                "units_remaining": risk["units_remaining"],
                "grams_per_unit": risk["grams_per_unit"],
            })

    ingredient_alerts = []
    for iid, ingredient in sorted(ingredients.items(), key=lambda item: str(item[1].get("name") or "").lower()):
        stock = ingredient_stock(ingredient)
        linked_products = at_risk_by_ingredient.get(str(iid), [])
        status = "ok"
        if stock["stk_act_kg"] <= 0:
            status = "out"
        elif linked_products or stock["stk_act_kg"] <= LOW_STOCK_KG_THRESHOLD:
            status = "low"
        if status == "ok":
            continue
        ingredient_alerts.append({
            "ingredient_id": str(iid),
            "ingredient_name": str(ingredient.get("name") or ""),
            "status": status,
            "stock_kg": round(stock["stk_act_kg"], 3),
            "low_stock_kg_threshold": LOW_STOCK_KG_THRESHOLD,
            "at_risk_products": linked_products,
        })

    product_alerts = [
        status for status in recipe_statuses
        if status["status"] in {"out", "low"}
    ]
    product_alerts.sort(key=lambda item: (item["units_remaining"] is None, item["units_remaining"] or 0, item["product_label"]))

    return {
        "unit_threshold": LOW_STOCK_UNIT_THRESHOLD,
        "kg_threshold": LOW_STOCK_KG_THRESHOLD,
        "ingredient_alerts": ingredient_alerts,
        "product_alerts": product_alerts,
        "ingredient_alert_count": len(ingredient_alerts),
        "product_alert_count": len(product_alerts),
    }


def find_product_by_code(code: str) -> Optional[Dict[str, Any]]:
    cols = product_columns()
    code_col = first_existing(cols, ["cdg_prod", "codigo", "sku", "code", "producto_codigo"])
    if not code_col:
        return None
    rows = pg_get(f"/{PRODUCTS_ENDPOINT}?{code_col}=eq.{requests.utils.quote(code)}&limit=1")
    return rows[0] if rows else None


def build_product_payload(code: str, name: str, active: bool = True) -> Dict[str, Any]:
    cols = product_columns()
    payload: Dict[str, Any] = {}

    code_col = first_existing(cols, ["cdg_prod", "codigo", "sku", "code", "producto_codigo"]) or "cdg_prod"
    name_col = first_existing(cols, ["nombre", "producto_nombre", "descripcion", "name", "producto"]) or "nombre"

    payload[code_col] = code.strip()
    payload[name_col] = name.strip()

    if "active" in cols:
        payload["active"] = active
    elif "activo" in cols:
        payload["activo"] = active
    elif "estado" in cols:
        payload["estado"] = "ACTIVO" if active else "INACTIVO"

    if "tipo_producto" in cols:
        payload.setdefault("tipo_producto", "TERMINADO")
    if "unidad_medida" in cols:
        payload.setdefault("unidad_medida", DEFAULT_UNIDAD)

    return payload


def build_product_update_payload(name: str, active: bool) -> Dict[str, Any]:
    cols = product_columns()
    payload: Dict[str, Any] = {}

    name_col = first_existing(cols, ["nombre", "producto_nombre", "descripcion", "name", "producto"])
    if name_col:
        payload[name_col] = name.strip()

    if "active" in cols:
        payload["active"] = active
    elif "activo" in cols:
        payload["activo"] = active
    elif "estado" in cols:
        payload["estado"] = "ACTIVO" if active else "INACTIVO"

    return payload


def build_price_payload(product_id: Any, unidad: str, precio: float, moneda: str, active: bool = True) -> Dict[str, Any]:
    cols = price_columns()
    payload: Dict[str, Any] = {}

    product_col = first_existing(cols, ["producto_id", "product_id"]) or "producto_id"
    payload[product_col] = product_id

    if "unidad" in cols:
        payload["unidad"] = unidad.strip().upper()
    elif "unit" in cols:
        payload["unit"] = unidad.strip().upper()

    if "precio" in cols:
        payload["precio"] = precio
    elif "price" in cols:
        payload["price"] = precio

    if "moneda" in cols:
        payload["moneda"] = moneda.strip().upper() or DEFAULT_MONEDA
    elif "currency" in cols:
        payload["currency"] = moneda.strip().upper() or DEFAULT_MONEDA

    if "active" in cols:
        payload["active"] = active
    elif "activo" in cols:
        payload["activo"] = active

    if "valid_from" in cols:
        payload["valid_from"] = str(date.today())

    return payload


def price_filter_for(product_id: Any, unidad: str, valid_from: Optional[str] = None) -> str:
    cols = price_columns()
    product_col = first_existing(cols, ["producto_id", "product_id"]) or "producto_id"
    unit_col = first_existing(cols, ["unidad", "unit"])

    filters = f"{product_col}=eq.{product_id}"
    if unit_col:
        filters += f"&{unit_col}=eq.{requests.utils.quote(unidad.strip().upper())}"
    if valid_from and "valid_from" in cols:
        filters += f"&valid_from=eq.{requests.utils.quote(valid_from)}"
    return filters


def find_price_for_date(product_id: Any, unidad: str, valid_from: str) -> Optional[Dict[str, Any]]:
    rows = pg_get(f"/{PRICES_ENDPOINT}?{price_filter_for(product_id, unidad, valid_from)}&limit=1")
    return rows[0] if rows else None


def deactivate_old_prices(product_id: Any, unidad: str) -> None:
    cols = price_columns()
    active_col = first_existing(cols, ["active", "activo"])

    filters = price_filter_for(product_id, unidad)
    if active_col:
        filters += f"&{active_col}=eq.true"

    payload: Dict[str, Any] = {}
    if active_col:
        payload[active_col] = False
    if "valid_to" in cols:
        payload["valid_to"] = str(date.today())

    if payload:
        try:
            pg_patch(f"/{PRICES_ENDPOINT}?{filters}", payload)
        except Exception:
            pass


def update_price_payload(unidad: str, precio: float, moneda: str, active: bool = True) -> Dict[str, Any]:
    cols = price_columns()
    payload: Dict[str, Any] = {}

    if "unidad" in cols:
        payload["unidad"] = unidad.strip().upper()
    elif "unit" in cols:
        payload["unit"] = unidad.strip().upper()

    if "precio" in cols:
        payload["precio"] = precio
    elif "price" in cols:
        payload["price"] = precio

    if "moneda" in cols:
        payload["moneda"] = moneda.strip().upper() or DEFAULT_MONEDA
    elif "currency" in cols:
        payload["currency"] = moneda.strip().upper() or DEFAULT_MONEDA

    if "active" in cols:
        payload["active"] = active
    elif "activo" in cols:
        payload["activo"] = active

    if "valid_to" in cols:
        payload["valid_to"] = None
    return payload


def create_or_update_price(product_id: Any, unidad: str, precio: float, moneda: str) -> None:
    # producto_precios is unique per product/unit/valid_from. A second same-day
    # price change must update today's row instead of inserting another row.
    today = str(date.today())
    todays_price = find_price_for_date(product_id, unidad, today)
    deactivate_old_prices(product_id, unidad)
    if todays_price and todays_price.get("id") is not None:
        pg_patch(f"/{PRICES_ENDPOINT}?id=eq.{todays_price.get('id')}", update_price_payload(unidad, precio, moneda, True))
        return
    pg_post(f"/{PRICES_ENDPOINT}", build_price_payload(product_id, unidad, precio, moneda, True))


def latest_prices_by_product() -> Dict[Any, List[Dict[str, Any]]]:
    try:
        rows = pg_get(f"/{PRICES_ENDPOINT}?order=id.desc&limit=5000")
    except Exception:
        return {}

    out: Dict[Any, List[Dict[str, Any]]] = {}
    pcols = price_columns()
    product_col = first_existing(pcols, ["producto_id", "product_id"]) or "producto_id"
    for row in rows:
        pid = row.get(product_col)
        out.setdefault(pid, []).append(row)
    return out


def get_products(search: str = "", active_filter: str = "all") -> List[Dict[str, Any]]:
    cols = product_columns()
    name_col = first_existing(cols, ["nombre", "producto_nombre", "descripcion", "name", "producto"])
    code_col = first_existing(cols, ["cdg_prod", "codigo", "sku", "code", "producto_codigo"])
    active_col = first_existing(cols, ["active", "activo"])

    path = f"/{PRODUCTS_ENDPOINT}?order=id.asc&limit=1000"

    if active_filter in {"true", "false"} and active_col:
        path += f"&{active_col}=eq.{active_filter}"

    rows = pg_get(path)

    if search:
        s = search.lower().strip()
        rows = [
            r for r in rows
            if s in str(r.get(name_col) or "").lower()
            or s in str(r.get(code_col) or "").lower()
        ]

    return rows


def render_price_badges(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return '<span class="muted">No prices</span>'

    pcols = price_columns()
    unit_col = first_existing(pcols, ["unidad", "unit"])
    price_col = first_existing(pcols, ["precio", "price"])
    curr_col = first_existing(pcols, ["moneda", "currency"])
    active_col = first_existing(pcols, ["active", "activo"])

    badges = []
    shown = 0
    seen = set()

    for row in rows:
        unit = row.get(unit_col) if unit_col else ""
        key = unit or row.get("id")
        if key in seen:
            continue
        seen.add(key)

        active = row.get(active_col, True) if active_col else True
        cls = "price" if active else "price off"
        badges.append(
            f'<span class="{cls}">{esc(unit)} S/ {esc(row.get(price_col))} {esc(row.get(curr_col) or "")}</span>'
        )
        shown += 1
        if shown >= 4:
            break

    return " ".join(badges)


def active_price_summary(rows: List[Dict[str, Any]]) -> str:
    pcols = price_columns()
    unit_col = first_existing(pcols, ["unidad", "unit"])
    price_col = first_existing(pcols, ["precio", "price"])
    curr_col = first_existing(pcols, ["moneda", "currency"])
    active_col = first_existing(pcols, ["active", "activo"])
    for row in rows:
        if active_col and not row.get(active_col, True):
            continue
        unit = row.get(unit_col) if unit_col else DEFAULT_UNIDAD
        price = row.get(price_col) if price_col else None
        curr = row.get(curr_col) if curr_col else DEFAULT_MONEDA
        if price is not None:
            return f"{unit or DEFAULT_UNIDAD} · S/ {price} {curr or ''}".strip()
    return "Precio por confirmar"


def html_page(title: str, body: str, flash: str = "", auth_query: str = "") -> HTMLResponse:
    flash_html = f'<div class="flash">{esc(flash)}</div>' if flash else ""
    return HTMLResponse(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{esc(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ margin:0; font-family:Arial,sans-serif; background:#0f172a; color:#e5e7eb; }}
    .wrap {{ max-width:1300px; margin:0 auto; padding:22px; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:18px; }}
    h1 {{ margin:0; font-size:32px; }}
    h2 {{ margin-top:0; }}
    a {{ color:#93c5fd; text-decoration:none; }}
    .nav a {{ margin-left:12px; }}
    .erp-nav {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 18px; padding:12px; border:1px solid #334155; border-radius:14px; background:#0b1220; }}
    .erp-nav a {{ color:#e5e7eb; background:#1f2937; border:1px solid #334155; border-radius:999px; padding:8px 11px; font-size:13px; font-weight:bold; }}
    .erp-nav a:hover {{ background:#2563eb; border-color:#60a5fa; }}
    .card {{ background:#111827; border:1px solid #334155; border-radius:18px; padding:18px; margin:16px 0; box-shadow:0 10px 35px rgba(0,0,0,.25); }}
    input, select, textarea {{ width:100%; box-sizing:border-box; padding:10px; border-radius:10px; border:1px solid #475569; background:#020617; color:#e5e7eb; }}
    textarea {{ min-height:220px; font-family:monospace; }}
    label {{ display:block; margin:10px 0 6px; color:#bfdbfe; }}
    button {{ padding:10px 14px; border-radius:12px; border:0; background:#2563eb; color:white; font-weight:bold; cursor:pointer; }}
    button.secondary {{ background:#334155; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
    .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ text-align:left; border-bottom:1px solid #334155; padding:10px; vertical-align:top; }}
    th {{ color:#93c5fd; }}
    .muted {{ color:#94a3b8; }}
    .pill {{ display:inline-block; padding:5px 9px; border-radius:999px; background:#334155; }}
    .active {{ background:#166534; }}
    .inactive {{ background:#7f1d1d; }}
    .warn {{ background:#78350f; color:#fde68a; }}
    .danger {{ background:#7f1d1d; color:#fecaca; }}
    .alert-list {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:12px; }}
    .alert-item {{ border:1px solid #334155; border-radius:14px; padding:12px; background:#020617; }}
    .price {{ display:inline-block; padding:5px 9px; border-radius:999px; background:#1d4ed8; margin:2px; }}
    .price.off {{ background:#475569; color:#cbd5e1; }}
    .money {{ color:#86efac; font-weight:bold; }}
    .danger-link {{ color:#fca5a5; }}
    .product-thumb {{ width:68px; height:50px; object-fit:cover; border-radius:10px; border:1px solid #334155; background:#020617; }}
    .product-photo {{ width:100%; max-width:360px; aspect-ratio:4/3; object-fit:cover; border-radius:14px; border:1px solid #334155; background:#020617; }}
    .menu-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:16px; }}
    .menu-card {{ background:#111827; border:1px solid #334155; border-radius:16px; overflow:hidden; }}
    .menu-card img {{ width:100%; aspect-ratio:4/3; object-fit:cover; background:#020617; display:block; }}
    .menu-card-body {{ padding:14px; }}
    .menu-card h3 {{ margin:0 0 8px; font-size:18px; }}
    .flash {{ background:#064e3b; border:1px solid #059669; padding:12px; border-radius:14px; margin-bottom:14px; }}
    .error {{ background:#7f1d1d; border:1px solid #ef4444; padding:12px; border-radius:14px; margin-bottom:14px; }}
    .workspace-grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; }}
    .workspace-card {{ background:#0b1220; border:1px solid #334155; border-radius:14px; padding:14px; }}
    .workspace-card span {{ display:block; color:#94a3b8; font-size:12px; font-weight:bold; text-transform:uppercase; letter-spacing:.05em; }}
    .workspace-card strong {{ display:block; margin-top:8px; font-size:26px; line-height:1; }}
    .quick-actions {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }}
    .quick-actions a {{ display:inline-block; padding:9px 12px; border-radius:12px; background:#334155; color:#e5e7eb; font-weight:bold; }}
    pre {{ white-space:pre-wrap; }}
    @media(max-width:900px) {{ .grid, .grid2, .workspace-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>{esc(title)}</h1>
        <div class="muted">PostgREST: {esc(POSTGREST_BASE_URL)}</div>
      </div>
      <div class="nav">
        <a href="/{auth_query}">Products</a>
        <a href="/recipes{auth_query}">Recipes</a>
        <a href="/costs{auth_query}">Recipe Costs</a>
        <a href="/bulk{auth_query}">Bulk CSV</a>
        <a href="/menu" target="_blank">Public Menu</a>
        <a href="/health{auth_query}">Health</a>
      </div>
    </div>
    {erp_nav(auth_query)}
    {flash_html}
    {body}
  </div>
</body>
</html>""")


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        products = pg_get(f"/{PRODUCTS_ENDPOINT}?limit=1")
        prices = pg_get(f"/{PRICES_ENDPOINT}?limit=1")
        images = load_product_images()
        recipe_cost_data = load_recipe_costs()
        alerts = low_stock_alerts(recipe_cost_data)
        return {
            "ok": True,
            "app": "replau-product-admin",
            "postgrest": POSTGREST_BASE_URL,
            "products_endpoint": PRODUCTS_ENDPOINT,
            "prices_endpoint": PRICES_ENDPOINT,
            "product_columns": product_columns(),
            "price_columns": price_columns(),
            "product_images": len(images),
            "product_image_dir": str(PRODUCT_IMAGE_DIR),
            "recipe_cost_storage": recipe_cost_data.get("storage", "json"),
            "recipe_cost_path": str(RECIPE_COST_PATH),
            "recipe_costs": {
                "ingredients": len(recipe_cost_data.get("ingredients", {})),
                "recipes": len(recipe_cost_data.get("recipes", {})),
            },
            "low_stock_alerts": {
                "ingredients": alerts["ingredient_alert_count"],
                "products": alerts["product_alert_count"],
                "unit_threshold": alerts["unit_threshold"],
                "kg_threshold": alerts["kg_threshold"],
            },
            "sample_products_ok": isinstance(products, list),
            "sample_prices_ok": isinstance(prices, list),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/api/menu")
def api_menu() -> JSONResponse:
    rows = get_products("", "true")
    cols = product_columns()
    prices = latest_prices_by_product()
    items = []
    for row in rows:
        pid = get_product_id(row)
        items.append({
            "id": pid,
            "code": get_product_code(row, cols),
            "name": get_product_name(row, cols),
            "active": get_product_active(row, cols),
            "image_url": product_image_url(pid),
            "price_summary": active_price_summary(prices.get(pid, [])),
        })
    return JSONResponse({"ok": True, "title": MENU_TITLE, "items": items})


@app.get("/api/recipe-costs")
def api_recipe_costs(request: Request, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> JSONResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    ingredients = data.get("ingredients", {})
    products = product_lookup()
    alerts = low_stock_alerts(data, products)
    recipes = []
    for rid, recipe in sorted(data.get("recipes", {}).items(), key=lambda item: recipe_name(item[1]).lower()):
        summary = recipe_cost_summary(recipe, ingredients)
        product_status = recipe_product_status(recipe, ingredients, products)
        recipes.append({
            "id": rid,
            "name": recipe_name(recipe),
            "product_id": recipe.get("product_id"),
            "product_label": recipe_product_label(recipe, products),
            "yield_units": recipe.get("yield_units") or 1,
            "total_cost": round(summary["total_cost"], 4),
            "cost_per_unit": round(summary["cost_per_unit"], 4),
            "ingredient_count": len(summary["lines"]),
            "stock_status": product_status["status"],
            "units_remaining": product_status["units_remaining"],
            "limiting_ingredient": product_status["limiting_ingredient"],
        })
    ingredient_items = []
    for iid, ingredient in sorted(ingredients.items(), key=lambda item: str(item[1].get("name") or "").lower()):
        stock = ingredient_stock(ingredient)
        ingredient_items.append({"id": iid, **ingredient, **stock})
    return JSONResponse({"ok": True, "ingredients": ingredient_items, "recipes": recipes, "low_stock_alerts": alerts})


@app.get("/api/low-stock-alerts")
def api_low_stock_alerts(request: Request, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> JSONResponse:
    check_auth(request, x_admin_token)
    return JSONResponse({"ok": True, **low_stock_alerts(load_recipe_costs())})


@app.get("/api/recipes")
def api_recipes(request: Request, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> JSONResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    ingredients = data.get("ingredients", {})
    products = product_lookup()
    items = []
    for rid, recipe in sorted(data.get("recipes", {}).items(), key=lambda item: recipe_name(item[1]).lower()):
        summary = recipe_cost_summary(recipe, ingredients)
        items.append({
            "id": rid,
            "name": recipe_name(recipe),
            "product_id": recipe.get("product_id"),
            "product_label": recipe_product_label(recipe, products),
            "yield_units": recipe.get("yield_units") or 1,
            "ingredient_count": len(summary["lines"]),
            "total_cost": round(summary["total_cost"], 4),
            "cost_per_unit": round(summary["cost_per_unit"], 4),
            "ingredients": [
                {
                    "line_id": recipe_line_id(line),
                    "ingredient_id": str(line.get("ingredient_id") or ""),
                    "ingredient_name": str(line.get("ingredient", {}).get("name") or ""),
                    "quantity_g": float(line.get("quantity_g") or 0),
                    "line_cost": round(float(line.get("line_cost") or 0), 4),
                }
                for line in summary["lines"]
            ],
        })
    return JSONResponse({"ok": True, "recipes": items})


@app.get("/recipes", response_class=HTMLResponse)
def recipes_page(request: Request, flash: str = "", x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    ingredients = data.get("ingredients", {})
    recipes = data.get("recipes", {})
    products = product_lookup()

    rows = ""
    for rid, recipe in sorted(recipes.items(), key=lambda item: recipe_name(item[1]).lower()):
        summary = recipe_cost_summary(recipe, ingredients)
        rows += f"""
        <tr>
          <td>{esc(rid)}</td>
          <td><a href="/recipes/{esc(rid)}{token_query(request)}"><strong>{esc(recipe_name(recipe))}</strong></a><div class="muted">{esc(recipe_product_label(recipe, products))}</div></td>
          <td>{float(recipe.get("yield_units") or 1):.3f}</td>
          <td>{len(summary["lines"])}</td>
          <td class="money">S/ {summary["total_cost"]:.4f}</td>
          <td class="money">S/ {summary["cost_per_unit"]:.4f}</td>
        </tr>
        """

    body = f"""
    <div class="grid2">
      <div class="card">
        <h2>Create recipe</h2>
        <form method="post" action="/recipes{token_query(request)}">
          <label>Recipe name</label><input name="name" required placeholder="Burger doble">
          <label>Linked product</label><select name="product_id" required>{product_options(require_existing=True)}</select>
          <label>Yield units</label><input name="yield_units" type="number" step="0.001" min="0.001" value="1" required>
          <br><br><button type="submit">Create recipe</button>
        </form>
      </div>
      <div class="card">
        <h2>Recipe module</h2>
        <p class="muted">Create the recipe first, then open it to add exact ingredient grams. Food cost is calculated from the same Postgres ingredient table used by Recipe Costs.</p>
        <p><a href="/costs{token_query(request)}">Manage ingredients and stock</a></p>
      </div>
    </div>
    <div class="card">
      <h2>Recipes</h2>
      <table>
        <thead><tr><th>ID</th><th>Recipe</th><th>Yield</th><th>Ingredients</th><th>Total cost</th><th>Cost / unit</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="6" class="muted">No recipes yet.</td></tr>'}</tbody>
      </table>
    </div>
    """
    return html_page("Recipe Creation", body, flash=flash, auth_query=token_query(request))


@app.post("/recipes")
def recipes_create(request: Request, name: str = Form(...), product_id: str = Form(""), yield_units: float = Form(1), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    linked_product_id = active_product_id_or_400(product_id)
    created = pg_post(f"/{RECIPE_COST_ENDPOINT}", {
        "nombre": name.strip(),
        "producto_id": linked_product_id,
        "rendimiento_unidades": parse_positive_float(yield_units, "yield_units"),
        "active": True,
    })
    rid = str((created[0] if created else {}).get("id") or "")
    return RedirectResponse(url=with_token(f"/recipes/{rid}?flash=Recipe+created", request), status_code=303)


@app.get("/recipes/{recipe_id}", response_class=HTMLResponse)
def recipes_detail(recipe_id: str, request: Request, flash: str = "", x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    ingredients = data.get("ingredients", {})
    recipe = data.get("recipes", {}).get(str(recipe_id))
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    summary = recipe_cost_summary(recipe, ingredients)
    product_status = recipe_product_status(recipe, ingredients)
    status_class = "danger" if product_status["status"] == "out" else "warn" if product_status["status"] == "low" else ""
    units_left = "n/a" if product_status["units_remaining"] is None else f'{product_status["units_remaining"]:.1f}'
    ingredient_options = "".join(
        f'<option value="{esc(iid)}">{esc(ing.get("name"))} · S/ {float(ing.get("cost_per_kg") or 0):.4f}/kg · stk {ingredient_stock(ing)["stk_act_kg"]:.3f}kg</option>'
        for iid, ing in sorted(ingredients.items(), key=lambda item: str(item[1].get("name") or "").lower())
    )
    rows = ""
    for idx, line in enumerate(summary["lines"]):
        ing = line["ingredient"]
        line_id = recipe_line_id(line)
        rows += f"""
        <tr>
          <td>{idx + 1}</td>
          <td><strong>{esc(ing.get("name"))}</strong><div class="muted">Ingredient {esc(line.get("ingredient_id"))}</div></td>
          <td>
            <form method="post" action="/recipes/{esc(recipe_id)}/ingredients{token_query(request)}">
              <input type="hidden" name="ingredient_id" value="{esc(line.get("ingredient_id"))}">
              <input name="quantity_g" type="number" step="0.001" min="0.001" value="{float(line.get("quantity_g") or 0):.3f}" required>
              <button class="secondary" type="submit" style="margin-top:8px;">Update grams</button>
            </form>
          </td>
          <td>S/ {float(ing.get("cost_per_kg") or 0):.4f}</td>
          <td class="money">S/ {line["line_cost"]:.4f}</td>
          <td>
            <form method="post" action="/recipes/{esc(recipe_id)}/ingredients/{esc(line_id)}/delete{token_query(request)}">
              <button class="secondary" type="submit">Remove</button>
            </form>
          </td>
        </tr>
        """
    body = f"""
    <div class="card">
      <h2>{esc(recipe_name(recipe))}</h2>
      <p class="muted">{esc(recipe_product_label(recipe))}</p>
      <p>Total recipe food cost: <span class="money">S/ {summary["total_cost"]:.4f}</span> · Cost per unit: <span class="money">S/ {summary["cost_per_unit"]:.4f}</span></p>
      <p><a href="/recipes{token_query(request)}">Back to recipes</a> · <a href="/costs/recipe/{esc(recipe_id)}{token_query(request)}">Cost view</a></p>
    </div>
    <div class="grid2">
      <div class="card">
        <h2>Recipe header</h2>
        <form method="post" action="/recipes/{esc(recipe_id)}/header{token_query(request)}">
          <label>Recipe name</label><input name="name" value="{esc(recipe_name(recipe))}" required>
          <label>Linked product</label><select name="product_id" required>{product_options(recipe.get("product_id"), require_existing=True)}</select>
          <label>Yield units</label><input name="yield_units" type="number" step="0.001" min="0.001" value="{float(recipe.get("yield_units") or 1):.3f}" required>
          <br><br><button type="submit">Save recipe</button>
        </form>
      </div>
      <div class="card">
        <h2>Add ingredient amount</h2>
        <form method="post" action="/recipes/{esc(recipe_id)}/ingredients{token_query(request)}">
          <label>Ingredient</label><select name="ingredient_id" required>{ingredient_options}</select>
          <label>Exact amount in grams</label><input name="quantity_g" type="number" step="0.001" min="0.001" required placeholder="180">
          <br><br><button type="submit">Add ingredient</button>
        </form>
      </div>
    </div>
    <div class="card">
      <h2>Recipe ingredients</h2>
      <table>
        <thead><tr><th>#</th><th>Ingredient</th><th>Grams</th><th>Cost/kg</th><th>Line cost</th><th></th></tr></thead>
        <tbody>{rows or '<tr><td colspan="6" class="muted">No ingredients in this recipe yet.</td></tr>'}</tbody>
      </table>
    </div>
    """
    return html_page(f"Recipe {recipe_id}", body, flash=flash, auth_query=token_query(request))


@app.post("/recipes/{recipe_id}/header")
def recipes_update_header(recipe_id: str, request: Request, name: str = Form(...), product_id: str = Form(""), yield_units: float = Form(1), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    recipe = load_recipe_costs().get("recipes", {}).get(str(recipe_id))
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    linked_product_id = active_product_id_or_400(product_id)
    pg_patch(f"/{RECIPE_COST_ENDPOINT}?id=eq.{requests.utils.quote(str(recipe_id))}", {
        "nombre": name.strip(),
        "producto_id": linked_product_id,
        "rendimiento_unidades": parse_positive_float(yield_units, "yield_units"),
    })
    return RedirectResponse(url=with_token(f"/recipes/{recipe_id}?flash=Recipe+saved", request), status_code=303)


@app.post("/recipes/{recipe_id}/ingredients")
def recipes_upsert_ingredient(recipe_id: str, request: Request, ingredient_id: str = Form(...), quantity_g: float = Form(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    recipe = data.get("recipes", {}).get(str(recipe_id))
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if str(ingredient_id) not in data.get("ingredients", {}):
        raise HTTPException(status_code=404, detail="Ingredient not found")
    existing = next(
        (line for line in recipe.get("ingredients", []) if str(line.get("ingredient_id")) == str(ingredient_id)),
        None,
    )
    if existing and existing.get("id"):
        pg_patch(f"/{RECIPE_INGREDIENT_COST_ENDPOINT}?id=eq.{requests.utils.quote(str(existing['id']))}", {
            "cantidad_g": parse_positive_float(quantity_g, "quantity_g"),
        })
    else:
        pg_post(f"/{RECIPE_INGREDIENT_COST_ENDPOINT}", {
            "receta_id": int(recipe_id),
            "ingrediente_id": int(ingredient_id),
            "cantidad_g": parse_positive_float(quantity_g, "quantity_g"),
        })
    return RedirectResponse(url=with_token(f"/recipes/{recipe_id}?flash=Ingredient+saved", request), status_code=303)


@app.post("/recipes/{recipe_id}/ingredients/{line_id}/delete")
def recipes_delete_ingredient(recipe_id: str, line_id: str, request: Request, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    recipe = load_recipe_costs().get("recipes", {}).get(str(recipe_id))
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    pg_delete(f"/{RECIPE_INGREDIENT_COST_ENDPOINT}?id=eq.{requests.utils.quote(str(line_id))}&receta_id=eq.{requests.utils.quote(str(recipe_id))}")
    return RedirectResponse(url=with_token(f"/recipes/{recipe_id}?flash=Ingredient+removed", request), status_code=303)


@app.get("/menu", response_class=HTMLResponse)
def public_menu() -> HTMLResponse:
    rows = get_products("", "true")
    cols = product_columns()
    prices = latest_prices_by_product()
    cards = ""
    for row in rows:
        pid = get_product_id(row)
        name = get_product_name(row, cols)
        code = get_product_code(row, cols)
        img = product_image_url(pid)
        img_html = f'<img src="{esc(img)}" alt="{esc(name)}">' if img else '<div style="aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;background:#020617;color:#94a3b8;">Sin foto</div>'
        cards += f"""
        <article class="menu-card">
          {img_html}
          <div class="menu-card-body">
            <h3>{esc(name)}</h3>
            <div class="muted">{esc(code)}</div>
            <p><strong>{esc(active_price_summary(prices.get(pid, [])))}</strong></p>
          </div>
        </article>
        """
    body = f"""
    <div class="card">
      <h2>Menu activo</h2>
      <p class="muted">Generado automáticamente desde el catálogo activo de Replau.</p>
      <div class="menu-grid">{cards or '<p class="muted">No hay productos activos.</p>'}</div>
    </div>
    """
    return html_page(MENU_TITLE, body)


@app.get("/costs", response_class=HTMLResponse)
def recipe_costs_page(request: Request, flash: str = "", x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    ingredients = data.get("ingredients", {})
    recipes = data.get("recipes", {})
    providers = provider_lookup()
    products = product_lookup()
    alerts = low_stock_alerts(data, products)

    product_alert_rows = ""
    for alert in alerts["product_alerts"][:12]:
        badge_class = "danger" if alert["status"] == "out" else "warn"
        units = "n/a" if alert["units_remaining"] is None else f'{alert["units_remaining"]:.1f}'
        product_alert_rows += f"""
        <tr>
          <td><span class="pill {badge_class}">{esc(alert["status"].upper())}</span></td>
          <td><strong>{esc(alert["product_label"])}</strong><div class="muted">{esc(alert["recipe_name"])}</div></td>
          <td>{esc(alert["limiting_ingredient"] or "No recipe ingredients")}</td>
          <td><strong>{esc(units)}</strong></td>
          <td><a href="/costs/recipe/{esc(alert["recipe_id"])}{token_query(request)}">Open recipe</a></td>
        </tr>
        """

    ingredient_alert_cards = ""
    for alert in alerts["ingredient_alerts"][:10]:
        badge_class = "danger" if alert["status"] == "out" else "warn"
        risk_items = "".join(
            f'<li>{esc(item["product_label"])} · {float(item["units_remaining"]):.1f} units left</li>'
            for item in alert["at_risk_products"][:5]
        )
        if not risk_items:
            risk_items = '<li class="muted">No linked product recipe is currently limited by this ingredient.</li>'
        ingredient_alert_cards += f"""
        <div class="alert-item">
          <span class="pill {badge_class}">{esc(alert["status"].upper())}</span>
          <h3>{esc(alert["ingredient_name"])}</h3>
          <p>Stock: <strong>{float(alert["stock_kg"]):.3f} kg</strong></p>
          <ul>{risk_items}</ul>
        </div>
        """

    total_stock_value = 0.0
    ingredient_rows = ""
    for iid, ing in sorted(ingredients.items(), key=lambda item: str(item[1].get("name") or "").lower()):
        stock = ingredient_stock(ing)
        cost_per_kg = float(ing.get("cost_per_kg") or 0)
        stock_value = max(stock["stk_act_kg"], 0) * cost_per_kg
        total_stock_value += stock_value
        provider_name = providers.get(str(ing.get("provider_id") or ""), "")
        ingredient_rows += f"""
        <tr>
          <td>{esc(iid)}</td>
          <td><strong>{esc(ing.get("name"))}</strong><div class="muted">{esc(provider_name or "No provider")}</div></td>
          <td class="money">S/ {cost_per_kg:.4f}</td>
          <td>{stock["stk_in_kg"]:.3f}</td>
          <td>{stock["stk_out_kg"]:.3f}</td>
          <td><strong>{stock["stk_act_kg"]:.3f}</strong></td>
          <td class="money">S/ {stock_value:.2f}</td>
          <td>
            <form method="post" action="/costs/ingredients/{esc(iid)}/stock{token_query(request)}">
              <div class="grid2">
                <input name="stk_in_delta_kg" type="number" step="0.001" min="0" value="0" aria-label="stock in kg">
                <input name="stk_out_delta_kg" type="number" step="0.001" min="0" value="0" aria-label="stock out kg">
              </div>
              <button class="secondary" type="submit" style="margin-top:8px;">Move stock</button>
            </form>
          </td>
        </tr>
        """

    recipe_rows = ""
    for rid, recipe in sorted(recipes.items(), key=lambda item: recipe_name(item[1]).lower()):
        summary = recipe_cost_summary(recipe, ingredients)
        product_status = recipe_product_status(recipe, ingredients, products)
        status_class = "danger" if product_status["status"] == "out" else "warn" if product_status["status"] == "low" else ""
        units_left = "n/a" if product_status["units_remaining"] is None else f'{product_status["units_remaining"]:.1f}'
        recipe_rows += f"""
        <tr>
          <td>{esc(rid)}</td>
          <td><a href="/costs/recipe/{esc(rid)}{token_query(request)}"><strong>{esc(recipe_name(recipe))}</strong></a></td>
          <td>{esc(recipe.get("yield_units") or 1)}</td>
          <td>{len(summary["lines"])}</td>
          <td><span class="pill {status_class}">{esc(product_status["status"].upper())}</span><div class="muted">{esc(product_status["limiting_ingredient"])} · {esc(units_left)} units</div></td>
          <td class="money">S/ {summary["total_cost"]:.4f}</td>
          <td class="money">S/ {summary["cost_per_unit"]:.4f}</td>
        </tr>
        """

    body = f"""
    <div class="card">
      <h2>Low stock alerts</h2>
      <p class="muted">Products are flagged when a recipe ingredient can support {alerts["unit_threshold"]:.0f} units or fewer. Unlinked ingredients are flagged below {alerts["kg_threshold"]:.3f} kg.</p>
      <div class="grid">
        <div class="alert-item"><div class="muted">Ingredient alerts</div><h2>{alerts["ingredient_alert_count"]}</h2></div>
        <div class="alert-item"><div class="muted">Products at risk</div><h2>{alerts["product_alert_count"]}</h2></div>
        <div class="alert-item"><div class="muted">Unit threshold</div><h2>{alerts["unit_threshold"]:.0f}</h2></div>
        <div class="alert-item"><div class="muted">KG floor</div><h2>{alerts["kg_threshold"]:.3f}</h2></div>
      </div>
      <h3>Products at risk</h3>
      <table>
        <thead><tr><th>Status</th><th>Product</th><th>Limiting ingredient</th><th>Units left</th><th>Action</th></tr></thead>
        <tbody>{product_alert_rows or '<tr><td colspan="5" class="muted">No linked product is currently low on recipe stock.</td></tr>'}</tbody>
      </table>
      <h3>Ingredient warnings</h3>
      <div class="alert-list">{ingredient_alert_cards or '<p class="muted">No low stock ingredient alerts.</p>'}</div>
    </div>
    <div class="grid2">
      <div class="card">
        <h2>Add ingredient</h2>
        <form method="post" action="/costs/ingredients{token_query(request)}">
          <label>Name</label><input name="name" required placeholder="Carne molida">
          <label>Provider</label><select name="provider_id">{provider_options()}</select>
          <div class="grid2">
            <div><label>Cost by kilo</label><input name="cost_per_kg" type="number" step="0.0001" min="0.0001" required placeholder="18.50"></div>
            <div><label>Currency</label><input name="currency" value="{esc(DEFAULT_MONEDA)}"></div>
          </div>
          <div class="grid">
            <div><label>stk_in kg</label><input name="stk_in_kg" type="number" step="0.001" min="0" value="0"></div>
            <div><label>stk_out kg</label><input name="stk_out_kg" type="number" step="0.001" min="0" value="0"></div>
            <div style="display:flex; align-items:end;"><button type="submit">Save ingredient</button></div>
          </div>
        </form>
      </div>
      <div class="card">
        <h2>Add recipe</h2>
        <form method="post" action="/costs/recipes{token_query(request)}">
          <label>Recipe name</label><input name="name" required placeholder="Burger doble">
          <label>Linked product</label><select name="product_id" required>{product_options(require_existing=True)}</select>
          <label>Yield units</label><input name="yield_units" type="number" step="0.001" min="0.001" value="1" required>
          <br><br><button type="submit">Create recipe</button>
        </form>
      </div>
    </div>
    <div class="card">
      <h2>Food cost recipes</h2>
      <p class="muted">Food cost only. Machinery and labour are intentionally excluded.</p>
      <table>
        <thead><tr><th>ID</th><th>Recipe</th><th>Yield</th><th>Ingredients</th><th>Stock status</th><th>Total cost</th><th>Cost / unit</th></tr></thead>
        <tbody>{recipe_rows or '<tr><td colspan="7" class="muted">No recipes yet.</td></tr>'}</tbody>
      </table>
    </div>
    <div class="card">
      <h2>Ingredients and stock</h2>
      <p class="muted">Current stock is calculated as stk_in - stk_out. Positive current stock value: <span class="money">S/ {total_stock_value:.2f}</span></p>
      <table>
        <thead><tr><th>ID</th><th>Ingredient</th><th>Cost/kg</th><th>stk_in kg</th><th>stk_out kg</th><th>stk_act kg</th><th>Stock value</th><th>Stock movement</th></tr></thead>
        <tbody>{ingredient_rows or '<tr><td colspan="8" class="muted">No ingredients yet.</td></tr>'}</tbody>
      </table>
    </div>
    """
    return html_page("Recipe Cost Calculator", body, flash=flash, auth_query=token_query(request))


@app.post("/costs/ingredients")
def create_ingredient(request: Request, name: str = Form(...), provider_id: str = Form(""), cost_per_kg: float = Form(...), currency: str = Form(DEFAULT_MONEDA), stk_in_kg: float = Form(0), stk_out_kg: float = Form(0), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    pg_post(f"/{INGREDIENT_COST_ENDPOINT}", {
        "nombre": name.strip(),
        "proveedor_id": int(provider_id) if str(provider_id or "").strip() else None,
        "costo_kg": parse_positive_float(cost_per_kg, "cost_per_kg"),
        "moneda": (currency or DEFAULT_MONEDA).strip().upper(),
        "stk_in": parse_nonnegative_float(stk_in_kg, "stk_in_kg"),
        "stk_out": parse_nonnegative_float(stk_out_kg, "stk_out_kg"),
        "active": True,
    })
    return RedirectResponse(url=with_token("/costs?flash=Ingredient+saved", request), status_code=303)


@app.post("/costs/ingredients/{ingredient_id}/stock")
def update_ingredient_stock(ingredient_id: str, request: Request, stk_in_delta_kg: float = Form(0), stk_out_delta_kg: float = Form(0), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    ing = data.get("ingredients", {}).get(str(ingredient_id))
    if not ing:
        raise HTTPException(status_code=404, detail="Ingredient not found")
    pg_patch(f"/{INGREDIENT_COST_ENDPOINT}?id=eq.{requests.utils.quote(str(ingredient_id))}", {
        "stk_in": float(ing.get("stk_in_kg") or 0) + parse_nonnegative_float(stk_in_delta_kg, "stk_in_delta_kg"),
        "stk_out": float(ing.get("stk_out_kg") or 0) + parse_nonnegative_float(stk_out_delta_kg, "stk_out_delta_kg"),
    })
    return RedirectResponse(url=with_token(f"/costs?flash=Stock+updated", request), status_code=303)


@app.post("/costs/recipes")
def create_recipe(request: Request, name: str = Form(...), product_id: str = Form(""), yield_units: float = Form(1), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    linked_product_id = active_product_id_or_400(product_id)
    created = pg_post(f"/{RECIPE_COST_ENDPOINT}", {
        "nombre": name.strip(),
        "producto_id": linked_product_id,
        "rendimiento_unidades": parse_positive_float(yield_units, "yield_units"),
        "active": True,
    })
    rid = str((created[0] if created else {}).get("id") or "")
    return RedirectResponse(url=with_token(f"/costs/recipe/{rid}?flash=Recipe+created", request), status_code=303)


@app.get("/costs/recipe/{recipe_id}", response_class=HTMLResponse)
def recipe_detail(recipe_id: str, request: Request, flash: str = "", x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    ingredients = data.get("ingredients", {})
    recipe = data.get("recipes", {}).get(str(recipe_id))
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    summary = recipe_cost_summary(recipe, ingredients)
    ingredient_options = "".join(
        f'<option value="{esc(iid)}">{esc(ing.get("name"))} · S/ {float(ing.get("cost_per_kg") or 0):.4f}/kg · stk {ingredient_stock(ing)["stk_act_kg"]:.3f}kg</option>'
        for iid, ing in sorted(ingredients.items(), key=lambda item: str(item[1].get("name") or "").lower())
    )
    rows = ""
    for idx, line in enumerate(summary["lines"]):
        ing = line["ingredient"]
        rows += f"""
        <tr>
          <td>{idx + 1}</td>
          <td><strong>{esc(ing.get("name"))}</strong></td>
          <td>{float(line.get("quantity_g") or 0):.3f}</td>
          <td>S/ {float(ing.get("cost_per_kg") or 0):.4f}</td>
          <td class="money">S/ {line["line_cost"]:.4f}</td>
        </tr>
        """
    body = f"""
    <div class="card">
      <h2>{esc(recipe_name(recipe))}</h2>
      <p class="muted">Yield: {esc(recipe.get("yield_units") or 1)} unit(s)</p>
      <p>Total recipe food cost: <span class="money">S/ {summary["total_cost"]:.4f}</span> · Cost per unit: <span class="money">S/ {summary["cost_per_unit"]:.4f}</span></p>
      <p>Stock status: <span class="pill {status_class}">{esc(product_status["status"].upper())}</span> · limiting ingredient: {esc(product_status["limiting_ingredient"] or "n/a")} · units left: <strong>{esc(units_left)}</strong></p>
      <p><a href="/costs{token_query(request)}">Back to recipe costs</a></p>
    </div>
    <div class="card">
      <h2>Add ingredient amount</h2>
      <form method="post" action="/costs/recipe/{esc(recipe_id)}/ingredients{token_query(request)}">
        <div class="grid">
          <div><label>Ingredient</label><select name="ingredient_id" required>{ingredient_options}</select></div>
          <div><label>Exact amount in grams</label><input name="quantity_g" type="number" step="0.001" min="0.001" required placeholder="180"></div>
          <div style="display:flex; align-items:end;"><button type="submit">Add to recipe</button></div>
        </div>
      </form>
    </div>
    <div class="card">
      <h2>Recipe ingredients</h2>
      <table>
        <thead><tr><th>#</th><th>Ingredient</th><th>Grams</th><th>Cost/kg</th><th>Line cost</th></tr></thead>
        <tbody>{rows or '<tr><td colspan="5" class="muted">No ingredients in this recipe yet.</td></tr>'}</tbody>
      </table>
    </div>
    """
    return html_page(f"Recipe {recipe_id}", body, flash=flash, auth_query=token_query(request))


@app.post("/costs/recipe/{recipe_id}/ingredients")
def add_recipe_ingredient(recipe_id: str, request: Request, ingredient_id: str = Form(...), quantity_g: float = Form(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    data = load_recipe_costs()
    recipe = data.get("recipes", {}).get(str(recipe_id))
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if str(ingredient_id) not in data.get("ingredients", {}):
        raise HTTPException(status_code=404, detail="Ingredient not found")
    existing = next(
        (line for line in recipe.get("ingredients", []) if str(line.get("ingredient_id")) == str(ingredient_id)),
        None,
    )
    if existing and existing.get("id"):
        pg_patch(f"/{RECIPE_INGREDIENT_COST_ENDPOINT}?id=eq.{requests.utils.quote(str(existing['id']))}", {
            "cantidad_g": parse_positive_float(quantity_g, "quantity_g"),
        })
    else:
        pg_post(f"/{RECIPE_INGREDIENT_COST_ENDPOINT}", {
            "receta_id": int(recipe_id),
            "ingrediente_id": int(ingredient_id),
            "cantidad_g": parse_positive_float(quantity_g, "quantity_g"),
        })
    return RedirectResponse(url=with_token(f"/costs/recipe/{recipe_id}?flash=Ingredient+added", request), status_code=303)


@app.get("/", response_class=HTMLResponse)
def index(request: Request, search: str = "", active_filter: str = "all", flash: str = "", x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)

    try:
        rows = get_products(search, active_filter)
        cols = product_columns()
        prices = latest_prices_by_product()
        recipe_data = load_recipe_costs()
        alerts = low_stock_alerts(recipe_data, product_lookup())
    except Exception as exc:
        return html_page("Replau Product Admin", f'<div class="error">{esc(type(exc).__name__)}: {esc(exc)}</div>')

    all_products = get_products("", "all")
    active_count = len([r for r in all_products if get_product_active(r, cols)])
    inactive_count = len(all_products) - active_count
    price_covered = len([r for r in all_products if prices.get(get_product_id(r))])
    recipe_count = len(recipe_data.get("recipes", {}))
    ingredient_count = len(recipe_data.get("ingredients", {}))

    product_rows = ""
    for r in rows:
        pid = get_product_id(r)
        code = get_product_code(r, cols)
        name = get_product_name(r, cols)
        active = get_product_active(r, cols)
        status = '<span class="pill active">ACTIVE</span>' if active else '<span class="pill inactive">INACTIVE</span>'
        product_rows += f"""
        <tr>
          <td>{esc(pid)}</td>
          <td>{product_image_html(pid, name)}</td>
          <td><strong>{esc(code)}</strong></td>
          <td>{esc(name)}</td>
          <td>{status}</td>
          <td>{render_price_badges(prices.get(pid, []))}</td>
          <td><a href="/product/{esc(pid)}{token_query(request)}">Edit</a></td>
        </tr>
        """

    body = f"""
    <div class="card">
      <h2>Catalog Finance Workspace</h2>
      <p class="muted">Producto, precio, receta, costo y stock en una sola vista de administración.</p>
      <div class="workspace-grid">
        <div class="workspace-card"><span>Productos activos</span><strong>{active_count}</strong></div>
        <div class="workspace-card"><span>Inactivos</span><strong>{inactive_count}</strong></div>
        <div class="workspace-card"><span>Con precio</span><strong>{price_covered}</strong></div>
        <div class="workspace-card"><span>Recetas</span><strong>{recipe_count}</strong></div>
        <div class="workspace-card"><span>Ingredientes</span><strong>{ingredient_count}</strong></div>
      </div>
      <div class="quick-actions">
        <a href="/recipes{token_query(request)}">Recipes</a>
        <a href="/costs{token_query(request)}">Costs / Stock</a>
        <a href="/api/recipe-costs{token_query(request)}" target="_blank">Cost API</a>
        <a href="/menu" target="_blank">Public Menu</a>
      </div>
      <p class="muted">Low-stock alerts: {esc(alerts.get("ingredient_alert_count", 0))} ingredient · {esc(alerts.get("product_alert_count", 0))} product</p>
    </div>

    <div class="card">
      <h2>Add product</h2>
      <form method="post" action="/products{token_query(request)}">
        <div class="grid">
          <div><label>Code / SKU</label><input name="code" required placeholder="HAMB001"></div>
          <div><label>Name</label><input name="name" required placeholder="HAMBURGUESA SIMPLE CON QUESO"></div>
          <div><label>Unit</label><input name="unidad" value="{esc(DEFAULT_UNIDAD)}" required></div>
          <div><label>Price</label><input name="precio" type="number" step="0.01" required placeholder="15.00"></div>
        </div>
        <div class="grid">
          <div><label>Currency</label><input name="moneda" value="{esc(DEFAULT_MONEDA)}"></div>
          <div><label>Active</label><select name="active"><option value="true">Active</option><option value="false">Inactive</option></select></div>
          <div style="display:flex; align-items:end;"><button type="submit">Create product + price</button></div>
        </div>
      </form>
    </div>

    <div class="card">
      <h2>Products</h2>
      <form method="get" action="/{token_query(request)}">
        {f'<input type="hidden" name="token" value="{esc(request.query_params.get("token"))}">' if token_query(request) else ""}
        <div class="grid">
          <div><label>Search</label><input name="search" value="{esc(search)}" placeholder="hamburguesa, coca, HAMB001"></div>
          <div><label>Status</label>
            <select name="active_filter">
              <option value="all" {"selected" if active_filter=="all" else ""}>All</option>
              <option value="true" {"selected" if active_filter=="true" else ""}>Active</option>
              <option value="false" {"selected" if active_filter=="false" else ""}>Inactive</option>
            </select>
          </div>
          <div style="display:flex; align-items:end;"><button type="submit">Filter</button></div>
        </div>
      </form>
      <p class="muted">{len(rows)} product(s)</p>
      <table>
        <thead><tr><th>ID</th><th>Image</th><th>Code</th><th>Name</th><th>Status</th><th>Latest prices</th><th>Action</th></tr></thead>
        <tbody>{product_rows or '<tr><td colspan="7" class="muted">No products found.</td></tr>'}</tbody>
      </table>
    </div>
    """
    return html_page("Replau Product Admin", body, flash=flash, auth_query=token_query(request))


@app.post("/products")
def create_product(request: Request, code: str = Form(...), name: str = Form(...), unidad: str = Form(DEFAULT_UNIDAD), precio: float = Form(...), moneda: str = Form(DEFAULT_MONEDA), active: str = Form("true"), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    existing = find_product_by_code(code)
    is_active = normalize_bool(active)

    if existing:
        pid = get_product_id(existing)
        pg_patch(f"/{PRODUCTS_ENDPOINT}?id=eq.{pid}", build_product_update_payload(name, is_active))
    else:
        created = pg_post(f"/{PRODUCTS_ENDPOINT}", build_product_payload(code, name, is_active))
        if isinstance(created, list) and created:
            pid = get_product_id(created[0])
        else:
            found = find_product_by_code(code)
            if not found:
                raise HTTPException(status_code=500, detail="Product created but ID not found")
            pid = get_product_id(found)

    deactivate_old_prices(pid, unidad)
    pg_post(f"/{PRICES_ENDPOINT}", build_price_payload(pid, unidad, precio, moneda, True))
    return RedirectResponse(url=with_token("/?flash=Product+saved", request), status_code=303)


@app.get("/product/{product_id}", response_class=HTMLResponse)
def product_detail(product_id: int, request: Request, flash: str = "", x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)
    rows = pg_get(f"/{PRODUCTS_ENDPOINT}?id=eq.{product_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Product not found")

    product = rows[0]
    cols = product_columns()
    name = get_product_name(product, cols)
    code = get_product_code(product, cols)
    active = get_product_active(product, cols)

    pcols = price_columns()
    product_col = first_existing(pcols, ["producto_id", "product_id"]) or "producto_id"
    price_rows = pg_get(f"/{PRICES_ENDPOINT}?{product_col}=eq.{product_id}&order=id.desc&limit=100")
    visible_price_cols = [c for c in pcols if c in ["id", product_col, "unidad", "unit", "precio", "price", "moneda", "currency", "active", "activo", "valid_from", "valid_to", "created_at"]]

    price_headers = "".join(f"<th>{esc(c)}</th>" for c in visible_price_cols)
    price_table_rows = "".join("<tr>" + "".join(f"<td>{esc(p.get(c))}</td>" for c in visible_price_cols) + "</tr>" for p in price_rows)
    image_url = product_image_url(product_id)

    body = f"""
    <div class="card">
      <h2>Product image</h2>
      <div class="grid2">
        <div>{product_image_html(product_id, name, size="photo")}</div>
        <div>
          <p class="muted">Upload a PNG, JPG, or WEBP image. It will appear in Product Admin and the generated public menu.</p>
          <form method="post" action="/product/{product_id}/image{token_query(request)}" enctype="multipart/form-data">
            <label>Image file</label>
            <input type="file" name="image" accept="image/png,image/jpeg,image/webp" required>
            <br><br><button type="submit">Upload image</button>
            {f'<a href="{esc(image_url)}" target="_blank" style="margin-left:12px;">Open image</a>' if image_url else ''}
          </form>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>Edit product</h2>
      <form method="post" action="/product/{product_id}/update{token_query(request)}">
        <div class="grid2">
          <div><label>Code</label><input value="{esc(code)}" disabled></div>
          <div><label>Name</label><input name="name" value="{esc(name)}" required></div>
        </div>
        <label>Status</label>
        <select name="active">
          <option value="true" {"selected" if active else ""}>Active</option>
          <option value="false" {"selected" if not active else ""}>Inactive</option>
        </select>
        <br><br>
        <button type="submit">Save product</button>
        <a href="/{token_query(request)}" style="margin-left:12px;">Back</a>
      </form>
    </div>
    <div class="card">
      <h2>Add / update price</h2>
      <p class="muted">This deactivates the old active price for the same unit and creates a new active price.</p>
      <form method="post" action="/product/{product_id}/price{token_query(request)}">
        <div class="grid">
          <div><label>Unit</label><input name="unidad" value="{esc(DEFAULT_UNIDAD)}" required></div>
          <div><label>Price</label><input name="precio" type="number" step="0.01" required></div>
          <div><label>Currency</label><input name="moneda" value="{esc(DEFAULT_MONEDA)}"></div>
          <div style="display:flex; align-items:end;"><button type="submit">Add price</button></div>
        </div>
      </form>
    </div>
    <div class="card">
      <h2>Prices</h2>
      <table><thead><tr>{price_headers}</tr></thead><tbody>{price_table_rows or '<tr><td class="muted">No prices.</td></tr>'}</tbody></table>
    </div>
    <div class="card"><h2>Raw product row</h2><pre>{esc(product)}</pre></div>
    """
    return html_page(f"Product {product_id}", body, flash=flash, auth_query=token_query(request))


@app.post("/product/{product_id}/update")
def update_product(product_id: int, request: Request, name: str = Form(...), active: str = Form("true"), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    payload = build_product_update_payload(name, normalize_bool(active))
    if payload:
        pg_patch(f"/{PRODUCTS_ENDPOINT}?id=eq.{product_id}", payload)
    return RedirectResponse(url=with_token(f"/product/{product_id}?flash=Product+updated", request), status_code=303)


@app.post("/product/{product_id}/price")
def add_price(product_id: int, request: Request, unidad: str = Form(DEFAULT_UNIDAD), precio: float = Form(...), moneda: str = Form(DEFAULT_MONEDA), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    create_or_update_price(product_id, unidad, precio, moneda)
    return RedirectResponse(url=with_token(f"/product/{product_id}?flash=Price+saved", request), status_code=303)


@app.post("/product/{product_id}/image")
async def upload_product_image(product_id: int, request: Request, image: UploadFile = File(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> RedirectResponse:
    check_auth(request, x_admin_token)
    rows = pg_get(f"/{PRODUCTS_ENDPOINT}?id=eq.{product_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Product not found")
    cols = product_columns()
    code = get_product_code(rows[0], cols)
    data = await image.read()
    save_product_image(product_id, code, image, data)
    return RedirectResponse(url=with_token(f"/product/{product_id}?flash=Image+saved", request), status_code=303)


@app.get("/bulk", response_class=HTMLResponse)
def bulk_page(request: Request, x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)
    sample = "cdg_prod,nombre,unidad,precio,moneda,active\nHAMB001,HAMBURGUESA SIMPLE CON QUESO,UNIDAD,15.00,PEN,true\nBEB001,COCA COLA MEDIANA,UNIDAD,7.00,PEN,true"
    body = f"""
    <div class="card">
      <h2>Bulk CSV import</h2>
      <p class="muted">Columns: cdg_prod,nombre,unidad,precio,moneda,active</p>
      <form method="post" action="/bulk{token_query(request)}">
        <label>Paste CSV</label>
        <textarea name="csv_text">{esc(sample)}</textarea>
        <br><br><button type="submit">Import CSV</button>
      </form>
    </div>
    <div class="card">
      <h2>Upload CSV file</h2>
      <form method="post" action="/bulk-file{token_query(request)}" enctype="multipart/form-data">
        <input type="file" name="file" accept=".csv,text/csv"><br><br><button type="submit">Upload CSV</button>
      </form>
    </div>
    """
    return html_page("Bulk Product Import", body, auth_query=token_query(request))


def import_csv_text(csv_text: str) -> Tuple[int, List[str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    count = 0
    messages: List[str] = []
    for i, row in enumerate(reader, start=2):
        code = (row.get("cdg_prod") or row.get("codigo") or row.get("sku") or "").strip()
        name = (row.get("nombre") or row.get("producto") or row.get("name") or "").strip()
        unidad = (row.get("unidad") or row.get("unit") or DEFAULT_UNIDAD).strip().upper()
        moneda = (row.get("moneda") or row.get("currency") or DEFAULT_MONEDA).strip().upper()
        active = normalize_bool(row.get("active", "true"))
        try:
            precio = float(str(row.get("precio") or row.get("price") or "").replace(",", "."))
        except Exception:
            messages.append(f"Line {i}: invalid price")
            continue
        if not code or not name:
            messages.append(f"Line {i}: missing code/name")
            continue
        existing = find_product_by_code(code)
        if existing:
            pid = get_product_id(existing)
            pg_patch(f"/{PRODUCTS_ENDPOINT}?id=eq.{pid}", build_product_update_payload(name, active))
        else:
            created = pg_post(f"/{PRODUCTS_ENDPOINT}", build_product_payload(code, name, active))
            pid = get_product_id(created[0]) if isinstance(created, list) and created else get_product_id(find_product_by_code(code))
        deactivate_old_prices(pid, unidad)
        pg_post(f"/{PRICES_ENDPOINT}", build_price_payload(pid, unidad, precio, moneda, True))
        count += 1
    return count, messages


@app.post("/bulk")
def bulk_import(request: Request, csv_text: str = Form(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)
    try:
        count, messages = import_csv_text(csv_text)
        msg_html = f"<p>Imported/updated {count} product(s).</p>"
        if messages:
            msg_html += "<ul>" + "".join(f"<li>{esc(m)}</li>" for m in messages) + "</ul>"
        return html_page("Bulk Import Result", f'<div class="card">{msg_html}<p><a href="/{token_query(request)}">Back to products</a></p></div>', auth_query=token_query(request))
    except Exception as exc:
        return html_page("Bulk Import Error", f'<div class="error">{esc(type(exc).__name__)}: {esc(exc)}</div><p><a href="/bulk{token_query(request)}">Back</a></p>', auth_query=token_query(request))


@app.post("/bulk-file")
async def bulk_file_import(request: Request, file: UploadFile = File(...), x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token")) -> HTMLResponse:
    check_auth(request, x_admin_token)
    data = await file.read()
    text = data.decode("utf-8-sig")
    count, messages = import_csv_text(text)
    msg_html = f"<p>Imported/updated {count} product(s) from {esc(file.filename)}.</p>"
    if messages:
        msg_html += "<ul>" + "".join(f"<li>{esc(m)}</li>" for m in messages) + "</ul>"
    return html_page("Bulk Import Result", f'<div class="card">{msg_html}<p><a href="/{token_query(request)}">Back to products</a></p></div>', auth_query=token_query(request))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("replau_product_admin:app", host=ADMIN_HOST, port=ADMIN_PORT, reload=False)
