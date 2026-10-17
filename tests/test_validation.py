from datetime import date
from decimal import Decimal

from invoiceguard.models import Invoice, LineItem, Party
from invoiceguard.validation import has_errors, iban_is_valid, validate

SELLER = Party(name="Test GmbH", vat_id="DE811234567", street="Weg 1", city="Berlin", postal_code="10115")
BUYER = Party(name="Spreeufer Möbelwerk GmbH", vat_id="DE298765432")


def make(**overrides) -> Invoice:
    base = dict(
        invoice_number="T-1",
        issue_date=date(2026, 10, 1),
        seller=SELLER,
        buyer=BUYER,
        lines=[LineItem(description="Paper", quantity=Decimal("10"), unit_price=Decimal("4.20"), vat_rate=Decimal("19"), net_amount=Decimal("42.00"))],
        net_total=Decimal("42.00"),
        vat_total=Decimal("7.98"),
        gross_total=Decimal("49.98"),
        iban="DE97200400600123456789",
    )
    base.update(overrides)
    return Invoice(**base)


def codes(inv, **kw):
    return {i.code for i in validate(inv, buyer_vat_id="DE298765432", today=date(2026, 10, 17), **kw)}


def test_iban_checksum():
    assert iban_is_valid("DE97 2004 0060 0123 4567 89")
    assert not iban_is_valid("DE98200400600123456789")
    assert not iban_is_valid("DE972004006001234567")  # wrong length for DE


def test_clean_invoice_has_no_errors():
    issues = validate(make(), buyer_vat_id="DE298765432", today=date(2026, 10, 17))
    assert not has_errors(issues)


def test_vat_miscalculation_detected():
    assert "VAT_MISCALCULATED" in codes(make(vat_total=Decimal("8.40"), gross_total=Decimal("50.40")))


def test_line_math_and_totals():
    bad_line = LineItem(description="Paper", quantity=Decimal("10"), unit_price=Decimal("4.20"), vat_rate=Decimal("19"), net_amount=Decimal("44.00"))
    found = codes(make(lines=[bad_line]))
    assert "LINE_MATH" in found and "NET_TOTAL_MISMATCH" in found


def test_dates_and_identifiers():
    found = codes(make(issue_date=date(2027, 1, 1), due_date=date(2026, 12, 1), iban="DE00123", seller=SELLER.model_copy(update={"vat_id": "12345"})))
    assert {"FUTURE_DATE", "DUE_BEFORE_ISSUE", "INVALID_IBAN", "INVALID_SELLER_VAT_ID"} <= found


def test_wrong_buyer():
    assert "WRONG_BUYER" in codes(
        make(
            buyer=Party(name="Other GmbH", vat_id="DE111111111"),
            net_total=Decimal("420.00"),
            lines=[LineItem(description="X", quantity=Decimal("100"), unit_price=Decimal("4.20"), vat_rate=Decimal("19"), net_amount=Decimal("420.00"))],
            vat_total=Decimal("79.80"),
            gross_total=Decimal("499.80"),
        )
    )
