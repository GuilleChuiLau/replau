#!/usr/bin/env python3
from __future__ import annotations

import os
import time
import smtplib
import traceback
import logging
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List

import requests


POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USERNAME)
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "false").lower() == "true"

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "20"))
BATCH_LIMIT = int(os.environ.get("BATCH_LIMIT", "10"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))

EMAIL_DRY_RUN = os.environ.get("EMAIL_DRY_RUN", "true").lower() == "true"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(message)s",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def pg_url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return POSTGREST_BASE_URL + path


def get_pending_emails() -> List[Dict[str, Any]]:
    url = (
        pg_url("/email_logistica_log")
        + "?status=eq.PENDING"
        + "&select=id,pedido_id,recipient,subject,body,created_at"
        + "&order=created_at.asc"
        + f"&limit={BATCH_LIMIT}"
    )

    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def patch_email_status(email_id: int, payload: Dict[str, Any]) -> Any:
    url = pg_url(f"/email_logistica_log?id=eq.{email_id}")

    response = requests.patch(
        url,
        headers={
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()
    return response.json()


def send_email(recipient: str, subject: str, body: str) -> None:
    if not SMTP_HOST:
        raise RuntimeError("SMTP_HOST is not configured")

    if not SMTP_FROM:
        raise RuntimeError("SMTP_FROM is not configured")

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    if EMAIL_DRY_RUN:
        logging.info("========== DRY RUN EMAIL ==========")
        logging.info("To: %s", recipient)
        logging.info("Subject: %s", subject)
        logging.info("\n%s", body)
        logging.info("===================================")
        return

    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=REQUEST_TIMEOUT) as server:
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=REQUEST_TIMEOUT) as server:
            server.ehlo()

            if SMTP_USE_TLS:
                server.starttls()
                server.ehlo()

            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)

            server.send_message(msg)


def process_one(email_row: Dict[str, Any]) -> None:
    email_id = email_row["id"]
    recipient = email_row["recipient"]
    subject = email_row["subject"]
    body = email_row["body"]

    logging.info("Processing email_logistica_log id=%s to=%s", email_id, recipient)

    try:
        send_email(recipient, subject, body)

        if EMAIL_DRY_RUN:
            logging.info("DRY RUN complete. Email id=%s left as PENDING.", email_id)
            return

        patch_email_status(
            email_id,
            {
                "status": "SENT",
                "sent_at": utc_now_iso(),
                "error_message": None,
            },
        )

        logging.info("SENT email id=%s", email_id)

    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        logging.error("ERROR email id=%s: %s", email_id, error_text)
        traceback.print_exc()

        try:
            patch_email_status(
                email_id,
                {
                    "status": "ERROR",
                    "error_message": error_text[:1000],
                },
            )
        except Exception:
            logging.exception("Could not update email id=%s to ERROR", email_id)


def run_once() -> None:
    emails = get_pending_emails()

    if not emails:
        logging.info("No pending emails.")
        return

    logging.info("Found %s pending email(s).", len(emails))

    for email_row in emails:
        process_one(email_row)


def run_forever() -> None:
    logging.info("Replau email worker started.")
    logging.info("POSTGREST_BASE_URL=%s", POSTGREST_BASE_URL)
    logging.info("EMAIL_DRY_RUN=%s", EMAIL_DRY_RUN)
    logging.info("POLL_SECONDS=%s", POLL_SECONDS)

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
