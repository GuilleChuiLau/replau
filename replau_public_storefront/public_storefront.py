#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
from typing import Any
from urllib.parse import quote

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
WHATSAPP_NUMBER = "".join(c for c in os.environ.get("PUBLIC_WHATSAPP_NUMBER", "51973875456") if c.isdigit())
STORE_NAME = os.environ.get("PUBLIC_STORE_NAME", "Replau Burger").strip() or "Replau Burger"
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8796"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))

app = FastAPI(title="Replau Public Storefront", docs_url=None, redoc_url=None, openapi_url=None)


def pg_get(path: str) -> Any:
    response = requests.get(POSTGREST_BASE_URL + path, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


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
    items = []
    for product in products:
        product_id = int(product["id"])
        price = latest.get(product_id)
        if not price:
            continue
        items.append({
            "id": product_id,
            "code": str(product.get("cdg_prod") or ""),
            "name": str(product.get("nombre") or ""),
            "price": float(price.get("precio") or 0),
            "currency": str(price.get("moneda") or "PEN"),
            "unit": str(price.get("unidad") or "UNIDAD"),
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
    .layout{{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:22px;align-items:start}} .menu{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
    .product,.cart{{background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 10px 30px rgba(78,40,18,.08)}} .product{{padding:18px;display:flex;flex-direction:column;min-height:190px}}
    .photo{{height:78px;border-radius:14px;background:linear-gradient(135deg,#fed7aa,#fecaca);display:grid;place-items:center;font-size:38px;margin-bottom:14px}}
    .product h3{{font-size:17px;margin:0 0 8px}} .code{{font-size:11px;color:var(--muted);letter-spacing:.06em}} .price{{font-size:21px;font-weight:900;margin:auto 0 12px}}
    button.add{{width:100%;border:0;border-radius:12px;background:var(--brand);color:#fff;padding:11px;font-weight:850;cursor:pointer}} button.add:hover{{background:var(--brand2)}}
    .cart{{position:sticky;top:92px;padding:18px}} .cart h2{{margin:0 0 6px}} .empty{{color:var(--muted);padding:24px 0;text-align:center}}
    .cart-line{{display:grid;grid-template-columns:1fr auto;gap:8px;padding:12px 0;border-bottom:1px solid var(--line)}} .qty{{display:flex;align-items:center;gap:8px}}
    .qty button{{width:30px;height:30px;border:1px solid var(--line);border-radius:9px;background:#fff;font-size:18px;cursor:pointer}} .total{{display:flex;justify-content:space-between;font-size:20px;font-weight:900;padding:18px 0}}
    label{{font-size:13px;font-weight:800}} input{{width:100%;margin:7px 0 14px;padding:12px;border:1px solid var(--line);border-radius:12px;font:inherit}}
    .whatsapp{{display:block;width:100%;border:0;border-radius:14px;background:var(--green);color:#fff;padding:14px;font-weight:900;font-size:16px;cursor:pointer;text-align:center}}
    .notice{{font-size:12px;color:var(--muted);line-height:1.45;margin:12px 0 0}} footer{{text-align:center;color:var(--muted);padding:40px 20px}}
    @media(max-width:850px){{.layout{{display:block}}.menu{{grid-template-columns:repeat(2,minmax(0,1fr))}}.cart{{position:fixed;inset:auto 0 0;top:auto;z-index:10;border-radius:22px 22px 0 0;max-height:78vh;overflow:auto;transform:translateY(calc(100% - 64px));transition:.25s}}.cart.open{{transform:none}}.cart h2{{cursor:pointer}}body.cart-open{{overflow:hidden}}}}
    @media(max-width:520px){{.menu{{grid-template-columns:1fr}}.hero{{padding-top:22px}}}}
  </style>
</head>
<body>
<header><div class="bar"><div><h1>{title}</h1><div class="tag">Pedido directo por WhatsApp</div></div><button class="cart-chip" onclick="toggleCart(true)">Carrito · <span id="cartCount">0</span></button></div></header>
<main class="wrap">
  <section class="hero"><h2>Elige, arma tu carrito y continúa por WhatsApp.</h2><p>Te enviaremos el resumen listo para confirmar disponibilidad, pago y entrega con nuestro asistente.</p></section>
  <div class="layout"><section class="menu" id="menu"></section>
    <aside class="cart" id="cart"><h2 onclick="toggleCart()">Tu carrito</h2><div id="cartLines"></div><div class="total"><span>Total estimado</span><span id="total">S/ 0.00</span></div>
      <label for="customerName">Tu nombre</label><input id="customerName" autocomplete="name" maxlength="80" placeholder="Ej. Juan Pérez">
      <button class="whatsapp" onclick="sendWhatsApp()">Continuar en WhatsApp</button>
      <p class="notice">El pedido se envía cuando presionas “Enviar” dentro de WhatsApp. El precio y disponibilidad se confirman en la conversación.</p>
    </aside>
  </div>
</main><footer>{title} · Menú actualizado desde Replau</footer>
<script>
const ITEMS={items_json}; const WA={json.dumps(whatsapp_url)}; let cart=JSON.parse(localStorage.getItem('replau-cart')||'{{}}');
const money=n=>new Intl.NumberFormat('es-PE',{{style:'currency',currency:'PEN'}}).format(n);
function save(){{localStorage.setItem('replau-cart',JSON.stringify(cart));renderCart()}}
function add(id){{cart[id]=(cart[id]||0)+1;save();toggleCart(true)}}
function change(id,d){{cart[id]=Math.max(0,(cart[id]||0)+d);if(!cart[id])delete cart[id];save()}}
function renderMenu(){{document.getElementById('menu').innerHTML=ITEMS.map(i=>`<article class="product"><div class="photo">🍔</div><div class="code">${{i.code}}</div><h3>${{i.name}}</h3><div class="price">${{money(i.price)}}</div><button class="add" onclick="add(${{i.id}})">Agregar</button></article>`).join('')||'<p>No hay productos disponibles.</p>'}}
function selected(){{return ITEMS.filter(i=>cart[i.id]).map(i=>({{...i,qty:cart[i.id]}}))}}
function renderCart(){{const rows=selected();document.getElementById('cartCount').textContent=rows.reduce((s,i)=>s+i.qty,0);document.getElementById('cartLines').innerHTML=rows.length?rows.map(i=>`<div class="cart-line"><div><strong>${{i.name}}</strong><br><small>${{money(i.price*i.qty)}}</small></div><div class="qty"><button onclick="change(${{i.id}},-1)">−</button><b>${{i.qty}}</b><button onclick="change(${{i.id}},1)">+</button></div></div>`).join(''):'<div class="empty">Tu carrito está vacío.</div>';document.getElementById('total').textContent=money(rows.reduce((s,i)=>s+i.price*i.qty,0))}}
function toggleCart(force){{if(innerWidth>850)return;const el=document.getElementById('cart');const open=force===undefined?!el.classList.contains('open'):force;el.classList.toggle('open',open);document.body.classList.toggle('cart-open',open)}}
function sendWhatsApp(){{const rows=selected(),name=document.getElementById('customerName').value.trim();if(!rows.length){{alert('Agrega al menos un producto.');return}}if(!name){{alert('Escribe tu nombre para continuar.');document.getElementById('customerName').focus();return}}const message=[name,...rows.map(i=>`${{i.qty}} ${{i.name}}`)].join('\n');window.location.href=WA+'?text='+encodeURIComponent(message)}}
renderMenu();renderCart();
</script>
</body></html>""", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("public_storefront:app", host=APP_HOST, port=APP_PORT, reload=False)
