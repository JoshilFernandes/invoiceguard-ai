"""Application facade shared by the API, the MCP server, the CLI and the evals."""

from __future__ import annotations

import base64
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from functools import lru_cache

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .audit import AuditLog
from .config import Settings, get_settings
from .erp import ERP
from .extraction import build_extractor
from .graph import Services, build_graph, summarize
from .models import Decision

INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY, filename TEXT, created TEXT, status TEXT, route TEXT,
  seller TEXT, invoice_number TEXT, gross_total TEXT, risk_score INTEGER)
"""


class InvoiceGuard:
    def __init__(self, settings: Settings | None = None):
        self.settings = s = settings or get_settings()
        s.state_dir.mkdir(parents=True, exist_ok=True)
        self.erp = ERP(s.state_dir / "erp.db", s.data_dir / "erp")
        self.audit = AuditLog(s.state_dir / "audit.jsonl")
        self.extractor = build_extractor(s.llm, s.offline)
        self._conn = sqlite3.connect(s.state_dir / "cases.db", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(INDEX_SCHEMA)
        self._lock = threading.Lock()
        self.checkpointer = SqliteSaver(sqlite3.connect(s.state_dir / "checkpoints.db", check_same_thread=False))
        self.graph = build_graph(Services(s, self.erp, self.audit, self.extractor), checkpointer=self.checkpointer)

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _cfg(case_id: str) -> dict:
        return {"configurable": {"thread_id": case_id}}

    def _index(self, values: dict) -> None:
        inv = values.get("invoice") or {}
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cases VALUES (?,?,COALESCE((SELECT created FROM cases WHERE case_id=?),?),?,?,?,?,?,?)",
                (
                    values["case_id"],
                    values.get("filename"),
                    values["case_id"],
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    values.get("status"),
                    values.get("route"),
                    (inv.get("seller") or {}).get("name"),
                    inv.get("invoice_number"),
                    inv.get("gross_total"),
                    values.get("risk_score"),
                ),
            )
            self._conn.commit()

    def _snapshot(self, case_id: str) -> dict:
        state = self.graph.get_state(self._cfg(case_id))
        values = dict(state.values)
        if state.next and "human_review" in state.next:
            values["status"] = "awaiting_review"
        return values

    # ------------------------------------------------------------ public API
    def submit(self, filename: str, data: bytes) -> dict:
        case_id = "INV-" + uuid.uuid4().hex[:10].upper()
        self.graph.invoke(
            {"case_id": case_id, "filename": filename, "content_b64": base64.b64encode(data).decode()},
            self._cfg(case_id),
        )
        values = self._snapshot(case_id)
        self._index(values)
        return summarize(values)

    def resume(self, case_id: str, decision: Decision) -> dict:
        current = self._snapshot(case_id)
        if current.get("status") != "awaiting_review":
            raise ValueError(f"Case {case_id} is not awaiting review (status={current.get('status')})")
        self.graph.invoke(Command(resume=decision.model_dump()), self._cfg(case_id))
        values = self._snapshot(case_id)
        self._index(values)
        return summarize(values)

    def get(self, case_id: str) -> dict | None:
        values = self._snapshot(case_id)
        return summarize(values) if values.get("case_id") else None

    def list_cases(self, status: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM cases", ()
        if status:
            sql, args = sql + " WHERE status = ?", (status,)
        return [dict(r) for r in self._conn.execute(sql + " ORDER BY created DESC, rowid DESC", args)]

    def xrechnung(self, case_id: str) -> str | None:
        return self._snapshot(case_id).get("xrechnung_xml")

    def stats(self) -> dict:
        rows = self.list_cases()
        total = len(rows)
        by = lambda key, val: sum(1 for r in rows if r[key] == val)  # noqa: E731
        auto = by("route", "auto_approve")
        return {
            "total": total,
            "posted": by("status", "posted"),
            "awaiting_review": by("status", "awaiting_review"),
            "rejected": by("status", "rejected"),
            "straight_through_rate": round(auto / total, 3) if total else 0.0,
        }


@lru_cache(maxsize=1)
def get_app() -> InvoiceGuard:
    return InvoiceGuard()
