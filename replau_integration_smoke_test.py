#!/usr/bin/env python3
"""
Replau end-to-end integration smoke test.

This test is intentionally safe:
- Creates a new test WhatsApp conversation/order using a unique phone number.
- Exercises PostgREST, quote RPC, inbound bridge, kitchen UI, logistics viewer,
  public order token flow, kitchen status RPC, and send adapter DRY RUN.
- Does NOT send real WhatsApp messages or real emails.

Exit code 0 means the core components are interacting correctly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


def process_env_value(process_marker: str, env_name: str) -> str:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "ignore")
            if process_marker not in cmdline:
                continue
            with open(f"/proc/{pid}/environ", "rb") as f:
                environ = f.read().split(b"\0")
        except OSError:
            continue
        prefix = f"{env_name}=".encode()
        for item in environ:
            if item.startswith(prefix):
                return item[len(prefix):].decode("utf-8", "ignore")
    return ""


def resolve_token(value: str, env_name: str, process_marker: str) -> str:
    return value or os.environ.get(env_name, "") or process_env_value(process_marker, env_name)


@dataclass
class Config:
    postgrest: str = "http://127.0.0.1:3000"
    bridge: str = "http://127.0.0.1:8789"
    logistics: str = "http://127.0.0.1:8790"
    kitchen: str = "http://127.0.0.1:8791"
    send_adapter: str = "http://127.0.0.1:8792"
    bridge_token: str = "RESTRICTED"
    send_token: str = "RESTRICTED"
    timeout: int = 60


class SmokeTestError(RuntimeError):
    pass


class Runner:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.created: dict[str, Any] = {}
        self.steps: list[tuple[str, str]] = []

    def log(self, name: str, detail: str = "OK") -> None:
        self.steps.append((name, detail))
        print(f"[OK] {name}: {detail}")

    def fail(self, name: str, detail: str) -> None:
        print(f"[FAIL] {name}: {detail}", file=sys.stderr)
        raise SmokeTestError(f"{name}: {detail}")

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.session.get(url, timeout=self.cfg.timeout, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.session.post(url, timeout=self.cfg.timeout, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> requests.Response:
        return self.session.patch(url, timeout=self.cfg.timeout, **kwargs)

    def require_ok(self, name: str, response: requests.Response) -> Any:
        if not response.ok:
            self.fail(name, f"HTTP {response.status_code}: {response.text[:600]}")
        ctype = response.headers.get("content-type", "")
        if "application/json" in ctype or "+json" in ctype:
            return response.json()
        return response.text

    def pg_get(self, path: str) -> Any:
        return self.require_ok(f"PostgREST GET {path}", self.get(self.cfg.postgrest + path))

    def pg_rpc(self, name: str, payload: dict[str, Any]) -> Any:
        return self.require_ok(
            f"PostgREST RPC {name}",
            self.post(self.cfg.postgrest + f"/rpc/{name}", json=payload),
        )

    def bridge_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self.require_ok(
            "Bridge webhook",
            self.post(
                self.cfg.bridge + "/webhook/whatsapp",
                json=payload,
                headers={"X-Hook-Token": self.cfg.bridge_token},
            ),
        )
        if not isinstance(data, dict) or not data.get("ok"):
            self.fail("Bridge webhook", json.dumps(data, ensure_ascii=False)[:800])
        return data

    def health_checks(self) -> None:
        root = self.require_ok("PostgREST root", self.get(self.cfg.postgrest + "/"))
        paths = root.get("paths", {}) if isinstance(root, dict) else {}
        for required in ["/productos", "/pedidos", "/pedido_items", "/rpc/cotizar_pedido_whatsapp"]:
            if required not in paths:
                self.fail("PostgREST root", f"missing path {required}")
        self.log("PostgREST root", "key paths present")

        for name, base in [
            ("bridge", self.cfg.bridge),
            ("logistics", self.cfg.logistics),
            ("kitchen", self.cfg.kitchen),
            ("send_adapter", self.cfg.send_adapter),
        ]:
            data = self.require_ok(f"{name} health", self.get(base + "/health"))
            if not isinstance(data, dict) or not data.get("ok"):
                self.fail(f"{name} health", json.dumps(data, ensure_ascii=False))
            self.log(f"{name} health", "healthy")

    def product_quote_checks(self) -> None:
        menu = self.pg_get("/productos?cdg_prod=eq.BURGER_DOUBLE_CHEESE&select=cdg_prod,nombre,active")
        if not menu or not menu[0].get("active"):
            self.fail("burger menu product", "BURGER_DOUBLE_CHEESE missing/inactive")
        self.log("burger menu product", menu[0]["nombre"])

        quote = self.pg_rpc(
            "cotizar_pedido_whatsapp",
            {
                "p_customer_name": "Smoke Test",
                "p_items": [
                    {"producto_texto": "hamburguesa doble con queso", "cantidad": 1, "unidad": "UNIDAD"},
                    {"producto_texto": "papas fritas grandes", "cantidad": 1, "unidad": "UNIDAD"},
                    {"producto_texto": "aros de cebolla pequenos", "cantidad": 1, "unidad": "UNIDAD"},
                ],
                "p_delivery": 0,
            },
        )
        if not quote.get("ok") or quote.get("errors_count") != 0:
            self.fail("quote RPC", json.dumps(quote, ensure_ascii=False)[:1000])
        if float(quote.get("total", 0)) <= 0:
            self.fail("quote RPC", "non-positive total")
        self.log("quote RPC", f"total S/ {quote.get('total')}")

    def conversation_order_flow(self) -> None:
        unique = int(time.time()) % 1_000_000
        phone = f"51988{unique:06d}"
        customer = f"Smoke Burger {unique}"
        special_request = "Sin cebolla; tocar timbre"
        self.created["phone"] = phone
        self.created["customer"] = customer
        self.created["special_request"] = special_request

        first = self.bridge_webhook(
            {
                "whatsapp_number": phone,
                "message_type": "text",
                "message_text": f"{customer}\n1 hamburguesa doble con queso\n1 papas fritas grandes\n1 aros de cebolla pequenos",
            }
        )
        if first.get("next_state") != "WAITING_PAYMENT_AND_LOCATION":
            self.fail("bridge quote flow", f"unexpected state {first.get('next_state')}: {first}")
        self.log("bridge quote flow", "WAITING_PAYMENT_AND_LOCATION")

        pay = self.bridge_webhook({"whatsapp_number": phone, "message_type": "text", "message_text": "Yape"})
        if pay.get("next_state") != "WAITING_PAYMENT_AND_LOCATION":
            self.fail("bridge payment flow", f"unexpected state {pay.get('next_state')}: {pay}")
        self.log("bridge payment flow", "payment accepted")

        loc = self.bridge_webhook(
            {
                "whatsapp_number": phone,
                "message_type": "location",
                "latitude": -12.046374,
                "longitude": -77.042793,
            }
        )
        if loc.get("next_state") != "WAITING_ADDRESS_CONFIRMATION":
            self.fail("bridge location flow", f"unexpected state {loc.get('next_state')}: {loc}")
        self.log("bridge location flow", "WAITING_ADDRESS_CONFIRMATION")

        conf = self.bridge_webhook({"whatsapp_number": phone, "message_type": "text", "message_text": "SI"})
        if not conf.get("awaiting_special_request"):
            self.fail("bridge special request prompt", json.dumps(conf, ensure_ascii=False)[:1200])
        self.log("bridge special request prompt", "prompted")

        conf = self.bridge_webhook(
            {"whatsapp_number": phone, "message_type": "text", "message_text": special_request}
        )
        if conf.get("next_state") != "CONFIRMED" or not conf.get("confirmation", {}).get("pedido_num"):
            self.fail("bridge confirmation flow", json.dumps(conf, ensure_ascii=False)[:1200])
        confirmation = conf["confirmation"]
        if "/track/" not in str(confirmation.get("tracking_url") or ""):
            self.fail("bridge confirmation flow", f"missing customer tracking_url: {json.dumps(confirmation, ensure_ascii=False)[:1200]}")
        self.created.update(
            {
                "pedido_id": confirmation.get("pedido_id"),
                "pedido_num": confirmation.get("pedido_num"),
                "order_url": confirmation.get("order_url"),
                "tracking_url": confirmation.get("tracking_url"),
                "total": confirmation.get("total"),
            }
        )
        self.log("bridge confirmation flow", f"{self.created['pedido_num']} total S/ {self.created['total']}")

    def human_handoff_flow(self) -> None:
        phone = self.created.get("phone")
        if not phone:
            self.fail("Human handoff flow", "missing smoke phone")
        try:
            start = self.post(
                self.cfg.logistics + "/handoff/start",
                data={"whatsapp_number": phone, "reason": "Smoke handoff", "next_url": "/dashboard"},
                allow_redirects=False,
            )
            if start.status_code not in {302, 303}:
                self.fail("Human handoff start", f"HTTP {start.status_code}: {start.text[:600]}")

            result = self.bridge_webhook(
                {"whatsapp_number": phone, "message_type": "text", "message_text": "necesito ayuda con mi pedido"}
            )
            if not result.get("human_handoff") or result.get("reply_text") != "":
                self.fail("Human handoff bridge suppression", json.dumps(result, ensure_ascii=False)[:1000])
            self.log("Human handoff bridge suppression", "bot reply suppressed")
        finally:
            resume = self.post(
                self.cfg.logistics + "/handoff/resume",
                data={"whatsapp_number": phone, "next_url": "/dashboard"},
                allow_redirects=False,
            )
            if resume.status_code not in {302, 303}:
                self.fail("Human handoff resume", f"HTTP {resume.status_code}: {resume.text[:600]}")
        self.log("Human handoff resume", "bot reactivated")

    def neutralize_test_email_queues(self) -> int:
        """Mark smoke-test logistics emails handled before the live worker can send them."""
        pedido_id = self.created.get("pedido_id")
        if not pedido_id:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        email_response = self.patch(
            self.cfg.postgrest + f"/email_logistica_log?pedido_id=eq.{pedido_id}&status=in.(PENDING,SENDING,ERROR)",
            json={
                "status": "SENT",
                "sent_at": now,
                "error_message": "Neutralized by integration smoke test cleanup; no real email should be sent.",
            },
            headers={"Prefer": "return=representation"},
        )
        if not email_response.ok:
            self.fail("Smoke cleanup email log", f"HTTP {email_response.status_code}: {email_response.text[:600]}")
        email_rows = email_response.json() if email_response.text.strip() else []
        if email_rows:
            self.log("Smoke email pre-cleanup", f"neutralized {len(email_rows)} email row(s)")
        return len(email_rows)

    def downstream_checks(self) -> None:
        pedido_id = self.created["pedido_id"]
        pedido_num = self.created["pedido_num"]
        order_url = self.created.get("order_url") or ""

        orders = self.pg_get(f"/pedidos?id=eq.{pedido_id}&select=id,pedido_num,observacion")
        if not orders:
            self.fail("order special request note", f"pedido_id {pedido_id} not found")
        observation = str(orders[0].get("observacion") or "")
        expected_note = f"Pedido especial: {self.created['special_request']}"
        if expected_note not in observation:
            self.fail("order special request note", f"missing {expected_note!r} in {observation!r}")
        self.log("order special request note", "stored in observacion")

        items = self.pg_get(f"/pedido_items?pedido_id=eq.{pedido_id}&select=id,producto_id,cantidad,total_linea")
        if len(items) < 3:
            self.fail("order items", f"expected >=3 items, got {len(items)}")
        self.log("order items", f"{len(items)} item rows")

        reservas = self.pg_get(f"/stock_reservas?pedido_id=eq.{pedido_id}&select=id,estado,cantidad")
        if len(reservas) < 3:
            self.fail("stock reservations", f"expected >=3 reservations, got {len(reservas)}")
        self.log("stock reservations", f"{len(reservas)} reservation rows")

        email_logs = self.pg_get(f"/email_logistica_log?pedido_id=eq.{pedido_id}&select=id,status")
        if not email_logs:
            self.fail("email logistics log", "missing email_logistica_log row")
        self.log("email logistics log", email_logs[0].get("status", "row present"))

        kitchen_orders = self.require_ok("Kitchen API orders", self.get(self.cfg.kitchen + "/api/orders"))
        if not any(row.get("id") == pedido_id for row in kitchen_orders):
            self.fail("Kitchen API orders", f"pedido_id {pedido_id} not found")
        self.log("Kitchen API orders", "order visible")

        detail = self.require_ok("Kitchen order detail", self.get(self.cfg.kitchen + f"/order/{pedido_id}"))
        if pedido_num not in str(detail):
            self.fail("Kitchen order detail", "pedido number missing from HTML")
        self.log("Kitchen order detail", "HTML rendered")

        status_result = self.pg_rpc(
            "update_kitchen_status",
            {
                "p_pedido_id": pedido_id,
                "p_kitchen_status": "EN_PREPARACION",
                "p_kitchen_notes": "Smoke test status update",
                "p_notify": False,
            },
        )
        if not status_result.get("ok"):
            self.fail("Kitchen status RPC", json.dumps(status_result, ensure_ascii=False))
        self.log("Kitchen status RPC", "EN_PREPARACION without live notification")

        parsed = urlparse(order_url)
        token = (parse_qs(parsed.query).get("token") or [None])[0]
        if not token:
            # Fallback via logistics view; order_url is expected from confirmation RPC though.
            public = self.pg_rpc("obtener_pedido_publico", {"p_pedido_num": pedido_num, "p_token": ""})
            self.fail("public order token", f"missing token in confirmation order_url: {order_url}; fallback={public}")

        api_order = self.require_ok(
            "Logistics public order API",
            self.get(self.cfg.logistics + f"/api/order/{pedido_num}", params={"token": token}),
        )
        if api_order.get("order", {}).get("pedido_num") != pedido_num:
            self.fail("Logistics public order API", json.dumps(api_order, ensure_ascii=False)[:1000])
        self.log("Logistics public order API", "token accepted")

        html = self.require_ok(
            "Logistics public order HTML",
            self.get(self.cfg.logistics + f"/order/{pedido_num}", params={"token": token}),
        )
        if pedido_num not in str(html):
            self.fail("Logistics public order HTML", "pedido number missing from HTML")
        self.log("Logistics public order HTML", "rendered")

        tracking_html = self.require_ok(
            "Customer tracking HTML",
            self.get(self.cfg.logistics + f"/track/{pedido_num}", params={"token": token}),
        )
        tracking_text = str(tracking_html)
        if pedido_num not in tracking_text or "Estado del pedido" not in tracking_text:
            self.fail("Customer tracking HTML", "tracking page missing order number or progress panel")
        if f'href="/order/{pedido_num}' in tracking_text:
            self.fail("Customer tracking HTML", "tracking page links to operational order page")
        self.log("Customer tracking HTML", "rendered")

        picking_html = self.require_ok(
            "Logistics picking page",
            self.get(self.cfg.logistics + f"/ops/picking/{pedido_num}", params={"token": token}),
        )
        if f'action="/order/{pedido_num}/status"' not in str(picking_html):
            self.fail("Logistics picking page", "status form action is not absolute/correct")
        self.log("Logistics picking page", "status form rendered")

    def send_adapter_dry_run(self) -> None:
        payload = {
            "whatsapp_number": self.created["phone"],
            "message_text": f"Smoke test dry-run for {self.created['pedido_num']}",
            "event_type": "SMOKE_TEST",
            "pedido_id": self.created["pedido_id"],
            "dry_run": True,
        }
        data = self.require_ok(
            "Send adapter dry-run",
            self.post(
                self.cfg.send_adapter + "/send/whatsapp",
                json=payload,
                headers={"X-Hook-Token": self.cfg.send_token},
            ),
        )
        stdout = data.get("stdout") if isinstance(data, dict) else None
        dry_run_seen = bool(data.get("dry_run")) or bool(isinstance(stdout, dict) and stdout.get("dryRun"))
        if not data.get("ok") or not dry_run_seen:
            self.fail("Send adapter dry-run", json.dumps(data, ensure_ascii=False)[:1000])
        self.log("Send adapter dry-run", "no real message sent")

    def neutralize_test_outbound_queues(self) -> None:
        """Prevent smoke-test rows from being picked up by real outbound workers later."""
        pedido_id = self.created.get("pedido_id")
        if not pedido_id:
            return

        # The real outbox worker may have picked up a smoke row just before cleanup.
        # Give in-flight attempts a moment to finish, then cancel anything still active.
        time.sleep(1)

        outbox_response = self.patch(
            self.cfg.postgrest + f"/whatsapp_outbox?pedido_id=eq.{pedido_id}&status=in.(PENDING,SENDING,ERROR)",
            json={
                "status": "CANCELLED",
                "error_message": "Cancelled by integration smoke test cleanup; no real WhatsApp should be sent.",
            },
            headers={"Prefer": "return=representation"},
        )
        if not outbox_response.ok:
            self.fail("Smoke cleanup WhatsApp outbox", f"HTTP {outbox_response.status_code}: {outbox_response.text[:600]}")

        outbox_rows = outbox_response.json() if outbox_response.text.strip() else []
        email_rows_count = self.neutralize_test_email_queues()
        active_outbox = self.pg_get(
            f"/whatsapp_outbox?pedido_id=eq.{pedido_id}&status=in.(PENDING,SENDING,ERROR)&select=id,status,event_type,error_message"
        )
        if active_outbox:
            final_outbox_response = self.patch(
                self.cfg.postgrest + f"/whatsapp_outbox?pedido_id=eq.{pedido_id}&status=in.(PENDING,SENDING,ERROR)",
                json={
                    "status": "CANCELLED",
                    "error_message": "Cancelled by integration smoke test final cleanup; no real WhatsApp should be sent.",
                },
                headers={"Prefer": "return=representation"},
            )
            if not final_outbox_response.ok:
                self.fail(
                    "Smoke cleanup WhatsApp outbox",
                    f"HTTP {final_outbox_response.status_code}: {final_outbox_response.text[:600]}",
                )
            final_rows = final_outbox_response.json() if final_outbox_response.text.strip() else []
            outbox_rows.extend(final_rows)
            active_outbox = self.pg_get(
                f"/whatsapp_outbox?pedido_id=eq.{pedido_id}&status=in.(PENDING,SENDING,ERROR)&select=id,status,event_type,error_message"
            )
            if active_outbox:
                self.fail("Smoke cleanup WhatsApp outbox", f"active rows remain: {json.dumps(active_outbox, ensure_ascii=False)[:800]}")

        # One more delayed pass catches workers that had already loaded the row
        # and wrote PENDING/ERROR back after the first cleanup returned.
        time.sleep(3)
        delayed_outbox_response = self.patch(
            self.cfg.postgrest + f"/whatsapp_outbox?pedido_id=eq.{pedido_id}&status=in.(PENDING,SENDING,ERROR)",
            json={
                "status": "CANCELLED",
                "error_message": "Cancelled by integration smoke test delayed cleanup; no real WhatsApp should be sent.",
            },
            headers={"Prefer": "return=representation"},
        )
        if not delayed_outbox_response.ok:
            self.fail(
                "Smoke cleanup WhatsApp outbox",
                f"HTTP {delayed_outbox_response.status_code}: {delayed_outbox_response.text[:600]}",
            )
        delayed_rows = delayed_outbox_response.json() if delayed_outbox_response.text.strip() else []
        outbox_rows.extend(delayed_rows)
        active_outbox = self.pg_get(
            f"/whatsapp_outbox?pedido_id=eq.{pedido_id}&status=in.(PENDING,SENDING,ERROR)&select=id,status,event_type,error_message"
        )
        if active_outbox:
            self.fail("Smoke cleanup WhatsApp outbox", f"active rows remain after delayed cleanup: {json.dumps(active_outbox, ensure_ascii=False)[:800]}")
        self.log("Smoke cleanup", f"cancelled {len(outbox_rows)} WhatsApp row(s), neutralized {email_rows_count} email row(s)")

    def run(self) -> None:
        self.health_checks()
        self.product_quote_checks()
        self.conversation_order_flow()
        self.human_handoff_flow()
        self.neutralize_test_email_queues()
        self.downstream_checks()
        self.send_adapter_dry_run()
        self.neutralize_test_outbound_queues()
        print("\nSUMMARY")
        print(json.dumps({"ok": True, "created": self.created, "steps": self.steps}, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Replau integration smoke test")
    parser.add_argument("--postgrest", default="http://127.0.0.1:3000")
    parser.add_argument("--bridge", default="http://127.0.0.1:8789")
    parser.add_argument("--logistics", default="http://127.0.0.1:8790")
    parser.add_argument("--kitchen", default="http://127.0.0.1:8791")
    parser.add_argument("--send-adapter", default="http://127.0.0.1:8792")
    parser.add_argument("--bridge-token", default="")
    parser.add_argument("--send-token", default="")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    cfg = Config(
        postgrest=args.postgrest.rstrip("/"),
        bridge=args.bridge.rstrip("/"),
        logistics=args.logistics.rstrip("/"),
        kitchen=args.kitchen.rstrip("/"),
        send_adapter=args.send_adapter.rstrip("/"),
        bridge_token=resolve_token(args.bridge_token, "OPENCLAW_HOOK_TOKEN", "bridge.py"),
        send_token=resolve_token(args.send_token, "HOOK_TOKEN", "openclaw_whatsapp_send_adapter.py"),
        timeout=args.timeout,
    )

    try:
        Runner(cfg).run()
        return 0
    except SmokeTestError:
        return 2
    except Exception as exc:
        print(f"[ERROR] unexpected failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
