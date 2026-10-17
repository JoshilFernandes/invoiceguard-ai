"""Three-way match: invoice line ↔ purchase order line ↔ goods receipt."""

from __future__ import annotations

from decimal import Decimal
from difflib import SequenceMatcher

from .erp import ERP
from .models import Invoice, Issue, Severity


def _best_po_line(line, po_lines: list[dict]) -> dict | None:
    if line.sku:
        for p in po_lines:
            if p["sku"] == line.sku:
                return p
    scored = [(SequenceMatcher(None, line.description.lower(), p["description"].lower()).ratio(), p) for p in po_lines]
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] >= 0.6 else None


def three_way_match(inv: Invoice, erp: ERP, vendor: dict | None, tolerance_pct: Decimal) -> tuple[list[Issue], list[dict]]:
    issues: list[Issue] = []
    rows: list[dict] = []

    if not inv.purchase_order:
        issues.append(Issue(code="NO_PO", severity=Severity.WARNING, message="Invoice references no purchase order", field="purchase_order"))
        return issues, rows

    po_lines = erp.get_po(inv.purchase_order)
    if not po_lines:
        issues.append(Issue(code="PO_NOT_FOUND", severity=Severity.ERROR, message=f"PO {inv.purchase_order} does not exist", field="purchase_order"))
        return issues, rows

    if vendor and po_lines[0]["vendor_id"] != vendor["vendor_id"]:
        issues.append(Issue(code="PO_VENDOR_MISMATCH", severity=Severity.ERROR, message=f"PO {inv.purchase_order} belongs to another vendor"))

    for idx, line in enumerate(inv.lines):
        po_line = _best_po_line(line, po_lines)
        row = {"line": idx + 1, "description": line.description, "invoiced_qty": str(line.quantity), "invoiced_price": str(line.unit_price)}
        if not po_line:
            issues.append(
                Issue(
                    code="LINE_NOT_ON_PO",
                    severity=Severity.ERROR,
                    message=f"Line {idx + 1} '{line.description}' is not on PO {inv.purchase_order}",
                    field=f"lines[{idx}]",
                )
            )
            row["status"] = "not on PO"
            rows.append(row)
            continue

        received = erp.received_quantity(inv.purchase_order, po_line["sku"])
        row.update(po_qty=str(po_line["quantity"]), po_price=str(po_line["unit_price"]), received_qty=str(received))
        status = "matched"

        if Decimal(line.quantity) > received:
            status = "quantity exceeds goods receipt"
            issues.append(
                Issue(
                    code="QTY_EXCEEDS_RECEIPT",
                    severity=Severity.ERROR,
                    message=f"Line {idx + 1}: invoiced {line.quantity}, only {received} received",
                    field=f"lines[{idx}].quantity",
                )
            )
        po_price = po_line["unit_price"]
        if po_price > 0:
            variance = (Decimal(line.unit_price) - po_price) / po_price * 100
            row["price_variance_pct"] = f"{variance.quantize(Decimal('0.1'))}"
            if variance > tolerance_pct:
                status = "price above PO"
                issues.append(
                    Issue(
                        code="PRICE_VARIANCE",
                        severity=Severity.ERROR,
                        message=f"Line {idx + 1}: unit price {line.unit_price} is {variance.quantize(Decimal('0.1'))}% above PO price {po_price} (tolerance {tolerance_pct}%)",
                        field=f"lines[{idx}].unit_price",
                    )
                )
        row["status"] = status
        rows.append(row)

    return issues, rows
