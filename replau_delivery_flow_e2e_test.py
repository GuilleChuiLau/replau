#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

POSTGREST = "http://127.0.0.1:3000"
BRIDGE = "http://127.0.0.1:8789"
LOGISTICS = "http://127.0.0.1:8790"
TOKEN = os.environ.get("OPENCLAW_HOOK_TOKEN", "RESTRICTED")
TIMEOUT = 60
TEST_DRIVER = {
    "codigo": "R900",
    "nombre": "Repartidor Test E2E",
    "whatsapp_number": "51900000900",
    "activo": True,
    "orden_turno": 0,
}

s = requests.Session()
created: dict[str, Any] = {}
original_driver_states: dict[int, bool] = {}


def fail(step: str, detail: str) -> None:
    print(f"[FAIL] {step}: {detail}", file=sys.stderr)
    raise SystemExit(2)


def ok(step: str, detail: str = "OK") -> None:
    print(f"[OK] {step}: {detail}")


def req(method: str, url: str, **kwargs: Any) -> requests.Response:
    return s.request(method, url, timeout=TIMEOUT, **kwargs)


def expect_json(step: str, r: requests.Response) -> Any:
    if not r.ok:
        fail(step, f"HTTP {r.status_code}: {r.text[:1000]}")
    try:
        return r.json()
    except Exception:
        fail(step, f"non-json: {r.text[:1000]}")


def pg_get(path: str) -> Any:
    return expect_json(f"GET {path}", req("GET", POSTGREST + path))


def pg_post(path: str, payload: dict[str, Any]) -> Any:
    return expect_json(f"POST {path}", req("POST", POSTGREST + path, json=payload, headers={"Prefer": "return=representation"}))


def pg_patch(path: str, payload: dict[str, Any]) -> Any:
    return expect_json(f"PATCH {path}", req("PATCH", POSTGREST + path, json=payload, headers={"Prefer": "return=representation"}))


def bridge(payload: dict[str, Any]) -> dict[str, Any]:
    data = expect_json("bridge webhook", req("POST", BRIDGE + "/webhook/whatsapp", json=payload, headers={"X-Hook-Token": TOKEN}))
    if not isinstance(data, dict) or not data.get("ok"):
        fail("bridge webhook", json.dumps(data, ensure_ascii=False)[:1200])
    return data


def cancel_outbox_for_order(note: str = "cancelled by delivery E2E test") -> None:
    pedido_id = created.get("pedido_id")
    if not pedido_id:
        return
    r = req(
        "PATCH",
        POSTGREST + f"/whatsapp_outbox?pedido_id=eq.{pedido_id}&status=in.(PENDING,SENDING,ERROR)",
        json={"status": "CANCELLED", "error_message": note},
        headers={"Prefer": "return=representation"},
    )
    if not r.ok:
        fail("cancel outbox", f"HTTP {r.status_code}: {r.text[:600]}")


def setup_test_driver() -> None:
    drivers = pg_get("/repartidores?select=id,codigo,nombre,whatsapp_number,activo,orden_turno")
    for d in drivers:
        if d.get("activo") and d.get("whatsapp_number") != TEST_DRIVER["whatsapp_number"]:
            original_driver_states[int(d["id"])] = True
            pg_patch(f"/repartidores?id=eq.{d['id']}", {"activo": False})
    rows = pg_get(f"/repartidores?whatsapp_number=eq.{TEST_DRIVER['whatsapp_number']}&limit=1")
    if rows:
        pg_patch(f"/repartidores?id=eq.{rows[0]['id']}", TEST_DRIVER)
        created["driver_id"] = rows[0]["id"]
    else:
        new_rows = pg_post("/repartidores", TEST_DRIVER)
        created["driver_id"] = new_rows[0]["id"] if new_rows else None
    ok("test driver", f"{TEST_DRIVER['codigo']} active, real active drivers paused temporarily")


