#!/usr/bin/env python3
"""
Replau OpenClaw WhatsApp Bridge

Receives WhatsApp/OpenClaw webhook messages, talks to PostgREST, manages
conversation state, and returns reply_text for OpenClaw to send back.
"""
from __future__ import annotations

import os
import re
import json
import logging
import time
import unicodedata
import base64
import hashlib
import mimetypes
import uuid
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import parse_qs, quote, urlparse

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

POSTGREST_BASE_URL = os.environ.get("POSTGREST_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
PUBLIC_ORDER_BASE_URL = os.environ.get("PUBLIC_ORDER_BASE_URL", POSTGREST_BASE_URL).rstrip("/")
HOOK_TOKEN = os.environ.get("OPENCLAW_HOOK_TOKEN", "RESTRICTED")
REQUIRE_HOOK_TOKEN = os.environ.get("REQUIRE_HOOK_TOKEN", "true").lower() == "true"
DEFAULT_DELIVERY = float(os.environ.get("DEFAULT_DELIVERY", "0"))
MENU_URL = os.environ.get("MENU_URL", "").strip()
CHANNEL_KIND = os.environ.get("REPLAU_CHANNEL_KIND", "whatsapp").strip() or "whatsapp"
CHANNEL_ID = os.environ.get("REPLAU_CHANNEL_ID", "replau-main").strip() or "replau-main"
CHANNEL_ACCOUNT_ID = os.environ.get("REPLAU_CHANNEL_ACCOUNT_ID", "").strip() or None
GEOCODER_PROVIDER = os.environ.get("GEOCODER_PROVIDER", "nominatim").lower()
NOMINATIM_USER_AGENT = os.environ.get("NOMINATIM_USER_AGENT", "replau-openclaw-whatsapp-bridge/1.0")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_GEOCODING_API_KEY")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))
BLOCKLIST_PATH = Path(os.environ.get("WHATSAPP_BLOCKLIST_PATH", "/home/guill/.openclaw/workspace/blocked_whatsapp_numbers.json"))
ABUSE_LEXICON_PATH = Path(os.environ.get("ABUSE_LEXICON_PATH", "/home/guill/.openclaw/workspace/abuse_lexicon_en_es.json"))
DELIVERY_PAYOUTS_PATH = Path(os.environ.get("REPLAU_DELIVERY_PAYOUTS_PATH", "/home/guill/.openclaw/workspace/replau_delivery_payouts.json"))
RESTAURANT_STATUS_PATH = Path(os.environ.get("REPLAU_RESTAURANT_STATUS_PATH", "/home/guill/.openclaw/workspace/replau_restaurant_status.json"))
HUMAN_HANDOFF_PATH = Path(os.environ.get("REPLAU_HUMAN_HANDOFF_PATH", "/home/guill/.openclaw/workspace/replau_human_handoff.json"))
PAYMENT_RECEIPT_DIR = Path(os.environ.get("PAYMENT_RECEIPT_DIR", "/home/guill/.openclaw/workspace/replau_payment_receipts"))
INBOUND_MEDIA_DIR = Path(os.environ.get("OPENCLAW_INBOUND_MEDIA_DIR", "/home/guill/.openclaw/media/inbound"))
MAX_RECEIPT_BYTES = int(os.environ.get("MAX_PAYMENT_RECEIPT_BYTES", str(10 * 1024 * 1024)))
WHATSAPP_RATE_LIMIT_BURST = int(os.environ.get("WHATSAPP_RATE_LIMIT_BURST", "6"))
WHATSAPP_RATE_LIMIT_BURST_SECONDS = int(os.environ.get("WHATSAPP_RATE_LIMIT_BURST_SECONDS", "10"))
WHATSAPP_RATE_LIMIT_MINUTE = int(os.environ.get("WHATSAPP_RATE_LIMIT_MINUTE", "12"))
WHATSAPP_RATE_LIMIT_REPEAT = int(os.environ.get("WHATSAPP_RATE_LIMIT_REPEAT", "4"))
WHATSAPP_RATE_LIMIT_REPEAT_SECONDS = int(os.environ.get("WHATSAPP_RATE_LIMIT_REPEAT_SECONDS", "30"))
WHATSAPP_RATE_LIMIT_MAX_SENDERS = int(os.environ.get("WHATSAPP_RATE_LIMIT_MAX_SENDERS", "10000"))
ABUSE_MESSAGE = "Tu número ha sido bloqueado por lenguaje ofensivo o comportamiento inapropiado. No podremos seguir atendiendo pedidos desde este número."
RATE_LIMIT_MESSAGE = "Recibimos muchos mensajes seguidos. Espera un momento y vuelve a intentarlo, por favor."
DEFAULT_RESTAURANT_CLOSED_MESSAGE = "Estamos cerrados temporalmente. Escríbenos más tarde para hacer tu pedido."
DEFAULT_ABUSE_PATTERNS = [
    (re.compile(r"\b(?:puta|puto|mierda|carajo|cojud[oa]|idiot[ae]|imbecil|imbecile|pendej[oa]|maldit[oa]|hij[oa]\s+de\s+puta|csm|ctm|conchatumadre|fuck|bitch|asshole)\b", re.IGNORECASE), "abusive_language"),
    (re.compile(r"\b(?:te\s+voy\s+a\s+matar|los\s+voy\s+a\s+matar|te\s+rompo|te\s+golpeo|los\s+quemo|te\s+hago\s+dano|i\s+will\s+kill\s+you|i\s+will\s+hurt\s+you)\b", re.IGNORECASE), "threatening_language"),
    (re.compile(r"\b(?:acoso|sexual|desnuda|desnudo|sexo|porn|porno|send\s+nudes)\b", re.IGNORECASE), "sexual_inappropriate_content"),
]

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)

app = FastAPI(title="Replau OpenClaw WhatsApp Bridge", version="1.0.0")

_RATE_LIMIT_LOCK = Lock()
_RATE_LIMIT_EVENTS: Dict[str, deque[Tuple[float, str]]] = defaultdict(deque)


def restaurant_status() -> Dict[str, Any]:
    default = {
        "accepting_orders": True,
        "reason": "",
        "customer_message": DEFAULT_RESTAURANT_CLOSED_MESSAGE,
        "updated_at": None,
        "updated_by": "system",
    }
    try:
        if RESTAURANT_STATUS_PATH.exists():
            data = json.loads(RESTAURANT_STATUS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**default, **data}
    except Exception as exc:
        logging.warning("Could not read restaurant status file %s: %s", RESTAURANT_STATUS_PATH, exc)
        return {**default, "accepting_orders": False, "reason": f"status file error: {exc}"}
    return default


