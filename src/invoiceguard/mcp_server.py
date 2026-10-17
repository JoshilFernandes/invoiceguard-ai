"""MCP server: exposes InvoiceGuard to any MCP client (Claude Desktop, Cursor, IDE agents).

    python -m invoiceguard.mcp_server                 # stdio
    python -m invoiceguard.mcp_server --http 8765     # streamable HTTP

An AP clerk can then ask their assistant "what's waiting for my approval?"
and approve or reject from chat; the decision resumes the paused LangGraph
run and lands in the same tamper-evident audit trail as UI decisions.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from fastmcp import FastMCP

from .models import Decision
from .service import get_app

mcp = FastMCP(
    "invoiceguard",
    instructions=(
        "Accounts-payable tools. Never approve an invoice unless the human user explicitly tells you to; always show risk signals and issues before asking."
    ),
)


@mcp.tool
def process_invoice(filename: str, content_base64: str | None = None, path: str | None = None) -> dict:
    """Run a PDF, XRechnung/ZUGFeRD XML or .eml invoice through the pipeline.

    Pass either the file content as base64 or a path readable by the server.
    """
    if content_base64:
        data = base64.b64decode(content_base64)
    elif path:
        data = Path(path).expanduser().read_bytes()
    else:
        raise ValueError("Provide content_base64 or path")
    return get_app().submit(filename, data)


@mcp.tool
def list_pending_reviews() -> list[dict]:
    """Invoices paused for human approval, newest first."""
    return get_app().list_cases("awaiting_review")


@mcp.tool
def get_invoice_case(case_id: str) -> dict:
    """Full detail of a case: extracted fields, issues, 3-way match, risk signals, decision."""
    case = get_app().get(case_id)
    if not case:
        raise ValueError(f"Unknown case {case_id}")
    return case


@mcp.tool
def decide_invoice(case_id: str, approve: bool, reviewer: str, comment: str | None = None) -> dict:
    """Approve or reject an invoice that is awaiting review. Requires the user's explicit instruction."""
    return get_app().resume(case_id, Decision(approve=approve, reviewer=reviewer, comment=comment))


@mcp.tool
def vendor_master_data(query: str | None = None) -> list[dict]:
    """Look up vendors (name, VAT ID, city, payment terms). IBANs are masked."""
    from .guardrails import mask_iban

    rows = get_app().erp.list_vendors()
    if query:
        q = query.lower()
        rows = [r for r in rows if q in r["name"].lower() or q in (r["vat_id"] or "").lower()]
    return [{**r, "iban": mask_iban(r["iban"])} for r in rows]


@mcp.tool
def ap_dashboard() -> dict:
    """Throughput KPIs plus audit-trail integrity check."""
    app = get_app()
    return {**app.stats(), "audit": app.audit.verify()}


def main() -> None:
    if "--http" in sys.argv:
        port = int(sys.argv[sys.argv.index("--http") + 1]) if len(sys.argv) > sys.argv.index("--http") + 1 else 8765
        mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
