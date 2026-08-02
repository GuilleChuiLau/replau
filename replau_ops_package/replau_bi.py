#!/usr/bin/env python3
"""Read-only business intelligence for the Replau Ops service."""
from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests


CANCELLED = {"ANULADO", "CANCELLED", "CANCELADO"}
TEST_MARKERS = re.compile(r"(?:SIMULACI(?:O|Ó)N|SYNTHETIC|CONTRACT[- ]?TEST|PRUEBA BI)", re.I)


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def percentile(values, pct):
    values = sorted(float(v) for v in values if v is not None and float(v) >= 0)
    if not values:
        return None
    position = (len(values) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return round(values[lower] + (values[upper] - values[lower]) * (position - lower), 1)


def date_window(start_text, end_text, timezone_name, today=None):
    tz = ZoneInfo(timezone_name)
    today = today or datetime.now(tz).date()
    try:
        end = date.fromisoformat(end_text) if end_text else today
        start = date.fromisoformat(start_text) if start_text else end - timedelta(days=6)
    except ValueError as exc:
        raise ValueError("Dates must use YYYY-MM-DD") from exc
    if end < start:
        raise ValueError("End date must not precede start date")
    if (end - start).days > 366:
        raise ValueError("Date range cannot exceed 367 days")
    start_dt = datetime.combine(start, time.min, tzinfo=tz)
    end_dt = datetime.combine(end, time.max, tzinfo=tz)
    days = (end - start).days + 1
    return {"start": start, "end": end, "start_dt": start_dt, "end_dt": end_dt, "days": days, "timezone": timezone_name}


def _is_test_order(order, customer):
    text = " ".join(str(v or "") for v in (
        order.get("pedido_num"), order.get("observacion"), customer.get("nombre"), customer.get("whatsapp_number")
    ))
    return bool(TEST_MARKERS.search(text))


def _minutes(start, end):
    a, b = parse_dt(start), parse_dt(end)
    if not a or not b:
        return None
    return max(0, (b - a).total_seconds() / 60)


def build_report(window, orders, items, customers, fulfillments, assignments, prior_orders):
    tz = ZoneInfo(window["timezone"])
    customer_by_id = {int(c["id"]): c for c in customers if c.get("id") is not None}
    in_scope, excluded = [], []
    for order in orders:
        customer = customer_by_id.get(int(order.get("cliente_id") or 0), {})
        (excluded if _is_test_order(order, customer) else in_scope).append(order)

    active = [o for o in in_scope if str(o.get("estado") or "").upper() not in CANCELLED]
    cancelled = [o for o in in_scope if str(o.get("estado") or "").upper() in CANCELLED]
    delivered = [o for o in active if str(o.get("estado") or "").upper() == "ENTREGADO"]
    active_ids = {int(o["id"]) for o in active if o.get("id") is not None}
    all_ids = {int(o["id"]) for o in in_scope if o.get("id") is not None}

    daily = defaultdict(lambda: {"orders": 0, "revenue": 0.0, "delivered": 0, "cancelled": 0})
    hourly = Counter()
    for order in in_scope:
        created = parse_dt(order.get("created_at"))
        if not created:
            continue
        local = created.astimezone(tz)
        bucket = daily[local.date().isoformat()]
        if str(order.get("estado") or "").upper() in CANCELLED:
            bucket["cancelled"] += 1
        else:
            bucket["orders"] += 1
            bucket["revenue"] += float(order.get("total") or 0)
            hourly[f"{local.hour:02d}:00"] += 1
            if str(order.get("estado") or "").upper() == "ENTREGADO":
                bucket["delivered"] += 1
    cursor = window["start"]
    trends = []
    while cursor <= window["end"]:
        row = daily[cursor.isoformat()]
        trends.append({"date": cursor.isoformat(), **row, "revenue": round(row["revenue"], 2)})
        cursor += timedelta(days=1)

    item_summary = defaultdict(lambda: {"product": "", "code": "", "quantity": 0.0, "sales": 0.0})
    for item in items:
        if int(item.get("pedido_id") or 0) not in active_ids:
            continue
        key = str(item.get("cdg_prod") or item.get("producto_nombre_maestro") or item.get("producto_texto") or "SIN_PRODUCTO")
        row = item_summary[key]
        row["product"] = item.get("producto_nombre_maestro") or item.get("producto_texto") or key
        row["code"] = item.get("cdg_prod") or ""
        row["quantity"] += float(item.get("cantidad") or 0)
        row["sales"] += float(item.get("total_linea") or 0)
    products = sorted(item_summary.values(), key=lambda x: (x["sales"], x["quantity"]), reverse=True)
    for row in products:
        row["quantity"], row["sales"] = round(row["quantity"], 3), round(row["sales"], 2)

    fulfillment_rows = [f for f in fulfillments if int(f.get("pedido_id") or 0) in all_ids]
    fulfillment_counts = Counter(str(f.get("status") or "SIN_ESTADO") for f in fulfillment_rows)
    expected = sum(float(f.get("expected_amount") or 0) for f in fulfillment_rows)
    received = sum(float(f.get("received_amount") or 0) for f in fulfillment_rows)
    refunded = sum(float(f.get("refunded_amount") or 0) for f in fulfillment_rows)
    terminal_cash = {"COD_COLLECTED", "RECONCILED", "SETTLED"}
    unreconciled = [f for f in fulfillment_rows if str(f.get("status") or "") not in terminal_cash and float(f.get("expected_amount") or 0) > float(f.get("received_amount") or 0)]

    assignment_rows = [a for a in assignments if int(a.get("pedido_id") or 0) in all_ids]
    latest_assignment = {}
    for row in assignment_rows:
        pid = int(row.get("pedido_id") or 0)
        if pid and (pid not in latest_assignment or int(row.get("id") or 0) > int(latest_assignment[pid].get("id") or 0)):
            latest_assignment[pid] = row
    delivery_minutes = [_minutes(a.get("assigned_at"), a.get("completed_at")) for a in latest_assignment.values()]
    delivery_minutes = [v for v in delivery_minutes if v is not None]
    failed_delivery = [a for a in latest_assignment.values() if str(a.get("status") or "").upper() == "FAILED"]
    kitchen_minutes = [_minutes(o.get("kitchen_started_at") or o.get("created_at"), o.get("kitchen_ready_at")) for o in active]
    kitchen_minutes = [v for v in kitchen_minutes if v is not None]

    customer_ids = {int(o.get("cliente_id") or 0) for o in active if o.get("cliente_id")}
    prior_ids = {int(o.get("cliente_id") or 0) for o in prior_orders if o.get("cliente_id") and str(o.get("estado") or "").upper() not in CANCELLED}
    returning = customer_ids & prior_ids
    revenue = sum(float(o.get("total") or 0) for o in active)
    payment_methods = Counter(str(o.get("metodo_pago") or "SIN_METODO") for o in active)
    status_counts = Counter(str(o.get("estado") or "SIN_ESTADO") for o in in_scope)
    order_rows = []
    fulfillment_by_order = {int(f.get("pedido_id") or 0): f for f in fulfillment_rows}
    for order in sorted(in_scope, key=lambda x: str(x.get("created_at") or ""), reverse=True):
        customer = customer_by_id.get(int(order.get("cliente_id") or 0), {})
        fulfillment = fulfillment_by_order.get(int(order.get("id") or 0), {})
        order_rows.append({
            "created_at": order.get("created_at"), "pedido_num": order.get("pedido_num"),
            "customer": customer.get("nombre") or "", "channel": order.get("canal") or "",
            "status": order.get("estado") or "", "payment_method": order.get("metodo_pago") or "",
            "payment_status": fulfillment.get("status") or order.get("payment_status") or "",
            "subtotal": float(order.get("subtotal") or 0), "delivery": float(order.get("delivery") or 0),
            "total": float(order.get("total") or 0),
        })
    return {
        "ok": True,
        "range": {"start": window["start"].isoformat(), "end": window["end"].isoformat(), "days": window["days"], "timezone": window["timezone"]},
        "summary": {
            "orders": len(active), "revenue": round(revenue, 2), "average_ticket": round(revenue / len(active), 2) if active else 0,
            "delivered_orders": len(delivered), "cancelled_orders": len(cancelled),
            "cancellation_rate_pct": round(100 * len(cancelled) / len(in_scope), 1) if in_scope else 0,
            "customers": len(customer_ids), "returning_customers": len(returning),
            "returning_customer_rate_pct": round(100 * len(returning) / len(customer_ids), 1) if customer_ids else 0,
            "excluded_test_orders": len(excluded),
        },
        "status_counts": dict(status_counts), "payment_method_counts": dict(payment_methods),
        "daily_trends": trends, "hourly_orders": [{"hour": k, "orders": v} for k, v in sorted(hourly.items())],
        "top_products": products[:10], "all_products": products,
        "operations": {
            "kitchen_minutes_p50": percentile(kitchen_minutes, .5), "kitchen_minutes_p90": percentile(kitchen_minutes, .9), "kitchen_samples": len(kitchen_minutes),
            "delivery_minutes_p50": percentile(delivery_minutes, .5), "delivery_minutes_p90": percentile(delivery_minutes, .9), "delivery_samples": len(delivery_minutes),
            "failed_deliveries": len(failed_delivery), "delivery_failure_rate_pct": round(100 * len(failed_delivery) / len(latest_assignment), 1) if latest_assignment else 0,
        },
        "payments": {"expected": round(expected, 2), "received": round(received, 2), "refunded": round(refunded, 2), "unreconciled_count": len(unreconciled), "unreconciled_value": round(sum(max(0, float(f.get("expected_amount") or 0) - float(f.get("received_amount") or 0)) for f in unreconciled), 2), "status_counts": dict(fulfillment_counts)},
        "orders": order_rows,
    }


def fetch_report(pg, start_text, end_text, timezone_name):
    window = date_window(start_text, end_text, timezone_name)
    quote = lambda value: requests.utils.quote(value, safe=":T-")
    start_q, end_q = quote(window["start_dt"].isoformat()), quote(window["end_dt"].isoformat())
    order_fields = "id,pedido_num,cliente_id,canal,estado,metodo_pago,subtotal,delivery,total,observacion,payment_status,kitchen_status,kitchen_started_at,kitchen_ready_at,created_at,updated_at"
    sources = {
        "orders": pg(f"/pedidos?created_at=gte.{start_q}&created_at=lte.{end_q}&select={order_fields}&order=id.asc&limit=10000"),
        "items": pg(f"/v_pedido_items_logistica?created_at=gte.{start_q}&created_at=lte.{end_q}&select=pedido_id,cdg_prod,producto_texto,producto_nombre_maestro,cantidad,total_linea,created_at&order=id.asc&limit=20000"),
        "customers": pg("/clientes_whatsapp?select=id,nombre,whatsapp_number&limit=10000"),
        "fulfillments": pg("/payment_fulfillments?select=pedido_id,payment_method,expected_amount,received_amount,refunded_amount,status&limit=10000"),
        "assignments": pg("/delivery_asignaciones?select=id,pedido_id,status,assigned_at,completed_at,failed_at&limit=10000"),
        "prior_orders": pg(f"/pedidos?created_at=lt.{start_q}&select=cliente_id,estado&limit=10000"),
    }
    errors = [f"{name}: {result.get('error')}" for name, result in sources.items() if not result.get("ok")]
    if errors:
        return {"ok": False, "error": "; ".join(errors), "range": {"start": window["start"].isoformat(), "end": window["end"].isoformat(), "timezone": timezone_name}}
    return build_report(window, *(sources[name]["data"] for name in ("orders", "items", "customers", "fulfillments", "assignments", "prior_orders")))


def report_csv(report):
    output = io.StringIO(newline="")
    fields = ["created_at", "pedido_num", "customer", "channel", "status", "payment_method", "payment_status", "subtotal", "delivery", "total"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in report.get("orders", []):
        safe = dict(row)
        for key, value in safe.items():
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                safe[key] = "'" + value
        writer.writerow(safe)
    return output.getvalue()