def load_human_handoffs() -> Dict[str, Any]:
    try:
        if HUMAN_HANDOFF_PATH.exists():
            data = json.loads(HUMAN_HANDOFF_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                entries = data.get("entries")
                return entries if isinstance(entries, dict) else data
    except Exception as exc:
        logging.warning("Could not read human handoff file %s: %s", HUMAN_HANDOFF_PATH, exc)
    return {}


def active_handoff_entry(identity: IdentityLike) -> Optional[Dict[str, Any]]:
    entry = load_human_handoffs().get(legacy_whatsapp_number(identity))
    if isinstance(entry, dict) and entry.get("active", True):
        return entry
    return None


def ordering_is_paused() -> bool:
    return not bool(restaurant_status().get("accepting_orders", True))


def restaurant_closed_reply_text() -> str:
    status = restaurant_status()
    return str(status.get("customer_message") or DEFAULT_RESTAURANT_CLOSED_MESSAGE).strip()


@dataclass(frozen=True)
class ConversationIdentity:
    """Transport/channel identity seam.

    Phase 2 keeps live persistence keyed by customer_address/whatsapp_number,
    but all new channel-aware code should pass this object instead of a raw
    phone string. Phase 3 can switch helpers to composite DB keys without
    changing ordering handlers.
    """

    channel_kind: str
    channel_id: str
    customer_address: str
    account_id: Optional[str] = None

    @property
    def legacy_whatsapp_number(self) -> str:
        return str(self.customer_address).strip()

    def as_metadata(self) -> Dict[str, Any]:
        return {
            "channel_kind": self.channel_kind,
            "channel_id": self.channel_id,
            "account_id": self.account_id,
            "customer_address": self.customer_address,
        }


IdentityLike = Union[str, ConversationIdentity]
ACTIVE_CONVERSATION_IDENTITY: ContextVar[Optional[ConversationIdentity]] = ContextVar(
    "replau_active_conversation_identity", default=None
)


def legacy_whatsapp_number(identity: IdentityLike) -> str:
    if isinstance(identity, ConversationIdentity):
        return identity.legacy_whatsapp_number
    return str(identity).strip()


def scoped_identity(identity: IdentityLike) -> ConversationIdentity:
    if isinstance(identity, ConversationIdentity):
        return identity
    active = ACTIVE_CONVERSATION_IDENTITY.get()
    if active and active.customer_address == str(identity).strip():
        return active
    return ConversationIdentity(CHANNEL_KIND, CHANNEL_ID, str(identity).strip(), CHANNEL_ACCOUNT_ID)


def conversation_identity_from_inbound(inbound: "NormalizedWebhook") -> ConversationIdentity:
    return ConversationIdentity(
        channel_kind=(inbound.channel_kind or CHANNEL_KIND or "whatsapp").strip(),
        channel_id=(inbound.channel_id or CHANNEL_ID or "replau-main").strip(),
        account_id=(inbound.account_id or CHANNEL_ACCOUNT_ID),
        customer_address=(inbound.customer_address or inbound.whatsapp_number).strip(),
    )


def inbound_rate_limit_reason(inbound: "NormalizedWebhook", now: Optional[float] = None) -> Optional[str]:
    """Return the matched limit without persisting or mutating conversation state."""
    identity = conversation_identity_from_inbound(inbound)
    key = f"{identity.channel_kind}:{identity.channel_id}:{identity.customer_address}"
    timestamp = time.monotonic() if now is None else now
    fingerprint = hashlib.sha256(
        f"{inbound.message_type}:{normalize_loose_text(inbound.message_text or '')}".encode("utf-8")
    ).hexdigest()
    longest_window = max(WHATSAPP_RATE_LIMIT_BURST_SECONDS, 60, WHATSAPP_RATE_LIMIT_REPEAT_SECONDS)

    with _RATE_LIMIT_LOCK:
        if key not in _RATE_LIMIT_EVENTS and len(_RATE_LIMIT_EVENTS) >= WHATSAPP_RATE_LIMIT_MAX_SENDERS:
            stale_keys = [
                sender_key
                for sender_key, sender_events in _RATE_LIMIT_EVENTS.items()
                if not sender_events or timestamp - sender_events[-1][0] >= longest_window
            ]
            for sender_key in stale_keys:
                _RATE_LIMIT_EVENTS.pop(sender_key, None)
            while len(_RATE_LIMIT_EVENTS) >= WHATSAPP_RATE_LIMIT_MAX_SENDERS:
                _RATE_LIMIT_EVENTS.pop(next(iter(_RATE_LIMIT_EVENTS)))
        events = _RATE_LIMIT_EVENTS[key]
        while events and timestamp - events[0][0] >= longest_window:
            events.popleft()
        burst_count = sum(timestamp - seen_at < WHATSAPP_RATE_LIMIT_BURST_SECONDS for seen_at, _ in events)
        minute_count = sum(timestamp - seen_at < 60 for seen_at, _ in events)
        repeat_count = sum(
            timestamp - seen_at < WHATSAPP_RATE_LIMIT_REPEAT_SECONDS and seen_fingerprint == fingerprint
            for seen_at, seen_fingerprint in events
        )
        events.append((timestamp, fingerprint))

        if repeat_count >= WHATSAPP_RATE_LIMIT_REPEAT:
            return "repeated_message"
        if burst_count >= WHATSAPP_RATE_LIMIT_BURST:
            return "burst"
        if minute_count >= WHATSAPP_RATE_LIMIT_MINUTE:
            return "minute"
        return None


class NormalizedWebhook(BaseModel):
    whatsapp_number: str = Field(..., description="Customer WhatsApp number, e.g. 51999999999")
    message_type: str = Field("text", description="text, location, image, or document")
    message_text: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    media_url: Optional[str] = None
    media_base64: Optional[str] = None
    media_filename: Optional[str] = None
    media_mime_type: Optional[str] = None
    channel_kind: Optional[str] = None
    channel_id: Optional[str] = None
    account_id: Optional[str] = None
    customer_address: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json_file(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("Could not load JSON file %s: %s", path, exc)
    return default


def load_blocklist() -> Dict[str, Any]:
    return load_json_file(BLOCKLIST_PATH, {})


def save_blocklist(data: Dict[str, Any]) -> None:
    try:
        BLOCKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        BLOCKLIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        logging.warning("Could not save blocklist %s: %s", BLOCKLIST_PATH, exc)


def compile_abuse_patterns_from_lexicon() -> List[Tuple[re.Pattern[str], str]]:
    lexicon = load_json_file(ABUSE_LEXICON_PATH, {})
    categories = lexicon.get("categories") or []
    compiled: List[Tuple[re.Pattern[str], str]] = []
    for category in categories:
        key = str(category.get("key") or "unknown")
        terms = [normalize_loose_text(term) for term in (category.get("terms") or []) if str(term).strip()]
        phrases = [normalize_loose_text(phrase) for phrase in (category.get("phrases") or []) if str(phrase).strip()]
        if terms:
            pattern = r"\b(?:" + "|".join(re.escape(term) for term in sorted(set(terms), key=len, reverse=True)) + r")\b"
            compiled.append((re.compile(pattern, re.IGNORECASE), key))
        for phrase in sorted(set(phrases), key=len, reverse=True):
            flexible = r"\b" + r"\s+".join(re.escape(part) for part in phrase.split()) + r"\b"
            compiled.append((re.compile(flexible, re.IGNORECASE), key))
    return compiled or DEFAULT_ABUSE_PATTERNS


def get_abuse_patterns() -> List[Tuple[re.Pattern[str], str]]:
    patterns = compile_abuse_patterns_from_lexicon()
    return patterns or DEFAULT_ABUSE_PATTERNS


def get_block_entry(identity: IdentityLike) -> Optional[Dict[str, Any]]:
    return load_blocklist().get(legacy_whatsapp_number(identity))


def block_number(identity: IdentityLike, reason: str, sample_text: Optional[str] = None) -> Dict[str, Any]:
    whatsapp_number = legacy_whatsapp_number(identity)
    data = load_blocklist()
    entry = {
        "blocked_at": utc_now_iso(),
        "reason": reason,
        "sample_text": sample_text,
    }
    data[whatsapp_number] = entry
    save_blocklist(data)
    return entry


def pg_url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return POSTGREST_BASE_URL + path


def pg_request(method: str, path: str, **kwargs: Any) -> Any:
    url = pg_url(path)
    last_exc: Optional[Exception] = None
    max_attempts = 8
    base_headers = dict(kwargs.pop("headers", {}) or {})
    for attempt in range(max_attempts):
        try:
            headers = dict(base_headers)
            headers.setdefault("Connection", "close")
            response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
            response.raise_for_status()
            if not response.text.strip():
                return None
            return response.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            logging.warning("Transient PostgREST %s %s failed on attempt %s/%s: %s", method, path, attempt + 1, max_attempts, exc)
            if attempt == max_attempts - 1:
                raise
            time.sleep(min(2.0, 0.25 * (attempt + 1)))
    if last_exc:
        raise last_exc
    raise RuntimeError("PostgREST request failed")


def pg_get(path: str) -> Any:
    return pg_request("GET", path)


def pg_post(path: str, payload: Dict[str, Any]) -> Any:
    return pg_request(
        "POST",
        path,
        headers={"Content-Type": "application/json", "Prefer": "return=representation", "Connection": "close"},
        json=payload,
    )


def pg_patch(path: str, payload: Dict[str, Any]) -> Any:
    return pg_request(
        "PATCH",
        path,
        headers={"Content-Type": "application/json", "Prefer": "return=representation", "Connection": "close"},
        json=payload,
    )


def log_whatsapp_message(
    identity: IdentityLike,
    direction: str,
    message_type: str = "text",
    message_text: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> None:
    scoped = scoped_identity(identity)
    whatsapp_number = scoped.customer_address
    raw_payload = dict(raw_payload or {})
    raw_payload.setdefault("_replau_channel_identity", scoped.as_metadata())
    pg_post(
        "/rpc/registrar_whatsapp_mensaje_canal",
        {
            "p_channel_kind": scoped.channel_kind,
            "p_channel_id": scoped.channel_id,
            "p_account_id": scoped.account_id,
            "p_customer_address": whatsapp_number,
            "p_direction": direction,
            "p_message_type": message_type,
            "p_message_text": message_text,
            "p_latitude": latitude,
            "p_longitude": longitude,
            "p_raw_payload": raw_payload,
        },
    )


def register_conversation_request(inbound: NormalizedWebhook, identity: ConversationIdentity) -> Optional[Dict[str, Any]]:
    """Record a user-initiated direct chat without making ordering depend on the staff queue."""
    raw = inbound.raw_payload or {}
    try:
        result = pg_post(
            "/rpc/register_whatsapp_conversation_request",
            {
                "p_channel_kind": identity.channel_kind,
                "p_channel_id": identity.channel_id,
                "p_account_id": identity.account_id,
                "p_customer_address": identity.customer_address,
                "p_sender_name": raw.get("sender_name"),
                "p_message_text": inbound.message_text,
                "p_provider_message_id": raw.get("message_id"),
            },
        )
        if isinstance(result, dict) and result.get("is_new"):
            logging.info("Registered new user-initiated WhatsApp request id=%s channel=%s", result.get("request_id"), identity.channel_id)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        logging.warning("Could not update WhatsApp conversation request queue: %s", exc)
        return None


def get_conversation(identity: IdentityLike) -> Optional[Dict[str, Any]]:
    scoped = scoped_identity(identity)
    safe_kind, safe_channel, safe_number = (quote(value, safe="") for value in (scoped.channel_kind, scoped.channel_id, scoped.customer_address))
    rows = pg_get(f"/whatsapp_conversation_states?channel_kind=eq.{safe_kind}&channel_id=eq.{safe_channel}&customer_address=eq.{safe_number}&select=*&limit=1")
    return rows[0] if rows else None


def get_customer_by_whatsapp(identity: IdentityLike) -> Optional[Dict[str, Any]]:
    safe_number = quote(legacy_whatsapp_number(identity), safe="")
    rows = pg_get(
        "/clientes_whatsapp"
        f"?whatsapp_number=eq.{safe_number}"
        "&select=id,whatsapp_number,nombre,last_order_num,last_order_total,last_payment_method,"
        "last_latitude,last_longitude,last_written_address,last_detected_address,last_confirmed_address,last_order_snapshot"
        "&limit=1"
    )
    return rows[0] if rows else None


def patch_conversation(identity: IdentityLike, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    scoped = scoped_identity(identity)
    safe_kind, safe_channel, safe_number = (quote(value, safe="") for value in (scoped.channel_kind, scoped.channel_id, scoped.customer_address))
    rows = pg_patch(f"/whatsapp_conversation_states?channel_kind=eq.{safe_kind}&channel_id=eq.{safe_channel}&customer_address=eq.{safe_number}", payload)
    return rows[0] if rows else None


def first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def customer_tracking_url(order_url: Any) -> str:
    text = str(order_url or "").strip()
    if not text:
        return ""
    return text.replace("/order/", "/track/")


LOCATION_COORD_RE = re.compile(r"(?:📍|location:|ubicaci[oó]n(?: enviada)?[: ]*)?\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)", re.IGNORECASE)


def _walk_dicts(value: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_dicts(child))
    return found


def extract_media_fields(data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Extract common OpenClaw/WhatsApp attachment fields without assuming one provider shape."""
    result: Dict[str, Optional[str]] = {
        "media_url": first_non_empty(data.get("media_url"), data.get("file_url"), data.get("url"), data.get("download_url")),
        "media_base64": first_non_empty(data.get("media_base64"), data.get("base64"), data.get("data_base64")),
        "media_filename": first_non_empty(data.get("media_filename"), data.get("filename"), data.get("file_name"), data.get("name")),
        "media_mime_type": first_non_empty(data.get("media_mime_type"), data.get("mime_type"), data.get("mimetype"), data.get("content_type")),
    }
    for obj in _walk_dicts(data):
        if not any(result.values()):
            pass
        if not result["media_url"]:
            result["media_url"] = first_non_empty(obj.get("media_url"), obj.get("file_url"), obj.get("url"), obj.get("download_url"), obj.get("link"))
        if not result["media_base64"]:
            result["media_base64"] = first_non_empty(obj.get("media_base64"), obj.get("base64"), obj.get("data_base64"), obj.get("data"))
        if not result["media_filename"]:
            result["media_filename"] = first_non_empty(obj.get("filename"), obj.get("file_name"), obj.get("name"))
        if not result["media_mime_type"]:
            result["media_mime_type"] = first_non_empty(obj.get("mime_type"), obj.get("mimetype"), obj.get("content_type"))
        if obj.get("type") in {"image", "document", "audio", "video"} and not result["media_mime_type"]:
            if obj.get("type") == "image":
                result["media_mime_type"] = "image/jpeg"
    return result


def is_media_marker(text: Optional[str]) -> bool:
    return bool(text and re.search(r"<\s*media\s*:\s*(?:image|document|file)\s*>", str(text), re.IGNORECASE))


def find_latest_inbound_media(max_age_seconds: int = 600) -> Optional[Path]:
    try:
        if not INBOUND_MEDIA_DIR.exists():
            return None
        allowed = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
        now = time.time()
        candidates = [
            path for path in INBOUND_MEDIA_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in allowed and now - path.stat().st_mtime <= max_age_seconds
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)
    except Exception as exc:
        logging.warning("Could not inspect inbound media dir %s: %s", INBOUND_MEDIA_DIR, exc)
        return None


def is_payment_receipt_media(inbound: NormalizedWebhook) -> bool:
    if inbound.message_type in {"image", "document"}:
        return True
    if inbound.media_url or inbound.media_base64:
        return True
    if is_media_marker(inbound.message_text):
        return True
    return False


def save_payment_receipt(inbound: NormalizedWebhook, draft: Dict[str, Any]) -> Dict[str, Any]:
    PAYMENT_RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    mime_type = inbound.media_mime_type or "application/octet-stream"
    original_name = inbound.media_filename or "payment-receipt"
    ext = (Path(original_name).suffix or mimetypes.guess_extension(mime_type) or "").lower()
    if ext in {".jpe"}:
        ext = ".jpg"
    allowed_exts = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
    if ext not in allowed_exts:
        if mime_type in {"image/jpeg", "image/jpg"}:
            ext = ".jpg"
        elif mime_type == "image/png":
            ext = ".png"
        elif mime_type == "image/webp":
            ext = ".webp"
        elif mime_type == "application/pdf":
            ext = ".pdf"
        else:
            ext = ".bin"
    raw: bytes
    source = "unknown"
    if inbound.media_base64:
        b64 = inbound.media_base64
        if "," in b64 and b64.strip().lower().startswith("data:"):
            b64 = b64.split(",", 1)[1]
        raw = base64.b64decode(b64, validate=False)
        source = "base64"
    elif inbound.media_url:
        if inbound.media_url.startswith("file://"):
            local_path = Path(inbound.media_url[7:])
            raw = local_path.read_bytes()
            source = "file"
        elif inbound.media_url.startswith("/"):
            local_path = Path(inbound.media_url)
            raw = local_path.read_bytes()
            source = "file"
        else:
            response = requests.get(inbound.media_url, timeout=REQUEST_TIMEOUT, headers={"Connection": "close"})
            response.raise_for_status()
            raw = response.content
            source = "url"
            mime_type = response.headers.get("content-type", mime_type).split(";", 1)[0]
    elif is_media_marker(inbound.message_text):
        local_path = find_latest_inbound_media()
        if not local_path:
            raise ValueError("No pude encontrar el archivo subido por WhatsApp todavía")
        raw = local_path.read_bytes()
        source = "openclaw_inbound_media"
        original_name = local_path.name
        guessed = mimetypes.guess_type(str(local_path))[0]
        if guessed:
            mime_type = guessed
        ext = local_path.suffix.lower() or ext
    else:
        raise ValueError("No media data or URL found in receipt message")
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError(f"Payment receipt is too large: {len(raw)} bytes")
    digest = hashlib.sha256(raw).hexdigest()
    customer = re.sub(r"[^0-9A-Za-z_-]+", "", inbound.whatsapp_number)[-15:] or "customer"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{stamp}_{customer}_{uuid.uuid4().hex[:8]}{ext}"
    path = PAYMENT_RECEIPT_DIR / filename
    path.write_bytes(raw)
    meta = {
        "saved": True,
        "path": str(path),
        "filename": filename,
        "original_filename": original_name,
        "mime_type": mime_type,
        "size_bytes": len(raw),
        "sha256": digest,
        "source": source,
        "saved_at": utc_now_iso(),
        "payment_method": draft.get("payment_method"),
    }
    return meta


def register_payment_proof_for_receipt(inbound: NormalizedWebhook, receipt: Dict[str, Any], pedido_id: Optional[int] = None) -> Dict[str, Any]:
    """Persist a saved WhatsApp payment receipt into the payment-proof review workflow."""
    media_type = "document" if str(receipt.get("mime_type") or inbound.media_mime_type or "").lower() == "application/pdf" else "image"
    payload = {
        "p_whatsapp_number": inbound.whatsapp_number,
        "p_media_url": inbound.media_url,
        "p_local_path": receipt.get("path"),
        "p_caption": inbound.message_text or "Comprobante Yape",
        "p_media_type": media_type,
        "p_media_id": None,
        "p_original_filename": receipt.get("original_filename") or receipt.get("filename") or inbound.media_filename,
        "p_pedido_id": pedido_id,
    }
    return pg_post("/rpc/registrar_comprobante_pago_whatsapp", payload)


def parse_location_from_text(message_text: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    if not message_text:
        return None, None
    match = LOCATION_COORD_RE.search(str(message_text))
    if not match:
        return None, None
    try:
        latitude = float(match.group(1))
        longitude = float(match.group(2))
    except Exception:
        return None, None
    if abs(latitude) > 90 or abs(longitude) > 180:
        return None, None
    return latitude, longitude


def extract_payload(data: Dict[str, Any]) -> NormalizedWebhook:
    """Accept normalized payloads and common WhatsApp/OpenClaw-ish nested shapes."""
    whatsapp_number = first_non_empty(
        data.get("customer_address"),
        data.get("whatsapp_number"),
        data.get("from"),
        data.get("wa_id"),
        data.get("phone"),
        data.get("sender"),
        data.get("sender_phone"),
    )
    message_text = first_non_empty(data.get("message_text"), data.get("text"), data.get("body"), data.get("message"))
    message_type = first_non_empty(data.get("message_type"), data.get("type")) or "text"
    media = extract_media_fields(data)
    latitude = data.get("latitude")
    longitude = data.get("longitude")

    contact = data.get("contact") or {}
    if not whatsapp_number:
        whatsapp_number = first_non_empty(contact.get("wa_id"), contact.get("phone"), contact.get("number"))

    msg = data.get("message_obj") or data.get("messageData") or data.get("message_data") or {}
    if isinstance(msg, dict):
        if not message_text:
            nested_text = msg.get("text")
            if isinstance(nested_text, dict):
                nested_text = nested_text.get("body")
            message_text = first_non_empty(msg.get("body"), nested_text)
        if not whatsapp_number:
            whatsapp_number = first_non_empty(msg.get("from"), msg.get("wa_id"), msg.get("phone"))
        loc = msg.get("location") or {}
        if isinstance(loc, dict):
            latitude = latitude if latitude is not None else loc.get("latitude")
            longitude = longitude if longitude is not None else loc.get("longitude")

    try:
        message0 = data["entry"][0]["changes"][0]["value"]["messages"][0]
        if not whatsapp_number:
            whatsapp_number = first_non_empty(message0.get("from"))
        if not message_text and isinstance(message0.get("text"), dict):
            message_text = first_non_empty(message0["text"].get("body"))
        if "location" in message0:
            message_type = "location"
            latitude = latitude if latitude is not None else message0["location"].get("latitude")
            longitude = longitude if longitude is not None else message0["location"].get("longitude")
        for media_kind in ("image", "document"):
            if media_kind in message0 and isinstance(message0[media_kind], dict):
                message_type = media_kind
                media_obj = message0[media_kind]
                message_text = message_text or first_non_empty(media_obj.get("caption"))
                media["media_url"] = media["media_url"] or first_non_empty(media_obj.get("url"), media_obj.get("link"))
                media["media_filename"] = media["media_filename"] or first_non_empty(media_obj.get("filename"))
                media["media_mime_type"] = media["media_mime_type"] or first_non_empty(media_obj.get("mime_type"))
    except Exception:
        pass

    # A web-order handoff can contain a Google Maps URL as one field.  It is
    # still a structured text message; promoting the whole payload to a
    # location discards the customer, items, address, and payment fields.
    first_line = normalize_loose_text(str(message_text or "").splitlines()[0])
    is_structured_web_order = first_line.startswith("pedido web confirmado")
    if latitude is None and longitude is None and not is_structured_web_order:
        parsed_latitude, parsed_longitude = parse_location_from_text(message_text)
        if parsed_latitude is not None and parsed_longitude is not None:
            latitude = parsed_latitude
            longitude = parsed_longitude

    if latitude is not None or longitude is not None:
        message_type = "location"
    elif media.get("media_url") or media.get("media_base64") or str(message_type).strip().lower() in {"image", "document"}:
        message_type = str(message_type or "image").strip().lower()
        if message_type not in {"image", "document"}:
            message_type = "image"
    if not whatsapp_number:
        raise ValueError("Could not extract whatsapp_number from payload")

    return NormalizedWebhook(
        whatsapp_number=str(whatsapp_number).strip(),
        message_type=str(message_type).strip().lower(),
        message_text=message_text,
        latitude=float(latitude) if latitude is not None else None,
        longitude=float(longitude) if longitude is not None else None,
        media_url=media.get("media_url"),
        media_base64=media.get("media_base64"),
        media_filename=media.get("media_filename"),
        media_mime_type=media.get("media_mime_type"),
        raw_payload=data,
    )


UNIT_MAP = {
    "bolsa": "BOLSA", "bolsas": "BOLSA",
    "pote": "POTE", "potes": "POTE",
    "saco": "SACO", "sacos": "SACO",
    "kg": "KG", "kilo": "KG", "kilos": "KG", "kilogramo": "KG", "kilogramos": "KG",
    "lt": "LT", "litro": "LT", "litros": "LT",
    "unidad": "UND", "unidades": "UND", "und": "UND",
    "botella": "BOTELLA", "botellas": "BOTELLA",
    "vaso": "VASO", "vasos": "VASO",
    "lata": "LATA", "latas": "LATA",
    "porcion": "PORCION", "porción": "PORCION", "porciones": "PORCION",
    "paquete": "PAQUETE", "paquetes": "PAQUETE",
    "bidon": "BIDON", "bidón": "BIDON", "bidones": "BIDON",
    "frasco": "FRASCO", "frascos": "FRASCO",
    "envase": "ENVASE", "envases": "ENVASE",
    "caja": "CAJA", "cajas": "CAJA",
}
PRODUCT_TOKEN_MAP = {
    "hamburguesas": "hamburguesa",
    "clasicas": "clasica",
    "clásicas": "clasica",
    "clasicos": "clasico",
    "clásicos": "clasico",
    "gaseosas": "gaseosa",
    "combos": "combo",
    "malteadas": "malteada",
    "porciones": "porcion",
    "porción": "porcion",
    "extras": "extra",
    "pequeña": "pequena",
    "pequeñas": "pequenas",
    "pequeño": "pequeno",
    "pequeños": "pequenos",
    "chicas": "pequenas",
    "chica": "pequena",
    "chicos": "pequenos",
    "chico": "pequeno",
    "medianas": "medianas",
    "mediana": "mediana",
    "grandes": "grandes",
    "grande": "grande",
    "burgers": "hamburguesa",
    "burger": "hamburguesa",
    "hamburger": "hamburguesa",
    "hamburgers": "hamburguesa",
    "single": "simple",
    "double": "doble",
    "triple": "triple",
    "hotwings": "alitas",
    "hotwing": "alitas",
    "cheese": "queso",
    "cheeseburger": "hamburguesa queso",
    "cheeseburgers": "hamburguesa queso",
    "bacon": "tocino",
    "fries": "papas",
    "french": "fritas",
    "wings": "alitas",
    "wing": "alitas",
    "onion": "cebolla",
    "rings": "aros",
    "ring": "aro",
    "small": "pequena",
    "medium": "mediana",
    "large": "grande",
    "coke": "coca cola",
    "cokes": "coca cola",
    "sodas": "gaseosa",
    "soda": "gaseosa",
    "bebida": "gaseosa",
    "bebidas": "gaseosa",
    "refresco": "gaseosa",
    "refrescos": "gaseosa",
    "gaseosas": "gaseosa",
    "gaseosa": "gaseosa",
    "soft": "gaseosa",
    "drink": "gaseosa",
    "drinks": "gaseosa",
    "acompañamiento": "side",
    "acompañamientos": "side",
    "acompanamiento": "side",
    "acompanamientos": "side",
    "guarnicion": "side",
    "guarniciones": "side",
    "orden": "order",
    "ordenes": "order",
    "órdenes": "order",
    "porcion": "order",
    "porciones": "order",
    "side": "side",
    "sides": "side",
    "order": "order",
    "orders": "order",
}
PRODUCT_PHRASE_MAP = {
    "HAMBURGUESA": "HAMBURGUESA SIMPLE",
    "HAMBURGUESAS": "HAMBURGUESA SIMPLE",
    "HAMBURGER": "HAMBURGUESA SIMPLE",
    "HAMBURGERS": "HAMBURGUESA SIMPLE",
    "BURGER": "HAMBURGUESA SIMPLE",
    "BURGERS": "HAMBURGUESA SIMPLE",
    "DOBLE HAMBURGUESA": "HAMBURGUESA DOBLE",
    "TRIPLE HAMBURGUESA": "HAMBURGUESA TRIPLE",
    "QUESO HAMBURGUESA": "HAMBURGUESA SIMPLE CON QUESO",
    "TOCINO HAMBURGUESA QUESO": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "TOCINO QUESO HAMBURGUESA": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "TOCINO AND QUESO HAMBURGUESA": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "TOCINO Y QUESO HAMBURGUESA": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "HAMBURGUESA TOCINO QUESO": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "DOBLE QUESO HAMBURGUESA": "HAMBURGUESA DOBLE CON QUESO",
    "DOBLE TOCINO HAMBURGUESA QUESO": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOBLE TOCINO QUESO HAMBURGUESA": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOBLE TOCINO AND QUESO HAMBURGUESA": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOBLE TOCINO Y QUESO HAMBURGUESA": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "TRIPLE HAMBURGUESA QUESO": "HAMBURGUESA TRIPLE CON QUESO",
    "TRIPLE QUESO HAMBURGUESA": "HAMBURGUESA TRIPLE CON QUESO",
    "TRIPLE TOCINO HAMBURGUESA QUESO": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE TOCINO AND QUESO HAMBURGUESA": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE TOCINO Y QUESO HAMBURGUESA": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "SIMPLE": "HAMBURGUESA SIMPLE",
    "SIMPLES": "HAMBURGUESA SIMPLE",
    "SIMPLE HAMBURGUESA": "HAMBURGUESA SIMPLE",
    "HAMBURGUESA SIMPLE": "HAMBURGUESA SIMPLE",
    "HAMBURGUESA SIMPLES": "HAMBURGUESA SIMPLE",
    "HAMBURGUESAS SIMPLE": "HAMBURGUESA SIMPLE",
    "HAMBURGUESAS SIMPLES": "HAMBURGUESA SIMPLE",
    "SINGLE": "HAMBURGUESA SIMPLE",
    "SINGLE BURGER": "HAMBURGUESA SIMPLE",
    "SINGLE HAMBURGER": "HAMBURGUESA SIMPLE",
    "BURGER SIMPLE": "HAMBURGUESA SIMPLE",
    "HAMBURGER SIMPLE": "HAMBURGUESA SIMPLE",
    "HAMBURGUESA CON QUESO": "HAMBURGUESA SIMPLE CON QUESO",
    "HAMBURGUESA QUESO": "HAMBURGUESA SIMPLE CON QUESO",
    "CHEESEBURGER": "HAMBURGUESA SIMPLE CON QUESO",
    "CHEESE BURGER": "HAMBURGUESA SIMPLE CON QUESO",
    "CHEESE HAMBURGER": "HAMBURGUESA SIMPLE CON QUESO",
    "BURGER CON QUESO": "HAMBURGUESA SIMPLE CON QUESO",
    "HAMBURGER CON QUESO": "HAMBURGUESA SIMPLE CON QUESO",
    "HAMBURGUESAS CON QUESO": "HAMBURGUESA SIMPLE CON QUESO",
    "SIMPLE QUESO": "HAMBURGUESA SIMPLE CON QUESO",
    "SIMPLE CON QUESO": "HAMBURGUESA SIMPLE CON QUESO",
    "SIMPLE BACON QUESO": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "SIMPLE CON BACON Y QUESO": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "SIMPLE CON TOCINO Y QUESO": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "BACON CHEESEBURGER": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "BACON CHEESE BURGER": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "BACON AND CHEESE BURGER": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "BACON AND CHEESE HAMBURGER": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "TOCINO Y QUESO": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "HAMBURGUESA TOCINO Y QUESO": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "HAMBURGUESA CON TOCINO Y QUESO": "HAMBURGUESA SIMPLE CON TOCINO Y QUESO",
    "DOBLE": "HAMBURGUESA DOBLE",
    "DOBLES": "HAMBURGUESA DOBLE",
    "HAMBURGUESA DOBLE": "HAMBURGUESA DOBLE",
    "HAMBURGUESA DOBLES": "HAMBURGUESA DOBLE",
    "HAMBURGUESAS DOBLE": "HAMBURGUESA DOBLE",
    "HAMBURGUESAS DOBLES": "HAMBURGUESA DOBLE",
    "DOUBLE": "HAMBURGUESA DOBLE",
    "DOBLE DOBLE": "HAMBURGUESA DOBLE",
    "DOUBLE BURGER": "HAMBURGUESA DOBLE",
    "DOUBLE HAMBURGER": "HAMBURGUESA DOBLE",
    "BURGER DOUBLE": "HAMBURGUESA DOBLE",
    "HAMBURGER DOUBLE": "HAMBURGUESA DOBLE",
    "DOBLE QUESO": "HAMBURGUESA DOBLE CON QUESO",
    "DOBLES QUESO": "HAMBURGUESA DOBLE CON QUESO",
    "DOUBLE CHEESEBURGER": "HAMBURGUESA DOBLE CON QUESO",
    "DOUBLE CHEESE BURGER": "HAMBURGUESA DOBLE CON QUESO",
    "DOUBLE CHEESE HAMBURGER": "HAMBURGUESA DOBLE CON QUESO",
    "DOBLE CHEESEBURGER": "HAMBURGUESA DOBLE CON QUESO",
    "HAMBURGUESA DOBLE QUESO": "HAMBURGUESA DOBLE CON QUESO",
    "HAMBURGUESA DOBLE CON QUESO": "HAMBURGUESA DOBLE CON QUESO",
    "HAMBURGUESAS DOBLES CON QUESO": "HAMBURGUESA DOBLE CON QUESO",
    "DOBLE HAMBURGUESA QUESO": "HAMBURGUESA DOBLE CON QUESO",
    "DOBLE CON QUESO": "HAMBURGUESA DOBLE CON QUESO",
    "DOBLE BACON QUESO": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOBLE CON BACON Y QUESO": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOBLE CON TOCINO Y QUESO": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOUBLE BACON CHEESEBURGER": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOUBLE BACON CHEESE BURGER": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOUBLE BACON AND CHEESE BURGER": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "DOBLE TOCINO Y QUESO": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "HAMBURGUESA DOBLE TOCINO Y QUESO": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "HAMBURGUESA DOBLE CON TOCINO Y QUESO": "HAMBURGUESA DOBLE CON TOCINO Y QUESO",
    "TRIPLE": "HAMBURGUESA TRIPLE",
    "TRIPLES": "HAMBURGUESA TRIPLE",
    "HAMBURGUESA TRIPLES": "HAMBURGUESA TRIPLE",
    "HAMBURGUESAS TRIPLE": "HAMBURGUESA TRIPLE",
    "HAMBURGUESAS TRIPLES": "HAMBURGUESA TRIPLE",
    "TRIPLE BURGER": "HAMBURGUESA TRIPLE",
    "TRIPLE HAMBURGER": "HAMBURGUESA TRIPLE",
    "BURGER TRIPLE": "HAMBURGUESA TRIPLE",
    "HAMBURGER TRIPLE": "HAMBURGUESA TRIPLE",
    "TRIPLE QUESO": "HAMBURGUESA TRIPLE CON QUESO",
    "TRIPLES QUESO": "HAMBURGUESA TRIPLE CON QUESO",
    "TRIPLE CHEESEBURGER": "HAMBURGUESA TRIPLE CON QUESO",
    "TRIPLE CHEESE BURGER": "HAMBURGUESA TRIPLE CON QUESO",
    "TRIPLE CHEESE HAMBURGER": "HAMBURGUESA TRIPLE CON QUESO",
    "HAMBURGUESA TRIPLE QUESO": "HAMBURGUESA TRIPLE CON QUESO",
    "HAMBURGUESA TRIPLE CON QUESO": "HAMBURGUESA TRIPLE CON QUESO",
    "HAMBURGUESAS TRIPLES CON QUESO": "HAMBURGUESA TRIPLE CON QUESO",
    "TRIPLE CON QUESO": "HAMBURGUESA TRIPLE CON QUESO",
    "TRIPLE BACON QUESO": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE TOCINO QUESO HAMBURGUESA": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE CON BACON Y QUESO": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE CON TOCINO Y QUESO": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE BACON CHEESEBURGER": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE BACON CHEESE BURGER": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE BACON AND CHEESE BURGER": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "TRIPLE TOCINO Y QUESO": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "HAMBURGUESA TRIPLE TOCINO Y QUESO": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "HAMBURGUESA TRIPLE CON TOCINO Y QUESO": "HAMBURGUESA TRIPLE CON TOCINO Y QUESO",
    "COMBO CLASSIC": "COMBO CLASSIC",
    "CLASSIC COMBO": "COMBO CLASSIC",
    "COMBO CLASICO": "COMBO CLASSIC",
    "COMBO CLASICA": "COMBO CLASSIC",
    "CLASICO COMBO": "COMBO CLASSIC",
    "CLASICA COMBO": "COMBO CLASSIC",
    "COMBO DOBLE QUESO": "COMBO DOBLE QUESO",
    "DOBLE QUESO COMBO": "COMBO DOBLE QUESO",
    "DOUBLE CHEESE COMBO": "COMBO DOBLE QUESO",
    "COMBO DOUBLE CHEESE": "COMBO DOBLE QUESO",
    "DOUBLE CHEESEBURGER COMBO": "COMBO DOBLE QUESO",
    "COMBO DOUBLE CHEESEBURGER": "COMBO DOBLE QUESO",
    "PAPAS": "PAPAS FRITAS MEDIANAS",
    "PAPAS FRITAS": "PAPAS FRITAS MEDIANAS",
    "PAPA FRITA": "PAPAS FRITAS MEDIANAS",
    "PAPA FRITAS": "PAPAS FRITAS MEDIANAS",
    "PAPAS FRITA": "PAPAS FRITAS MEDIANAS",
    "FRITAS PAPAS": "PAPAS FRITAS MEDIANAS",
    "PEQUENA PAPAS": "PAPAS FRITAS PEQUENAS",
    "MEDIANA PAPAS": "PAPAS FRITAS MEDIANAS",
    "GRANDE PAPAS": "PAPAS FRITAS GRANDES",
    "PEQUENA FRITAS PAPAS": "PAPAS FRITAS PEQUENAS",
    "MEDIANA FRITAS PAPAS": "PAPAS FRITAS MEDIANAS",
    "GRANDE FRITAS PAPAS": "PAPAS FRITAS GRANDES",
    "PAPAS PEQUENAS": "PAPAS FRITAS PEQUENAS",
    "PAPAS CHICAS": "PAPAS FRITAS PEQUENAS",
    "PAPAS MEDIANAS": "PAPAS FRITAS MEDIANAS",
    "PAPAS FRITAS MEDIANA": "PAPAS FRITAS MEDIANAS",
    "PAPAS FRITAS MEDIANAS": "PAPAS FRITAS MEDIANAS",
    "PAPAS FRITAS MEDIA": "PAPAS FRITAS MEDIANAS",
    "PAPAS GRANDES": "PAPAS FRITAS GRANDES",
    "PAPAS FRITAS GRANDE": "PAPAS FRITAS GRANDES",
    "PAPAS FRITAS GRANDES": "PAPAS FRITAS GRANDES",
    "AROS": "AROS DE CEBOLLA MEDIANOS",
    "ARO CEBOLLA": "AROS DE CEBOLLA MEDIANOS",
    "AROS CEBOLLA": "AROS DE CEBOLLA MEDIANOS",
    "PEQUENA CEBOLLA AROS": "AROS DE CEBOLLA PEQUENOS",
    "MEDIANA CEBOLLA AROS": "AROS DE CEBOLLA MEDIANOS",
    "GRANDE CEBOLLA AROS": "AROS DE CEBOLLA GRANDES",
    "AROS DE CEBOLLA": "AROS DE CEBOLLA MEDIANOS",
    "AROS PEQUENOS": "AROS DE CEBOLLA PEQUENOS",
    "AROS CHICOS": "AROS DE CEBOLLA PEQUENOS",
    "AROS MEDIANOS": "AROS DE CEBOLLA MEDIANOS",
    "AROS GRANDES": "AROS DE CEBOLLA GRANDES",
    "COCA": "COCA COLA MEDIANA",
    "COCA COLA": "COCA COLA MEDIANA",
    "COKE": "COCA COLA MEDIANA",
    "COCA COLA PEQUENA": "COCA COLA PEQUENA",
    "COCA COLA CHICA": "COCA COLA PEQUENA",
    "COCA COLA MEDIANA": "COCA COLA MEDIANA",
    "COCA COLA GRANDE": "COCA COLA GRANDE",
    "PEQUENA COCA COLA": "COCA COLA PEQUENA",
    "MEDIANA COCA COLA": "COCA COLA MEDIANA",
    "GRANDE COCA COLA": "COCA COLA GRANDE",
    "INCA": "INCA KOLA MEDIANA",
    "INCA KOLA": "INCA KOLA MEDIANA",
    "INCA KOLA PEQUENA": "INCA KOLA PEQUENA",
    "INCA KOLA CHICA": "INCA KOLA PEQUENA",
    "INCA KOLA MEDIANA": "INCA KOLA MEDIANA",
    "INCA KOLA GRANDE": "INCA KOLA GRANDE",
    "PEPSI": "PEPSI MEDIANA",
    "PEPSI PEQUENA": "PEPSI PEQUENA",
    "PEPSI CHICA": "PEPSI PEQUENA",
    "PEPSI MEDIANA": "PEPSI MEDIANA",
    "PEPSI GRANDE": "PEPSI GRANDE",
    "SPRITE": "SPRITE MEDIANA",
    "SPRITE PEQUENA": "SPRITE PEQUENA",
    "PEQUENA SPRITE": "SPRITE PEQUENA",
    "MEDIANA SPRITE": "SPRITE MEDIANA",
    "GRANDE SPRITE": "SPRITE GRANDE",
    "SPRITE CHICA": "SPRITE PEQUENA",
    "SPRITE MEDIANA": "SPRITE MEDIANA",
    "SPRITE GRANDE": "SPRITE GRANDE",
    "FANTA": "FANTA MEDIANA",
    "FANTA PEQUENA": "FANTA PEQUENA",
    "FANTA CHICA": "FANTA PEQUENA",
    "FANTA MEDIANA": "FANTA MEDIANA",
    "FANTA GRANDE": "FANTA GRANDE",
    "ALITAS": "ALITAS FRITAS PICANTES X 6",
    "ALITAS FRITAS": "ALITAS FRITAS PICANTES X 6",
    "ALITAS FRITAS PICANTES": "ALITAS FRITAS PICANTES X 6",
    "ALITAS FRITAS 6": "ALITAS FRITAS PICANTES X 6",
    "ALITAS FRITAS X 6": "ALITAS FRITAS PICANTES X 6",
    "ALITAS FRITAS PICANTES 6": "ALITAS FRITAS PICANTES X 6",
    "ALITAS FRITAS PICANTES X 6": "ALITAS FRITAS PICANTES X 6",
    "ALITAS 6": "ALITAS FRITAS PICANTES X 6",
    "ALITAS X 6": "ALITAS FRITAS PICANTES X 6",
    "6 ALITAS": "ALITAS FRITAS PICANTES X 6",
    "HOTWINGS 6": "ALITAS FRITAS PICANTES X 6",
    "HOTWINGS X 6": "ALITAS FRITAS PICANTES X 6",
    "ALITAS FRITAS 12": "ALITAS FRITAS PICANTES X 12",
    "ALITAS FRITAS X 12": "ALITAS FRITAS PICANTES X 12",
    "ALITAS FRITAS PICANTES 12": "ALITAS FRITAS PICANTES X 12",
    "ALITAS FRITAS PICANTES X 12": "ALITAS FRITAS PICANTES X 12",
    "ALITAS 12": "ALITAS FRITAS PICANTES X 12",
    "ALITAS X 12": "ALITAS FRITAS PICANTES X 12",
    "12 ALITAS": "ALITAS FRITAS PICANTES X 12",
    "HOTWINGS 12": "ALITAS FRITAS PICANTES X 12",
    "HOTWINGS X 12": "ALITAS FRITAS PICANTES X 12",
    "ALITAS FRITAS 24": "ALITAS FRITAS PICANTES X 24",
    "ALITAS FRITAS X 24": "ALITAS FRITAS PICANTES X 24",
    "ALITAS FRITAS PICANTES 24": "ALITAS FRITAS PICANTES X 24",
    "ALITAS FRITAS PICANTES X 24": "ALITAS FRITAS PICANTES X 24",
    "ALITAS 24": "ALITAS FRITAS PICANTES X 24",
    "ALITAS X 24": "ALITAS FRITAS PICANTES X 24",
    "24 ALITAS": "ALITAS FRITAS PICANTES X 24",
    "HOTWINGS 24": "ALITAS FRITAS PICANTES X 24",
    "HOTWINGS X 24": "ALITAS FRITAS PICANTES X 24",
    "SALSA EXTRA": "SALSA EXTRA",
    "EXTRA SALSA": "SALSA EXTRA",
}
SAUCE_FLAVORS = {"BBQ", "BUFFALO", "HONEY MUSTARD", "BLUE CHEESE", "RANCH"}
WINGS_PACK_SIZES = {6: ("ALITAS FRITAS PICANTES X 6", 1), 12: ("ALITAS FRITAS PICANTES X 12", 2), 24: ("ALITAS FRITAS PICANTES X 24", 4)}
WINGS_SAUCE_OPTIONS_TEXT = "BBQ, Honey Mustard, Buffalo, Blue Cheese o Ranch"
PAYMENT_MAP = {
    "yape": "YAPE",
    "plin": "PLIN",
    "transferencia": "TRANSFERENCIA",
    "transfer": "TRANSFERENCIA",
    "transferir": "TRANSFERENCIA",
    "transf": "TRANSFERENCIA",
    "deposito": "TRANSFERENCIA",
    "depositar": "TRANSFERENCIA",
    "banco": "TRANSFERENCIA",
    "contra entrega": "CONTRA_ENTREGA",
    "contraentrega": "CONTRA_ENTREGA",
    "efectivo": "CONTRA_ENTREGA",
}
PAYMENT_PATTERNS = [
    (re.compile(r"\bya?pe\b"), "YAPE"),
    (re.compile(r"\bplin\b"), "PLIN"),
    (re.compile(r"\b(?:transferencia|transfer|transferir|transf(?:erencia)?|deposito|depositar|banco)\b"), "TRANSFERENCIA"),
    (re.compile(r"\b(?:contra\s*entrega|contraentrega|efectivo|pago\s+al\s+(?:recibir|llegar)|pago\s+en\s+efectivo|pago\s+contra\s+entrega|al\s+recibir|al\s+llegar)\b"), "CONTRA_ENTREGA"),
]
PAYMENT_EXAMPLES_TEXT = "Yape, Plin, Transferencia o Contra entrega"


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def infer_sized_menu_item(normalized: str) -> Optional[str]:
    tokens = normalized.split()
    token_set = set(tokens)

    size = "MEDIANA"
    if token_set.intersection({"PEQUENA", "PEQUENAS", "PEQUENO", "PEQUENOS"}):
        size = "PEQUENA"
    elif token_set.intersection({"GRANDE", "GRANDES"}):
        size = "GRANDE"
    elif token_set.intersection({"MEDIANA", "MEDIANAS", "MEDIANO", "MEDIANOS"}):
        size = "MEDIANA"

    # Side orders: French fries / papas fritas.
    if "PAPAS" in token_set or ("FRITAS" in token_set and "PAPAS" in token_set):
        return f"PAPAS FRITAS {size}S"

    # Side orders: onion rings / aros de cebolla.
    if "AROS" in token_set or ("ARO" in token_set and "CEBOLLA" in token_set) or ("RINGS" in token_set and "ONION" in token_set):
        onion_size = {"PEQUENA": "PEQUENOS", "MEDIANA": "MEDIANOS", "GRANDE": "GRANDES"}[size]
        return f"AROS DE CEBOLLA {onion_size}"

    brand: Optional[str] = None
    if "COCA" in token_set or "COKE" in token_set:
        brand = "COCA COLA"
    elif "INCA" in token_set:
        brand = "INCA KOLA"
    elif "PEPSI" in token_set:
        brand = "PEPSI"
    elif "SPRITE" in token_set:
        brand = "SPRITE"
    elif "FANTA" in token_set:
        brand = "FANTA"

    # Drinks: allow "small coke", "large soft drink Pepsi", "2 bebidas medianas Inca Kola", etc.
    if brand:
        return f"{brand} {size}"

    if "ALITAS" in token_set:
        for pack_size, (product_name, _included_sauces) in WINGS_PACK_SIZES.items():
            if str(pack_size) in token_set:
                return product_name
        return WINGS_PACK_SIZES[6][0]

    return None


def normalize_product_name(text: str) -> str:
    text = normalize_spaces(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("-", " ")
    text = re.sub(r"^(de|del|la|el|los|las)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\s+y$", "", text)
    tokens = [PRODUCT_TOKEN_MAP.get(token.lower(), token) for token in text.split()]
    normalized = " ".join(tokens).upper()
    inferred = infer_sized_menu_item(normalized)
    if inferred:
        return inferred
    return PRODUCT_PHRASE_MAP.get(normalized, normalized)


NUMBER_WORD_MAP = {
    "un": "1",
    "una": "1",
    "uno": "1",
    "dos": "2",
    "tres": "3",
    "cuatro": "4",
    "cinco": "5",
    "seis": "6",
    "siete": "7",
    "ocho": "8",
    "nueve": "9",
    "diez": "10",
    "doce": "12",
    "veinticuatro": "24",
    "veinte cuatro": "24",
}

CONVERSATIONAL_PREFIXES = [
    "quiero",
    "mandame",
    "mándame",
    "dame",
    "me das",
    "ponme",
    "enviame",
    "envíame",
]

CON_ATTACHMENT_MAP = {
    "PAPAS": "1 PAPAS",
    "PAPAS GRANDES": "1 PAPAS GRANDES",
    "PAPAS MEDIANAS": "1 PAPAS MEDIANAS",
    "COCA": "1 COCA",
    "COCA PERSONAL": "1 COCA PERSONAL",
    "GASEOSA": "1 GASEOSA",
    "GASEOSA PERSONAL": "1 GASEOSA PERSONAL",
    "EXTRA QUESO": "1 EXTRA QUESO",
    "EXTRA BACON": "1 EXTRA TOCINO",
    "EXTRA TOCINO": "1 EXTRA TOCINO",
    "EXTRA HUEVO": "1 EXTRA HUEVO",
    "EXTRA CARNE": "1 EXTRA CARNE",
}
ADD_ON_PHRASES = {
    "SALSA EXTRA",
    "EXTRA QUESO",
    "EXTRA QUESO CHEDDAR",
    "EXTRA BACON",
    "EXTRA TOCINO",
    "EXTRA HUEVO",
    "EXTRA CARNE",
    "SALSA DE AJO",
    "SALSA PICANTE",
    "SALSA BBQ",
    "SALSA DE LA CASA",
}
MODIFIER_PREFIXES = ["sin "]


def extract_sauce_flavors(text: str) -> tuple[str, List[str]]:
    text = normalize_spaces(text)
    flavor_re = re.compile(r"(?i)\bblue\s+cheese\b|\bhoney\s+mustard\b|\bmiel\s+y\s+mostaza\b|\bbuffalo\b|\branch\b|\bbbq\b")
    flavors: List[str] = []

    def label(match_text: str) -> str:
        normalized = normalize_loose_text(match_text)
        if normalized in {"honey mustard", "miel y mostaza"}:
            return "Honey Mustard"
        if normalized == "buffalo":
            return "Buffalo"
        if normalized == "blue cheese":
            return "Blue Cheese"
        if normalized == "ranch":
            return "Ranch"
        return "BBQ"

    for match in flavor_re.finditer(text):
        flavors.append(label(match.group(0)))
    text = flavor_re.sub(" ", text)
    return normalize_spaces(text), flavors


def is_spicy_wings_request(text: str) -> bool:
    normalized = normalize_loose_text(text)
    return bool(
        re.search(r"\b(?:hot\s*wings?|hotwings?|wings?\s+hot)\b", normalized)
        or ("alitas" in normalized.split() and re.search(r"\bpicantes?\b", normalized))
    )


def maybe_wings_pack_from_quantity(product: str, qty: float) -> tuple[str, float, int]:
    if product in {"ALITAS", "ALITAS FRITAS", "ALITAS FRITAS PICANTES", WINGS_PACK_SIZES[6][0]} and float(qty).is_integer():
        pack = int(qty)
        if pack in WINGS_PACK_SIZES:
            product_name, included = WINGS_PACK_SIZES[pack]
            return product_name, 1.0, included
    for pack_size, (product_name, included) in WINGS_PACK_SIZES.items():
        if product == product_name:
            return product, qty, included * int(qty) if float(qty).is_integer() else included
    return product, qty, 0


def strip_conversational_prefix(text: str) -> str:
    text = normalize_spaces(text)
    lowered = text.lower()
    for prefix in CONVERSATIONAL_PREFIXES:
        if lowered.startswith(prefix + " "):
            return normalize_spaces(text[len(prefix):])
    return text


def normalize_quantity_words(text: str) -> str:
    text = normalize_spaces(text)
    if not text:
        return text
    qty_words = "|".join(sorted(NUMBER_WORD_MAP.keys(), key=len, reverse=True))
    pattern = rf"(?i)(^|(?<=[,+])\s*|\s+y\s+)(?P<word>{qty_words})(?=\s+)"

    def repl(match: re.Match[str]) -> str:
        prefix = match.group(1)
        word = match.group("word")
        return f"{prefix}{NUMBER_WORD_MAP.get(word.lower(), word)}"

    return re.sub(pattern, repl, text)


def expand_con_attachments(line: str) -> List[Dict[str, Any]]:
    line = normalize_spaces(line)
    segments: List[Dict[str, Any]] = [{"text": line, "attachment": False, "attachment_source": None}]

    for phrase, replacement in sorted(CON_ATTACHMENT_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        next_segments: List[Dict[str, Any]] = []
        pattern = re.compile(rf"(?i)\s+con\s+{re.escape(phrase)}(?=\s+y\s+|\s*$)")
        for segment in segments:
            text = segment["text"]
            if not pattern.search(text):
                next_segments.append(segment)
                continue

            base_text = text
            attachments_found = False
            while pattern.search(base_text):
                base_text = pattern.sub(" | " + replacement, base_text, count=1)
                attachments_found = True
            if not attachments_found:
                next_segments.append(segment)
                continue
            parts = [normalize_spaces(part) for part in base_text.split("|") if normalize_spaces(part)]
            first = True
            for part in parts:
                if first and not segment.get("attachment"):
                    next_segments.append({"text": part, "attachment": False, "attachment_source": None})
                elif first and segment.get("attachment"):
                    next_segments.append({"text": part, "attachment": True, "attachment_source": segment.get("attachment_source") or phrase})
                else:
                    next_segments.append({"text": part, "attachment": True, "attachment_source": phrase})
                first = False
        segments = next_segments

    return segments


def extract_modifiers(text: str) -> tuple[str, List[str]]:
    text = normalize_spaces(text)
    modifiers: List[str] = []
    pattern = r"(?i)\s+(?:y\s+)?(sin\s+.+)$"
    match = re.search(pattern, text)
    if match:
        modifier_text = normalize_spaces(match.group(1))
        parts = [normalize_spaces(p) for p in re.split(r"(?i)\s+y\s+sin\s+", modifier_text.replace("SIN ", "sin ")) if normalize_spaces(p)]
        for part in parts:
            if not part.lower().startswith("sin "):
                part = "sin " + part
            modifiers.append(part)
        text = normalize_spaces(re.sub(pattern, "", text))
    return text, modifiers


def split_item_line(line: str) -> List[Dict[str, Any]]:
    line = strip_conversational_prefix(line)
    line = normalize_quantity_words(line)
    line_without_modifiers, modifiers = extract_modifiers(line)
    expanded_segments: List[Dict[str, Any]] = []
    for segment in expand_con_attachments(line_without_modifiers):
        segment_text = segment["text"]
        segment_text = re.sub(r"\s*(\+|,)\s*", " | ", segment_text)
        segment_text = re.sub(r"(?i)\s+y\s+(?=\d+(?:[.,]\d+)?\s+)", " | ", segment_text)
        parts = [normalize_spaces(part) for part in segment_text.split("|") if normalize_spaces(part)]
        for part in parts:
            payload = {**segment, "text": part}
            if modifiers and not segment.get("attachment"):
                payload["modifiers"] = list(modifiers)
            expanded_segments.append(payload)
    return expanded_segments or [{"text": normalize_spaces(line_without_modifiers), "attachment": False, "attachment_source": None, "modifiers": list(modifiers)}]


def normalize_loose_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return normalize_spaces(text)


def detect_abuse_reason(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    normalized = normalize_loose_text(text)
    for pattern, reason in get_abuse_patterns():
        if pattern.search(normalized):
            return reason
    return None


def parse_payment_method(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    normalized = normalize_loose_text(text)
    for pattern, value in PAYMENT_PATTERNS:
        if pattern.search(normalized):
            return value
    for key, value in PAYMENT_MAP.items():
        if key in normalized:
            return value
    normalized2 = normalized.upper().replace(" ", "_")
    if normalized2 in {"YAPE", "PLIN", "TRANSFERENCIA", "CONTRA_ENTREGA"}:
        return normalized2
    return None


def parse_yape_channel(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    candidate_lines = []
    for line in str(text).splitlines():
        if ADDRESS_LABEL_RE.search(line):
            continue
        cleaned = normalize_loose_text(line)
        if cleaned:
            candidate_lines.append(cleaned)
    normalized = normalize_loose_text(" ".join(candidate_lines) or str(text))
    if normalized in {"app", "1", "opcion 1", "opcion app", "aplicacion", "aplicacion yape", "yape app", "app yape"}:
        return "APP"
    if normalized in {"pos", "2", "opcion 2", "opcion pos", "yape pos", "pos yape", "tarjeta", "maquinita"}:
        return "POS"
    if re.search(r"\b(?:yape\s+app|app\s+(?:de\s+)?yape|por\s+app|desde\s+(?:la\s+)?app)\b", normalized):
        return "APP"
    if re.search(r"\b(?:yape\s+pos|pos\s+(?:de\s+)?yape|con\s+pos|punto\s+de\s+venta|maquinita)\b", normalized):
        return "POS"
    return None

def yape_channel_prompt() -> str:
    return (
        "Perfecto, usarás Yape. ¿Cómo pagará el cliente?\n\n"
        "Responde una opción:\n"
        "1. APP - pago desde la app de Yape; luego debe subir el comprobante.\n"
        "2. POS - pago con POS/maquinita al recibir."
    )


def yape_app_upload_prompt() -> str:
    return (
        "Perfecto, pago por Yape APP.\n\n"
        "Por favor sube la imagen del comprobante de Yape (JPG o PNG) antes de confirmar el pedido."
    )


ADDRESS_LABEL_RE = re.compile(r"\b(?:direcci[oó]n|direccion|dir|address|domicilio|ubicaci[oó]n|referencia|ref)\b\s*[:\-]?\s*(.+)", re.IGNORECASE)
PAYMENT_LINE_RE = re.compile(r"\b(?:pago|pagar[ée]?|forma\s+de\s+pago|m[eé]todo\s+de\s+pago|con)\b\s*(?:con|por|en)?\s*(yape|plin|transferencia|transfer|transf|dep[oó]sito|deposito|efectivo|contra\s*entrega|contraentrega)\b", re.IGNORECASE)


def parse_written_address(text: Optional[str]) -> Optional[str]:
    """Extract a human-written delivery address from the payment/address reply."""
    if not text:
        return None
    lines = [normalize_spaces(line) for line in str(text).splitlines() if normalize_spaces(line)]
    candidates: List[str] = []
    for line in lines:
        match = ADDRESS_LABEL_RE.search(line)
        if match and normalize_spaces(match.group(1)):
            candidates.append(normalize_spaces(match.group(1)))
            continue
        without_payment = PAYMENT_LINE_RE.sub("", line)
        for key in sorted(PAYMENT_MAP.keys(), key=len, reverse=True):
            without_payment = re.sub(r"\b" + re.escape(key) + r"\b", "", without_payment, flags=re.IGNORECASE)
        without_payment = re.sub(r"\b(?:pago|pagar[ée]?|forma\s+de\s+pago|m[eé]todo\s+de\s+pago|con|por|en|y|,|;)\b", " ", without_payment, flags=re.IGNORECASE)
        cleaned = normalize_spaces(without_payment.strip(" .,:;-"))
        if cleaned and len(cleaned) >= 8 and not parse_payment_method(cleaned):
            candidates.append(cleaned)
    if not candidates:
        return None
    return normalize_spaces("; ".join(dict.fromkeys(candidates)))


def build_confirmed_address(draft: Dict[str, Any]) -> str:
    written = first_non_empty(draft.get("written_address"))
    detected = first_non_empty(draft.get("detected_address"))
    if written and detected and written != detected:
        return f"{written}\nReferencia del mapa: {detected}"
    return written or detected or ""


PICKUP_ADDRESS = os.environ.get("REPLAU_PICKUP_ADDRESS", "Recojo en restaurante Replau")


def is_pickup_intent(text: Optional[str]) -> bool:
    if not text:
        return False
    normalized = normalize_loose_text(text)
    return bool(re.search(r"\b(?:recojo|recoger|recogere|recogerlo|retiro|retirar|retirare|pickup|pick\s*up)\b", normalized)) or bool(
        re.search(r"\b(?:en\s+(?:el\s+)?(?:local|restaurante|tienda)|paso\s+(?:por|a)\s+(?:el\s+)?(?:local|restaurante|tienda))\b", normalized)
    )


def parse_fulfillment_choice(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    normalized = normalize_loose_text(text)
    if normalized in {"1", "opcion 1", "delivery", "delibery", "envio", "envio a domicilio", "domicilio"}:
        return "DELIVERY"
    if normalized in {"2", "opcion 2", "recojo", "recoger", "retiro", "pickup", "pick up", "restaurante", "local", "tienda"}:
        return "PICKUP"
    if re.search(r"\b(?:delivery|delibery|env[ií]o|domicilio)\b", normalized):
        return "DELIVERY"
    if is_pickup_intent(text):
        return "PICKUP"
    return None


def fulfillment_choice_prompt(prefix: str = "") -> str:
    intro = (prefix.rstrip() + "\n\n") if prefix else ""
    return intro + "¿Será:\n1. Delivery\n2. Recojo en restaurante?\n\nPuedes responder 1, 2, delivery o recojo."


def mark_delivery_order(draft: Dict[str, Any]) -> None:
    draft["fulfillment_method"] = "DELIVERY"
    draft.pop("awaiting_fulfillment_choice", None)


def mark_pickup_order(draft: Dict[str, Any]) -> None:
    draft["fulfillment_method"] = "PICKUP"
    draft["delivery"] = 0
    draft["confirmed_address"] = PICKUP_ADDRESS
    draft["written_address"] = PICKUP_ADDRESS
    draft["detected_address"] = None
    draft["latitude"] = None
    draft["longitude"] = None


def is_pickup_order(draft: Dict[str, Any]) -> bool:
    return str(draft.get("fulfillment_method") or "").upper() == "PICKUP"


def pickup_confirmation_text(draft: Dict[str, Any]) -> str:
    return (
        "Perfecto, marqué el pedido para recojo en restaurante ✅\n\n"
        f"Pago: {draft.get('payment_method')}\n"
        f"Recojo: {draft.get('confirmed_address') or PICKUP_ADDRESS}\n\n"
        "Responde SI para confirmar el pedido o NO si quieres cambiarlo."
    )


def maybe_ready_for_pickup_confirmation(inbound: NormalizedWebhook, draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not is_pickup_order(draft) or not draft.get("payment_method"):
        return None
    if draft.get("payment_method") == "YAPE" and draft.get("yape_channel") == "APP" and not draft.get("payment_receipt"):
        draft["awaiting_yape_receipt"] = True
        patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
        text = "Perfecto, será recojo en restaurante. Falta el comprobante de Yape APP; por favor sube la imagen del pago aprobado."
        patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_receipt=True, fulfillment_method="PICKUP")
    patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_ADDRESS_CONFIRMATION", "pedido_borrador": draft})
    text = pickup_confirmation_text(draft)
    patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION", fulfillment_method="PICKUP")


def is_repeat_order_intent(text: Optional[str]) -> bool:
    if not text:
        return False
    normalized = normalize_loose_text(text)
    return normalized in {
        "repetir orden",
        "repetir pedido",
        "repite mi orden",
        "repite mi pedido",
        "quiero repetir orden",
        "quiero repetir pedido",
        "mismo pedido",
        "lo mismo",
    }


def is_yes(text: Optional[str]) -> bool:
    if not text:
        return False
    return normalize_spaces(text).lower() in {"si", "sí", "s", "yes", "y", "ok", "correcto", "confirmo", "confirmar"}


def is_menu_request(text: Optional[str]) -> bool:
    if not text:
        return False
    normalized = normalize_loose_text(text)
    return bool(re.search(r"\b(?:menu|carta|catalogo|cat[aá]logo|lista|precios|price\s*list)\b", normalized))


def is_web_order_handoff(text: Optional[str]) -> bool:
    if not text:
        return False
    return normalize_loose_text(str(text).splitlines()[0]).startswith("pedido web confirmado")


def web_order_handoff_credentials(text: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract the order number, signed tracking token, and tracking URL."""
    if not is_web_order_handoff(text):
        return None, None, None
    value = str(text)
    order_match = re.search(r"pedido\s+web\s+confirmado\s*:\s*([A-Za-z0-9_-]+)", value, re.IGNORECASE)
    url_match = re.search(r"https?://[^\s]+/track/([A-Za-z0-9_-]+)\?[^\s]+", value, re.IGNORECASE)
    if not order_match or not url_match:
        return None, None, None
    tracking_url = url_match.group(0).rstrip(".,;)")
    query = parse_qs(urlparse(tracking_url).query)
    token = first_non_empty(*(query.get("token") or []))
    url_order = url_match.group(1)
    order_num = order_match.group(1)
    if normalize_loose_text(order_num) != normalize_loose_text(url_order):
        return None, None, None
    return order_num, token, tracking_url


def handle_web_order_handoff(inbound: NormalizedWebhook) -> Dict[str, Any]:
    """Validate a signed storefront handoff and link it to this WhatsApp chat."""
    pedido_num, token, tracking_url = web_order_handoff_credentials(inbound.message_text)
    if not pedido_num or not token:
        text = "No pude validar los datos del pedido web. Ábrelo nuevamente desde la página de confirmación."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="ASKING_NAME_AND_ITEMS", web_order_handoff=False)

    result = pg_post("/rpc/obtener_pedido_publico", {"p_pedido_num": pedido_num, "p_token": token})
    if not isinstance(result, dict) or not result.get("ok") or not isinstance(result.get("order"), dict):
        text = "El enlace del pedido web es inválido o venció. Abre nuevamente el seguimiento desde la página."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="ASKING_NAME_AND_ITEMS", web_order_handoff=False)

    order = result["order"]
    items = result.get("items") if isinstance(result.get("items"), list) else []
    draft = {
        "pedido_id": order.get("id"),
        "pedido_num": order.get("pedido_num") or pedido_num,
        "customer_name": order.get("cliente_nombre"),
        "items": items,
        "total": order.get("total"),
        "payment_method": order.get("metodo_pago"),
        "written_address": order.get("direccion_escrita"),
        "confirmed_address": order.get("direccion_confirmada") or order.get("direccion_detectada"),
        "latitude": order.get("latitud_entrega"),
        "longitude": order.get("longitud_entrega"),
        "tracking_url": tracking_url,
        "source": "WEB",
        "confirmation_result": {
            "pedido_id": order.get("id"),
            "pedido_num": order.get("pedido_num") or pedido_num,
            "total": order.get("total"),
            "tracking_url": tracking_url,
        },
    }
    text = f"Recibí tu pedido web {draft['pedido_num']} ✅"
    if draft.get("customer_name"):
        text += f"\nCliente: {draft['customer_name']}"
    if draft.get("total") is not None:
        text += f"\nTotal: S/ {float(draft['total']):.2f}"
    if draft.get("payment_method"):
        text += f"\nPago: {draft['payment_method']}"
    if tracking_url:
        text += f"\n\nSigue tu pedido aquí:\n{tracking_url}"
    if draft.get("payment_method") in {"YAPE", "PLIN", "TRANSFERENCIA"}:
        text += "\n\nSi el comprobante no se cargó en la web, envíalo aquí como foto o PDF."
    patch_conversation(inbound.whatsapp_number, {
        "estado": "CONFIRMED",
        "pedido_id": order.get("id"),
        "pedido_borrador": draft,
        "last_outbound_text": text,
    })
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state="CONFIRMED", web_order_handoff=True, pedido_id=order.get("id"))


def menu_reply_text() -> str:
    if MENU_URL:
        return (
            "🍔 Mira el menú completo de Replau Burger aquí:\n"
            f"{MENU_URL}\n\n"
            "Cuando elijas, envíame tu nombre y los productos que deseas pedir."
        )
    return (
        "Sí, puedo enviarte el menú, pero todavía no tengo configurado el enlace de descarga.\n\n"
        "Mientras tanto, puedes escribirme tu pedido directamente."
    )




def strip_legacy_payment_prompt(text: str) -> str:
    if not text:
        return text
    text = re.sub(
        r"\n{0,2}Opciones de pago:\s*\n(?:.*?\n)*?Por favor indica tu forma de pago y envíame tu ubicación\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\n{0,2}Por favor indica tu forma de pago y envíame tu ubicación\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.rstrip()


def menu_offer_text() -> str:
    return (
        "Hola, bienvenido a Replau 🍔\n\n"
        "¿Quieres que te envíe el menú para descargarlo? Responde SI o escribe MENU.\n\n"
        "Si ya sabes qué pedir, envíame tu nombre y los productos.\n"
        "Ejemplo:\nJuan Pérez\n1 hamburguesa doble con queso\n1 papas grandes\n1 coca cola mediana"
    )


def looks_like_fresh_order(text: Optional[str]) -> bool:
    if not text or is_yes(text) or is_no(text) or is_menu_request(text) or is_repeat_order_intent(text):
        return False
    if parse_payment_method(text):
        return False
    try:
        customer_name, items = parse_name_and_items(text)
    except Exception:
        return False
    return bool(customer_name and items)


def is_no(text: Optional[str]) -> bool:
    if not text:
        return False
    return normalize_spaces(text).lower() in {"no", "n", "corregir", "incorrecto", "cambiar"}


def parse_name_and_items(message_text: str) -> Tuple[str, List[Dict[str, Any]]]:
    lines = [normalize_spaces(line) for line in message_text.splitlines() if normalize_spaces(line)]
    # Accept the common WhatsApp style: "Name; 1 burger; 1 fries" as equivalent
    # to the preferred multi-line format. This prevents a fresh order from being
    # mistaken for an address/payment reply in stale conversations.
    if len(lines) == 1 and ";" in lines[0]:
        parts = [normalize_spaces(part) for part in lines[0].split(";") if normalize_spaces(part)]
        if len(parts) >= 2:
            lines = parts
    if len(lines) < 2:
        raise ValueError("Send your name on the first line and order items on the next lines.")

    customer_name = lines[0]
    items: List[Dict[str, Any]] = []

    for raw_line in lines[1:]:
        previous_product: Optional[str] = None
        for segment in split_item_line(raw_line):
            line = segment["text"]
            match = re.match(
                r"^\s*(?P<qty>\d+(?:[.,]\d+)?)\s+(?P<rest>.+?)\s*$",
                line,
                re.IGNORECASE,
            )
            if not match:
                raise ValueError(f"Could not parse item line: {line}")

            qty = float(match.group("qty").replace(",", "."))
            rest = normalize_spaces(match.group("rest") or "")
            if not rest:
                raise ValueError(f"Missing product name in line: {line}")

            modifiers = list(segment.get("modifiers") or [])
            rest, sauce_flavors = extract_sauce_flavors(rest)
            rest, inline_modifiers = extract_modifiers(rest)
            if inline_modifiers:
                modifiers.extend(inline_modifiers)
            if sauce_flavors:
                modifiers.append("salsas: " + ", ".join(sauce_flavors))
            tokens = rest.split()
            unit = None
            product_tokens = tokens

            if tokens:
                unit_candidate = UNIT_MAP.get(tokens[0].lower())
                if unit_candidate:
                    unit = unit_candidate
                    product_tokens = tokens[1:]
                    if product_tokens and product_tokens[0].lower() == "de":
                        product_tokens = product_tokens[1:]

            rest_for_product = " ".join(product_tokens)
            spicy_wings = is_spicy_wings_request(rest_for_product)
            product = normalize_product_name(rest_for_product)
            if not product:
                raise ValueError(f"Missing product name in line: {line}")
            product, qty, included_sauce_count = maybe_wings_pack_from_quantity(product, qty)
            if product.startswith(("PAPAS FRITAS", "AROS DE CEBOLLA", "COCA COLA", "INCA KOLA", "PEPSI", "SPRITE", "FANTA", "ALITAS FRITAS", "SALSA EXTRA")):
                unit = None
            parent_product = previous_product
            item: Dict[str, Any] = {"producto_texto": product, "cantidad": qty, "unidad": unit}
            if modifiers:
                item["modifiers"] = modifiers
                item["modifier_note"] = ", ".join(modifiers)
            if segment.get("attachment"):
                item["attached_via_con"] = True
                item["attachment_source"] = segment.get("attachment_source")
                if parent_product:
                    item["attached_to_producto_texto"] = parent_product
            if product.startswith("COMBO ") or "COMBO" in product or segment.get("attachment"):
                item["combo_candidate"] = True
            if product in ADD_ON_PHRASES or product.startswith("EXTRA "):
                item["add_on_candidate"] = True
            items.append(item)
            if product.startswith("ALITAS FRITAS") and sauce_flavors and len(sauce_flavors) > included_sauce_count:
                extra_count = len(sauce_flavors) - included_sauce_count
                items.append({
                    "producto_texto": "SALSA EXTRA",
                    "cantidad": float(extra_count),
                    "unidad": None,
                    "modifiers": ["salsas extra: " + ", ".join(sauce_flavors[included_sauce_count:])],
                    "modifier_note": "salsas extra: " + ", ".join(sauce_flavors[included_sauce_count:]),
                    "add_on_candidate": True,
                })
            if not segment.get("attachment"):
                previous_product = product

    return customer_name, items




def parse_corrected_items_preserving_name(message_text: str, existing_customer_name: str) -> tuple[str, List[Dict[str, Any]]]:
    """Parse product correction replies without letting product text replace the name.

    After a failed quote, customers often reply only with corrected items, e.g.
    "1 hamburguesa simple". The normal parser treats the first line as the
    customer name when there are multiple lines, so keep the first captured name
    sticky during correction mode.
    """
    preserved_name = normalize_spaces(existing_customer_name or "Cliente") or "Cliente"

    # Preferred correction format: item lines only.
    try:
        _dummy_name, items = parse_name_and_items(f"{preserved_name}\n{message_text}")
        return preserved_name, items
    except Exception as item_only_exc:
        # If the customer repeats a full order with their name again, accept it,
        # but still preserve the original name captured at the beginning.
        try:
            _ignored_name, items = parse_name_and_items(message_text)
            return preserved_name, items
        except Exception:
            raise item_only_exc


def should_preserve_name_for_item_retry(draft: Dict[str, Any]) -> bool:
    if not draft or not draft.get("customer_name"):
        return False
    if draft.get("awaiting_item_correction"):
        return True
    quote = draft.get("quote_result") or {}
    return quote.get("ok") is False or bool(quote.get("errors"))

def reverse_geocode(latitude: float, longitude: float) -> str:
    """
    Reverse geocode latitude/longitude into a readable address.

    Providers:
    - google
    - nominatim
    - none

    Recommended production setting:
    GEOCODER_PROVIDER=google
    """

    fallback = f"Ubicación enviada: {latitude}, {longitude}"

    if GEOCODER_PROVIDER == "none":
        return fallback

    if GEOCODER_PROVIDER == "google":
        if not GOOGLE_MAPS_API_KEY:
            logging.warning("GEOCODER_PROVIDER=google but GOOGLE_MAPS_API_KEY is not configured")
            return fallback

        try:
            response = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={
                    "latlng": f"{latitude},{longitude}",
                    "language": os.environ.get("GOOGLE_GEOCODE_LANGUAGE", "es"),
                    "region": os.environ.get("GOOGLE_GEOCODE_REGION", "pe"),
                    "key": GOOGLE_MAPS_API_KEY,
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            status = data.get("status")
            results = data.get("results") or []

            if status == "OK" and results:
                preferred_types = {
                    "street_address",
                    "premise",
                    "subpremise",
                    "route",
                    "establishment",
                }

                # Prefer real street/building addresses over Google Plus Codes.
                for result in results:
                    result_types = set(result.get("types") or [])
                    formatted = result.get("formatted_address")

                    if not formatted:
                        continue

                    if "plus_code" in result_types:
                        continue

                    if result_types.intersection(preferred_types):
                        return formatted

                # If no preferred address exists, use the first non-plus-code result.
                for result in results:
                    result_types = set(result.get("types") or [])
                    formatted = result.get("formatted_address")

                    if formatted and "plus_code" not in result_types:
                        return formatted

                # Last fallback: Google Plus Code or any first result.
                return results[0].get("formatted_address") or fallback

            logging.warning(
                "Google reverse geocoding failed status=%s error=%s",
                status,
                data.get("error_message"),
            )
            return fallback

        except Exception as exc:
            logging.warning("Google reverse geocoding failed: %s", exc)
            return fallback

    if GEOCODER_PROVIDER == "nominatim":
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "format": "jsonv2",
                    "lat": latitude,
                    "lon": longitude,
                    "zoom": 18,
                    "addressdetails": 1,
                },
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("display_name") or fallback

        except Exception as exc:
            logging.warning("Nominatim reverse geocoding failed: %s", exc)
            return fallback

    logging.warning("Unknown GEOCODER_PROVIDER=%s", GEOCODER_PROVIDER)
    return fallback


def reply(text: str, **extra: Any) -> Dict[str, Any]:
    payload = {"ok": True, "reply_text": text}
    payload.update(extra)
    return payload


def format_quote_lines(items: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for idx, item in enumerate(items, start=1):
        producto = item.get("producto_nombre") or item.get("producto_texto_cliente") or item.get("producto_texto") or "Producto"
        cantidad = item.get("cantidad")
        unidad = item.get("unidad") or ""
        total_linea = item.get("total_linea")
        if total_linea is not None:
            lines.append(f"{idx}. {producto} x {cantidad} {unidad} — S/ {float(total_linea):.2f}")
        else:
            precio = item.get("precio_unitario") or 0
            try:
                total_linea = float(cantidad or 0) * float(precio or 0)
                lines.append(f"{idx}. {producto} x {cantidad} {unidad} — S/ {total_linea:.2f}")
            except Exception:
                lines.append(f"{idx}. {producto} x {cantidad} {unidad}")
    return "\n".join(lines)


def wings_sauce_slots(items: List[Dict[str, Any]]) -> int:
    sauce_slots = 0
    for item in items or []:
        product = str(item.get("producto_texto") or item.get("producto_nombre") or item.get("producto_texto_cliente") or "").upper()
        if not product.startswith("ALITAS FRITAS PICANTES"):
            continue
        note = str(item.get("modifier_note") or "")
        if "salsas:" in note.lower():
            continue
        qty = item.get("cantidad") or 1
        try:
            qty_int = int(float(qty)) if float(qty).is_integer() else 1
        except Exception:
            qty_int = 1
        for _pack_size, (product_name, included) in WINGS_PACK_SIZES.items():
            if product == product_name:
                sauce_slots += included * qty_int
                break
    return sauce_slots


def wings_sauce_selection_prompt(items: List[Dict[str, Any]]) -> str:
    sauce_slots = wings_sauce_slots(items)
    if sauce_slots <= 0:
        return ""
    plural = "salsa" if sauce_slots == 1 else "salsas"
    return f"\n\nPara tus hotwings, elige {sauce_slots} {plural}: {WINGS_SAUCE_OPTIONS_TEXT}."


def append_wings_sauce_prompt(text: str, items: List[Dict[str, Any]]) -> str:
    prompt = wings_sauce_selection_prompt(items)
    if prompt and prompt not in text:
        return text.rstrip() + prompt
    return text


def item_product_name(item: Dict[str, Any]) -> str:
    return str(item.get("producto_nombre") or item.get("producto_texto_cliente") or item.get("producto_texto") or "").upper()


def smart_upsell_suggestion(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    names = [item_product_name(item) for item in items or []]
    if not names:
        return None
    has_burger = any("HAMBURGUESA" in name for name in names)
    has_wings = any(name.startswith("ALITAS FRITAS") for name in names)
    has_side = any(name.startswith("PAPAS FRITAS") or name.startswith("AROS DE CEBOLLA") for name in names)
    has_drink = any(name.startswith(("COCA COLA", "INCA KOLA", "PEPSI", "SPRITE", "FANTA")) for name in names)
    has_sauce = any(name.startswith("SALSA EXTRA") for name in names)

    if has_burger and not has_side:
        return {"producto_texto": "PAPAS FRITAS MEDIANAS", "cantidad": 1, "unidad": None, "reason": "acompañamiento"}
    if (has_burger or has_side or has_wings) and not has_drink:
        return {"producto_texto": "COCA COLA MEDIANA", "cantidad": 1, "unidad": None, "reason": "bebida"}
    if (has_wings or has_side) and not has_sauce:
        return {"producto_texto": "SALSA EXTRA", "cantidad": 1, "unidad": None, "reason": "salsa extra"}
    return None


def smart_upsell_prompt(suggestion: Dict[str, Any]) -> str:
    product = suggestion.get("producto_texto") or "un adicional"
    reason = suggestion.get("reason") or "adicional"
    return (
        f"\n\nSugerencia rápida: ¿quieres agregar 1 {product.title()} como {reason}?\n"
        "Responde SI para agregarlo, NO para seguir sin agregar, o envía tu forma de pago directamente."
    )


def maybe_attach_smart_upsell(draft: Dict[str, Any], quote_text: str) -> str:
    if draft.get("awaiting_wings_sauces") or draft.get("smart_upsell_asked"):
        return quote_text
    suggestion = smart_upsell_suggestion(draft.get("original_items") or draft.get("items") or [])
    if not suggestion:
        draft["smart_upsell_asked"] = True
        return quote_text
    draft["awaiting_smart_upsell"] = True
    draft["smart_upsell_suggestion"] = suggestion
    return quote_text.rstrip() + smart_upsell_prompt(suggestion)


def payment_prompt_text() -> str:
    return (
        "\n\nPara continuar, indica tu forma de pago:\n"
        "1. Yape\n"
        "2. POS\n"
        "3. Pago en efectivo"
    )


def apply_smart_upsell(inbound: NormalizedWebhook, draft: Dict[str, Any]) -> Dict[str, Any]:
    suggestion = draft.get("smart_upsell_suggestion") or {}
    product = suggestion.get("producto_texto")
    if not product:
        draft["smart_upsell_asked"] = True
        draft.pop("awaiting_smart_upsell", None)
        draft.pop("smart_upsell_suggestion", None)
        text = "Listo, sigo con tu pedido. Indícame tu forma de pago: " + PAYMENT_EXAMPLES_TEXT + "."
        patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft, "last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", smart_upsell_added=False)

    added_item = {"producto_texto": product, "cantidad": suggestion.get("cantidad") or 1, "unidad": suggestion.get("unidad")}
    original_items = list(draft.get("original_items") or [])
    original_items.append(added_item)
    quote_result = pg_post(
        "/rpc/cotizar_pedido_whatsapp",
        {"p_customer_name": draft.get("customer_name") or "Cliente", "p_items": original_items, "p_delivery": draft.get("delivery", DEFAULT_DELIVERY)},
    )
    quote_items = quote_result.get("items") or original_items
    draft.update(
        {
            "items": quote_items,
            "original_items": original_items,
            "subtotal": quote_result.get("subtotal"),
            "delivery": quote_result.get("delivery", DEFAULT_DELIVERY),
            "total": quote_result.get("total"),
            "quote_result": quote_result,
            "smart_upsell_asked": True,
            "smart_upsell_added": added_item,
            "awaiting_item_correction": not bool(quote_result.get("ok")),
        }
    )
    draft.pop("awaiting_smart_upsell", None)
    draft.pop("smart_upsell_suggestion", None)
    quote_text = quote_result.get("whatsapp_quote_text") or "Pedido actualizado."
    quote_text = strip_legacy_payment_prompt(quote_text)
    if quote_result.get("ok"):
        quote_text += payment_prompt_text()
    patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft, "last_outbound_text": quote_text})
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", quote_text)
    return reply(quote_text, quote=quote_result, next_state="WAITING_PAYMENT_AND_LOCATION", smart_upsell_added=True)


def skip_smart_upsell(draft: Dict[str, Any]) -> None:
    draft["smart_upsell_asked"] = True
    draft["smart_upsell_skipped"] = True
    draft.pop("awaiting_smart_upsell", None)
    draft.pop("smart_upsell_suggestion", None)


def store_wings_sauce_selection(draft: Dict[str, Any], text: str) -> tuple[bool, str]:
    _remaining_text, sauce_flavors = extract_sauce_flavors(text)
    if not sauce_flavors:
        return False, f"Por favor elige la salsa para tus hotwings: {WINGS_SAUCE_OPTIONS_TEXT}."

    slots = int(draft.get("wings_sauce_slots") or wings_sauce_slots(draft.get("original_items") or draft.get("items") or []))
    if slots > 0 and len(sauce_flavors) > slots:
        plural = "salsa" if slots == 1 else "salsas"
        return False, f"Tus hotwings incluyen {slots} {plural}. Elige hasta {slots}: {WINGS_SAUCE_OPTIONS_TEXT}."

    sauce_note = "salsas: " + normalize_spaces(text)
    updated = False
    for collection_name in ("original_items", "items"):
        for item in draft.get(collection_name) or []:
            product = str(item.get("producto_texto") or item.get("producto_nombre") or item.get("producto_texto_cliente") or "").upper()
            if product.startswith("ALITAS FRITAS PICANTES"):
                modifiers = list(item.get("modifiers") or [])
                modifiers = [m for m in modifiers if not str(m).lower().startswith("salsas:")]
                modifiers.append(sauce_note)
                item["modifiers"] = modifiers
                item["modifier_note"] = ", ".join(modifiers)
                updated = True
                break
        if updated:
            # Keep both quote/display items and original confirmation items annotated.
            updated = False
            continue

    draft["wings_sauces"] = sauce_flavors
    draft["wings_sauce_note"] = sauce_note
    draft["awaiting_wings_sauces"] = False
    draft.pop("wings_sauce_slots", None)
    return True, "Registré las salsas para tus hotwings: " + ", ".join(sauce_flavors)


def handle_repeat_order(inbound: NormalizedWebhook, conversation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    customer = get_customer_by_whatsapp(inbound.whatsapp_number)
    snapshot = (customer or {}).get("last_order_snapshot") or {}
    previous_items = snapshot.get("items") or []
    if not customer or not previous_items:
        text = (
            "Todavía no tengo una orden anterior guardada para repetir.\n\n"
            "Por favor envíame tu nombre y los productos que deseas comprar."
        )
        patch_conversation(inbound.whatsapp_number, {"estado": "ASKING_NAME_AND_ITEMS", "last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="ASKING_NAME_AND_ITEMS")

    customer_name = customer.get("nombre") or "Cliente"
    quote_items = [
        {
            "producto_texto": item.get("producto_texto") or item.get("producto_nombre") or item.get("producto_texto_cliente"),
            "cantidad": item.get("cantidad"),
            "unidad": item.get("unidad"),
        }
        for item in previous_items
        if item.get("producto_texto") or item.get("producto_nombre") or item.get("producto_texto_cliente")
    ]
    quote_result = pg_post(
        "/rpc/cotizar_pedido_whatsapp",
        {"p_customer_name": customer_name, "p_items": quote_items, "p_delivery": DEFAULT_DELIVERY},
    )
    items = quote_result.get("items") or previous_items
    draft = {
        "repeat_order": True,
        "customer_name": customer_name,
        "items": items,
        "original_items": quote_items,
        "subtotal": quote_result.get("subtotal"),
        "delivery": quote_result.get("delivery", DEFAULT_DELIVERY),
        "total": quote_result.get("total"),
        "quote_result": quote_result,
        "payment_method": customer.get("last_payment_method") or snapshot.get("payment_method"),
        "latitude": customer.get("last_latitude"),
        "longitude": customer.get("last_longitude"),
        "written_address": customer.get("last_written_address"),
        "detected_address": customer.get("last_detected_address"),
        "confirmed_address": customer.get("last_confirmed_address"),
    }
    if not draft.get("confirmed_address"):
        draft["confirmed_address"] = build_confirmed_address(draft)

    missing_delivery = not (draft.get("payment_method") and draft.get("latitude") is not None and draft.get("longitude") is not None and draft.get("confirmed_address"))
    next_state = "WAITING_PAYMENT_AND_LOCATION" if missing_delivery else "WAITING_ADDRESS_CONFIRMATION"

    if missing_delivery:
        text = (
            "Encontré tu última orden y la puedo repetir:\n\n"
            f"{format_quote_lines(items)}\n\n"
            f"Total actualizado: S/ {float(quote_result.get('total') or 0):.2f}\n\n"
            "Me falta confirmar pago/dirección. Envíame forma de pago, dirección escrita y ubicación por WhatsApp."
        )
    else:
        text = (
            "Encontré tu última orden. La puedo repetir así:\n\n"
            f"{format_quote_lines(items)}\n\n"
            f"Total actualizado: S/ {float(quote_result.get('total') or 0):.2f}\n"
            f"Pago: {draft['payment_method']}\n\n"
            "Dirección guardada:\n"
            f"{draft['confirmed_address']}\n\n"
            "Responde SI para confirmar este nuevo pedido o NO para hacer uno desde cero."
        )

    patch_conversation(inbound.whatsapp_number, {"estado": next_state, "pedido_borrador": draft, "last_outbound_text": text})
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state=next_state, repeat_order=True, quote=quote_result)



def handle_new_or_asking(inbound: NormalizedWebhook, conversation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    draft = (conversation or {}).get("pedido_borrador") or {}

    if inbound.message_type == "text" and inbound.message_text:
        if is_menu_request(inbound.message_text) or (draft.get("awaiting_menu_offer") and is_yes(inbound.message_text)):
            text = menu_reply_text()
            patch_conversation(
                inbound.whatsapp_number,
                {"estado": "ASKING_NAME_AND_ITEMS", "pedido_borrador": None, "last_outbound_text": text},
            )
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="ASKING_NAME_AND_ITEMS", menu_url=MENU_URL or None)

        if draft.get("awaiting_menu_offer") and is_no(inbound.message_text):
            text = "Perfecto. Envíame tu nombre y los productos que deseas pedir."
            patch_conversation(
                inbound.whatsapp_number,
                {"estado": "ASKING_NAME_AND_ITEMS", "pedido_borrador": None, "last_outbound_text": text},
            )
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="ASKING_NAME_AND_ITEMS")

    if inbound.message_type != "text" or not inbound.message_text:
        text = menu_offer_text()
        patch_conversation(
            inbound.whatsapp_number,
            {"estado": "ASKING_NAME_AND_ITEMS", "pedido_borrador": {"awaiting_menu_offer": True}, "last_outbound_text": text},
        )
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="ASKING_NAME_AND_ITEMS", awaiting_menu_offer=True)

    try:
        if should_preserve_name_for_item_retry(draft):
            customer_name, items = parse_corrected_items_preserving_name(inbound.message_text, draft.get("customer_name") or "Cliente")
        else:
            customer_name, items = parse_name_and_items(inbound.message_text)
        quote_result = pg_post(
            "/rpc/cotizar_pedido_whatsapp",
            {"p_customer_name": customer_name, "p_items": items, "p_delivery": DEFAULT_DELIVERY},
        )
        quote_items = quote_result.get("items", items)
        sauce_slots = wings_sauce_slots(items)
        draft = {
            "customer_name": customer_name,
            "items": quote_items,
            "original_items": items,
            "subtotal": quote_result.get("subtotal"),
            "delivery": quote_result.get("delivery", DEFAULT_DELIVERY),
            "total": quote_result.get("total"),
            "quote_result": quote_result,
            "awaiting_item_correction": not bool(quote_result.get("ok")),
        }
        if sauce_slots > 0:
            draft["awaiting_wings_sauces"] = True
            draft["wings_sauce_slots"] = sauce_slots
        quote_text = quote_result.get("whatsapp_quote_text") or "Pedido cotizado."
        quote_text = strip_legacy_payment_prompt(quote_text)
        quote_text = append_wings_sauce_prompt(quote_text, items)
        if quote_result.get("ok"):
            quote_text = maybe_attach_smart_upsell(draft, quote_text)
            quote_text += payment_prompt_text()
        next_state = "WAITING_PAYMENT_AND_LOCATION" if quote_result.get("ok") else "ASKING_NAME_AND_ITEMS"
        patch_conversation(inbound.whatsapp_number, {"estado": next_state, "pedido_borrador": draft, "last_outbound_text": quote_text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", quote_text)
        return reply(quote_text, quote=quote_result, parsed_items=items, next_state=next_state)
    except Exception as exc:
        text = (
            "No pude interpretar el pedido todavía.\n\n"
            "¿Quieres que te envíe el menú para descargarlo? Responde SI o escribe MENU.\n\n"
            "Si ya sabes qué pedir, envíalo así:\nJuan Pérez\n1 hamburguesa doble con queso\n1 papas grandes\n1 coca cola mediana\n\n"
            f"Detalle técnico: {exc}"
        )
        patch_conversation(inbound.whatsapp_number, {"estado": "ASKING_NAME_AND_ITEMS", "pedido_borrador": {"awaiting_menu_offer": True}, "last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="ASKING_NAME_AND_ITEMS")


def handle_waiting_payment_and_location(inbound: NormalizedWebhook, conversation: Dict[str, Any]) -> Dict[str, Any]:
    draft = conversation.get("pedido_borrador") or {}

    if is_payment_receipt_media(inbound):
        parsed_payment = parse_payment_method(inbound.message_text)
        written_address = parse_written_address(inbound.message_text) if (inbound.message_text and ADDRESS_LABEL_RE.search(inbound.message_text)) else None
        if parsed_payment:
            draft["payment_method"] = parsed_payment
        elif not draft.get("payment_method"):
            draft["payment_method"] = "YAPE"
        if draft.get("payment_method") == "YAPE":
            draft["yape_channel"] = draft.get("yape_channel") or "APP"
        if written_address:
            draft["written_address"] = written_address
        try:
            receipt = save_payment_receipt(inbound, draft)
            draft["payment_receipt"] = receipt
            draft.setdefault("payment_receipts", []).append(receipt)
            draft["awaiting_yape_receipt"] = False
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
        except Exception as exc:
            text = f"Recibí el comprobante, pero no pude guardarlo: {exc}. Por favor envíalo nuevamente como imagen JPG/PNG o PDF."
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", receipt_saved=False)

        ready = maybe_ready_for_pickup_confirmation(inbound, draft)
        if ready:
            return ready

        missing = []
        if not draft.get("payment_method"):
            missing.append(f"tu forma de pago ({PAYMENT_EXAMPLES_TEXT})")
        if not draft.get("written_address"):
            missing.append("tu dirección escrita")
        if draft.get("latitude") is None or draft.get("longitude") is None:
            missing.append("tu ubicación de WhatsApp")
        if missing:
            if not draft.get("fulfillment_method"):
                draft["awaiting_fulfillment_choice"] = True
                text = fulfillment_choice_prompt("Comprobante de pago guardado ✅")
            else:
                text = "Comprobante de pago guardado ✅\n\nAhora envíame " + " y ".join(missing) + "."
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft, "last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", receipt_saved=True, payment_receipt=draft.get("payment_receipt"), awaiting_fulfillment_choice=draft.get("awaiting_fulfillment_choice", False))

        patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_ADDRESS_CONFIRMATION", "pedido_borrador": draft})
        address_to_confirm = draft.get("confirmed_address") or build_confirmed_address(draft)
        text = (
            "Comprobante de pago guardado ✅\n\n"
            "Confirma la dirección de entrega:\n\n"
            f"{address_to_confirm}\n\n"
            f"Coordenadas: {draft.get('latitude')}, {draft.get('longitude')}\n\n"
            "¿Confirmas que esta es la dirección de entrega?\nResponde SI para confirmar o escribe la dirección corregida."
        )
        patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION", receipt_saved=True, payment_receipt=draft.get("payment_receipt"))

    if inbound.message_type == "text" and inbound.message_text:
        if draft.get("awaiting_wings_sauces"):
            ok, sauce_text = store_wings_sauce_selection(draft, inbound.message_text)
            if not ok:
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft, "last_outbound_text": sauce_text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", sauce_text)
                return reply(sauce_text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_wings_sauces=True)
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
            missing = []
            if not draft.get("payment_method"):
                missing.append(f"tu forma de pago ({PAYMENT_EXAMPLES_TEXT})")
            if not draft.get("written_address"):
                missing.append("tu dirección escrita")
            if draft.get("latitude") is None or draft.get("longitude") is None:
                missing.append("tu ubicación de WhatsApp")
            text = sauce_text + ("\n\nAhora envíame " + " y ".join(missing) + "." if missing else "")
            patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_wings_sauces=False)

        if draft.get("awaiting_smart_upsell"):
            if is_yes(inbound.message_text):
                return apply_smart_upsell(inbound, draft)
            if is_no(inbound.message_text):
                skip_smart_upsell(draft)
                text = "Perfecto, seguimos sin agregar adicionales. Indícame tu forma de pago: " + PAYMENT_EXAMPLES_TEXT + "."
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft, "last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", smart_upsell_added=False)
            if parse_payment_method(inbound.message_text) or parse_yape_channel(inbound.message_text) or parse_written_address(inbound.message_text) or parse_fulfillment_choice(inbound.message_text) or is_pickup_intent(inbound.message_text):
                skip_smart_upsell(draft)
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
            else:
                suggestion = draft.get("smart_upsell_suggestion") or {}
                text = (
                    f"¿Agrego 1 {str(suggestion.get('producto_texto') or 'adicional').title()} al pedido?\n\n"
                    "Responde SI para agregar, NO para continuar sin agregar, o envía tu forma de pago directamente."
                )
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft, "last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_smart_upsell=True)

        yape_channel = parse_yape_channel(inbound.message_text)
        written_address = parse_written_address(inbound.message_text)
        fulfillment_choice = parse_fulfillment_choice(inbound.message_text)
        pickup_requested = fulfillment_choice == "PICKUP" or is_pickup_intent(inbound.message_text)
        delivery_requested = fulfillment_choice == "DELIVERY"
        parsed_payment_for_pickup = parse_payment_method(inbound.message_text)

        if draft.get("awaiting_fulfillment_choice"):
            if delivery_requested:
                mark_delivery_order(draft)
                if written_address:
                    draft["written_address"] = written_address
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
                text = "Perfecto, será delivery ✅\n\nPor favor envíame tu ubicación por WhatsApp para detectar la dirección y pedirte confirmación."
                patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", fulfillment_method="DELIVERY")
            if pickup_requested:
                draft.pop("awaiting_fulfillment_choice", None)
                mark_pickup_order(draft)
                ready = maybe_ready_for_pickup_confirmation(inbound, draft)
                if ready:
                    return ready
            text = fulfillment_choice_prompt("Por favor elige una opción válida.")
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft, "last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_fulfillment_choice=True)

        if pickup_requested:
            mark_pickup_order(draft)
            if parsed_payment_for_pickup:
                draft["payment_method"] = parsed_payment_for_pickup
            if draft.get("payment_method") == "YAPE" and yape_channel:
                draft["yape_channel"] = yape_channel
                draft["awaiting_yape_channel"] = False
                draft["awaiting_yape_receipt"] = yape_channel == "APP"
            if not draft.get("payment_method"):
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
                text = f"Perfecto, será recojo en restaurante. Ahora indícame tu forma de pago: {PAYMENT_EXAMPLES_TEXT}."
                patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", fulfillment_method="PICKUP")
            if draft.get("payment_method") == "YAPE" and not draft.get("yape_channel"):
                draft["awaiting_yape_channel"] = True
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
                text = yape_channel_prompt()
                patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_channel=True, fulfillment_method="PICKUP")
            ready = maybe_ready_for_pickup_confirmation(inbound, draft)
            if ready:
                return ready

        if draft.get("awaiting_yape_channel"):
            if yape_channel == "APP":
                draft["payment_method"] = "YAPE"
                draft["yape_channel"] = "APP"
                draft["awaiting_yape_channel"] = False
                draft["awaiting_yape_receipt"] = True
                if written_address:
                    draft["written_address"] = written_address
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
                text = yape_app_upload_prompt()
                patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_receipt=True)
            if yape_channel == "POS":
                draft["payment_method"] = "YAPE"
                draft["yape_channel"] = "POS"
                draft["awaiting_yape_channel"] = False
                draft["awaiting_yape_receipt"] = False
                if written_address:
                    draft["written_address"] = written_address
                ready = maybe_ready_for_pickup_confirmation(inbound, draft)
                if ready:
                    return ready
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
                if draft.get("latitude") is None or draft.get("longitude") is None:
                    text = (
                        "Perfecto, pago con Yape POS. ¿Será delivery o recojo en restaurante?\n"
                        "Para delivery, envíame dirección escrita y ubicación de WhatsApp. Para recojo, responde RECOJO."
                    )
                else:
                    text = "Perfecto, pago con Yape POS. Ya tengo tu ubicación; envíame tu dirección escrita si falta."
                patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", yape_channel="POS")
            text = "Por favor responde APP si pagará desde la app de Yape, o POS si pagará con POS/maquinita."
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_channel=True)

        parsed_payment = parse_payment_method(inbound.message_text)
        if parsed_payment == "YAPE":
            draft["payment_method"] = "YAPE"
            if written_address:
                draft["written_address"] = written_address
            if yape_channel == "APP":
                draft["yape_channel"] = "APP"
                draft["awaiting_yape_channel"] = False
                draft["awaiting_yape_receipt"] = True
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
                text = yape_app_upload_prompt()
                patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_receipt=True)
            if yape_channel == "POS":
                draft["yape_channel"] = "POS"
                draft["awaiting_yape_channel"] = False
                draft["awaiting_yape_receipt"] = False
                ready = maybe_ready_for_pickup_confirmation(inbound, draft)
                if ready:
                    return ready
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
                if written_address:
                    text = (
                        "Perfecto, registré el pago como: YAPE POS.\n"
                        f"También guardé esta dirección escrita:\n{written_address}\n\n"
                        "Ahora por favor envíame tu ubicación por WhatsApp para confirmar las coordenadas de entrega."
                    )
                else:
                    text = (
                        "Perfecto, registré el pago como: YAPE POS. ¿Será delivery o recojo en restaurante?\n"
                        "Para delivery, envíame dirección escrita y ubicación de WhatsApp. Para recojo, responde RECOJO."
                    )
                patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
                log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
                return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", yape_channel="POS")
            draft["awaiting_yape_channel"] = True
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
            text = yape_channel_prompt()
            patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_channel=True)

        if draft.get("awaiting_yape_receipt"):
            if written_address:
                draft["written_address"] = written_address
                patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
            text = "Estoy esperando el comprobante de Yape APP. Por favor sube la imagen JPG/PNG del pago aprobado."
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_receipt=True)

        if parsed_payment:
            draft["payment_method"] = parsed_payment
            if written_address and not is_pickup_order(draft):
                draft["written_address"] = written_address
            ready = maybe_ready_for_pickup_confirmation(inbound, draft)
            if ready:
                return ready
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
            if written_address:
                text = (
                    f"Perfecto, registré el pago como: {parsed_payment}.\n"
                    f"También guardé esta dirección escrita:\n{written_address}\n\n"
                    "Ahora por favor envíame tu ubicación por WhatsApp para confirmar las coordenadas de entrega."
                )
            else:
                text = (
                    f"Perfecto, registré el pago como: {parsed_payment}. ¿Será delivery o recojo en restaurante?\n"
                    "Para delivery, envíame dirección escrita y ubicación de WhatsApp. Para recojo, responde RECOJO."
                )
            patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION")

        if written_address:
            draft["written_address"] = written_address
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
            text = (
                f"Guardé esta dirección escrita:\n{written_address}\n\n"
                f"Ahora indícame tu forma de pago: {PAYMENT_EXAMPLES_TEXT}, y luego envíame tu ubicación por WhatsApp."
            )
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION")

        text = (
            f"Por favor indícame una forma de pago válida: {PAYMENT_EXAMPLES_TEXT}.\n"
            "También dime si será delivery o recojo en restaurante. Para delivery, envía dirección escrita y ubicación de WhatsApp."
        )
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION")

    if inbound.message_type == "location":
        if inbound.latitude is None or inbound.longitude is None:
            text = "Recibí una ubicación, pero no pude leer la latitud y longitud. Por favor envíala nuevamente."
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION")

        detected_address = reverse_geocode(inbound.latitude, inbound.longitude)
        draft["latitude"] = inbound.latitude
        draft["longitude"] = inbound.longitude
        draft["detected_address"] = detected_address
        draft["confirmed_address"] = build_confirmed_address(draft)

        if draft.get("payment_method") == "YAPE" and draft.get("yape_channel") == "APP" and not draft.get("payment_receipt"):
            draft["awaiting_yape_receipt"] = True
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
            address_to_confirm = draft.get("confirmed_address") or detected_address
            text = (
                "Recibí tu ubicación ✅\n\n"
                "Dirección detectada para confirmar después de guardar el comprobante:\n\n"
                f"{address_to_confirm}\n\n"
                f"Coordenadas: {inbound.latitude}, {inbound.longitude}\n\n"
                "Falta el comprobante de Yape APP. Por favor sube la imagen del pago aprobado para continuar."
            )
            patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_receipt=True)

        if not draft.get("payment_method"):
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
            text = (
                f"Recibí tu ubicación. Ahora indícame tu forma de pago: {PAYMENT_EXAMPLES_TEXT}.\n"
                "Puedes responder por ejemplo: 'pago con yape', 'plin', 'te transfiero' o 'pago al recibir'."
            )
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION")

        patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_ADDRESS_CONFIRMATION", "pedido_borrador": draft})
        address_to_confirm = draft.get("confirmed_address") or detected_address
        text = (
            "Confirma la dirección de entrega:\n\n"
            f"{address_to_confirm}\n\n"
            f"Coordenadas: {inbound.latitude}, {inbound.longitude}\n\n"
            "¿Confirmas que esta es la dirección de entrega?\nResponde SI para confirmar o escribe la dirección corregida."
        )
        patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION")

    text = (
        f"Estoy esperando tu forma de pago y tu ubicación. Formas de pago: {PAYMENT_EXAMPLES_TEXT}.\n"
        "También entiendo frases como 'pago con yape', 'te hago transferencia' o 'pago en efectivo'."
    )
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION")

