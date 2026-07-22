#!/usr/bin/env python3
"""Apply bounded privacy retention to the private WhatsApp request queue."""

from __future__ import annotations

import json
import os
import sys

import requests


def retention_days(name: str, default: int, minimum: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < minimum or value > 3650:
        raise ValueError(f"{name} must be between {minimum} and 3650")
    return value


def run() -> dict:
    base_url = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
    active_days = retention_days("WHATSAPP_REQUEST_ACTIVE_REDACT_DAYS", 30, 7)
    closed_days = retention_days("WHATSAPP_REQUEST_CLOSED_REDACT_DAYS", 7, 1)
    delete_days = retention_days("WHATSAPP_REQUEST_DELETE_DAYS", 90, 30)
    if delete_days <= closed_days:
        raise ValueError("WHATSAPP_REQUEST_DELETE_DAYS must exceed WHATSAPP_REQUEST_CLOSED_REDACT_DAYS")
    response = requests.post(
        f"{base_url}/rpc/apply_whatsapp_conversation_request_retention",
        json={
            "p_active_redact_days": active_days,
            "p_closed_redact_days": closed_days,
            "p_delete_days": delete_days,
        },
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("retention RPC returned an invalid result")
    return result


if __name__ == "__main__":
    try:
        print(json.dumps(run(), sort_keys=True))
    except Exception as exc:
        print(f"conversation request retention failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
