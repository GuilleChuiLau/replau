#!/usr/bin/env python3
from __future__ import annotations

import html
import os
import time
from typing import Any, Dict

import requests
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
KITCHEN_HOST = os.environ.get("KITCHEN_HOST", "127.0.0.1")
KITCHEN_PORT = int(os.environ.get("KITCHEN_PORT", "8791"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
AUTO_REFRESH_SECONDS = int(os.environ.get("AUTO_REFRESH_SECONDS", "15"))

app = FastAPI(title="Replau Kitchen UI", version="1.0.0")


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def public_prefix(request: Request) -> str:
    prefix = (request.headers.get("x-forwarded-prefix") or os.environ.get("PUBLIC_PREFIX") or "").strip()
    if not prefix or prefix == "/":
        return ""
    return "/" + prefix.strip("/")


def money(value: Any) -> str:
    try:
        return f"S/ {float(value):,.2f}"
    except Exception:
        return "S/ 0.00"


def pg_request(method: str, path: str, **kwargs: Any) -> Any:
    url = f"{POSTGREST_BASE_URL}{path}"
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Connection", "close")
    max_attempts = 8
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == max_attempts - 1:
                raise
            time.sleep(min(2.0, 0.25 * (attempt + 1)))
    if last_exc:
        raise last_exc
    raise RuntimeError("PostGREST request failed")


def fetch_json(path: str) -> Any:
    return pg_request("GET", path)


def post_rpc(name: str, payload: Dict[str, Any]) -> Any:
    return pg_request(
        "POST",
        f"/rpc/{name}",
        headers={"Content-Type": "application/json"},
        json=payload,
    )


def color_class(queue_color: str) -> str:
    queue_color = (queue_color or "").upper()
    if queue_color == "RED":
        return "card red"
    if queue_color == "YELLOW":
        return "card yellow"
    return "card green"


def kitchen_progress_html(status: Any) -> str:
    value = str(status or "").upper()
    prep_done = value in {"EN_PREPARACION", "LISTO", "ENTREGADO"}
    listo_done = value in {"LISTO", "ENTREGADO"}
    steps = [("Recibido", True), ("Preparación", prep_done), ("Listo", listo_done)]
    return "".join(
        f'<span class="step {"done" if done else ""}">{esc(label)}</span>'
        for label, done in steps
    )


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        response = requests.get(f"{POSTGREST_BASE_URL}/", headers={"Connection": "close"}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return {
            "ok": True,
            "postgrest_ok": True,
            "postgrest_base_url": POSTGREST_BASE_URL,
            "auto_refresh_seconds": AUTO_REFRESH_SECONDS,
        }
    except Exception as exc:
        return {"ok": False, "postgrest_ok": False, "error": str(exc)}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    base = public_prefix(request)
    orders = fetch_json("/v_kitchen_orders")
    cards = ""
    for order in orders:
        cards += f"""
        <a class="{color_class(order.get('queue_color'))}" href="{base}/order/{order.get('id')}">
          <div class="station-card-head">
            <div>
              <div class="station-kicker">Cocina</div>
              <div class="pedido">{esc(order.get('pedido_num'))}</div>
              <div class="cliente">{esc(order.get('cliente_nombre'))}</div>
            </div>
            <div class="mins">{esc(order.get('queue_minutes'))} min</div>
          </div>
          <div class="kitchen-progress">{kitchen_progress_html(order.get('kitchen_status'))}</div>
          <div class="station-facts">
            <div><span>Estado cocina</span><strong>{esc(order.get('kitchen_status'))}</strong></div>
            <div><span>Pago</span><strong>{esc(order.get('metodo_pago'))}</strong></div>
            <div><span>Total</span><strong>{money(order.get('total'))}</strong></div>
          </div>
        </a>
        """
    if not cards:
        cards = '<div class="empty">No hay pedidos en cocina.</div>'

    return HTMLResponse(f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Kitchen Board - Replau</title>
  <meta http-equiv="refresh" content="{AUTO_REFRESH_SECONDS}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap");
    :root {{
      --bg:#0f172a; --bg-2:#020617; --card:#111827; --card-2:#0b1220; --text:#e5e7eb; --muted:#94a3b8; --line:#334155;
      --blue:#3b82f6; --green:#00e676; --orange:#ffd400; --red:#ff1744; --purple:#8b5cf6; --brand:#8b5cf6;
      --shadow:0 18px 48px rgba(0,0,0,.35); --radius:24px;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"Montserrat", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:radial-gradient(circle at top left, rgba(249,115,22,.16), transparent 30%), radial-gradient(circle at top right, rgba(59,130,246,.10), transparent 28%), linear-gradient(180deg,var(--bg),var(--bg-2)); color:var(--text); min-height:100vh; }}
    a {{ color:#93c5fd; }}
    .wrap {{ max-width:1440px; margin:0 auto; padding:28px; }}
    .topbar {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; flex-wrap:wrap; margin-bottom:24px; padding:20px 22px; background:rgba(17,24,39,.88); border:1px solid rgba(51,65,85,.95); border-radius:var(--radius); box-shadow:var(--shadow); backdrop-filter:blur(10px); }}
    h1 {{ margin:0 0 8px; font-size:clamp(34px,4vw,52px); line-height:1; letter-spacing:-.045em; }}
    .sub {{ color:var(--muted); font-size:15px; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; }}
    .button {{ display:inline-flex; align-items:center; justify-content:center; border:0; border-radius:14px; padding:11px 15px; background:linear-gradient(135deg,var(--brand),#6d28d9); color:white; text-decoration:none; cursor:pointer; font-size:14px; font-weight:850; box-shadow:0 10px 24px rgba(139,92,246,.24); }}
    .button.secondary {{ background:#374151; box-shadow:none; }}
    .legend {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:24px; color:#f8fafc; }}
    .legend span {{ padding:9px 14px; border-radius:999px; font-size:13px; font-weight:850; letter-spacing:.02em; border:1px solid rgba(255,255,255,.14); box-shadow:0 10px 24px rgba(0,0,0,.18); background:#020617; color:#cbd5e1; }}
    .l-green {{ background:linear-gradient(135deg,#00e676,#00c853)!important; border-color:rgba(0,230,118,.8)!important; color:#052e16!important; box-shadow:0 0 28px rgba(0,230,118,.28)!important; }}
    .l-yellow {{ background:linear-gradient(135deg,#ffd400,#f59e0b)!important; border-color:rgba(255,212,0,.8)!important; color:#1f1300!important; box-shadow:0 0 28px rgba(255,212,0,.28)!important; }}
    .l-red {{ background:linear-gradient(135deg,#ff1744,#b91c1c)!important; border-color:rgba(255,23,68,.75)!important; color:#ffffff!important; box-shadow:0 0 28px rgba(255,23,68,.28)!important; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:18px; }}
    .card {{ color:var(--text); text-decoration:none; border-radius:26px; padding:22px; box-shadow:0 22px 55px rgba(0,0,0,.28); border:1px solid rgba(139,92,246,.24); border-top:5px solid var(--orange); position:relative; overflow:hidden; transition:transform .16s ease, box-shadow .16s ease; background:linear-gradient(180deg, rgba(15,23,42,.98), rgba(17,24,39,.96)); }}
    .card::after {{ content:""; position:absolute; inset:auto -35px -50px auto; width:140px; height:140px; border-radius:50%; background:rgba(139,92,246,.12); }}
    .card:hover {{ transform:translateY(-3px); box-shadow:0 26px 70px rgba(139,92,246,.16); }}
    .green {{ border-top-color:var(--green); box-shadow:0 24px 70px rgba(0,230,118,.20); }}
    .yellow {{ border-top-color:var(--orange); box-shadow:0 24px 70px rgba(255,212,0,.20); }}
    .red {{ border-top-color:var(--red); box-shadow:0 24px 70px rgba(255,23,68,.24); }}
    .station-card-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:16px; position:relative; z-index:1; }}
    .station-kicker {{ color:#a7f3d0; text-transform:uppercase; font-weight:900; font-size:12px; letter-spacing:.08em; }}
    .pedido {{ margin:4px 0; font-size:clamp(30px,4vw,46px); line-height:1; font-weight:950; letter-spacing:-.045em; }}
    .mins {{ font-size:16px; font-weight:950; background:#020617; color:#ddd6fe; border:1px solid rgba(139,92,246,.45); padding:8px 12px; border-radius:999px; }}
    .cliente {{ color:#cbd5e1; font-size:18px; font-weight:800; }}
    .kitchen-progress {{ display:flex; gap:8px; flex-wrap:wrap; margin:12px 0 16px; position:relative; z-index:1; }}
    .step {{ border:1px solid rgba(148,163,184,.35); color:#94a3b8; border-radius:999px; padding:6px 10px; font-size:12px; font-weight:900; text-transform:uppercase; letter-spacing:.06em; }}
    .step.done {{ background:rgba(34,197,94,.16); border-color:rgba(34,197,94,.42); color:#bbf7d0; }}
    .station-facts {{ display:grid; gap:10px; position:relative; z-index:1; }}
    .station-facts div {{ background:rgba(139,92,246,.12); border:1px solid rgba(139,92,246,.25); border-radius:16px; padding:12px; }}
    .station-facts span {{ display:block; color:#94a3b8; font-size:12px; font-weight:850; text-transform:uppercase; letter-spacing:.06em; }}
    .station-facts strong {{ display:block; margin-top:4px; color:#f8fafc; }}
    .empty {{ background:rgba(17,24,39,.88); border:1px solid rgba(51,65,85,.95); border-radius:24px; padding:34px; font-size:20px; text-align:center; box-shadow:var(--shadow); color:var(--muted); }}
    @media(max-width:700px) {{ .wrap {{ padding:14px; }} .grid {{ grid-template-columns:1fr; }} .topbar {{ display:block; }} .actions {{ margin-top:14px; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1>Kitchen Board</h1>
        <div class="sub">Pantalla fija de cocina · Auto-refresh cada {AUTO_REFRESH_SECONDS} segundos</div>
      </div>
      <div class="actions">
        <a class="button" href="{base}/">Actualizar</a>
        <a class="button secondary" href="http://127.0.0.1:8790/ops/picking">Picking</a>
        <a class="button secondary" href="http://127.0.0.1:8790/ops/delivery">Delivery</a>
      </div>
    </div>
    <div class="legend">
      <span class="l-green">0-20 min</span>
      <span class="l-yellow">más de 20 min</span>
      <span class="l-red">más de 30 min</span>
    </div>
    <div class="grid">{cards}</div>
  </div>
</body>
</html>""")


@app.get("/api/orders", response_class=JSONResponse)
def api_orders() -> JSONResponse:
    return JSONResponse(fetch_json("/v_kitchen_orders"))


@app.get("/order/{pedido_id}", response_class=HTMLResponse)
def order_detail(pedido_id: int, request: Request) -> HTMLResponse:
    base = public_prefix(request)
    order_rows = fetch_json(f"/v_kitchen_orders?id=eq.{pedido_id}")
    if not order_rows:
        raise HTTPException(status_code=404, detail="Pedido not found")
    order = order_rows[0]
    items = fetch_json(f"/v_kitchen_order_items?pedido_id=eq.{pedido_id}&order=id.asc")

    rows = ""
    for item in items:
        rows += f"""
        <tr>
          <td>{esc(item.get('producto_nombre'))}</td>
          <td class="num">{esc(item.get('cantidad'))}</td>
          <td>{esc(item.get('unidad'))}</td>
          <td class="num">{money(item.get('total_linea'))}</td>
        </tr>
        """
    if not rows:
        rows = '<tr><td colspan="4">Sin items</td></tr>'

    maps_section = f"<p><strong>Dirección:</strong> {esc(order.get('direccion'))}</p>" if order.get("direccion") else ""

    return HTMLResponse(f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>{esc(order.get('pedido_num'))} - Kitchen</title>
  <meta http-equiv="refresh" content="{AUTO_REFRESH_SECONDS}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap");
    :root {{ --bg:#0f172a; --bg-2:#020617; --card:#111827; --card-2:#0b1220; --text:#e5e7eb; --muted:#94a3b8; --line:#334155; --blue:#3b82f6; --green:#00e676; --orange:#ffd400; --red:#ff1744; --purple:#8b5cf6; --brand:#8b5cf6; --radius:24px; --shadow:0 18px 48px rgba(0,0,0,.35); }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"Montserrat", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:radial-gradient(circle at top left, rgba(249,115,22,.16), transparent 30%), radial-gradient(circle at top right, rgba(59,130,246,.10), transparent 28%), linear-gradient(180deg,var(--bg),var(--bg-2)); color:var(--text); }}
    .page {{ max-width:1120px; margin:0 auto; padding:28px; }}
    .card {{ background:rgba(17,24,39,.96); border:1px solid rgba(51,65,85,.95); border-top:5px solid var(--orange); border-radius:var(--radius); padding:24px; margin-bottom:20px; box-shadow:var(--shadow); }}
    h1 {{ margin:0 0 10px; font-size:clamp(34px,4vw,48px); letter-spacing:-.045em; }}
    h2 {{ letter-spacing:-.025em; }}
    p {{ color:#cbd5e1; }}
    .badge {{ display:inline-block; padding:9px 14px; border-radius:999px; background:linear-gradient(135deg,var(--brand),#6d28d9); color:white; font-weight:900; letter-spacing:.02em; }}
    .queue {{ font-size:22px; font-weight:950; margin-top:12px; color:#bbf7d0; }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; overflow:hidden; border-radius:18px; border:1px solid var(--line); background:#0b1220; }}
    th,td {{ padding:13px 14px; border-bottom:1px solid var(--line); text-align:left; background:#0b1220; }}
    th {{ background:#020617; color:#93c5fd; font-size:12px; text-transform:uppercase; letter-spacing:.07em; }}
    tr:last-child td {{ border-bottom:0; }}
    .num {{ text-align:right; }}
    .buttons {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:16px; }}
    button {{ border:0; border-radius:14px; padding:12px 16px; font-size:15px; font-weight:850; cursor:pointer; color:white; box-shadow:0 10px 24px rgba(139,92,246,.24); transition:transform .14s ease, filter .14s ease; }}
    button:hover {{ transform:translateY(-1px); filter:brightness(1.04); }}
    .b1 {{ background:#374151; }} .b2 {{ background:linear-gradient(135deg,#ffd400,#f59e0b); color:#1f1300; }} .b3 {{ background:linear-gradient(135deg,#00e676,#00c853); color:#052e16; }} .b4 {{ background:linear-gradient(135deg,#00e676,#00c853); color:#052e16; }} .b5 {{ background:#374151; }}
    .back {{ display:inline-block; margin-bottom:16px; text-decoration:none; color:#93c5fd; font-weight:850; }}
    textarea {{ width:100%; min-height:90px; border-radius:16px; padding:12px 14px; border:1px solid var(--line); box-sizing:border-box; font:inherit; background:#020617; color:#e5e7eb; }}
  </style>
</head>
<body>
  <div class="page">
    <a class="back" href="{base}/">← Volver al Kitchen Board</a>
    <div class="card">
      <h1>{esc(order.get('pedido_num'))}</h1>
      <div class="badge">{esc(order.get('kitchen_status'))}</div>
      <div class="queue">Tiempo en cola: {esc(order.get('queue_minutes'))} min</div>
      <p><strong>Cliente:</strong> {esc(order.get('cliente_nombre'))}</p>
      <p><strong>WhatsApp:</strong> {esc(order.get('whatsapp_number'))}</p>
      <p><strong>Pago:</strong> {esc(order.get('metodo_pago'))}</p>
      <p><strong>Total:</strong> {money(order.get('total'))}</p>
      {maps_section}
      <p><strong>Notas cocina:</strong> {esc(order.get('kitchen_notes'))}</p>
      <form method="post" action="{base}/order/{order.get('id')}/status">
        <label for="notes"><strong>Actualizar notas cocina</strong></label>
        <textarea id="notes" name="notes">{esc(order.get('kitchen_notes'))}</textarea>
        <div class="buttons">
          <button class="b1" name="status" value="NUEVO">Nuevo</button>
          <button class="b2" name="status" value="EN_PREPARACION">En preparación</button>
          <button class="b3" name="status" value="LISTO">Listo</button>
          <button class="b4" name="status" value="ENTREGADO">Entregado</button>
          <button class="b5" name="status" value="ANULADO">Anulado</button>
        </div>
      </form>
    </div>
    <div class="card">
      <h2>Items del pedido</h2>
      <table>
        <thead>
          <tr>
            <th>Producto</th>
            <th class="num">Cantidad</th>
            <th>Unidad</th>
            <th class="num">Total</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </div>
</body>
</html>""")


@app.post("/order/{pedido_id}/status")
def order_status(pedido_id: int, request: Request, status: str = Form(...), notes: str = Form("")):
    result = post_rpc(
        "update_kitchen_status",
        {
            "p_pedido_id": pedido_id,
            "p_kitchen_status": status,
            "p_kitchen_notes": notes or None,
            # Include p_notify explicitly so PostgREST selects the 4-argument
            # update_kitchen_status overload installed by the notifications upgrade.
            "p_notify": True,
        },
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return RedirectResponse(url=f"{public_prefix(request)}/order/{pedido_id}", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("kitchen_ui:app", host=KITCHEN_HOST, port=KITCHEN_PORT, reload=False)
