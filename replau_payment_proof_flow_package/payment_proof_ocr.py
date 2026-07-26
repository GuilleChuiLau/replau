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

CACHE_VERSION = 5

PROVIDER_MARKERS = {
    "YAPE": ("yape", "yapeaste"),
    "PLIN": ("plin", "plineaste"),
    "BCP": ("bcp", "banco de credito"),
    "BBVA": ("bbva",),
    "INTERBANK": ("interbank",),
    "SCOTIABANK": ("scotiabank",),
}

RECIPIENT_LABELS = (
    "pagado a", "destinatario", "para", "recibido por", "enviaste a",
    "enviado a", "pagaste a", "nombre del destinatario",
)

OPERATION_LABELS = (
    "numero de operacion", "nro de operacion", "nro. de operacion",
    "codigo de operacion", "operacion", "constancia", "referencia",
)


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


def _money(value: str) -> float | None:
    try:
        return float(Decimal(value.replace(",", "").strip()))
    except (InvalidOperation, ValueError):
        return None


def _localized_money(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9.,]", "", value or "")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(".") > cleaned.rfind(","):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    return _money(cleaned)


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


def _provider_recipient(lines: list[str]) -> str | None:
    """Extract common same-line Yape/Plin recipient phrases."""
    patterns = (
        r"^\s*yapeaste\s+a\s+(.+)$",
        r"^\s*plineaste\s+a\s+(.+)$",
        r"^\s*(?:enviaste|pagaste|transferiste)\s+a\s+(.+)$",
    )
    for index, line in enumerate(lines):
        if _plain(line).strip() in {"yapeaste a", "plineaste a", "enviaste a", "pagaste a", "transferiste a"}:
            return lines[index + 1].strip() if index + 1 < len(lines) else None
        for pattern in patterns:
            match = re.match(pattern, _plain(line), re.I)
            if match:
                # Preserve the OCR text when the recipient is on the next line.
                if match.group(1).strip():
                    words = line.split()
                    return " ".join(words[2:]).strip() if len(words) > 2 else match.group(1).strip()
                if index + 1 < len(lines):
                    return lines[index + 1].strip()
    return None


def _line_confidence(
    lines: list[str],
    confidences: list[float],
    predicate: Any,
    default: float = 0.0,
) -> float:
    matches = [confidences[i] for i, line in enumerate(lines) if predicate(line) and i < len(confidences)]
    return round(max(matches), 4) if matches else default


