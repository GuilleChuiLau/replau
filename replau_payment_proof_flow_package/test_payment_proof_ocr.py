#!/usr/bin/env python3
import tempfile
from pathlib import Path

from payment_proof_ocr import PaymentProofOCR, extract_fields, extract_fields_detailed


def test_bcp_service_receipt_fields() -> None:
    fields = extract_fields([
        "BCP", "¡Pago de servicio exitoso!", "S/ 239.90",
        "Martes, 30 Junio 2026 - 11:57 a.m.", "Pagado a",
        "Movistar-Integratel Peru", "Número de operación", "02419854",
    ])
    assert fields["provider"] == "BCP"
    assert fields["amount"] == 239.90
    assert fields["recipient"] == "Movistar-Integratel Peru"
    assert fields["operation_number"] == "02419854"
    assert fields["success_text_detected"] is True


def test_yape_fields_and_localized_amount() -> None:
    fields, confidence, _ = extract_fields_detailed([
        "Yape", "¡Yapeaste!", "S/ 1,239.90", "Enviado a",
        "BIG BOY SAC", "Nro. de operación", "YA-98127364",
        "26/07/2026 14:35",
    ], [0.99, 0.97, 0.96, 0.94, 0.93, 0.91, 0.90, 0.89])
    assert fields["provider"] == "YAPE"
    assert fields["amount"] == 1239.90
    assert fields["recipient"] == "BIG BOY SAC"
    assert fields["operation_number"] == "YA-98127364"
    assert fields["timestamp_text"] == "26/07/2026 14:35"
    assert confidence["amount"] == 0.96


def test_plin_fields() -> None:
    fields = extract_fields([
        "PLIN", "Transferencia exitosa", "S/ 45,50",
        "Destinatario", "RESTAURANTE BIG BOY",
        "Código de operación", "PL99887766",
    ])
    assert fields["provider"] == "PLIN"
    assert fields["amount"] == 45.50
    assert fields["operation_number"] == "PL99887766"


def test_yape_compact_layout_recipient_after_amount() -> None:
    fields = extract_fields([
        "Yape", "¡Yapeaste!", "S/20", "BIG BOY RESTAURANTE",
        "26 julio 2026 14:35", "Nro. de operación", "99887766",
    ])
    assert fields["recipient"] == "BIG BOY RESTAURANTE"


def test_scoring_has_structured_reason_codes_and_stays_advisory() -> None:
    with tempfile.TemporaryDirectory() as directory:
        ocr = PaymentProofOCR(cache_dir=Path(directory))
        base = {
            "fields": {
                "provider": None,
                "amount": 10.0,
                "recipient": "Unknown",
                "operation_number": None,
                "success_text_detected": False,
            },
            "field_confidence": {"amount": 0.40, "recipient": 0.40},
        }
        result = ocr._score(base, 12.0, "fixture")
        codes = {reason["code"] for reason in result["review_reasons"]}
        assert {"AMOUNT_MISMATCH", "OPERATION_MISSING", "PROVIDER_UNKNOWN", "LOW_CONFIDENCE_AMOUNT"} <= codes
        assert result["advisory_only"] is True
        assert result["recommendation"] == "REVIEW_OR_REJECT"


if __name__ == "__main__":
    test_bcp_service_receipt_fields()
    test_yape_fields_and_localized_amount()
    test_plin_fields()
    test_yape_compact_layout_recipient_after_amount()
    test_scoring_has_structured_reason_codes_and_stays_advisory()
    print("PAYMENT_OCR_UNIT_OK")
