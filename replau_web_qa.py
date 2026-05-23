#!/usr/bin/env python3
"""Replau web QA crawler/regression test for Logistics + Kitchen HTML flows."""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

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


class LinkFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.forms: list[dict[str, Any]] = []
        self._form: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        d = {k: html.unescape(v or "") for k, v in attrs}
        if tag == "a" and d.get("href"):
            self.links.append(d["href"])
        elif tag == "form":
            self._form = {"method": d.get("method", "get").lower(), "action": d.get("action", ""), "inputs": [], "buttons": []}
        elif tag == "input" and self._form is not None:
            self._form["inputs"].append(d)
        elif tag == "button" and self._form is not None:
            self._form["buttons"].append(d)

    def handle_endtag(self, tag: str):
        if tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None


@dataclass
class Cfg:
    postgrest: str = "http://127.0.0.1:3000"
    logistics: str = "http://127.0.0.1:8790"
    kitchen: str = "http://127.0.0.1:8791"
    ops: str = "http://127.0.0.1:8793"
    ops_token: str = ""
    product_admin: str = "http://127.0.0.1:8794"
    product_admin_token: str = ""
    payment_review: str = "http://127.0.0.1:8795"
    payment_review_token: str = ""
    timeout: int = 30
    mutate: bool = True


class QA:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.s = requests.Session()
        self.failures: list[str] = []
        self.warnings: list[str] = []
        self.checked: list[str] = []

    def ok(self, msg: str):
        self.checked.append(msg)
        print(f"[OK] {msg}")

    def fail(self, msg: str):
        self.failures.append(msg)
        print(f"[FAIL] {msg}", file=sys.stderr)

    def warn(self, msg: str):
        self.warnings.append(msg)
        print(f"[WARN] {msg}")

    def get_json(self, url: str):
        r = self.s.get(url, timeout=self.cfg.timeout)
        if not r.ok:
            self.fail(f"GET JSON {url} -> {r.status_code} {r.text[:200]}")
            return None
        return r.json()

    def fetch_html(self, name: str, url: str) -> tuple[str, LinkFormParser] | None:
        r = self.s.get(url, timeout=self.cfg.timeout)
        if not r.ok:
            self.fail(f"{name} GET {url} -> {r.status_code} {r.text[:300]}")
            return None
        text = r.text
        if "detail\":\"Not Found" in text or '"detail":"Not Found"' in text or text.strip() == '{"detail":"Not Found"}':
            self.fail(f"{name} returned Not Found detail: {url}")
        p = LinkFormParser(); p.feed(text)
        self.ok(f"{name} renders ({len(p.links)} links, {len(p.forms)} forms)")
        return text, p

    def ops_url(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        suffix = f"{sep}token={self.cfg.ops_token}" if self.cfg.ops_token else ""
        return self.cfg.ops + path + suffix

    def product_admin_url(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        suffix = f"{sep}token={self.cfg.product_admin_token}" if self.cfg.product_admin_token else ""
        return self.cfg.product_admin + path + suffix

    def payment_review_url(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        suffix = f"{sep}token={self.cfg.payment_review_token}" if self.cfg.payment_review_token else ""
        return self.cfg.payment_review + path + suffix

    def same_service_link_ok(self, base_url: str, href: str) -> bool:
        if href in {"#", ""} or href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            return True
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        if parsed.netloc not in {urlparse(self.cfg.logistics).netloc, urlparse(self.cfg.kitchen).netloc, urlparse(self.cfg.ops).netloc, urlparse(self.cfg.payment_review).netloc}:
            return True
        r = self.s.get(abs_url, timeout=self.cfg.timeout, allow_redirects=True)
        if r.status_code >= 400:
            self.fail(f"broken link from {base_url}: {href} -> {abs_url} HTTP {r.status_code} {r.text[:200]}")
            return False
        return True

    def check_links(self, name: str, base_url: str, parser: LinkFormParser):
        bad_before = len(self.failures)
        for href in parser.links:
            self.same_service_link_ok(base_url, href)
        if len(self.failures) == bad_before:
            self.ok(f"{name} links reachable")

    def check_ops_protected_buttons(self, parser: LinkFormParser):
        protected_ports = {
            "8793": "Ops Dashboard",
            "8794": "Product Admin",
            "8795": "Payment Proofs",
        }
        found: dict[str, str] = {}
        for href in parser.links:
            abs_url = urljoin(self.cfg.ops, href)
            parsed = urlparse(abs_url)
            port = parsed.port and str(parsed.port)
            if port in protected_ports:
                found[port] = abs_url
                if "token=" not in parsed.query:
                    self.fail(f"{protected_ports[port]} button missing token: {href}")
                    continue
                r = self.s.get(abs_url, timeout=self.cfg.timeout, allow_redirects=True)
                if r.status_code >= 400:
                    self.fail(f"{protected_ports[port]} button token rejected: HTTP {r.status_code} {r.text[:160]}")
                elif "Invalid or missing" in r.text[:500]:
                    self.fail(f"{protected_ports[port]} button returned invalid-token text")
        for port, name in protected_ports.items():
            if port in found:
                self.ok(f"ops {name} button has working token")

    def form_data(self, form: dict[str, Any], button_name: str | None = None, button_value: str | None = None) -> dict[str, str]:
        data: dict[str, str] = {}
        for inp in form.get("inputs", []):
            name = inp.get("name")
            if name:
                data[name] = inp.get("value", "")
        if button_name:
            data[button_name] = button_value or ""
        return data

    def choose_order(self) -> tuple[int, str, str] | None:
        rows = self.get_json(self.cfg.postgrest + "/v_pedidos_logistica?order=id.desc&limit=1&select=id,pedido_num,order_url")
        if not rows:
            self.fail("No logistics order available to test")
            return None
        row = rows[0]
        token_match = re.search(r"[?&]token=([^&]+)", row.get("order_url") or "")
        if not token_match:
            self.fail(f"Order {row.get('pedido_num')} has no token URL")
            return None
        return int(row["id"]), row["pedido_num"], token_match.group(1)

    def choose_routable_delivery_order(self) -> tuple[str, str] | None:
        assignments = self.get_json(
            self.cfg.postgrest
            + "/v_delivery_asignaciones?driver_latitude=not.is.null&driver_longitude=not.is.null"
            + "&select=pedido_num&order=driver_location_at.desc.nullslast&limit=10"
        )
        if not assignments:
            return None
        for assignment in assignments:
            pedido_num = assignment.get("pedido_num")
            if not pedido_num:
                continue
            rows = self.get_json(
                self.cfg.postgrest
                + f"/v_pedidos_logistica?pedido_num=eq.{pedido_num}&select=pedido_num,order_url,latitud,longitud&limit=1"
            )
            if not rows or rows[0].get("latitud") is None or rows[0].get("longitud") is None:
                continue
            token_match = re.search(r"[?&]token=([^&]+)", rows[0].get("order_url") or "")
            if token_match:
                return str(pedido_num), token_match.group(1)
        return None

    def test_delivery_route_map(self) -> None:
        health = self.get_json(self.cfg.logistics + "/health") or {}
        if not health.get("google_maps_enabled"):
            self.warn("Delivery route map skipped: Google Maps key is not enabled")
            return
        if not health.get("google_routes_enabled"):
            self.warn("Delivery route ETA skipped: Google Routes key is not enabled")
            return
        chosen = self.choose_routable_delivery_order()
        if not chosen:
            self.warn("Delivery route map skipped: no assignment with driver and customer coordinates")
            return
        pedido_num, token = chosen
        delivery_url = self.cfg.logistics + f"/ops/delivery/{pedido_num}?token={token}"
        got = self.fetch_html("logistics delivery route map", delivery_url)
        if got:
            html, parser = got
            self.check_links("logistics delivery route map", delivery_url, parser)
            for needle in ["ETA estimado", "Distancia ruta", "Ruta en Google Maps", f"/api/route/{pedido_num}"]:
                if needle in html:
                    self.ok(f"logistics delivery route map includes {needle}")
                else:
                    self.fail(f"logistics delivery route map missing {needle}")
        route = self.get_json(self.cfg.logistics + f"/api/route/{pedido_num}?token={token}")
        if not route:
            return
        for field in ["eta", "distance", "encoded_polyline"]:
            if route.get(field):
                self.ok(f"logistics delivery route API includes {field}")
            else:
                self.fail(f"logistics delivery route API missing {field}")

    def test_logistics(self):
        chosen = self.choose_order()
        if not chosen:
            return
        pedido_id, pedido_num, token = chosen
        pages = {
            "logistics dashboard": self.cfg.logistics + "/dashboard",
            "logistics blocked": self.cfg.logistics + "/blocked",
            "logistics public order": self.cfg.logistics + f"/order/{pedido_num}?token={token}",
            "logistics tracking": self.cfg.logistics + f"/track/{pedido_num}?token={token}",
            "logistics picking": self.cfg.logistics + f"/ops/picking/{pedido_num}?token={token}",
            "logistics delivery station": self.cfg.logistics + "/ops/delivery",
            "logistics delivery": self.cfg.logistics + f"/ops/delivery/{pedido_num}?token={token}",
        }
        parsed_pages: dict[str, tuple[str, LinkFormParser, str]] = {}
        for name, url in pages.items():
            got = self.fetch_html(name, url)
            if got:
                parsed_pages[name] = (got[0], got[1], url)
                self.check_links(name, url, got[1])
        if "logistics delivery station" in parsed_pages:
            html, _, _ = parsed_pages["logistics delivery station"]
            needles = ["Delivery Station", "Dispatch Board", "Sin repartidor"]
            if "Sin pedidos para delivery" not in html:
                needles.extend(["Ofrecer repartidor", "Clear"])
            for needle in needles:
                if needle not in html:
                    self.fail(f"logistics delivery station missing {needle}")
            if not self.failures:
                self.ok("logistics delivery station includes dispatch board")
        if "logistics dashboard" in parsed_pages:
            html, parser, _ = parsed_pages["logistics dashboard"]
            for needle in ["Logistics Workspace", "Picking", "Delivery", "Handoff humano"]:
                if needle in html:
                    self.ok(f"logistics dashboard includes {needle}")
                else:
                    self.fail(f"logistics dashboard missing {needle}")
            conversation_clear_forms = [
                form for form in parser.forms
                if "/conversation/" in form.get("action", "") and form.get("action", "").endswith("/clear")
            ]
            conversation_clear_all_forms = [
                form for form in parser.forms
                if form.get("action", "") == "/conversations/clear-all"
            ]
            email_clear_all_forms = [
                form for form in parser.forms
                if form.get("action", "") == "/email-logs/clear-all"
            ]
            email_clear_forms = [
                form for form in parser.forms
                if "/email-log/" in form.get("action", "") and form.get("action", "").endswith("/clear")
            ]
            active_conversations = self.get_json(
                self.cfg.postgrest + "/v_whatsapp_conversaciones?estado=not.in.(CONFIRMED,ANULADO,CANCELLED)&limit=1&select=id"
            )
            if conversation_clear_all_forms:
                self.ok("logistics dashboard includes conversation Clear all button")
            elif active_conversations:
                self.fail("logistics dashboard missing conversation Clear all button")
            if conversation_clear_forms:
                self.ok("logistics dashboard includes conversation Clear buttons")
            else:
                if active_conversations:
                    self.fail("logistics dashboard missing conversation Clear buttons")
                else:
                    self.ok("No active conversations available; conversation Clear buttons hidden")
            if email_clear_all_forms:
                self.ok("logistics dashboard includes email Clear all button")
            elif "Cola de email logística (0)" not in html:
                self.fail("logistics dashboard missing email Clear all button")
            else:
                self.ok("No email logs available; email Clear all button hidden")
            if email_clear_forms:
                self.ok("logistics dashboard includes email Clear buttons")
            elif "Cola de email logística (0)" not in html:
                self.fail("logistics dashboard missing email Clear buttons")
            else:
                self.ok("No email logs available; email Clear buttons hidden")

        # Test key logistics forms and redirects.
        for page_name in ["logistics public order", "logistics picking", "logistics delivery"]:
            if page_name not in parsed_pages:
                continue
            _, parser, page_url = parsed_pages[page_name]
            status_forms = [f for f in parser.forms if "/status" in f.get("action", "")]
            if not status_forms:
                self.fail(f"{page_name} missing status form")
                continue
            form = status_forms[0]
            action = urljoin(page_url, form["action"])
            buttons = [b for b in form.get("buttons", []) if b.get("name") == "estado"]
            if not buttons:
                self.fail(f"{page_name} status form has no estado buttons")
                continue
            # Pick least destructive normal button per page.
            preferred = "EN_PREPARACION" if "picking" in page_name else ("DESPACHADO" if "delivery" in page_name else "CONFIRMADO")
            button = next((b for b in buttons if b.get("value") == preferred), buttons[0])
            data = self.form_data(form, "estado", button.get("value", ""))
            if not self.cfg.mutate:
                self.ok(f"{page_name} status form structurally valid")
                continue
            r = self.s.post(action, data=data, timeout=self.cfg.timeout, allow_redirects=False)
            if r.status_code not in {302, 303}:
                self.fail(f"{page_name} form POST -> HTTP {r.status_code}: {r.text[:300]}")
                continue
            loc = r.headers.get("location", "")
            target = urljoin(action, loc)
            r2 = self.s.get(target, timeout=self.cfg.timeout)
            if not r2.ok or "Not Found" in r2.text[:500] or '{"detail":"Not Found"}' in r2.text:
                self.fail(f"{page_name} bad redirect {loc} -> HTTP {r2.status_code} {r2.text[:300]}")
            else:
                self.ok(f"{page_name} form redirects cleanly")

        # blocked/unblock form should not produce /blocked/blocked style redirect if a row exists.
        if "logistics blocked" in parsed_pages:
            _, parser, page_url = parsed_pages["logistics blocked"]
            for form in parser.forms:
                if "blocked/unblock" in form.get("action", ""):
                    action = urljoin(page_url, form["action"])
                    self.ok(f"blocked unblock form action resolves to {urlparse(action).path}")
        self.test_delivery_route_map()

    def test_kitchen(self):
        root = self.fetch_html("kitchen board", self.cfg.kitchen + "/")
        if root:
            if "Kitchen Workspace" in root[0]:
                self.ok("kitchen board includes Kitchen Workspace")
            else:
                self.fail("kitchen board missing Kitchen Workspace")
            self.check_links("kitchen board", self.cfg.kitchen + "/", root[1])
        rows = self.get_json(self.cfg.postgrest + "/v_kitchen_orders?order=id.desc&limit=1")
        if not rows:
            self.warn("No kitchen orders available")
            return
        pedido_id = rows[0]["id"]
        detail_url = self.cfg.kitchen + f"/order/{pedido_id}"
        got = self.fetch_html("kitchen order detail", detail_url)
        if not got:
            return
        text, parser = got
        self.check_links("kitchen order detail", detail_url, parser)
        status_forms = [f for f in parser.forms if "/status" in f.get("action", "") or f.get("action", "").endswith("status")]
        if not status_forms:
            self.fail("kitchen order detail missing status form")
            return
        form = status_forms[0]
        action = urljoin(detail_url, form["action"])
        if not self.cfg.mutate:
            self.ok("kitchen status form structurally valid")
            return
        data = self.form_data(form, "status", "LISTO")
        r = self.s.post(action, data=data, timeout=self.cfg.timeout, allow_redirects=False)
        if r.status_code not in {302, 303}:
            self.fail(f"kitchen status form POST -> HTTP {r.status_code}: {r.text[:300]}")
            return
        loc = r.headers.get("location", "")
        target = urljoin(action, loc)
        r2 = self.s.get(target, timeout=self.cfg.timeout)
        if not r2.ok or '{"detail":"Not Found"}' in r2.text or "Not Found" in r2.text[:500]:
            self.fail(f"kitchen status bad redirect {loc} -> {target} HTTP {r2.status_code} {r2.text[:300]}")
        else:
            self.ok("kitchen status form redirects cleanly")

    def test_product_admin(self):
        for name, url in {
            "product admin root": self.product_admin_url("/"),
            "product admin public menu": self.cfg.product_admin + "/menu",
            "product admin recipes": self.product_admin_url("/recipes"),
            "product admin recipe costs": self.product_admin_url("/costs"),
        }.items():
            got = self.fetch_html(name, url)
            if got:
                if name == "product admin root" and "Catalog Finance Workspace" not in got[0]:
                    self.fail("product admin root missing Catalog Finance Workspace")
                elif name == "product admin root":
                    self.ok("product admin root includes Catalog Finance Workspace")
                self.check_links(name, url, got[1])
        data = self.get_json(self.cfg.product_admin + "/api/menu")
        if isinstance(data, dict) and data.get("ok") and isinstance(data.get("items"), list):
            self.ok("product admin menu API reachable")
        else:
            self.fail("product admin menu API invalid")
        cost_data = self.get_json(self.product_admin_url("/api/recipe-costs"))
        if isinstance(cost_data, dict) and cost_data.get("ok") and isinstance(cost_data.get("ingredients"), list) and isinstance(cost_data.get("recipes"), list):
            self.ok("product admin recipe cost API reachable")
            alerts = cost_data.get("low_stock_alerts")
            if isinstance(alerts, dict) and "ingredient_alert_count" in alerts and "product_alert_count" in alerts:
                self.ok("product admin recipe cost API includes low stock alerts")
            else:
                self.fail("product admin recipe cost API missing low stock alerts")
        else:
            self.fail("product admin recipe cost API invalid")
        alert_data = self.get_json(self.product_admin_url("/api/low-stock-alerts"))
        if isinstance(alert_data, dict) and alert_data.get("ok") and isinstance(alert_data.get("ingredient_alerts"), list) and isinstance(alert_data.get("product_alerts"), list):
            self.ok("product admin low stock alert API reachable")
        else:
            self.fail("product admin low stock alert API invalid")
        recipe_data = self.get_json(self.product_admin_url("/api/recipes"))
        if isinstance(recipe_data, dict) and recipe_data.get("ok") and isinstance(recipe_data.get("recipes"), list):
            self.ok("product admin recipe API reachable")
        else:
            self.fail("product admin recipe API invalid")

    def test_ops_dashboard(self):
        got = self.fetch_html("ops management dashboard", self.ops_url("/"))
        if got:
            text, parser = got
            self.check_links("ops management dashboard", self.ops_url("/"), parser)
            self.check_ops_protected_buttons(parser)
            for needle in ["Restaurant Management Today", "Owner Command Center", "Purchase Agent", "Purchase Recommendations", "Ingredient Behavior", "Owner / Manager Workspace", "Kitchen Workspace", "Logistics Workspace", "Cashier Workspace", "Catalog Finance Workspace", "Sales booked", "Top Products", "Rush Hours", "Margin Signals"]:
                if needle in text:
                    self.ok(f"ops dashboard includes {needle}")
                else:
                    self.fail(f"ops dashboard missing {needle}")
        data = self.get_json(self.ops_url("/api/business-summary"))
        if isinstance(data, dict) and data.get("ok") and "revenue" in data and "top_products" in data:
            self.ok("ops business summary API reachable")
        else:
            self.fail("ops business summary API invalid")
        owner = self.get_json(self.ops_url("/api/owner-command"))
        if isinstance(owner, dict) and owner.get("ok") and isinstance(owner.get("cards"), list):
            self.ok("ops owner command API reachable")
        else:
            self.fail("ops owner command API invalid")
        if isinstance(owner, dict) and owner.get("recipe_count", 0) > 0 and isinstance(owner.get("margin_rows"), list) and owner.get("margin_rows"):
            self.ok("ops owner command includes recipe margin signals")
        else:
            self.fail("ops owner command missing recipe margin signals")
        purchase = self.get_json(self.ops_url("/api/purchase-agent"))
        if isinstance(purchase, dict) and purchase.get("ok") and isinstance(purchase.get("recommendations"), list) and isinstance(purchase.get("ingredient_behavior"), list):
            self.ok("ops purchase agent API reachable")
        else:
            self.fail("ops purchase agent API invalid")

    def test_payment_review(self):
        got = self.fetch_html("payment review workspace", self.payment_review_url("/"))
        if not got:
            return
        text, parser = got
        self.check_links("payment review workspace", self.payment_review_url("/"), parser)
        for needle in ["Cashier Workspace", "Por revisar", "Valor pendiente", "Payment proofs", "View submitted file"]:
            if needle in text:
                self.ok(f"payment review includes {needle}")
            else:
                self.fail(f"payment review missing {needle}")

    def run(self) -> int:
        for name, url in [
            ("postgrest", self.cfg.postgrest + "/"),
            ("logistics health", self.cfg.logistics + "/health"),
            ("kitchen health", self.cfg.kitchen + "/health"),
            ("ops health", self.ops_url("/health")),
            ("product admin health", self.cfg.product_admin + "/health"),
            ("payment review health", self.payment_review_url("/health")),
        ]:
            r = self.s.get(url, timeout=self.cfg.timeout)
            if not r.ok:
                self.fail(f"{name} health HTTP {r.status_code}")
            else:
                self.ok(f"{name} reachable")
        self.test_logistics()
        self.test_kitchen()
        self.test_ops_dashboard()
        self.test_product_admin()
        self.test_payment_review()
        print(f"\nChecked: {len(self.checked)} OK, {len(self.warnings)} warnings, {len(self.failures)} failures")
        if self.failures:
            for f in self.failures:
                print(f" - {f}", file=sys.stderr)
            return 2
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--postgrest", default="http://127.0.0.1:3000")
    ap.add_argument("--logistics", default="http://127.0.0.1:8790")
    ap.add_argument("--kitchen", default="http://127.0.0.1:8791")
    ap.add_argument("--ops", default="http://127.0.0.1:8793")
    ap.add_argument("--ops-token", default="")
    ap.add_argument("--product-admin", default="http://127.0.0.1:8794")
    ap.add_argument("--product-admin-token", default="")
    ap.add_argument("--payment-review", default="http://127.0.0.1:8795")
    ap.add_argument("--payment-review-token", default="")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--no-mutate", action="store_true", help="Only crawl/check structure; do not POST status forms")
    args = ap.parse_args()
    return QA(Cfg(
        postgrest=args.postgrest.rstrip('/'),
        logistics=args.logistics.rstrip('/'),
        kitchen=args.kitchen.rstrip('/'),
        ops=args.ops.rstrip('/'),
        ops_token=resolve_token(args.ops_token, "OPS_TOKEN", "replau_health_dashboard.py"),
        product_admin=args.product_admin.rstrip('/'),
        product_admin_token=resolve_token(args.product_admin_token, "ADMIN_TOKEN", "replau_product_admin.py"),
        payment_review=args.payment_review.rstrip('/'),
        payment_review_token=resolve_token(args.payment_review_token, "REVIEW_TOKEN", "replau_payment_proof_review.py"),
        timeout=args.timeout,
        mutate=not args.no_mutate,
    )).run()

if __name__ == "__main__":
    raise SystemExit(main())
