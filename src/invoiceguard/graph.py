"""The InvoiceGuard agent as a LangGraph state machine.

    ingest → guard → classify ─┬─(not an invoice)──────────────────────► reject
                               └► extract ⇄ validate (self-repair loop)
                                     → match → risk → decide ─┬► auto_approve ─► post
                                                              ├► human_review ─┬► post
                                                              │   (interrupt)  └► reject
                                                              └► reject

State is checkpointed (SQLite), so a case waiting for a human survives
restarts and can be resumed hours later from the API, the UI or MCP.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .audit import AuditLog
from .config import Settings
from .erp import ERP
from .extraction import Extractor
from .guardrails import mask_iban, scan_for_injection
from .ingest import IngestedDocument, ingest
from .matching import three_way_match
from .models import Decision, Invoice, Issue, Route, Severity
from .risk import score_risk
from .validation import validate
from .xrechnung import parse_xrechnung, to_xrechnung_ubl


class CaseState(TypedDict, total=False):
    case_id: str
    filename: str
    content_b64: str
    doc: dict
    doc_type: str
    classification: dict
    source: str  # "e-invoice" | extractor name
    invoice: dict
    attempts: int
    security_issues: list[dict]
    validation_issues: list[dict]
    match_issues: list[dict]
    match_rows: list[dict]
    vendor: dict | None
    risk_score: int
    risk_signals: list[dict]
    route: str
    reasons: list[str]
    decision: dict
    status: str
    xrechnung_xml: str


@dataclass
class Services:
    settings: Settings
    erp: ERP
    audit: AuditLog
    extractor: Extractor


def _doc(state: CaseState) -> IngestedDocument:
    return IngestedDocument(**state["doc"])


def _issues(raw: list[dict] | None) -> list[Issue]:
    return [Issue(**i) for i in raw or []]


def build_graph(svc: Services, checkpointer=None):
    s = svc.settings

    # ------------------------------------------------------------ nodes
    def ingest_node(state: CaseState) -> CaseState:
        doc = ingest(state["filename"], base64.b64decode(state["content_b64"]))
        svc.audit.append(state["case_id"], "ingested", channel=doc.channel, sha256=doc.sha256, attachments=doc.attachments)
        return {"doc": doc.__dict__, "attempts": 0, "status": "processing", "content_b64": ""}

    def guard_node(state: CaseState) -> CaseState:
        found = scan_for_injection(_doc(state).text)
        if found:
            svc.audit.append(state["case_id"], "guardrail_triggered", codes=[i.code for i in found])
        return {"security_issues": [i.model_dump(mode="json") for i in found]}

    def classify_node(state: CaseState) -> CaseState:
        doc = _doc(state)
        if doc.xml:
            result = {"document_type": "invoice", "confidence": 1.0, "reason": "structured EN 16931 e-invoice"}
        else:
            result = svc.extractor.classify(doc.text).model_dump()
        svc.audit.append(state["case_id"], "classified", **result)
        return {"doc_type": result["document_type"], "classification": result}

    def extract_node(state: CaseState) -> CaseState:
        doc = _doc(state)
        attempts = state.get("attempts", 0) + 1
        if doc.xml:
            inv, source = parse_xrechnung(doc.xml), "e-invoice"
        else:
            feedback = [i for i in _issues(state.get("validation_issues")) if i.category == "extraction"]
            inv, source = svc.extractor.extract(doc.text, feedback or None), svc.extractor.name
        svc.audit.append(
            state["case_id"],
            "extracted" if attempts == 1 else "re_extracted",
            source=source,
            attempt=attempts,
            invoice_number=inv.invoice_number,
            seller=inv.seller.name,
            gross_total=str(inv.gross_total),
        )
        return {"invoice": inv.model_dump(mode="json"), "source": source, "attempts": attempts}

    def validate_node(state: CaseState) -> CaseState:
        inv = Invoice(**state["invoice"])
        issues = validate(inv, buyer_vat_id=s.buyer_vat_id)
        svc.audit.append(state["case_id"], "validated", codes=[i.code for i in issues])
        return {"validation_issues": [i.model_dump(mode="json") for i in issues]}

    def match_node(state: CaseState) -> CaseState:
        inv = Invoice(**state["invoice"])
        vendor = svc.erp.find_vendor(inv.seller.vat_id, inv.seller.name)
        issues, rows = three_way_match(inv, svc.erp, vendor, s.price_tolerance_pct)
        svc.audit.append(state["case_id"], "matched", vendor_id=vendor and vendor["vendor_id"], codes=[i.code for i in issues])
        return {"vendor": vendor, "match_issues": [i.model_dump(mode="json") for i in issues], "match_rows": rows}

    def risk_node(state: CaseState) -> CaseState:
        inv = Invoice(**state["invoice"])
        score, signals = score_risk(inv, _doc(state), state.get("vendor"), svc.erp, _issues(state.get("security_issues")))
        svc.audit.append(state["case_id"], "risk_scored", score=score, signals=[x.code for x in signals])
        return {"risk_score": score, "risk_signals": [x.model_dump() for x in signals]}

    def decide_node(state: CaseState) -> CaseState:
        route, reasons = decide(state, s)
        svc.audit.append(state["case_id"], "routed", route=route.value, reasons=reasons)
        return {"route": route.value, "reasons": reasons}

    def human_review_node(state: CaseState) -> CaseState:
        inv = state["invoice"]
        # Pauses the graph; state is persisted until a reviewer answers.
        answer = interrupt(
            {
                "case_id": state["case_id"],
                "question": "Approve this invoice for payment?",
                "invoice_number": inv["invoice_number"],
                "seller": inv["seller"]["name"],
                "gross_total": inv["gross_total"],
                "currency": inv["currency"],
                "iban": mask_iban(inv.get("iban")),
                "risk_score": state.get("risk_score"),
                "reasons": state.get("reasons", []),
            }
        )
        decision = Decision(**answer)
        svc.audit.append(state["case_id"], "human_decision", actor=decision.reviewer, approve=decision.approve, comment=decision.comment)
        return {"decision": decision.model_dump()}

    def post_node(state: CaseState) -> CaseState:
        inv = Invoice(**state["invoice"])
        vendor = state.get("vendor")
        if vendor:
            svc.erp.post_invoice(vendor["vendor_id"], inv.invoice_number, inv.issue_date.isoformat(), Decimal(inv.gross_total))
        xml = to_xrechnung_ubl(inv)
        svc.audit.append(state["case_id"], "posted", vendor_id=vendor and vendor["vendor_id"], gross_total=str(inv.gross_total))
        return {"status": "posted", "xrechnung_xml": xml}

    def reject_node(state: CaseState) -> CaseState:
        reasons = state.get("reasons") or [f"Document is a {state.get('doc_type')}, not an invoice"]
        if state.get("decision") and not state["decision"]["approve"]:
            reasons = reasons + [f"Rejected by {state['decision']['reviewer']}: {state['decision'].get('comment') or 'no comment'}"]
        svc.audit.append(state["case_id"], "rejected", reasons=reasons)
        return {"status": "rejected", "reasons": reasons, "route": state.get("route") or Route.REJECT.value}

    # ------------------------------------------------------------ edges
    def after_classify(state: CaseState) -> str:
        return "extract" if state["doc_type"] in ("invoice", "credit_note") else "reject"

    def after_validate(state: CaseState) -> str:
        needs_repair = any(i.severity == Severity.ERROR and i.category == "extraction" for i in _issues(state.get("validation_issues")))
        can_repair = state.get("source") not in ("e-invoice", "rules") and state.get("attempts", 0) <= s.max_repair_attempts
        return "extract" if needs_repair and can_repair else "match"

    def after_decide(state: CaseState) -> str:
        return {"auto_approve": "post", "human_review": "human_review", "reject": "reject"}[state["route"]]

    def after_review(state: CaseState) -> str:
        return "post" if state["decision"]["approve"] else "reject"

    g = StateGraph(CaseState)
    for name, fn in [
        ("ingest", ingest_node),
        ("guard", guard_node),
        ("classify", classify_node),
        ("extract", extract_node),
        ("validate", validate_node),
        ("match", match_node),
        ("risk", risk_node),
        ("decide", decide_node),
        ("human_review", human_review_node),
        ("post", post_node),
        ("reject", reject_node),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "guard")
    g.add_edge("guard", "classify")
    g.add_conditional_edges("classify", after_classify, ["extract", "reject"])
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", after_validate, ["extract", "match"])
    g.add_edge("match", "risk")
    g.add_edge("risk", "decide")
    g.add_conditional_edges("decide", after_decide, ["post", "human_review", "reject"])
    g.add_conditional_edges("human_review", after_review, ["post", "reject"])
    g.add_edge("post", END)
    g.add_edge("reject", END)
    return g.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------- policy

HARD_REJECT = {"DUPLICATE_INVOICE", "WRONG_BUYER"}


def decide(state: CaseState, s: Settings) -> tuple[Route, list[str]]:
    """Deterministic approval policy. The LLM never makes this call."""
    issues = _issues(state.get("security_issues")) + _issues(state.get("validation_issues")) + _issues(state.get("match_issues"))
    signals = state.get("risk_signals", [])
    codes = {i.code for i in issues} | {x["code"] for x in signals}
    gross = Decimal(state["invoice"]["gross_total"])

    hard = sorted(codes & HARD_REJECT)
    if hard:
        msgs = [i.message for i in issues if i.code in hard] + [x["detail"] for x in signals if x["code"] in hard]
        return Route.REJECT, msgs

    reasons = [f"[{i.code}] {i.message}" for i in issues if i.severity == Severity.ERROR]
    risk = state.get("risk_score", 0)
    if risk >= s.risk_review_threshold:
        reasons.append(f"Risk score {risk} ≥ {s.risk_review_threshold}: " + "; ".join(x["code"] for x in signals))
    if gross > s.auto_approve_limit:
        reasons.append(f"Amount {gross} exceeds auto-approval limit {s.auto_approve_limit}")
    if "NO_PO" in codes:
        reasons.append("No purchase order to match against")
    if not state.get("vendor"):
        reasons.append("Vendor not in master data")

    return (Route.HUMAN_REVIEW, reasons) if reasons else (Route.AUTO_APPROVE, ["All checks passed; within auto-approval policy"])


def summarize(values: dict[str, Any]) -> dict[str, Any]:
    """Compact, UI/API-friendly view of a case state."""
    inv = values.get("invoice") or {}
    return {
        "case_id": values.get("case_id"),
        "filename": values.get("filename"),
        "status": values.get("status"),
        "route": values.get("route"),
        "doc_type": values.get("doc_type"),
        "source": values.get("source"),
        "attempts": values.get("attempts"),
        "invoice": inv,
        "vendor": values.get("vendor"),
        "risk_score": values.get("risk_score"),
        "risk_signals": values.get("risk_signals", []),
        "issues": (values.get("security_issues") or []) + (values.get("validation_issues") or []) + (values.get("match_issues") or []),
        "match_rows": values.get("match_rows", []),
        "reasons": values.get("reasons", []),
        "decision": values.get("decision"),
        "has_xrechnung": bool(values.get("xrechnung_xml")),
    }
