#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, Literal, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

ADAPTER_HOST = os.environ.get("ADAPTER_HOST", "127.0.0.1")
ADAPTER_PORT = int(os.environ.get("ADAPTER_PORT", "8792"))

HOOK_TOKEN = os.environ.get("HOOK_TOKEN", "RESTRICTED")
REQUIRE_HOOK_TOKEN = os.environ.get("REQUIRE_HOOK_TOKEN", "true").lower() == "true"

OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
OPENCLAW_CHANNEL = os.environ.get("OPENCLAW_CHANNEL", "whatsapp")
OPENCLAW_ACCOUNT = os.environ.get("OPENCLAW_ACCOUNT", "").strip()
OPENCLAW_TIMEOUT = int(os.environ.get("OPENCLAW_TIMEOUT", "90"))

WHATSAPP_TRANSPORT = os.environ.get("WHATSAPP_TRANSPORT", "openclaw").strip().lower()
META_GRAPH_API_BASE = os.environ.get("META_GRAPH_API_BASE", "https://graph.facebook.com").rstrip("/")
META_GRAPH_API_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v23.0").strip()
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "").strip()
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
META_REQUEST_TIMEOUT = int(os.environ.get("META_REQUEST_TIMEOUT", "30"))

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Replau WhatsApp Send Adapter", version="2.0.0")


class ReplyButton(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1, max_length=256)
    title: str = Field(..., min_length=1, max_length=20)


class InteractiveButtons(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(..., min_length=1, max_length=1024)
    buttons: list[ReplyButton] = Field(..., min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_button_ids(self) -> "InteractiveButtons":
        ids = [button.id for button in self.buttons]
        if len(ids) != len(set(ids)):
            raise ValueError("interactive button ids must be unique")
        return self


class TemplateMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., pattern=r"^[a-z0-9_]{1,512}$")
    language: str = Field(..., pattern=r"^[A-Za-z]{2,3}([_-][A-Za-z0-9]{2,8})?$")
    category: Literal["UTILITY", "MARKETING", "AUTHENTICATION"]
    components: list[Dict[str, Any]] = Field(default_factory=list, max_length=10)


class StructuredMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    type: Literal["text", "interactive_buttons", "template"]
    fallback_text: str = Field(..., min_length=1, max_length=4096)
    interactive: Optional[InteractiveButtons] = None
    template: Optional[TemplateMessage] = None

    @model_validator(mode="after")
    def fields_match_type(self) -> "StructuredMessage":
        if self.type == "interactive_buttons" and self.interactive is None:
            raise ValueError("interactive payload is required for interactive_buttons")
        if self.type == "template" and self.template is None:
            raise ValueError("template payload is required for template")
        if self.type == "text" and (self.interactive is not None or self.template is not None):
            raise ValueError("text payload cannot include interactive or template fields")
        if self.type != "interactive_buttons" and self.interactive is not None:
            raise ValueError("interactive payload is only valid for interactive_buttons")
        if self.type != "template" and self.template is not None:
            raise ValueError("template payload is only valid for template")
        return self


class WhatsAppSendPayload(BaseModel):
    whatsapp_number: str = Field(..., description="E.164 number like +51999999999, or digits like 51999999999")
    message_text: Optional[str] = Field(default=None, max_length=4096, description="Safe text fallback")
    message_payload: Optional[StructuredMessage] = None
    event_type: Optional[str] = None
    pedido_id: Optional[int] = None
    outbox_id: Optional[int] = None
    reply_to_message_id: Optional[str] = Field(default=None, max_length=512)
    dry_run: bool = False

    @model_validator(mode="after")
    def require_consistent_fallback(self) -> "WhatsAppSendPayload":
        fallback = (self.message_payload.fallback_text if self.message_payload else self.message_text or "").strip()
        if not fallback:
            raise ValueError("message_text or message_payload.fallback_text is required")
        if self.message_text and self.message_payload and self.message_text.strip() != self.message_payload.fallback_text.strip():
            raise ValueError("message_text must equal message_payload.fallback_text")
        return self


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


def requested_message_type(payload: WhatsAppSendPayload) -> str:
    return payload.message_payload.type if payload.message_payload else "text"


def fallback_text(payload: WhatsAppSendPayload) -> str:
    return (payload.message_payload.fallback_text if payload.message_payload else payload.message_text or "").strip()


def build_meta_message_body(payload: WhatsAppSendPayload, target: str) -> Dict[str, Any]:
    target_digits = re.sub(r"\D", "", target)
    base: Dict[str, Any] = {"messaging_product": "whatsapp", "to": target_digits}
    structured = payload.message_payload
    if structured is None or structured.type == "text":
        return {**base, "type": "text", "text": {"body": fallback_text(payload)}}
    if structured.type == "interactive_buttons":
        assert structured.interactive is not None
        return {
            **base,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": structured.interactive.body},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": button.id, "title": button.title}}
                        for button in structured.interactive.buttons
                    ]
                },
            },
        }
    assert structured.template is not None
    template: Dict[str, Any] = {
        "name": structured.template.name,
        "language": {"code": structured.template.language},
    }
    if structured.template.components:
        template["components"] = structured.template.components
    return {**base, "type": "template", "template": template}