def confirm_draft_order(inbound: NormalizedWebhook, draft: Dict[str, Any]) -> Dict[str, Any]:
    required = ["customer_name", "items", "payment_method", "confirmed_address"]
    if not is_pickup_order(draft):
        required += ["latitude", "longitude"]
    missing = [key for key in required if draft.get(key) in (None, "", [])]
    if missing:
        patch_conversation(inbound.whatsapp_number, {"estado": "ERROR", "pedido_borrador": draft})
        text = "No pude confirmar el pedido porque faltan datos internos: " + ", ".join(missing) + ". Por favor inicia el pedido nuevamente."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="ERROR", missing=missing)

    original_items = draft.get("original_items", [])
    modifier_notes: List[str] = []
    confirmation_items = []
    for idx, item in enumerate(draft.get("items", [])):
        original_item = original_items[idx] if idx < len(original_items) else {}
        producto_texto = item.get("producto_nombre") or item.get("producto_texto_cliente")
        if original_item.get("modifier_note"):
            producto_texto = f"{producto_texto} ({original_item['modifier_note']})"
            modifier_notes.append(f"{item.get('producto_nombre') or item.get('producto_texto_cliente')}: {original_item['modifier_note']}")
        if original_item.get("attached_via_con") and original_item.get("attached_to_producto_texto"):
            modifier_notes.append(
                f"{item.get('producto_nombre') or item.get('producto_texto_cliente')} agregado por 'con {original_item.get('attachment_source')}' en {original_item.get('attached_to_producto_texto')}"
            )
        confirmation_items.append({
            "producto_id": item.get("producto_id"),
            "producto_texto": producto_texto,
            "cantidad": item.get("cantidad"),
            "unidad": item.get("unidad"),
            "precio_unitario": item.get("precio_unitario"),
        })

    observacion = "Pedido confirmado desde OpenClaw WhatsApp bridge"
    if is_pickup_order(draft):
        observacion += " | Modalidad: RECOJO EN RESTAURANTE"
    else:
        observacion += " | Modalidad: DELIVERY"
    if modifier_notes:
        observacion += " | Notas: " + " ; ".join(modifier_notes)
    if draft.get("payment_method") == "YAPE" and draft.get("yape_channel"):
        observacion += f" | Yape canal: {draft.get('yape_channel')}"
    if draft.get("special_request"):
        observacion += f" | Pedido especial: {draft.get('special_request')}"
    if draft.get("payment_receipt"):
        receipt = draft["payment_receipt"]
        observacion += (
            " | Comprobante de pago guardado: "
            f"{receipt.get('path')} sha256={receipt.get('sha256')} size={receipt.get('size_bytes')}"
        )

    confirm_result = pg_post(
        "/rpc/confirmar_pedido_whatsapp",
        {
            "p_whatsapp_number": inbound.whatsapp_number,
            "p_customer_name": draft["customer_name"],
            "p_payment_method": draft["payment_method"],
            "p_latitude": draft.get("latitude"),
            "p_longitude": draft.get("longitude"),
            "p_detected_address": draft.get("detected_address"),
            "p_confirmed_address": draft["confirmed_address"],
            "p_items": confirmation_items,
            "p_base_url": PUBLIC_ORDER_BASE_URL,
            "p_delivery": draft.get("delivery", DEFAULT_DELIVERY),
            "p_observacion": observacion,
        },
    )
    if draft.get("payment_receipt"):
        try:
            proof_result = register_payment_proof_for_receipt(inbound, draft["payment_receipt"], confirm_result.get("pedido_id"))
            draft["payment_proof_result"] = proof_result
        except Exception as exc:
            logging.warning("Could not register payment proof for pedido_id=%s: %s", confirm_result.get("pedido_id"), exc)
            draft["payment_proof_error"] = str(exc)
    text = confirm_result.get("whatsapp_confirmation_text") or "Pedido confirmado."
    order_url = confirm_result.get("order_url")
    tracking_url = customer_tracking_url(order_url)
    pedido_num = confirm_result.get("pedido_num") or ""
    if tracking_url:
        text = text.replace(
            f"Logística puede abrir el pedido aquí:\n[Abrir pedido {pedido_num}]({order_url})",
            f"Sigue tu pedido aquí:\n{tracking_url}",
        )
        text = text.replace(
            f"Logística puede ver el pedido aquí:\n{order_url}",
            f"Sigue tu pedido aquí:\n{tracking_url}",
        )
        text = text.replace(
            f"Puedes ver tu pedido aquí:\n{order_url}",
            f"Sigue tu pedido aquí:\n{tracking_url}",
        )
        if "Sigue tu pedido aquí:" not in text:
            text += f"\n\nSigue tu pedido aquí:\n{tracking_url}"
        confirm_result["tracking_url"] = tracking_url
    patch_conversation(
        inbound.whatsapp_number,
        {
            "estado": "CONFIRMED",
            "pedido_id": confirm_result.get("pedido_id"),
            "pedido_borrador": {**draft, "confirmation_result": confirm_result},
            "last_outbound_text": text,
        },
    )
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state="CONFIRMED", confirmation=confirm_result)


