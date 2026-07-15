#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, JSONResponse, Response

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
WHATSAPP_NUMBER = "".join(c for c in os.environ.get("PUBLIC_WHATSAPP_NUMBER", "51973875456") if c.isdigit())
STORE_NAME = os.environ.get("PUBLIC_STORE_NAME", "Replau Burger").strip() or "Replau Burger"
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8796"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))
PRODUCT_ADMIN_URL = os.environ.get("PRODUCT_ADMIN_URL", "http://127.0.0.1:8794").rstrip("/")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://orders.replau.com").rstrip("/")
DEFAULT_DELIVERY = max(0.0, float(os.environ.get("DEFAULT_DELIVERY", "0")))
RESTAURANT_STATUS_PATH = Path(os.environ.get("REPLAU_RESTAURANT_STATUS_PATH", "/home/guill/.openclaw/workspace/replau_restaurant_status.json"))
CHECKOUT_RATE_LIMIT = max(1, int(os.environ.get("CHECKOUT_RATE_LIMIT", "8")))
CHECKOUT_RATE_WINDOW = max(60, int(os.environ.get("CHECKOUT_RATE_WINDOW", "900")))

app = FastAPI(title="Replau Public Storefront", docs_url=None, redoc_url=None, openapi_url=None)
_checkout_lock = threading.Lock()
_checkout_attempts: dict[str, deque[float]] = defaultdict(deque)
_checkout_results: dict[str, tuple[float, dict[str, Any]]] = {}


class CheckoutItem(BaseModel):
    product_id: int
    quantity: int = Field(ge=1, le=20)


class CheckoutRequest(BaseModel):
    customer_name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=8, max_length=24)
    fulfillment: str = Field(pattern="^(DELIVERY|PICKUP)$")
    address: str = Field(default="", max_length=300)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    payment_method: str = Field(pattern="^(YAPE|PLIN|TRANSFERENCIA|CONTRA_ENTREGA)$")
    notes: str = Field(default="", max_length=300)
    idempotency_key: str = Field(min_length=16, max_length=80)
    website: str = Field(default="", max_length=1)
    items: list[CheckoutItem] = Field(min_length=1, max_length=40)


