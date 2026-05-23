#!/usr/bin/env python3
from __future__ import annotations

import html
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8795"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))
REQUIRE_REVIEW_TOKEN = os.environ.get("REQUIRE_REVIEW_TOKEN", "false").lower() == "true"
REVIEW_TOKEN = os.environ.get("REVIEW_TOKEN", "").strip()
PAYMENT_RECEIPT_DIR = Path(os.environ.get("PAYMENT_RECEIPT_DIR", "/home/guill/.openclaw/workspace/replau_payment_receipts")).resolve()

app = FastAPI(title="Replau Payment Proof Review", version="1.0.0")


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def check_auth(request: Request, x_review_token: Optional[str] = None) -> None:
    if not REQUIRE_REVIEW_TOKEN:
        return
    if request.query_params.get("token") == REVIEW_TOKEN or x_review_token == REVIEW_TOKEN:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing review token")


def token_query(request: Request) -> str:
    """Preserve query-token auth across links, forms, and redirects."""
    token = request.query_params.get("token")
    if REQUIRE_REVIEW_TOKEN and token == REVIEW_TOKEN:
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
        <a href="/{auth_query}">Payments</a>
        <a href="{esc(local_service_url("http://127.0.0.1:8794", script_name="replau_product_admin.py", token_env="ADMIN_TOKEN"))}">Products</a>
        <a href="{esc(local_service_url("http://127.0.0.1:8794", "recipes", "replau_product_admin.py", "ADMIN_TOKEN"))}">Recipes</a>
        <a href="{esc(local_service_url("http://127.0.0.1:8794", "costs", "replau_product_admin.py", "ADMIN_TOKEN"))}">Costs</a>
        <a href="http://127.0.0.1:8794/menu" target="_blank">Public Menu</a>
      </div>
    """


def pg_get(path: str) -> Any:
    r = requests.get(POSTGREST_BASE_URL + path, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def pg_rpc(name: str, payload: Dict[str, Any]) -> Any:
    r = requests.post(
        f"{POSTGREST_BASE_URL}/rpc/{name}",
        json=payload,
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def layout(title: str, body: str, flash: str = "", auth_query: str = "") -> HTMLResponse:
    flash_html = f'<div class="flash">{esc(flash)}</div>' if flash else ""
    return HTMLResponse(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{esc(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ margin:0; font-family:"Inter", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#0f172a; color:#e5e7eb; }}
    .wrap {{ max-width:1300px; margin:0 auto; padding:22px; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }}
    h1 {{ margin:0; font-size:32px; }}
    a {{ color:#93c5fd; text-decoration:none; }}
    .nav a {{ margin-left:12px; }}
    .erp-nav {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 18px; padding:12px; border:1px solid #334155; border-radius:14px; background:#0b1220; }}
    .erp-nav a {{ color:#e5e7eb; background:#1f2937; border:1px solid #334155; border-radius:999px; padding:8px 11px; font-size:13px; font-weight:bold; }}
    .erp-nav a:hover {{ background:#2563eb; border-color:#60a5fa; }}
    .card {{ background:#111827; border:1px solid #334155; border-radius:18px; padding:18px; margin:16px 0; box-shadow:0 10px 35px rgba(0,0,0,.25); }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ text-align:left; border-bottom:1px solid #334155; padding:10px; vertical-align:top; }}
    th {{ color:#93c5fd; }}
    input, textarea, select {{ width:100%; box-sizing:border-box; padding:10px; border-radius:10px; border:1px solid #475569; background:#020617; color:#e5e7eb; }}
    textarea {{ min-height:80px; }}
    button {{ padding:10px 14px; border-radius:12px; border:0; background:#2563eb; color:white; font-weight:bold; cursor:pointer; margin:4px; }}
    button.good {{ background:#166534; }}
    button.bad {{ background:#b91c1c; }}
    .pill {{ display:inline-block; padding:5px 9px; border-radius:999px; background:#334155; }}
    .RECEIVED {{ background:#1d4ed8; }} .VERIFIED {{ background:#166534; }} .REJECTED {{ background:#b91c1c; }} .CANCELLED {{ background:#475569; }}
    .muted {{ color:#94a3b8; }}
    .flash {{ background:#064e3b; border:1px solid #059669; padding:12px; border-radius:14px; margin:14px 0; }}
    .media {{ max-width:260px; max-height:260px; border-radius:12px; border:1px solid #334155; background:#020617; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .kpi-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .kpi {{ background:#0b1220; border:1px solid #334155; border-radius:14px; padding:14px; }}
    .kpi span {{ display:block; color:#94a3b8; font-size:12px; font-weight:bold; text-transform:uppercase; letter-spacing:.05em; }}
    .kpi strong {{ display:block; margin-top:8px; font-size:26px; line-height:1; }}
    .quick-actions {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }}
    .quick-actions a {{ display:inline-block; padding:9px 12px; border-radius:12px; background:#334155; color:#e5e7eb; font-weight:bold; }}
    @media(max-width:900px) {{ .grid,.kpi-grid {{ grid-template-columns:1fr; }} }}
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
        <a href="/{auth_query}">Proofs</a>
        <a href="/health{auth_query}">Health</a>
      </div>
    </div>
    {erp_nav(auth_query)}
    {flash_html}
    {body}
  </div>
</body>
</html>""")


