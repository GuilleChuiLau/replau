#!/usr/bin/env python3
from __future__ import annotations

import os
import time
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from urllib.parse import quote

import requests

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")

# OpenClaw outbound sender endpoint.
# You will configure this when your OpenClaw side has a send-message route.
OPENCLAW_SEND_URL = os.environ.get("OPENCLAW_SEND_URL", "").strip()
OPENCLAW_HOOK_TOKEN = os.environ.get("OPENCLAW_HOOK_TOKEN", os.environ.get("HOOK_TOKEN", "RESTRICTED"))

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "5"))
BATCH_LIMIT = int(os.environ.get("BATCH_LIMIT", "10"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "5"))
POLICY_DENY_RETRY_SECONDS = int(os.environ.get("POLICY_DENY_RETRY_SECONDS", "900"))
DRY_RUN_RECHECK_SECONDS = int(os.environ.get("DRY_RUN_RECHECK_SECONDS", "300"))
COALESCIBLE_EVENTS = {
    "DELIVERY_ASSIGNED",
    "DELIVERY_PICKED_UP",
    "DELIVERY_EN_ROUTE",
    "DELIVERY_ARRIVED",
}

# Start dry-run true. It prints messages and leaves them PENDING.
WHATSAPP_DRY_RUN = os.environ.get("WHATSAPP_DRY_RUN", "true").lower() == "true"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def pg_url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return POSTGREST_BASE_URL + path


def postgrest_request(method: str, path_or_url: str, **kwargs: Any) -> requests.Response:
    url = path_or_url if path_or_url.startswith("http") else pg_url(path_or_url)
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Connection", "close")
    max_attempts = 8
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            logging.warning(
                "Transient PostGREST %s %s failed on attempt %s/%s: %s",
                method.upper(), url, attempt + 1, max_attempts, exc
            )
            if attempt == max_attempts - 1:
                raise
            time.sleep(min(2.0, 0.25 * (attempt + 1)))
    assert last_exc is not None
    raise last_exc


def get_pending() -> List[Dict[str, Any]]:
    now = quote(utc_now_iso(), safe=":-TZ")
    url = (
        pg_url("/whatsapp_outbox")
        + "?status=eq.PENDING"
        + f"&attempts=lt.{MAX_ATTEMPTS}"
        + f"&or=(not_before.is.null,not_before.lte.{now})"
        + "&select=id,pedido_id,whatsapp_number,message_text,event_type,attempts,created_at,not_before,policy_decision"
        + "&order=created_at.asc"
        + f"&limit={BATCH_LIMIT}"
    )
    response = postgrest_request("GET", url)
    return response.json()


def patch_outbox(outbox_id: int, payload: Dict[str, Any]) -> Any:
    response = postgrest_request(
        "PATCH",
        f"/whatsapp_outbox?id=eq.{outbox_id}",
        headers={"Content-Type": "application/json", "Prefer": "return=representation"},
        json=payload,
    )
    return response.json()


def pg_rpc(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = postgrest_request(
        "POST",
        f"/rpc/{name}",
        headers={"Content-Type": "application/json"},
        json=payload,
    )
    return response.json()


def later_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, seconds))).isoformat().replace("+00:00", "Z")


def evaluate_policy(row: Dict[str, Any], record: bool = True) -> Dict[str, Any]:
    return pg_rpc(
        "evaluate_whatsapp_outbound_policy",
        {"p_outbox_id": row["id"], "p_record": record},
    )


def coalesce_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[Any, str], List[Dict[str, Any]]] = {}
    retained: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("event_type") not in COALESCIBLE_EVENTS:
            retained.append(row)
            continue
        key = (row.get("pedido_id"), "".join(ch for ch in str(row.get("whatsapp_number") or "") if ch.isdigit()))
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        group.sort(key=lambda row: (str(row.get("created_at") or ""), int(row["id"])))
        newest = group[-1]
        retained.append(newest)
        for older in group[:-1]:
            pg_rpc(
                "record_whatsapp_coalesced",
                {
                    "p_cancelled_outbox_id": older["id"],
                    "p_retained_outbox_id": newest["id"],
                },
            )
            logging.info("Coalesced WhatsApp outbox id=%s into id=%s", older["id"], newest["id"])
    return sorted(retained, key=lambda row: (str(row.get("created_at") or ""), int(row["id"])))


