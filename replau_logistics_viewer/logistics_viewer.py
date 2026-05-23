#!/usr/bin/env python3
from __future__ import annotations

import os
import html
import json
import time
import fcntl
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import quote, urlparse, parse_qs

import requests
from fastapi import FastAPI, HTTPException, Query, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
VIEWER_HOST = os.environ.get("VIEWER_HOST", "127.0.0.1")
VIEWER_PORT = int(os.environ.get("VIEWER_PORT", "8790"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
DASHBOARD_LIMIT = int(os.environ.get("DASHBOARD_LIMIT", "20"))
PAYMENT_PROOF_REVIEW_URL = os.environ.get("PAYMENT_PROOF_REVIEW_URL", "http://127.0.0.1:8795/")
PRODUCT_ADMIN_URL = os.environ.get("PRODUCT_ADMIN_URL", "http://127.0.0.1:8794/")
OPS_DASHBOARD_URL = os.environ.get("OPS_DASHBOARD_URL", "http://127.0.0.1:8793/")
BLOCKLIST_PATH = Path(os.environ.get("WHATSAPP_BLOCKLIST_PATH", "/home/guill/.openclaw/workspace/blocked_whatsapp_numbers.json"))
HUMAN_HANDOFF_PATH = Path(os.environ.get("REPLAU_HUMAN_HANDOFF_PATH", "/home/guill/.openclaw/workspace/replau_human_handoff.json"))
HUMAN_HANDOFF_LOCK_PATH = Path(os.environ.get("REPLAU_HUMAN_HANDOFF_LOCK_PATH", str(HUMAN_HANDOFF_PATH) + ".lock"))
SUCURSALES_PATH = Path(os.environ.get("REPLAU_SUCURSALES_PATH", "/home/guill/.openclaw/workspace/replau_sucursales.json"))
DELIVERY_PAYOUTS_PATH = Path(os.environ.get("REPLAU_DELIVERY_PAYOUTS_PATH", "/home/guill/.openclaw/workspace/replau_delivery_payouts.json"))
DELIVERY_PAYOUTS_LOCK_PATH = Path(os.environ.get("REPLAU_DELIVERY_PAYOUTS_LOCK_PATH", str(DELIVERY_PAYOUTS_PATH) + ".lock"))
CLEARED_EMAIL_LOGS_PATH = Path(os.environ.get("CLEARED_EMAIL_LOGS_PATH", "/home/guill/.openclaw/workspace/replau_cleared_email_logs.json"))
OPENCLAW_CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "/home/guill/.openclaw/openclaw.json"))


def load_google_maps_api_key() -> str:
    direct = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if direct:
        return direct
    try:
        cfg = json.loads(OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return ""
    candidates = [
        ("skills", "entries", "goplaces", "apiKey"),
        ("plugins", "entries", "google", "config", "mapsApiKey"),
        ("plugins", "entries", "google", "config", "apiKey"),
        ("plugins", "entries", "google", "config", "webSearch", "apiKey"),
    ]
    for path in candidates:
        node = cfg
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, str) and node.strip():
            return node.strip()
    return ""


GOOGLE_MAPS_API_KEY = load_google_maps_api_key()
GOOGLE_ROUTES_API_KEY = os.environ.get("GOOGLE_ROUTES_API_KEY", "").strip() or GOOGLE_MAPS_API_KEY
GOOGLE_ROUTES_URL = os.environ.get(
    "GOOGLE_ROUTES_URL",
    "https://routes.googleapis.com/directions/v2:computeRoutes",
).strip()

app = FastAPI(title="Replau Logistics Viewer", version="1.1.0")



def tokenized_local_service_url(base: str, process_marker: str, token_env: str, path: str = "") -> str:
    """Return local protected service URL with its token when visible from this host."""
    parsed = urlparse(base)
    if path and not parsed.path.rstrip("/").endswith(path.strip("/")):
        base = base.rstrip("/") + "/" + path.strip("/")
    if "token=" in base:
        return base
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
                if process_marker not in cmdline:
                    continue
                env = (proc / "environ").read_bytes().split(b"\0")
                prefix = (token_env + "=").encode()
                for item in env:
                    if item.startswith(prefix):
                        token = item.split(b"=", 1)[1].decode("utf-8", "ignore")
                        if token:
                            sep = "&" if "?" in base else "?"
                            return base + sep + "token=" + quote(token, safe="")
            except Exception:
                continue
    except Exception:
        pass
    return base


def payment_proof_review_url() -> str:
    return tokenized_local_service_url(PAYMENT_PROOF_REVIEW_URL, "replau_payment_proof_review.py", "REVIEW_TOKEN")


def product_admin_url(path: str = "") -> str:
    return tokenized_local_service_url(PRODUCT_ADMIN_URL, "replau_product_admin.py", "ADMIN_TOKEN", path)


def ops_dashboard_url() -> str:
    return tokenized_local_service_url(OPS_DASHBOARD_URL, "replau_health_dashboard.py", "OPS_TOKEN")


def erp_nav() -> str:
    return f"""
    <div class="erp-shell">
      <div class="erp-nav" aria-label="Replau ERP navigation">
        <a href="{esc(ops_dashboard_url())}">Ops</a>
        <a href="/dashboard">Logistics</a>
        <a href="http://127.0.0.1:8791/">Kitchen</a>
        <a href="{esc(payment_proof_review_url())}">Payments</a>
        <a href="{esc(product_admin_url())}">Products</a>
        <a href="{esc(product_admin_url("recipes"))}">Recipes</a>
        <a href="{esc(product_admin_url("costs"))}">Costs</a>
        <a href="http://127.0.0.1:8794/menu" target="_blank">Public Menu</a>
      </div>
    </div>
    """


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


def pg_post(path: str, payload: Dict[str, Any]) -> Any:
    return pg_request(
        "POST",
        path,
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        json=payload,
    )


def pg_patch(path: str, payload: Dict[str, Any]) -> Any:
    return pg_request(
        "PATCH",
        path,
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        json=payload,
    )


def pg_delete(path: str) -> Any:
    return pg_request(
        "DELETE",
        path,
        headers={"Prefer": "return=representation"},
    )


def delivery_assignment_notes(existing: Any, addition: str) -> str:
    existing_text = str(existing or "").strip()
    addition_text = addition.strip()
    if existing_text and addition_text:
        return f"{existing_text}\n{addition_text}"
    return existing_text or addition_text


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