def restore_drivers() -> None:
    try:
        pg_patch(f"/repartidores?whatsapp_number=eq.{TEST_DRIVER['whatsapp_number']}", {"activo": False})
    except Exception as exc:
        print(f"[WARN] could not pause test driver {TEST_DRIVER['codigo']}: {exc}", file=sys.stderr)
    for driver_id, active in original_driver_states.items():
        try:
            pg_patch(f"/repartidores?id=eq.{driver_id}", {"activo": active})
        except Exception as exc:
            print(f"[WARN] could not restore driver {driver_id}: {exc}", file=sys.stderr)


def create_order() -> None:
    unique = int(time.time()) % 1_000_000
    phone = f"51989{unique:06d}"
    customer = f"Delivery E2E {unique}"
    created.update({"phone": phone, "customer": customer})

    first = bridge({"whatsapp_number": phone, "message_type": "text", "message_text": f"{customer}\n1 hamburguesa simple\n1 papas fritas pequenas"})
    if first.get("next_state") != "WAITING_PAYMENT_AND_LOCATION":
        fail("quote", str(first))
    ok("pedido cotizado", "WAITING_PAYMENT_AND_LOCATION")

    pay = bridge({"whatsapp_number": phone, "message_type": "text", "message_text": "Contra entrega"})
    if pay.get("next_state") != "WAITING_PAYMENT_AND_LOCATION":
        fail("payment", str(pay))
    ok("pago", "CONTRA_ENTREGA")

    loc = bridge({"whatsapp_number": phone, "message_type": "location", "latitude": -12.046374, "longitude": -77.042793})
    if loc.get("next_state") != "WAITING_ADDRESS_CONFIRMATION":
        fail("location", str(loc))
    ok("ubicación cliente", "guardada")

    conf = bridge({"whatsapp_number": phone, "message_type": "text", "message_text": "SI"})
    if conf.get("awaiting_special_request"):
        conf = bridge({"whatsapp_number": phone, "message_type": "text", "message_text": "NO"})
    confirmation = conf.get("confirmation") or {}
    if conf.get("next_state") != "CONFIRMED" or not confirmation.get("pedido_num"):
        fail("confirm order", json.dumps(conf, ensure_ascii=False)[:1200])
    created.update({"pedido_id": confirmation["pedido_id"], "pedido_num": confirmation["pedido_num"], "order_url": confirmation["order_url"]})
    cancel_outbox_for_order("cancelled immediately after order creation by delivery E2E test")
    ok("pedido confirmado", created["pedido_num"])


def dispatch_from_delivery_station() -> None:
    pedido_num = created["pedido_num"]
    token = (parse_qs(urlparse(created["order_url"]).query).get("token") or [""])[0]
    if not token:
        fail("token", created["order_url"])
    created["token"] = token
    r = req(
        "POST",
        LOGISTICS + f"/order/{pedido_num}/status",
        data={"token": token, "estado": "DESPACHADO", "next_url": "/ops/delivery"},
        allow_redirects=False,
    )
    if r.status_code not in {302, 303}:
        fail("dispatch status", f"HTTP {r.status_code}: {r.text[:600]}")
    assignments = pg_get(f"/v_delivery_asignaciones?pedido_id=eq.{created['pedido_id']}&order=created_at.desc&limit=1")
    if not assignments or assignments[0].get("status") != "OFFERED":
        fail("driver offer", json.dumps(assignments, ensure_ascii=False))
    created["assignment_id"] = assignments[0]["id"]
    outbox = pg_get(f"/whatsapp_outbox?pedido_id=eq.{created['pedido_id']}&whatsapp_number=eq.{TEST_DRIVER['whatsapp_number']}&order=id.desc&limit=1")
    if not outbox or "Pago carrera" not in outbox[0].get("message_text", ""):
        fail("driver offer outbox", json.dumps(outbox, ensure_ascii=False)[:1000])
    cancel_outbox_for_order("cancelled driver offer by delivery E2E test")
    ok("Delivery Station ofrece repartidor", f"assignment {created['assignment_id']} fee S/ {assignments[0].get('fee')}")