def media_html(row: Dict[str, Any]) -> str:
    url = row.get("media_url") or row.get("local_path") or ""
    if not url:
        return '<span class="muted">No media URL</span>'
    safe = esc(url)
    if str(url).startswith("http"):
        return f'<a href="{safe}" target="_blank"><img class="media" src="{safe}" onerror="this.style.display=\'none\';this.parentElement.innerHTML=\'Open media\';"></a>'
    return f'<code>{safe}</code>'


def proof_view_link(proof_id: Any, request: Request) -> str:
    if proof_id is None:
        return ""
    return f"/proof/{quote(str(proof_id), safe='')}/view{token_query(request)}"


def proof_media_link(proof_id: Any, request: Request) -> str:
    if proof_id is None:
        return ""
    return f"/proof/{quote(str(proof_id), safe='')}/media{token_query(request)}"


def proof_media_tag(proof_id: Any, row: Dict[str, Any], request: Request) -> str:
    media_url = proof_media_link(proof_id, request)
    view_url = proof_view_link(proof_id, request)
    media_type = str(row.get("media_type") or "").lower()
    local_path = str(row.get("local_path") or "")
    suffix = Path(local_path).suffix.lower()
    open_links = f"""
      <div class="quick-actions">
        <a href="{esc(media_url)}" target="_blank">Open exact file</a>
        <a href="{esc(view_url)}" target="_blank">Viewer</a>
      </div>
    """
    if media_type == "document" or suffix == ".pdf":
        return f"""
          <iframe src="{esc(media_url)}" style="width:100%;min-height:520px;border:1px solid #334155;border-radius:12px;background:#020617;"></iframe>
          {open_links}
        """
    return f"""
      <a href="{esc(media_url)}" target="_blank">
        <img class="media" style="max-width:100%;max-height:720px;" src="{esc(media_url)}" alt="Customer submitted proof file">
      </a>
      {open_links}
    """


def get_proof(proof_id: int) -> Dict[str, Any]:
    rows = pg_get(f"/v_payment_proofs_logistica?id=eq.{proof_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Proof not found")
    return rows[0]