def extract_fields_detailed(
    lines: list[str],
    confidences: list[float] | None = None,
) -> tuple[dict[str, Any], dict[str, float], dict[str, str]]:
    confidences = confidences or [0.0] * len(lines)
    joined = "\n".join(lines)
    amount_matches = re.findall(
        r"(?:S\s*/|S/|PEN)\s*([0-9][0-9.,]*(?:[.,]\d{2})?)",
        joined,
        re.I,
    )
    operation = None
    operation_line = ""
    for index, line in enumerate(lines):
        normalized = _plain(line)
        if any(label in normalized for label in OPERATION_LABELS):
            same_line = next((
                candidate for candidate in re.findall(r"\b[A-Z0-9-]{6,24}\b", line, re.I)
                if any(character.isdigit() for character in candidate)
            ), None)
            if same_line:
                operation = same_line
                operation_line = line
            elif index + 1 < len(lines):
                following = next((
                    candidate for candidate in re.findall(r"\b[A-Z0-9-]{6,24}\b", lines[index + 1], re.I)
                    if any(character.isdigit() for character in candidate)
                ), None)
                operation = following
                operation_line = lines[index + 1] if following else ""
            break
    plain_joined = _plain(joined)
    provider = next(
        (name for name, markers in PROVIDER_MARKERS.items() if any(marker in plain_joined for marker in markers)),
        None,
    )
    recipient = _provider_recipient(lines) or _next_value(lines, RECIPIENT_LABELS)
    if not recipient and provider in {"YAPE", "PLIN"}:
        for index, line in enumerate(lines[:-1]):
            if re.search(r"(?:S\s*/|S/|PEN)\s*[0-9]", line, re.I):
                candidate = lines[index + 1].strip()
                normalized_candidate = _plain(candidate)
                if (
                    re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]{2,}", candidate)
                    and not re.search(r"\b20\d{2}\b|\d{1,2}:\d{2}", candidate)
                    and not any(label in normalized_candidate for label in OPERATION_LABELS)
                ):
                    recipient = candidate
                    break
    compact = re.sub(r"[^a-z0-9]", "", _plain(joined))
    # OCR commonly reads the opening inverted exclamation mark as "i" and
    # drops spaces, so match the stable words rather than punctuation.
    success = any(term in compact for term in (
        "pagodeservicioexitoso", "pagoexitoso", "operacionexitosa",
        "transferenciaexitosa", "envioexitoso", "yapeaste",
    ))
    timestamp = next((
        line for line in lines
        if (
            re.search(r"\b20\d{2}\b", line)
            or re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", line)
        ) and re.search(r"\d{1,2}:\d{2}", line)
    ), None)
    amount_text = amount_matches[0] if amount_matches else ""
    amount_value = _localized_money(amount_text)
    fields = {
        "amount": amount_value,
        "operation_number": operation,
        "recipient": recipient,
        "provider": provider,
        "timestamp_text": timestamp,
        "success_text_detected": success,
    }
    field_confidence = {
        "provider": _line_confidence(lines, confidences, lambda line: provider is not None and any(
            marker in _plain(line) for marker in PROVIDER_MARKERS.get(provider or "", ())
        )),
        "amount": _line_confidence(lines, confidences, lambda line: amount_text in line),
        "recipient": _line_confidence(lines, confidences, lambda line: recipient is not None and recipient in line),
        "operation_number": _line_confidence(lines, confidences, lambda line: bool(operation_line) and line == operation_line),
        "timestamp_text": _line_confidence(lines, confidences, lambda line: timestamp is not None and line == timestamp),
        "success_text_detected": _line_confidence(lines, confidences, lambda line: any(
            term in re.sub(r"[^a-z0-9]", "", _plain(line))
            for term in (
                "pagodeservicioexitoso", "pagoexitoso", "operacionexitosa",
                "transferenciaexitosa", "envioexitoso", "yapeaste",
            )
        )),
    }
    evidence = {
        "provider": provider or "",
        "amount": amount_text,
        "recipient": recipient or "",
        "operation_number": operation_line,
        "timestamp_text": timestamp or "",
    }
    return fields, field_confidence, evidence


def extract_fields(lines: list[str]) -> dict[str, Any]:
    fields, _, _ = extract_fields_detailed(lines)
    return fields