def is_no_special_request(text: Optional[str]) -> bool:
    if not text:
        return False
    normalized = normalize_loose_text(text)
    return normalized in {"no", "n", "ninguno", "ninguna", "sin pedido especial", "sin pedidos especiales", "sin comentarios", "no gracias", "nada", "nada mas", "nada especial"}


def special_request_prompt_text() -> str:
    return (
        "¿Tienes algún pedido especial o comentario para la orden?\n\n"
        "Por ejemplo: sin cebolla, tocar timbre, enviar cubiertos, etc.\n"
        "Si no tienes, responde NO."
    )


def handle_waiting_special_request(inbound: NormalizedWebhook, conversation: Dict[str, Any]) -> Dict[str, Any]:
    draft = conversation.get("pedido_borrador") or {}
    if inbound.message_type != "text" or not inbound.message_text:
        text = special_request_prompt_text()
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION")
    if is_no_special_request(inbound.message_text):
        draft["special_request"] = None
    else:
        draft["special_request"] = normalize_spaces(inbound.message_text)
    draft["special_request_asked"] = True
    draft.pop("awaiting_special_request", None)
    return confirm_draft_order(inbound, draft)


def handle_waiting_address_confirmation(inbound: NormalizedWebhook, conversation: Dict[str, Any]) -> Dict[str, Any]:
    draft = conversation.get("pedido_borrador") or {}

    if is_payment_receipt_media(inbound):
        try:
            receipt = save_payment_receipt(inbound, draft)
            draft["payment_receipt"] = receipt
            draft.setdefault("payment_receipts", []).append(receipt)
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_ADDRESS_CONFIRMATION", "pedido_borrador": draft})
        except Exception as exc:
            text = f"Recibí el comprobante, pero no pude guardarlo: {exc}. Por favor envíalo nuevamente como imagen JPG/PNG o PDF."
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION", receipt_saved=False)
        text = "Comprobante de pago guardado ✅\n\nAhora confirma la dirección respondiendo SI, o responde NO para corregirla."
        patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION", receipt_saved=True, payment_receipt=draft.get("payment_receipt"))

    if draft.get("awaiting_special_request"):
        return handle_waiting_special_request(inbound, conversation)

    if inbound.message_type != "text" or not inbound.message_text:
        text = "Por favor responde SI para confirmar la dirección o NO para corregirla."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION")

    if draft.get("payment_method") == "YAPE" and draft.get("yape_channel") == "APP" and not draft.get("payment_receipt"):
        draft["awaiting_yape_receipt"] = True
        patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft})
        text = "Antes de confirmar, falta el comprobante de Yape APP. Por favor sube la imagen del pago aprobado."
        patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", awaiting_yape_receipt=True)

    if is_no(inbound.message_text):
        if draft.get("repeat_order"):
            text = (
                "De acuerdo, no repetiré la orden anterior.\n\n"
                "Empecemos desde cero: envíame tu nombre y los productos que deseas comprar."
            )
            patch_conversation(inbound.whatsapp_number, {"estado": "ASKING_NAME_AND_ITEMS", "pedido_borrador": None, "last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="ASKING_NAME_AND_ITEMS")
        if is_pickup_order(draft):
            text = (
                "De acuerdo. ¿Prefieres delivery? Envíame tu dirección escrita y ubicación de WhatsApp, "
                "o escribe RECOJO para mantener recojo en restaurante."
            )
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_PAYMENT_AND_LOCATION", "pedido_borrador": draft, "last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_PAYMENT_AND_LOCATION", fulfillment_method="PICKUP")
        draft["awaiting_corrected_address"] = True
        patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_ADDRESS_CONFIRMATION", "pedido_borrador": draft})
        text = (
            "De acuerdo. Escríbeme la dirección correcta de entrega.\n\n"
            "No necesitas enviar otra vez el pago ni la ubicación; usaré las coordenadas que ya enviaste y registraré la dirección corregida."
        )
        patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION", awaiting_corrected_address=True)

    if draft.get("awaiting_corrected_address") and not is_yes(inbound.message_text):
        corrected_address = parse_written_address(inbound.message_text) or normalize_spaces(inbound.message_text)
        if len(corrected_address) < 6:
            text = "Por favor escríbeme la dirección correcta completa para confirmar el pedido."
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION", awaiting_corrected_address=True)
        draft["written_address"] = corrected_address
        draft["confirmed_address"] = corrected_address
        draft["address_corrected_by_customer"] = True
        draft.pop("awaiting_corrected_address", None)
        patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_ADDRESS_CONFIRMATION", "pedido_borrador": draft})

    elif not is_yes(inbound.message_text):
        corrected_address = parse_written_address(inbound.message_text) or normalize_spaces(inbound.message_text)
        if len(corrected_address) >= 6:
            draft["written_address"] = corrected_address
            draft["confirmed_address"] = corrected_address
            draft["address_corrected_by_customer"] = True
            patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_ADDRESS_CONFIRMATION", "pedido_borrador": draft})
        else:
            text = "Por favor responde SI para confirmar la dirección o escribe la dirección correcta completa."
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION")

    if not draft.get("special_request_asked"):
        draft["awaiting_special_request"] = True
        patch_conversation(inbound.whatsapp_number, {"estado": "WAITING_ADDRESS_CONFIRMATION", "pedido_borrador": draft})
        text = special_request_prompt_text()
        patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="WAITING_ADDRESS_CONFIRMATION", awaiting_special_request=True)

    return confirm_draft_order(inbound, draft)


def confirmed_order_summary_text(conversation: Dict[str, Any]) -> Optional[str]:
    draft = conversation.get("pedido_borrador") or {}
    confirmation = draft.get("confirmation_result") or {}
    pedido_num = first_non_empty(
        confirmation.get("pedido_num"),
        draft.get("pedido_num"),
        (draft.get("quote_result") or {}).get("pedido_num"),
    )
    total = first_non_empty(
        confirmation.get("total"),
        draft.get("total"),
        (draft.get("quote_result") or {}).get("total"),
    )
    order_url = first_non_empty(
        confirmation.get("order_url"),
        draft.get("order_url"),
    )
    tracking_url = first_non_empty(
        confirmation.get("tracking_url"),
        draft.get("tracking_url"),
        customer_tracking_url(order_url),
    )
    if not pedido_num:
        return None
    text = f"Tu pedido ya está confirmado ✅\n\nPedido: {pedido_num}"
    if total is not None:
        try:
            text += f"\nTotal: S/ {float(total):.2f}"
        except Exception:
            text += f"\nTotal: S/ {total}"
    if tracking_url:
        text += f"\n\nSigue tu pedido aquí:\n{tracking_url}"
    return text


def handle_confirmed(inbound: NormalizedWebhook, conversation: Dict[str, Any]) -> Dict[str, Any]:
    if is_payment_receipt_media(inbound):
        draft = dict(conversation.get("pedido_borrador") or {})
        pedido_id = conversation.get("pedido_id") or draft.get("pedido_id")
        if not pedido_id:
            text = (
                "Recibí la imagen, pero no pude relacionarla con un pedido confirmado. "
                "Envíame el número del pedido para que el equipo pueda revisarlo."
            )
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="CONFIRMED", receipt_saved=False)
        try:
            receipt = save_payment_receipt(inbound, draft)
            proof_result = register_payment_proof_for_receipt(inbound, receipt, int(pedido_id))
        except Exception as exc:
            logging.warning("Could not save/register web-order payment proof pedido_id=%s: %s", pedido_id, exc)
            text = (
                "Recibí el comprobante, pero no pude guardarlo. "
                "Por favor envíalo nuevamente como imagen JPG/PNG o PDF."
            )
            patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="CONFIRMED", receipt_saved=False)
        draft["payment_receipt"] = receipt
        draft.setdefault("payment_receipts", []).append(receipt)
        draft["payment_proof_result"] = proof_result
        text = proof_result.get("whatsapp_reply_text") or (
            "Recibí tu comprobante de pago ✅\n\n"
            "Lo enviaremos a revisión. Te avisaremos cuando sea verificado."
        )
        patch_conversation(
            inbound.whatsapp_number,
            {"estado": "CONFIRMED", "pedido_borrador": draft, "last_outbound_text": text},
        )
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(
            text,
            next_state="CONFIRMED",
            receipt_saved=True,
            payment_proof=proof_result,
        )
    if inbound.message_type == "text" and is_web_order_handoff(inbound.message_text):
        text = confirmed_order_summary_text(conversation) or "Recibí los datos de tu pedido web ✅"
        if "Yape" in inbound.message_text or "Plin" in inbound.message_text or "Transferencia" in inbound.message_text:
            text += "\n\nAhora envía aquí la foto de tu comprobante de pago."
        patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="CONFIRMED", web_order_handoff=True)
    if inbound.message_type == "text" and inbound.message_text and len(inbound.message_text.splitlines()) >= 2:
        patch_conversation(inbound.whatsapp_number, {"estado": "ASKING_NAME_AND_ITEMS"})
        return handle_new_or_asking(inbound, conversation)
    if inbound.message_type == "text" and is_yes(inbound.message_text):
        text = confirmed_order_summary_text(conversation)
        if text:
            patch_conversation(inbound.whatsapp_number, {"last_outbound_text": text})
            log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
            return reply(text, next_state="CONFIRMED", recovered_confirmation=True)
    text = "Tu pedido ya fue confirmado. Si deseas hacer otro pedido, envíame tu nombre y los nuevos productos en un nuevo mensaje."
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state="CONFIRMED")



