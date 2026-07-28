#!/usr/bin/env python3
"""Apply bounded privacy retention to the private WhatsApp request queue."""

from __future__ import annotations

import json
import os
import sys
import time

import requests


def post_with_startup_retry(url: str, payload: dict) -> requests.Response:
    attempts = int(os.environ.get("RETENTION_HTTP_ATTEMPTS", "6"))
    delay = float(os.environ.get("RETENTION_HTTP_RETRY_SECONDS", "5"))
    if attempts < 1 or attempts > 12:
        raise ValueError("RETENTION_HTTP_ATTEMPTS must be between 1 and 12")
    response = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(url, json=payload, timeout=30)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts:
                raise
            time.sleep(delay)
            continue
        if response.status_code < 500:
            response.raise_for_status()
            return response
        try:
            response.raise_for_status()
        except requests.HTTPError:
            if attempt == attempts:
                raise
            time.sleep(delay)
    raise RuntimeError("retention HTTP retry loop exhausted")


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
    staff_response = post_with_startup_retry(
        f"{base_url}/rpc/apply_whatsapp_staff_reply_retention",
        {"p_active_redact_days": active_days, "p_closed_redact_days": closed_days},
    )
    staff_result = staff_response.json()
    if not isinstance(staff_result, dict) or staff_result.get("ok") is not True:
        raise RuntimeError("staff reply retention RPC returned an invalid result")
    response = post_with_startup_retry(
        f"{base_url}/rpc/apply_whatsapp_conversation_request_retention",
        {
            "p_active_redact_days": active_days,
            "p_closed_redact_days": closed_days,
            "p_delete_days": delete_days,
        },
    )
    result = response.json()
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("retention RPC returned an invalid result")
    return {**result, **staff_result}


if __name__ == "__main__":
    try:
        print(json.dumps(run(), sort_keys=True))
    except Exception as exc:
        print(f"conversation request retention failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
