#!/usr/bin/env python3
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

BASE = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "8"))
OUTBOX_MAX = int(os.environ.get("OUTBOX_MAX_ATTEMPTS", "5"))
EMAIL_NOTIFICATIONS_ENABLED = os.environ.get("EMAIL_NOTIFICATIONS_ENABLED", "false").lower() == "true"
EMAIL_PENDING_MAX_MINUTES = int(os.environ.get("EMAIL_PENDING_MAX_MINUTES", "30"))


def get(path: str) -> Any:
    response = requests.get(BASE + path, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def split_actionable_whatsapp(
    rows: list[dict[str, Any]], policy: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # The latest explicit policy decision is the operational baseline. Rows
    # older than it remain visible for audit but are no longer actionable,
    # whether the new decision paused or re-enabled delivery.
    if not policy:
        return rows, []
    paused_at = parse_timestamp(policy.get("updated_at"))
    if paused_at is None:
        return rows, []
    actionable, historical = [], []
    for row in rows:
        incident_at = parse_timestamp(row.get("last_attempt_at") or row.get("created_at"))
        (historical if incident_at and incident_at < paused_at else actionable).append(row)
    return actionable, historical


def stale_email_rows(
    rows: list[dict[str, Any]],
    enabled: bool,
    max_minutes: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - max_minutes * 60
    return [
        row
        for row in rows
        if (created := parse_timestamp(row.get("updated_at") or row.get("created_at")))
        and created.timestamp() <= cutoff
    ]


def main() -> int:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Replau stuck monitor")
    failures: list[str] = []
    pending = get(
        "/v_whatsapp_outbox?status=eq.PENDING"
        "&select=id,pedido_num,event_type,status,attempts,last_attempt_at,created_at,error_message"
        "&order=id.desc&limit=100"
    )
    stuck = [row for row in pending if int(row.get("attempts") or 0) >= OUTBOX_MAX]
    errors = get(
        "/v_whatsapp_outbox?status=eq.ERROR"
        "&select=id,pedido_num,event_type,status,attempts,last_attempt_at,created_at,error_message"
        "&order=id.desc&limit=100"
    )
    policies = get("/whatsapp_outbound_policy?select=state,state_reason,updated_at&limit=1")
    policy = policies[0] if policies else None
    actionable_stuck, historical_stuck = split_actionable_whatsapp(stuck, policy)
    actionable_errors, historical_errors = split_actionable_whatsapp(errors, policy)

    emails = get(
        "/email_logistica_log?status=eq.PENDING"
        "&select=id,pedido_id,recipient,status,created_at,updated_at,error_message"
        "&order=id.desc&limit=100"
    )
    email_stuck = stale_email_rows(
        emails, EMAIL_NOTIFICATIONS_ENABLED, EMAIL_PENDING_MAX_MINUTES
    )

    if historical_stuck or historical_errors:
        print(
            "ACKNOWLEDGED HISTORICAL WHATSAPP:",
            {
                "policy_state": policy.get("state") if policy else None,
                "baseline_at": policy.get("updated_at") if policy else None,
                "stuck_ids": [row.get("id") for row in historical_stuck],
                "error_ids": [row.get("id") for row in historical_errors],
            },
        )
    if emails and not EMAIL_NOTIFICATIONS_ENABLED:
        print(f"EMAIL DISABLED: preserved pending rows={len(emails)}")
    if actionable_stuck:
        failures.append(f"stuck WhatsApp rows={len(actionable_stuck)}")
        print("STUCK WHATSAPP:", actionable_stuck)
    if actionable_errors:
        failures.append(f"ERROR WhatsApp rows={len(actionable_errors)}")
        print("ERROR WHATSAPP:", actionable_errors)
    if email_stuck:
        failures.append(f"stuck email rows={len(email_stuck)}")
        print("STUCK EMAIL:", email_stuck)
    if failures:
        print("CRITICAL:", " | ".join(failures))
        return 1
    print("OK: no new actionable stuck rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
