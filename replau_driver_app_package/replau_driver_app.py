#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8796"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))

REQUIRE_ADMIN_TOKEN = os.environ.get("REQUIRE_ADMIN_TOKEN", "false").lower() == "true"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
DRIVER_DOCUMENT_DIR = Path(
    os.environ.get("REPLAU_DRIVER_DOCUMENT_DIR", "/home/guill/.openclaw/workspace/replau_driver_documents")
).resolve()
MAX_UPLOAD_BYTES = int(os.environ.get("REPLAU_DRIVER_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024)))

app = FastAPI(title="Replau Driver App", version="0.1.0")
DRIVER_DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_phone(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def check_admin_auth(request: Request, x_admin_token: Optional[str] = None) -> None:
    if not REQUIRE_ADMIN_TOKEN:
        return
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Admin token is not configured")
    if request.query_params.get("token") == ADMIN_TOKEN or x_admin_token == ADMIN_TOKEN:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing admin token")


def token_query(request: Request) -> str:
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


def pg_url(path: str) -> str:
    return POSTGREST_BASE_URL + (path if path.startswith("/") else "/" + path)


def pg_get(path: str) -> Any:
    response = requests.get(pg_url(path), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def pg_post(path: str, payload: Dict[str, Any]) -> Any:
    response = requests.post(
        pg_url(path),
        json=payload,
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json() if response.text.strip() else []


def pg_patch(path: str, payload: Dict[str, Any]) -> Any:
    response = requests.patch(
        pg_url(path),
        json=payload,
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json() if response.text.strip() else []


def pg_rpc(name: str, payload: Dict[str, Any]) -> Any:
    response = requests.post(
        pg_url(f"/rpc/{name}"),
        json=payload,
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json() if response.text.strip() else {}


def save_or_update_consent(
    account_id: int,
    consent_type: str,
    version: str,
    accepted: bool,
    accepted_at: str,
    ip_address: str,
    user_agent: str,
) -> None:
    existing = pg_get(
        f"/driver_consents?driver_account_id=eq.{account_id}"
        f"&consent_type=eq.{quote(consent_type, safe='')}"
        f"&version=eq.{quote(version, safe='')}&select=id&limit=1"
    )
    payload = {
        "driver_account_id": account_id,
        "consent_type": consent_type,
        "version": version,
        "accepted": accepted,
        "accepted_at": accepted_at,
        "ip_address": ip_address,
        "user_agent": user_agent,
    }
    if existing:
        pg_patch(f"/driver_consents?id=eq.{existing[0]['id']}", payload)
    else:
        pg_post("/driver_consents", payload)


def layout(title: str, body: str, *, auth_query: str = "", flash: str = "") -> HTMLResponse:
    flash_html = f'<div class="flash">{esc(flash)}</div>' if flash else ""
    return HTMLResponse(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{esc(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ margin:0; font-family:"Segoe UI", Arial, sans-serif; background:#0f172a; color:#e5e7eb; }}
    a {{ color:#93c5fd; text-decoration:none; }}
    .wrap {{ max-width:1180px; margin:0 auto; padding:20px; }}
    .top {{ display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:16px; }}
    h1 {{ margin:0; font-size:30px; }}
    h2 {{ margin:0 0 10px; font-size:21px; }}
    .nav {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .nav a {{ border:1px solid #334155; background:#111827; border-radius:8px; padding:8px 10px; color:#e5e7eb; font-weight:700; }}
    .panel {{ border:1px solid #334155; background:#111827; border-radius:8px; padding:16px; margin:14px 0; }}
    .grid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:10px; }}
    .kpi {{ border:1px solid #334155; background:#0b1220; border-radius:8px; padding:12px; }}
    .kpi span {{ display:block; color:#94a3b8; font-size:12px; font-weight:800; text-transform:uppercase; }}
    .kpi strong {{ display:block; margin-top:5px; font-size:24px; }}
    label {{ display:block; margin:10px 0; color:#cbd5e1; font-weight:700; font-size:13px; }}
    input, select, textarea {{ width:100%; box-sizing:border-box; margin-top:5px; padding:11px; color:#e5e7eb; background:#020617; border:1px solid #334155; border-radius:8px; }}
    input[type=checkbox] {{ width:auto; margin:0 8px 0 0; }}
    textarea {{ min-height:84px; }}
    button, .button {{ display:inline-block; border:0; border-radius:8px; padding:10px 13px; background:#2563eb; color:white; font-weight:800; cursor:pointer; }}
    button.good, .button.good {{ background:#166534; }}
    button.bad, .button.bad {{ background:#991b1b; }}
    .muted {{ color:#94a3b8; }}
    .flash {{ border:1px solid #0e7490; background:#083344; color:#cffafe; border-radius:8px; padding:12px; margin:12px 0; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ border-bottom:1px solid #334155; padding:9px; text-align:left; vertical-align:top; }}
    th {{ color:#93c5fd; }}
    .badge {{ display:inline-block; border-radius:999px; padding:4px 8px; font-size:12px; font-weight:800; background:#334155; color:#e5e7eb; }}
    .APPROVED, .ACTIVE, .PASSED, .VERIFIED, .ASSIGNED, .ACCEPTED, .ONLINE {{ background:#14532d; color:#bbf7d0; }}
    .REJECTED, .SUSPENDED, .FAILED, .CANCELLED, .EXPIRED, .LOST {{ background:#7f1d1d; color:#fecaca; }}
    .CREATED, .CONSENTED, .SUBMITTED, .OFFERED, .VIEWED, .OPEN {{ background:#1e3a8a; color:#bfdbfe; }}
    .code {{ font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }}
    @media(max-width:860px) {{ .grid,.kpis {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h1>{esc(title)}</h1>
      <div class="nav">
        <a href="/driver">Driver</a>
        <a href="/ops/drivers{auth_query}">Driver Ops</a>
        <a href="/ops/driver-dispatch{auth_query}">Driver Dispatch</a>
        <a href="http://127.0.0.1:8790/ops/delivery">Delivery Station</a>
      </div>
    </div>
    {flash_html}
    {body}
  </div>
</body>
</html>""")


def badge(value: Any) -> str:
    text = str(value or "")
    return f'<span class="badge {esc(text)}">{esc(text or "UNKNOWN")}</span>'


def account_rows(status: str = "") -> List[Dict[str, Any]]:
    query = "/v_driver_accounts?order=created_at.desc&limit=100"
    if status:
        query += "&status=eq." + quote(status, safe="")
    try:
        return pg_get(query)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in {404, 406}:
            return []
        raise


def parse_optional_float(value: str) -> Optional[float]:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid number: {text}") from exc


@app.get("/health")
def health() -> JSONResponse:
    checks = {"ok": True, "postgrest": False, "driver_schema": False}
    try:
        pg_get("/")
        checks["postgrest"] = True
        pg_get("/v_driver_accounts?limit=1")
        checks["driver_schema"] = True
    except Exception as exc:
        checks["ok"] = False
        checks["error"] = str(exc)
    return JSONResponse(checks, status_code=200 if checks["ok"] else 503)


@app.get("/api/driver/health")
def api_driver_health() -> JSONResponse:
    return health()


@app.get("/driver", response_class=HTMLResponse)
def driver_home(flash: str = "") -> HTMLResponse:
    body = """
    <div class="grid">
      <section class="panel">
        <h2>Apply as a driver</h2>
        <form method="post" action="/driver/signup">
          <label>Phone with country code
            <input name="phone" inputmode="tel" placeholder="51999999999" required>
          </label>
          <label>Legal name
            <input name="legal_name" placeholder="Full name as shown on ID" required>
          </label>
          <label>Email
            <input name="email" type="email" placeholder="optional">
          </label>
          <button class="good" type="submit">Start application</button>
        </form>
      </section>
      <section class="panel">
        <h2>Check application</h2>
        <form method="get" action="/driver/status">
          <label>Phone
            <input name="phone" inputmode="tel" placeholder="51999999999" required>
          </label>
          <button type="submit">Check status</button>
        </form>
      </section>
    </div>
    <section class="panel">
      <h2>Phase 1 pilot scope</h2>
      <p class="muted">This app is the onboarding and verification surface. Real dispatch remains blocked until operations approves the driver and links the account to an authorized repartidor.</p>
    </section>
    """
    return layout("Replau Driver", body, flash=flash)


@app.post("/driver/signup")
def driver_signup(
    phone: str = Form(...),
    legal_name: str = Form(...),
    email: str = Form(""),
) -> RedirectResponse:
    clean = clean_phone(phone)
    if len(clean) < 8:
        raise HTTPException(status_code=400, detail="Invalid phone")
    payload = {
        "phone": clean,
        "legal_name": legal_name.strip(),
        "email": email.strip() or None,
        "status": "CREATED",
    }
    try:
        rows = pg_post("/driver_accounts", payload)
        account_id = rows[0]["id"]
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 409:
            rows = pg_get(f"/driver_accounts?phone=eq.{quote(clean, safe='')}&select=id&limit=1")
            if not rows:
                raise
            account_id = rows[0]["id"]
        else:
            raise
    return RedirectResponse(url=f"/driver/application/{account_id}", status_code=303)


@app.get("/driver/status", response_class=HTMLResponse)
def driver_status(phone: str) -> HTMLResponse:
    clean = clean_phone(phone)
    rows = pg_get(f"/v_driver_accounts?phone=eq.{quote(clean, safe='')}&limit=1")
    if not rows:
        return layout("Application not found", '<div class="panel"><p>No application found for that phone.</p><a class="button" href="/driver">Back</a></div>')
    return driver_application(int(rows[0]["id"]))


@app.get("/driver/application/{account_id}", response_class=HTMLResponse)
def driver_application(account_id: int, flash: str = "") -> HTMLResponse:
    rows = pg_get(f"/v_driver_accounts?id=eq.{account_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Application not found")
    account = rows[0]
    docs = pg_get(f"/driver_documents?driver_account_id=eq.{account_id}&order=created_at.desc")
    doc_rows = "".join(
        f"<tr><td>{esc(d.get('document_type'))}</td><td>{badge(d.get('status'))}</td><td class='code'>{esc(d.get('original_filename'))}</td><td>{esc(d.get('created_at'))}</td></tr>"
        for d in docs
    ) or '<tr><td colspan="4" class="muted">No documents uploaded yet.</td></tr>'

    body = f"""
    <section class="panel">
      <h2>{esc(account.get('legal_name'))}</h2>
      <div class="kpis">
        <div class="kpi"><span>Status</span><strong>{badge(account.get('status'))}</strong></div>
        <div class="kpi"><span>Trust</span><strong>{esc(account.get('trust_tier'))}</strong></div>
        <div class="kpi"><span>Documents</span><strong>{esc(account.get('document_count'))}</strong></div>
        <div class="kpi"><span>Phone</span><strong>{esc(account.get('phone'))}</strong></div>
      </div>
    </section>
    <div class="grid">
      <section class="panel">
        <h2>Consent</h2>
        <form method="post" action="/driver/application/{account_id}/consent">
          <label><input type="checkbox" name="terms" value="true" required> I accept driver terms.</label>
          <label><input type="checkbox" name="privacy" value="true" required> I accept privacy/data processing.</label>
          <label><input type="checkbox" name="location" value="true" required> I accept location use while online.</label>
          <label><input type="checkbox" name="biometric" value="true"> I accept identity/liveness checks.</label>
          <button class="good" type="submit">Save consent</button>
        </form>
      </section>
      <section class="panel">
        <h2>Upload evidence</h2>
        <form method="post" action="/driver/application/{account_id}/documents" enctype="multipart/form-data">
          <label>Document type
            <select name="document_type" required>
              <option value="GOV_ID_FRONT">Government ID front</option>
              <option value="GOV_ID_BACK">Government ID back</option>
              <option value="SELFIE">Selfie</option>
              <option value="LICENSE_FRONT">License front</option>
              <option value="LICENSE_BACK">License back</option>
              <option value="VEHICLE_REGISTRATION">Vehicle registration</option>
              <option value="VEHICLE_PHOTO_FRONT">Vehicle photo front</option>
              <option value="VEHICLE_PHOTO_REAR">Vehicle photo rear / plate</option>
              <option value="INSURANCE">Insurance / SOAT</option>
              <option value="PAYOUT_PROOF">Payout proof</option>
            </select>
          </label>
          <label>File
            <input type="file" name="file" accept="image/*,application/pdf" required>
          </label>
          <button type="submit">Upload</button>
        </form>
      </section>
    </div>
    <section class="panel">
      <h2>Documents</h2>
      <table><thead><tr><th>Type</th><th>Status</th><th>File</th><th>Uploaded</th></tr></thead><tbody>{doc_rows}</tbody></table>
    </section>
    """
    return layout("Driver Application", body, flash=flash)


@app.post("/driver/application/{account_id}/consent")
def save_consent(
    account_id: int,
    request: Request,
    terms: str = Form(...),
    privacy: str = Form(...),
    location: str = Form(...),
    biometric: str = Form("false"),
) -> RedirectResponse:
    rows = pg_get(f"/driver_accounts?id=eq.{account_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Application not found")
    accepted_at = utc_now_iso()
    user_agent = request.headers.get("user-agent", "")
    ip_address = request.client.host if request.client else ""
    version = "phase1-2026-06-12"
    for consent_type, accepted in {
        "TERMS": terms,
        "PRIVACY": privacy,
        "LOCATION": location,
        "BIOMETRIC": biometric,
    }.items():
        save_or_update_consent(
            account_id,
            consent_type,
            version,
            str(accepted).lower() in {"1", "true", "yes", "on"},
            accepted_at,
            ip_address,
            user_agent,
        )
    pg_patch(f"/driver_accounts?id=eq.{account_id}", {"status": "CONSENTED"})
    return RedirectResponse(url=f"/driver/application/{account_id}?flash=Consent+saved", status_code=303)


@app.post("/driver/application/{account_id}/documents")
async def upload_document(
    account_id: int,
    document_type: str = Form(...),
    file: UploadFile = File(...),
) -> RedirectResponse:
    rows = pg_get(f"/driver_accounts?id=eq.{account_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Application not found")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    digest = hashlib.sha256(data).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "upload.bin")[:120]
    account_dir = DRIVER_DOCUMENT_DIR / str(account_id)
    account_dir.mkdir(parents=True, exist_ok=True)
    storage_key = f"{account_id}/{document_type.lower()}_{digest[:16]}_{safe_name}"
    path = DRIVER_DOCUMENT_DIR / storage_key
    path.write_bytes(data)
    pg_post("/driver_documents", {
        "driver_account_id": account_id,
        "document_type": document_type,
        "storage_key": storage_key,
        "sha256": digest,
        "content_type": file.content_type,
        "original_filename": file.filename,
        "byte_size": len(data),
        "status": "SUBMITTED",
    })
    next_status = "IDENTITY_PENDING" if document_type.startswith("GOV_ID") or document_type == "SELFIE" else None
    if next_status:
        pg_patch(f"/driver_accounts?id=eq.{account_id}", {"status": next_status})
    return RedirectResponse(url=f"/driver/application/{account_id}?flash=Document+uploaded", status_code=303)


@app.post("/api/driver/{account_id}/online")
def api_driver_online(account_id: int, device_id: str = Form(""), app_version: str = Form("phase1")) -> JSONResponse:
    rows = pg_get(f"/driver_accounts?id=eq.{account_id}&status=in.(APPROVED,ACTIVE)&limit=1")
    if not rows:
        raise HTTPException(status_code=403, detail="Driver is not approved")
    session = pg_post("/driver_online_sessions", {
        "driver_account_id": account_id,
        "status": "ONLINE",
        "device_id": device_id.strip() or None,
        "app_version": app_version.strip() or None,
    })[0]
    pg_patch(f"/driver_accounts?id=eq.{account_id}", {"status": "ACTIVE"})
    return JSONResponse({"ok": True, "session_id": session["id"]})


@app.post("/api/driver/{account_id}/offline")
def api_driver_offline(account_id: int) -> JSONResponse:
    sessions = pg_get(
        f"/driver_online_sessions?driver_account_id=eq.{account_id}&status=eq.ONLINE&order=started_at.desc&limit=5"
    )
    for session in sessions:
        pg_patch(f"/driver_online_sessions?id=eq.{session['id']}", {
            "status": "OFFLINE",
            "ended_at": utc_now_iso(),
            "last_seen_at": utc_now_iso(),
        })
    pg_patch(f"/driver_accounts?id=eq.{account_id}&status=eq.ACTIVE", {"status": "APPROVED"})
    return JSONResponse({"ok": True, "closed_sessions": len(sessions)})


@app.post("/api/driver/{account_id}/location")
def api_driver_location(
    account_id: int,
    session_id: int = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    accuracy_m: float = Form(0),
) -> JSONResponse:
    sessions = pg_get(
        f"/driver_online_sessions?id=eq.{session_id}&driver_account_id=eq.{account_id}&status=eq.ONLINE&limit=1"
    )
    if not sessions:
        raise HTTPException(status_code=403, detail="No active online session")
    captured_at = utc_now_iso()
    pg_post("/driver_locations", {
        "driver_account_id": account_id,
        "session_id": session_id,
        "latitude": latitude,
        "longitude": longitude,
        "accuracy_m": accuracy_m,
        "captured_at": captured_at,
    })
    pg_patch(f"/driver_online_sessions?id=eq.{session_id}", {"last_seen_at": utc_now_iso()})
    return JSONResponse({"ok": True})


@app.get("/api/driver/{account_id}/offers")
def api_driver_offers(account_id: int) -> JSONResponse:
    rows = pg_get(
        "/v_delivery_offer_candidates"
        f"?driver_account_id=eq.{account_id}"
        "&status=in.(OFFERED,VIEWED)"
        "&batch_status=eq.OPEN"
        "&order=offered_at.desc"
        "&limit=10"
    )
    return JSONResponse({"ok": True, "offers": rows})


@app.post("/api/driver/{account_id}/offers/{candidate_id}/view")
def api_driver_offer_view(account_id: int, candidate_id: int) -> JSONResponse:
    rows = pg_get(
        f"/delivery_offer_candidates?id=eq.{candidate_id}&driver_account_id=eq.{account_id}&status=eq.OFFERED&limit=1"
    )
    if rows:
        pg_patch(f"/delivery_offer_candidates?id=eq.{candidate_id}", {"status": "VIEWED", "viewed_at": utc_now_iso()})
    return JSONResponse({"ok": True})


@app.post("/api/driver/{account_id}/offers/{candidate_id}/accept")
def api_driver_offer_accept(account_id: int, candidate_id: int) -> JSONResponse:
    result = pg_rpc("driver_accept_nearby_offer", {
        "p_driver_account_id": account_id,
        "p_candidate_id": candidate_id,
    })
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@app.post("/api/driver/{account_id}/offers/{candidate_id}/decline")
def api_driver_offer_decline(account_id: int, candidate_id: int) -> JSONResponse:
    result = pg_rpc("driver_decline_nearby_offer", {
        "p_driver_account_id": account_id,
        "p_candidate_id": candidate_id,
    })
    return JSONResponse(result, status_code=200 if result.get("ok") else 409)


@app.get("/ops/driver-dispatch", response_class=HTMLResponse)
def ops_driver_dispatch(
    request: Request,
    flash: str = "",
    x_admin_token: Optional[str] = Header(default=None),
) -> HTMLResponse:
    check_admin_auth(request, x_admin_token)
    pickups = pg_get("/pickup_points?order=codigo.asc")
    orders = pg_get(
        "/v_pedidos_logistica"
        "?select=id,pedido_num,estado,direccion_confirmada,direccion_detectada,created_at"
        "&estado=in.(CONFIRMADO,EN_PREPARACION,DESPACHADO)"
        "&order=id.desc"
        "&limit=25"
    )
    mappings = pg_get("/v_order_pickup_points?order=pedido_id.desc&limit=50")
    candidates = pg_get("/v_delivery_offer_candidates?order=batch_created_at.desc,score.asc&limit=50")
    online_sessions = pg_get("/driver_online_sessions?select=id&status=eq.ONLINE&limit=200")
    mapping_by_order = {row.get("pedido_id"): row for row in mappings}
    active_pickups = [p for p in pickups if p.get("activo")]
    open_candidates = [c for c in candidates if c.get("status") in {"OFFERED", "VIEWED"} and c.get("batch_status") == "OPEN"]

    def pickup_options_for(selected_id: Any) -> str:
        options = []
        for pickup in active_pickups:
            selected = " selected" if pickup.get("id") == selected_id else ""
            options.append(
                f"<option value='{esc(pickup.get('id'))}'{selected}>"
                f"{esc(pickup.get('codigo'))} - {esc(pickup.get('nombre'))}</option>"
            )
        return "".join(options)

    kpis = f"""
    <div class="kpis">
      <div class="kpi"><span>Active pickups</span><strong>{len(active_pickups)}</strong></div>
      <div class="kpi"><span>Mapped orders</span><strong>{len(mappings)}</strong></div>
      <div class="kpi"><span>Open offers</span><strong>{len(open_candidates)}</strong></div>
      <div class="kpi"><span>Online drivers</span><strong>{len(online_sessions)}</strong></div>
    </div>
    """
    pickup_rows = "".join(
        f"""
        <tr>
          <td><strong>{esc(p.get('codigo'))}</strong><br><span class="muted">{esc(p.get('nombre'))}</span></td>
          <td>{esc(p.get('direccion'))}</td>
          <td class="code">{esc(p.get('latitude'))}, {esc(p.get('longitude'))}</td>
          <td>{esc(p.get('service_radius_km'))} km</td>
          <td>{badge('ACTIVE' if p.get('activo') else 'INACTIVE')}</td>
        </tr>
        """
        for p in pickups
    ) or '<tr><td colspan="5" class="muted">No pickup points configured yet.</td></tr>'
    order_rows = ""
    for order in orders:
        mapping = mapping_by_order.get(order.get("id"), {})
        selected_pickup_id = mapping.get("pickup_point_id")
        mapping_badge = (
            f'{badge("MAPPED")}<br><span class="muted">{esc(mapping.get("pickup_codigo"))}</span>'
            if mapping else badge("UNMAPPED")
        )
        offer_disabled = "" if active_pickups else " disabled"
        order_rows += f"""
        <tr>
          <td><strong>{esc(order.get('pedido_num'))}</strong><br>{badge(order.get('estado'))}</td>
          <td>{esc(order.get('direccion_confirmada') or order.get('direccion_detectada') or '')}</td>
          <td>{mapping_badge}</td>
          <td>
            <form method="post" action="{with_token('/ops/orders/' + str(order.get('id')) + '/pickup', request)}">
              <select name="pickup_point_id" required>{pickup_options_for(selected_pickup_id)}</select>
              <button type="submit">Set</button>
            </form>
          </td>
          <td>
            <form method="post" action="{with_token('/ops/orders/' + str(order.get('id')) + '/offer-nearby', request)}">
              <input name="radius_km" placeholder="8.05" style="max-width:80px">
              <input name="max_candidates" placeholder="5" style="max-width:58px">
              <button type="submit"{offer_disabled}>Offer</button>
            </form>
          </td>
        </tr>
        """
    order_rows = order_rows or '<tr><td colspan="5" class="muted">No active orders found.</td></tr>'
    candidate_rows = "".join(
        f"""
        <tr>
          <td><strong>{esc(c.get('pedido_num'))}</strong><br><span class="muted code">batch {esc(c.get('batch_id'))}</span></td>
          <td><strong>{esc(c.get('pickup_codigo') or '')}</strong><br><span class="muted">{esc(c.get('pickup_nombre') or '')}</span></td>
          <td>{esc(c.get('driver_name'))}<br><span class="muted code">{esc(c.get('driver_phone'))}</span></td>
          <td>{esc(c.get('distance_km'))} km<br><span class="muted">{esc(c.get('eta_seconds'))} sec</span></td>
          <td>{badge(c.get('status'))}<br>{badge(c.get('batch_status'))}</td>
          <td>{esc(c.get('offered_at'))}</td>
        </tr>
        """
        for c in candidates
    ) or '<tr><td colspan="6" class="muted">No nearby offers yet.</td></tr>'
    body = f"""
    <section class="panel">{kpis}</section>
    <section class="panel">
      <h2>Pickup points</h2>
      <form method="post" action="{with_token('/ops/pickup-points', request)}">
        <label>Code <input name="codigo" placeholder="REST_SURCO" required></label>
        <label>Name <input name="nombre" placeholder="Restaurant / pickup" required></label>
        <label>Address <input name="direccion" required></label>
        <label>Latitude <input name="latitude" required></label>
        <label>Longitude <input name="longitude" required></label>
        <label>Radius km <input name="service_radius_km" value="8.05"></label>
        <button type="submit">Save pickup</button>
      </form>
      <table><thead><tr><th>Pickup</th><th>Address</th><th>Coords</th><th>Radius</th><th>Status</th></tr></thead><tbody>{pickup_rows}</tbody></table>
    </section>
    <section class="panel">
      <h2>Order pickup mapping</h2>
      <table><thead><tr><th>Order</th><th>Customer address</th><th>Pickup</th><th>Map</th><th>Offer</th></tr></thead><tbody>{order_rows}</tbody></table>
    </section>
    <section class="panel">
      <h2>Nearby offer candidates</h2>
      <table><thead><tr><th>Order</th><th>Pickup</th><th>Driver</th><th>Distance</th><th>Status</th><th>Offered</th></tr></thead><tbody>{candidate_rows}</tbody></table>
    </section>
    """
    return layout("Driver Dispatch", body, auth_query=token_query(request), flash=flash)


@app.post("/ops/pickup-points")
def ops_pickup_point_save(
    request: Request,
    codigo: str = Form(...),
    nombre: str = Form(...),
    direccion: str = Form(...),
    latitude: str = Form(...),
    longitude: str = Form(...),
    service_radius_km: str = Form("8.05"),
    referencia: str = Form(""),
    telefono: str = Form(""),
    x_admin_token: Optional[str] = Header(default=None),
) -> RedirectResponse:
    check_admin_auth(request, x_admin_token)
    lat = parse_optional_float(latitude)
    lon = parse_optional_float(longitude)
    radius = parse_optional_float(service_radius_km) or 8.05
    if lat is None or lon is None:
        raise HTTPException(status_code=400, detail="Latitude and longitude are required")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Latitude/longitude out of range")
    code = codigo.strip().upper()
    existing = pg_get(f"/pickup_points?codigo=eq.{quote(code, safe='')}&limit=1")
    payload = {
        "codigo": code,
        "nombre": nombre.strip(),
        "direccion": direccion.strip(),
        "referencia": referencia.strip() or None,
        "telefono": telefono.strip() or None,
        "latitude": lat,
        "longitude": lon,
        "service_radius_km": radius,
        "activo": True,
    }
    if existing:
        pg_patch(f"/pickup_points?id=eq.{existing[0]['id']}", payload)
    else:
        pg_post("/pickup_points", payload)
    return RedirectResponse(url=with_token("/ops/driver-dispatch?flash=Pickup+saved", request), status_code=303)


@app.post("/ops/orders/{pedido_id}/pickup")
def ops_order_pickup_set(
    pedido_id: int,
    request: Request,
    pickup_point_id: int = Form(...),
    x_admin_token: Optional[str] = Header(default=None),
) -> RedirectResponse:
    check_admin_auth(request, x_admin_token)
    result = pg_rpc("driver_set_order_pickup_point", {
        "p_pedido_id": pedido_id,
        "p_pickup_point_id": pickup_point_id,
    })
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return RedirectResponse(url=with_token("/ops/driver-dispatch?flash=Pickup+mapped", request), status_code=303)


@app.post("/ops/orders/{pedido_id}/offer-nearby")
def ops_order_offer_nearby(
    pedido_id: int,
    request: Request,
    radius_km: str = Form(""),
    max_candidates: str = Form("5"),
    x_admin_token: Optional[str] = Header(default=None),
) -> RedirectResponse:
    check_admin_auth(request, x_admin_token)
    radius = parse_optional_float(radius_km)
    try:
        max_count = int((max_candidates or "5").strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid max candidate count") from exc
    result = pg_rpc("driver_create_nearby_offer_batch", {
        "p_pedido_id": pedido_id,
        "p_pickup_point_id": None,
        "p_radius_km": radius,
        "p_max_candidates": max_count,
        "p_offer_ttl_seconds": 300,
    })
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    flash_text = quote(f"Offer batch {result.get('batch_id')} candidates {result.get('candidate_count')}", safe="")
    return RedirectResponse(url=with_token(f"/ops/driver-dispatch?flash={flash_text}", request), status_code=303)


@app.get("/ops/drivers", response_class=HTMLResponse)
def ops_drivers(
    request: Request,
    status: str = "",
    flash: str = "",
    x_admin_token: Optional[str] = Header(default=None),
) -> HTMLResponse:
    check_admin_auth(request, x_admin_token)
    rows = account_rows(status)
    pending_count = len([r for r in rows if r.get("status") not in {"APPROVED", "ACTIVE", "REJECTED", "SUSPENDED"}])
    approved_count = len([r for r in rows if r.get("status") in {"APPROVED", "ACTIVE"}])
    table_rows = "".join(
        f"""
        <tr>
          <td><strong>{esc(r.get('legal_name'))}</strong><br><span class="muted code">{esc(r.get('phone'))}</span></td>
          <td>{badge(r.get('status'))}<br><span class="muted">{esc(r.get('trust_tier'))}</span></td>
          <td>{esc(r.get('document_count'))}</td>
          <td>{esc(r.get('open_check_count'))}</td>
          <td>{esc(r.get('latest_location_at') or '')}</td>
          <td><a class="button" href="{with_token('/ops/drivers/' + str(r.get('id')), request)}">Review</a></td>
        </tr>
        """
        for r in rows
    ) or '<tr><td colspan="6" class="muted">No driver applications found.</td></tr>'
    body = f"""
    <section class="panel">
      <div class="kpis">
        <div class="kpi"><span>Total loaded</span><strong>{len(rows)}</strong></div>
        <div class="kpi"><span>Needs work</span><strong>{pending_count}</strong></div>
        <div class="kpi"><span>Approved</span><strong>{approved_count}</strong></div>
        <div class="kpi"><span>Mode</span><strong>Phase 2</strong></div>
      </div>
    </section>
    <section class="panel">
      <h2>Applications</h2>
      <table>
        <thead><tr><th>Driver</th><th>Status</th><th>Docs</th><th>Review flags</th><th>Location</th><th>Action</th></tr></thead>
        <tbody>{table_rows}</tbody>
      </table>
    </section>
    """
    return layout("Driver Ops", body, auth_query=token_query(request), flash=flash)


@app.get("/ops/drivers/{account_id}", response_class=HTMLResponse)
def ops_driver_detail(
    account_id: int,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None),
) -> HTMLResponse:
    check_admin_auth(request, x_admin_token)
    rows = pg_get(f"/v_driver_accounts?id=eq.{account_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Driver account not found")
    account = rows[0]
    docs = pg_get(f"/driver_documents?driver_account_id=eq.{account_id}&order=created_at.desc")
    checks = pg_get(f"/driver_verification_checks?driver_account_id=eq.{account_id}&order=created_at.desc")
    vehicles = pg_get(f"/driver_vehicles?driver_account_id=eq.{account_id}&order=created_at.desc")
    doc_rows = "".join(
        f"<tr><td>{esc(d.get('document_type'))}</td><td>{badge(d.get('status'))}</td><td class='code'>{esc(d.get('storage_key'))}</td><td>{esc(d.get('byte_size'))}</td></tr>"
        for d in docs
    ) or '<tr><td colspan="4" class="muted">No documents.</td></tr>'
    check_rows = "".join(
        f"<tr><td>{esc(c.get('check_type'))}</td><td>{esc(c.get('provider'))}</td><td>{badge(c.get('status'))}</td><td>{esc(c.get('reason_code') or '')}</td></tr>"
        for c in checks
    ) or '<tr><td colspan="4" class="muted">No checks yet.</td></tr>'
    vehicle_rows = "".join(
        f"<tr><td>{esc(v.get('vehicle_type'))}</td><td>{esc(v.get('plate'))}</td><td>{badge(v.get('status'))}</td><td>{esc(v.get('insurance_expires_at') or '')}</td></tr>"
        for v in vehicles
    ) or '<tr><td colspan="4" class="muted">No vehicles yet.</td></tr>'
    body = f"""
    <section class="panel">
      <h2>{esc(account.get('legal_name'))}</h2>
      <div class="kpis">
        <div class="kpi"><span>Status</span><strong>{badge(account.get('status'))}</strong></div>
        <div class="kpi"><span>Trust</span><strong>{esc(account.get('trust_tier'))}</strong></div>
        <div class="kpi"><span>Risk</span><strong>{esc(account.get('risk_score'))}</strong></div>
        <div class="kpi"><span>Phone</span><strong>{esc(account.get('phone'))}</strong></div>
      </div>
    </section>
    <div class="grid">
      <section class="panel">
        <h2>Decision</h2>
        <form method="post" action="{with_token('/ops/drivers/' + str(account_id) + '/approve', request)}">
          <label>Driver code
            <input name="codigo" placeholder="DRV{account_id:04d}">
          </label>
          <label>Notes
            <textarea name="notes" placeholder="Approval notes"></textarea>
          </label>
          <button class="good" type="submit">Approve and create repartidor</button>
        </form>
        <form method="post" action="{with_token('/ops/drivers/' + str(account_id) + '/reject', request)}">
          <label>Reject reason
            <textarea name="reason" required></textarea>
          </label>
          <button class="bad" type="submit">Reject</button>
        </form>
      </section>
      <section class="panel">
        <h2>Vehicle quick add</h2>
        <form method="post" action="{with_token('/ops/drivers/' + str(account_id) + '/vehicle', request)}">
          <label>Type
            <select name="vehicle_type">
              <option value="MOTORCYCLE">Motorcycle</option>
              <option value="CAR">Car</option>
              <option value="BICYCLE">Bicycle</option>
              <option value="CARGO_BIKE">Cargo bike</option>
            </select>
          </label>
          <label>Plate <input name="plate" required></label>
          <label>Insurance expires <input name="insurance_expires_at" type="date"></label>
          <button type="submit">Save vehicle</button>
        </form>
      </section>
    </div>
    <section class="panel"><h2>Documents</h2><table><thead><tr><th>Type</th><th>Status</th><th>Storage</th><th>Bytes</th></tr></thead><tbody>{doc_rows}</tbody></table></section>
    <section class="panel"><h2>Verification checks</h2><table><thead><tr><th>Type</th><th>Provider</th><th>Status</th><th>Reason</th></tr></thead><tbody>{check_rows}</tbody></table></section>
    <section class="panel"><h2>Vehicles</h2><table><thead><tr><th>Type</th><th>Plate</th><th>Status</th><th>Insurance expires</th></tr></thead><tbody>{vehicle_rows}</tbody></table></section>
    """
    return layout("Driver Review", body, auth_query=token_query(request))


@app.post("/ops/drivers/{account_id}/vehicle")
def ops_driver_vehicle(
    account_id: int,
    request: Request,
    vehicle_type: str = Form(...),
    plate: str = Form(...),
    insurance_expires_at: str = Form(""),
    x_admin_token: Optional[str] = Header(default=None),
) -> RedirectResponse:
    check_admin_auth(request, x_admin_token)
    normalized_plate = re.sub(r"[^A-Za-z0-9]+", "", plate).upper()
    if not normalized_plate:
        raise HTTPException(status_code=400, detail="Plate required")
    pg_post("/driver_vehicles", {
        "driver_account_id": account_id,
        "vehicle_type": vehicle_type,
        "plate": normalized_plate,
        "insurance_expires_at": insurance_expires_at or None,
        "status": "SUBMITTED",
    })
    return RedirectResponse(url=with_token(f"/ops/drivers/{account_id}", request), status_code=303)


@app.post("/ops/drivers/{account_id}/approve")
def ops_driver_approve(
    account_id: int,
    request: Request,
    codigo: str = Form(""),
    notes: str = Form(""),
    x_admin_token: Optional[str] = Header(default=None),
) -> RedirectResponse:
    check_admin_auth(request, x_admin_token)
    result = pg_rpc("driver_approve_to_repartidor", {
        "p_driver_account_id": account_id,
        "p_reviewer": "driver-ops",
        "p_codigo": codigo.strip() or None,
        "p_notes": notes.strip() or None,
    })
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return RedirectResponse(url=with_token("/ops/drivers?flash=Driver+approved", request), status_code=303)


@app.post("/ops/drivers/{account_id}/reject")
def ops_driver_reject(
    account_id: int,
    request: Request,
    reason: str = Form(...),
    x_admin_token: Optional[str] = Header(default=None),
) -> RedirectResponse:
    check_admin_auth(request, x_admin_token)
    pg_patch(f"/driver_accounts?id=eq.{account_id}", {
        "status": "REJECTED",
        "trust_tier": "TIER_5_RESTRICTED",
        "rejection_reason": reason.strip(),
        "rejected_at": utc_now_iso(),
    })
    pg_post("/driver_manual_reviews", {
        "driver_account_id": account_id,
        "reviewer": "driver-ops",
        "decision": "REJECT",
        "reason": reason.strip(),
    })
    return RedirectResponse(url=with_token("/ops/drivers?flash=Driver+rejected", request), status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=APP_HOST, port=APP_PORT)
