import importlib
import os
import unittest
from unittest.mock import patch


class MenuIntentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["MENU_URL"] = "https://orders.replau.com"
        cls.bridge = importlib.import_module("bridge")

    def test_recognizes_common_menu_requests(self):
        requests = (
            "menu",
            "menú",
            "MENU",
            "carta",
            "Quiero ver el menú",
            "catálogo",
            "lista de precios",
        )
        for message in requests:
            with self.subTest(message=message):
                self.assertTrue(self.bridge.is_menu_request(message))

    def test_does_not_match_unrelated_text(self):
        self.assertFalse(self.bridge.is_menu_request("Quiero dos hamburguesas"))

    def test_registers_user_initiated_request_with_channel_identity(self):
        inbound = self.bridge.NormalizedWebhook(
            whatsapp_number="51999999999",
            customer_address="51999999999",
            channel_kind="whatsapp",
            channel_id="whatsapp-account:restaurant-2",
            account_id="restaurant-2",
            message_text="Hola, quiero pedir",
            raw_payload={"message_id": "wamid.123", "sender_name": "Memo"},
        )
        identity = self.bridge.conversation_identity_from_inbound(inbound)
        with patch.object(self.bridge, "pg_post", return_value={"ok": True, "is_new": True}) as post:
            self.bridge.register_conversation_request(inbound, identity)
        post.assert_called_once_with(
            "/rpc/register_whatsapp_conversation_request",
            {
                "p_channel_kind": "whatsapp",
                "p_channel_id": "whatsapp-account:restaurant-2",
                "p_account_id": "restaurant-2",
                "p_customer_address": "51999999999",
                "p_sender_name": "Memo",
                "p_message_text": "Hola, quiero pedir",
                "p_provider_message_id": "wamid.123",
            },
        )

    def test_queue_failure_does_not_block_customer_ordering(self):
        inbound = self.bridge.NormalizedWebhook(whatsapp_number="51999999999", message_text="menu")
        identity = self.bridge.conversation_identity_from_inbound(inbound)
        with patch.object(self.bridge, "pg_post", side_effect=RuntimeError("queue unavailable")):
            self.assertIsNone(self.bridge.register_conversation_request(inbound, identity))

    def test_same_phone_uses_distinct_conversation_paths_for_two_accounts(self):
        first = self.bridge.ConversationIdentity("whatsapp", "whatsapp-account:first", "51999999999", "first")
        second = self.bridge.ConversationIdentity("whatsapp", "whatsapp-account:second", "51999999999", "second")
        with patch.object(self.bridge, "pg_get", side_effect=[[{"estado": "NEW"}], [{"estado": "CONFIRMED"}]]) as get:
            self.assertEqual(self.bridge.get_conversation(first)["estado"], "NEW")
            self.assertEqual(self.bridge.get_conversation(second)["estado"], "CONFIRMED")
        self.assertIn("channel_id=eq.whatsapp-account%3Afirst", get.call_args_list[0].args[0])
        self.assertIn("channel_id=eq.whatsapp-account%3Asecond", get.call_args_list[1].args[0])

    def test_request_context_scopes_legacy_handler_calls(self):
        identity = self.bridge.ConversationIdentity("whatsapp", "whatsapp-account:second", "51999999999", "second")
        token = self.bridge.ACTIVE_CONVERSATION_IDENTITY.set(identity)
        try:
            with patch.object(self.bridge, "pg_patch", return_value=[{"estado": "NEW"}]) as update:
                self.bridge.patch_conversation("51999999999", {"estado": "NEW"})
        finally:
            self.bridge.ACTIVE_CONVERSATION_IDENTITY.reset(token)
        self.assertIn("channel_id=eq.whatsapp-account%3Asecond", update.call_args.args[0])

    def test_channel_message_log_includes_composite_identity(self):
        identity = self.bridge.ConversationIdentity("whatsapp", "whatsapp-account:second", "51999999999", "second")
        with patch.object(self.bridge, "pg_post", return_value={"ok": True}) as post:
            self.bridge.log_whatsapp_message(identity, "INBOUND", "text", "hola")
        self.assertEqual(post.call_args.args[0], "/rpc/registrar_whatsapp_mensaje_canal")
        payload = post.call_args.args[1]
        self.assertEqual(payload["p_channel_id"], "whatsapp-account:second")
        self.assertEqual(payload["p_customer_address"], "51999999999")

    def test_reply_contains_public_storefront(self):
        reply = self.bridge.menu_reply_text()
        self.assertIn("Replau Burger", reply)
        self.assertIn("https://orders.replau.com", reply)

    def test_recognizes_web_order_handoff(self):
        message = "PEDIDO WEB CONFIRMADO: PED-123\nNombre: Memo\nProductos:\n- 2 x Hamburguesa"
        self.assertTrue(self.bridge.is_web_order_handoff(message))
        self.assertFalse(self.bridge.is_web_order_handoff("Memo\n2 hamburguesas"))

    def test_web_handoff_with_maps_url_stays_text(self):
        message = (
            "PEDIDO WEB CONFIRMADO: PED-123\n"
            "Nombre: Memo\nProductos:\n- 2 x Hamburguesa\n"
            "Ubicación: https://www.google.com/maps?q=-12.1199,-76.9917\n"
            "Pago: Yape\n"
            "Seguimiento: https://orders.replau.com/track/PED-123?token=signed-token"
        )
        inbound = self.bridge.extract_payload({"whatsapp_number": "51999999999", "message_text": message})
        self.assertEqual(inbound.message_type, "text")
        self.assertIsNone(inbound.latitude)
        self.assertIsNone(inbound.longitude)

    def test_signed_web_handoff_links_order_from_any_conversation_state(self):
        message = (
            "PEDIDO WEB CONFIRMADO: PED-123\nNombre: Memo\n"
            "Ubicación: https://www.google.com/maps?q=-12.1199,-76.9917\nPago: Yape\n"
            "Seguimiento: https://orders.replau.com/track/PED-123?token=signed-token"
        )
        inbound = self.bridge.NormalizedWebhook(
            whatsapp_number="51999999999", message_type="text", message_text=message
        )
        public_order = {
            "ok": True,
            "order": {
                "id": 123,
                "pedido_num": "PED-123",
                "cliente_nombre": "Memo",
                "total": 42.5,
                "metodo_pago": "YAPE",
                "direccion_confirmada": "Av. Prueba 123",
            },
            "items": [{"producto_nombre": "Hamburguesa", "cantidad": 2}],
        }
        with patch.object(self.bridge, "pg_post", return_value=public_order) as post, patch.object(
            self.bridge, "patch_conversation"
        ) as update, patch.object(self.bridge, "log_whatsapp_message"):
            result = self.bridge.handle_web_order_handoff(inbound)
        post.assert_called_once_with(
            "/rpc/obtener_pedido_publico", {"p_pedido_num": "PED-123", "p_token": "signed-token"}
        )
        saved = update.call_args.args[1]
        self.assertEqual(saved["estado"], "CONFIRMED")
        self.assertEqual(saved["pedido_id"], 123)
        self.assertEqual(saved["pedido_borrador"]["customer_name"], "Memo")
        self.assertEqual(saved["pedido_borrador"]["payment_method"], "YAPE")
        self.assertTrue(result["web_order_handoff"])

    def test_confirmed_order_receipt_is_registered_against_existing_order(self):
        inbound = self.bridge.NormalizedWebhook(
            whatsapp_number="51999999999",
            message_type="image",
            media_base64="ZmFrZQ==",
            media_filename="comprobante.png",
            media_mime_type="image/png",
        )
        conversation = {"estado": "CONFIRMED", "pedido_id": 42, "pedido_borrador": {"pedido_num": "PED-42"}}
        receipt = {"path": "/tmp/proof.png", "sha256": "abc", "mime_type": "image/png"}
        proof = {"ok": True, "proof_id": 7, "pedido_id": 42, "whatsapp_reply_text": "Recibí tu comprobante ✅"}
        with patch.object(self.bridge, "save_payment_receipt", return_value=receipt), patch.object(
            self.bridge, "register_payment_proof_for_receipt", return_value=proof
        ) as register, patch.object(self.bridge, "patch_conversation") as update, patch.object(
            self.bridge, "log_whatsapp_message"
        ):
            result = self.bridge.handle_confirmed(inbound, conversation)
        register.assert_called_once_with(inbound, receipt, 42)
        self.assertTrue(result["receipt_saved"])
        self.assertEqual(result["payment_proof"]["proof_id"], 7)
        self.assertEqual(update.call_args.args[1]["estado"], "CONFIRMED")


if __name__ == "__main__":
    unittest.main()
