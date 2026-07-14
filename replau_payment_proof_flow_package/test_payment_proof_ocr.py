#!/usr/bin/env python3
from payment_proof_ocr import extract_fields


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


if __name__ == "__main__":
    test_bcp_service_receipt_fields()
    print("PAYMENT_OCR_UNIT_OK")
