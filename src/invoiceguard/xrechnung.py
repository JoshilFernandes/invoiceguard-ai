"""EN 16931 / XRechnung (UBL 2.1 syntax) serialisation and parsing.

`to_xrechnung_ubl` turns an approved `Invoice` into an XRechnung 3.0 UBL
document so that a scanned/PDF supplier invoice leaves the pipeline as a
structured e-invoice. `parse_xrechnung` reads incoming UBL (and the basic
fields of UN/CEFACT CII, the syntax used inside ZUGFeRD/Factur-X), so native
e-invoices skip LLM extraction entirely.

This produces structurally valid documents for the core business terms; for
production use, run the official KoSIT validator on the output.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from decimal import Decimal

from .models import Invoice, LineItem, Party

UBL_NS = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CII_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
CII_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
CII_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"

CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

ET.register_namespace("", UBL_NS)
ET.register_namespace("cac", CAC)
ET.register_namespace("cbc", CBC)


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _money(v: Decimal) -> str:
    return f"{Decimal(v).quantize(Decimal('0.01'))}"


def _sub(parent: ET.Element, ns: str, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, _q(ns, tag), attrs)
    if text is not None:
        el.text = text
    return el


def _party(parent: ET.Element, wrapper: str, p: Party) -> None:
    outer = _sub(parent, CAC, wrapper)
    party = _sub(outer, CAC, "Party")
    addr = _sub(party, CAC, "PostalAddress")
    if p.street:
        _sub(addr, CBC, "StreetName", p.street)
    if p.city:
        _sub(addr, CBC, "CityName", p.city)
    if p.postal_code:
        _sub(addr, CBC, "PostalZone", p.postal_code)
    country = _sub(addr, CAC, "Country")
    _sub(country, CBC, "IdentificationCode", p.country_code)
    if p.vat_id:
        tax = _sub(party, CAC, "PartyTaxScheme")
        _sub(tax, CBC, "CompanyID", p.vat_id)
        scheme = _sub(tax, CAC, "TaxScheme")
        _sub(scheme, CBC, "ID", "VAT")
    legal = _sub(party, CAC, "PartyLegalEntity")
    _sub(legal, CBC, "RegistrationName", p.name)


def to_xrechnung_ubl(inv: Invoice) -> str:
    root = ET.Element(_q(UBL_NS, "Invoice"))
    _sub(root, CBC, "CustomizationID", CUSTOMIZATION_ID)
    _sub(root, CBC, "ProfileID", PROFILE_ID)
    _sub(root, CBC, "ID", inv.invoice_number)
    _sub(root, CBC, "IssueDate", inv.issue_date.isoformat())
    if inv.due_date:
        _sub(root, CBC, "DueDate", inv.due_date.isoformat())
    _sub(root, CBC, "InvoiceTypeCode", "381" if inv.document_type.value == "credit_note" else "380")
    _sub(root, CBC, "DocumentCurrencyCode", inv.currency)
    # BT-10 is mandatory in XRechnung; fall back to the PO number.
    _sub(root, CBC, "BuyerReference", inv.buyer_reference or inv.purchase_order or "n/a")
    if inv.purchase_order:
        ref = _sub(root, CAC, "OrderReference")
        _sub(ref, CBC, "ID", inv.purchase_order)

    _party(root, "AccountingSupplierParty", inv.seller)
    _party(root, "AccountingCustomerParty", inv.buyer)

    if inv.iban:
        pm = _sub(root, CAC, "PaymentMeans")
        _sub(pm, CBC, "PaymentMeansCode", "58")  # SEPA credit transfer
        acct = _sub(pm, CAC, "PayeeFinancialAccount")
        _sub(acct, CBC, "ID", inv.iban.replace(" ", ""))
    if inv.payment_terms:
        terms = _sub(root, CAC, "PaymentTerms")
        _sub(terms, CBC, "Note", inv.payment_terms)

    # VAT breakdown per rate (BG-23)
    by_rate: dict[Decimal, Decimal] = defaultdict(lambda: Decimal("0"))
    for line in inv.lines:
        by_rate[Decimal(line.vat_rate)] += Decimal(line.net_amount)
    tax_total = _sub(root, CAC, "TaxTotal")
    _sub(tax_total, CBC, "TaxAmount", _money(inv.vat_total), currencyID=inv.currency)
    for rate, base in sorted(by_rate.items()):
        sub = _sub(tax_total, CAC, "TaxSubtotal")
        _sub(sub, CBC, "TaxableAmount", _money(base), currencyID=inv.currency)
        _sub(sub, CBC, "TaxAmount", _money(base * rate / 100), currencyID=inv.currency)
        cat = _sub(sub, CAC, "TaxCategory")
        _sub(cat, CBC, "ID", "S" if rate > 0 else "Z")
        _sub(cat, CBC, "Percent", f"{rate.normalize():f}")
        scheme = _sub(cat, CAC, "TaxScheme")
        _sub(scheme, CBC, "ID", "VAT")

    lmt = _sub(root, CAC, "LegalMonetaryTotal")
    _sub(lmt, CBC, "LineExtensionAmount", _money(inv.net_total), currencyID=inv.currency)
    _sub(lmt, CBC, "TaxExclusiveAmount", _money(inv.net_total), currencyID=inv.currency)
    _sub(lmt, CBC, "TaxInclusiveAmount", _money(inv.gross_total), currencyID=inv.currency)
    _sub(lmt, CBC, "PayableAmount", _money(inv.gross_total), currencyID=inv.currency)

    for i, line in enumerate(inv.lines, start=1):
        il = _sub(root, CAC, "InvoiceLine")
        _sub(il, CBC, "ID", str(i))
        _sub(il, CBC, "InvoicedQuantity", f"{Decimal(line.quantity).normalize():f}", unitCode="C62")
        _sub(il, CBC, "LineExtensionAmount", _money(line.net_amount), currencyID=inv.currency)
        item = _sub(il, CAC, "Item")
        _sub(item, CBC, "Name", line.description)
        if line.sku:
            sid = _sub(item, CAC, "SellersItemIdentification")
            _sub(sid, CBC, "ID", line.sku)
        cat = _sub(item, CAC, "ClassifiedTaxCategory")
        _sub(cat, CBC, "ID", "S" if line.vat_rate > 0 else "Z")
        _sub(cat, CBC, "Percent", f"{Decimal(line.vat_rate).normalize():f}")
        scheme = _sub(cat, CAC, "TaxScheme")
        _sub(scheme, CBC, "ID", "VAT")
        price = _sub(il, CAC, "Price")
        _sub(price, CBC, "PriceAmount", _money(line.unit_price), currencyID=inv.currency)

    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------- parsing


def _t(el: ET.Element | None, path: str, ns: dict[str, str]) -> str | None:
    if el is None:
        return None
    found = el.find(path, ns)
    return found.text.strip() if found is not None and found.text else None


def _parse_ubl_party(el: ET.Element | None, ns: dict[str, str]) -> Party:
    return Party(
        name=_t(el, ".//cac:PartyLegalEntity/cbc:RegistrationName", ns) or "unknown",
        vat_id=_t(el, ".//cac:PartyTaxScheme/cbc:CompanyID", ns),
        street=_t(el, ".//cac:PostalAddress/cbc:StreetName", ns),
        city=_t(el, ".//cac:PostalAddress/cbc:CityName", ns),
        postal_code=_t(el, ".//cac:PostalAddress/cbc:PostalZone", ns),
        country_code=_t(el, ".//cac:Country/cbc:IdentificationCode", ns) or "DE",
    )


def _parse_ubl(root: ET.Element) -> Invoice:
    ns = {"cac": CAC, "cbc": CBC}
    lines = []
    for il in root.findall("cac:InvoiceLine", ns):
        qty = Decimal(_t(il, "cbc:InvoicedQuantity", ns) or "0")
        price = Decimal(_t(il, "cac:Price/cbc:PriceAmount", ns) or "0")
        lines.append(
            LineItem(
                description=_t(il, "cac:Item/cbc:Name", ns) or "",
                sku=_t(il, "cac:Item/cac:SellersItemIdentification/cbc:ID", ns),
                quantity=qty,
                unit_price=price,
                vat_rate=Decimal(_t(il, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent", ns) or "0"),
                net_amount=Decimal(_t(il, "cbc:LineExtensionAmount", ns) or "0"),
            )
        )
    due = _t(root, "cbc:DueDate", ns)
    return Invoice(
        invoice_number=_t(root, "cbc:ID", ns) or "",
        issue_date=date.fromisoformat(_t(root, "cbc:IssueDate", ns) or "1970-01-01"),
        due_date=date.fromisoformat(due) if due else None,
        currency=_t(root, "cbc:DocumentCurrencyCode", ns) or "EUR",
        buyer_reference=_t(root, "cbc:BuyerReference", ns),
        purchase_order=_t(root, "cac:OrderReference/cbc:ID", ns),
        seller=_parse_ubl_party(root.find("cac:AccountingSupplierParty", ns), ns),
        buyer=_parse_ubl_party(root.find("cac:AccountingCustomerParty", ns), ns),
        lines=lines,
        net_total=Decimal(_t(root, "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount", ns) or "0"),
        vat_total=Decimal(_t(root, "cac:TaxTotal/cbc:TaxAmount", ns) or "0"),
        gross_total=Decimal(_t(root, "cac:LegalMonetaryTotal/cbc:PayableAmount", ns) or "0"),
        iban=_t(root, "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID", ns),
        payment_terms=_t(root, "cac:PaymentTerms/cbc:Note", ns),
    )


def _parse_cii(root: ET.Element) -> Invoice:
    ns = {"rsm": CII_RSM, "ram": CII_RAM, "udt": CII_UDT}

    def d(s: str | None) -> date:
        s = s or "19700101"
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    tx = root.find("rsm:SupplyChainTradeTransaction", ns)
    agreement = tx.find("ram:ApplicableHeaderTradeAgreement", ns) if tx is not None else None
    settlement = tx.find("ram:ApplicableHeaderTradeSettlement", ns) if tx is not None else None

    def party(el: ET.Element | None) -> Party:
        return Party(
            name=_t(el, "ram:Name", ns) or "unknown",
            vat_id=_t(el, "ram:SpecifiedTaxRegistration/ram:ID", ns),
            street=_t(el, "ram:PostalTradeAddress/ram:LineOne", ns),
            city=_t(el, "ram:PostalTradeAddress/ram:CityName", ns),
            postal_code=_t(el, "ram:PostalTradeAddress/ram:PostcodeCode", ns),
            country_code=_t(el, "ram:PostalTradeAddress/ram:CountryID", ns) or "DE",
        )

    lines = []
    for li in tx.findall("ram:IncludedSupplyChainTradeLineItem", ns) if tx is not None else []:
        lines.append(
            LineItem(
                description=_t(li, "ram:SpecifiedTradeProduct/ram:Name", ns) or "",
                sku=_t(li, "ram:SpecifiedTradeProduct/ram:SellerAssignedID", ns),
                quantity=Decimal(_t(li, ".//ram:BilledQuantity", ns) or "0"),
                unit_price=Decimal(_t(li, ".//ram:NetPriceProductTradePrice/ram:ChargeAmount", ns) or "0"),
                vat_rate=Decimal(_t(li, ".//ram:RateApplicablePercent", ns) or "0"),
                net_amount=Decimal(_t(li, ".//ram:LineTotalAmount", ns) or "0"),
            )
        )
    summ = "ram:SpecifiedTradeSettlementHeaderMonetarySummation"
    return Invoice(
        invoice_number=_t(root, "rsm:ExchangedDocument/ram:ID", ns) or "",
        issue_date=d(_t(root, "rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString", ns)),
        currency=_t(settlement, "ram:InvoiceCurrencyCode", ns) or "EUR",
        buyer_reference=_t(agreement, "ram:BuyerReference", ns),
        purchase_order=_t(agreement, "ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID", ns),
        seller=party(agreement.find("ram:SellerTradeParty", ns) if agreement is not None else None),
        buyer=party(agreement.find("ram:BuyerTradeParty", ns) if agreement is not None else None),
        lines=lines,
        net_total=Decimal(_t(settlement, f"{summ}/ram:TaxBasisTotalAmount", ns) or "0"),
        vat_total=Decimal(_t(settlement, f"{summ}/ram:TaxTotalAmount", ns) or "0"),
        gross_total=Decimal(_t(settlement, f"{summ}/ram:DuePayableAmount", ns) or "0"),
        iban=_t(settlement, ".//ram:PayeePartyCreditorFinancialAccount/ram:IBANID", ns),
    )


def is_e_invoice(xml_text: str) -> bool:
    head = xml_text[:2000]
    return UBL_NS in head or CII_RSM in head


def parse_xrechnung(xml_text: str) -> Invoice:
    """Parse an incoming UBL or CII e-invoice into the internal model."""
    root = ET.fromstring(xml_text.encode("utf-8"))
    if root.tag == _q(UBL_NS, "Invoice"):
        return _parse_ubl(root)
    if root.tag == _q(CII_RSM, "CrossIndustryInvoice"):
        return _parse_cii(root)
    raise ValueError(f"Unsupported XML root element: {root.tag}")
