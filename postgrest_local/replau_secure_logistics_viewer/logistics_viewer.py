#!/usr/bin/env python3
from __future__ import annotations

import os
import html
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote, urlparse, parse_qs

import requests
from fastapi import FastAPI, HTTPException, Query, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
VIEWER_HOST = os.environ.get("VIEWER_HOST", "127.0.0.1")
VIEWER_PORT = int(os.environ.get("VIEWER_PORT", "8790"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
DASHBOARD_LIMIT = int(os.environ.get("DASHBOARD_LIMIT", "20"))
BLOCKLIST_PATH = Path(os.environ.get("WHATSAPP_BLOCKLIST_PATH", "/home/guill/.openclaw/workspace/blocked_whatsapp_numbers.json"))

app = FastAPI(title="Replau Logistics Viewer", version="1.1.0")


STATUS_COLORS = {
    "CONFIRMADO": ("#e8f0fe", "#174ea6"),
    "EN_PREPARACION": ("#fff4e5", "#b06000"),
    "DESPACHADO": ("#e6f4ea", "#188038"),
    "ENTREGADO": ("#e6f4ea", "#137333"),
    "ANULADO": ("#fce8e6", "#c5221f"),
    "WAITING_PAYMENT_AND_LOCATION": ("#fff4e5", "#b06000"),
    "WAITING_ADDRESS_CONFIRMATION": ("#f3e8fd", "#7b1fa2"),
    "ASKING_NAME_AND_ITEMS": ("#f1f3f4", "#5f6368"),
}
PAYMENT_COLORS = {
    "YAPE": ("#efe7ff", "#5b21b6"),
    "PLIN": ("#ffe4f1", "#be185d"),
    "TRANSFERENCIA": ("#e0f2fe", "#075985"),
    "CONTRA_ENTREGA": ("#ecfccb", "#3f6212"),
}


def pg_request(method: str, path: str, **kwargs: Any) -> Any:
    url = f"{POSTGREST_BASE_URL}{path}"
    last_exc: Exception | None = None
    max_attempts = 8
    for attempt in range(max_attempts):
        try:
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Connection", "close")
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
    raise RuntimeError("PostgREST request failed")


def pg_rpc(name: str, payload: Dict[str, Any]) -> Any:
    return pg_request(
        "POST",
        f"/rpc/{name}",
        headers={"Content-Type": "application/json"},
        json=payload,
    )


def pg_get(path: str) -> Any:
    return pg_request("GET", path)


def load_blocklist() -> Dict[str, Any]:
    try:
        if BLOCKLIST_PATH.exists():
            return json.loads(BLOCKLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def save_blocklist(data: Dict[str, Any]) -> None:
    BLOCKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    BLOCKLIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def money(value: Any) -> str:
    try:
        return f"S/ {float(value):,.2f}"
    except Exception:
        return "S/ 0.00"


def badge_html(status: Any) -> str:
    text = esc(status or "SIN_ESTADO")
    bg, fg = STATUS_COLORS.get(str(status or "").upper(), ("#f1f3f4", "#3c4043"))
    return f'<span class="badge" style="background:{bg};color:{fg}">{text}</span>'


def payment_badge_html(payment: Any) -> str:
    value = str(payment or "SIN_PAGO").upper()
    label = value.replace("_", " ")
    bg, fg = PAYMENT_COLORS.get(value, ("#f1f3f4", "#3c4043"))
    return f'<span class="badge payment-badge" style="background:{bg};color:{fg}">{esc(label)}</span>'


def trim_text(value: Any, limit: int = 120) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def searchable_text(*values: Any) -> str:
    return " ".join(str(v or "") for v in values).lower()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def age_minutes(value: Any) -> int | None:
    dt = parse_dt(value)
    if not dt:
        return None
    delta = datetime.now(timezone.utc) - dt
    return max(0, int(delta.total_seconds() // 60))


def stale_level(minutes: int | None, warn_after: int = 15, danger_after: int = 45) -> str:
    if minutes is None:
        return ""
    if minutes >= danger_after:
        return "danger"
    if minutes >= warn_after:
        return "warn"
    return ""


def stale_html(value: Any, warn_after: int = 15, danger_after: int = 45) -> str:
    minutes = age_minutes(value)
    if minutes is None:
        return ""
    level = stale_level(minutes, warn_after=warn_after, danger_after=danger_after)
    cls = f"stale {level}".strip()
    return f'<span class="{cls}">Hace {minutes} min</span>'


def order_token(order: Dict[str, Any]) -> str:
    url = str(order.get("order_url") or "")
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return (parse_qs(parsed.query).get("token") or [""])[0]
    except Exception:
        return ""


def order_workflow_stage(order: Dict[str, Any]) -> str:
    estado = str(order.get("estado") or "")
    if estado in {"CONFIRMADO", "EN_PREPARACION"}:
        return "picking"
    if estado == "DESPACHADO":
        return "delivery"
    return "other"


def conversation_priority(state: Any) -> int:
    state = str(state or "")
    if state == "WAITING_ADDRESS_CONFIRMATION":
        return 0
    if state == "WAITING_PAYMENT_AND_LOCATION":
        return 1
    if state == "ASKING_NAME_AND_ITEMS":
        return 2
    return 9


def order_priority(order: Dict[str, Any]) -> int:
    status = str(order.get("estado") or "")
    email_status = str((order.get("email_log") or {}).get("status") or "")
    if email_status == "ERROR":
        return 0
    if status == "DESPACHADO":
        return 1
    if status == "EN_PREPARACION":
        return 2
    if status == "CONFIRMADO":
        return 3
    if email_status == "PENDING":
        return 4
    return 9


def build_summary(orders: List[Dict[str, Any]], conversations: List[Dict[str, Any]], email_logs: List[Dict[str, Any]], reservations: List[Dict[str, Any]]) -> Dict[str, Any]:
    order_state_counts = Counter(str(order.get("estado") or "SIN_ESTADO") for order in orders)
    conv_state_counts = Counter(str(conv.get("estado") or "SIN_ESTADO") for conv in conversations)
    email_state_counts = Counter(str(log.get("status") or "SIN_ESTADO") for log in email_logs)
    return {
        "summary": {
            "orders_total": len(orders),
            "orders_confirmed": order_state_counts.get("CONFIRMADO", 0),
            "orders_in_progress": order_state_counts.get("EN_PREPARACION", 0) + order_state_counts.get("DESPACHADO", 0),
            "conversations_waiting": sum(
                conv_state_counts.get(state, 0)
                for state in ["WAITING_PAYMENT_AND_LOCATION", "WAITING_ADDRESS_CONFIRMATION", "ASKING_NAME_AND_ITEMS"]
            ),
            "emails_pending": email_state_counts.get("PENDING", 0),
            "emails_error": email_state_counts.get("ERROR", 0),
            "active_reservations": sum(int((row.get("reservas_activas") or 0)) for row in reservations),
        },
        "order_state_counts": dict(order_state_counts),
        "conv_state_counts": dict(conv_state_counts),
        "email_state_counts": dict(email_state_counts),
    }


def fetch_dashboard_data(limit: int = DASHBOARD_LIMIT) -> Dict[str, Any]:
    orders = pg_get(f"/v_pedidos_logistica?order=id.desc&limit={limit}")
    conversations = pg_get(f"/v_whatsapp_conversaciones?order=updated_at.desc&limit={limit}")
    email_logs = pg_get(f"/email_logistica_log?order=id.desc&limit={limit}")
    reservations = pg_get(f"/v_pedidos_reserva_resumen?order=pedido_id.desc&limit={limit}")
    items = pg_get(f"/v_pedido_items_logistica?order=pedido_id.desc&limit={limit * 3}")

    reservation_by_num = {row.get("pedido_num"): row for row in reservations if row.get("pedido_num")}
    email_by_pedido = {row.get("pedido_id"): row for row in email_logs if row.get("pedido_id") is not None}

    orders_enriched: List[Dict[str, Any]] = []
    for order in orders:
        merged = dict(order)
        merged["reservation"] = reservation_by_num.get(order.get("pedido_num"))
        merged["email_log"] = email_by_pedido.get(order.get("id"))
        orders_enriched.append(merged)

    derived = build_summary(orders_enriched, conversations, email_logs, reservations)

    return {
        "summary": derived["summary"],
        "orders": orders_enriched,
        "conversations": conversations,
        "email_logs": email_logs,
        "reservations": reservations,
        "items": items,
        "order_state_counts": derived["order_state_counts"],
        "conv_state_counts": derived["conv_state_counts"],
        "email_state_counts": derived["email_state_counts"],
    }


def render_layout(title: str, body: str, auto_refresh_seconds: int | None = None) -> str:
    refresh = f'<meta http-equiv="refresh" content="{auto_refresh_seconds}">' if auto_refresh_seconds else ""
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>{esc(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh}
  <style>
    @import url("https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800;900&display=swap");
    :root {{
      --bg:#f4f5f7; --bg-2:#e5e7eb; --card:#ffffff; --text:#17202a; --muted:#697386; --line:#d1d5db;
      --blue:#2563eb; --green:#24945a; --orange:#f7b32b; --red:#dc3f35; --purple:#7c3aed; --brand:#e4572e;
      --radius:22px; --shadow:0 18px 48px rgba(17,24,39,.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family:"Montserrat", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:radial-gradient(circle at top left, rgba(228,87,46,.08), transparent 28%), linear-gradient(180deg,var(--bg),var(--bg-2)); color:var(--text); }}
    a {{ color: var(--brand); text-decoration: none; font-weight:700; }}
    .page {{ max-width: 1400px; margin: 0 auto; padding: 28px; }}
    .topbar {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; flex-wrap:wrap; margin-bottom:24px; padding:20px 22px; background:rgba(255,255,255,.82); border:1px solid rgba(209,213,219,.92); border-radius:var(--radius); box-shadow:var(--shadow); backdrop-filter:blur(10px); }}
    .topbar h1 {{ margin:0 0 6px; font-size:clamp(32px,4vw,48px); line-height:1; letter-spacing:-.045em; }}
    .muted {{ color:var(--muted); }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; }}
    .button {{ display:inline-flex; align-items:center; justify-content:center; border:0; border-radius:14px; padding:11px 15px; background:linear-gradient(135deg,var(--brand),#b83e1d); color:white; text-decoration:none; cursor:pointer; font-size:14px; font-weight:850; box-shadow:0 10px 24px rgba(228,87,46,.22); transition:transform .14s ease, filter .14s ease; }}
    .button:hover {{ transform:translateY(-1px); filter:brightness(1.04); }}
    .button.secondary {{ background:#374151; box-shadow:none; }}
    .button.good {{ background:linear-gradient(135deg,#2fb36d,var(--green)); }}
    .button.warn {{ background:linear-gradient(135deg,#ffd166,var(--orange)); color:#2b1a05; }}
    .button.danger {{ background:linear-gradient(135deg,#ef6351,var(--red)); }}
    .grid-cards {{ display:grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap:16px; margin-bottom:22px; }}
    .summary-card, .panel {{ background:rgba(255,255,255,.96); border:1px solid rgba(209,213,219,.92); border-radius:var(--radius); padding:20px; box-shadow:var(--shadow); }}
    .summary-card .k {{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; font-weight:850; }}
    .summary-card .v {{ margin-top:8px; font-size:34px; line-height:1; font-weight:950; letter-spacing:-.04em; }}
    .panel {{ margin-bottom:20px; border-top:5px solid transparent; }}
    .panel.priority-red {{ border-top-color: var(--red); background:linear-gradient(180deg, #f9fafb 0%, #ffffff 32%); }}
    .panel.priority-orange {{ border-top-color: var(--orange); background:linear-gradient(180deg, #f9fafb 0%, #ffffff 32%); }}
    .panel.priority-purple {{ border-top-color: var(--purple); background:linear-gradient(180deg, #f9fafb 0%, #ffffff 32%); }}
    .panel h2 {{ margin:0 0 14px; font-size:24px; letter-spacing:-.025em; }}
    .panel-head {{ display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }}
    .panel-sub {{ font-size:13px; color:var(--muted); }}
    .chips {{ display:flex; flex-wrap:wrap; gap:9px; }}
    .chip {{ background:#f3f4f6; border:1px solid #d1d5db; border-radius:999px; padding:8px 11px; font-size:13px; color:#374151; font-weight:750; }}
    .chip-link {{ display:inline-block; background:#f3f4f6; color:#374151; border-radius:999px; padding:8px 11px; font-size:13px; text-decoration:none; border:1px solid #d1d5db; font-weight:800; }}
    .chip-link.active {{ background:linear-gradient(135deg,var(--brand),#b83e1d); color:white; border-color:transparent; }}
    .badge {{ display:inline-block; padding:7px 11px; border-radius:999px; font-size:12px; font-weight:900; box-shadow:inset 0 0 0 1px rgba(255,255,255,.18); }}
    .payment-badge {{ letter-spacing:.04em; }}
    .table-wrap {{ overflow:auto; border-radius:18px; border:1px solid var(--line); }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; min-width:900px; background:white; }}
    th, td {{ text-align:left; padding:13px 14px; border-bottom:1px solid var(--line); vertical-align:top; }}
    tr:last-child td {{ border-bottom:0; }}
    tr.stale-warn td {{ background:#fff6dc; }}
    tr.stale-danger td {{ background:#f9fafb; }}
    th {{ background:#e5e7eb; font-size:12px; color:#374151; text-transform:uppercase; letter-spacing:.07em; position:sticky; top:0; font-weight:900; }}
    td.num, th.num {{ text-align:right; }}
    .stack {{ display:flex; flex-direction:column; gap:5px; }}
    .tiny {{ font-size:12px; color:var(--muted); }}
    .stale {{ display:inline-block; margin-top:4px; font-size:11px; font-weight:900; border-radius:999px; padding:5px 9px; background:#eef3fd; color:#174ea6; }}
    .stale.warn {{ background:#fff1d1; color:#965400; }}
    .stale.danger {{ background:#ffe0dc; color:#b82018; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .two-col {{ display:grid; grid-template-columns: 2fr 1fr; gap:20px; }}
    .list {{ display:grid; gap:11px; }}
    .list-item {{ background:#ffffff; border:1px solid var(--line); border-radius:16px; padding:14px; }}
    .list-item strong {{ display:block; margin-bottom:4px; }}
    .payment-callout {{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:16px 18px; border-radius:18px; border:1px solid #d1d5db; background:linear-gradient(180deg, #f3f4f6 0%, #ffffff 100%); margin-bottom:16px; }}
    .payment-callout .label {{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; font-weight:850; }}
    .payment-callout .value {{ font-size:22px; font-weight:950; margin-top:4px; letter-spacing:-.02em; }}
    .order-notes {{ white-space:pre-wrap; font-size:16px; line-height:1.55; color:#2f3a4a; background:#fff8e6; border:1px solid #f3d28a; border-left:6px solid var(--orange); border-radius:18px; padding:16px 18px; }}
    @media (max-width: 1100px) {{ .grid-cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .two-col {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 640px) {{ .page {{ padding:12px; }} .grid-cards {{ grid-template-columns: 1fr; }} .topbar h1 {{ font-size:26px; }} }}
  </style>
</head>
<body>
{body}
</body>
</html>"""


def filter_dashboard_data(
    data: Dict[str, Any],
    view: str,
    search: str = "",
    order_status: str = "all",
    conv_status: str = "all",
    email_status: str = "all",
) -> Dict[str, Any]:
    filtered = dict(data)
    orders = list(data["orders"])
    conversations = list(data["conversations"])
    email_logs = list(data["email_logs"])
    query = search.strip().lower()

    if query:
        orders = [o for o in orders if query in searchable_text(o.get("pedido_num"), o.get("cliente_nombre"), o.get("whatsapp_number"), o.get("direccion_confirmada"), o.get("direccion_detectada"))]
        conversations = [c for c in conversations if query in searchable_text(c.get("whatsapp_number"), c.get("cliente_nombre"), c.get("last_inbound_text"), c.get("last_outbound_text"), c.get("estado"))]
        email_logs = [e for e in email_logs if query in searchable_text(e.get("pedido_id"), e.get("recipient"), e.get("subject"), e.get("status"), e.get("error_message"))]

    search_scoped = {
        "orders": list(orders),
        "conversations": list(conversations),
        "email_logs": list(email_logs),
    }

    if view == "attention":
        conversations = [c for c in conversations if c.get("estado") in {"WAITING_ADDRESS_CONFIRMATION", "WAITING_PAYMENT_AND_LOCATION", "ASKING_NAME_AND_ITEMS"}]
        orders = [o for o in orders if (o.get("email_log") or {}).get("status") in {"PENDING", "ERROR"} or o.get("estado") in {"CONFIRMADO", "EN_PREPARACION", "DESPACHADO"}]
        email_logs = [e for e in email_logs if e.get("status") in {"PENDING", "ERROR"}]
    elif view == "live":
        orders = [o for o in orders if o.get("estado") in {"CONFIRMADO", "EN_PREPARACION", "DESPACHADO"}]
        conversations = [c for c in conversations if c.get("estado") not in {"CONFIRMED", "ANULADO", "CANCELLED"}]
    elif view == "orders":
        conversations = []
        email_logs = []
    elif view == "conversations":
        orders = []
        email_logs = []
    elif view == "emails":
        orders = []
        conversations = []

    if order_status != "all":
        orders = [o for o in orders if str(o.get("estado") or "") == order_status]
    if conv_status != "all":
        conversations = [c for c in conversations if str(c.get("estado") or "") == conv_status]
    if email_status != "all":
        email_logs = [e for e in email_logs if str(e.get("status") or "") == email_status]

    orders.sort(key=lambda o: (order_priority(o), -(o.get("id") or 0)))
    conversations.sort(key=lambda c: (conversation_priority(c.get("estado")), -int(age_minutes(c.get("updated_at")) or 0), str(c.get("updated_at") or "")))
    email_logs.sort(key=lambda e: (0 if str(e.get("status") or "") == "ERROR" else 1 if str(e.get("status") or "") == "PENDING" else 9, -(e.get("id") or 0)))

    filtered["orders"] = orders
    filtered["conversations"] = conversations
    filtered["email_logs"] = email_logs
    filtered["active_view"] = view
    filtered["search"] = search
    filtered["order_status"] = order_status
    filtered["conv_status"] = conv_status
    filtered["email_status"] = email_status
    filtered["search_scoped"] = search_scoped
    filtered.update(build_summary(orders, conversations, email_logs, filtered["reservations"]))
    return filtered


def render_dashboard_page(
    data: Dict[str, Any],
    view: str = "all",
    search: str = "",
    order_status: str = "all",
    conv_status: str = "all",
    email_status: str = "all",
) -> str:
    data = filter_dashboard_data(data, view, search, order_status, conv_status, email_status)
    summary = data["summary"]
    orders = data["orders"]
    conversations = data["conversations"]
    email_logs = data["email_logs"]
    reservations = data["reservations"]

    urgent_conversations = [c for c in data["conversations"] if c.get("estado") == "WAITING_ADDRESS_CONFIRMATION"][:5]
    urgent_emails = [e for e in data["email_logs"] if e.get("status") in {"ERROR", "PENDING"}][:5]
    dispatch_orders = [o for o in data["orders"] if o.get("estado") in {"CONFIRMADO", "EN_PREPARACION", "DESPACHADO"}][:6]

    cards = [
        ("Pedidos recientes", summary["orders_total"]),
        ("Confirmados", summary["orders_confirmed"]),
        ("En operación", summary["orders_in_progress"]),
        ("Conversaciones esperando", summary["conversations_waiting"]),
        ("Emails pendientes", summary["emails_pending"]),
    ]
    card_html = "".join(
        f'<div class="summary-card"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div></div>'
        for label, value in cards
    )

    search_scoped = data["search_scoped"]
    filter_counts = {
        "all": len(search_scoped["orders"]) + len(search_scoped["conversations"]) + len(search_scoped["email_logs"]),
        "attention": len([c for c in search_scoped["conversations"] if c.get("estado") in {"WAITING_ADDRESS_CONFIRMATION", "WAITING_PAYMENT_AND_LOCATION", "ASKING_NAME_AND_ITEMS"}]) + len([o for o in search_scoped["orders"] if (o.get("email_log") or {}).get("status") in {"PENDING", "ERROR"} or o.get("estado") in {"CONFIRMADO", "EN_PREPARACION", "DESPACHADO"}]) + len([e for e in search_scoped["email_logs"] if e.get("status") in {"PENDING", "ERROR"}]),
        "live": len([o for o in search_scoped["orders"] if o.get("estado") in {"CONFIRMADO", "EN_PREPARACION", "DESPACHADO"}]) + len([c for c in search_scoped["conversations"] if c.get("estado") not in {"CONFIRMED", "ANULADO", "CANCELLED"}]),
        "orders": len(search_scoped["orders"]),
        "conversations": len(search_scoped["conversations"]),
        "emails": len(search_scoped["email_logs"]),
    }
    filters = [
        ("all", "Todo"),
        ("attention", "Atención"),
        ("live", "Operación viva"),
        ("orders", "Pedidos"),
        ("conversations", "Conversaciones"),
        ("emails", "Emails"),
    ]
    base_qs = f"search={quote(search, safe='')}&order_status={quote(order_status, safe='')}&conv_status={quote(conv_status, safe='')}&email_status={quote(email_status, safe='')}"
    filter_html = "".join(
        f'<a class="button {"secondary" if key != view else ""}" href="dashboard?view={key}&{base_qs}">{label} ({filter_counts.get(key, 0)})</a>'
        for key, label in filters
    )

    order_chips = "".join(
        f'<div class="chip">{esc(k)}: <strong>{esc(v)}</strong></div>'
        for k, v in sorted(data["order_state_counts"].items())
    ) or '<div class="chip">Sin pedidos</div>'
    conv_chips = "".join(
        f'<div class="chip">{esc(k)}: <strong>{esc(v)}</strong></div>'
        for k, v in sorted(data["conv_state_counts"].items())
    ) or '<div class="chip">Sin conversaciones</div>'
    email_chips = "".join(
        f'<div class="chip">{esc(k)}: <strong>{esc(v)}</strong></div>'
        for k, v in sorted(data["email_state_counts"].items())
    ) or '<div class="chip">Sin emails</div>'

    order_status_counts = Counter(str(o.get("estado") or "SIN_ESTADO") for o in search_scoped["orders"])
    conv_status_counts = Counter(str(c.get("estado") or "SIN_ESTADO") for c in search_scoped["conversations"])
    email_status_counts = Counter(str(e.get("status") or "SIN_ESTADO") for e in search_scoped["email_logs"])
    order_filter_html = ''.join(
        f'<a class="chip-link {"active" if status == order_status else ""}" href="dashboard?view={quote(view, safe="")}&search={quote(search, safe="")}&order_status={quote(status, safe="")}&conv_status={quote(conv_status, safe="")}&email_status={quote(email_status, safe="")}">{esc(label)} ({count})</a>'
        for status, label, count in [("all", "Todos", len(search_scoped["orders"]))] + [(s, s, c) for s, c in sorted(order_status_counts.items())]
    )
    conv_filter_html = ''.join(
        f'<a class="chip-link {"active" if status == conv_status else ""}" href="dashboard?view={quote(view, safe="")}&search={quote(search, safe="")}&order_status={quote(order_status, safe="")}&conv_status={quote(status, safe="")}&email_status={quote(email_status, safe="")}">{esc(label)} ({count})</a>'
        for status, label, count in [("all", "Todos", len(search_scoped["conversations"]))] + [(s, s, c) for s, c in sorted(conv_status_counts.items())]
    )
    email_filter_html = ''.join(
        f'<a class="chip-link {"active" if status == email_status else ""}" href="dashboard?view={quote(view, safe="")}&search={quote(search, safe="")}&order_status={quote(order_status, safe="")}&conv_status={quote(conv_status, safe="")}&email_status={quote(status, safe="")}">{esc(label)} ({count})</a>'
        for status, label, count in [("all", "Todos", len(search_scoped["email_logs"]))] + [(s, s, c) for s, c in sorted(email_status_counts.items())]
    )

    order_rows = ""
    for order in orders:
        reservation = order.get("reservation") or {}
        email_log = order.get("email_log") or {}
        stale = stale_level(age_minutes(order.get("created_at")), warn_after=20, danger_after=50)
        row_class = f' class="stale-{stale}"' if stale else ""
        order_rows += f"""
        <tr{row_class}>
          <td>
            <div class="stack">
              <strong>{esc(order.get('pedido_num'))}</strong>
              <span class="tiny">{esc(order.get('created_at'))}</span>
              {stale_html(order.get('created_at'), warn_after=20, danger_after=50)}
            </div>
          </td>
          <td>
            <div class="stack">
              <strong>{esc(order.get('cliente_nombre'))}</strong>
              <span class="tiny mono">{esc(order.get('whatsapp_number'))}</span>
            </div>
          </td>
          <td>{badge_html(order.get('estado'))}</td>
          <td><div class="stack">{payment_badge_html(order.get('metodo_pago'))}<span class="tiny">Cobro</span></div></td>
          <td class="num">{money(order.get('total'))}</td>
          <td>
            <div class="stack">
              <span>Activas: {esc(reservation.get('reservas_activas', 0))}</span>
              <span class="tiny">Cant. activa: {esc(reservation.get('cantidad_reservada_activa', 0))}</span>
            </div>
          </td>
          <td>{badge_html(email_log.get('status') or 'SIN_EMAIL')}</td>
          <td>{esc(trim_text(order.get('direccion_confirmada') or order.get('direccion_detectada'), 80))}</td>
          <td>
            <div class="actions">
              <a class="button" href="{esc(order.get('order_url') or '#')}" target="_blank">Ver pedido</a>
              <a class="button secondary" href="{esc(order.get('maps_url') or '#')}" target="_blank">Maps</a>
              {f'<a class="button warn" href="ops/picking/{esc(order.get("pedido_num"))}?token={quote(order_token(order), safe="")}">Picking</a>' if order_token(order) and order_workflow_stage(order) == 'picking' else ''}
              {f'<a class="button danger" href="ops/delivery/{esc(order.get("pedido_num"))}?token={quote(order_token(order), safe="")}">Delivery</a>' if order_token(order) and order_workflow_stage(order) == 'delivery' else ''}
            </div>
          </td>
        </tr>
        """

    conv_rows = ""
    for conv in conversations:
        stale = stale_level(age_minutes(conv.get("updated_at")), warn_after=15, danger_after=35)
        row_class = f' class="stale-{stale}"' if stale else ""
        conv_rows += f"""
        <tr{row_class}>
          <td><div class="stack"><span class="mono">{esc(conv.get('whatsapp_number'))}</span>{stale_html(conv.get('updated_at'), warn_after=15, danger_after=35)}</div></td>
          <td>{badge_html(conv.get('estado'))}</td>
          <td>{esc(trim_text(conv.get('last_inbound_text'), 90))}</td>
          <td>{esc(trim_text(conv.get('last_outbound_text'), 120))}</td>
          <td>{esc(conv.get('updated_at'))}</td>
        </tr>
        """

    email_rows = ""
    for log in email_logs:
        stale = stale_level(age_minutes(log.get("created_at")), warn_after=10, danger_after=30)
        row_class = f' class="stale-{stale}"' if stale else ""
        email_rows += f"""
        <tr{row_class}>
          <td><div class="stack"><span>{esc(log.get('pedido_id'))}</span>{stale_html(log.get('created_at'), warn_after=10, danger_after=30)}</div></td>
          <td>{badge_html(log.get('status'))}</td>
          <td>{esc(log.get('recipient'))}</td>
          <td>{esc(trim_text(log.get('subject'), 80))}</td>
          <td>{esc(trim_text(log.get('error_message'), 80))}</td>
          <td>{esc(log.get('created_at'))}</td>
        </tr>
        """

    reservation_list = "".join(
        f'''<div class="list-item"><strong>{esc(row.get("pedido_num"))} · {esc(row.get("cliente_nombre"))}</strong><div class="tiny">Estado: {esc(row.get("estado_pedido"))}</div><div class="tiny">Reservas activas: {esc(row.get("reservas_activas"))} · Cantidad activa: {esc(row.get("cantidad_reservada_activa"))}</div><div class="tiny">Consumos: {esc(row.get("reservas_consumidas"))} · Liberadas: {esc(row.get("reservas_liberadas"))}</div></div>'''
        for row in reservations[:8]
    ) or '<div class="list-item">Sin reservas.</div>'

    urgent_conv_list = "".join(
        f'''<div class="list-item"><strong>{esc(c.get("whatsapp_number"))}</strong><div>{badge_html(c.get("estado"))}</div><div class="tiny">{esc(trim_text(c.get("last_inbound_text"), 90))}</div></div>'''
        for c in urgent_conversations
    ) or '<div class="list-item">Nada urgente en conversaciones.</div>'
    urgent_email_list = "".join(
        f'''<div class="list-item"><strong>Pedido {esc(e.get("pedido_id"))}</strong><div>{badge_html(e.get("status"))}</div><div class="tiny">{esc(trim_text(e.get("subject"), 90))}</div></div>'''
        for e in urgent_emails
    ) or '<div class="list-item">Sin emails urgentes.</div>'
    dispatch_list = "".join(
        f'''<div class="list-item"><strong>{esc(o.get("pedido_num"))} · {esc(o.get("cliente_nombre"))}</strong><div style="display:flex;gap:8px;flex-wrap:wrap">{badge_html(o.get("estado"))}{payment_badge_html(o.get("metodo_pago"))}</div><div class="tiny">{money(o.get("total"))} · {esc(trim_text(o.get("direccion_confirmada") or o.get("direccion_detectada"), 70))}</div></div>'''
        for o in dispatch_orders
    ) or '<div class="list-item">Sin pedidos en operación.</div>'

    picking_orders = [o for o in data["orders"] if order_workflow_stage(o) == "picking"][:8]
    delivery_orders = [o for o in data["orders"] if order_workflow_stage(o) == "delivery"][:8]
    picking_list = "".join(
        f'''<div class="list-item"><strong>{esc(o.get("pedido_num"))} · {esc(o.get("cliente_nombre"))}</strong><div style="display:flex;gap:8px;flex-wrap:wrap">{badge_html(o.get("estado"))}{payment_badge_html(o.get("metodo_pago"))}</div><div class="tiny">{money(o.get("total"))} · {esc(trim_text(o.get("direccion_confirmada") or o.get("direccion_detectada"), 60))}</div><div class="actions" style="margin-top:10px"><a class="button warn" href="ops/picking/{esc(o.get("pedido_num"))}?token={quote(order_token(o), safe='')}">Picking</a></div></div>'''
        for o in picking_orders if order_token(o)
    ) or '<div class="list-item">Sin pedidos para picking.</div>'
    delivery_list = "".join(
        f'''<div class="list-item"><strong>{esc(o.get("pedido_num"))} · {esc(o.get("cliente_nombre"))}</strong><div style="display:flex;gap:8px;flex-wrap:wrap">{badge_html(o.get("estado"))}{payment_badge_html(o.get("metodo_pago"))}</div><div class="tiny">{money(o.get("total"))} · {esc(trim_text(o.get("direccion_confirmada") or o.get("direccion_detectada"), 60))}</div><div class="actions" style="margin-top:10px"><a class="button secondary" href="ops/delivery/{esc(o.get("pedido_num"))}?token={quote(order_token(o), safe='')}">Delivery</a></div></div>'''
        for o in delivery_orders if order_token(o)
    ) or '<div class="list-item">Sin pedidos para delivery.</div>'

    body = f"""
    <div class="page">
      <div class="topbar">
        <div>
          <h1>Replau Logistics Dashboard</h1>
          <div class="muted">Vista operativa rápida para logística. Auto-refresh cada 30s.</div>
        </div>
        <div class="actions">
          <a class="button" href="dashboard?view={esc(view)}&search={quote(search, safe='')}&order_status={quote(order_status, safe='')}&conv_status={quote(conv_status, safe='')}&email_status={quote(email_status, safe='')}">Actualizar</a>
          <a class="button secondary" href="blocked">Bloqueados</a>
          <a class="button secondary" href="health" target="_blank">Health</a>
          <a class="button secondary" href="api/dashboard" target="_blank">API</a>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>Filtros rápidos</h2>
          <div class="panel-sub">Cambia el enfoque del tablero sin perder el resumen general</div>
        </div>
        <div class="actions" style="margin-bottom:12px">{filter_html}</div>
        <form method="get" action="dashboard" class="actions">
          <input type="hidden" name="view" value="{esc(view)}">
          <input type="hidden" name="order_status" value="{esc(order_status)}">
          <input type="hidden" name="conv_status" value="{esc(conv_status)}">
          <input type="hidden" name="email_status" value="{esc(email_status)}">
          <input type="text" name="search" value="{esc(search)}" placeholder="Buscar pedido, cliente, WhatsApp, dirección..." style="min-width:320px;max-width:520px;flex:1;padding:12px 14px;border:1px solid var(--line);border-radius:14px;font-size:14px;font:inherit;background:#ffffff;">
          <button class="button" type="submit">Buscar</button>
          <a class="button secondary" href="dashboard?view={esc(view)}">Limpiar</a>
        </form>
      </div>

      <div class="grid-cards">{card_html}</div>

      <div class="two-col">
        <div class="panel priority-purple">
          <div class="panel-head">
            <h2>Atención inmediata ({len(urgent_conversations)})</h2>
            <div class="panel-sub">Conversaciones bloqueadas o pendientes de confirmación</div>
          </div>
          <div class="list">{urgent_conv_list}</div>
        </div>
        <div class="panel priority-orange">
          <div class="panel-head">
            <h2>Pedidos a mover ({len(dispatch_orders)})</h2>
            <div class="panel-sub">Pedidos confirmados/preparación/despacho</div>
          </div>
          <div class="list">{dispatch_list}</div>
        </div>
      </div>

      <div class="two-col">
        <div class="panel priority-orange">
          <div class="panel-head">
            <h2>Picking ({len(picking_orders)})</h2>
            <div class="panel-sub">Pedidos listos para preparar o recolectar</div>
          </div>
          <div class="list">{picking_list}</div>
        </div>
        <div class="panel priority-red">
          <div class="panel-head">
            <h2>Delivery ({len(delivery_orders)})</h2>
            <div class="panel-sub">Pedidos ya despachados o por entregar</div>
          </div>
          <div class="list">{delivery_list}</div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h2>Resumen de estados</h2>
          <div class="panel-sub">Pedidos, conversaciones y cola de email</div>
        </div>
        <div class="chips">{order_chips}</div>
        <div style="height:8px"></div>
        <div class="chips">{conv_chips}</div>
        <div style="height:8px"></div>
        <div class="chips">{email_chips}</div>
      </div>

      <div class="panel priority-orange">
        <div class="panel-head">
          <h2>Pedidos recientes</h2>
          <div class="panel-sub">Estado, pago, reservas, email y acceso rápido</div>
        </div>
        <div class="chips" style="margin-bottom:12px">{order_filter_html}</div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Pedido</th>
                <th>Cliente</th>
                <th>Estado</th>
                <th>Pago</th>
                <th class="num">Total</th>
                <th>Reservas</th>
                <th>Email</th>
                <th>Dirección</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>{order_rows}</tbody>
          </table>
        </div>
      </div>

      <div class="two-col">
        <div class="panel priority-purple">
          <div class="panel-head">
            <h2>Conversaciones activas</h2>
            <div class="panel-sub">Clientes atorados o en espera de siguiente paso</div>
          </div>
          <div class="chips" style="margin-bottom:12px">{conv_filter_html}</div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>WhatsApp</th>
                  <th>Estado</th>
                  <th>Último inbound</th>
                  <th>Último outbound</th>
                  <th>Actualizado</th>
                </tr>
              </thead>
              <tbody>{conv_rows}</tbody>
            </table>
          </div>
        </div>

        <div>
          <div class="panel">
            <div class="panel-head">
              <h2>Reservas de stock</h2>
              <div class="panel-sub">Fotografía rápida de reservas activas</div>
            </div>
            <div class="list">{reservation_list}</div>
          </div>

          <div class="panel priority-red">
            <div class="panel-head">
              <h2>Cola de email logística ({len(email_logs)})</h2>
              <div class="panel-sub">Últimos correos generados</div>
            </div>
            <div class="chips" style="margin-bottom:12px">{email_filter_html}</div>
            <div class="list" style="margin-bottom:12px">{urgent_email_list}</div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Pedido</th>
                    <th>Estado</th>
                    <th>Destino</th>
                    <th>Asunto</th>
                    <th>Error</th>
                    <th>Creado</th>
                  </tr>
                </thead>
                <tbody>{email_rows}</tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
    """
    return render_layout("Replau Logistics Dashboard", body, auto_refresh_seconds=30)


def render_order_notes_panel(order: Dict[str, Any], title: str = "Notas del pedido") -> str:
    notes = str(order.get("observacion") or "").strip()
    if not notes:
        return ""
    return f"""
      <div class="panel priority-orange">
        <div class="panel-head"><h2>{esc(title)}</h2><div class="panel-sub">Comentarios, salsas, pedidos especiales y comprobantes registrados.</div></div>
        <div class="order-notes">{esc(notes)}</div>
      </div>
    """


def render_items_rows(items: List[Dict[str, Any]]) -> str:
    rows = ""
    for item in items:
        rows += f"""
        <tr>
          <td>{esc(item.get('producto_texto'))}</td>
          <td>{esc(item.get('cdg_prod'))}</td>
          <td class="num">{esc(item.get('cantidad'))}</td>
          <td>{esc(item.get('unidad'))}</td>
          <td class="num">{money(item.get('precio_unitario'))}</td>
          <td class="num"><strong>{money(item.get('total_linea'))}</strong></td>
        </tr>
        """
    return rows


def render_order_page(data: Dict[str, Any], token: str) -> str:
    order = data["order"]
    items = data["items"]

    pedido_num = esc(order.get("pedido_num"))
    estado = badge_html(order.get("estado"))
    cliente = esc(order.get("cliente_nombre"))
    whatsapp = esc(order.get("whatsapp_number"))
    metodo_pago = esc(order.get("metodo_pago"))
    payment_badge = payment_badge_html(order.get("metodo_pago"))
    direccion = esc(order.get("direccion_confirmada") or order.get("direccion_detectada"))
    maps_url = order.get("maps_url")
    created_at = esc(order.get("created_at"))

    rows = render_items_rows(items)

    maps_link = ""
    if maps_url:
        maps_link = f'<a class="button secondary" href="{esc(maps_url)}" target="_blank">Abrir en Google Maps</a>'

    body = f"""
    <div class="page">
      <div class="topbar">
        <div>
          <h1>Pedido {pedido_num}</h1>
          <div class="muted">Creado: {created_at}</div>
        </div>
        <div class="actions">
          {estado}
          <a class="button warn" href="../ops/picking/{pedido_num}?token={quote(token, safe='')}">Picking</a>
          <a class="button danger" href="../ops/delivery/{pedido_num}?token={quote(token, safe='')}">Delivery</a>
          <a class="button secondary" href="../dashboard">Dashboard</a>
        </div>
      </div>

      <div class="panel">
        <div class="payment-callout">
          <div>
            <div class="label">Método de pago</div>
            <div class="value">{metodo_pago}</div>
          </div>
          <div>{payment_badge}</div>
        </div>
        <div class="grid-cards" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
          <div class="summary-card"><div class="k">Cliente</div><div class="v" style="font-size:18px">{cliente}</div></div>
          <div class="summary-card"><div class="k">WhatsApp</div><div class="v" style="font-size:18px">{whatsapp}</div></div>
          <div class="summary-card"><div class="k">Pago</div><div class="v" style="font-size:18px">{metodo_pago}</div></div>
          <div class="summary-card"><div class="k">Total</div><div class="v">{money(order.get('total'))}</div></div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head"><h2>Dirección</h2></div>
        <p>{direccion}</p>
        {maps_link}
      </div>

      {render_order_notes_panel(order)}

      <div class="panel" id="items">
        <div class="panel-head"><h2>Items</h2></div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Producto</th>
                <th>Código</th>
                <th class="num">Cantidad</th>
                <th>Unidad</th>
                <th class="num">Precio</th>
                <th class="num">Total</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head"><h2>Actualizar estado</h2></div>
        <form method="post" action="/order/{pedido_num}/status">
          <input type="hidden" name="token" value="{esc(token)}">
          <input type="hidden" name="next_url" value="/order/{pedido_num}?token={quote(token, safe='')}">
          <button class="button" name="estado" value="CONFIRMADO">Confirmado</button>
          <button class="button warn" name="estado" value="EN_PREPARACION">En preparación</button>
          <button class="button secondary" name="estado" value="DESPACHADO">Despachado</button>
          <button class="button good" name="estado" value="ENTREGADO">Entregado</button>
          <button class="button danger" name="estado" value="ANULADO">Anulado</button>
        </form>
      </div>
    </div>
    """
    return render_layout(f"Pedido {pedido_num} - Replau", body)


def render_picking_page(data: Dict[str, Any], token: str) -> str:
    order = data["order"]
    items = data["items"]
    pedido_num = esc(order.get("pedido_num"))
    metodo_pago = esc(order.get("metodo_pago"))
    payment_badge = payment_badge_html(order.get("metodo_pago"))
    rows = "".join(
        f'''<div class="list-item"><label style="display:flex;gap:10px;align-items:flex-start"><input type="checkbox"><span><strong>{esc(item.get("producto_texto"))}</strong><br><span class="tiny">Cant: {esc(item.get("cantidad"))} · Unidad: {esc(item.get("unidad"))} · {money(item.get("total_linea"))}</span></span></label></div>'''
        for item in items
    ) or '<div class="list-item">Sin items.</div>'
    body = f"""
    <div class="page">
      <div class="topbar">
        <div>
          <h1>Picking · Pedido {pedido_num}</h1>
          <div class="muted">Checklist rápida para preparar el pedido antes del despacho.</div>
        </div>
        <div class="actions">
          {badge_html(order.get('estado'))}
          <a class="button secondary" href="../../order/{pedido_num}?token={quote(token, safe='')}">Vista completa</a>
          <a class="button secondary" href="../../dashboard">Dashboard</a>
        </div>
      </div>

      <div class="payment-callout">
        <div>
          <div class="label">Cobro a revisar antes de salir</div>
          <div class="value">{metodo_pago}</div>
        </div>
        <div>{payment_badge}</div>
      </div>

      <div class="grid-cards" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
        <div class="summary-card"><div class="k">Cliente</div><div class="v" style="font-size:18px">{esc(order.get('cliente_nombre'))}</div></div>
        <div class="summary-card"><div class="k">WhatsApp</div><div class="v" style="font-size:18px">{esc(order.get('whatsapp_number'))}</div></div>
        <div class="summary-card"><div class="k">Pago</div><div class="v" style="font-size:18px">{metodo_pago}</div></div>
        <div class="summary-card"><div class="k">Total</div><div class="v">{money(order.get('total'))}</div></div>
      </div>

      {render_order_notes_panel(order)}

      <div class="panel priority-orange">
        <div class="panel-head"><h2>Checklist de picking</h2><div class="panel-sub">Marca visual local para revisar armado, empaques y observaciones.</div></div>
        <div class="list">{rows}</div>
      </div>

      <div class="panel">
        <div class="panel-head"><h2>Acciones de picking</h2></div>
        <form method="post" action="/order/{pedido_num}/status" class="actions">
          <input type="hidden" name="token" value="{esc(token)}">
          <input type="hidden" name="next_url" value="/ops/picking/{pedido_num}?token={quote(token, safe='')}">
          <button class="button warn" name="estado" value="EN_PREPARACION">Marcar en preparación</button>
          <button class="button secondary" name="estado" value="DESPACHADO">Packing listo / despachar</button>
        </form>
      </div>

      <div class="panel" id="items">
        <div class="panel-head"><h2>Detalle de items</h2></div>
        <div class="table-wrap"><table><thead><tr><th>Producto</th><th>Código</th><th class="num">Cantidad</th><th>Unidad</th><th class="num">Precio</th><th class="num">Total</th></tr></thead><tbody>{render_items_rows(items)}</tbody></table></div>
      </div>
    </div>
    """
    return render_layout(f"Picking {pedido_num} - Replau", body)


def render_delivery_page(data: Dict[str, Any], token: str) -> str:
    order = data["order"]
    pedido_num = esc(order.get("pedido_num"))
    metodo_pago = esc(order.get("metodo_pago"))
    payment_badge = payment_badge_html(order.get("metodo_pago"))
    maps_url = order.get("maps_url")
    maps_link = f'<a class="button secondary" href="{esc(maps_url)}" target="_blank">Abrir Maps</a>' if maps_url else ""
    body = f"""
    <div class="page">
      <div class="topbar">
        <div>
          <h1>Delivery · Pedido {pedido_num}</h1>
          <div class="muted">Pantalla rápida para salida y entrega del pedido.</div>
        </div>
        <div class="actions">
          {badge_html(order.get('estado'))}
          {maps_link}
          <a class="button secondary" href="../../order/{pedido_num}?token={quote(token, safe='')}">Vista completa</a>
        </div>
      </div>

      <div class="payment-callout">
        <div>
          <div class="label">Cobro para el repartidor</div>
          <div class="value">{metodo_pago}</div>
        </div>
        <div>{payment_badge}</div>
      </div>

      <div class="grid-cards" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
        <div class="summary-card"><div class="k">Cliente</div><div class="v" style="font-size:18px">{esc(order.get('cliente_nombre'))}</div></div>
        <div class="summary-card"><div class="k">WhatsApp</div><div class="v" style="font-size:18px">{esc(order.get('whatsapp_number'))}</div></div>
        <div class="summary-card"><div class="k">Dirección</div><div class="v" style="font-size:16px">{esc(trim_text(order.get('direccion_confirmada') or order.get('direccion_detectada'), 90))}</div></div>
        <div class="summary-card"><div class="k">Total</div><div class="v">{money(order.get('total'))}</div></div>
      </div>

      {render_order_notes_panel(order)}

      <div class="panel priority-red">
        <div class="panel-head"><h2>Ruta y entrega</h2><div class="panel-sub">Usa Maps, confirma salida, y marca entrega al final.</div></div>
        <div class="list">
          <div class="list-item"><strong>Dirección</strong><div>{esc(order.get('direccion_confirmada') or order.get('direccion_detectada'))}</div></div>
          <div class="list-item"><strong>Referencia</strong><div>{esc(order.get('referencia') or 'Sin referencia')}</div></div>
          <div class="list-item"><strong>Pago</strong><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span>{metodo_pago}</span>{payment_badge}</div></div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head"><h2>Acciones de delivery</h2></div>
        <form method="post" action="/order/{pedido_num}/status" class="actions">
          <input type="hidden" name="token" value="{esc(token)}">
          <input type="hidden" name="next_url" value="/ops/delivery/{pedido_num}?token={quote(token, safe='')}">
          <button class="button secondary" name="estado" value="DESPACHADO">Confirmar salida</button>
          <button class="button good" name="estado" value="ENTREGADO">Marcar entregado</button>
          <button class="button danger" name="estado" value="ANULADO">Anular</button>
        </form>
      </div>

      <div class="panel" id="items">
        <div class="panel-head"><h2>Resumen del pedido</h2></div>
        <div class="table-wrap"><table><thead><tr><th>Producto</th><th>Código</th><th class="num">Cantidad</th><th>Unidad</th><th class="num">Precio</th><th class="num">Total</th></tr></thead><tbody>{render_items_rows(data['items'])}</tbody></table></div>
      </div>
    </div>
    """
    return render_layout(f"Delivery {pedido_num} - Replau", body)


def render_blocked_numbers_page(message: str = "") -> str:
    blocklist = load_blocklist()
    rows = ""
    for number, entry in sorted(blocklist.items()):
        rows += f"""
        <tr>
          <td><span class="mono">{esc(number)}</span></td>
          <td>{badge_html(str(entry.get('reason') or 'blocked').upper())}</td>
          <td>{esc(entry.get('sample_text'))}</td>
          <td>{esc(entry.get('blocked_at'))}</td>
          <td>
            <form method="post" action="/blocked/unblock" onsubmit="return confirm('¿Desbloquear {esc(number)}?');">
              <input type="hidden" name="whatsapp_number" value="{esc(number)}">
              <button class="button good" type="submit">Unblock</button>
            </form>
          </td>
        </tr>
        """
    if not rows:
        rows = '<tr><td colspan="5" class="tiny">No hay números bloqueados.</td></tr>'

    flash_html = f'<div class="panel"><div class="tiny">{esc(message)}</div></div>' if message else ''

    body = f"""
    <div class="page">
      <div class="topbar">
        <div>
          <h1>Números bloqueados</h1>
          <div class="muted">Vista rápida de moderación para WhatsApp.</div>
        </div>
        <div class="actions">
          <a class="button secondary" href="dashboard">Dashboard</a>
          <a class="button secondary" href="blocked">Actualizar</a>
        </div>
      </div>

      {flash_html}

      <div class="panel priority-red">
        <div class="panel-head">
          <h2>Bloqueos activos ({len(blocklist)})</h2>
          <div class="panel-sub">Motivo, muestra del mensaje y fecha del bloqueo</div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Número</th>
                <th>Motivo</th>
                <th>Muestra</th>
                <th>Bloqueado</th>
                <th>Acción</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </div>
    </div>
    """
    return render_layout("Números bloqueados - Replau", body, auto_refresh_seconds=30)


def fetch_public_order(pedido_num: str, token: str) -> Dict[str, Any]:
    data = pg_rpc(
        "obtener_pedido_publico",
        {
            "p_pedido_num": pedido_num,
            "p_token": token,
        },
    )
    if not data.get("ok"):
        raise HTTPException(status_code=403, detail=data)
    return data


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        response = requests.get(f"{POSTGREST_BASE_URL}/", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return {"ok": True, "postgrest_ok": True, "postgrest_base_url": POSTGREST_BASE_URL}
    except Exception as exc:
        return {"ok": False, "postgrest_ok": False, "error": str(exc)}


@app.get("/api/dashboard")
def api_dashboard(limit: int = Query(DASHBOARD_LIMIT, ge=1, le=100)) -> JSONResponse:
    try:
        return JSONResponse(fetch_dashboard_data(limit=limit))
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    limit: int = Query(DASHBOARD_LIMIT, ge=1, le=100),
    view: str = Query("all"),
    search: str = Query(""),
    order_status: str = Query("all"),
    conv_status: str = Query("all"),
    email_status: str = Query("all"),
) -> HTMLResponse:
    try:
        data = fetch_dashboard_data(limit=limit)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    return HTMLResponse(
        render_dashboard_page(
            data,
            view=view,
            search=search,
            order_status=order_status,
            conv_status=conv_status,
            email_status=email_status,
        )
    )


@app.get("/blocked", response_class=HTMLResponse)
def blocked_numbers_page(message: str = Query("")) -> HTMLResponse:
    return HTMLResponse(render_blocked_numbers_page(message=message))


@app.post("/blocked/unblock")
def blocked_numbers_unblock(whatsapp_number: str = Form(...)) -> RedirectResponse:
    data = load_blocklist()
    if whatsapp_number in data:
        data.pop(whatsapp_number, None)
        save_blocklist(data)
        message = f"Número {whatsapp_number} desbloqueado"
    else:
        message = f"Número {whatsapp_number} no estaba bloqueado"
    return RedirectResponse(url=f"/blocked?message={quote(message, safe='')}", status_code=303)


@app.get("/api/order/{pedido_num}")
def api_order(pedido_num: str, token: str = Query(...)) -> JSONResponse:
    try:
        data = pg_rpc(
            "obtener_pedido_publico",
            {
                "p_pedido_num": pedido_num,
                "p_token": token,
            },
        )
        return JSONResponse(data)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)


@app.get("/order/{pedido_num}", response_class=HTMLResponse)
def order_page(pedido_num: str, token: str = Query(...)) -> HTMLResponse:
    try:
        data = fetch_public_order(pedido_num, token)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    except HTTPException as exc:
        error = "Link inválido o vencido"
        return HTMLResponse(
            render_layout(
                "Link inválido",
                f'<div class="page"><div class="panel"><h1>{error}</h1><p>No pude validar el token del pedido.</p><a class="button secondary" href="/dashboard">Ir al dashboard</a></div></div>'
            ),
            status_code=exc.status_code,
        )

    return HTMLResponse(render_order_page(data, token))


@app.get("/ops/picking/{pedido_num}", response_class=HTMLResponse)
def picking_page(pedido_num: str, token: str = Query(...)) -> HTMLResponse:
    try:
        data = fetch_public_order(pedido_num, token)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    return HTMLResponse(render_picking_page(data, token))


@app.get("/ops/delivery/{pedido_num}", response_class=HTMLResponse)
def delivery_page(pedido_num: str, token: str = Query(...)) -> HTMLResponse:
    try:
        data = fetch_public_order(pedido_num, token)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    return HTMLResponse(render_delivery_page(data, token))


@app.post("/order/{pedido_num}/status")
def update_status(
    pedido_num: str,
    token: str = Form(...),
    estado: str = Form(...),
    next_url: str = Form(""),
):
    data = pg_rpc(
        "actualizar_estado_pedido_publico",
        {
            "p_pedido_num": pedido_num,
            "p_token": token,
            "p_estado": estado,
        },
    )

    if not data.get("ok"):
        raise HTTPException(status_code=403, detail=data)

    target = next_url or f"/order/{pedido_num}?token={quote(token, safe='')}"
    return RedirectResponse(url=target, status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("logistics_viewer:app", host=VIEWER_HOST, port=VIEWER_PORT, reload=False)