def send_whatsapp(row: Dict[str, Any]) -> Dict[str, Any]:
    if WHATSAPP_DRY_RUN:
        logging.info(
            "DRY RUN WhatsApp outbox id=%s event=%s message_length=%s",
            row["id"], row["event_type"], len(str(row.get("message_text") or "")),
        )
        return {"ok": True, "dry_run": True}

    if not OPENCLAW_SEND_URL:
        raise RuntimeError("OPENCLAW_SEND_URL is not configured")

    payload = {
        "whatsapp_number": row["whatsapp_number"],
        "message_text": row["message_text"],
        "event_type": row["event_type"],
        "pedido_id": row["pedido_id"],
        "outbox_id": row["id"],
    }

    response = requests.post(
        OPENCLAW_SEND_URL,
        headers={
            "Content-Type": "application/json",
            "X-Hook-Token": OPENCLAW_HOOK_TOKEN,
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    try:
        return response.json()
    except Exception:
        return {"ok": True, "text": response.text}


def process_one(row: Dict[str, Any]) -> None:
    outbox_id = row["id"]
    attempts = int(row.get("attempts") or 0)

    logging.info("Processing WhatsApp outbox id=%s event=%s", outbox_id, row["event_type"])

    try:
        policy = evaluate_policy(row, record=not WHATSAPP_DRY_RUN)
        if not policy.get("allowed"):
            decision = str(policy.get("decision") or "PAUSED")
            retry_after = int(policy.get("retry_after_seconds") or POLICY_DENY_RETRY_SECONDS)
            payload: Dict[str, Any] = {
                "policy_decision": decision,
                "policy_reason": str(policy.get("reason") or "Policy denied delivery")[:500],
                "policy_evaluated_at": utc_now_iso(),
            }
            if decision == "OPTED_OUT":
                payload["status"] = "CANCELLED"
                payload["error_message"] = "Cancelled by recipient opt-out policy"
            else:
                payload["status"] = "PENDING"
                payload["not_before"] = later_iso(retry_after)
            patch_outbox(outbox_id, payload)
            logging.warning("WhatsApp outbox id=%s held by policy decision=%s", outbox_id, decision)
            return

        if WHATSAPP_DRY_RUN:
            result = send_whatsapp(row)
            patch_outbox(
                outbox_id,
                {
                    "status": "PENDING",
                    "not_before": later_iso(DRY_RUN_RECHECK_SECONDS),
                    "policy_decision": "DRY_RUN",
                    "policy_reason": "Policy allowed; live delivery disabled",
                    "policy_evaluated_at": utc_now_iso(),
                    "raw_response": result,
                    "error_message": None,
                },
            )
            return

        patch_outbox(
            outbox_id,
            {
                "status": "SENDING",
                "attempts": attempts + 1,
                "last_attempt_at": utc_now_iso(),
                "error_message": None,
            },
        )

        result = send_whatsapp(row)

        patch_outbox(
            outbox_id,
            {
                "status": "SENT",
                "sent_at": utc_now_iso(),
                "raw_response": result,
                "error_message": None,
            },
        )
        try:
            pg_rpc("record_whatsapp_delivery_result", {"p_outbox_id": outbox_id, "p_succeeded": True, "p_error": None})
        except Exception:
            # Delivery is already committed as SENT. Never retry a delivered
            # message merely because policy telemetry failed afterward.
            logging.exception("Could not record WhatsApp delivery success in policy telemetry")

        logging.info("SENT whatsapp_outbox id=%s", outbox_id)

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        logging.error("ERROR whatsapp_outbox id=%s: %s", outbox_id, error_text)
        traceback.print_exc()

        new_status = "ERROR" if attempts + 1 >= MAX_ATTEMPTS else "PENDING"

        try:
            try:
                pg_rpc(
                    "record_whatsapp_delivery_result",
                    {"p_outbox_id": outbox_id, "p_succeeded": False, "p_error": error_text[:500]},
                )
            except Exception:
                logging.exception("Could not record WhatsApp delivery failure in policy circuit")
            patch_outbox(
                outbox_id,
                {
                    "status": new_status,
                    "attempts": attempts + 1,
                    "last_attempt_at": utc_now_iso(),
                    "error_message": error_text[:1000],
                },
            )
        except Exception:
            logging.exception("Could not update whatsapp_outbox id=%s after failure", outbox_id)


def run_once() -> None:
    rows = get_pending()
    if not rows:
        logging.info("No pending WhatsApp notifications.")
        return

    logging.info("Found %s pending WhatsApp notification(s).", len(rows))

    for row in coalesce_rows(rows):
        process_one(row)


def run_forever() -> None:
    logging.info("Replau WhatsApp Outbox Worker started.")
    logging.info("POSTGREST_BASE_URL=%s", POSTGREST_BASE_URL)
    logging.info("WHATSAPP_DRY_RUN=%s", WHATSAPP_DRY_RUN)
    logging.info("OPENCLAW_SEND_URL=%s", OPENCLAW_SEND_URL or "(not configured)")
    while True:
        try:
            run_once()
        except Exception:
            logging.exception("Worker loop error")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    mode = os.environ.get("WORKER_MODE", "forever").lower()
    if mode == "once":
        run_once()
    else:
        run_forever()
