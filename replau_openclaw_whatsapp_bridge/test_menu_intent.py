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

    def test_reply_contains_public_storefront(self):
        reply = self.bridge.menu_reply_text()
        self.assertIn("Replau Burger", reply)
        self.assertIn("https://orders.replau.com", reply)

    def test_recognizes_web_order_handoff(self):
        message = "PEDIDO WEB CONFIRMADO: PED-123\nNombre: Memo\nProductos:\n- 2 x Hamburguesa"
        self.assertTrue(self.bridge.is_web_order_handoff(message))
        self.assertFalse(self.bridge.is_web_order_handoff("Memo\n2 hamburguesas"))

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