def get_active_driver_by_whatsapp(identity: IdentityLike) -> Optional[Dict[str, Any]]:
    safe_number = quote(legacy_whatsapp_number(identity), safe="")
    try:
        rows = pg_get(f"/repartidores?whatsapp_number=eq.{safe_number}&activo=eq.true&select=*&limit=1")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in {404, 406}:
            return None
        body = exc.response.text if exc.response is not None else ""
        if "repartidores" in body or "PGRST" in body:
            return None
        raise
    return rows[0] if rows else None


def driver_delivered_words(text: Optional[str]) -> bool:
    value = normalize_loose_text(text or "")
    return value in {
        "entregado",
        "entregue",
        "entregue",
        "pedido entregado",
        "ya entregue",
        "ya entregue",
        "delivery entregado",
        "finalizado",
        "completado",
        "complete",
        "delivered",
    }


def driver_pickup_words(text: Optional[str]) -> bool:
    value = normalize_loose_text(text or "")
    return value in {
        "recogido",
        "recogi",
        "recogí",
        "ya recogi",
        "ya recogí",
        "pedido recogido",
        "en camino",
        "sali",
        "salí",
        "ya sali",
        "ya salí",
        "voy en camino",
    }


def driver_arrived_words(text: Optional[str]) -> bool:
    value = normalize_loose_text(text or "")
    return value in {
        "llegue",
        "llegué",
        "ya llegue",
        "ya llegué",
        "estoy afuera",
        "estoy en puerta",
        "en puerta",
        "llegue al punto",
        "llegué al punto",
        "arrived",
    }