def load_human_handoffs() -> Dict[str, Any]:
    try:
        if HUMAN_HANDOFF_PATH.exists():
            data = json.loads(HUMAN_HANDOFF_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                entries = data.get("entries")
                return entries if isinstance(entries, dict) else data
    except Exception:
        return {}
    return {}


def save_human_handoffs(entries: Dict[str, Any]) -> None:
    HUMAN_HANDOFF_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    HUMAN_HANDOFF_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


@contextmanager
def locked_human_handoffs():
    HUMAN_HANDOFF_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HUMAN_HANDOFF_LOCK_PATH.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def handoff_entry_for(number: Any) -> Dict[str, Any] | None:
    phone = clean_phone_digits(number)
    if not phone:
        return None
    entry = load_human_handoffs().get(phone)
    if isinstance(entry, dict) and entry.get("active", True):
        return entry
    return None


def handoff_badge_html(number: Any) -> str:
    entry = handoff_entry_for(number)
    if not entry:
        return ""
    reason = str(entry.get("reason") or "Handoff").strip()
    return f'<span class="badge" style="background:#fef3c7;color:#92400e">HUMANO: {esc(trim_text(reason, 28))}</span>'


def handoff_form_html(number: Any, next_url: str, compact: bool = False) -> str:
    phone = clean_phone_digits(number)
    if not phone:
        return ""
    entry = handoff_entry_for(phone)
    if entry:
        return f"""
        <form method="post" action="/handoff/resume" onsubmit="return confirm('¿Reactivar bot para {esc(phone)}?');">
          <input type="hidden" name="whatsapp_number" value="{esc(phone)}">
          <input type="hidden" name="next_url" value="{esc(next_url)}">
          <button class="button good" type="submit">Reactivar bot</button>
        </form>
        """
    reason_input = "" if compact else '<input name="reason" placeholder="Motivo de handoff" style="padding:10px;min-width:180px">'
    return f"""
    <form method="post" action="/handoff/start" onsubmit="return confirm('¿Pausar bot para {esc(phone)}?');">
      <input type="hidden" name="whatsapp_number" value="{esc(phone)}">
      <input type="hidden" name="next_url" value="{esc(next_url)}">
      {reason_input}
      <button class="button warn" type="submit">Handoff humano</button>
    </form>
    """


def load_sucursales() -> List[Dict[str, Any]]:
    try:
        if SUCURSALES_PATH.exists():
            data = json.loads(SUCURSALES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("sucursales"), list):
                return data["sucursales"]
    except Exception:
        pass
    return []


def save_sucursales(rows: List[Dict[str, Any]]) -> None:
    SUCURSALES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUCURSALES_PATH.write_text(json.dumps({"sucursales": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_delivery_payouts() -> Dict[str, Any]:
    try:
        if DELIVERY_PAYOUTS_PATH.exists():
            data = json.loads(DELIVERY_PAYOUTS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("next_id", 1)
                data.setdefault("batches", [])
                return data
    except Exception:
        pass
    return {"next_id": 1, "batches": []}


def save_delivery_payouts(data: Dict[str, Any]) -> None:
    DELIVERY_PAYOUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DELIVERY_PAYOUTS_PATH.with_suffix(DELIVERY_PAYOUTS_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(DELIVERY_PAYOUTS_PATH)


@contextmanager
def locked_delivery_payouts():
    DELIVERY_PAYOUTS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DELIVERY_PAYOUTS_LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def update_delivery_payouts(mutator: Callable[[Dict[str, Any]], Tuple[Dict[str, Any], Any]]) -> Any:
    with locked_delivery_payouts():
        payouts = load_delivery_payouts()
        updated, result = mutator(payouts)
        save_delivery_payouts(updated)
        return result


def payout_assignment_ids(payouts: Dict[str, Any], include_open: bool = True) -> set[int]:
    statuses = {"PAID", "OPEN"} if include_open else {"PAID"}
    ids: set[int] = set()
    for batch in payouts.get("batches", []):
        if str(batch.get("status") or "OPEN").upper() not in statuses:
            continue
        for assignment_id in batch.get("assignment_ids", []):
            try:
                ids.add(int(assignment_id))
            except Exception:
                continue
    return ids


def fetch_completed_delivery_assignments() -> List[Dict[str, Any]]:
    try:
        return pg_get(
            "/v_delivery_asignaciones?status=eq.COMPLETED"
            "&order=completed_at.desc.nullslast,created_at.desc&limit=500"
        )
    except requests.HTTPError:
        return []


def money_value(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def delivery_account_rows(drivers: List[Dict[str, Any]], completed: List[Dict[str, Any]], payouts: Dict[str, Any]) -> List[Dict[str, Any]]:
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    batched_ids = payout_assignment_ids(payouts, include_open=True)
    for assignment in completed:
        try:
            assignment_id = int(assignment.get("id"))
            driver_id = int(assignment.get("repartidor_id"))
        except Exception:
            continue
        if assignment_id in batched_ids:
            continue
        grouped.setdefault(driver_id, []).append(assignment)

    paid_totals: Dict[int, float] = {}
    open_totals: Dict[int, float] = {}
    last_paid: Dict[int, Dict[str, Any]] = {}
    for batch in payouts.get("batches", []):
        try:
            driver_id = int(batch.get("repartidor_id"))
        except Exception:
            continue
        status = str(batch.get("status") or "OPEN").upper()
        total = money_value(batch.get("total_amount"))
        if status == "PAID":
            paid_totals[driver_id] = paid_totals.get(driver_id, 0.0) + total
            current = last_paid.get(driver_id)
            if current is None or str(batch.get("paid_at") or "") > str(current.get("paid_at") or ""):
                last_paid[driver_id] = batch
        elif status == "OPEN":
            open_totals[driver_id] = open_totals.get(driver_id, 0.0) + total

    rows: List[Dict[str, Any]] = []
    for driver in drivers:
        try:
            driver_id = int(driver.get("id"))
        except Exception:
            continue
        unpaid = grouped.get(driver_id, [])
        rows.append({
            "driver": driver,
            "unpaid_assignments": unpaid,
            "unpaid_route_count": len(unpaid),
            "unpaid_total": sum(money_value(a.get("fee")) for a in unpaid),
            "open_total": open_totals.get(driver_id, 0.0),
            "paid_total": paid_totals.get(driver_id, 0.0),
            "last_paid": last_paid.get(driver_id),
        })
    return rows


def load_cleared_email_ids() -> set[int]:
    try:
        if CLEARED_EMAIL_LOGS_PATH.exists():
            data = json.loads(CLEARED_EMAIL_LOGS_PATH.read_text(encoding="utf-8"))
            return {int(v) for v in data.get("cleared_ids", [])}
    except Exception:
        pass
    return set()


def save_cleared_email_ids(ids: set[int]) -> None:
    CLEARED_EMAIL_LOGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLEARED_EMAIL_LOGS_PATH.write_text(
        json.dumps({"cleared_ids": sorted(ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def conversation_clear_form_html(whatsapp_number: Any, next_url: str = "/dashboard?view=conversations") -> str:
    phone = clean_phone_digits(whatsapp_number)
    if not phone:
        return ""
    return f"""
      <form method="post" action="/conversation/{esc(phone)}/clear" onsubmit="return confirm('¿Limpiar conversación {esc(phone)}? Se marcará como CANCELLED y saldrá de Conversaciones activas.');">
        <input type="hidden" name="next_url" value="{esc(next_url)}">
        <button class="button good" type="submit">Clear</button>
      </form>
    """


def conversation_clear_all_form_html(count: int, next_url: str = "/dashboard?view=conversations") -> str:
    if count <= 0:
        return ""
    return f"""
      <form method="post" action="/conversations/clear-all" onsubmit="return confirm('¿Limpiar todas las conversaciones activas? Se marcarán como CANCELLED y saldrán de Conversaciones activas.');">
        <input type="hidden" name="next_url" value="{esc(next_url)}">
        <button class="button good" type="submit">Clear all</button>
      </form>
    """


def email_clear_all_form_html(
    count: int,
    view: str = "all",
    search: str = "",
    order_status: str = "all",
    conv_status: str = "all",
    email_status: str = "all",
) -> str:
    if count <= 0:
        return ""
    next_url = (
        f"/dashboard?view={quote(view, safe='')}"
        f"&search={quote(search, safe='')}"
        f"&order_status={quote(order_status, safe='')}"
        f"&conv_status={quote(conv_status, safe='')}"
        f"&email_status={quote(email_status, safe='')}"
    )
    return f"""
      <form method="post" action="/email-logs/clear-all" onsubmit="return confirm('¿Limpiar todos los emails visibles de la cola logística? No se borran los registros.');">
        <input type="hidden" name="view" value="{esc(view)}">
        <input type="hidden" name="search" value="{esc(search)}">
        <input type="hidden" name="order_status" value="{esc(order_status)}">
        <input type="hidden" name="conv_status" value="{esc(conv_status)}">
        <input type="hidden" name="email_status" value="{esc(email_status)}">
        <input type="hidden" name="next_url" value="{esc(next_url)}">
        <button class="button good" type="submit">Clear all</button>
      </form>
    """


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


def is_pickup_fulfillment(order: Dict[str, Any]) -> bool:
    # Best-effort pickup detection without requiring a DB schema change.
    notes = str(order.get("observacion") or "").upper()
    address = str(order.get("direccion_confirmada") or order.get("direccion_detectada") or "").upper()
    if "MODALIDAD: DELIVERY" in notes:
        return False
    return (
        "MODALIDAD: RECOJO" in notes
        or "MODALIDAD: PICKUP" in notes
        or "RECOJO EN RESTAURANTE" in notes
        or address.startswith("RECOJO EN RESTAURANTE")
    )


def order_workflow_stage(order: Dict[str, Any]) -> str:
    estado = str(order.get("estado") or "")
    kitchen_status = str(order.get("kitchen_status") or "")
    if estado == "DESPACHADO":
        return "delivery"
    # Operational flow: CONFIRMADO/EN_PREPARACION belongs to kitchen until
    # kitchen marks it LISTO. Only then should it enter Picking.
    if estado in {"CONFIRMADO", "EN_PREPARACION"} and kitchen_status == "LISTO":
        return "picking"
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
            "human_handoffs": len([c for c in conversations if isinstance(c.get("human_handoff"), dict)]),
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
    handoffs = load_human_handoffs()
    email_logs = pg_get(f"/email_logistica_log?order=id.desc&limit={limit}")
    cleared_email_ids = load_cleared_email_ids()
    if cleared_email_ids:
        email_logs = [row for row in email_logs if int(row.get("id") or 0) not in cleared_email_ids]
    reservations = pg_get(f"/v_pedidos_reserva_resumen?order=pedido_id.desc&limit={limit}")
    items = pg_get(f"/v_pedido_items_logistica?order=pedido_id.desc&limit={limit * 3}")

    reservation_by_num = {row.get("pedido_num"): row for row in reservations if row.get("pedido_num")}
    email_by_pedido = {row.get("pedido_id"): row for row in email_logs if row.get("pedido_id") is not None}
    kitchen_by_pedido: Dict[Any, Dict[str, Any]] = {}
    order_ids = [str(order.get("id")) for order in orders if order.get("id") is not None]
    if order_ids:
        try:
            kitchen_rows = pg_get(
                "/pedidos?select=id,kitchen_status,kitchen_started_at,kitchen_ready_at,kitchen_notes"
                f"&id=in.({','.join(order_ids)})"
            )
            kitchen_by_pedido = {row.get("id"): row for row in kitchen_rows if row.get("id") is not None}
        except Exception:
            # Older DB snapshots may not have kitchen columns; keep logistics usable.
            kitchen_by_pedido = {}

    orders_enriched: List[Dict[str, Any]] = []
    for order in orders:
        merged = dict(order)
        merged.update(kitchen_by_pedido.get(order.get("id"), {}))
        merged["reservation"] = reservation_by_num.get(order.get("pedido_num"))
        merged["email_log"] = email_by_pedido.get(order.get("id"))
        merged["human_handoff"] = handoffs.get(clean_phone_digits(order.get("whatsapp_number")))
        orders_enriched.append(merged)

    conversations_enriched: List[Dict[str, Any]] = []
    for conv in conversations:
        merged = dict(conv)
        merged["human_handoff"] = handoffs.get(clean_phone_digits(conv.get("whatsapp_number")))
        conversations_enriched.append(merged)

    derived = build_summary(orders_enriched, conversations_enriched, email_logs, reservations)

    return {
        "summary": derived["summary"],
        "orders": orders_enriched,
        "conversations": conversations_enriched,
        "email_logs": email_logs,
        "reservations": reservations,
        "items": items,
        "human_handoffs": handoffs,
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
    :root {{
      --bg:#0f172a; --bg-2:#020617; --card:#111827; --card-2:#0b1220; --text:#e5e7eb; --muted:#94a3b8; --line:#334155;
      --blue:#3b82f6; --green:#22c55e; --orange:#8b5cf6; --red:#22c55e; --purple:#8b5cf6; --brand:#8b5cf6;
      --radius:22px; --shadow:0 18px 48px rgba(0,0,0,.35);
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family:"Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:radial-gradient(circle at top left, rgba(249,115,22,.16), transparent 30%), radial-gradient(circle at top right, rgba(59,130,246,.10), transparent 28%), linear-gradient(180deg,var(--bg),var(--bg-2)); color:var(--text); }}
    a {{ color:#93c5fd; text-decoration: none; font-weight:700; }}
    .erp-shell {{ max-width:1400px; margin:0 auto; padding:18px 28px 0; }}
    .erp-nav {{ display:flex; flex-wrap:wrap; gap:8px; padding:12px; border:1px solid rgba(51,65,85,.95); border-radius:16px; background:rgba(11,18,32,.92); box-shadow:0 10px 28px rgba(0,0,0,.22); }}
    .erp-nav a {{ color:#e5e7eb; background:#1f2937; border:1px solid #334155; border-radius:999px; padding:8px 11px; font-size:13px; font-weight:850; }}
    .erp-nav a:hover {{ background:linear-gradient(135deg,var(--brand),#6d28d9); border-color:transparent; }}
    .page {{ max-width: 1400px; margin: 0 auto; padding: 28px; }}
    .topbar {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; flex-wrap:wrap; margin-bottom:24px; padding:20px 22px; background:rgba(17,24,39,.88); border:1px solid rgba(51,65,85,.95); border-radius:var(--radius); box-shadow:var(--shadow); backdrop-filter:blur(10px); }}
    .topbar h1 {{ margin:0 0 6px; font-size:clamp(32px,4vw,48px); line-height:1; letter-spacing:-.045em; }}
    .muted {{ color:var(--muted); }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; }}
    .button {{ display:inline-flex; align-items:center; justify-content:center; border:0; border-radius:14px; padding:11px 15px; background:linear-gradient(135deg,var(--brand),#6d28d9); color:white; text-decoration:none; cursor:pointer; font-size:14px; font-weight:850; box-shadow:0 10px 24px rgba(139,92,246,.24); transition:transform .14s ease, filter .14s ease; }}
    .button:hover {{ transform:translateY(-1px); filter:brightness(1.04); }}
    .button.secondary {{ background:#374151; box-shadow:none; }}
    .button.good {{ background:linear-gradient(135deg,#2fb36d,var(--green)); }}
    .button.warn {{ background:linear-gradient(135deg,#a78bfa,var(--orange)); color:white; }}
    .button.danger {{ background:linear-gradient(135deg,#4ade80,var(--red)); color:#052e16; }}
    input, select, textarea {{ background:#020617; color:#e5e7eb; border:1px solid var(--line); border-radius:12px; }}
    input::placeholder {{ color:#64748b; }}
    .grid-cards {{ display:grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap:16px; margin-bottom:22px; }}
    .summary-card, .panel {{ background:rgba(17,24,39,.96); border:1px solid rgba(51,65,85,.95); border-radius:var(--radius); padding:20px; box-shadow:var(--shadow); }}
    .summary-card .k {{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; font-weight:850; }}
    .summary-card .v {{ margin-top:8px; font-size:34px; line-height:1; font-weight:950; letter-spacing:-.04em; }}
    .panel {{ margin-bottom:20px; border-top:5px solid transparent; }}
    .panel.priority-red {{ border-top-color: var(--red); background:linear-gradient(180deg, rgba(22,101,52,.22) 0%, rgba(17,24,39,.96) 34%); }}
    .panel.priority-orange {{ border-top-color: var(--orange); background:linear-gradient(180deg, rgba(109,40,217,.22) 0%, rgba(17,24,39,.96) 34%); }}
    .panel.priority-purple {{ border-top-color: var(--purple); background:linear-gradient(180deg, rgba(76,29,149,.22) 0%, rgba(17,24,39,.96) 34%); }}
    .panel h2 {{ margin:0 0 14px; font-size:24px; letter-spacing:-.025em; }}
    .panel-head {{ display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }}
    .panel-sub {{ font-size:13px; color:var(--muted); }}
    .chips {{ display:flex; flex-wrap:wrap; gap:9px; }}
    .chip {{ background:#020617; border:1px solid var(--line); border-radius:999px; padding:8px 11px; font-size:13px; color:#cbd5e1; font-weight:750; }}
    .chip-link {{ display:inline-block; background:#020617; color:#cbd5e1; border-radius:999px; padding:8px 11px; font-size:13px; text-decoration:none; border:1px solid var(--line); font-weight:800; }}
    .chip-link.active {{ background:linear-gradient(135deg,var(--brand),#6d28d9); color:white; border-color:transparent; }}
    .badge {{ display:inline-block; padding:7px 11px; border-radius:999px; font-size:12px; font-weight:900; box-shadow:inset 0 0 0 1px rgba(255,255,255,.18); }}
    .payment-badge {{ letter-spacing:.04em; }}
    .table-wrap {{ overflow:auto; border-radius:18px; border:1px solid var(--line); }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; min-width:900px; background:#0b1220; }}
    th, td {{ text-align:left; padding:13px 14px; border-bottom:1px solid var(--line); vertical-align:top; }}
    tr:last-child td {{ border-bottom:0; }}
    tr.stale-warn td {{ background:rgba(109,40,217,.18); }}
    tr.stale-danger td {{ background:rgba(22,101,52,.16); }}
    th {{ background:#020617; font-size:12px; color:#93c5fd; text-transform:uppercase; letter-spacing:.07em; position:sticky; top:0; font-weight:900; }}
    td.num, th.num {{ text-align:right; }}
    .stack {{ display:flex; flex-direction:column; gap:5px; }}
    .tiny {{ font-size:12px; color:var(--muted); }}
    .stale {{ display:inline-block; margin-top:4px; font-size:11px; font-weight:900; border-radius:999px; padding:5px 9px; background:#1e3a8a; color:#bfdbfe; }}
    .stale.warn {{ background:#78350f; color:#fde68a; }}
    .stale.danger {{ background:#166534; color:#bbf7d0; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .two-col {{ display:grid; grid-template-columns: 2fr 1fr; gap:20px; }}
    .list {{ display:grid; gap:11px; }}
    .list-item {{ background:#0b1220; border:1px solid var(--line); border-radius:16px; padding:14px; }}
    .list-item strong {{ display:block; margin-bottom:4px; }}
    .payment-callout {{ display:flex; align-items:center; justify-content:space-between; gap:12px; padding:16px 18px; border-radius:18px; border:1px solid var(--line); background:linear-gradient(180deg, #020617 0%, #111827 100%); margin-bottom:16px; }}
    .payment-callout .label {{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; font-weight:850; }}
    .payment-callout .value {{ font-size:22px; font-weight:950; margin-top:4px; letter-spacing:-.02em; }}
    .workspace-grid {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:12px; }}
    .workspace-card {{ background:#0b1220; border:1px solid rgba(139,92,246,.26); border-radius:16px; padding:14px; min-height:112px; }}
    .workspace-card .k {{ color:#c4b5fd; font-size:12px; font-weight:900; text-transform:uppercase; letter-spacing:.06em; }}
    .workspace-card .v {{ margin:8px 0 6px; font-size:30px; line-height:1; font-weight:950; }}
    .order-notes {{ white-space:pre-wrap; font-size:16px; line-height:1.55; color:#ddd6fe; background:rgba(109,40,217,.16); border:1px solid rgba(139,92,246,.45); border-left:6px solid var(--orange); border-radius:18px; padding:16px 18px; }}
    @media (max-width: 1100px) {{ .grid-cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .workspace-grid {{ grid-template-columns:repeat(3,minmax(0,1fr)); }} .two-col {{ grid-template-columns: 1fr; }} }}
    @media (max-width: 640px) {{ .page {{ padding:12px; }} .grid-cards,.workspace-grid {{ grid-template-columns: 1fr; }} .topbar h1 {{ font-size:26px; }} }}
  </style>
</head>
<body>
{erp_nav()}
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
    else:
        # Clear marks orders as ANULADO. Hide them from the default Todos view,
        # but keep the ANULADO status chip available for explicit review.
        orders = [o for o in orders if str(o.get("estado") or "") != "ANULADO"]
    if conv_status != "all":
        conversations = [c for c in conversations if str(c.get("estado") or "") == conv_status]
    else:
        # Clear marks conversations as CANCELLED. Hide closed conversations from
        # the default Todos list, but keep status chips available for audit.
        conversations = [
            c for c in conversations
            if str(c.get("estado") or "") not in {"CONFIRMED", "ANULADO", "CANCELLED"}
        ]
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
    handoff_active = len([c for c in data["conversations"] if isinstance(c.get("human_handoff"), dict)])
    cash_orders = len([o for o in data["orders"] if str(o.get("metodo_pago") or "").upper() in {"EFECTIVO", "CASH"}])

    cards = [
        ("Pedidos recientes", summary["orders_total"]),
        ("Confirmados", summary["orders_confirmed"]),
        ("En operación", summary["orders_in_progress"]),
        ("Handoff humano", summary["human_handoffs"]),
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

    workspace_cards = [
        ("Atención cliente", len(urgent_conversations), "Conversaciones esperando dirección o intervención"),
        ("Pedidos a mover", len(dispatch_orders), "Confirmados, preparación o despacho"),
        ("Picking", len([o for o in data["orders"] if order_workflow_stage(o) == "picking"]), "Listos para preparar/recolectar"),
        ("Delivery", len([o for o in data["orders"] if order_workflow_stage(o) == "delivery"]), "Despachados, ruta o recojo cliente"),
        ("Handoff humano", handoff_active, "Clientes con bot pausado"),
        ("Cobro efectivo", cash_orders, "Pedidos donde el repartidor/caja debe cobrar"),
    ]
    workspace_html = "".join(
        f'<div class="workspace-card"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div><div class="tiny">{esc(detail)}</div></div>'
        for label, value, detail in workspace_cards
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
    conv_open_count = sum(
        1
        for c in search_scoped["conversations"]
        if str(c.get("estado") or "") not in {"CONFIRMED", "ANULADO", "CANCELLED"}
    )
    email_status_counts = Counter(str(e.get("status") or "SIN_ESTADO") for e in search_scoped["email_logs"])
    order_filter_html = ''.join(
        f'<a class="chip-link {"active" if status == order_status else ""}" href="dashboard?view={quote(view, safe="")}&search={quote(search, safe="")}&order_status={quote(status, safe="")}&conv_status={quote(conv_status, safe="")}&email_status={quote(email_status, safe="")}">{esc(label)} ({count})</a>'
        for status, label, count in [("all", "Todos", len(search_scoped["orders"]))] + [(s, s, c) for s, c in sorted(order_status_counts.items())]
    )
    conv_filter_html = ''.join(
        f'<a class="chip-link {"active" if status == conv_status else ""}" href="dashboard?view={quote(view, safe="")}&search={quote(search, safe="")}&order_status={quote(order_status, safe="")}&conv_status={quote(status, safe="")}&email_status={quote(email_status, safe="")}">{esc(label)} ({count})</a>'
        for status, label, count in [("all", "Todos", conv_open_count)] + [(s, s, c) for s, c in sorted(conv_status_counts.items())]
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
              {handoff_badge_html(order.get('whatsapp_number'))}
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
              {handoff_form_html(order.get('whatsapp_number'), '/dashboard', compact=True)}
              {f'<form method="post" action="/order/{esc(order.get("pedido_num"))}/status" onsubmit="return confirm(\'¿Limpiar {esc(order.get("pedido_num"))} de Pedidos recientes? Se marcará como ANULADO y saldrá de la cola activa.\');"><input type="hidden" name="token" value="{esc(order_token(order))}"><input type="hidden" name="next_url" value="/dashboard"><button class="button good" name="estado" value="ANULADO">Clear</button></form>' if order_token(order) else ''}
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
          <td><div class="stack"><span class="mono">{esc(conv.get('whatsapp_number'))}</span>{handoff_badge_html(conv.get('whatsapp_number'))}{stale_html(conv.get('updated_at'), warn_after=15, danger_after=35)}</div></td>
          <td>{badge_html(conv.get('estado'))}</td>
          <td>{esc(trim_text(conv.get('last_inbound_text'), 90))}</td>
          <td>{esc(trim_text(conv.get('last_outbound_text'), 120))}</td>
          <td>{esc(conv.get('updated_at'))}</td>
          <td><div class="actions">{handoff_form_html(conv.get('whatsapp_number'), '/dashboard?view=conversations', compact=True)}{conversation_clear_form_html(conv.get('whatsapp_number'))}</div></td>
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
          <td><form method="post" action="/email-log/{esc(log.get('id'))}/clear" onsubmit="return confirm('¿Limpiar este email de la cola logística? No se borra el registro.');"><button class="button good" type="submit">Clear</button></form></td>
        </tr>
        """

    reservation_list = "".join(
        f'''<div class="list-item"><strong>{esc(row.get("pedido_num"))} · {esc(row.get("cliente_nombre"))}</strong><div class="tiny">Estado: {esc(row.get("estado_pedido"))}</div><div class="tiny">Reservas activas: {esc(row.get("reservas_activas"))} · Cantidad activa: {esc(row.get("cantidad_reservada_activa"))}</div><div class="tiny">Consumos: {esc(row.get("reservas_consumidas"))} · Liberadas: {esc(row.get("reservas_liberadas"))}</div></div>'''
        for row in reservations[:8]
    ) or '<div class="list-item">Sin reservas.</div>'

    urgent_conv_list = "".join(
        f'''<div class="list-item"><strong>{esc(c.get("whatsapp_number"))}</strong><div style="display:flex;gap:8px;flex-wrap:wrap">{badge_html(c.get("estado"))}{handoff_badge_html(c.get("whatsapp_number"))}</div><div class="tiny">{esc(trim_text(c.get("last_inbound_text"), 90))}</div><div class="actions" style="margin-top:10px">{handoff_form_html(c.get("whatsapp_number"), "/dashboard", compact=True)}</div></div>'''
        for c in urgent_conversations
    ) or '<div class="list-item">Nada urgente en conversaciones.</div>'
    urgent_email_list = "".join(
        f'''<div class="list-item"><strong>Pedido {esc(e.get("pedido_id"))}</strong><div>{badge_html(e.get("status"))}</div><div class="tiny">{esc(trim_text(e.get("subject"), 90))}</div></div>'''
        for e in urgent_emails
    ) or '<div class="list-item">Sin emails urgentes.</div>'
    dispatch_list = "".join(
        f'''<div class="list-item"><strong>{esc(o.get("pedido_num"))} · {esc(o.get("cliente_nombre"))}</strong><div style="display:flex;gap:8px;flex-wrap:wrap">{badge_html(o.get("estado"))}{payment_badge_html(o.get("metodo_pago"))}</div><div class="tiny">{money(o.get("total"))} · {esc(trim_text(o.get("direccion_confirmada") or o.get("direccion_detectada"), 70))}</div><div class="actions" style="margin-top:10px">{f'<a class="button" href="{esc(o.get("order_url") or "#")}" target="_blank">Ver pedido</a>' if o.get("order_url") else ''}{f'<form method="post" action="/order/{esc(o.get("pedido_num"))}/status" onsubmit="return confirm(\'¿Limpiar {esc(o.get("pedido_num"))} de Pedidos a mover? Se marcará como ANULADO y saldrá de la cola activa.\');"><input type="hidden" name="token" value="{esc(order_token(o))}"><input type="hidden" name="next_url" value="/dashboard"><button class="button good" name="estado" value="ANULADO">Clear</button></form>' if order_token(o) else ''}</div></div>'''
        for o in dispatch_orders
    ) or '<div class="list-item">Sin pedidos en operación.</div>'

    picking_orders = [o for o in data["orders"] if order_workflow_stage(o) == "picking"][:8]
    delivery_orders = [o for o in data["orders"] if order_workflow_stage(o) == "delivery"][:8]
    picking_list = "".join(
        f'''<div class="list-item"><strong>{esc(o.get("pedido_num"))} · {esc(o.get("cliente_nombre"))}</strong><div style="display:flex;gap:8px;flex-wrap:wrap">{badge_html(o.get("estado"))}{payment_badge_html(o.get("metodo_pago"))}</div><div class="tiny">{money(o.get("total"))} · {esc(trim_text(o.get("direccion_confirmada") or o.get("direccion_detectada"), 60))}</div><div class="actions" style="margin-top:10px"><a class="button warn" href="ops/picking/{esc(o.get("pedido_num"))}?token={quote(order_token(o), safe='')}">Picking</a><form method="post" action="/order/{esc(o.get("pedido_num"))}/status" onsubmit="return confirm('¿Limpiar {esc(o.get("pedido_num"))} de Picking? Se marcará como ANULADO y saldrá de la cola activa.');"><input type="hidden" name="token" value="{esc(order_token(o))}"><input type="hidden" name="next_url" value="/dashboard"><button class="button good" name="estado" value="ANULADO">Clear</button></form></div></div>'''
        for o in picking_orders if order_token(o)
    ) or '<div class="list-item">Sin pedidos para picking.</div>'
    delivery_list = "".join(
        f'''<div class="list-item"><strong>{esc(o.get("pedido_num"))} · {esc(o.get("cliente_nombre"))}</strong><div style="display:flex;gap:8px;flex-wrap:wrap">{badge_html(o.get("estado"))}{payment_badge_html(o.get("metodo_pago"))}</div><div class="tiny">{money(o.get("total"))} · {esc(trim_text(o.get("direccion_confirmada") or o.get("direccion_detectada"), 60))}</div><div class="actions" style="margin-top:10px"><a class="button secondary" href="ops/delivery/{esc(o.get("pedido_num"))}?token={quote(order_token(o), safe='')}">Delivery</a><form method="post" action="/order/{esc(o.get("pedido_num"))}/status" onsubmit="return confirm('¿Limpiar {esc(o.get("pedido_num"))} de Delivery? Se marcará como ANULADO y saldrá de la cola activa.');"><input type="hidden" name="token" value="{esc(order_token(o))}"><input type="hidden" name="next_url" value="/dashboard"><button class="button good" name="estado" value="ANULADO">Clear</button></form></div></div>'''
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
          <a class="button warn" href="/ops/picking">Picking Station</a>
          <a class="button good" href="/ops/delivery">Delivery Station</a>
          <a class="button warn" href="{esc(payment_proof_review_url())}" target="_blank">Comprobantes de pago</a>
          <a class="button secondary" href="{esc(product_admin_url('costs'))}" target="_blank">Costos / Stock</a>
          <a class="button secondary" href="{esc(product_admin_url())}" target="_blank">Productos</a>
          <a class="button secondary" href="{esc(ops_dashboard_url())}" target="_blank">Ops health</a>
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
          <input type="text" name="search" value="{esc(search)}" placeholder="Buscar pedido, cliente, WhatsApp, dirección..." style="min-width:320px;max-width:520px;flex:1;padding:12px 14px;border:1px solid var(--line);border-radius:14px;font-size:14px;font:inherit;background:#020617;color:#e5e7eb;">
          <button class="button" type="submit">Buscar</button>
          <a class="button secondary" href="dashboard?view={esc(view)}">Limpiar</a>
        </form>
      </div>

      <div class="grid-cards">{card_html}</div>

      <div class="panel priority-purple">
        <div class="panel-head">
          <h2>Logistics Workspace</h2>
          <div class="panel-sub">Vista por rol para cliente, picking, delivery, handoff y cobros.</div>
        </div>
        <div class="workspace-grid">{workspace_html}</div>
      </div>

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
            <div>
              <h2>Conversaciones activas</h2>
              <div class="panel-sub">Clientes atorados o en espera de siguiente paso</div>
            </div>
            <div class="actions">{conversation_clear_all_form_html(len(conversations))}</div>
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
                <th>Handoff</th>
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
              <div class="actions">
                <div class="panel-sub">Últimos correos generados</div>
                {email_clear_all_form_html(len(email_logs), view, search, order_status, conv_status, email_status)}
              </div>
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
                    <th>Acción</th>
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



def render_picking_station_page(data: Dict[str, Any]) -> str:
    orders = [o for o in data["orders"] if order_workflow_stage(o) == "picking" and order_token(o)]
    item_rows_by_pedido_id: Dict[Any, List[Dict[str, Any]]] = {}
    for item in data.get("items", []):
        item_rows_by_pedido_id.setdefault(item.get("pedido_id"), []).append(item)

    cards = ""
    for idx, order in enumerate(orders, start=1):
        pedido_num_raw = str(order.get("pedido_num") or "")
        pedido_num = esc(pedido_num_raw)
        token = order_token(order)
        items = item_rows_by_pedido_id.get(order.get("id"), [])
        item_checklist = "".join(
            f"""<label class="station-item"><input type="checkbox"><span><strong>{esc(item.get('producto_texto'))}</strong><small>Cant: {esc(item.get('cantidad'))} · {esc(item.get('unidad'))} · {money(item.get('total_linea'))}</small></span></label>"""
            for item in items
        ) or '<div class="station-empty">Sin items registrados.</div>'
        notes = str(order.get("observacion") or "").strip()
        notes_html = f'<div class="station-notes"><strong>Notas:</strong><br>{esc(notes)}</div>' if notes else ""
        maps_url = order.get("maps_url") or "#"
        card_class = "station-card first" if idx == 1 else "station-card"
        cards += f"""
        <section class="{card_class}">
          <div class="station-card-head">
            <div>
              <div class="station-kicker">#{idx} en cola</div>
              <h2>{pedido_num}</h2>
              <div class="station-meta">{esc(order.get('cliente_nombre'))} · {money(order.get('total'))}</div>
            </div>
            <div class="station-badges">{badge_html(order.get('estado'))}{payment_badge_html(order.get('metodo_pago'))}{stale_html(order.get('created_at'), warn_after=20, danger_after=50)}</div>
          </div>
          <div class="station-grid">
            <div>
              <h3>Items para recoger/preparar</h3>
              <div class="station-items">{item_checklist}</div>
            </div>
            <div>
              <h3>Datos rápidos</h3>
              <div class="station-facts">
                <div><span>WhatsApp</span><strong>{esc(order.get('whatsapp_number'))}</strong></div>
                <div><span>Pago</span><strong>{esc(order.get('metodo_pago'))}</strong></div>
                <div><span>Dirección</span><strong>{esc(trim_text(order.get('direccion_confirmada') or order.get('direccion_detectada'), 120))}</strong></div>
              </div>
              {notes_html}
              <div class="actions station-actions">
                <a class="button" href="/ops/picking/{pedido_num}?token={quote(token, safe='')}">Abrir pedido</a>
                <a class="button secondary" href="{esc(maps_url)}" target="_blank">Maps</a>
                <form method="post" action="/order/{pedido_num}/status">
                  <input type="hidden" name="token" value="{esc(token)}">
                  <input type="hidden" name="next_url" value="/ops/picking">
                  <button class="button secondary" name="estado" value="DESPACHADO">Preparado / listo para delivery</button>
                </form>
                <form method="post" action="/order/{pedido_num}/status" onsubmit="return confirm('¿Limpiar {pedido_num} de Picking? Se marcará como ANULADO y saldrá de la cola activa.');">
                  <input type="hidden" name="token" value="{esc(token)}">
                  <input type="hidden" name="next_url" value="/ops/picking">
                  <button class="button good" name="estado" value="ANULADO">Clear</button>
                </form>
              </div>
            </div>
          </div>
        </section>
        """

    if not cards:
        cards = '<div class="panel"><h2>Sin pedidos para picking</h2><p class="muted">Cuando cocina marque un pedido como LISTO, aparecerá aquí automáticamente.</p></div>'

    body = f"""
    <div class="page picking-station">
      <div class="topbar">
        <div>
          <h1>Picking Station</h1>
          <div class="muted">Pantalla fija para el área de picking. Auto-refresh cada 10s.</div>
        </div>
        <div class="actions">
          <span class="chip">{len(orders)} pedidos listos</span>
          <a class="button" href="/ops/picking">Actualizar</a>
          <a class="button secondary" href="/dashboard">Dashboard</a>
        </div>
      </div>
      <style>
        .picking-station .station-card {{ background:linear-gradient(180deg, rgba(15,23,42,.98), rgba(17,24,39,.96)); border:1px solid rgba(139,92,246,.28); border-top:5px solid var(--orange); border-radius:26px; padding:22px; margin:18px 0; box-shadow:0 22px 55px rgba(0,0,0,.28); }}
        .picking-station .station-card.first {{ border-top-color:var(--green); box-shadow:0 24px 70px rgba(34,197,94,.16); }}
        .station-card-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:18px; margin-bottom:18px; }}
        .station-kicker {{ color:#a7f3d0; text-transform:uppercase; font-weight:900; font-size:12px; letter-spacing:.08em; }}
        .station-card h2 {{ margin:4px 0; font-size:clamp(30px,4vw,48px); letter-spacing:-.04em; }}
        .station-meta {{ color:#cbd5e1; font-size:17px; font-weight:700; }}
        .station-badges {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
        .station-grid {{ display:grid; grid-template-columns:1.2fr .8fr; gap:18px; }}
        .station-card h3 {{ margin:0 0 12px; color:#ddd6fe; }}
        .station-items {{ display:grid; gap:10px; }}
        .station-item {{ display:flex; gap:12px; align-items:flex-start; background:rgba(255,255,255,.045); border:1px solid rgba(255,255,255,.08); border-radius:16px; padding:13px; font-size:18px; }}
        .station-item input {{ width:22px; height:22px; margin-top:2px; accent-color:var(--green); }}
        .station-item small {{ display:block; color:#94a3b8; margin-top:4px; font-size:13px; }}
        .station-facts {{ display:grid; gap:10px; }}
        .station-facts div, .station-notes {{ background:rgba(139,92,246,.12); border:1px solid rgba(139,92,246,.25); border-radius:16px; padding:12px; }}
        .station-facts span {{ display:block; color:#94a3b8; font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.06em; }}
        .station-facts strong {{ display:block; margin-top:4px; color:#f8fafc; }}
        .station-notes {{ margin-top:12px; color:#ddd6fe; line-height:1.45; }}
        .driver-quick-actions {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:10px; margin-top:16px; }}
        .driver-quick-actions form {{ margin:0; }}
        .driver-btn {{ width:100%; min-height:58px; font-size:18px; border-radius:18px; }}
        .station-actions {{ margin-top:12px; align-items:center; }}
        .station-actions form {{ display:inline-flex; margin:0; }}
        .station-empty {{ color:#94a3b8; padding:14px; }}
        @media(max-width:900px) {{ .station-grid, .station-card-head {{ grid-template-columns:1fr; display:block; }} .station-badges {{ justify-content:flex-start; margin-top:12px; }} .driver-quick-actions {{ grid-template-columns:1fr; }} }}
      </style>
      {cards}
    </div>
    """
    return render_layout("Picking Station - Replau", body, auto_refresh_seconds=10)

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
          <button class="button secondary" name="estado" value="DESPACHADO">Preparado / listo para delivery</button>
          <button class="button good" name="estado" value="ANULADO" onclick="return confirm('¿Limpiar este pedido de Picking? Se marcará como ANULADO y saldrá de la cola activa.');">Clear</button>
        </form>
      </div>

      <div class="panel" id="items">
        <div class="panel-head"><h2>Detalle de items</h2></div>
        <div class="table-wrap"><table><thead><tr><th>Producto</th><th>Código</th><th class="num">Cantidad</th><th>Unidad</th><th class="num">Precio</th><th class="num">Total</th></tr></thead><tbody>{render_items_rows(items)}</tbody></table></div>
      </div>
    </div>
    """
    return render_layout(f"Picking {pedido_num} - Replau", body)



def delivery_priority_key(order: Dict[str, Any]) -> tuple[int, str]:
    # Oldest active delivery first. Orders without timestamps go last.
    minutes = age_minutes(order.get("updated_at") or order.get("created_at"))
    return (-(minutes if minutes is not None else -1), str(order.get("pedido_num") or ""))


def clean_phone_digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def whatsapp_customer_url(order: Dict[str, Any]) -> str:
    phone = clean_phone_digits(order.get("whatsapp_number"))
    return f"https://wa.me/{phone}" if phone else ""


def delivery_route_url(order: Dict[str, Any], assignment: Dict[str, Any] | None = None) -> str:
    customer_lat = parse_coord(order.get("latitud"))
    customer_lon = parse_coord(order.get("longitud"))
    driver_lat = parse_coord((assignment or {}).get("driver_latitude"))
    driver_lon = parse_coord((assignment or {}).get("driver_longitude"))
    if customer_lat is not None and customer_lon is not None:
        url = "https://www.google.com/maps/dir/?api=1"
        if driver_lat is not None and driver_lon is not None:
            url += f"&origin={driver_lat},{driver_lon}"
        url += f"&destination={customer_lat},{customer_lon}&travelmode=driving"
        return url
    return str(order.get("maps_url") or "")


def render_delivery_progress(order: Dict[str, Any], assignment: Dict[str, Any] | None) -> str:
    estado = str(order.get("estado") or "")
    if is_pickup_fulfillment(order):
        completed = estado == "ENTREGADO"
        steps = [
            ("Listo para recojo", estado in {"DESPACHADO", "ENTREGADO"}),
            ("Cliente en puerta", False),
            ("Entregado", completed),
        ]
        return "".join(
            f'<span class="delivery-step {"done" if done else ""}">{esc(label)}</span>'
            for label, done in steps
        )
    notes = str((assignment or {}).get("notes") or "").upper()
    has_assignment = bool(assignment)
    has_driver_location = bool((assignment or {}).get("driver_latitude") and (assignment or {}).get("driver_longitude"))
    is_on_the_way = "EN_CAMINO" in notes
    arrived = "LLEGO_DESTINO" in notes
    completed = estado == "ENTREGADO" or str((assignment or {}).get("status") or "") == "COMPLETED"
    steps = [
        ("Despachado", estado in {"DESPACHADO", "ENTREGADO"}),
        ("Repartidor", has_assignment),
        ("Ubicación", has_driver_location),
        ("En camino", is_on_the_way or arrived or completed),
        ("Llegó", arrived or completed),
        ("Terminado", completed),
    ]
    return "".join(
        f'<span class="delivery-step {"done" if done else ""}">{esc(label)}</span>'
        for label, done in steps
    )


def render_delivery_station_page(data: Dict[str, Any]) -> str:
    overview = fetch_delivery_overview()
    drivers = overview.get("drivers", [])
    active_drivers = [d for d in drivers if bool(d.get("activo"))]
    active_assignments = overview.get("assignments", [])
    assignment_by_pedido_id: Dict[Any, Dict[str, Any]] = {}
    for assignment_row in active_assignments:
        pedido_id = assignment_row.get("pedido_id")
        if pedido_id is not None and pedido_id not in assignment_by_pedido_id:
            assignment_by_pedido_id[pedido_id] = assignment_row
    driver_load: Dict[int, int] = {}
    for assignment_row in active_assignments:
        try:
            driver_id = int(assignment_row.get("repartidor_id"))
        except Exception:
            continue
        driver_load[driver_id] = driver_load.get(driver_id, 0) + 1

    orders = sorted(
        [o for o in data["orders"] if order_workflow_stage(o) == "delivery" and order_token(o)],
        key=delivery_priority_key,
    )
    item_rows_by_pedido_id: Dict[Any, List[Dict[str, Any]]] = {}
    for item in data.get("items", []):
        item_rows_by_pedido_id.setdefault(item.get("pedido_id"), []).append(item)

    def lane_for(order: Dict[str, Any], assignment: Dict[str, Any] | None) -> str:
        if is_pickup_fulfillment(order):
            return "pickup"
        status = str((assignment or {}).get("status") or "")
        notes = str((assignment or {}).get("notes") or "").upper()
        if not assignment:
            return "unassigned"
        if status == "OFFERED":
            return "offered"
        if "LLEGO_DESTINO" in notes:
            return "arrived"
        if "EN_CAMINO" in notes or assignment.get("driver_latitude"):
            return "en_route"
        return "assigned"

    lane_labels = {
        "unassigned": "Sin repartidor",
        "offered": "Ofrecidos",
        "assigned": "Asignados",
        "en_route": "En camino",
        "arrived": "Llegaron",
        "pickup": "Recojo cliente",
    }
    lane_counts = Counter()
    order_assignments: Dict[Any, Dict[str, Any] | None] = {}
    for order in orders:
        assignment = None if is_pickup_fulfillment(order) else assignment_by_pedido_id.get(order.get("id")) or fetch_delivery_assignment(order.get("id"))
        order_assignments[order.get("id")] = assignment
        lane_counts[lane_for(order, assignment)] += 1

    driver_options = "".join(
        f'<option value="{esc(driver.get("id"))}">{esc(driver.get("codigo"))} - {esc(driver.get("nombre"))} ({driver_load.get(int(driver.get("id") or 0), 0)} activas)</option>'
        for driver in active_drivers
    )
    driver_cards = "".join(
        f"""
        <div class="dispatch-driver-card">
          <strong>{esc(driver.get('codigo'))}</strong>
          <span>{esc(driver.get('nombre'))}</span>
          <b>{driver_load.get(int(driver.get('id') or 0), 0)} activas</b>
        </div>
        """
        for driver in active_drivers
    ) or '<div class="dispatch-driver-card muted">No hay repartidores activos.</div>'

    summary_cards = [
        ("Delivery / recojo", len(orders)),
        ("Sin repartidor", lane_counts.get("unassigned", 0)),
        ("Ofrecidos", lane_counts.get("offered", 0)),
        ("En ruta / llegaron", lane_counts.get("en_route", 0) + lane_counts.get("arrived", 0)),
        ("Repartidores activos", len(active_drivers)),
    ]
    summary_html = "".join(
        f'<div class="summary-card dispatch-kpi"><div class="k">{esc(label)}</div><div class="v">{esc(value)}</div></div>'
        for label, value in summary_cards
    )
    lane_nav = "".join(
        f'<a class="chip-link" href="#lane-{esc(key)}">{esc(label)} ({lane_counts.get(key, 0)})</a>'
        for key, label in lane_labels.items()
    )

    cards_by_lane: Dict[str, str] = {key: "" for key in lane_labels}
    for idx, order in enumerate(orders, start=1):
        pedido_num_raw = str(order.get("pedido_num") or "")
        pedido_num = esc(pedido_num_raw)
        token = order_token(order)
        items = item_rows_by_pedido_id.get(order.get("id"), [])
        item_summary = "".join(
            f"""<div class="station-item delivery-item"><span><strong>{esc(item.get('producto_texto'))}</strong><small>Cant: {esc(item.get('cantidad'))} · {esc(item.get('unidad'))} · {money(item.get('total_linea'))}</small></span></div>"""
            for item in items
        ) or '<div class="station-empty">Sin items registrados.</div>'
        notes = str(order.get("observacion") or "").strip()
        notes_html = f'<div class="station-notes"><strong>Notas:</strong><br>{esc(notes)}</div>' if notes else ""
        pickup_order = is_pickup_fulfillment(order)
        assignment = order_assignments.get(order.get("id"))
        maps_url = order.get("maps_url") or "#"
        route_url = delivery_route_url(order, assignment) or maps_url
        customer_chat_url = whatsapp_customer_url(order)
        customer_chat_link = f'<a class="button secondary" href="{esc(customer_chat_url)}" target="_blank">WhatsApp cliente</a>' if customer_chat_url else ""
        address = order.get("direccion_confirmada") or order.get("direccion_detectada") or ""
        lane = lane_for(order, assignment)
        progress_html = render_delivery_progress(order, assignment)
        assignment_status = "Recojo cliente" if pickup_order else str((assignment or {}).get("status") or "Sin repartidor")
        driver_label = "No requiere repartidor" if pickup_order else ((assignment or {}).get("repartidor_nombre") or (assignment or {}).get("repartidor_codigo") or "Pendiente")
        direct_assign_form = "" if pickup_order else f"""
              <form method="post" action="/ops/delivery/assign-driver" class="dispatch-assign-form">
                <input type="hidden" name="pedido_num" value="{pedido_num}">
                <label class="tiny">Asignar a<br>
                  <select name="repartidor_id" required>
                    <option value="">Elegir repartidor</option>
                    {driver_options}
                  </select>
                </label>
                <button class="button good" type="submit">Asignar directo</button>
              </form>
        """
        pickup_callout = f'''
              <div class="pickup-callout">
                <strong>🛍️ RECOJO CLIENTE</strong>
                <span>No asignar repartidor. Cuando el cliente reciba el pedido en puerta, usa el botón grande.</span>
                <form method="post" action="/order/{pedido_num}/status">
                  <input type="hidden" name="token" value="{esc(token)}">
                  <input type="hidden" name="next_url" value="/ops/delivery">
                  <button class="button good pickup-handoff" name="estado" value="ENTREGADO">✅ Entregado a cliente en puerta</button>
                </form>
              </div>
        ''' if pickup_order else ""
        card_class = "station-card first pickup-card" if idx == 1 and pickup_order else ("station-card pickup-card" if pickup_order else ("station-card first" if idx == 1 else "station-card"))
        cards_by_lane[lane] += f"""
        <section class="{card_class}">
          <div class="station-card-head">
            <div>
              <div class="station-kicker">#{idx} en ruta</div>
              <h2>{pedido_num}</h2>
              <div class="station-meta">{esc(order.get('cliente_nombre'))} · {money(order.get('total'))}</div>
              <div class="delivery-progress">{progress_html}</div>
            </div>
            <div class="station-badges">{badge_html(order.get('estado'))}{payment_badge_html(order.get('metodo_pago'))}{badge_html(assignment_status)}{stale_html(order.get('updated_at') or order.get('created_at'), warn_after=20, danger_after=50)}</div>
          </div>
          <div class="station-grid">
            <div>
              <h3>Entrega</h3>
              <div class="delivery-address"><strong>Dirección</strong><br>{esc(address)}</div>
              <div class="actions station-actions compact-actions">
                <a class="button good" href="/ops/delivery/{pedido_num}?token={quote(token, safe='')}#mapa">Mapa</a>
                <a class="button secondary" href="{esc(route_url)}" target="_blank">Ruta</a>
                <button class="button secondary" type="button" onclick="navigator.clipboard && navigator.clipboard.writeText(this.dataset.copy || '')" data-copy="{esc(address)}">Copiar dirección</button>
                {customer_chat_link}
              </div>
              <div class="station-facts" style="margin-top:12px">
                <div><span>WhatsApp</span><strong>{esc(order.get('whatsapp_number'))}</strong></div>
                <div><span>Pago / Cobro</span><strong>{esc(order.get('metodo_pago'))} · {money(order.get('total'))}</strong></div>
                <div><span>Repartidor</span><strong>{esc(driver_label)}</strong></div>
                <div><span>Referencia</span><strong>{esc(order.get('referencia') or 'Sin referencia')}</strong></div>
              </div>
              {notes_html}
            </div>
            <div>
              <h3>Resumen del pedido</h3>
              <div class="station-items">{item_summary}</div>
              {pickup_callout}
              {'' if pickup_order else render_driver_assignment_panel(order.get('id'), token)}
              {direct_assign_form}
              <div class="driver-quick-actions">
                <form method="post" action="/order/{pedido_num}/status">
                  <input type="hidden" name="token" value="{esc(token)}">
                  <input type="hidden" name="next_url" value="/ops/delivery">
                  <button class="button secondary driver-btn" name="estado" value="DESPACHADO">🛵 Salí</button>
                </form>
                <form method="post" action="/order/{pedido_num}/status">
                  <input type="hidden" name="token" value="{esc(token)}">
                  <input type="hidden" name="next_url" value="/ops/delivery">
                  <button class="button good driver-btn" name="estado" value="ENTREGADO">✅ Entregado</button>
                </form>
                <form method="post" action="/order/{pedido_num}/status" onsubmit="return confirm('¿Marcar problema en {pedido_num}? Se sacará de la cola activa como ANULADO para revisión manual.');">
                  <input type="hidden" name="token" value="{esc(token)}">
                  <input type="hidden" name="next_url" value="/ops/delivery">
                  <button class="button danger driver-btn" name="estado" value="ANULADO">⚠️ Problema</button>
                </form>
              </div>
              <div class="actions station-actions">
                <a class="button" href="/ops/delivery/{pedido_num}?token={quote(token, safe='')}">Abrir pedido</a>
                <a class="button secondary" href="/ops/delivery/{pedido_num}?token={quote(token, safe='')}#mapa">Mapa</a>
                <form method="post" action="/ops/delivery/offer-next">
                  <input type="hidden" name="pedido_num" value="{pedido_num}">
                  <button class="button secondary" type="submit">Ofrecer repartidor</button>
                </form>
                <form method="post" action="/order/{pedido_num}/status" onsubmit="return confirm('¿Limpiar {pedido_num} de Delivery Station? Se marcará como ANULADO y saldrá de la cola activa.');">
                  <input type="hidden" name="token" value="{esc(token)}">
                  <input type="hidden" name="next_url" value="/ops/delivery">
                  <button class="button good" name="estado" value="ANULADO">Clear</button>
                </form>
              </div>
            </div>
          </div>
        </section>
        """

    if orders:
        cards = "".join(
            f"""
            <section class="dispatch-lane" id="lane-{esc(key)}">
              <div class="dispatch-lane-head">
                <h2>{esc(label)}</h2>
                <span class="chip">{lane_counts.get(key, 0)} pedidos</span>
              </div>
              {cards_by_lane.get(key) or '<div class="station-empty">Sin pedidos en esta columna.</div>'}
            </section>
            """
            for key, label in lane_labels.items()
        )
    else:
        cards = '<div class="panel"><h2>Sin pedidos para delivery</h2><p class="muted">Cuando Picking despache un pedido, aparecerá aquí automáticamente.</p></div>'

    body = f"""
    <div class="page delivery-station picking-station">
      <div class="topbar">
        <div>
          <h1>Delivery Station</h1>
          <div class="muted">Pantalla fija para delivery y recojo cliente. Auto-refresh cada 10s.</div>
        </div>
        <div class="actions">
          <span class="chip">{len(orders)} pedidos delivery/recojo</span>
          <a class="button" href="/ops/delivery">Actualizar</a>
          <a class="button secondary" href="/dashboard">Dashboard</a>
        </div>
      </div>
      <div class="grid-cards dispatch-kpis">{summary_html}</div>
      <div class="panel dispatch-board-panel">
        <div class="panel-head"><h2>Dispatch Board</h2><div class="panel-sub">Lanes operativas, carga por repartidor y asignación directa desde la misma pantalla.</div></div>
        <div class="dispatch-board-tools">
          <div class="chips">{lane_nav}</div>
          <div class="dispatch-driver-strip">{driver_cards}</div>
        </div>
      </div>
      {render_delivery_ops_panel()}
      {render_sucursales_panel()}
      <style>
        .delivery-station .station-card {{ background:linear-gradient(180deg, rgba(15,23,42,.98), rgba(17,24,39,.96)); border:1px solid rgba(34,197,94,.26); border-top:5px solid var(--green); border-radius:26px; padding:22px; margin:18px 0; box-shadow:0 22px 55px rgba(0,0,0,.28); }}
        .delivery-station .station-card.first {{ border-top-color:var(--orange); box-shadow:0 24px 70px rgba(139,92,246,.16); }}
        .station-card-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:18px; margin-bottom:18px; }}
        .station-kicker {{ color:#a7f3d0; text-transform:uppercase; font-weight:900; font-size:12px; letter-spacing:.08em; }}
        .station-card h2 {{ margin:4px 0; font-size:clamp(30px,4vw,48px); letter-spacing:-.04em; }}
        .station-meta {{ color:#cbd5e1; font-size:17px; font-weight:700; }}
        .station-badges {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
        .delivery-progress {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }}
        .delivery-step {{ border:1px solid rgba(148,163,184,.35); color:#94a3b8; border-radius:999px; padding:6px 10px; font-size:12px; font-weight:900; text-transform:uppercase; letter-spacing:.06em; }}
        .delivery-step.done {{ background:rgba(34,197,94,.16); border-color:rgba(34,197,94,.42); color:#bbf7d0; }}
        .station-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
        .station-card h3 {{ margin:0 0 12px; color:#ddd6fe; }}
        .delivery-address {{ font-size:22px; line-height:1.35; background:rgba(34,197,94,.12); border:1px solid rgba(34,197,94,.28); border-radius:18px; padding:16px; }}
        .station-items {{ display:grid; gap:10px; }}
        .station-item {{ display:flex; gap:12px; align-items:flex-start; background:rgba(255,255,255,.045); border:1px solid rgba(255,255,255,.08); border-radius:16px; padding:13px; font-size:17px; }}
        .station-item small {{ display:block; color:#94a3b8; margin-top:4px; font-size:13px; }}
        .station-facts {{ display:grid; gap:10px; }}
        .station-facts div, .station-notes {{ background:rgba(139,92,246,.12); border:1px solid rgba(139,92,246,.25); border-radius:16px; padding:12px; }}
        .station-facts span {{ display:block; color:#94a3b8; font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.06em; }}
        .station-facts strong {{ display:block; margin-top:4px; color:#f8fafc; }}
        .station-notes {{ margin-top:12px; color:#ddd6fe; line-height:1.45; }}
        .driver-quick-actions {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:10px; margin-top:16px; }}
        .pickup-card {{ border-top-color:#f59e0b; }}
        .pickup-callout {{ background:rgba(245,158,11,.16); border:1px solid rgba(245,158,11,.38); border-radius:18px; padding:16px; margin:14px 0; display:grid; gap:10px; }}
        .pickup-callout strong {{ color:#fde68a; font-size:22px; letter-spacing:.04em; }}
        .pickup-callout span {{ color:#fffbeb; }}
        .pickup-handoff {{ width:100%; min-height:88px; font-size:22px; border-radius:20px; }}
        .driver-quick-actions form {{ margin:0; }}
        .driver-btn {{ width:100%; min-height:64px; font-size:18px; border-radius:18px; }}
        .station-actions {{ margin-top:14px; align-items:center; }}
        .station-actions.compact-actions {{ gap:8px; }}
        .station-actions.compact-actions .button {{ min-height:44px; }}
        .station-actions form {{ display:inline-flex; margin:0; }}
        .station-empty {{ color:#94a3b8; padding:14px; }}
        .dispatch-kpis {{ grid-template-columns:repeat(5, minmax(0, 1fr)); }}
        .dispatch-kpi .v {{ color:#bbf7d0; }}
        .dispatch-board-panel {{ border-top-color:#22c55e; }}
        .dispatch-board-tools {{ display:grid; grid-template-columns:1fr; gap:14px; }}
        .dispatch-driver-strip {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:10px; }}
        .dispatch-driver-card {{ background:rgba(255,255,255,.045); border:1px solid rgba(255,255,255,.09); border-radius:14px; padding:12px; display:grid; gap:4px; }}
        .dispatch-driver-card strong {{ color:#bbf7d0; }}
        .dispatch-driver-card span {{ color:#e5e7eb; font-size:13px; font-weight:800; }}
        .dispatch-driver-card b {{ color:#94a3b8; font-size:12px; }}
        .dispatch-lane {{ margin:24px 0 30px; scroll-margin-top:16px; }}
        .dispatch-lane-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin:0 0 10px; padding:0 4px; }}
        .dispatch-lane-head h2 {{ margin:0; color:#f8fafc; font-size:26px; letter-spacing:-.02em; }}
        .dispatch-assign-form {{ margin-top:14px; display:grid; grid-template-columns:1fr auto; gap:10px; align-items:end; }}
        .dispatch-assign-form select {{ width:100%; min-height:46px; padding:11px; }}
        @media(max-width:900px) {{ .station-grid, .station-card-head {{ grid-template-columns:1fr; display:block; }} .station-badges {{ justify-content:flex-start; margin-top:12px; }} .driver-quick-actions {{ grid-template-columns:1fr; }} }}
        @media(max-width:900px) {{ .dispatch-kpis {{ grid-template-columns:1fr 1fr; }} .dispatch-assign-form {{ grid-template-columns:1fr; }} }}
      </style>
      {cards}
    </div>
    """
    return render_layout("Delivery Station - Replau", body, auto_refresh_seconds=10)

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

      {render_driver_assignment_panel(order.get('id'), token)}

      {render_delivery_map_panel(order, fetch_delivery_assignment(order.get('id')), 'Mapa de entrega', token)}

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
          <button class="button secondary" name="estado" value="DESPACHADO">🛵 Salí</button>
          <button class="button good" name="estado" value="ENTREGADO">✅ Entregado</button>
          <button class="button danger" name="estado" value="ANULADO" onclick="return confirm('¿Marcar problema en este delivery? Se sacará de la cola activa como ANULADO para revisión manual.');">⚠️ Problema</button>
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




def fetch_delivery_overview() -> Dict[str, Any]:
    try:
        drivers = pg_get("/repartidores?order=orden_turno.asc,id.asc")
    except requests.HTTPError:
        drivers = []
    try:
        assignments = pg_get(
            "/v_delivery_asignaciones?status=in.(OFFERED,ACCEPTED,ASSIGNED)"
            "&order=created_at.desc&limit=50"
        )
    except requests.HTTPError:
        assignments = []
    completed = fetch_completed_delivery_assignments()
    payouts = load_delivery_payouts()
    return {"drivers": drivers, "assignments": assignments, "completed": completed, "payouts": payouts}


def render_sucursales_panel() -> str:
    sucursales = sorted(load_sucursales(), key=lambda r: (str(r.get("codigo") or ""), str(r.get("nombre") or "")))
    rows = ""
    for s in sucursales:
        active = bool(s.get("activo", True))
        maps_url = ""
        lat = s.get("latitud")
        lon = s.get("longitud")
        if lat not in (None, "") and lon not in (None, ""):
            maps_url = f"https://www.google.com/maps?q={lat},{lon}"
        elif s.get("direccion"):
            maps_url = f"https://www.google.com/maps/search/?api=1&query={quote(str(s.get('direccion')), safe='')}"
        maps_link = f'<a href="{esc(maps_url)}" target="_blank">Maps</a>' if maps_url else "—"
        next_active = "false" if active else "true"
        action_label = "Pausar" if active else "Activar"
        action_class = "button danger" if active else "button good"
        rows += f"""
        <tr>
          <td><strong>{esc(s.get('codigo'))}</strong></td>
          <td>{esc(s.get('nombre'))}</td>
          <td>{esc(s.get('direccion'))}<br><span class="tiny">{esc(s.get('referencia') or '')}</span></td>
          <td><span class="mono">{esc(lat or '—')}, {esc(lon or '—')}</span></td>
          <td>{esc(s.get('telefono') or '—')}</td>
          <td>{maps_link}</td>
          <td>{'<span class="badge" style="background:#dcfce7;color:#166534">ACTIVA</span>' if active else '<span class="badge" style="background:#fee2e2;color:#991b1b">PAUSADA</span>'}</td>
          <td>
            <form method="post" action="/ops/delivery/sucursal-active">
              <input type="hidden" name="codigo" value="{esc(s.get('codigo'))}">
              <input type="hidden" name="activo" value="{next_active}">
              <button class="{action_class}" type="submit">{action_label}</button>
            </form>
          </td>
        </tr>
        """
    if not rows:
        rows = '<tr><td colspan="8" class="tiny">No hay restaurantes / puntos de recojo configurados todavía.</td></tr>'

    return f"""
    <div class="panel priority-orange">
      <div class="panel-head"><h2>Restaurantes / puntos de recojo</h2><div class="panel-sub">Otros restaurantes donde se puede recoger pedido. Dirección y coordenadas son datos clave para rutas y ETA.</div></div>
      <div class="panel" style="margin-bottom:16px;background:rgba(255,255,255,.04)">
        <div class="panel-head"><h3>Agregar restaurante / punto de recojo</h3><div class="panel-sub">Usa código corto. Ej: REST_SURCO, POLLERIA1, LOCAL_MIRAFLORES.</div></div>
        <form method="post" action="/ops/delivery/sucursal-create" class="actions" style="align-items:end;gap:10px;flex-wrap:wrap">
          <label class="tiny">Código<br><input name="codigo" placeholder="REST_SURCO" required style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;width:130px"></label>
          <label class="tiny">Restaurante<br><input name="nombre" placeholder="Nombre restaurante" required style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;min-width:190px"></label>
          <label class="tiny">Dirección de recojo<br><input name="direccion" placeholder="Dirección exacta de recojo" required style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;min-width:280px"></label>
          <label class="tiny">Teléfono<br><input name="telefono" placeholder="519..." style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;width:135px"></label>
          <label class="tiny">Latitud<br><input name="latitud" placeholder="-12.119915" required style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;width:125px"></label>
          <label class="tiny">Longitud<br><input name="longitud" placeholder="-76.991731" required style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;width:125px"></label>
          <label class="tiny">Referencia<br><input name="referencia" placeholder="Cruce, puerta, piso" style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;min-width:170px"></label>
          <label class="tiny" style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><input type="checkbox" name="activo" value="true" checked> Disponible para recojo</label>
          <button class="button good" type="submit">Agregar punto de recojo</button>
        </form>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Código</th><th>Restaurante</th><th>Dirección de recojo</th><th>Coordenadas</th><th>Teléfono</th><th>Mapa</th><th>Estado</th><th>Acción</th></tr></thead><tbody>{rows}</tbody></table></div>
    </div>
    """


def render_delivery_account_panel(drivers: List[Dict[str, Any]], completed: List[Dict[str, Any]], payouts: Dict[str, Any]) -> str:
    account_rows = delivery_account_rows(drivers, completed, payouts)
    rows_html = ""
    for row in account_rows:
        driver = row["driver"]
        driver_id = driver.get("id")
        last_paid = row.get("last_paid") or {}
        can_create = row["unpaid_route_count"] > 0
        create_button = f"""
              <form method="post" action="/ops/delivery/payout-create" onsubmit="return confirm('¿Crear liquidación para {esc(driver.get('codigo'))} por {money(row['unpaid_total'])}?');">
                <input type="hidden" name="repartidor_id" value="{esc(driver_id)}">
                <button class="button good" type="submit">Consolidar</button>
              </form>
        """ if can_create else '<span class="tiny">Sin rutas nuevas</span>'
        rows_html += f"""
        <tr>
          <td><strong>{esc(driver.get('codigo'))}</strong><br><span class="tiny">{esc(driver.get('nombre'))}</span></td>
          <td class="num"><strong>{esc(row['unpaid_route_count'])}</strong></td>
          <td class="num"><strong>{money(row['unpaid_total'])}</strong></td>
          <td class="num">{money(row['open_total'])}</td>
          <td class="num">{money(row['paid_total'])}</td>
          <td>{esc(last_paid.get('paid_at') or '—')}<br><span class="tiny">{money(last_paid.get('total_amount')) if last_paid else ''}</span></td>
          <td>{create_button}</td>
        </tr>
        """
    if not rows_html:
        rows_html = '<tr><td colspan="7" class="tiny">No hay repartidores configurados.</td></tr>'

    open_rows = ""
    recent_batches = sorted(
        [b for b in payouts.get("batches", []) if str(b.get("status") or "OPEN").upper() == "OPEN"],
        key=lambda b: str(b.get("created_at") or ""),
        reverse=True,
    )
    for batch in recent_batches[:20]:
        open_rows += f"""
        <tr>
          <td><strong>#{esc(batch.get('id'))}</strong><br><span class="tiny">{esc(batch.get('created_at'))}</span></td>
          <td>{esc(batch.get('repartidor_codigo'))}<br><span class="tiny">{esc(batch.get('repartidor_nombre'))}</span></td>
          <td class="num">{esc(batch.get('route_count'))}</td>
          <td class="num"><strong>{money(batch.get('total_amount'))}</strong></td>
          <td>
            <form method="post" action="/ops/delivery/payout-paid" class="actions" style="gap:6px;align-items:end;flex-wrap:wrap" onsubmit="return confirm('¿Marcar liquidación #{esc(batch.get('id'))} como pagada?');">
              <input type="hidden" name="batch_id" value="{esc(batch.get('id'))}">
              <label class="tiny">Método<br><input name="payment_method" placeholder="Yape" style="padding:9px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;width:90px"></label>
              <label class="tiny">Referencia<br><input name="payment_reference" placeholder="Operación" style="padding:9px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;width:120px"></label>
              <button class="button good" type="submit">Marcar pagado</button>
            </form>
          </td>
        </tr>
        """
    if not open_rows:
        open_rows = '<tr><td colspan="5" class="tiny">No hay liquidaciones abiertas.</td></tr>'

    return f"""
    <div class="panel priority-purple">
      <div class="panel-head"><h2>Cuenta repartidor</h2><div class="panel-sub">Consolida varias rutas completadas en una sola liquidación por repartidor.</div></div>
      <div class="grid-cards" style="grid-template-columns:1.1fr .9fr; align-items:start">
        <div>
          <h3>Saldos por repartidor</h3>
          <div class="table-wrap"><table><thead><tr><th>Repartidor</th><th class="num">Rutas nuevas</th><th class="num">Por consolidar</th><th class="num">Abierto</th><th class="num">Pagado</th><th>Último pago</th><th>Acción</th></tr></thead><tbody>{rows_html}</tbody></table></div>
        </div>
        <div>
          <h3>Liquidaciones abiertas</h3>
          <div class="table-wrap"><table><thead><tr><th>Liq.</th><th>Repartidor</th><th class="num">Rutas</th><th class="num">Total</th><th>Pago</th></tr></thead><tbody>{open_rows}</tbody></table></div>
        </div>
      </div>
    </div>
    """


def render_delivery_ops_panel() -> str:
    overview = fetch_delivery_overview()
    drivers = overview.get("drivers", [])
    assignments = overview.get("assignments", [])
    completed = overview.get("completed", [])
    payouts = overview.get("payouts", {})

    driver_rows = ""
    for d in drivers:
        active = bool(d.get("activo"))
        next_active = "false" if active else "true"
        action_label = "Pausar" if active else "Activar"
        action_class = "button danger" if active else "button good"
        driver_rows += f"""
        <tr>
          <td><strong>{esc(d.get('codigo'))}</strong></td>
          <td>{esc(d.get('nombre'))}</td>
          <td><span class="mono">{esc(d.get('whatsapp_number'))}</span></td>
          <td>{'<span class="badge" style="background:#dcfce7;color:#166534">AUTORIZADO</span>' if active else '<span class="badge" style="background:#fee2e2;color:#991b1b">PAUSADO</span>'}</td>
          <td>
            <div class="actions" style="gap:6px;flex-wrap:nowrap">
              <form method="post" action="/ops/delivery/driver-active">
                <input type="hidden" name="repartidor_id" value="{esc(d.get('id'))}">
                <input type="hidden" name="activo" value="{next_active}">
                <button class="{action_class}" type="submit">{action_label}</button>
              </form>
              <form method="post" action="/ops/delivery/driver-delete" onsubmit="return confirm('¿Eliminar definitivamente al repartidor {esc(d.get('codigo'))} - {esc(d.get('nombre'))}? Si tiene asignaciones activas, primero cancélalas.');">
                <input type="hidden" name="repartidor_id" value="{esc(d.get('id'))}">
                <button class="button danger" type="submit">Eliminar</button>
              </form>
            </div>
          </td>
        </tr>
        """
    if not driver_rows:
        driver_rows = '<tr><td colspan="5" class="tiny">No hay repartidores configurados.</td></tr>'

    assignment_rows = ""
    for a in assignments:
        lat = a.get("driver_latitude")
        lon = a.get("driver_longitude")
        location = "—"
        if lat is not None and lon is not None:
            location = f'<a href="https://www.google.com/maps?q={esc(lat)},{esc(lon)}" target="_blank">Maps</a>'
        assignment_rows += f"""
        <tr>
          <td><strong>{esc(a.get('pedido_num'))}</strong><br><span class="tiny">{esc(a.get('pedido_estado'))}</span></td>
          <td>{esc(a.get('repartidor_nombre') or '—')}<br><span class="tiny">{esc(a.get('repartidor_whatsapp') or '')}</span></td>
          <td>{badge_html(str(a.get('status') or ''))}</td>
          <td>{money(a.get('fee'))}</td>
          <td>{location}</td>
          <td>
            <form method="post" action="/ops/delivery/assignment-cancel" onsubmit="return confirm('¿Cancelar asignación de {esc(a.get('pedido_num'))}?');">
              <input type="hidden" name="assignment_id" value="{esc(a.get('id'))}">
              <button class="button danger" type="submit">Cancelar</button>
            </form>
          </td>
        </tr>
        """
    if not assignment_rows:
        assignment_rows = '<tr><td colspan="6" class="tiny">No hay asignaciones activas.</td></tr>'

    next_order = max([int(d.get("orden_turno") or 0) for d in drivers] or [0]) + 1
    return f"""
    {render_delivery_account_panel(drivers, completed, payouts)}

    <div class="panel priority-green">
      <div class="panel-head"><h2>Control de repartidores</h2><div class="panel-sub">Agrega repartidores autorizados, activa/pausa permisos y limpia asignaciones trabadas.</div></div>

      <div class="panel" style="margin-bottom:16px;background:rgba(255,255,255,.04)">
        <div class="panel-head"><h3>Agregar nuevo repartidor autorizado</h3><div class="panel-sub">El WhatsApp debe ir con código de país, sin +. Si queda Activo, podrá recibir y responder deliveries automáticamente.</div></div>
        <form method="post" action="/ops/delivery/driver-create" class="actions" style="align-items:end;gap:10px;flex-wrap:wrap">
          <label class="tiny">Código<br><input name="codigo" placeholder="R004" required style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff"></label>
          <label class="tiny">Nombre<br><input name="nombre" placeholder="Nombre del repartidor" required style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;min-width:220px"></label>
          <label class="tiny">WhatsApp<br><input name="whatsapp_number" placeholder="51999999999" required inputmode="numeric" style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff"></label>
          <label class="tiny">Turno<br><input name="orden_turno" type="number" value="{next_order}" min="0" style="padding:11px;border-radius:12px;border:1px solid rgba(255,255,255,.14);background:#0f172a;color:#fff;width:90px"></label>
          <label class="tiny" style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><input type="checkbox" name="activo" value="true" checked> Autorizado / activo</label>
          <button class="button good" type="submit">Agregar repartidor</button>
        </form>
      </div>

      <div class="grid-cards" style="grid-template-columns:1fr 1fr; align-items:start">
        <div>
          <h3>Repartidores</h3>
          <div class="table-wrap"><table><thead><tr><th>Código</th><th>Nombre</th><th>WhatsApp</th><th>Estado</th><th>Acción</th></tr></thead><tbody>{driver_rows}</tbody></table></div>
        </div>
        <div>
          <h3>Asignaciones activas</h3>
          <div class="table-wrap"><table><thead><tr><th>Pedido</th><th>Repartidor</th><th>Estado</th><th>Fee</th><th>Ubic.</th><th>Acción</th></tr></thead><tbody>{assignment_rows}</tbody></table></div>
        </div>
      </div>
    </div>
    """


def fetch_delivery_assignment(pedido_id: Any) -> Dict[str, Any] | None:
    if pedido_id is None:
        return None
    try:
        rows = pg_get(
            f"/v_delivery_asignaciones?pedido_id=eq.{pedido_id}"
            "&status=in.(ASSIGNED,ACCEPTED,COMPLETED,OFFERED)"
            "&order=created_at.desc&limit=1"
        )
        return rows[0] if rows else None
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        body = exc.response.text if exc.response is not None else ""
        if status in {404, 406} or "v_delivery_asignaciones" in body:
            return None
        raise




def parse_coord(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_google_duration_seconds(value: Any) -> int | None:
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        return max(0, int(float(value[:-1])))
    except (TypeError, ValueError):
        return None


def format_duration_es(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, mins = divmod(minutes, 60)
    return f"{hours} h {mins} min" if mins else f"{hours} h"


def format_distance_km(meters: Any) -> str:
    try:
        meters_float = float(meters)
    except (TypeError, ValueError):
        return "—"
    if meters_float < 1000:
        return f"{round(meters_float)} m"
    return f"{meters_float / 1000:.1f} km"


def compute_google_route(driver_lat: float, driver_lon: float, customer_lat: float, customer_lon: float) -> Dict[str, Any]:
    if not GOOGLE_ROUTES_API_KEY:
        raise HTTPException(status_code=503, detail="GOOGLE_ROUTES_API_KEY is not configured")
    payload = {
        "origin": {"location": {"latLng": {"latitude": driver_lat, "longitude": driver_lon}}},
        "destination": {"location": {"latLng": {"latitude": customer_lat, "longitude": customer_lon}}},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": False,
        "languageCode": "es-419",
        "units": "METRIC",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_ROUTES_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
    }
    try:
        response = requests.post(GOOGLE_ROUTES_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"Google Routes API error: {detail}")
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Google Routes API unavailable: {exc}")

    route = (data.get("routes") or [{}])[0]
    seconds = parse_google_duration_seconds(route.get("duration"))
    distance_meters = route.get("distanceMeters")
    encoded_polyline = ((route.get("polyline") or {}).get("encodedPolyline")) or ""
    return {
        "ok": bool(route),
        "eta": format_duration_es(seconds),
        "duration_seconds": seconds,
        "distance": format_distance_km(distance_meters),
        "distance_meters": distance_meters,
        "encoded_polyline": encoded_polyline,
        "status": "Ruta calculada con Google Routes API" if route else "Google no devolvió ruta",
    }


def render_delivery_map_panel(order: Dict[str, Any], assignment: Dict[str, Any] | None = None, title: str = "Mapa de delivery", token: str = "") -> str:
    customer_lat = parse_coord(order.get("latitud"))
    customer_lon = parse_coord(order.get("longitud"))
    driver_lat = parse_coord((assignment or {}).get("driver_latitude"))
    driver_lon = parse_coord((assignment or {}).get("driver_longitude"))
    maps_url = order.get("maps_url") or (
        f"https://www.google.com/maps?q={customer_lat},{customer_lon}"
        if customer_lat is not None and customer_lon is not None
        else ""
    )
    driver_maps_url = (
        f"https://www.google.com/maps?q={driver_lat},{driver_lon}"
        if driver_lat is not None and driver_lon is not None
        else ""
    )
    route_url = ""
    if customer_lat is not None and customer_lon is not None and driver_lat is not None and driver_lon is not None:
        route_url = (
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={driver_lat},{driver_lon}"
            f"&destination={customer_lat},{customer_lon}"
            "&travelmode=driving"
        )

    if customer_lat is None or customer_lon is None:
        return f"""
        <div class="panel priority-orange" id="mapa">
          <div class="panel-head"><h2>{esc(title)}</h2><div class="panel-sub">Sin coordenadas del cliente todavía.</div></div>
          <p class="muted">Para ver el mapa, el cliente debe compartir ubicación de WhatsApp o el pedido debe tener latitud/longitud.</p>
          <div class="actions">{f'<a class="button secondary" href="{esc(maps_url)}" target="_blank">Abrir Maps</a>' if maps_url else ''}</div>
        </div>
        """

    customer = {"lat": customer_lat, "lng": customer_lon}
    driver = {"lat": driver_lat, "lng": driver_lon} if driver_lat is not None and driver_lon is not None else None
    map_id = f"delivery-map-{esc(order.get('pedido_num') or order.get('id') or 'order')}"
    map_dom_id = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in map_id)
    customer_json = json.dumps(customer)
    driver_json = json.dumps(driver)
    eta_dom_id = f"{map_dom_id}-eta"
    distance_dom_id = f"{map_dom_id}-distance"
    route_status_dom_id = f"{map_dom_id}-route-status"
    has_key = bool(GOOGLE_MAPS_API_KEY)
    script_src = f"https://maps.googleapis.com/maps/api/js?key={quote(GOOGLE_MAPS_API_KEY, safe='')}&callback=init_{map_dom_id.replace('-', '_')}&libraries=routes" if has_key else ""
    callback_name = f"init_{map_dom_id.replace('-', '_')}"
    route_api_url = (
        f"/api/route/{quote(str(order.get('pedido_num') or ''), safe='')}?token={quote(token, safe='')}"
        if driver and token and order.get("pedido_num")
        else ""
    )
    buttons = []
    if maps_url:
        buttons.append(f'<a class="button secondary" href="{esc(maps_url)}" target="_blank">Casa cliente</a>')
    if driver_maps_url:
        buttons.append(f'<a class="button secondary" href="{esc(driver_maps_url)}" target="_blank">Repartidor</a>')
    if route_url:
        buttons.append(f'<a class="button good" href="{esc(route_url)}" target="_blank">Ruta en Google Maps</a>')
    buttons_html = "".join(buttons)

    if not has_key:
        embed_url = (
            "https://maps.google.com/maps?"
            f"saddr={driver_lat},{driver_lon}&daddr={customer_lat},{customer_lon}&output=embed"
            if driver else
            f"https://maps.google.com/maps?q={customer_lat},{customer_lon}&z=16&output=embed"
        )
        return f"""
        <div class="panel priority-green" id="mapa">
          <div class="panel-head"><h2>{esc(title)}</h2><div class="panel-sub">Dirección de entrega + repartidor en tiempo real por última ubicación.</div></div>
          <iframe src="{esc(embed_url)}" width="100%" height="420" style="border:0;border-radius:20px;background:#0f172a" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
          <div class="actions" style="margin-top:12px">{buttons_html}</div>
          <p class="muted" style="margin-top:8px">Modo sin API key: usa iframe de Google Maps. Con <span class="mono">GOOGLE_MAPS_API_KEY</span> activamos pins custom, ruta dibujada y ETA.</p>
        </div>
        """

    driver_note = "Repartidor con última ubicación recibida." if driver else "Aún sin ubicación del repartidor; se muestra solo la casa del cliente."
    eta_initial = "Calculando…" if driver else "Sin ubicación"
    distance_initial = "Calculando…" if driver else "—"
    status_initial = "Calculando con Google Routes API" if driver else "Esperando ubicación del repartidor"
    return f"""
    <div class="panel priority-green" id="mapa">
      <div class="panel-head"><h2>{esc(title)}</h2><div class="panel-sub">Dirección de entrega + repartidor en tiempo real por última ubicación.</div></div>
      <div class="grid-cards" style="grid-template-columns: repeat(3, minmax(0, 1fr)); margin-bottom:14px;">
        <div class="summary-card"><div class="k">ETA estimado</div><div class="v" id="{eta_dom_id}" style="font-size:22px">{eta_initial}</div></div>
        <div class="summary-card"><div class="k">Distancia ruta</div><div class="v" id="{distance_dom_id}" style="font-size:22px">{distance_initial}</div></div>
        <div class="summary-card"><div class="k">Ruta</div><div class="v" id="{route_status_dom_id}" style="font-size:16px">{status_initial}</div></div>
      </div>
      <div id="{map_dom_id}" style="height:420px;border-radius:20px;overflow:hidden;border:1px solid rgba(255,255,255,.12);background:#0f172a"></div>
      <div class="actions" style="margin-top:12px">{buttons_html}</div>
      <div class="muted" style="margin-top:8px">{esc(driver_note)}</div>
      <script>
        window.{callback_name} = function() {{
          const customer = {customer_json};
          const driver = {driver_json};
          const map = new google.maps.Map(document.getElementById({json.dumps(map_dom_id)}), {{
            center: driver || customer,
            zoom: driver ? 14 : 16,
            mapTypeControl: false,
            streetViewControl: false,
            fullscreenControl: true
          }});
          new google.maps.Marker({{ position: customer, map, label: "C", title: "Casa del cliente" }});
          const bounds = new google.maps.LatLngBounds();
          bounds.extend(customer);
          if (driver) {{
            new google.maps.Marker({{ position: driver, map, label: "R", title: "Repartidor" }});
            bounds.extend(driver);
            map.fitBounds(bounds, 80);
            const etaEl = document.getElementById({json.dumps(eta_dom_id)});
            const distanceEl = document.getElementById({json.dumps(distance_dom_id)});
            const routeStatusEl = document.getElementById({json.dumps(route_status_dom_id)});
            function decodePolyline(encoded) {{
              const points = [];
              let index = 0, lat = 0, lng = 0;
              while (index < encoded.length) {{
                let b, shift = 0, result = 0;
                do {{ b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; }} while (b >= 0x20);
                lat += (result & 1) ? ~(result >> 1) : (result >> 1);
                shift = 0; result = 0;
                do {{ b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; }} while (b >= 0x20);
                lng += (result & 1) ? ~(result >> 1) : (result >> 1);
                points.push({{ lat: lat / 1e5, lng: lng / 1e5 }});
              }}
              return points;
            }}
            const routeApiUrl = {json.dumps(route_api_url)};
            if (!routeApiUrl) {{
              etaEl.textContent = "—";
              distanceEl.textContent = "—";
              routeStatusEl.textContent = "Sin endpoint de ruta";
              return;
            }}
            fetch(routeApiUrl, {{ cache: "no-store" }})
              .then((response) => response.ok ? response.json() : Promise.reject(response))
              .then((route) => {{
                etaEl.textContent = route.eta || "—";
                distanceEl.textContent = route.distance || "—";
                routeStatusEl.textContent = route.status || "Ruta calculada";
                if (route.encoded_polyline) {{
                  const path = decodePolyline(route.encoded_polyline);
                  const polyline = new google.maps.Polyline({{
                    path,
                    map,
                    strokeColor: "#22c55e",
                    strokeOpacity: 0.95,
                    strokeWeight: 5
                  }});
                  const routeBounds = new google.maps.LatLngBounds();
                  path.forEach((point) => routeBounds.extend(point));
                  if (!routeBounds.isEmpty()) map.fitBounds(routeBounds, 80);
                }}
              }})
              .catch(() => {{
                etaEl.textContent = "—";
                distanceEl.textContent = "—";
                routeStatusEl.textContent = "No se pudo calcular con Routes API";
              }});
          }}
        }};
      </script>
      <script async defer src="{esc(script_src)}"></script>
    </div>
    """


def render_driver_assignment_panel(pedido_id: Any, token: str = "") -> str:
    assignment = fetch_delivery_assignment(pedido_id)
    if not assignment:
        return """
        <div class="panel priority-orange">
          <div class="panel-head"><h2>Repartidor</h2><div class="panel-sub">Aún sin asignación visible.</div></div>
          <p class="muted">Cuando el módulo de repartidores esté activo, aquí aparecerá el repartidor ofrecido/asignado y su última ubicación.</p>
        </div>
        """
    lat = assignment.get("driver_latitude")
    lon = assignment.get("driver_longitude")
    location_html = '<span class="muted">Sin ubicación todavía</span>'
    if lat is not None and lon is not None:
        driver_maps = f"https://www.google.com/maps?q={lat},{lon}"
        location_html = f'<a class="button good" href="{esc(driver_maps)}" target="_blank">Abrir ubicación</a>'
    return f"""
    <div class="panel priority-green">
      <div class="panel-head"><h2>Repartidor</h2><div class="panel-sub">Asignación de delivery</div></div>
      <div class="grid-cards" style="grid-template-columns: repeat(4, minmax(0, 1fr));">
        <div class="summary-card"><div class="k">Nombre</div><div class="v" style="font-size:18px">{esc(assignment.get('repartidor_nombre') or '—')}</div></div>
        <div class="summary-card"><div class="k">Código</div><div class="v" style="font-size:18px">{esc(assignment.get('repartidor_codigo') or '—')}</div></div>
        <div class="summary-card"><div class="k">Estado</div><div class="v" style="font-size:18px">{esc(assignment.get('status') or '—')}</div></div>
        <div class="summary-card"><div class="k">Pago carrera</div><div class="v" style="font-size:18px">{money(assignment.get('fee'))}</div></div>
      </div>
      <div class="actions" style="margin-top:12px">{location_html}</div>
      <div class="muted" style="margin-top:8px">Última ubicación: {esc(assignment.get('driver_location_at') or 'pendiente')}</div>
    </div>
    """


def render_customer_tracking_progress(order: Dict[str, Any], assignment: Dict[str, Any] | None) -> str:
    estado = str(order.get("estado") or "").upper()
    assignment_status = str((assignment or {}).get("status") or "").upper()
    delivered = estado == "ENTREGADO" or assignment_status == "COMPLETED"
    on_way = estado == "DESPACHADO" or assignment_status in {"ACCEPTED", "ASSIGNED", "COMPLETED"}
    preparing = estado in {"EN_PREPARACION", "DESPACHADO", "ENTREGADO"} or on_way
    confirmed = estado in {"CONFIRMADO", "EN_PREPARACION", "DESPACHADO", "ENTREGADO"} or bool(estado)
    steps = [
        ("Confirmado", confirmed),
        ("En preparación", preparing),
        ("En camino", on_way),
        ("Entregado", delivered),
    ]
    html_steps = "".join(
        f'<div class="track-step {"done" if done else ""}"><span></span><strong>{esc(label)}</strong></div>'
        for label, done in steps
    )
    return f"""
      <div class="panel">
        <div class="panel-head"><h2>Estado del pedido</h2><div class="panel-sub">Actualización automática cada 20 segundos</div></div>
        <style>
          .track-progress {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:10px; }}
          .track-step {{ min-height:88px; border:1px solid var(--line); border-radius:18px; padding:14px; background:#020617; color:#94a3b8; }}
          .track-step span {{ display:block; width:18px; height:18px; border-radius:999px; border:2px solid #475569; margin-bottom:10px; }}
          .track-step strong {{ display:block; font-size:15px; line-height:1.25; }}
          .track-step.done {{ background:rgba(34,197,94,.12); border-color:rgba(34,197,94,.38); color:#dcfce7; }}
          .track-step.done span {{ background:#22c55e; border-color:#22c55e; box-shadow:0 0 0 5px rgba(34,197,94,.14); }}
          @media (max-width: 640px) {{ .track-progress {{ grid-template-columns:1fr 1fr; }} }}
        </style>
        <div class="track-progress">{html_steps}</div>
      </div>
    """


def render_tracking_page(data: Dict[str, Any], token: str) -> str:
    order = data["order"]
    items = data.get("items", [])
    pedido_num = esc(order.get("pedido_num"))
    assignment = fetch_delivery_assignment(order.get("id"))
    cliente = esc(order.get("cliente_nombre"))
    destino = esc(order.get("direccion_confirmada") or order.get("direccion_detectada") or "")
    maps_url = order.get("maps_url")
    destino_link = f'<a class="button secondary" href="{esc(maps_url)}" target="_blank">Ver destino</a>' if maps_url else ""

    if assignment:
        driver_name = esc(assignment.get("repartidor_nombre") or "Repartidor")
        driver_code = esc(assignment.get("repartidor_codigo") or "")
        status = esc(assignment.get("status") or "")
        last_at = esc(assignment.get("driver_location_at") or "Sin ubicación todavía")
        lat = assignment.get("driver_latitude")
        lon = assignment.get("driver_longitude")
        if lat is not None and lon is not None:
            driver_maps = f"https://www.google.com/maps?q={lat},{lon}"
            location_panel = f"""
            <div class="panel priority-green">
              <div class="panel-head"><h2>Última ubicación del repartidor</h2><div class="panel-sub">Actualizado: {last_at}</div></div>
              <p class="delivery-address">Lat/Lon: {esc(lat)}, {esc(lon)}</p>
              <div class="actions"><a class="button good" href="{esc(driver_maps)}" target="_blank">Abrir ubicación del repartidor</a>{destino_link}</div>
            </div>
            """
        else:
            location_panel = f"""
            <div class="panel priority-orange">
              <div class="panel-head"><h2>Esperando ubicación</h2></div>
              <p>El pedido ya tiene repartidor, pero todavía no ha compartido ubicación.</p>
              <div class="actions">{destino_link}</div>
            </div>
            """
        assignment_panel = f"""
        <div class="panel">
          <div class="grid-cards" style="grid-template-columns: repeat(3, minmax(0, 1fr));">
            <div class="summary-card"><div class="k">Repartidor</div><div class="v" style="font-size:18px">{driver_name}</div></div>
            <div class="summary-card"><div class="k">Código</div><div class="v" style="font-size:18px">{driver_code}</div></div>
            <div class="summary-card"><div class="k">Estado</div><div class="v" style="font-size:18px">{status}</div></div>
          </div>
        </div>
        {location_panel}
        """
    else:
        assignment_panel = f"""
        <div class="panel priority-orange">
          <div class="panel-head"><h2>Tracking todavía no disponible</h2></div>
          <p>El pedido aún no tiene repartidor asignado o el módulo de repartidores todavía no está activado.</p>
          <div class="actions">{destino_link}</div>
        </div>
        """

    body = f"""
    <div class="page">
      <div class="topbar">
        <div>
          <h1>Tracking {pedido_num}</h1>
          <div class="muted">Cliente: {cliente}. Esta página se actualiza automáticamente.</div>
        </div>
        <div class="actions">{badge_html(order.get("estado"))}</div>
      </div>

      {render_customer_tracking_progress(order, assignment)}

      <div class="panel">
        <div class="grid-cards" style="grid-template-columns: repeat(3, minmax(0, 1fr));">
          <div class="summary-card"><div class="k">Pedido</div><div class="v" style="font-size:18px">{pedido_num}</div></div>
          <div class="summary-card"><div class="k">Total</div><div class="v" style="font-size:18px">{money(order.get('total'))}</div></div>
          <div class="summary-card"><div class="k">Items</div><div class="v" style="font-size:18px">{len(items)}</div></div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head"><h2>Destino</h2></div>
        <p class="delivery-address">{destino}</p>
        <div class="actions">{destino_link}</div>
      </div>

      {render_delivery_map_panel(order, assignment, 'Mapa de tracking', token)}

      {assignment_panel}
    </div>
    """
    return render_layout(f"Tracking {pedido_num} - Replau", body, auto_refresh_seconds=20)


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
        return {
            "ok": True,
            "postgrest_ok": True,
            "postgrest_base_url": POSTGREST_BASE_URL,
            "google_maps_enabled": bool(GOOGLE_MAPS_API_KEY),
            "google_routes_enabled": bool(GOOGLE_ROUTES_API_KEY),
        }
    except Exception as exc:
        return {
            "ok": False,
            "postgrest_ok": False,
            "google_maps_enabled": bool(GOOGLE_MAPS_API_KEY),
            "google_routes_enabled": bool(GOOGLE_ROUTES_API_KEY),
            "error": str(exc),
        }


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


@app.post("/email-log/{email_id}/clear")
def clear_email_log(email_id: int) -> RedirectResponse:
    cleared = load_cleared_email_ids()
    cleared.add(email_id)
    save_cleared_email_ids(cleared)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/email-logs/clear-all")
def clear_all_email_logs(
    view: str = Form("all"),
    search: str = Form(""),
    order_status: str = Form("all"),
    conv_status: str = Form("all"),
    email_status: str = Form("all"),
    next_url: str = Form("/dashboard"),
) -> RedirectResponse:
    data = fetch_dashboard_data()
    filtered = filter_dashboard_data(data, view, search, order_status, conv_status, email_status)
    visible_ids = {
        int(row.get("id"))
        for row in filtered["email_logs"]
        if row.get("id") is not None
    }
    if visible_ids:
        cleared = load_cleared_email_ids()
        cleared.update(visible_ids)
        save_cleared_email_ids(cleared)
    target = next_url if next_url.startswith("/") else "/dashboard"
    return RedirectResponse(url=target, status_code=303)


@app.post("/conversation/{whatsapp_number}/clear")
def clear_conversation(whatsapp_number: str, next_url: str = Form("/dashboard?view=conversations")) -> RedirectResponse:
    phone = clean_phone_digits(whatsapp_number)
    if not phone:
        raise HTTPException(status_code=400, detail="WhatsApp inválido")
    rows = pg_patch(
        f"/whatsapp_conversaciones?whatsapp_number=eq.{quote(phone, safe='')}",
        {"estado": "CANCELLED", "pedido_borrador": None},
    )
    if rows == []:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    target = next_url if next_url.startswith("/") else "/dashboard?view=conversations"
    return RedirectResponse(url=target, status_code=303)


@app.post("/conversations/clear-all")
def clear_all_conversations(next_url: str = Form("/dashboard?view=conversations")) -> RedirectResponse:
    pg_patch(
        "/whatsapp_conversaciones?estado=not.in.(CONFIRMED,ANULADO,CANCELLED)",
        {"estado": "CANCELLED", "pedido_borrador": None},
    )
    target = next_url if next_url.startswith("/") else "/dashboard?view=conversations"
    return RedirectResponse(url=target, status_code=303)


@app.post("/handoff/start")
def start_handoff(
    whatsapp_number: str = Form(...),
    reason: str = Form(""),
    next_url: str = Form("/dashboard"),
) -> RedirectResponse:
    phone = clean_phone_digits(whatsapp_number)
    if not phone:
        raise HTTPException(status_code=400, detail="WhatsApp inválido")
    with locked_human_handoffs():
        entries = load_human_handoffs()
        entries[phone] = {
            "active": True,
            "reason": reason.strip() or "Atención manual",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": "logistics-dashboard",
        }
        save_human_handoffs(entries)
    target = next_url if next_url.startswith("/") else "/dashboard"
    return RedirectResponse(url=target, status_code=303)


@app.post("/handoff/resume")
def resume_handoff(
    whatsapp_number: str = Form(...),
    next_url: str = Form("/dashboard"),
) -> RedirectResponse:
    phone = clean_phone_digits(whatsapp_number)
    if not phone:
        raise HTTPException(status_code=400, detail="WhatsApp inválido")
    with locked_human_handoffs():
        entries = load_human_handoffs()
        entries.pop(phone, None)
        save_human_handoffs(entries)
    target = next_url if next_url.startswith("/") else "/dashboard"
    return RedirectResponse(url=target, status_code=303)


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


@app.get("/api/route/{pedido_num}")
def api_route(pedido_num: str, token: str = Query(...)) -> JSONResponse:
    try:
        data = fetch_public_order(pedido_num, token)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    order = data.get("order") or {}
    assignment = fetch_delivery_assignment(order.get("id"))
    customer_lat = parse_coord(order.get("latitud"))
    customer_lon = parse_coord(order.get("longitud"))
    driver_lat = parse_coord((assignment or {}).get("driver_latitude"))
    driver_lon = parse_coord((assignment or {}).get("driver_longitude"))
    if customer_lat is None or customer_lon is None:
        raise HTTPException(status_code=400, detail="El pedido no tiene coordenadas de cliente")
    if driver_lat is None or driver_lon is None:
        raise HTTPException(status_code=400, detail="La asignación no tiene ubicación de repartidor")
    return JSONResponse(compute_google_route(driver_lat, driver_lon, customer_lat, customer_lon))


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




@app.get("/track/{pedido_num}", response_class=HTMLResponse)
def track_page(pedido_num: str, token: str = Query(...)) -> HTMLResponse:
    try:
        data = fetch_public_order(pedido_num, token)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    except HTTPException as exc:
        return HTMLResponse(
            render_layout(
                "Tracking no disponible",
                '<div class="page"><div class="panel"><h1>Link inválido o vencido</h1><p>No pude validar el token del pedido.</p></div></div>'
            ),
            status_code=exc.status_code,
        )
    return HTMLResponse(render_tracking_page(data, token))


@app.get("/ops/picking", response_class=HTMLResponse)
def picking_station_page(limit: int = Query(100, ge=1, le=250)) -> HTMLResponse:
    try:
        data = fetch_dashboard_data(limit=limit)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    return HTMLResponse(render_picking_station_page(data))


@app.get("/ops/picking/{pedido_num}", response_class=HTMLResponse)
def picking_page(pedido_num: str, token: str = Query(...)) -> HTMLResponse:
    try:
        data = fetch_public_order(pedido_num, token)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    return HTMLResponse(render_picking_page(data, token))


@app.get("/ops/delivery", response_class=HTMLResponse)
def delivery_station_page(limit: int = Query(100, ge=1, le=250)) -> HTMLResponse:
    try:
        data = fetch_dashboard_data(limit=limit)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    return HTMLResponse(render_delivery_station_page(data))


@app.get("/ops/delivery/{pedido_num}", response_class=HTMLResponse)
def delivery_page(pedido_num: str, token: str = Query(...)) -> HTMLResponse:
    try:
        data = fetch_public_order(pedido_num, token)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=500, detail=exc.response.text)
    return HTMLResponse(render_delivery_page(data, token))


@app.post("/ops/delivery/offer-next")
def delivery_offer_next(pedido_num: str = Form(...)) -> RedirectResponse:
    rows = pg_get(f"/v_pedidos_logistica?pedido_num=eq.{quote(pedido_num, safe='')}&select=id&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Pedido not found")
    data = pg_rpc("ofrecer_delivery_a_siguiente_repartidor", {"p_pedido_id": rows[0]["id"]})
    if not data.get("ok"):
        raise HTTPException(status_code=409, detail=data)
    return RedirectResponse(url="/ops/delivery", status_code=303)


@app.post("/ops/delivery/assign-driver")
def delivery_assign_driver(pedido_num: str = Form(...), repartidor_id: int = Form(...)) -> RedirectResponse:
    orders = pg_get(
        f"/v_pedidos_logistica?pedido_num=eq.{quote(pedido_num, safe='')}"
        "&select=id,pedido_num,direccion_confirmada,direccion_detectada,maps_url&limit=1"
    )
    if not orders:
        raise HTTPException(status_code=404, detail="Pedido not found")
    order = orders[0]
    drivers = pg_get(f"/repartidores?id=eq.{repartidor_id}&activo=eq.true&limit=1")
    if not drivers:
        raise HTTPException(status_code=404, detail="Repartidor activo no encontrado")
    driver = drivers[0]
    pedido_id = order["id"]
    now = utc_now_iso()

    active = pg_get(
        f"/delivery_asignaciones?pedido_id=eq.{pedido_id}"
        "&status=in.(OFFERED,ACCEPTED,ASSIGNED)&select=id,notes"
    )
    for assignment in active:
        notes = delivery_assignment_notes(
            assignment.get("notes"),
            f"Cancelado por reasignacion directa a {driver.get('codigo')} desde Dispatch Board",
        )
        pg_patch(f"/delivery_asignaciones?id=eq.{assignment['id']}", {"status": "CANCELLED", "notes": notes})

    fee_rows = pg_get("/delivery_config?key=eq.driver_fee_pen&select=value&limit=1")
    fee = money_value((fee_rows[0] if fee_rows else {}).get("value") or 7)
    created = pg_post(
        "/delivery_asignaciones",
        {
            "pedido_id": pedido_id,
            "repartidor_id": repartidor_id,
            "status": "ASSIGNED",
            "fee": fee,
            "responded_at": now,
            "assigned_at": now,
            "response_text": "ASIGNADO_DESDE_DISPATCH",
            "notes": "Asignado directamente desde Dispatch Board",
        },
    )

    address = order.get("direccion_confirmada") or order.get("direccion_detectada") or "(sin direccion)"
    maps_line = f"\nMapa: {order.get('maps_url')}\n" if order.get("maps_url") else "\n"
    message = (
        "Pedido asignado desde Dispatch Board\n\n"
        f"Pedido: {order.get('pedido_num')}\n"
        f"Direccion:\n{address}\n"
        f"{maps_line}"
        f"Pago carrera: S/ {fee:.2f}\n\n"
        "Comparte ubicacion por WhatsApp y responde SALI cuando estes en camino."
    )
    try:
        pg_post(
            "/whatsapp_outbox",
            {
                "pedido_id": pedido_id,
                "whatsapp_number": driver.get("whatsapp_number"),
                "message_text": message,
                "event_type": "CUSTOM",
                "status": "PENDING",
            },
        )
    except Exception:
        # The board assignment is the source of truth; outbox notification is best effort.
        pass

    if not created:
        raise HTTPException(status_code=500, detail="No se pudo crear la asignacion")
    return RedirectResponse(url="/ops/delivery#lane-assigned", status_code=303)


@app.post("/ops/delivery/assignment-cancel")
def delivery_assignment_cancel(assignment_id: int = Form(...)) -> RedirectResponse:
    rows = pg_get(f"/delivery_asignaciones?id=eq.{assignment_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Assignment not found")
    notes = delivery_assignment_notes(rows[0].get("notes"), "Cancelado manualmente desde Delivery Station")
    pg_patch(f"/delivery_asignaciones?id=eq.{assignment_id}", {"status": "CANCELLED", "notes": notes})
    return RedirectResponse(url="/ops/delivery", status_code=303)


@app.post("/ops/delivery/payout-create")
def delivery_payout_create(repartidor_id: int = Form(...)) -> RedirectResponse:
    drivers = pg_get(f"/repartidores?id=eq.{repartidor_id}&limit=1")
    if not drivers:
        raise HTTPException(status_code=404, detail="Repartidor no encontrado")
    driver = drivers[0]
    completed = fetch_completed_delivery_assignments()

    def create_batch(payouts: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        rows = delivery_account_rows(drivers, completed, payouts)
        unpaid = rows[0]["unpaid_assignments"] if rows else []
        if not unpaid:
            raise HTTPException(status_code=409, detail="No hay rutas completadas pendientes para liquidar")

        batch_id = int(payouts.get("next_id") or 1)
        assignment_ids = []
        for assignment in unpaid:
            try:
                assignment_ids.append(int(assignment.get("id")))
            except Exception:
                continue
        if not assignment_ids:
            raise HTTPException(status_code=409, detail="No hay rutas válidas pendientes para liquidar")
        total = sum(money_value(a.get("fee")) for a in unpaid)
        completed_dates = [str(a.get("completed_at") or "") for a in unpaid if a.get("completed_at")]
        batch = {
            "id": batch_id,
            "repartidor_id": repartidor_id,
            "repartidor_codigo": driver.get("codigo"),
            "repartidor_nombre": driver.get("nombre"),
            "status": "OPEN",
            "assignment_ids": assignment_ids,
            "route_count": len(assignment_ids),
            "total_amount": round(total, 2),
            "period_start": min(completed_dates) if completed_dates else None,
            "period_end": max(completed_dates) if completed_dates else None,
            "created_at": utc_now_iso(),
            "paid_at": None,
            "payment_method": None,
            "payment_reference": None,
            "notes": None,
        }
        payouts["next_id"] = batch_id + 1
        payouts.setdefault("batches", []).append(batch)
        return payouts, batch

    update_delivery_payouts(create_batch)
    return RedirectResponse(url="/ops/delivery", status_code=303)


@app.post("/ops/delivery/payout-paid")
def delivery_payout_paid(
    batch_id: int = Form(...),
    payment_method: str = Form(""),
    payment_reference: str = Form(""),
) -> RedirectResponse:
    def mark_paid(payouts: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        for batch in payouts.get("batches", []):
            try:
                current_id = int(batch.get("id"))
            except Exception:
                continue
            if current_id != batch_id:
                continue
            if str(batch.get("status") or "OPEN").upper() == "CANCELLED":
                raise HTTPException(status_code=409, detail="La liquidación está cancelada")
            batch["status"] = "PAID"
            batch["paid_at"] = batch.get("paid_at") or utc_now_iso()
            batch["payment_method"] = payment_method.strip() or batch.get("payment_method") or None
            batch["payment_reference"] = payment_reference.strip() or batch.get("payment_reference") or None
            batch["updated_at"] = utc_now_iso()
            return payouts, True
        raise HTTPException(status_code=404, detail="Liquidación no encontrada")

    update_delivery_payouts(mark_paid)
    return RedirectResponse(url="/ops/delivery", status_code=303)


@app.post("/ops/delivery/driver-active")
def delivery_driver_active(repartidor_id: int = Form(...), activo: str = Form(...)) -> RedirectResponse:
    active_bool = str(activo).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}
    pg_patch(f"/repartidores?id=eq.{repartidor_id}", {"activo": active_bool})
    return RedirectResponse(url="/ops/delivery", status_code=303)


@app.post("/ops/delivery/driver-delete")
def delivery_driver_delete(repartidor_id: int = Form(...)) -> RedirectResponse:
    active = pg_get(
        f"/delivery_asignaciones?repartidor_id=eq.{repartidor_id}"
        "&status=in.(OFFERED,ACCEPTED,ASSIGNED)&select=id&limit=1"
    )
    if active:
        raise HTTPException(status_code=409, detail="El repartidor tiene asignaciones activas. Cancélalas antes de eliminarlo.")
    rows = pg_delete(f"/repartidores?id=eq.{repartidor_id}")
    if not rows:
        raise HTTPException(status_code=404, detail="Repartidor no encontrado")
    return RedirectResponse(url="/ops/delivery", status_code=303)


@app.post("/ops/delivery/driver-create")
def delivery_driver_create(
    codigo: str = Form(...),
    nombre: str = Form(...),
    whatsapp_number: str = Form(...),
    orden_turno: int = Form(0),
    activo: str = Form("false"),
) -> RedirectResponse:
    code = codigo.strip().upper()
    name = nombre.strip()
    phone = "".join(ch for ch in whatsapp_number.strip() if ch.isdigit())
    if not code or not name or not phone:
        raise HTTPException(status_code=400, detail="Código, nombre y WhatsApp son obligatorios")
    if len(phone) < 8:
        raise HTTPException(status_code=400, detail="WhatsApp inválido")
    active_bool = str(activo).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}
    try:
        pg_post(
            "/repartidores",
            {
                "codigo": code,
                "nombre": name,
                "whatsapp_number": phone,
                "activo": active_bool,
                "orden_turno": orden_turno,
            },
        )
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=409, detail=detail)
    return RedirectResponse(url="/ops/delivery", status_code=303)


@app.post("/ops/delivery/sucursal-create")
def delivery_sucursal_create(
    codigo: str = Form(...),
    nombre: str = Form(...),
    direccion: str = Form(...),
    telefono: str = Form(""),
    latitud: str = Form(""),
    longitud: str = Form(""),
    referencia: str = Form(""),
    activo: str = Form("false"),
) -> RedirectResponse:
    code = codigo.strip().upper()
    name = nombre.strip()
    address = direccion.strip()
    if not code or not name or not address:
        raise HTTPException(status_code=400, detail="Código, nombre y dirección son obligatorios")
    if not latitud.strip() or not longitud.strip():
        raise HTTPException(status_code=400, detail="Latitud y longitud son obligatorias para puntos de recojo")
    try:
        lat_value = float(latitud.strip())
        lon_value = float(longitud.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="Latitud/longitud inválidas")
    if not (-90 <= lat_value <= 90 and -180 <= lon_value <= 180):
        raise HTTPException(status_code=400, detail="Latitud/longitud fuera de rango")
    rows = [r for r in load_sucursales() if str(r.get("codigo") or "").upper() != code]
    active_bool = str(activo).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}
    row = {
        "codigo": code,
        "nombre": name,
        "direccion": address,
        "telefono": telefono.strip(),
        "latitud": f"{lat_value:.8f}",
        "longitud": f"{lon_value:.8f}",
        "referencia": referencia.strip(),
        "activo": active_bool,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    rows.append(row)
    save_sucursales(rows)
    return RedirectResponse(url="/ops/delivery", status_code=303)


@app.post("/ops/delivery/sucursal-active")
def delivery_sucursal_active(codigo: str = Form(...), activo: str = Form(...)) -> RedirectResponse:
    code = codigo.strip().upper()
    active_bool = str(activo).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}
    rows = load_sucursales()
    changed = False
    for row in rows:
        if str(row.get("codigo") or "").upper() == code:
            row["activo"] = active_bool
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
    if not changed:
        raise HTTPException(status_code=404, detail="Sucursal not found")
    save_sucursales(rows)
    return RedirectResponse(url="/ops/delivery", status_code=303)


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

    if estado == "ENTREGADO":
        try:
            rows = pg_get(f"/v_pedidos_logistica?pedido_num=eq.{quote(pedido_num, safe='')}&select=id&limit=1")
            if rows:
                assignments = pg_get(
                    f"/delivery_asignaciones?pedido_id=eq.{rows[0]['id']}"
                    "&status=in.(OFFERED,ACCEPTED,ASSIGNED)&order=created_at.desc&limit=1"
                )
                if assignments:
                    pg_patch(
                        f"/delivery_asignaciones?id=eq.{assignments[0]['id']}",
                        {"status": "COMPLETED"},
                    )
        except Exception:
            # Status update already succeeded; do not block the operator on optional assignment bookkeeping.
            pass

    if estado == "DESPACHADO" and (not next_url or "/ops/picking/" in next_url):
        # Once picking is prepared/ready, move the operator into the delivery flow.
        target = f"/ops/delivery/{pedido_num}?token={quote(token, safe='')}"
    elif estado == "ENTREGADO" and (not next_url or "/ops/delivery/" in next_url):
        target = f"/order/{pedido_num}?token={quote(token, safe='')}"
    else:
        target = next_url or f"/order/{pedido_num}?token={quote(token, safe='')}"
    return RedirectResponse(url=target, status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("logistics_viewer:app", host=VIEWER_HOST, port=VIEWER_PORT, reload=False)
