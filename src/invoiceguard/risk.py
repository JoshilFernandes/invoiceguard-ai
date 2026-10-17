"""Explainable fraud / risk scoring.

Business-email-compromise ("CEO fraud", fake bank-detail changes) is the most
common AP fraud pattern. Every signal here is a named, weighted rule, so a
reviewer sees exactly *why* an invoice scored high — no black-box model.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .erp import ERP
from .guardrails import mask_iban, urgency_hits
from .ingest import IngestedDocument
from .models import Invoice, Issue, RiskSignal


def _domain(addr: str) -> str:
    addr = addr.strip().strip(">").lower()
    return addr.rsplit("@", 1)[-1] if "@" in addr else ""


def score_risk(
    inv: Invoice,
    doc: IngestedDocument,
    vendor: dict | None,
    erp: ERP,
    security_issues: list[Issue],
    today: date | None = None,
) -> tuple[int, list[RiskSignal]]:
    today = today or date.today()
    signals: list[RiskSignal] = []

    if security_issues:
        signals.append(RiskSignal(code="PROMPT_INJECTION", weight=50, detail="Document tries to instruct the AI; treated as hostile"))

    if vendor is None:
        signals.append(RiskSignal(code="UNKNOWN_VENDOR", weight=40, detail=f"'{inv.seller.name}' / {inv.seller.vat_id} is not in vendor master data"))
    else:
        master_iban = (vendor.get("iban") or "").replace(" ", "")
        inv_iban = (inv.iban or "").replace(" ", "")
        if inv_iban and master_iban and inv_iban != master_iban:
            signals.append(
                RiskSignal(
                    code="BANK_DETAILS_CHANGED",
                    weight=60,
                    detail=f"Invoice IBAN {mask_iban(inv_iban)} differs from master data {mask_iban(master_iban)} — verify by phone with a known contact",
                )
            )
            if inv_iban[:2] != master_iban[:2]:
                signals.append(RiskSignal(code="IBAN_COUNTRY_CHANGED", weight=15, detail=f"Payment country changed {master_iban[:2]} → {inv_iban[:2]}"))
        created = date.fromisoformat(vendor["created"])
        if (today - created).days < 60:
            signals.append(RiskSignal(code="NEW_VENDOR", weight=15, detail=f"Vendor created {created}, less than 60 days ago"))

        avg, std, n = erp.vendor_amount_stats(vendor["vendor_id"])
        if n >= 3 and std > 0 and Decimal(inv.gross_total) > avg + 3 * std:
            signals.append(
                RiskSignal(code="AMOUNT_OUTLIER", weight=25, detail=f"Gross {inv.gross_total} is far above this vendor's usual {avg} ± {std} (n={n})")
            )
        if erp.is_duplicate(vendor["vendor_id"], inv.invoice_number):
            signals.append(RiskSignal(code="DUPLICATE_INVOICE", weight=80, detail=f"Invoice {inv.invoice_number} was already booked for this vendor"))

    hits = urgency_hits(doc.text)
    if hits:
        signals.append(
            RiskSignal(code="PRESSURE_LANGUAGE", weight=min(20, 10 * len(hits)), detail=f"Urgency / bank-change wording detected ({len(hits)} cues)")
        )

    if doc.email_meta:
        sender, reply_to = _domain(doc.email_meta.get("from", "")), _domain(doc.email_meta.get("reply_to", ""))
        if reply_to and sender and reply_to != sender:
            signals.append(RiskSignal(code="REPLY_TO_MISMATCH", weight=20, detail=f"Reply-To domain {reply_to} differs from sender {sender}"))

    gross = Decimal(inv.gross_total)
    if gross >= 1000 and gross % 1000 == 0:
        signals.append(RiskSignal(code="ROUND_AMOUNT", weight=5, detail="Suspiciously round gross amount"))

    return min(100, sum(s.weight for s in signals)), signals
