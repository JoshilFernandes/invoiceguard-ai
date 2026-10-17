from decimal import Decimal

from invoiceguard.ingest import ingest
from invoiceguard.xrechnung import CUSTOMIZATION_ID, is_e_invoice, parse_xrechnung, to_xrechnung_ubl

from .conftest import sample


def test_ubl_round_trip():
    original = parse_xrechnung(sample("02_xrechnung_cloud_hosting.xml").decode())
    xml = to_xrechnung_ubl(original)
    assert CUSTOMIZATION_ID in xml and is_e_invoice(xml)
    again = parse_xrechnung(xml)
    assert again.invoice_number == original.invoice_number == "CN-2026-10"
    assert again.gross_total == Decimal("1834.98")
    assert [ln.sku for ln in again.lines] == ["SKU-CL-01", "SKU-CL-07"]
    assert again.seller.vat_id == "DE245678901"


def test_cii_zugferd_parse():
    inv = parse_xrechnung(sample("10_zugferd_cii_toner.xml").decode())
    assert inv.invoice_number == "BS-2026-1016"
    assert inv.purchase_order == "PO-2026-0412"
    assert inv.vat_total == Decimal("34.16") and inv.lines[0].quantity == Decimal("2")


def test_ingest_detects_structured_xml():
    doc = ingest("x.xml", sample("02_xrechnung_cloud_hosting.xml"))
    assert doc.channel == "xml" and doc.xml


def test_pdf_to_xrechnung_export(guard):
    case = guard.submit("01.pdf", sample("01_clean_office_supplies.pdf"))
    xml = guard.xrechnung(case["case_id"])
    assert parse_xrechnung(xml).gross_total == Decimal("627.84")