def meta_api_call(body: Dict[str, Any]) -> Dict[str, Any]:
    if not META_PHONE_NUMBER_ID or not META_ACCESS_TOKEN:
        raise HTTPException(status_code=503, detail="Meta Cloud API transport is not configured")
    request = Request(
        f"{META_GRAPH_API_BASE}/{META_GRAPH_API_VERSION}/{META_PHONE_NUMBER_ID}/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=META_REQUEST_TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        detail = raw[:1000] or str(exc)
        raise HTTPException(status_code=502, detail={"error": "Meta Cloud API request failed", "response": detail})
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail={"error": "Meta Cloud API request failed", "response": str(exc)})
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": True, "text": raw}


@app.get("/health")
def health() -> Dict[str, Any]:
    cmd = openclaw_command_available()
    transport_ok = WHATSAPP_TRANSPORT in {"openclaw", "meta_cloud_api"}
    if WHATSAPP_TRANSPORT == "openclaw":
        transport_ok = transport_ok and cmd["ok"]
        capabilities = ["text", "structured_fallback"]
    else:
        transport_ok = transport_ok and bool(META_PHONE_NUMBER_ID and META_ACCESS_TOKEN)
        capabilities = ["text", "interactive_buttons", "template", "read", "typing_indicator"]

    return {
        "ok": transport_ok,
        "adapter": "replau-whatsapp-send-adapter",
        "transport": WHATSAPP_TRANSPORT,
        "capabilities": capabilities,
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

    if WHATSAPP_TRANSPORT not in {"openclaw", "meta_cloud_api"}:
        raise HTTPException(status_code=503, detail=f"Unsupported WHATSAPP_TRANSPORT: {WHATSAPP_TRANSPORT}")

    message = fallback_text(payload)
    message_type = requested_message_type(payload)

    if WHATSAPP_TRANSPORT == "meta_cloud_api":
        if payload.dry_run:
            return JSONResponse({
                "ok": True,
                "dry_run": True,
                "transport": WHATSAPP_TRANSPORT,
                "requested_message_type": message_type,
                "request_body": build_meta_message_body(payload, target),
            })
        if payload.reply_to_message_id:
            meta_api_call({
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": payload.reply_to_message_id,
                "typing_indicator": {"type": "text"},
            })
        result = meta_api_call(build_meta_message_body(payload, target))
        return JSONResponse({
            "ok": True,
            "transport": WHATSAPP_TRANSPORT,
            "requested_message_type": message_type,
            "delivery_mode": "native",
            "response": result,
        })

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
        "transport": WHATSAPP_TRANSPORT,
        "requested_message_type": message_type,
        "delivery_mode": "native" if message_type == "text" else "fallback_text",
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
