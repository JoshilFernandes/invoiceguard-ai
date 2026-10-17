from datetime import date
from decimal import Decimal

import pytest

from invoiceguard.extraction import classify_rules
from invoiceguard.graph import Services, build_graph
from invoiceguard.models import Decision, Invoice, LineItem, Party
from invoiceguard.service import InvoiceGuard

from .conftest import sample


def test_auto_approve_posts_to_ledger(guard):
    case = guard.submit("01.pdf", sample("01_clean_office_supplies.pdf"))
    assert case["status"] == "posted" and case["route"] == "auto_approve"
    assert guard.erp.is_duplicate("V001", "BS-2026-1009")


def test_human_in_the_loop_approve(guard):
    case = guard.submit("04.eml", sample("04_bank_change_fraud.eml"))
    assert case["status"] == "awaiting_review"
    assert {"BANK_DETAILS_CHANGED", "REPLY_TO_MISMATCH"} <= {s["code"] for s in case["risk_signals"]}

    # Graph is paused and checkpointed — a brand-new process can resume it.
    fresh = InvoiceGuard(guard.settings)
    done = fresh.resume(case["case_id"], Decision(approve=True, reviewer="anna", comment="confirmed by phone"))
    assert done["status"] == "posted" and done["has_xrechnung"]
    assert any(e["event"] == "human_decision" and e["actor"] == "anna" for e in fresh.audit.entries(case["case_id"]))


def test_human_in_the_loop_reject_and_no_double_decision(guard):
    case = guard.submit("07.pdf", sample("07_prompt_injection.pdf"))
    done = guard.resume(case["case_id"], Decision(approve=False, reviewer="anna", comment="injection attempt"))
    assert done["status"] == "rejected"
    with pytest.raises(ValueError):
        guard.resume(case["case_id"], Decision(approve=True))


def test_duplicate_and_non_invoice_rejected(guard):
    assert guard.submit("05.pdf", sample("05_duplicate_invoice.pdf"))["status"] == "rejected"
    note = guard.submit("09.pdf", sample("09_delivery_note.pdf"))
    assert note["status"] == "rejected" and note["doc_type"] == "delivery_note"


class FlakyExtractor:
    """Misreads the net total on the first pass, then gets it right once given feedback."""

    name = "fake-llm"

    def __init__(self):
        self.calls = []

    def classify(self, text):
        return classify_rules(text)

    def extract(self, text, feedback=None):
        self.calls.append([i.code for i in feedback or []])
        net = Decimal("527.60") if feedback else Decimal("572.60")
        return Invoice(
            invoice_number="BS-2026-1099",
            issue_date=date(2026, 10, 9),
            purchase_order="PO-2026-0412",
            seller=Party(name="Bürobedarf Schulz GmbH", vat_id="DE811234567", street="Steindamm 41", city="Hamburg", postal_code="20099"),
            buyer=Party(name="Spreeufer Möbelwerk GmbH", vat_id="DE298765432"),
            lines=[
                LineItem(
                    sku="SKU-1001",
                    description="Kopierpapier",
                    quantity=Decimal("40"),
                    unit_price=Decimal("4.20"),
                    vat_rate=Decimal("19"),
                    net_amount=Decimal("168.00"),
                ),
                LineItem(
                    sku="SKU-1007",
                    description="Toner",
                    quantity=Decimal("4"),
                    unit_price=Decimal("89.90"),
                    vat_rate=Decimal("19"),
                    net_amount=Decimal("359.60"),
                ),
            ],
            net_total=net,
            vat_total=Decimal("100.24"),
            gross_total=Decimal("627.84"),
            iban="DE97200400600123456789",
        )


def test_self_repair_loop(guard):
    import base64

    flaky = FlakyExtractor()
    graph = build_graph(Services(guard.settings, guard.erp, guard.audit, flaky))
    out = graph.invoke({"case_id": "T1", "filename": "01.pdf", "content_b64": base64.b64encode(sample("01_clean_office_supplies.pdf")).decode()})
    assert out["attempts"] == 2
    assert flaky.calls[1] == ["NET_TOTAL_MISMATCH"]  # validator feedback was passed back to the extractor
    assert out["status"] == "posted"
