"""A small ERP stand-in (vendor master, purchase orders, goods receipts, AP ledger).

Backed by SQLite and seeded from `data/erp/*.json`. In a real deployment this
module is the adapter you swap for SAP, DATEV, Odoo, etc. — the agent only
talks to the methods below.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path
from statistics import mean, pstdev

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
  vendor_id TEXT PRIMARY KEY, name TEXT, vat_id TEXT, iban TEXT, city TEXT,
  created TEXT, payment_terms_days INTEGER);
CREATE TABLE IF NOT EXISTS purchase_orders (
  po_number TEXT, vendor_id TEXT, sku TEXT, description TEXT, quantity TEXT, unit_price TEXT);
CREATE TABLE IF NOT EXISTS goods_receipts (po_number TEXT, sku TEXT, quantity TEXT, received_on TEXT);
CREATE TABLE IF NOT EXISTS ap_ledger (
  vendor_id TEXT, invoice_number TEXT, issue_date TEXT, gross_total TEXT, source TEXT,
  UNIQUE(vendor_id, invoice_number));
"""


class ERP:
    def __init__(self, db_path: Path, seed_dir: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.seed_dir = seed_dir
        if not self.conn.execute("SELECT 1 FROM vendors LIMIT 1").fetchone():
            self.seed()

    # ------------------------------------------------------------ seeding
    def seed(self) -> None:
        with self._lock:
            for table in ("vendors", "purchase_orders", "goods_receipts", "ap_ledger"):
                self.conn.execute(f"DELETE FROM {table}")
            load = lambda name: json.loads((self.seed_dir / name).read_text(encoding="utf-8"))  # noqa: E731
            for v in load("vendors.json"):
                self.conn.execute(
                    "INSERT INTO vendors VALUES (?,?,?,?,?,?,?)",
                    (v["vendor_id"], v["name"], v["vat_id"], v["iban"], v["city"], v["created"], v["payment_terms_days"]),
                )
            for po in load("purchase_orders.json"):
                for line in po["lines"]:
                    self.conn.execute(
                        "INSERT INTO purchase_orders VALUES (?,?,?,?,?,?)",
                        (po["po_number"], po["vendor_id"], line["sku"], line["description"], str(line["quantity"]), str(line["unit_price"])),
                    )
            for gr in load("goods_receipts.json"):
                self.conn.execute(
                    "INSERT INTO goods_receipts VALUES (?,?,?,?)",
                    (gr["po_number"], gr["sku"], str(gr["quantity"]), gr["received_on"]),
                )
            for row in load("ap_ledger.json"):
                self.conn.execute(
                    "INSERT INTO ap_ledger VALUES (?,?,?,?,?)",
                    (row["vendor_id"], row["invoice_number"], row["issue_date"], str(row["gross_total"]), "history"),
                )
            self.conn.commit()

    # ------------------------------------------------------------ vendors
    def find_vendor(self, vat_id: str | None, name: str | None) -> dict | None:
        if vat_id:
            row = self.conn.execute("SELECT * FROM vendors WHERE vat_id = ?", (vat_id.replace(" ", ""),)).fetchone()
            if row:
                return dict(row)
        if name:
            row = self.conn.execute("SELECT * FROM vendors WHERE lower(name) = lower(?)", (name.strip(),)).fetchone()
            if row:
                return dict(row)
        return None

    def list_vendors(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM vendors ORDER BY name")]

    # ------------------------------------------------------------ POs / GRs
    def get_po(self, po_number: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)).fetchall()
        return [{**dict(r), "quantity": Decimal(r["quantity"]), "unit_price": Decimal(r["unit_price"])} for r in rows]

    def received_quantity(self, po_number: str, sku: str) -> Decimal:
        rows = self.conn.execute("SELECT quantity FROM goods_receipts WHERE po_number = ? AND sku = ?", (po_number, sku)).fetchall()
        return sum((Decimal(r["quantity"]) for r in rows), Decimal("0"))

    # ------------------------------------------------------------ ledger
    def is_duplicate(self, vendor_id: str, invoice_number: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM ap_ledger WHERE vendor_id = ? AND invoice_number = ?", (vendor_id, invoice_number)).fetchone())

    def vendor_amount_stats(self, vendor_id: str) -> tuple[Decimal, Decimal, int]:
        rows = self.conn.execute("SELECT gross_total FROM ap_ledger WHERE vendor_id = ?", (vendor_id,)).fetchall()
        amounts = [float(r["gross_total"]) for r in rows]
        if not amounts:
            return Decimal("0"), Decimal("0"), 0
        return Decimal(str(round(mean(amounts), 2))), Decimal(str(round(pstdev(amounts), 2))), len(amounts)

    def post_invoice(self, vendor_id: str, invoice_number: str, issue_date: str, gross_total: Decimal) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO ap_ledger VALUES (?,?,?,?,?)",
                (vendor_id, invoice_number, issue_date, str(gross_total), "invoiceguard"),
            )
            self.conn.commit()
