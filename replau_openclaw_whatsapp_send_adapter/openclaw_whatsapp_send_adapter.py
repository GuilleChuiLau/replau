#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

ADAPTER_HOST = os.environ.get("ADAPTER_HOST", "127.0.0.1")
ADAPTER_PORT = int(os.environ.get("ADAPTER_PORT", "8792"))

HOOK_TOKEN = os.environ.get("HOOK_TOKEN", "RESTRICTED")
REQUIRE_HOOK_TOKEN = os.environ.get("REQUIRE_HOOK_TOKEN", "true").lower() == "true"

OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
OPENCLAW_CHANNEL = os.environ.get("OPENCLAW_CHANNEL", "whatsapp")
OPENCLAW_ACCOUNT = os.environ.get("OPENCLAW_ACCOUNT", "").strip()
OPENCLAW_TIMEOUT = int(os.environ.get("OPENCLAW_TIMEOUT", "90"))

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Replau OpenClaw WhatsApp Send Adapter", version="1.0.0")


class WhatsAppSendPayload(BaseModel):
    whatsapp_number: str = Field(..., description="E.164 number like +51999999999, or digits like 51999999999")
    message_text: str = Field(..., description="Message to send")
    event_type: Optional[str] = None
    pedido_id: Optional[int] = None
    outbox_id: Optional[int] = None
    dry_run: bool = False


def normalize_target(value: str) -> str:
    target = (value or "").strip()

    if not target:
        raise ValueError("whatsapp_number is required")

    if "@" in target or target.startswith("whatsapp:"):
        return target

    if target.startswith("+"):
        digits = "+" + re.sub(r"\D", "", target[1:])
        if len(digits) < 9:
            raise ValueError(f"Invalid E.164 target: {value}")
        return digits

    digits = re.sub(r"\D", "", target)
    if len(digits) < 8:
        raise ValueError(f"Invalid WhatsApp number: {value}")

    return "+" + digits


def check_token(x_hook_token: Optional[str]) -> None:
    if not REQUIRE_HOOK_TOKEN:
        return

    if not x_hook_token or x_hook_token != HOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Hook-Token")


def openclaw_command_available() -> Dict[str, Any]:
    resolved = shutil.which(OPENCLAW_BIN)

    if not resolved:
        return {
            "ok": False,
            "openclaw_bin": OPENCLAW_BIN,
            "resolved": None,
            "error": "openclaw binary not found in PATH",
        }

    return {
        "ok": True,
        "openclaw_bin": OPENCLAW_BIN,
        "resolved": resolved,
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    cmd = openclaw_command_available()

    return {
        "ok": cmd["ok"],
        "adapter": "replau-openclaw-whatsapp-send-adapter",
        "openclaw": cmd,
        "channel": OPENCLAW_CHANNEL,
        "account": OPENCLAW_ACCOUNT or None,
        "require_hook_token": REQUIRE_HOOK_TOKEN,
    }


@app.post("/send/whatsapp")
def send_whatsapp(
    payload: WhatsAppSendPayload,
    x_hook_token: Optional[str] = Header(default=None, alias="X-Hook-Token"),
) -> JSONResponse:
    check_token(x_hook_token)

    try:
        target = normalize_target(payload.whatsapp_number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    message = payload.message_text.strip()

    if not message:
        raise HTTPException(status_code=400, detail="message_text is required")

    cmd = [
        OPENCLAW_BIN,
        "message",
        "send",
        "--channel",
        OPENCLAW_CHANNEL,
        "--target",
        target,
        "--message",
        message,
        "--json",
    ]

    if OPENCLAW_ACCOUNT:
        cmd.extend(["--account", OPENCLAW_ACCOUNT])

    if payload.dry_run:
        cmd.append("--dry-run")

    logging.info(
        "Sending WhatsApp via OpenClaw target=%s event_type=%s pedido_id=%s outbox_id=%s dry_run=%s",
        target,
        payload.event_type,
        payload.pedido_id,
        payload.outbox_id,
        payload.dry_run,
    )

    try:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=OPENCLAW_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail={
                "ok": False,
                "error": "OpenClaw command timed out",
                "timeout_seconds": OPENCLAW_TIMEOUT,
            },
        )

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()

    parsed_stdout: Any = None
    if stdout:
        try:
            parsed_stdout = json.loads(stdout)
        except Exception:
            parsed_stdout = stdout

    ok = completed.returncode == 0

    result = {
        "ok": ok,
        "returncode": completed.returncode,
        "target": target,
        "event_type": payload.event_type,
        "pedido_id": payload.pedido_id,
        "outbox_id": payload.outbox_id,
        "stdout": parsed_stdout,
        "stderr": stderr,
        "cmd": [
            OPENCLAW_BIN,
            "message",
            "send",
            "--channel",
            OPENCLAW_CHANNEL,
            "--target",
            target,
            "--message",
            "[redacted]",
            "--json",
        ],
    }

    if ok:
        return JSONResponse(result)

    raise HTTPException(status_code=502, detail=result)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("openclaw_whatsapp_send_adapter:app", host=ADAPTER_HOST, port=ADAPTER_PORT, reload=False)
