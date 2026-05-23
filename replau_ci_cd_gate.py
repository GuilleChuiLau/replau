#!/usr/bin/env python3
"""Replau local CI/CD gate.

This script turns the manual release checklist into one repeatable command.
It is intentionally local-first because Replau currently runs from a source
tree in /home/guill/codex with deployed runtime copies in /opt/replau_*.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


ROOT = Path("/home/guill/codex")


@dataclass(frozen=True)
class FilePair:
    label: str
    source: Path
    deployed: Path


@dataclass(frozen=True)
class Probe:
    label: str
    url: str
    expect_json_ok: bool = False
    token_env: str = ""
    process_marker: str = ""


SOURCE_DIRS = [
    ROOT / "postgrest_local",
    ROOT / "replau_email_worker",
    ROOT / "replau_google_reverse_geocode_package",
    ROOT / "replau_kitchen_ui",
    ROOT / "replau_logistics_viewer",
    ROOT / "replau_openclaw_whatsapp_bridge",
    ROOT / "replau_openclaw_whatsapp_send_adapter",
    ROOT / "replau_ops_package",
    ROOT / "replau_payment_proof_flow_package",
    ROOT / "replau_product_admin_package",
    ROOT / "replau_whatsapp_outbox_worker",
]

TOP_LEVEL_PY = [
    ROOT / "replau_delivery_flow_e2e_test.py",
    ROOT / "replau_integration_smoke_test.py",
    ROOT / "replau_web_qa.py",
]

DEPLOY_PAIRS = [
    FilePair("email worker", ROOT / "replau_email_worker/email_worker.py", Path("/opt/replau_email_worker/email_worker.py")),
    FilePair("kitchen UI", ROOT / "replau_kitchen_ui/kitchen_ui.py", Path("/opt/replau_kitchen_ui/kitchen_ui.py")),
    FilePair("logistics viewer", ROOT / "replau_logistics_viewer/logistics_viewer.py", Path("/opt/replau_logistics_viewer/logistics_viewer.py")),
    FilePair("WhatsApp bridge", ROOT / "replau_openclaw_whatsapp_bridge/bridge.py", Path("/opt/replau_openclaw_whatsapp_bridge/bridge.py")),
    FilePair("reverse geocode helper", ROOT / "replau_google_reverse_geocode_package/google_reverse_geocode.py", Path("/opt/replau_openclaw_whatsapp_bridge/google_reverse_geocode.py")),
    FilePair("reverse geocode test", ROOT / "replau_google_reverse_geocode_package/test_reverse_geocode.py", Path("/opt/replau_openclaw_whatsapp_bridge/test_reverse_geocode.py")),
    FilePair("send adapter", ROOT / "replau_openclaw_whatsapp_send_adapter/openclaw_whatsapp_send_adapter.py", Path("/opt/replau_openclaw_whatsapp_send_adapter/openclaw_whatsapp_send_adapter.py")),
    FilePair("ops dashboard", ROOT / "replau_ops_package/replau_health_dashboard.py", Path("/opt/replau_ops/replau_health_dashboard.py")),
    FilePair("stuck monitor", ROOT / "replau_ops_package/replau_stuck_monitor.py", Path("/opt/replau_ops/replau_stuck_monitor.py")),
    FilePair("WhatsApp watchdog", ROOT / "replau_ops_package/replau_whatsapp_watchdog.py", Path("/opt/replau_ops/replau_whatsapp_watchdog.py")),
    FilePair("backup script", ROOT / "replau_ops_package/replau_backup.sh", Path("/opt/replau_ops/replau_backup.sh")),
    FilePair("payment proof review", ROOT / "replau_payment_proof_flow_package/replau_payment_proof_review.py", Path("/opt/replau_payment_proof_review/replau_payment_proof_review.py")),
    FilePair("product admin", ROOT / "replau_product_admin_package/replau_product_admin.py", Path("/opt/replau_product_admin/replau_product_admin.py")),
    FilePair("outbox worker", ROOT / "replau_whatsapp_outbox_worker/whatsapp_outbox_worker.py", Path("/opt/replau_whatsapp_outbox_worker/whatsapp_outbox_worker.py")),
]

HTTP_PROBES = [
    Probe("PostgREST", "http://127.0.0.1:3000/"),
    Probe("WhatsApp bridge", "http://127.0.0.1:8789/health", True),
    Probe("Logistics viewer", "http://127.0.0.1:8790/health", True),
    Probe("Kitchen UI", "http://127.0.0.1:8791/health", True),
    Probe("WhatsApp send adapter", "http://127.0.0.1:8792/health", True),
    Probe("Ops dashboard", "http://127.0.0.1:8793/health", True, "OPS_TOKEN", "replau_health_dashboard.py"),
    Probe("Product admin", "http://127.0.0.1:8794/health", True, "ADMIN_TOKEN", "replau_product_admin.py"),
    Probe("Payment proof review", "http://127.0.0.1:8795/health", True, "REVIEW_TOKEN", "replau_payment_proof_review.py"),
    Probe("OpenClaw gateway", "http://127.0.0.1:18789/health", True),
]


class Gate:
    def __init__(self) -> None:
        self.ok_count = 0
        self.warnings: list[str] = []
        self.failures: list[str] = []

    def ok(self, message: str) -> None:
        self.ok_count += 1
        print(f"[OK] {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[WARN] {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[FAIL] {message}", file=sys.stderr)

    def run(self, name: str, cmd: list[str], timeout: int) -> str | None:
        try:
            result = subprocess.run(
                cmd,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail(f"{name} timed out after {timeout}s")
            return None
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            self.fail(f"{name} failed with exit {result.returncode}: {detail[-1200:]}")
            return None
        self.ok(name)
        return result.stdout

    def summary(self) -> int:
        print()
        print(f"Summary: {self.ok_count} OK, {len(self.warnings)} warnings, {len(self.failures)} failures")
        if self.warnings:
            print("Warnings:")
            for item in self.warnings:
                print(f"- {item}")
        if self.failures:
            print("Failures:")
            for item in self.failures:
                print(f"- {item}")
        return 1 if self.failures else 0


def iter_python_files() -> Iterable[Path]:
    seen: set[Path] = set()
    for top in SOURCE_DIRS:
        if not top.exists():
            continue
        for path in top.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if path not in seen:
                seen.add(path)
                yield path
    for path in TOP_LEVEL_PY:
        if path.exists() and path not in seen:
            yield path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_env_value(process_marker: str, env_name: str) -> str:
    if not process_marker or not env_name:
        return ""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
            if process_marker not in cmdline:
                continue
            environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        except OSError:
            continue
        prefix = f"{env_name}=".encode()
        for item in environ:
            if item.startswith(prefix):
                return item[len(prefix) :].decode("utf-8", "ignore")
    return ""


def probe_url(probe: Probe) -> str:
    token = os.environ.get(probe.token_env, "") or process_env_value(probe.process_marker, probe.token_env)
    if not token:
        return probe.url
    separator = "&" if urlparse(probe.url).query else "?"
    return f"{probe.url}{separator}token={quote(token, safe='')}"


def check_python_compile(gate: Gate) -> None:
    files = [str(path) for path in iter_python_files()]
    if not files:
        gate.fail("no Python files found for compile check")
        return
    gate.run("Python compile check", [sys.executable, "-m", "py_compile", *files], timeout=90)


def check_source_deploy_drift(gate: Gate) -> None:
    for pair in DEPLOY_PAIRS:
        if not pair.source.exists():
            gate.fail(f"{pair.label}: source missing {pair.source}")
            continue
        if not pair.deployed.exists():
            gate.fail(f"{pair.label}: deployed file missing {pair.deployed}")
            continue
        if sha256(pair.source) != sha256(pair.deployed):
            gate.fail(f"{pair.label}: source/deployed drift ({pair.source} != {pair.deployed})")
            continue
        gate.ok(f"{pair.label} source matches deployed copy")


def check_http(gate: Gate, timeout: int) -> None:
    for probe in HTTP_PROBES:
        url = probe_url(probe)
        req = Request(url, headers={"User-Agent": "replau-ci-cd-gate/1.0"})
        try:
            with urlopen(req, timeout=timeout) as response:
                body = response.read()
                status = response.status
        except HTTPError as exc:
            gate.fail(f"{probe.label} HTTP {exc.code} at {probe.url}")
            continue
        except URLError as exc:
            gate.fail(f"{probe.label} unreachable at {probe.url}: {exc.reason}")
            continue
        except TimeoutError:
            gate.fail(f"{probe.label} timed out at {probe.url}")
            continue
        if status >= 400:
            gate.fail(f"{probe.label} HTTP {status} at {probe.url}")
            continue
        if probe.expect_json_ok:
            try:
                data = json.loads(body.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                gate.fail(f"{probe.label} did not return JSON health data")
                continue
            if data.get("ok") is not True:
                detail = json_health_summary(data)
                gate.fail(f"{probe.label} health ok was not true: {detail}")
                continue
        gate.ok(f"{probe.label} probe healthy")


def json_health_summary(data: object) -> str:
    if not isinstance(data, dict):
        return str(data)[:500]
    summary: dict[str, object] = {}
    for key in ("overall", "critical", "warnings", "status", "error"):
        if key in data:
            summary[key] = data[key]
    return json.dumps(summary or data, ensure_ascii=False)[:1000]


def check_systemd(gate: Gate) -> None:
    services = [
        "postgrest-localapi.service",
        "replau-openclaw-whatsapp-bridge.service",
        "replau-logistics-viewer.service",
        "replau-kitchen-ui.service",
        "replau-openclaw-whatsapp-send-adapter.service",
        "replau-health-dashboard.service",
        "replau-product-admin.service",
        "replau-payment-proof-review.service",
        "postgresql.service",
        "apache2.service",
    ]
    for service in services:
        out = gate.run(f"systemd active {service}", ["systemctl", "is-active", service], timeout=10)
        if out is not None and out.strip() != "active":
            gate.fail(f"{service} is {out.strip() or 'unknown'}")

    user_services = ["openclaw-gateway.service"]
    for service in user_services:
        out = gate.run(f"user systemd active {service}", ["systemctl", "--user", "is-active", service], timeout=10)
        if out is not None and out.strip() != "active":
            gate.fail(f"{service} is {out.strip() or 'unknown'}")


def check_git_clean(gate: Gate) -> None:
    if not (ROOT / ".git").exists():
        gate.fail(f"{ROOT} is not a git repository")
        return
    status = gate.run(
        "git working tree status",
        ["git", "-C", str(ROOT), "status", "--porcelain=v1", "--untracked-files=all"],
        timeout=20,
    )
    if status is None:
        return
    dirty = [line for line in status.splitlines() if line.strip()]
    if dirty:
        sample = "\n".join(dirty[:30])
        if len(dirty) > 30:
            sample += f"\n... {len(dirty) - 30} more"
        gate.fail(f"git working tree is not clean:\n{sample}")
        return
    gate.ok("git working tree is clean")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Replau local CI/CD gate.")
    parser.add_argument("--skip-http", action="store_true", help="Skip local HTTP health probes.")
    parser.add_argument("--systemd", action="store_true", help="Also require key systemd services to be active.")
    parser.add_argument("--require-clean-git", action="store_true", help="Fail unless /home/guill/codex has no uncommitted git changes.")
    parser.add_argument("--web-qa", action="store_true", help="Run non-mutating web QA after static checks.")
    parser.add_argument("--smoke", action="store_true", help="Run full integration smoke after web QA/static checks.")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds for HTTP probes and web QA.")
    parser.add_argument("--smoke-timeout", type=int, default=90, help="Timeout in seconds for the smoke test.")
    args = parser.parse_args()

    gate = Gate()
    check_python_compile(gate)
    check_source_deploy_drift(gate)
    if args.require_clean_git:
        check_git_clean(gate)
    if args.systemd:
        check_systemd(gate)
    if not args.skip_http:
        check_http(gate, timeout=args.timeout)
    if args.web_qa:
        gate.run("web QA no-mutate", [str(ROOT / "replau_web_qa.py"), "--timeout", str(args.timeout), "--no-mutate"], timeout=args.timeout + 20)
    if args.smoke:
        gate.run("integration smoke", [str(ROOT / "replau_integration_smoke_test.py"), "--timeout", str(args.smoke_timeout)], timeout=args.smoke_timeout + 30)
    return gate.summary()


if __name__ == "__main__":
    raise SystemExit(main())
