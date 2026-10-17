"""Domain models.

Field names follow the EN 16931 semantic model where it makes sense; the
matching Business Term (BT-xx) is noted in each description so the mapping
to XRechnung / ZUGFeRD stays traceable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class DocumentType(StrEnum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DELIVERY_NOTE = "delivery_note"
    REMINDER = "payment_reminder"
    OTHER = "other"


class Party(BaseModel):
    name: str = Field(description="Legal name (BT-27 seller / BT-44 buyer)")
    vat_id: str | None = Field(None, description="VAT identifier, e.g. DE123456789 (BT-31 / BT-48)")
    street: str | None = Field(None, description="Address line (BT-35 / BT-50)")
    city: str | None = Field(None, description="City (BT-37 / BT-52)")
    postal_code: str | None = Field(None, description="Post code (BT-38 / BT-53)")
    country_code: str = Field("DE", description="ISO 3166-1 alpha-2 (BT-40 / BT-55)")


class LineItem(BaseModel):
    description: str = Field(description="Item name (BT-153)")
    quantity: Decimal = Field(description="Invoiced quantity (BT-129)")
    unit_price: Decimal = Field(description="Net unit price (BT-146)")
    vat_rate: Decimal = Field(description="VAT rate in percent, e.g. 19 or 7 (BT-152)")
    net_amount: Decimal = Field(description="Line net amount = quantity x unit price (BT-131)")
    sku: str | None = Field(None, description="Seller item id (BT-155), used for PO matching")


class Invoice(BaseModel):
    """Structured invoice as extracted by the agent."""

    document_type: DocumentType = DocumentType.INVOICE
    invoice_number: str = Field(description="Invoice number (BT-1)")
    issue_date: date = Field(description="Issue date (BT-2)")
    due_date: date | None = Field(None, description="Payment due date (BT-9)")
    currency: str = Field("EUR", description="ISO 4217 currency (BT-5)")
    buyer_reference: str | None = Field(None, description="Leitweg-ID / buyer reference (BT-10)")
    purchase_order: str | None = Field(None, description="Purchase order reference (BT-13)")
    seller: Party
    buyer: Party
    lines: list[LineItem]
    net_total: Decimal = Field(description="Sum of line net amounts (BT-106/BT-109)")
    vat_total: Decimal = Field(description="Total VAT amount (BT-110)")
    gross_total: Decimal = Field(description="Amount due incl. VAT (BT-112)")
    iban: str | None = Field(None, description="Payee IBAN (BT-84)")
    payment_terms: str | None = Field(None, description="Payment terms text (BT-20)")


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Issue(BaseModel):
    code: str
    severity: Severity
    message: str
    field: str | None = None
    # "extraction" issues may be fixed by re-reading the document;
    # "business" issues are real problems with the invoice itself.
    category: str = "business"


class RiskSignal(BaseModel):
    code: str
    weight: int
    detail: str


class Route(StrEnum):
    AUTO_APPROVE = "auto_approve"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"


class Decision(BaseModel):
    approve: bool
    reviewer: str = "reviewer"
    comment: str | None = None
    corrections: dict | None = None