def driver_account_words(text: Optional[str]) -> bool:
    value = normalize_loose_text(text or "")
    return value in {"cuenta", "mi cuenta", "saldo", "liquidacion", "liquidación", "pago", "pagos"}


def load_delivery_payouts() -> Dict[str, Any]:
    try:
        if DELIVERY_PAYOUTS_PATH.exists():
            data = json.loads(DELIVERY_PAYOUTS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("batches", [])
                return data
    except Exception:
        pass
    return {"next_id": 1, "batches": []}


def delivery_payout_assignment_ids(payouts: Dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for batch in payouts.get("batches", []):
        if str(batch.get("status") or "OPEN").upper() not in {"OPEN", "PAID"}:
            continue
        for assignment_id in batch.get("assignment_ids", []):
            try:
                ids.add(int(assignment_id))
            except Exception:
                continue
    return ids


def numeric_value(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def handle_driver_account_request(inbound: NormalizedWebhook, driver: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not driver_account_words(inbound.message_text if inbound.message_type == "text" else None):
        return None
    driver_id = driver.get("id")
    payouts = load_delivery_payouts()
    batched_ids = delivery_payout_assignment_ids(payouts)
    assignments = pg_get(
        f"/v_delivery_asignaciones?repartidor_id=eq.{quote(str(driver_id), safe='')}"
        "&status=eq.COMPLETED&order=completed_at.desc.nullslast,created_at.desc&limit=100"
    )
    pending = []
    for assignment in assignments:
        try:
            assignment_id = int(assignment.get("id"))
        except Exception:
            continue
        if assignment_id not in batched_ids:
            pending.append(assignment)
    pending_total = sum(numeric_value(a.get("fee")) for a in pending)
    open_total = 0.0
    open_count = 0
    paid_total = 0.0
    for batch in payouts.get("batches", []):
        try:
            batch_driver_id = int(batch.get("repartidor_id"))
        except Exception:
            continue
        if str(batch_driver_id) != str(driver_id):
            continue
        status = str(batch.get("status") or "OPEN").upper()
        if status == "OPEN":
            open_total += numeric_value(batch.get("total_amount"))
            open_count += int(batch.get("route_count") or 0)
        elif status == "PAID":
            paid_total += numeric_value(batch.get("total_amount"))

    text = (
        "Cuenta repartidor 🧾\n\n"
        f"Rutas completadas por consolidar: {len(pending)}\n"
        f"Saldo por consolidar: S/ {pending_total:.2f}\n"
        f"Liquidaciones abiertas: {open_count} rutas · S/ {open_total:.2f}\n"
        f"Total histórico pagado: S/ {paid_total:.2f}"
    )
    if pending[:5]:
        latest = ", ".join(str(a.get("pedido_num") or a.get("pedido_id")) for a in pending[:5])
        text += f"\n\nÚltimas pendientes: {latest}"
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state="DRIVER_ACCOUNT", driver=True)


def active_driver_assignment(driver: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    safe_driver_id = quote(str(driver.get("id") or ""), safe="")
    assignments = pg_get(
        f"/v_delivery_asignaciones?repartidor_id=eq.{safe_driver_id}"
        "&status=in.(ASSIGNED,ACCEPTED,OFFERED,PICKED_UP,EN_ROUTE,ARRIVED)&order=assigned_at.desc.nullslast,offered_at.desc,created_at.desc&limit=1"
    )
    return assignments[0] if assignments else None


def queue_customer_delivery_update(pedido_id: Any, message_text: str, event_type: str = "CUSTOM") -> None:
    rows = pg_get(f"/v_pedidos_logistica?id=eq.{quote(str(pedido_id), safe='')}&select=id,whatsapp_number&limit=1")
    if not rows or not rows[0].get("whatsapp_number"):
        return
    pg_post(
        "/whatsapp_outbox",
        {
            "pedido_id": pedido_id,
            "whatsapp_number": rows[0]["whatsapp_number"],
            "message_text": message_text,
            "event_type": event_type,
            "status": "PENDING",
        },
    )


def append_assignment_note(assignment: Dict[str, Any], note: str) -> None:
    existing = str(assignment.get("notes") or "").strip()
    updated = f"{existing}\n{note}" if existing else note
    pg_patch(f"/delivery_asignaciones?id=eq.{quote(str(assignment.get('id')), safe='')}", {"notes": updated})

def driver_delivery_transition(inbound: NormalizedWebhook, assignment: Dict[str, Any], action: str) -> Dict[str, Any]:
    raw=inbound.raw_payload or {}
    seed=str(raw.get("message_id") or f"{inbound.whatsapp_number}:{inbound.message_text}:{action}")
    key=f"driver-{assignment.get('id')}-{action.lower()}-{hashlib.sha256(seed.encode()).hexdigest()[:24]}"
    return pg_post("/rpc/update_delivery_operation",{
        "p_assignment_id":assignment.get("id"),"p_action":action,"p_actor":f"driver-{assignment.get('repartidor_codigo') or assignment.get('repartidor_id')}",
        "p_reason":None,"p_priority":None,"p_promised_at":None,"p_idempotency_key":key})


def update_driver_pickup_or_arrival(inbound: NormalizedWebhook, driver: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text_value = inbound.message_text if inbound.message_type == "text" else None
    is_pickup = driver_pickup_words(text_value)
    is_arrived = driver_arrived_words(text_value)
    if not (is_pickup or is_arrived):
        return None

    assignment = active_driver_assignment(driver)
    if not assignment:
        text = "No tienes un pedido activo para actualizar."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_NO_ACTIVE_ASSIGNMENT", driver=True)

    pedido_id = assignment.get("pedido_id")
    pedido_num = assignment.get("pedido_num") or "pedido"
    tracking_url = ""
    try:
        rows = pg_get(f"/v_pedidos_logistica?id=eq.{quote(str(pedido_id), safe='')}&select=order_url&limit=1")
        if rows and rows[0].get("order_url"):
            tracking_url = customer_tracking_url(rows[0]["order_url"])
    except Exception:
        tracking_url = ""

    if is_pickup:
        driver_delivery_transition(inbound,assignment,"EN_ROUTE")
        text = f"Salida confirmada ✅\n\n{pedido_num} quedó como EN CAMINO."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_ON_THE_WAY", driver=True, dispatch={"pedido_id": pedido_id, "pedido_num": pedido_num, "assignment_id": assignment.get("id")})

    driver_delivery_transition(inbound,assignment,"ARRIVE")
    text = f"Llegada confirmada ✅\n\nAvisé al cliente que {pedido_num} ya llegó."
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state="DRIVER_ARRIVED", driver=True, dispatch={"pedido_id": pedido_id, "pedido_num": pedido_num, "assignment_id": assignment.get("id")})


def complete_driver_delivery(inbound: NormalizedWebhook, driver: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not driver_delivered_words(inbound.message_text if inbound.message_type == "text" else None):
        return None
    assignment = active_driver_assignment(driver)
    if not assignment:
        text = "No tienes un pedido activo para marcar como entregado."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_NO_ACTIVE_ASSIGNMENT", driver=True)

    pedido_id = assignment.get("pedido_id")
    pedido_num = assignment.get("pedido_num") or "pedido"
    try:
        driver_delivery_transition(inbound,assignment,"DELIVER")
    except requests.HTTPError as exc:
        text="No pude completar la entrega. Confirma primero el cobro/pago en Logistics."
        log_whatsapp_message(inbound.whatsapp_number,"OUTBOUND","text",text)
        return reply(text,next_state="DRIVER_DELIVERY_BLOCKED",driver=True,dispatch={"pedido_id":pedido_id,"assignment_id":assignment.get("id"),"error":str(exc)})
    except Exception as exc:
        logging.warning("Could not complete driver delivery %s: %s",pedido_num,exc)
        raise

    text = f"Entrega confirmada ✅\n\n{pedido_num} quedó marcado como ENTREGADO."
    log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
    return reply(text, next_state="DRIVER_DELIVERED", driver=True, dispatch={"pedido_id": pedido_id, "pedido_num": pedido_num, "assignment_id": assignment.get("id")})


def handle_driver_message(inbound: NormalizedWebhook, driver: Dict[str, Any]) -> Dict[str, Any]:
    account_result = handle_driver_account_request(inbound, driver)
    if account_result is not None:
        return account_result

    delivered_result = complete_driver_delivery(inbound, driver)
    if delivered_result is not None:
        return delivered_result

    pickup_or_arrival_result = update_driver_pickup_or_arrival(inbound, driver)
    if pickup_or_arrival_result is not None:
        return pickup_or_arrival_result

    response_text = inbound.message_text if inbound.message_type == "text" else None
    try:
        result = pg_post(
            "/rpc/repartidor_responder_delivery",
            {
                "p_whatsapp_number": inbound.whatsapp_number,
                "p_response_text": response_text,
                "p_latitude": inbound.latitude,
                "p_longitude": inbound.longitude,
            },
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        body = exc.response.text if exc.response is not None else ""
        if status in {404, 406} or "repartidor_responder_delivery" in body:
            return reply("", next_state="DRIVER_MODULE_NOT_INSTALLED", driver=True)
        raise

    driver_status = result.get("driver_status") if isinstance(result, dict) else None
    if driver_status == "UNKNOWN_RESPONSE":
        text = "Responde ACEPTAR para tomar el delivery o NO para pasarlo."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_PENDING_RESPONSE", driver=True, dispatch=result)
    if driver_status == "NO_PENDING_OFFER":
        text = "No tienes deliveries pendientes por aceptar en este momento."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_NO_PENDING_OFFER", driver=True, dispatch=result)
    if driver_status == "NO_ACTIVE_ASSIGNMENT":
        text = "Recibí tu ubicación, pero no tienes un pedido asignado activo."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_NO_ACTIVE_ASSIGNMENT", driver=True, dispatch=result)
    if driver_status == "LOCATION_SAVED":
        text = "Ubicación recibida ✅ Gracias."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_LOCATION_SAVED", driver=True, dispatch=result)
    if driver_status == "REJECTED":
        text = "Entendido, pasaré el delivery al siguiente repartidor."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_REJECTED", driver=True, dispatch=result)
    if driver_status == "ASSIGNED":
        # Assignment details are queued through whatsapp_outbox by the dispatch RPC.
        # Make the next operational actions explicit for the repartidor.
        text = "Pedido asignado ✅\n\nComparte tu ubicación. Cuando recojas el pedido responde RECOGIDO. Al llegar responde LLEGUÉ. Cuando lo entregues, responde ENTREGADO para cerrar el delivery."
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="DRIVER_ASSIGNED", driver=True, dispatch=result)
    return reply("", next_state="DRIVER_HANDLED", driver=True, dispatch=result)


def _route_message_scoped(inbound: NormalizedWebhook) -> Dict[str, Any]:
    identity = conversation_identity_from_inbound(inbound)
    register_conversation_request(inbound, identity)
    log_whatsapp_message(
        identity,
        "INBOUND",
        inbound.message_type,
        inbound.message_text,
        inbound.latitude,
        inbound.longitude,
        inbound.raw_payload,
    )
    conversation = get_conversation(identity)
    state = (conversation or {}).get("estado") or "NEW"
    logging.info(
        "Routing message from %s channel=%s/%s type=%s state=%s",
        identity.customer_address, identity.channel_kind, identity.channel_id, inbound.message_type, state,
    )

    driver = get_active_driver_by_whatsapp(identity)
    if driver:
        return handle_driver_message(inbound, driver)

    block_entry = get_block_entry(identity)
    if block_entry:
        patch_conversation(
            identity,
            {
                "estado": "CANCELLED",
                "last_outbound_text": ABUSE_MESSAGE,
            },
        )
        return reply("", next_state="CANCELLED", blocked=True, block_reason=(block_entry or {}).get("reason"))

    abuse_reason = detect_abuse_reason(inbound.message_text if inbound.message_type == "text" else None)
    if abuse_reason:
        block_number(identity, abuse_reason, inbound.message_text)
        patch_conversation(
            identity,
            {
                "estado": "CANCELLED",
                "last_outbound_text": ABUSE_MESSAGE,
                "pedido_borrador": None,
            },
        )
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", ABUSE_MESSAGE)
        return reply(ABUSE_MESSAGE, next_state="CANCELLED", blocked=True, block_reason=abuse_reason)

    handoff = active_handoff_entry(identity)
    if handoff:
        logging.info("Human handoff active for %s; suppressing bot reply", identity.customer_address)
        return reply(
            "",
            next_state=state,
            human_handoff=True,
            handoff_reason=handoff.get("reason") or "",
            handoff_updated_at=handoff.get("updated_at") or "",
        )

    # Storefront orders are already confirmed transactions. Recognize their
    # signed handoff independently of stale WhatsApp conversation state.
    if inbound.message_type == "text" and is_web_order_handoff(inbound.message_text):
        return handle_web_order_handoff(inbound)

    if ordering_is_paused() and state not in {"CONFIRMED"}:
        text = restaurant_closed_reply_text()
        patch_conversation(
            identity,
            {
                "estado": state if state not in {"NEW", "ERROR", "CANCELLED"} else "ASKING_NAME_AND_ITEMS",
                "last_outbound_text": text,
            },
        )
        log_whatsapp_message(identity, "OUTBOUND", "text", text)
        return reply(text, next_state=state, ordering_paused=True, restaurant_status=restaurant_status())

    if inbound.message_type == "text" and is_menu_request(inbound.message_text):
        text = menu_reply_text()
        patch_conversation(inbound.whatsapp_number, {"estado": "ASKING_NAME_AND_ITEMS", "pedido_borrador": None, "last_outbound_text": text})
        log_whatsapp_message(inbound.whatsapp_number, "OUTBOUND", "text", text)
        return reply(text, next_state="ASKING_NAME_AND_ITEMS", menu_url=MENU_URL or None)

    if inbound.message_type == "text" and is_repeat_order_intent(inbound.message_text):
        return handle_repeat_order(inbound, conversation)

    if (
        inbound.message_type == "text"
        and state in {"WAITING_PAYMENT_AND_LOCATION", "WAITING_ADDRESS_CONFIRMATION"}
        and not ((conversation or {}).get("pedido_borrador") or {}).get("awaiting_corrected_address")
        and looks_like_fresh_order(inbound.message_text)
    ):
        # A customer may start a new order while an old conversation is still waiting
        # for payment/address. Always quote the new order first instead of treating it
        # as delivery info for the stale draft.
        patch_conversation(inbound.whatsapp_number, {"estado": "ASKING_NAME_AND_ITEMS", "pedido_borrador": None})
        return handle_new_or_asking(inbound, conversation)

    if state in {"NEW", "ASKING_NAME_AND_ITEMS", "ERROR", "CANCELLED"}:
        return handle_new_or_asking(inbound, conversation)
    if state == "WAITING_PAYMENT_AND_LOCATION":
        return handle_waiting_payment_and_location(inbound, conversation)
    if state == "WAITING_ADDRESS_CONFIRMATION":
        return handle_waiting_address_confirmation(inbound, conversation)
    if state == "CONFIRMED":
        return handle_confirmed(inbound, conversation)
    return handle_new_or_asking(inbound, conversation)


def route_message(inbound: NormalizedWebhook) -> Dict[str, Any]:
    identity = conversation_identity_from_inbound(inbound)
    token = ACTIVE_CONVERSATION_IDENTITY.set(identity)
    try:
        return _route_message_scoped(inbound)
    finally:
        ACTIVE_CONVERSATION_IDENTITY.reset(token)


@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        pg_get("/")
        handoffs = load_human_handoffs()
        active_count = len([entry for entry in handoffs.values() if isinstance(entry, dict) and entry.get("active", True)])
        return {
            "ok": True,
            "postgrest_ok": True,
            "postgrest_base_url": POSTGREST_BASE_URL,
            "restaurant_status": restaurant_status(),
            "human_handoff_active": active_count,
        }
    except Exception as exc:
        return {"ok": False, "postgrest_ok": False, "error": str(exc), "restaurant_status": restaurant_status()}


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, x_hook_token: Optional[str] = Header(default=None)) -> JSONResponse:
    if REQUIRE_HOOK_TOKEN:
        token = x_hook_token or request.query_params.get("token")
        if token != HOOK_TOKEN:
            raise HTTPException(status_code=401, detail="Invalid hook token")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    try:
        inbound = extract_payload(data)
        rate_limit_reason = inbound_rate_limit_reason(inbound)
        if rate_limit_reason:
            identity = conversation_identity_from_inbound(inbound)
            logging.warning(
                "Rate limited WhatsApp inbound channel=%s/%s reason=%s",
                identity.channel_kind,
                identity.channel_id,
                rate_limit_reason,
            )
            return JSONResponse(
                reply(RATE_LIMIT_MESSAGE, next_state="RATE_LIMITED", rate_limited=True, rate_limit_reason=rate_limit_reason)
            )
        result = route_message(inbound)
        return JSONResponse(result)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 500
        body = exc.response.text if exc.response is not None else str(exc)
        logging.exception("PostgREST HTTP error")
        raise HTTPException(status_code=500, detail={"message": "PostgREST error", "postgrest_status": status, "postgrest_body": body})
    except Exception as exc:
        logging.exception("Webhook processing error")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/debug/parse")
async def debug_parse(request: Request) -> Dict[str, Any]:
    data = await request.json()
    try:
        customer_name, items = parse_name_and_items(data.get("message_text", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"customer_name": customer_name, "items": items}


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("BRIDGE_PORT", "8789"))
    uvicorn.run("bridge:app", host=host, port=port, reload=False)
