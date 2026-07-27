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
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CACHE_VERSION = 8
PAYMENT_TIMEZONE = ZoneInfo(os.environ.get("PAYMENT_TIMEZONE", "America/Lima"))

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

SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


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


def _parse_receipt_timestamp(value: str | None) -> datetime | None:
    text = _plain(value or "").replace("a. m.", "am").replace("p. m.", "pm")
    text = text.replace("a.m.", "am").replace("p.m.", "pm")
    numeric = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\D+(\d{1,2}):(\d{2})(?:\s*(am|pm))?",
        text,
    )
    if numeric:
        day, month, year, hour, minute = (int(numeric.group(i)) for i in range(1, 6))
        year += 2000 if year < 100 else 0
        marker = numeric.group(6)
        if marker == "pm" and hour < 12:
            hour += 12
        elif marker == "am" and hour == 12:
            hour = 0
        try:
            return datetime(year, month, day, hour, minute, tzinfo=PAYMENT_TIMEZONE)
        except ValueError:
            return None
    named = re.search(
        r"\b(\d{1,2})\s+(?:de\s+)?([a-z]+)\.?\s+(?:de\s+)?(20\d{2})[^:]*?(\d{1,2}):(\d{2})(?:\s*(am|pm))?",
        text,
    )
    if named and named.group(2) in SPANISH_MONTHS:
        day, year, hour, minute = int(named.group(1)), int(named.group(3)), int(named.group(4)), int(named.group(5))
        marker = named.group(6)
        if marker == "pm" and hour < 12:
            hour += 12
        elif marker == "am" and hour == 12:
            hour = 0
        try:
            return datetime(year, SPANISH_MONTHS[named.group(2)], day, hour, minute, tzinfo=PAYMENT_TIMEZONE)
        except ValueError:
            return None
    return None


def _hamming_distance(left: str, right: str) -> int | None:
    try:
        if len(left) != len(right):
            return None
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return None


def _perceptual_hash(path: Path) -> str | None:
    try:
        import cv2
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return None
        resized = cv2.resize(image, (9, 8), interpolation=cv2.INTER_AREA)
        bits = resized[:, 1:] > resized[:, :-1]
        value = 0
        for bit in bits.flatten():
            value = (value << 1) | int(bool(bit))
        return f"{value:016x}"
    except (ImportError, OSError, ValueError):
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

    def analyze(self, path: Path, order_total: Any = None, force: bool = False) -> dict[str, Any]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        cache_file = self.cache_dir / f"{digest}.json"
        if cache_file.is_file() and not force:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("cache_version") == CACHE_VERSION:
                return self._score(cached, order_total, digest)

        lines, confidences, confidence, passes = self._ocr(path)
        fields, field_confidence, evidence = extract_fields_detailed(lines, confidences)
        base = {
            "sha256": digest,
            "perceptual_hash": _perceptual_hash(path),
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
        similar_images: list[dict[str, Any]] = []
        operation = fields.get("operation_number")
        perceptual_hash = str(result.get("perceptual_hash") or "")
        similarity_limit = max(0, min(16, int(os.environ.get("PAYMENT_PERCEPTUAL_HASH_DISTANCE", "2"))))
        for candidate in self.cache_dir.glob("*.json"):
            if candidate.stem == digest:
                continue
            try:
                other = json.loads(candidate.read_text(encoding="utf-8"))
                other_fields = other.get("fields") or {}
                if operation and other_fields.get("operation_number") == operation:
                    duplicates.append(candidate.stem)
                other_hash = str(other.get("perceptual_hash") or "")
                distance = _hamming_distance(perceptual_hash, other_hash) if perceptual_hash and other_hash else None
                if distance is not None and distance <= similarity_limit:
                    similar_images.append({"sha256": candidate.stem, "distance": distance})
            except (OSError, ValueError):
                continue
        checks["duplicate_operation"] = bool(duplicates)
        if duplicates:
            reason("DUPLICATE_OPERATION", "HIGH", "The operation number appears in another analyzed proof.")
        checks["similar_image"] = bool(similar_images)
        checks["similar_image_distance"] = min((item["distance"] for item in similar_images), default=None)
        if similar_images:
            reason("SIMILAR_PROOF_IMAGE", "HIGH", "This proof image is visually similar to another analyzed proof.")

        receipt_time = _parse_receipt_timestamp(str(fields.get("timestamp_text") or ""))
        checks["receipt_timestamp_parsed"] = receipt_time is not None
        checks["receipt_age_hours"] = None
        if not fields.get("timestamp_text"):
            reason("TIMESTAMP_MISSING", "MEDIUM", "No receipt date and time could be extracted.")
        elif receipt_time is None:
            reason("TIMESTAMP_UNPARSEABLE", "MEDIUM", "The extracted receipt date and time could not be parsed.")
        else:
            fields["timestamp_iso"] = receipt_time.isoformat()
            now = datetime.now(PAYMENT_TIMEZONE)
            age_hours = (now - receipt_time).total_seconds() / 3600
            checks["receipt_age_hours"] = round(age_hours, 2)
            max_age_hours = max(1, int(os.environ.get("PAYMENT_RECEIPT_MAX_AGE_HOURS", "72")))
            future_minutes = max(0, int(os.environ.get("PAYMENT_RECEIPT_FUTURE_TOLERANCE_MINUTES", "10")))
            if receipt_time > now + timedelta(minutes=future_minutes):
                reason("TIMESTAMP_IN_FUTURE", "HIGH", "The receipt timestamp is later than the allowed clock tolerance.")
            elif age_hours > max_age_hours:
                reason("TIMESTAMP_TOO_OLD", "HIGH", f"The receipt is older than the configured {max_age_hours}-hour limit.")

        result["fields"] = fields
        result["similar_images"] = similar_images
        result["checks"] = checks
        result["warnings"] = warnings
        result["review_reasons"] = review_reasons
        result["recommendation"] = "MANUAL_REVIEW" if not warnings else "REVIEW_OR_REJECT"
        result["advisory_only"] = True
        return result