def driver_flow() -> None:
    phone = TEST_DRIVER["whatsapp_number"]
    accept = bridge({"whatsapp_number": phone, "message_type": "text", "message_text": "ACEPTAR"})
    if accept.get("next_state") != "DRIVER_ASSIGNED":
        fail("driver accept", json.dumps(accept, ensure_ascii=False)[:1200])
    cancel_outbox_for_order("cancelled assignment detail by delivery E2E test")
    ok("repartidor acepta", "DRIVER_ASSIGNED")

    loc = bridge({"whatsapp_number": phone, "message_type": "location", "latitude": -12.119934, "longitude": -76.991731})
    if loc.get("next_state") != "DRIVER_LOCATION_SAVED":
        fail("driver location", json.dumps(loc, ensure_ascii=False)[:1200])
    customer_msg = pg_get(f"/whatsapp_outbox?pedido_id=eq.{created['pedido_id']}&whatsapp_number=eq.{created['phone']}&message_text=ilike.*Tracking*&order=id.desc&limit=1")
    if not customer_msg:
        fail("customer tracking msg", "missing Tracking outbox message")
    cancel_outbox_for_order("cancelled customer tracking by delivery E2E test")
    ok("ubicación repartidor", "guardada y tracking al cliente generado")

    pickup = bridge({"whatsapp_number": phone, "message_type": "text", "message_text": "RECOGIDO"})
    if pickup.get("next_state") != "DRIVER_ON_THE_WAY":
        fail("driver pickup", json.dumps(pickup, ensure_ascii=False)[:1200])
    cancel_outbox_for_order("cancelled en camino msg by delivery E2E test")
    ok("recojo restaurante", "EN CAMINO generado")

    arrived = bridge({"whatsapp_number": phone, "message_type": "text", "message_text": "LLEGUÉ"})
    if arrived.get("next_state") != "DRIVER_ARRIVED":
        fail("driver arrived", json.dumps(arrived, ensure_ascii=False)[:1200])
    cancel_outbox_for_order("cancelled arrived msg by delivery E2E test")
    ok("llegada a destino", "mensaje al cliente generado")

    delivered = bridge({"whatsapp_number": phone, "message_type": "text", "message_text": "ENTREGADO"})
    if delivered.get("next_state") != "DRIVER_DELIVERED":
        fail("driver delivered", json.dumps(delivered, ensure_ascii=False)[:1200])
    cancel_outbox_for_order("cancelled delivered msg by delivery E2E test")
    order = pg_get(f"/v_pedidos_logistica?id=eq.{created['pedido_id']}&select=pedido_num,estado&limit=1")
    assignment = pg_get(f"/v_delivery_asignaciones?id=eq.{created['assignment_id']}&limit=1")
    if not order or order[0].get("estado") != "ENTREGADO":
        fail("final order state", json.dumps(order, ensure_ascii=False))
    if not assignment or assignment[0].get("status") != "COMPLETED":
        fail("final assignment state", json.dumps(assignment, ensure_ascii=False))
    ok("entrega final", "pedido ENTREGADO / asignación COMPLETED")


def render_checks() -> None:
    r = req("GET", LOGISTICS + "/ops/delivery")
    if not r.ok:
        fail("delivery station render", f"HTTP {r.status_code}: {r.text[:600]}")
    text = r.text
    needles = ["Delivery Station", "Dispatch Board", "Sin repartidor", "Control de repartidores"]
    if "Sin pedidos para delivery" in text:
        needles.append("Sin pedidos para delivery")
    else:
        needles.extend(["Asignar directo", "Ofrecer repartidor", "Clear"])
    for needle in needles:
        if needle not in text:
            fail("delivery station render", f"missing {needle}")
    ok("Delivery Station render", "OK")


def main() -> int:
    try:
        for name, url in [("bridge", BRIDGE + "/health"), ("logistics", LOGISTICS + "/health")]:
            data = expect_json(f"{name} health", req("GET", url))
            if not data.get("ok"):
                fail(f"{name} health", json.dumps(data))
        setup_test_driver()
        create_order()
        dispatch_from_delivery_station()
        driver_flow()
        render_checks()
        print("\nSUMMARY")
        print(json.dumps({"ok": True, "created": created}, indent=2, ensure_ascii=False))
        return 0
    finally:
        try:
            cancel_outbox_for_order("final delivery E2E cleanup")
        finally:
            restore_drivers()


if __name__ == "__main__":
    raise SystemExit(main())