def pg_get(path: str) -> Any:
    response = requests.get(POSTGREST_BASE_URL + path, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def pg_post(path: str, payload: dict[str, Any]) -> Any:
    response = requests.post(POSTGREST_BASE_URL + path, json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def restaurant_status() -> dict[str, Any]:
    default = {"accepting_orders": True, "customer_message": "Por el momento no estamos recibiendo pedidos."}
    try:
        if RESTAURANT_STATUS_PATH.exists():
            data = json.loads(RESTAURANT_STATUS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**default, **data}
    except (OSError, ValueError):
        return {**default, "accepting_orders": False}
    return default


def client_identity(request: Request) -> str:
    cloudflare_ip = request.headers.get("cf-connecting-ip", "").strip()
    if re.fullmatch(r"[0-9a-fA-F:.]{3,45}", cloudflare_ip):
        return cloudflare_ip
    return request.client.host if request.client else "unknown"


def permit_checkout(identity: str, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    with _checkout_lock:
        attempts = _checkout_attempts[identity]
        while attempts and current - attempts[0] > CHECKOUT_RATE_WINDOW:
            attempts.popleft()
        if len(attempts) >= CHECKOUT_RATE_LIMIT:
            return False
        attempts.append(current)
        return True


def checkout_items(payload: CheckoutRequest) -> list[dict[str, Any]]:
    catalog = {item["id"]: item for item in menu_items()}
    quantities: dict[int, int] = defaultdict(int)
    for item in payload.items:
        quantities[item.product_id] += item.quantity
        if quantities[item.product_id] > 20:
            raise ValueError("La cantidad máxima por producto es 20.")
    missing = [product_id for product_id in quantities if product_id not in catalog]
    if missing:
        raise ValueError("Uno de los productos ya no está disponible. Actualiza el menú e inténtalo nuevamente.")
    return [
        {"producto_id": product_id, "producto_texto": catalog[product_id]["name"], "cantidad": quantity, "unidad": catalog[product_id]["unit"]}
        for product_id, quantity in quantities.items()
    ]


def safe_tracking_url(order_url: Any) -> str:
    value = str(order_url or "")
    if "/order/" not in value:
        return ""
    return value.replace("/order/", "/track/")


def product_presentation(code: str, name: str) -> tuple[str, str, str]:
    code = code.upper()
    if code.startswith("COMBO_"):
        return "Combos", "🍔🍟", "Hamburguesa y papas en una combinación lista para disfrutar."
    if code.startswith("WINGS_"):
        return "Alitas", "🍗", "Alitas fritas crujientes preparadas al momento."
    if code.startswith(("BURGER_", "CHICKEN_BURGER")):
        detail = "de pollo " if "POLLO" in name.upper() else ""
        return "Hamburguesas", "🍔", f"Hamburguesa {detail}preparada al momento con ingredientes frescos."
    if code.startswith(("FRIES_", "ONION_RINGS_", "CHICKEN_STRIPS_")):
        return "Acompañamientos", "🍟", "Acompañamiento caliente y crujiente, ideal para completar tu pedido."
    if code.startswith(("SODA_", "WATER_")):
        return "Bebidas", "🥤", "Bebida fría para acompañar tu pedido."
    if code.endswith("_EXTRA"):
        return "Extras", "➕", "Agrega este extra a tu pedido y personalízalo a tu gusto."
    return "Otros", "🍽️", "Preparado al momento para tu pedido."


def product_images() -> dict[int, str]:
    try:
        response = requests.get(f"{PRODUCT_ADMIN_URL}/api/menu", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        rows = response.json().get("items") or []
    except (requests.RequestException, ValueError, AttributeError):
        return {}
    images: dict[int, str] = {}
    for row in rows:
        url = str(row.get("image_url") or "")
        if re.fullmatch(r"/media/products/[A-Za-z0-9._%+-]+", url):
            images[int(row["id"])] = url
    return images


def menu_items() -> list[dict[str, Any]]:
    products = pg_get(
        "/productos?select=id,cdg_prod,nombre,tipo_producto"
        "&active=eq.true&tipo_producto=eq.TERMINADO&order=id.asc"
    )
    prices = pg_get(
        "/producto_precios?select=producto_id,precio,moneda,unidad,valid_from,id"
        "&active=eq.true&order=valid_from.desc,id.desc"
    )
    latest: dict[int, dict[str, Any]] = {}
    for price in prices:
        product_id = int(price["producto_id"])
        latest.setdefault(product_id, price)
    images = product_images()
    items = []
    for product in products:
        product_id = int(product["id"])
        price = latest.get(product_id)
        if not price:
            continue
        code = str(product.get("cdg_prod") or "")
        name = str(product.get("nombre") or "")
        category, icon, description = product_presentation(code, name)
        items.append({
            "id": product_id,
            "code": code,
            "name": name,
            "price": float(price.get("precio") or 0),
            "currency": str(price.get("moneda") or "PEN"),
            "unit": str(price.get("unidad") or "UNIDAD"),
            "category": category,
            "icon": icon,
            "description": description,
            "image_url": images.get(product_id, ""),
        })
    return items


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        items = menu_items()
        return {"ok": True, "app": "replau-public-storefront", "sellable_items": len(items)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/api/menu")
def api_menu() -> JSONResponse:
    return JSONResponse({"ok": True, "store": STORE_NAME, "items": menu_items()})


@app.get("/api/store-status")
def api_store_status() -> JSONResponse:
    status = restaurant_status()
    return JSONResponse({"ok": True, "accepting_orders": bool(status.get("accepting_orders", True)), "customer_message": str(status.get("customer_message") or "")})


@app.post("/api/checkout")
def api_checkout(payload: CheckoutRequest, request: Request) -> JSONResponse:
    if payload.website:
        return JSONResponse({"ok": False, "error": "No se pudo procesar el pedido."}, status_code=400)
    status = restaurant_status()
    if not status.get("accepting_orders", True):
        return JSONResponse({"ok": False, "error": str(status.get("customer_message") or "Por el momento no estamos recibiendo pedidos.")}, status_code=409)
    phone = "".join(character for character in payload.phone if character.isdigit())
    if not 9 <= len(phone) <= 15:
        return JSONResponse({"ok": False, "error": "Ingresa un número de WhatsApp válido."}, status_code=422)
    if payload.fulfillment == "DELIVERY" and len(payload.address.strip()) < 8:
        return JSONResponse({"ok": False, "error": "Ingresa una dirección de entrega completa."}, status_code=422)
    with _checkout_lock:
        cached = _checkout_results.get(payload.idempotency_key)
        if cached and time.monotonic() - cached[0] < 86400:
            return JSONResponse(cached[1])
    if not permit_checkout(client_identity(request)):
        return JSONResponse({"ok": False, "error": "Demasiados intentos. Espera unos minutos antes de volver a intentar."}, status_code=429)
    try:
        items = checkout_items(payload)
        address = payload.address.strip() if payload.fulfillment == "DELIVERY" else "Recojo en restaurante"
        delivery = DEFAULT_DELIVERY if payload.fulfillment == "DELIVERY" else 0
        result = pg_post("/rpc/confirmar_pedido_whatsapp", {
            "p_whatsapp_number": phone,
            "p_customer_name": payload.customer_name.strip(),
            "p_payment_method": payload.payment_method,
            "p_latitude": payload.latitude if payload.fulfillment == "DELIVERY" else None,
            "p_longitude": payload.longitude if payload.fulfillment == "DELIVERY" else None,
            "p_detected_address": address,
            "p_confirmed_address": address,
            "p_items": items,
            "p_base_url": PUBLIC_BASE_URL,
            "p_delivery": delivery,
            "p_observacion": "Pedido confirmado desde tienda web" + (f" | Notas: {payload.notes.strip()}" if payload.notes.strip() else ""),
        })
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    except requests.RequestException:
        return JSONResponse({"ok": False, "error": "No pudimos registrar el pedido. Inténtalo nuevamente."}, status_code=502)
    response = {"ok": True, "order_number": result.get("pedido_num"), "total": result.get("total"), "payment_method": result.get("payment_method"), "tracking_url": safe_tracking_url(result.get("order_url")), "whatsapp_url": f"https://wa.me/{WHATSAPP_NUMBER}?text={quote('Hola, acabo de crear el pedido ' + str(result.get('pedido_num') or '') + '.', safe='')}"}
    with _checkout_lock:
        _checkout_results[payload.idempotency_key] = (time.monotonic(), response)
    return JSONResponse(response, status_code=201)


@app.get("/media/products/{filename}")
def product_image(filename: str) -> Response:
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+", filename):
        return Response(status_code=404)
    try:
        response = requests.get(f"{PRODUCT_ADMIN_URL}/media/products/{filename}", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException:
        return Response(status_code=404)
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        return Response(status_code=404)
    return Response(response.content, media_type=content_type, headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})


@app.get("/", response_class=HTMLResponse)
def storefront() -> HTMLResponse:
    items_json = json.dumps(menu_items(), ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(STORE_NAME)
    whatsapp_url = f"https://wa.me/{quote(WHATSAPP_NUMBER, safe='')}"
    return HTMLResponse(f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="description" content="Menú y pedidos por WhatsApp de {title}">
  <title>{title} · Pedidos</title>
  <style>
    :root{{--bg:#fff7ed;--ink:#28160d;--muted:#78665b;--brand:#dc2626;--brand2:#991b1b;--card:#fff;--line:#ead8c8;--green:#16a34a}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}}
    header{{position:sticky;top:0;z-index:5;background:rgba(255,247,237,.95);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}}
    .bar,.wrap{{max-width:1180px;margin:auto;padding:16px 20px}} .bar{{display:flex;justify-content:space-between;align-items:center;gap:12px}}
    h1{{font-size:24px;margin:0}} .tag{{color:var(--muted);font-size:13px}} .cart-chip{{border:0;border-radius:999px;background:var(--ink);color:#fff;padding:11px 15px;font-weight:800;cursor:pointer}}
    .hero{{padding:34px 0 14px}} .hero h2{{font-size:clamp(30px,6vw,56px);line-height:1;margin:0 0 12px;max-width:760px}} .hero p{{color:var(--muted);font-size:18px;max-width:680px}}
    .tools{{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 18px}} .search{{flex:1;min-width:220px;margin:0;padding:13px 15px;border:1px solid var(--line);border-radius:14px;background:#fff;font:inherit}}
    .categories{{display:flex;gap:8px;overflow:auto;padding-bottom:4px}} .category{{white-space:nowrap;border:1px solid var(--line);border-radius:999px;background:#fff;padding:11px 14px;font-weight:800;cursor:pointer}} .category.active{{background:var(--ink);color:#fff;border-color:var(--ink)}}
    .layout{{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:22px;align-items:start}} .menu{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
    .product,.cart{{background:var(--card);border:1px solid var(--line);border-radius:22px;box-shadow:0 10px 30px rgba(78,40,18,.08)}} .product{{overflow:hidden;display:flex;flex-direction:column;min-height:395px}}
    .photo{{width:100%;aspect-ratio:4/3;padding:8px;background:#fff8ed;display:grid;place-items:center;font-size:60px;overflow:hidden}} .photo img{{display:block;width:100%;height:100%;object-fit:contain;object-position:center}}
    .product-body{{padding:18px;display:flex;flex:1;flex-direction:column}} .product h3{{font-size:19px;margin:5px 0 8px}} .code{{font-size:11px;color:var(--muted);letter-spacing:.06em}} .description{{color:var(--muted);font-size:14px;line-height:1.45;margin:0 0 14px}} .price{{font-size:23px;font-weight:900;margin:auto 0 14px}}
    .category-label{{font-size:11px;color:var(--brand2);font-weight:900;text-transform:uppercase;letter-spacing:.05em}}
    button.add{{width:100%;border:0;border-radius:12px;background:var(--brand);color:#fff;padding:11px;font-weight:850;cursor:pointer}} button.add:hover{{background:var(--brand2)}}
    .cart{{position:sticky;top:92px;padding:18px}} .cart h2{{margin:0 0 6px}} .empty{{color:var(--muted);padding:24px 0;text-align:center}}
    .cart-line{{display:grid;grid-template-columns:1fr auto;gap:8px;padding:12px 0;border-bottom:1px solid var(--line)}} .qty{{display:flex;align-items:center;gap:8px}}
    .qty button{{width:30px;height:30px;border:1px solid var(--line);border-radius:9px;background:#fff;font-size:18px;cursor:pointer}} .total{{display:flex;justify-content:space-between;font-size:20px;font-weight:900;padding:18px 0}}
    label{{font-size:13px;font-weight:800}} input{{width:100%;margin:7px 0 14px;padding:12px;border:1px solid var(--line);border-radius:12px;font:inherit}}
    .checkout{{display:block;width:100%;border:0;border-radius:14px;background:var(--brand);color:#fff;padding:14px;font-weight:900;font-size:16px;cursor:pointer;text-align:center}} .checkout:disabled{{opacity:.6;cursor:wait}}
    .notice{{font-size:12px;color:var(--muted);line-height:1.45;margin:12px 0 0}} footer{{text-align:center;color:var(--muted);padding:40px 20px}}
    .modal-backdrop{{position:fixed;inset:0;z-index:30;background:rgba(40,22,13,.68);display:none;align-items:center;justify-content:center;padding:18px}} .modal-backdrop.open{{display:flex}}
    .modal{{width:min(620px,100%);max-height:92vh;overflow:auto;background:#fff;border-radius:22px;padding:22px;box-shadow:0 24px 70px rgba(0,0,0,.25)}} .modal-head{{display:flex;justify-content:space-between;gap:12px;align-items:center}} .modal h2{{margin:0}} .close{{border:0;background:transparent;font-size:28px;cursor:pointer}}
    .choice-row{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:8px 0 15px}} .choice{{border:1px solid var(--line);border-radius:12px;background:#fff;padding:12px;font-weight:800;cursor:pointer}} .choice.active{{border-color:var(--brand);background:#fff1f1;color:var(--brand2)}}
    textarea,select{{width:100%;margin:7px 0 14px;padding:12px;border:1px solid var(--line);border-radius:12px;font:inherit}} .location{{border:1px solid var(--line);background:#fff;padding:10px 12px;border-radius:12px;cursor:pointer;margin-bottom:14px}} .error{{display:none;color:#b91c1c;background:#fef2f2;border-radius:10px;padding:10px;margin:0 0 12px}} .success{{text-align:center;padding:16px 4px}} .success .check{{font-size:52px}} .success a{{display:block;margin-top:10px;padding:12px;border-radius:12px;text-decoration:none;font-weight:850;background:var(--ink);color:#fff}} .success a.secondary{{background:var(--green)}} .hp{{position:absolute;left:-10000px}}
    @media(max-width:850px){{.layout{{display:block}}.menu{{grid-template-columns:repeat(2,minmax(0,1fr))}}.cart{{position:fixed;inset:auto 0 0;top:auto;z-index:10;border-radius:22px 22px 0 0;max-height:78vh;overflow:auto;transform:translateY(calc(100% - 64px));transition:.25s}}.cart.open{{transform:none}}.cart h2{{cursor:pointer}}body.cart-open{{overflow:hidden}}}}
    @media(max-width:700px){{.menu{{grid-template-columns:1fr}}.hero{{padding-top:22px}}.photo{{aspect-ratio:1/1}}.product{{min-height:420px}}}}
  </style>
</head>
<body>
<header><div class="bar"><div><h1>{title}</h1><div class="tag">Pedidos en línea</div></div><button class="cart-chip" onclick="toggleCart(true)">Carrito · <span id="cartCount">0</span></button></div></header>
<main class="wrap">
  <section class="hero"><h2>Elige, confirma y sigue tu pedido en línea.</h2><p>Arma tu carrito, indica dónde lo quieres y recibe tu número de pedido al instante.</p></section>
  <div class="tools"><input class="search" id="search" type="search" placeholder="Buscar hamburguesas, papas, bebidas..." oninput="renderMenu()"><div class="categories" id="categories"></div></div>
  <div class="layout"><section class="menu" id="menu"></section>
    <aside class="cart" id="cart"><h2 onclick="toggleCart()">Tu carrito</h2><div id="cartLines"></div><div class="total"><span>Total estimado</span><span id="total">S/ 0.00</span></div>
      <button class="checkout" onclick="openCheckout()">Finalizar pedido</button>
      <p class="notice">Los productos y precios se validan nuevamente antes de crear el pedido.</p>
    </aside>
  </div>
</main><footer>{title} · Menú actualizado desde Replau</footer>
<div class="modal-backdrop" id="checkoutModal" role="dialog" aria-modal="true" aria-labelledby="checkoutTitle"><section class="modal"><div class="modal-head"><h2 id="checkoutTitle">Finalizar pedido</h2><button class="close" onclick="closeCheckout()" aria-label="Cerrar">×</button></div><div id="checkoutForm">
  <p class="error" id="checkoutError"></p>
  <label for="checkoutName">Nombre</label><input id="checkoutName" autocomplete="name" maxlength="80" placeholder="Ej. Juan Pérez">
  <label for="checkoutPhone">WhatsApp</label><input id="checkoutPhone" inputmode="tel" autocomplete="tel" maxlength="24" placeholder="Ej. 973 875 456">
  <label>¿Cómo recibirás el pedido?</label><div class="choice-row"><button class="choice active" id="deliveryChoice" onclick="setFulfillment('DELIVERY')">Delivery</button><button class="choice" id="pickupChoice" onclick="setFulfillment('PICKUP')">Recojo</button></div>
  <div id="deliveryFields"><label for="checkoutAddress">Dirección y referencia</label><textarea id="checkoutAddress" maxlength="300" rows="3" placeholder="Calle, número, distrito y referencia"></textarea><button class="location" onclick="useLocation()" id="locationButton">📍 Usar mi ubicación actual</button></div>
  <label for="checkoutPayment">Forma de pago</label><select id="checkoutPayment"><option value="CONTRA_ENTREGA">Contra entrega</option><option value="YAPE">Yape</option><option value="PLIN">Plin</option><option value="TRANSFERENCIA">Transferencia</option></select>
  <label for="checkoutNotes">Notas (opcional)</label><textarea id="checkoutNotes" maxlength="300" rows="2" placeholder="Sin cebolla, tocar el timbre..."></textarea>
  <label class="hp" aria-hidden="true">Website<input id="checkoutWebsite" tabindex="-1" autocomplete="off"></label>
  <button class="checkout" id="placeOrder" onclick="placeOrder()">Crear pedido</button><p class="notice">Al crear el pedido confirmas que los datos ingresados son correctos. Para pagos digitales coordinaremos el comprobante por WhatsApp.</p>
</div><div id="checkoutSuccess" class="success" hidden></div></section></div>
<script>
const ITEMS={items_json}; const WA={json.dumps(whatsapp_url)}; const CATEGORY_ORDER=['Todos','Combos','Hamburguesas','Alitas','Acompañamientos','Bebidas','Extras','Otros']; let activeCategory='Todos'; let cart=JSON.parse(localStorage.getItem('replau-cart')||'{{}}');let fulfillment='DELIVERY',coords={{latitude:null,longitude:null}},submitting=false;
const money=n=>new Intl.NumberFormat('es-PE',{{style:'currency',currency:'PEN'}}).format(n);
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function save(){{localStorage.setItem('replau-cart',JSON.stringify(cart));renderCart()}}
function add(id){{cart[id]=(cart[id]||0)+1;save();toggleCart(true)}}
function change(id,d){{cart[id]=Math.max(0,(cart[id]||0)+d);if(!cart[id])delete cart[id];save()}}
function setCategory(category){{activeCategory=category;renderCategories();renderMenu()}}
function renderCategories(){{const available=new Set(ITEMS.map(i=>i.category));const categories=CATEGORY_ORDER.filter(c=>c==='Todos'||available.has(c));document.getElementById('categories').innerHTML=categories.map(c=>`<button class="category ${{c===activeCategory?'active':''}}" onclick='setCategory(${{JSON.stringify(c)}})'>${{escapeHtml(c)}}</button>`).join('')}}
function renderMenu(){{const query=document.getElementById('search').value.trim().toLocaleLowerCase('es');const visible=ITEMS.filter(i=>(activeCategory==='Todos'||i.category===activeCategory)&&(!query||`${{i.name}} ${{i.description}} ${{i.category}}`.toLocaleLowerCase('es').includes(query)));document.getElementById('menu').innerHTML=visible.map(i=>`<article class="product"><div class="photo">${{i.image_url?`<img src="${{escapeHtml(i.image_url)}}" alt="${{escapeHtml(i.name)}}" loading="lazy">`:escapeHtml(i.icon)}}</div><div class="product-body"><div class="category-label">${{escapeHtml(i.category)}}</div><div class="code">${{escapeHtml(i.code)}}</div><h3>${{escapeHtml(i.name)}}</h3><p class="description">${{escapeHtml(i.description)}}</p><div class="price">${{money(i.price)}}</div><button class="add" onclick="add(${{i.id}})">Agregar</button></div></article>`).join('')||'<p>No encontramos productos con ese filtro.</p>'}}
function selected(){{return ITEMS.filter(i=>cart[i.id]).map(i=>({{...i,qty:cart[i.id]}}))}}
function renderCart(){{const rows=selected();document.getElementById('cartCount').textContent=rows.reduce((s,i)=>s+i.qty,0);document.getElementById('cartLines').innerHTML=rows.length?rows.map(i=>`<div class="cart-line"><div><strong>${{i.name}}</strong><br><small>${{money(i.price*i.qty)}}</small></div><div class="qty"><button onclick="change(${{i.id}},-1)">−</button><b>${{i.qty}}</b><button onclick="change(${{i.id}},1)">+</button></div></div>`).join(''):'<div class="empty">Tu carrito está vacío.</div>';document.getElementById('total').textContent=money(rows.reduce((s,i)=>s+i.price*i.qty,0))}}
function toggleCart(force){{if(innerWidth>850)return;const el=document.getElementById('cart');const open=force===undefined?!el.classList.contains('open'):force;el.classList.toggle('open',open);document.body.classList.toggle('cart-open',open)}}
function openCheckout(){{if(!selected().length){{alert('Agrega al menos un producto.');return}}toggleCart(false);document.getElementById('checkoutModal').classList.add('open');document.body.classList.add('cart-open');document.getElementById('checkoutName').focus()}}
function closeCheckout(){{if(submitting)return;document.getElementById('checkoutModal').classList.remove('open');document.body.classList.remove('cart-open')}}
function setFulfillment(value){{fulfillment=value;document.getElementById('deliveryChoice').classList.toggle('active',value==='DELIVERY');document.getElementById('pickupChoice').classList.toggle('active',value==='PICKUP');document.getElementById('deliveryFields').hidden=value==='PICKUP'}}
function showError(message){{const el=document.getElementById('checkoutError');el.textContent=message;el.style.display='block';el.scrollIntoView({{behavior:'smooth',block:'nearest'}})}}
function useLocation(){{if(!navigator.geolocation){{showError('Tu navegador no permite obtener la ubicación.');return}}const button=document.getElementById('locationButton');button.textContent='Obteniendo ubicación…';navigator.geolocation.getCurrentPosition(position=>{{coords={{latitude:position.coords.latitude,longitude:position.coords.longitude}};button.textContent='✅ Ubicación agregada'}},()=>{{button.textContent='📍 Usar mi ubicación actual';showError('No pudimos obtener tu ubicación. Puedes continuar escribiendo la dirección.')}},{{enableHighAccuracy:true,timeout:10000,maximumAge:60000}})}}
function newKey(){{return (crypto.randomUUID?crypto.randomUUID():`${{Date.now()}}-${{Math.random()}}`)+'-'+Date.now()}}
async function placeOrder(){{if(submitting)return;const name=document.getElementById('checkoutName').value.trim(),phone=document.getElementById('checkoutPhone').value.trim(),address=document.getElementById('checkoutAddress').value.trim();if(name.length<2){{showError('Escribe tu nombre.');return}}if(phone.replace(/\\D/g,'').length<9){{showError('Ingresa un número de WhatsApp válido.');return}}if(fulfillment==='DELIVERY'&&address.length<8){{showError('Ingresa tu dirección completa.');return}}submitting=true;const button=document.getElementById('placeOrder');button.disabled=true;button.textContent='Creando pedido…';document.getElementById('checkoutError').style.display='none';let key=sessionStorage.getItem('replau-checkout-key');if(!key){{key=newKey();sessionStorage.setItem('replau-checkout-key',key)}}try{{const response=await fetch('/api/checkout',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{customer_name:name,phone,fulfillment,address,latitude:coords.latitude,longitude:coords.longitude,payment_method:document.getElementById('checkoutPayment').value,notes:document.getElementById('checkoutNotes').value.trim(),idempotency_key:key,website:document.getElementById('checkoutWebsite').value,items:selected().map(i=>({{product_id:i.id,quantity:i.qty}}))}})}});const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'No pudimos crear el pedido.');cart={{}};save();sessionStorage.removeItem('replau-checkout-key');document.getElementById('checkoutForm').hidden=true;const success=document.getElementById('checkoutSuccess');success.hidden=false;success.innerHTML=`<div class="check">✅</div><h2>Pedido ${{escapeHtml(data.order_number)}} confirmado</h2><p>Total: <strong>${{money(Number(data.total||0))}}</strong></p>${{data.tracking_url?`<a href="${{escapeHtml(data.tracking_url)}}">Seguir mi pedido</a>`:''}}<a class="secondary" href="${{escapeHtml(data.whatsapp_url)}}">Contactar por WhatsApp</a>`}}catch(error){{showError(error.message);submitting=false;button.disabled=false;button.textContent='Crear pedido'}}}}
renderCategories();renderMenu();renderCart();
</script>
</body></html>""", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("public_storefront:app", host=APP_HOST, port=APP_PORT, reload=False)
