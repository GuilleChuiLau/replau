#!/usr/bin/env python3
"""Validate or explicitly submit Replau WhatsApp templates to Meta.

Dry-run is the default. Applying requires both --apply and
META_TEMPLATE_APPLY_CONFIRM=YES so normal tests cannot create external state.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "templates.es_PE.json"
NAME_RE = re.compile(r"^[a-z0-9_]{1,512}$")
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}([_-][A-Za-z0-9]{2,8})?$")


def load_and_validate(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("templates"), list):
        raise ValueError("manifest must use schema_version 1 and contain templates")
    names: set[str] = set()
    templates: list[dict[str, Any]] = []
    for template in data["templates"]:
        if not isinstance(template, dict):
            raise ValueError("each template must be an object")
        name = str(template.get("name") or "")
        language = str(template.get("language") or "")
        category = str(template.get("category") or "")
        components = template.get("components")
        if not NAME_RE.fullmatch(name) or name in names:
            raise ValueError(f"invalid or duplicate template name: {name}")
        if not LANGUAGE_RE.fullmatch(language):
            raise ValueError(f"invalid template language: {language}")
        if category not in {"UTILITY", "MARKETING", "AUTHENTICATION"}:
            raise ValueError(f"invalid template category: {category}")
        if not isinstance(components, list) or not components or len(components) > 10:
            raise ValueError(f"invalid components for template: {name}")
        serialized = json.dumps(template, ensure_ascii=False)
        if "access_token" in serialized.lower() or "bearer " in serialized.lower():
            raise ValueError(f"manifest must not contain credentials: {name}")
        names.add(name)
        templates.append(template)
    return templates


def submit_template(template: dict[str, Any], api_base: str, api_version: str, waba_id: str, token: str) -> dict[str, Any]:
    request = Request(
        f"{api_base.rstrip('/')}/{api_version}/{waba_id}/message_templates",
        data=json.dumps(template, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise RuntimeError(f"Meta rejected {template['name']}: HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Meta request failed for {template['name']}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or provision Replau WhatsApp templates")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--apply", action="store_true", help="Create templates in the configured Meta WABA")
    args = parser.parse_args()
    templates = load_and_validate(args.manifest)

    if not args.apply:
        print(json.dumps({"ok": True, "dry_run": True, "templates": [item["name"] for item in templates]}, indent=2))
        return 0

    if os.environ.get("META_TEMPLATE_APPLY_CONFIRM") != "YES":
        raise SystemExit("Refusing external changes: set META_TEMPLATE_APPLY_CONFIRM=YES with --apply")
    waba_id = os.environ.get("META_WABA_ID", "").strip()
    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    if not waba_id or not token:
        raise SystemExit("META_WABA_ID and META_ACCESS_TOKEN are required with --apply")
    api_base = os.environ.get("META_GRAPH_API_BASE", "https://graph.facebook.com")
    api_version = os.environ.get("META_GRAPH_API_VERSION", "v23.0")
    results = [submit_template(item, api_base, api_version, waba_id, token) for item in templates]
    print(json.dumps({"ok": True, "created": len(results), "responses": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