def local_proof_path(row: Dict[str, Any]) -> Path:
    raw_path = str(row.get("local_path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=404, detail="Proof has no saved local file")
    path = Path(raw_path).expanduser().resolve()
    try:
        path.relative_to(PAYMENT_RECEIPT_DIR)
    except ValueError:
        raise HTTPException(status_code=403, detail="Proof file path is outside the receipt directory")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Saved proof file not found")
    return path


def money(value: Any) -> str:
    try:
        return f"S/ {float(value or 0):,.2f}"
    except Exception:
        return "S/ 0.00"


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        rows = pg_get("/v_payment_proofs_logistica?limit=1")
        return {"ok": True, "app": "replau-payment-proof-review", "postgrest": POSTGREST_BASE_URL, "sample_ok": isinstance(rows, list)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str = "ALL", flash: str = "", x_review_token: Optional[str] = Header(default=None, alias="X-Review-Token")) -> HTMLResponse:
    check_auth(request, x_review_token)
    path = "/v_payment_proofs_logistica?order=id.desc&limit=100"
    if status and status != "ALL":
        path += f"&status=eq.{status}"
    rows = pg_get(path)
    all_rows = rows if status == "ALL" else pg_get("/v_payment_proofs_logistica?order=id.desc&limit=100")
    received = [r for r in all_rows if str(r.get("status") or "").upper() == "RECEIVED"]
    verified = [r for r in all_rows if str(r.get("status") or "").upper() == "VERIFIED"]
    rejected = [r for r in all_rows if str(r.get("status") or "").upper() == "REJECTED"]
    pending_value = sum(float(r.get("total") or 0) for r in received)
    auth_suffix = ("&" + token_query(request)[1:]) if token_query(request) else ""

    tr = ""
    for r in rows:
        st = esc(r.get("status"))
        proof_id = r.get("id")
        saved_file_action = (
            f'<a href="{esc(proof_view_link(proof_id, request))}" target="_blank">View submitted file</a>'
            if r.get("local_path")
            else '<span class="muted">No saved file</span>'
        )
        tr += f"""
        <tr>
          <td>{esc(proof_id)}</td>
          <td><strong>{esc(r.get('pedido_num'))}</strong><br><span class="muted">pedido_id={esc(r.get('pedido_id'))}</span></td>
          <td>{esc(r.get('cliente_nombre'))}<br>{esc(r.get('whatsapp_number'))}</td>
          <td>S/ {esc(r.get('total'))}<br><span class="muted">{esc(r.get('payment_status'))}</span></td>
          <td>{media_html(r)}<br><span class="muted">{esc(r.get('caption'))}</span></td>
          <td><span class="pill {st}">{st}</span><br>{esc(r.get('created_at'))}</td>
          <td>
            <a href="/proof/{esc(proof_id)}{token_query(request)}">Review</a><br>
            {saved_file_action}
          </td>
        </tr>
        """

    body = f"""
    <div class="card">
      <h2>Cashier Workspace</h2>
      <p class="muted">Prioridad de caja: comprobantes recibidos, valor pendiente y decisiones recientes.</p>
      <div class="kpi-grid">
        <div class="kpi"><span>Por revisar</span><strong>{len(received)}</strong></div>
        <div class="kpi"><span>Valor pendiente</span><strong>{money(pending_value)}</strong></div>
        <div class="kpi"><span>Verificados</span><strong>{len(verified)}</strong></div>
        <div class="kpi"><span>Rechazados</span><strong>{len(rejected)}</strong></div>
      </div>
      <div class="quick-actions">
        <a href="/{token_query(request)}">Todo</a>
        <a href="/?status=RECEIVED{auth_suffix}">Por revisar</a>
        <a href="/?status=VERIFIED{auth_suffix}">Verificados</a>
        <a href="http://127.0.0.1:8790/dashboard">Logistics</a>
      </div>
    </div>
    <div class="card">
      <form method="get" action="/{token_query(request)}">
        {f'<input type="hidden" name="token" value="{esc(request.query_params.get("token"))}">' if token_query(request) else ""}
        <label>Status</label>
        <select name="status">
          {''.join(f'<option value="{s}" {"selected" if status == s else ""}>{s}</option>' for s in ['ALL','RECEIVED','VERIFIED','REJECTED','CANCELLED'])}
        </select>
        <br><br><button type="submit">Filter</button>
      </form>
    </div>
    <div class="card">
      <h2>Payment proofs</h2>
      <table>
        <thead><tr><th>ID</th><th>Order</th><th>Customer</th><th>Total/Payment</th><th>Proof</th><th>Status</th><th>Action</th></tr></thead>
        <tbody>{tr or '<tr><td colspan="7" class="muted">No proofs found.</td></tr>'}</tbody>
      </table>
    </div>
    """
    return layout("Payment Proof Review", body, flash=flash, auth_query=token_query(request))


@app.get("/proof/{proof_id}/media")
def proof_media(proof_id: int, request: Request, x_review_token: Optional[str] = Header(default=None, alias="X-Review-Token")) -> FileResponse:
    check_auth(request, x_review_token)
    row = get_proof(proof_id)
    path = local_proof_path(row)
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/proof/{proof_id}/view", response_class=HTMLResponse)
def proof_file_view(proof_id: int, request: Request, x_review_token: Optional[str] = Header(default=None, alias="X-Review-Token")) -> HTMLResponse:
    check_auth(request, x_review_token)
    row = get_proof(proof_id)
    local_proof_path(row)
    body = f"""
    <div class="card">
      <h2>Customer submitted file</h2>
      <p><strong>Proof:</strong> #{esc(row.get('id'))} · <strong>Order:</strong> {esc(row.get('pedido_num'))}</p>
      <p class="muted">Original file saved as: {esc(row.get('local_path'))}</p>
      {proof_media_tag(proof_id, row, request)}
    </div>
    <p><a href="/proof/{proof_id}{token_query(request)}">Back to Review Proof</a></p>
    """
    return layout(f"Proof File {proof_id}", body, auth_query=token_query(request))


@app.get("/proof/{proof_id}", response_class=HTMLResponse)
def proof_detail(proof_id: int, request: Request, flash: str = "", x_review_token: Optional[str] = Header(default=None, alias="X-Review-Token")) -> HTMLResponse:
    check_auth(request, x_review_token)
    rows = pg_get(f"/v_payment_proofs_logistica?id=eq.{proof_id}&limit=1")
    if not rows:
        raise HTTPException(status_code=404, detail="Proof not found")
    r = rows[0]
    body = f"""
    <div class="grid">
      <div class="card">
        <h2>Proof #{esc(r.get('id'))}</h2>
        <p><strong>Order:</strong> {esc(r.get('pedido_num'))} / pedido_id={esc(r.get('pedido_id'))}</p>
        <p><strong>Customer:</strong> {esc(r.get('cliente_nombre'))} / {esc(r.get('whatsapp_number'))}</p>
        <p><strong>Total:</strong> S/ {esc(r.get('total'))}</p>
        <p><strong>Payment status:</strong> {esc(r.get('payment_status'))}</p>
        <p><strong>Proof status:</strong> <span class="pill {esc(r.get('status'))}">{esc(r.get('status'))}</span></p>
        <p><strong>Caption:</strong><br>{esc(r.get('caption'))}</p>
        <p><strong>Created:</strong> {esc(r.get('created_at'))}</p>
      </div>
      <div class="card">
        <h2>Media</h2>
        {proof_media_tag(proof_id, r, request)}
        <p class="muted">media_url: {esc(r.get('media_url'))}</p>
        <p class="muted">local_path: {esc(r.get('local_path'))}</p>
      </div>
    </div>
    <div class="card">
      <h2>Review</h2>
      <form method="post" action="/proof/{proof_id}/review{token_query(request)}">
        <label>Reviewed by</label>
        <input name="verified_by" value="logistica">
        <label>Notes</label>
        <textarea name="notes" placeholder="Optional note. For rejection, this is sent to the customer."></textarea>
        <label>Notify customer by WhatsApp?</label>
        <select name="notify"><option value="true">Yes</option><option value="false">No</option></select>
        <br><br>
        <button class="good" name="status" value="VERIFIED" type="submit">Verify payment</button>
        <button class="bad" name="status" value="REJECTED" type="submit">Reject proof</button>
        <button class="secondary" name="status" value="CANCELLED" type="submit">Cancel proof</button>
      </form>
    </div>
    <p><a href="/{token_query(request)}">Back</a></p>
    """
    return layout(f"Review Proof {proof_id}", body, flash=flash, auth_query=token_query(request))


@app.post("/proof/{proof_id}/review")
def review_proof(proof_id: int, request: Request, status: str = Form(...), verified_by: str = Form("logistica"), notes: str = Form(""), notify: str = Form("true"), x_review_token: Optional[str] = Header(default=None, alias="X-Review-Token")) -> RedirectResponse:
    check_auth(request, x_review_token)
    pg_rpc("revisar_comprobante_pago", {
        "p_proof_id": proof_id,
        "p_status": status,
        "p_verified_by": verified_by,
        "p_notes": notes,
        "p_notify": notify.lower() == "true",
    })
    # Return to the full grid so the reviewed row remains visible and shows
    # its new VERIFIED/REJECTED/CANCELLED state after refresh.
    return RedirectResponse(url=with_token("/?status=ALL&flash=Proof+reviewed", request), status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("replau_payment_proof_review:app", host=APP_HOST, port=APP_PORT, reload=False)
