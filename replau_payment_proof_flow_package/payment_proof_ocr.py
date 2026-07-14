#!/usr/bin/env python3
"""Local, advisory OCR for Replau payment proofs.

OCR can compare what a screenshot says with an order. It cannot prove that the
bank transaction exists, so this module never returns an automatic approval.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CACHE_VERSION = 2


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


def _money(value: str) -> float | None:
    try:
        return float(Decimal(value.replace(",", "").strip()))
    except (InvalidOperation, ValueError):
        return None


def _next_value(lines: list[str], labels: tuple[str, ...]) -> str | None:
    for index, line in enumerate(lines):
        normalized = _plain(line)
        if any(label in normalized for label in labels):
            remainder = re.sub(r"^[^:]*:\s*", "", line).strip()
            if ":" in line and remainder != line:
                return remainder
            if index + 1 < len(lines):
                return lines[index + 1].strip()
    return None


def extract_fields(lines: list[str]) -> dict[str, Any]:
    joined = "\n".join(lines)
    amount_matches = re.findall(r"(?:S\s*/|S/|PEN)\s*([0-9][0-9,]*\.\d{2})", joined, re.I)
    operation = None
    for index, line in enumerate(lines):
        if "operacion" in _plain(line):
            same_line = re.search(r"([0-9]{6,})", line)
            if same_line:
                operation = same_line.group(1)
            elif index + 1 < len(lines):
                following = re.search(r"([0-9]{6,})", lines[index + 1])
                operation = following.group(1) if following else None
            break
    recipient = _next_value(lines, ("pagado a", "destinatario", "para", "recibido por"))
    provider = "BCP" if "bcp" in _plain(joined) else None
    compact = re.sub(r"[^a-z0-9]", "", _plain(joined))
    # OCR commonly reads the opening inverted exclamation mark as "i" and
    # drops spaces, so match the stable words rather than punctuation.
    success = any(term in compact for term in (
        "pagodeservicioexitoso", "pagoexitoso", "operacionexitosa"
    ))
    timestamp = next((line for line in lines if re.search(r"\b20\d{2}\b", line) and re.search(r"\d{1,2}:\d{2}", line)), None)
    return {
        "amount": _money(amount_matches[0]) if amount_matches else None,
        "operation_number": operation,
        "recipient": recipient,
        "provider": provider,
        "timestamp_text": timestamp,
        "success_text_detected": success,
    }


class PaymentProofOCR:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = Path(cache_dir or os.environ.get(
            "PAYMENT_OCR_CACHE_DIR", "~/.local/state/replau/payment-ocr"
        )).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._engine: Any = None

    def _ocr(self, path: Path) -> tuple[list[str], float]:
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
        result, _ = self._engine(str(path))
        rows = result or []
        return [str(row[1]).strip() for row in rows], (
            sum(float(row[2]) for row in rows) / len(rows) if rows else 0.0
        )

    def analyze(self, path: Path, order_total: Any = None) -> dict[str, Any]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        cache_file = self.cache_dir / f"{digest}.json"
        if cache_file.is_file():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("cache_version") == CACHE_VERSION:
                return self._score(cached, order_total, digest)

        lines, confidence = self._ocr(path)
        base = {
            "sha256": digest,
            "cache_version": CACHE_VERSION,
            "engine": "rapidocr-onnxruntime",
            "ocr_confidence": round(confidence, 4),
            "lines": lines,
            "fields": extract_fields(lines),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        cache_file.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._score(base, order_total, digest)

    def _score(self, base: dict[str, Any], order_total: Any, digest: str) -> dict[str, Any]:
        result = dict(base)
        fields = dict(result.get("fields") or {})
        warnings: list[str] = []
        checks: dict[str, Any] = {}

        expected_terms = [x.strip() for x in os.environ.get("PAYMENT_EXPECTED_RECIPIENTS", "").split(",") if x.strip()]
        recipient = _plain(str(fields.get("recipient") or ""))
        checks["recipient_match"] = None if not expected_terms else any(_plain(x) in recipient for x in expected_terms)
        if checks["recipient_match"] is False:
            warnings.append("Recipient does not match the configured restaurant/payment recipient.")

        expected_amount = _money(str(order_total)) if order_total not in (None, "") else None
        extracted_amount = fields.get("amount")
        checks["amount_match"] = None if expected_amount is None or extracted_amount is None else abs(expected_amount - float(extracted_amount)) < 0.01
        if checks["amount_match"] is False:
            warnings.append("Receipt amount does not match the order total.")
        if extracted_amount is None:
            warnings.append("No payment amount could be extracted.")
        if not fields.get("operation_number"):
            warnings.append("No operation/reference number could be extracted.")
        if not fields.get("success_text_detected"):
            warnings.append("No successful-payment wording was detected.")

        duplicates = []
        operation = fields.get("operation_number")
        for candidate in self.cache_dir.glob("*.json"):
            if candidate.stem == digest:
                continue
            try:
                other = json.loads(candidate.read_text(encoding="utf-8"))
                other_fields = other.get("fields") or {}
                if operation and other_fields.get("operation_number") == operation:
                    duplicates.append(candidate.stem)
            except (OSError, ValueError):
                continue
        checks["duplicate_operation"] = bool(duplicates)
        if duplicates:
            warnings.append("The operation number appears in another analyzed proof.")

        result["checks"] = checks
        result["warnings"] = warnings
        result["recommendation"] = "MANUAL_REVIEW" if not warnings else "REVIEW_OR_REJECT"
        result["advisory_only"] = True
        return result