class PaymentProofOCR:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = Path(cache_dir or os.environ.get(
            "PAYMENT_OCR_CACHE_DIR", "~/.local/state/replau/payment-ocr"
        )).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._engine: Any = None

    def _run_engine(self, source: Any, pass_name: str) -> list[dict[str, Any]]:
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
        result, _ = self._engine(source)
        rows = result or []
        return [
            {"text": str(row[1]).strip(), "confidence": round(float(row[2]), 4), "pass": pass_name}
            for row in rows
            if str(row[1]).strip()
        ]

    def _ocr(self, path: Path) -> tuple[list[str], list[float], float, list[str]]:
        passes = [("original", str(path))]
        try:
            import cv2
            image = cv2.imread(str(path))
            if image is not None:
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                scale = max(1.0, min(2.0, 1600 / max(gray.shape)))
                enlarged = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                contrast = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(enlarged)
                threshold = cv2.adaptiveThreshold(
                    contrast, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
                )
                passes.extend((("contrast", contrast), ("threshold", threshold)))
        except (ImportError, OSError, ValueError):
            pass

        best: dict[str, dict[str, Any]] = {}
        used_passes: list[str] = []
        for pass_name, source in passes:
            try:
                entries = self._run_engine(source, pass_name)
            except Exception:
                if pass_name == "original":
                    raise
                continue
            used_passes.append(pass_name)
            for entry in entries:
                key = re.sub(r"[^a-z0-9]", "", _plain(entry["text"]))
                if not key:
                    continue
                if key not in best or entry["confidence"] > best[key]["confidence"]:
                    best[key] = entry

        rows = list(best.values())
        lines = [row["text"] for row in rows]
        confidences = [float(row["confidence"]) for row in rows]
        average = sum(confidences) / len(confidences) if confidences else 0.0
        return lines, confidences, average, used_passes

    def analyze(self, path: Path, order_total: Any = None) -> dict[str, Any]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        cache_file = self.cache_dir / f"{digest}.json"
        if cache_file.is_file():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("cache_version") == CACHE_VERSION:
                return self._score(cached, order_total, digest)

        lines, confidences, confidence, passes = self._ocr(path)
        fields, field_confidence, evidence = extract_fields_detailed(lines, confidences)
        base = {
            "sha256": digest,
            "cache_version": CACHE_VERSION,
            "engine": "rapidocr-onnxruntime",
            "ocr_confidence": round(confidence, 4),
            "ocr_passes": passes,
            "lines": lines,
            "line_confidences": confidences,
            "fields": fields,
            "field_confidence": field_confidence,
            "field_evidence": evidence,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }
        cache_file.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._score(base, order_total, digest)

    def _score(self, base: dict[str, Any], order_total: Any, digest: str) -> dict[str, Any]:
        result = dict(base)
        fields = dict(result.get("fields") or {})
        warnings: list[str] = []
        review_reasons: list[dict[str, str]] = []
        checks: dict[str, Any] = {}

        def reason(code: str, severity: str, message: str) -> None:
            review_reasons.append({"code": code, "severity": severity, "message": message})
            warnings.append(message)

        expected_terms = [x.strip() for x in os.environ.get("PAYMENT_EXPECTED_RECIPIENTS", "").split(",") if x.strip()]
        recipient = _plain(str(fields.get("recipient") or ""))
        checks["recipient_match"] = None if not expected_terms else any(_plain(x) in recipient for x in expected_terms)
        if checks["recipient_match"] is False:
            reason("RECIPIENT_MISMATCH", "HIGH", "Recipient does not match the configured restaurant/payment recipient.")

        expected_amount = _money(str(order_total)) if order_total not in (None, "") else None
        extracted_amount = fields.get("amount")
        checks["amount_match"] = None if expected_amount is None or extracted_amount is None else abs(expected_amount - float(extracted_amount)) < 0.01
        if checks["amount_match"] is False:
            reason("AMOUNT_MISMATCH", "HIGH", "Receipt amount does not match the order total.")
        if extracted_amount is None:
            reason("AMOUNT_MISSING", "HIGH", "No payment amount could be extracted.")
        if not fields.get("operation_number"):
            reason("OPERATION_MISSING", "MEDIUM", "No operation/reference number could be extracted.")
        if not fields.get("success_text_detected"):
            reason("SUCCESS_TEXT_MISSING", "MEDIUM", "No successful-payment wording was detected.")
        if not fields.get("provider"):
            reason("PROVIDER_UNKNOWN", "LOW", "Payment provider could not be identified.")

        field_confidence = result.get("field_confidence") or {}
        for field_name in ("amount", "recipient", "operation_number"):
            if fields.get(field_name) and float(field_confidence.get(field_name) or 0) < 0.65:
                reason(
                    f"LOW_CONFIDENCE_{field_name.upper()}",
                    "MEDIUM",
                    f"The extracted {field_name.replace('_', ' ')} has low OCR confidence.",
                )

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
            reason("DUPLICATE_OPERATION", "HIGH", "The operation number appears in another analyzed proof.")

        result["checks"] = checks
        result["warnings"] = warnings
        result["review_reasons"] = review_reasons
        result["recommendation"] = "MANUAL_REVIEW" if not warnings else "REVIEW_OR_REJECT"
        result["advisory_only"] = True
        return result
