#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

import openclaw_whatsapp_send_adapter as adapter


def buttons_payload() -> dict:
    return {
        "schema_version": 1,
        "type": "interactive_buttons",
        "fallback_text": "1) Ver menú 2) Ver mi pedido 3) Hablar con alguien",
        "interactive": {
            "body": "¿Qué deseas hacer?",
            "buttons": [
                {"id": "replau.view_menu", "title": "Ver menú"},
                {"id": "replau.view_order", "title": "Ver mi pedido"},
                {"id": "replau.human_help", "title": "Hablar con alguien"},
            ],
        },
    }


class SendAdapterTests(unittest.TestCase):
    def test_rejects_mismatched_fallback(self) -> None:
        with self.assertRaises(ValidationError):
            adapter.WhatsAppSendPayload(
                whatsapp_number="51999999999",
                message_text="different",
                message_payload=buttons_payload(),
            )

    def test_builds_native_meta_reply_buttons(self) -> None:
        payload = adapter.WhatsAppSendPayload(
            whatsapp_number="+51999999999",
            message_text=buttons_payload()["fallback_text"],
            message_payload=buttons_payload(),
        )
        body = adapter.build_meta_message_body(payload, "+51999999999")
        self.assertEqual(body["type"], "interactive")
        self.assertEqual(body["to"], "51999999999")
        self.assertEqual(body["interactive"]["action"]["buttons"][0]["reply"]["id"], "replau.view_menu")

    def test_builds_native_meta_utility_template(self) -> None:
        structured = {
            "schema_version": 1,
            "type": "template",
            "fallback_text": "Tu pedido PED-42 está listo.",
            "template": {
                "name": "replau_order_ready",
                "language": "es_PE",
                "category": "UTILITY",
                "components": [{"type": "body", "parameters": [{"type": "text", "text": "PED-42"}]}],
            },
        }
        payload = adapter.WhatsAppSendPayload(whatsapp_number="51999999999", message_payload=structured)
        body = adapter.build_meta_message_body(payload, "+51999999999")
        self.assertEqual(body["type"], "template")
        self.assertEqual(body["template"]["name"], "replau_order_ready")
        self.assertEqual(body["template"]["language"]["code"], "es_PE")

    def test_openclaw_transport_degrades_buttons_to_safe_text(self) -> None:
        payload = adapter.WhatsAppSendPayload(
            whatsapp_number="51999999999",
            message_text=buttons_payload()["fallback_text"],
            message_payload=buttons_payload(),
        )
        completed = SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
        with patch.object(adapter, "WHATSAPP_TRANSPORT", "openclaw"), patch.object(
            adapter.subprocess, "run", return_value=completed
        ) as run:
            response = adapter.send_whatsapp(payload, x_hook_token=adapter.HOOK_TOKEN)
        result = json.loads(response.body)
        self.assertEqual(result["delivery_mode"], "fallback_text")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--message") + 1], buttons_payload()["fallback_text"])

    def test_meta_transport_marks_read_then_sends_native_message(self) -> None:
        payload = adapter.WhatsAppSendPayload(
            whatsapp_number="51999999999",
            message_payload=buttons_payload(),
            reply_to_message_id="wamid.inbound-1",
        )
        with patch.object(adapter, "WHATSAPP_TRANSPORT", "meta_cloud_api"), patch.object(
            adapter, "meta_api_call", side_effect=[{"success": True}, {"messages": [{"id": "wamid.outbound-1"}]}]
        ) as call:
            response = adapter.send_whatsapp(payload, x_hook_token=adapter.HOOK_TOKEN)
        result = json.loads(response.body)
        self.assertEqual(result["delivery_mode"], "native")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0].args[0]["status"], "read")
        self.assertEqual(call.call_args_list[1].args[0]["type"], "interactive")


if __name__ == "__main__":
    unittest.main()
