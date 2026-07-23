#!/usr/bin/env python3
import json
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from starlette.requests import Request

from public_storefront import CheckoutItem, CheckoutRequest, api_checkout, api_reverse_geocode, checkout_items, menu_items, proof_file_signature_ok, safe_tracking_url, storefront


def main() -> None:
    items = menu_items()
    assert items, "menu is empty"
    assert all(item["price"] > 0 for item in items)
    assert all(item["category"] and item["description"] and item["icon"] for item in items)
    assert all(item["image_url"].startswith("/media/products/") for item in items if item["image_url"])
    names = {item["name"] for item in items}
    assert "OREJONES" not in names
    assert "PIMIENTA MOLIDA" not in names
    assert "HAMBURGUESA SIMPLE" in names
    assert {"Combos", "Hamburguesas", "Alitas", "Acompañamientos", "Bebidas", "Extras"}.issubset({item["category"] for item in items})
    page = storefront().body.decode("utf-8")
    assert "Buscar hamburguesas" in page
    assert "object-fit:contain" in page
    assert "aspect-ratio:4/3" in page
    assert "@media(max-width:700px)" in page
    assert "Finalizar pedido" in page
    assert "fetch('/api/checkout'" in page
    assert "Agregar mi ubicación actual" in page
    assert "fetch(`/api/reverse-geocode?" in page
    assert "address.value=data.address" in page
    assert "mobile-cart-summary" in page
    assert "100dvh" in page
    assert "Progreso del checkout" in page
    assert "Resumen del pedido" in page
    assert "Agregar al carrito" in page
    assert "Tu antojo, a unos cuantos clics." in page
    assert "Continuar en WhatsApp" not in page
    assert 'id="checkoutProof"' in page
    assert "fetch('/api/payment-proof'" in page
    assert "Comprobante recibido" in page
    assert proof_file_signature_ok("image/jpeg", b"\xff\xd8\xfftest")
    assert proof_file_signature_ok("image/png", b"\x89PNG\r\n\x1a\nrest")
    assert proof_file_signature_ok("image/webp", b"RIFF1234WEBPrest")
    assert proof_file_signature_ok("application/pdf", b"%PDF-1.7")
    assert not proof_file_signature_ok("image/png", b"not-a-png")
    assert safe_tracking_url("https://orders.replau.com/order/token") == "https://orders.replau.com/track/token"
    first = items[0]
    payload = CheckoutRequest(
        customer_name="Cliente prueba", phone="973875456", fulfillment="PICKUP",
        payment_method="CONTRA_ENTREGA", idempotency_key="0123456789abcdef",
        items=[CheckoutItem(product_id=first["id"], quantity=2)],
    )
    normalized = checkout_items(payload)
    assert normalized == [{"producto_id": first["id"], "producto_texto": first["name"], "cantidad": 2, "unidad": first["unit"]}]
    request = Request({"type": "http", "client": ("203.0.113.10", 1234), "headers": []})
    with patch("public_storefront.restaurant_status", return_value={"accepting_orders": True}), patch(
        "public_storefront.pg_post",
        return_value={"pedido_num": "PED-TEST", "total": first["price"] * 2, "payment_method": "CONTRA_ENTREGA", "order_url": "https://orders.replau.com/order/test-token"},
    ) as create_order:
        response = api_checkout(payload, request)
    body = json.loads(response.body)
    assert response.status_code == 201 and body["ok"] and body["order_number"] == "PED-TEST"
    assert body["tracking_url"] == "https://orders.replau.com/track/test-token"
    handoff = parse_qs(urlparse(body["whatsapp_url"]).query)["text"][0]
    assert "PEDIDO WEB CONFIRMADO: PED-TEST" in handoff
    assert "Nombre: Cliente prueba" in handoff
    assert f"- 2 x {first['name']}" in handoff
    assert "Dirección: Recojo en restaurante" in handoff
    assert "Pago: Contra entrega" in handoff
    assert "Seguimiento: https://orders.replau.com/track/test-token" in handoff
    sent = create_order.call_args.args[1]
    assert sent["p_items"] == normalized and all("precio_unitario" not in item for item in sent["p_items"])
    with patch("public_storefront.GOOGLE_MAPS_API_KEY", "test-key"), patch("public_storefront.requests.get") as google:
        google.return_value.raise_for_status.return_value = None
        google.return_value.json.return_value = {
            "status": "OK",
            "results": [{"formatted_address": "Av. Prueba 123, Lima, Perú"}],
        }
        geocode = api_reverse_geocode(-12.1, -77.03)
    assert json.loads(geocode.body) == {"ok": True, "address": "Av. Prueba 123, Lima, Perú"}
    print(f"STOREFRONT_MENU_OK: {len(items)} sellable products")


if __name__ == "__main__":
    main()
