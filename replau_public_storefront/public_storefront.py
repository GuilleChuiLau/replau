#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
from typing import Any
from urllib.parse import quote

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
WHATSAPP_NUMBER = "".join(c for c in os.environ.get("PUBLIC_WHATSAPP_NUMBER", "51973875456") if c.isdigit())
STORE_NAME = os.environ.get("PUBLIC_STORE_NAME", "Replau Burger").strip() or "Replau Burger"
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8796"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))
PRODUCT_ADMIN_URL = os.environ.get("PRODUCT_ADMIN_URL", "http://127.0.0.1:8794").rstrip("/")

app = FastAPI(title="Replau Public Storefront", docs_url=None, redoc_url=None, openapi_url=None)


def pg_get(path: str) -> Any:
    response = requests.get(POSTGREST_BASE_URL + path, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


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
    .product,.cart{{background:var(--card);border:1px solid var(--line);border-radius:22px;box-shadow:0 10px 30px rgba(78,40,18,.08)}} .product{{overflow:hidden;display:flex;flex-direction:column;min-height:455px}}
    .photo{{height:270px;padding:8px;background:#fff8ed;display:grid;place-items:center;font-size:64px;overflow:hidden}} .photo img{{width:100%;height:100%;object-fit:contain;object-position:center}}
    .product-body{{padding:18px;display:flex;flex:1;flex-direction:column}} .product h3{{font-size:19px;margin:5px 0 8px}} .code{{font-size:11px;color:var(--muted);letter-spacing:.06em}} .description{{color:var(--muted);font-size:14px;line-height:1.45;margin:0 0 14px}} .price{{font-size:23px;font-weight:900;margin:auto 0 14px}}
    .category-label{{font-size:11px;color:var(--brand2);font-weight:900;text-transform:uppercase;letter-spacing:.05em}}
    button.add{{width:100%;border:0;border-radius:12px;background:var(--brand);color:#fff;padding:11px;font-weight:850;cursor:pointer}} button.add:hover{{background:var(--brand2)}}
    .cart{{position:sticky;top:92px;padding:18px}} .cart h2{{margin:0 0 6px}} .empty{{color:var(--muted);padding:24px 0;text-align:center}}
    .cart-line{{display:grid;grid-template-columns:1fr auto;gap:8px;padding:12px 0;border-bottom:1px solid var(--line)}} .qty{{display:flex;align-items:center;gap:8px}}
    .qty button{{width:30px;height:30px;border:1px solid var(--line);border-radius:9px;background:#fff;font-size:18px;cursor:pointer}} .total{{display:flex;justify-content:space-between;font-size:20px;font-weight:900;padding:18px 0}}
    label{{font-size:13px;font-weight:800}} input{{width:100%;margin:7px 0 14px;padding:12px;border:1px solid var(--line);border-radius:12px;font:inherit}}
    .whatsapp{{display:block;width:100%;border:0;border-radius:14px;background:var(--green);color:#fff;padding:14px;font-weight:900;font-size:16px;cursor:pointer;text-align:center}}
    .notice{{font-size:12px;color:var(--muted);line-height:1.45;margin:12px 0 0}} footer{{text-align:center;color:var(--muted);padding:40px 20px}}
    @media(max-width:850px){{.layout{{display:block}}.menu{{grid-template-columns:repeat(2,minmax(0,1fr))}}.cart{{position:fixed;inset:auto 0 0;top:auto;z-index:10;border-radius:22px 22px 0 0;max-height:78vh;overflow:auto;transform:translateY(calc(100% - 64px));transition:.25s}}.cart.open{{transform:none}}.cart h2{{cursor:pointer}}body.cart-open{{overflow:hidden}}}}
    @media(max-width:520px){{.menu{{grid-template-columns:1fr}}.hero{{padding-top:22px}}.photo{{height:300px}}.product{{min-height:490px}}}}
  </style>
</head>
<body>
<header><div class="bar"><div><h1>{title}</h1><div class="tag">Pedido directo por WhatsApp</div></div><button class="cart-chip" onclick="toggleCart(true)">Carrito · <span id="cartCount">0</span></button></div></header>
<main class="wrap">
  <section class="hero"><h2>Elige, arma tu carrito y continúa por WhatsApp.</h2><p>Te enviaremos el resumen listo para confirmar disponibilidad, pago y entrega con nuestro asistente.</p></section>
  <div class="tools"><input class="search" id="search" type="search" placeholder="Buscar hamburguesas, papas, bebidas..." oninput="renderMenu()"><div class="categories" id="categories"></div></div>
  <div class="layout"><section class="menu" id="menu"></section>
    <aside class="cart" id="cart"><h2 onclick="toggleCart()">Tu carrito</h2><div id="cartLines"></div><div class="total"><span>Total estimado</span><span id="total">S/ 0.00</span></div>
      <label for="customerName">Tu nombre</label><input id="customerName" autocomplete="name" maxlength="80" placeholder="Ej. Juan Pérez">
      <button class="whatsapp" onclick="sendWhatsApp()">Continuar en WhatsApp</button>
      <p class="notice">El pedido se envía cuando presionas “Enviar” dentro de WhatsApp. El precio y disponibilidad se confirman en la conversación.</p>
    </aside>
  </div>
</main><footer>{title} · Menú actualizado desde Replau</footer>
<script>
const ITEMS={items_json}; const WA={json.dumps(whatsapp_url)}; const CATEGORY_ORDER=['Todos','Combos','Hamburguesas','Alitas','Acompañamientos','Bebidas','Extras','Otros']; let activeCategory='Todos'; let cart=JSON.parse(localStorage.getItem('replau-cart')||'{{}}');
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
function sendWhatsApp(){{const rows=selected(),name=document.getElementById('customerName').value.trim();if(!rows.length){{alert('Agrega al menos un producto.');return}}if(!name){{alert('Escribe tu nombre para continuar.');document.getElementById('customerName').focus();return}}const message=[name,...rows.map(i=>`${{i.qty}} ${{i.name}}`)].join('\\n');window.location.href=WA+'?text='+encodeURIComponent(message)}}
renderCategories();renderMenu();renderCart();
</script>
</body></html>""", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("public_storefront:app", host=APP_HOST, port=APP_PORT, reload=False)
