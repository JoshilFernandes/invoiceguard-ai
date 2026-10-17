"""Tamper-evident audit trail.

Every pipeline step appends a JSON line whose hash covers the previous line's
hash (a simple hash chain). Editing or deleting any past entry breaks the
chain, which `verify()` detects — the property German bookkeeping rules
(GoBD: Unveränderbarkeit / immutability) ask for. PII is masked before writing.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from .guardrails import redact

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        self._lock = threading.Lock()

    def _last_hash(self) -> str:
        last = GENESIS
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    @staticmethod
    def _digest(prev: str, body: dict) -> str:
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256((prev + payload).encode("utf-8")).hexdigest()

    def append(self, case_id: str, event: str, actor: str = "agent", **data) -> dict:
        clean = json.loads(redact(json.dumps(data, ensure_ascii=False, default=str)))
        with self._lock:
            prev = self._last_hash()
            body = {
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "case_id": case_id,
                "event": event,
                "actor": actor,
                "data": clean,
            }
            entry = {**body, "prev_hash": prev, "hash": self._digest(prev, body)}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self, case_id: str | None = None) -> list[dict]:
        with self.path.open("r", encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        return [r for r in rows if case_id is None or r["case_id"] == case_id]

    def verify(self) -> dict:
        prev = GENESIS
        rows = self.entries()
        for i, row in enumerate(rows):
            body = {k: row[k] for k in ("ts", "case_id", "event", "actor", "data")}
            if row["prev_hash"] != prev or row["hash"] != self._digest(prev, body):
                return {"valid": False, "entries": len(rows), "broken_at": i}
            prev = row["hash"]
        return {"valid": True, "entries": len(rows), "head": prev}
