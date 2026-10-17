"""Deterministic validation: arithmetic, identifiers and mandatory content.

Covers the checks an AP clerk does by hand: §14 (4) UStG mandatory invoice
content, EN 16931 calculation rules (BR-CO-xx), IBAN checksum (ISO 13616)
and VAT-ID format. These never call an LLM, so they are cheap, explainable
and identical on every run.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .models import Invoice, Issue, Severity

CENT = Decimal("0.01")
GERMAN_VAT_RATES = {Decimal("0"), Decimal("7"), Decimal("19")}
EU_VAT_RE = re.compile(r"^(DE\d{9}|AT U\d{8}|ATU\d{8}|FR[A-Z0-9]{2}\d{9}|NL\d{9}B\d{2}|[A-Z]{2}[A-Z0-9]{8,12})$")
SMALL_INVOICE_LIMIT = Decimal("250")  # Kleinbetragsrechnung, §33 UStDV


def iban_is_valid(iban: str) -> bool:
    s = iban.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    if s.startswith("DE") and len(s) != 22:
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def _close(a: Decimal, b: Decimal, tol: Decimal = Decimal("0.02")) -> bool:
    return abs(Decimal(a) - Decimal(b)) <= tol


def validate(inv: Invoice, *, buyer_vat_id: str | None = None, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []

    def add(code: str, sev: Severity, msg: str, field: str | None = None, cat: str = "business") -> None:
        issues.append(Issue(code=code, severity=sev, message=msg, field=field, category=cat))

    small = Decimal(inv.gross_total) <= SMALL_INVOICE_LIMIT

    # ---- mandatory content (§14 (4) UStG / EN 16931 BR-xx)
    if not inv.invoice_number.strip():
        add("MISSING_INVOICE_NUMBER", Severity.ERROR, "Invoice number (BT-1) missing", "invoice_number", "extraction")
    if not inv.seller.name or inv.seller.name == "unknown":
        add("MISSING_SELLER", Severity.ERROR, "Seller name (BT-27) missing", "seller.name", "extraction")
    if not small and not (inv.seller.street and inv.seller.city):
        add("MISSING_SELLER_ADDRESS", Severity.ERROR, "Seller address incomplete", "seller", "extraction")
    if not inv.seller.vat_id:
        add("MISSING_SELLER_VAT_ID", Severity.ERROR, "Seller VAT ID / tax number missing (§14 UStG)", "seller.vat_id", "extraction")
    elif not EU_VAT_RE.match(inv.seller.vat_id.replace(" ", "")):
        add("INVALID_SELLER_VAT_ID", Severity.ERROR, f"Seller VAT ID '{inv.seller.vat_id}' has invalid format", "seller.vat_id")
    if not inv.lines:
        add("NO_LINES", Severity.ERROR, "No invoice lines found", "lines", "extraction")

    # ---- addressed to us?
    if buyer_vat_id and not small and inv.buyer.vat_id and inv.buyer.vat_id.replace(" ", "") != buyer_vat_id:
        add("WRONG_BUYER", Severity.ERROR, f"Invoice is addressed to VAT ID {inv.buyer.vat_id}, not to us", "buyer.vat_id")

    # ---- dates
    if inv.issue_date > today:
        add("FUTURE_DATE", Severity.ERROR, f"Issue date {inv.issue_date} is in the future", "issue_date")
    elif inv.issue_date < today - timedelta(days=365):
        add("STALE_INVOICE", Severity.WARNING, f"Invoice is older than 12 months ({inv.issue_date})", "issue_date")
    if inv.due_date and inv.due_date < inv.issue_date:
        add("DUE_BEFORE_ISSUE", Severity.ERROR, "Due date lies before issue date", "due_date")

    # ---- currency & rates
    if inv.currency != "EUR":
        add("FOREIGN_CURRENCY", Severity.WARNING, f"Currency {inv.currency}: FX handling required", "currency")
    for i, line in enumerate(inv.lines):
        if Decimal(line.vat_rate) not in GERMAN_VAT_RATES:
            add("UNUSUAL_VAT_RATE", Severity.WARNING, f"Line {i + 1}: VAT rate {line.vat_rate}% is not a German rate", f"lines[{i}].vat_rate")
        expected = (Decimal(line.quantity) * Decimal(line.unit_price)).quantize(CENT)
        if not _close(expected, line.net_amount):
            add(
                "LINE_MATH",
                Severity.ERROR,
                f"Line {i + 1}: {line.quantity} x {line.unit_price} = {expected}, invoice says {line.net_amount}",
                f"lines[{i}].net_amount",
                "extraction",
            )

    # ---- totals (BR-CO-10 .. BR-CO-15)
    line_sum = sum((Decimal(line.net_amount) for line in inv.lines), Decimal("0"))
    if inv.lines and not _close(line_sum, inv.net_total):
        add("NET_TOTAL_MISMATCH", Severity.ERROR, f"Sum of lines {line_sum} != net total {inv.net_total}", "net_total", "extraction")

    by_rate: dict[Decimal, Decimal] = defaultdict(lambda: Decimal("0"))
    for line in inv.lines:
        by_rate[Decimal(line.vat_rate)] += Decimal(line.net_amount)
    expected_vat = sum(((base * rate / 100).quantize(CENT) for rate, base in by_rate.items()), Decimal("0"))
    if inv.lines and not _close(expected_vat, inv.vat_total):
        add(
            "VAT_MISCALCULATED",
            Severity.ERROR,
            f"VAT should be {expected_vat} for the stated rates, invoice charges {inv.vat_total}",
            "vat_total",
        )
    if not _close(Decimal(inv.net_total) + Decimal(inv.vat_total), inv.gross_total):
        add("GROSS_TOTAL_MISMATCH", Severity.ERROR, "Net + VAT does not equal gross total", "gross_total")

    # ---- payment
    if inv.iban and not iban_is_valid(inv.iban):
        add("INVALID_IBAN", Severity.ERROR, "IBAN fails the ISO 13616 mod-97 checksum", "iban")
    if not inv.iban:
        add("MISSING_IBAN", Severity.WARNING, "No payee IBAN on invoice; vendor master data will be used", "iban")

    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == Severity.ERROR for i in issues)
