# Bridge integration for WhatsApp payment proof images

This package installs the database side and a review UI. To connect real WhatsApp images to the flow, patch `/opt/replau_openclaw_whatsapp_bridge/bridge.py` so that image/document messages call:

```http
POST http://127.0.0.1:3000/rpc/registrar_comprobante_pago_whatsapp
```

Payload example:

```json
{
  "p_whatsapp_number": "51998116843",
  "p_media_url": "https://.../image.jpg",
  "p_local_path": null,
  "p_caption": "Comprobante Yape",
  "p_media_type": "image",
  "p_media_id": "optional-whatsapp-media-id",
  "p_original_filename": "optional.jpg",
  "p_pedido_id": null
}
```

The RPC returns:

```json
{
  "ok": true,
  "proof_id": 1,
  "pedido_id": 18,
  "pedido_num": "PED-000015",
  "payment_status": "PROOF_RECEIVED",
  "whatsapp_reply_text": "Recibí tu comprobante de pago ✅..."
}
```

## Suggested bridge helper

Add this helper near your PostgREST helper functions:

```python
def registrar_comprobante_pago_whatsapp(payload: dict) -> dict:
    rpc_payload = {
        "p_whatsapp_number": payload.get("whatsapp_number") or payload.get("from") or payload.get("sender"),
        "p_media_url": payload.get("media_url") or payload.get("image_url") or payload.get("url"),
        "p_local_path": payload.get("local_path") or payload.get("file_path"),
        "p_caption": payload.get("caption") or payload.get("message_text") or payload.get("text"),
        "p_media_type": payload.get("message_type") or payload.get("media_type") or "image",
        "p_media_id": payload.get("media_id") or payload.get("id"),
        "p_original_filename": payload.get("filename") or payload.get("file_name"),
        "p_pedido_id": payload.get("pedido_id"),
    }
    response = requests.post(
        f"{POSTGREST_BASE_URL}/rpc/registrar_comprobante_pago_whatsapp",
        json=rpc_payload,
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()
```

Then in your main webhook handler, before normal text/location handling, add:

```python
if message_type in {"image", "photo", "document"}:
    result = registrar_comprobante_pago_whatsapp(payload)
    return reply(
        result.get("whatsapp_reply_text") or "Recibí tu comprobante de pago ✅ Lo enviaremos a revisión.",
        payment_proof=result,
        next_state="PAYMENT_PROOF_RECEIVED",
    )
```

Keep this conservative: any image from an active customer order becomes a received payment proof and must be reviewed in:

```text
http://127.0.0.1:8795
```
