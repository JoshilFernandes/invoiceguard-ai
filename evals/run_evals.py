"""Evaluation harness — gates CI.

    python evals/run_evals.py                       # offline rule extractor (CI)
    INVOICEGUARD_LLM=groq:llama-3.3-70b-versatile python evals/run_evals.py

Metrics
  routing_accuracy        route chosen == expected route
  field_accuracy          extracted key fields == golden values
  flag_recall             expected issue / risk codes that were raised
  unsafe_auto_approvals   cases that should have stopped but were auto-approved (must be 0)

Writes evals/report.md and evals/report.json; exits non-zero if a gate fails.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GATES = {"routing_accuracy": 0.9, "field_accuracy": 0.95, "flag_recall": 0.9, "unsafe_auto_approvals": 0}


def get_field(invoice: dict, dotted: str):
    cur = invoice
    for part in dotted.split("."):
        cur = (cur or {}).get(part)
    return cur


def same(a, b) -> bool:
    if a is None or b is None:
        return a == b
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except Exception:
        return str(a).replace(" ", "") == str(b).replace(" ", "")


def main() -> int:
    os.environ["INVOICEGUARD_STATE_DIR"] = tempfile.mkdtemp(prefix="ig-eval-")
    from invoiceguard.service import InvoiceGuard

    app = InvoiceGuard()
    golden = json.loads((ROOT / "evals" / "golden.json").read_text(encoding="utf-8"))
    samples = ROOT / "data" / "samples"

    rows, route_ok, field_hits, field_total, flag_hits, flag_total, unsafe = [], 0, 0, 0, 0, 0, 0
    started = time.perf_counter()
    for case in golden:
        t0 = time.perf_counter()
        r = app.submit(case["file"], (samples / case["file"]).read_bytes())
        ms = (time.perf_counter() - t0) * 1000
        inv = r.get("invoice") or {}
        raised = {i["code"].split(":")[0] for i in r["issues"]} | {s["code"] for s in r["risk_signals"]}

        ok_route = r["route"] == case["route"]
        route_ok += ok_route
        if r["route"] == "auto_approve" and case["route"] != "auto_approve":
            unsafe += 1

        wrong_fields = []
        for name, expected in case["fields"].items():
            field_total += 1
            if same(get_field(inv, name), expected):
                field_hits += 1
            else:
                wrong_fields.append(f"{name}={get_field(inv, name)!r} (want {expected!r})")

        missed = [c for c in case["must_flag"] if c not in raised]
        flag_total += len(case["must_flag"])
        flag_hits += len(case["must_flag"]) - len(missed)

        rows.append(
            {
                "file": case["file"],
                "expected": case["route"],
                "got": r["route"],
                "pass": ok_route and not wrong_fields and not missed,
                "wrong_fields": wrong_fields,
                "missed_flags": missed,
                "latency_ms": round(ms),
            }
        )

    n = len(golden)
    metrics = {
        "extractor": app.extractor.name,
        "cases": n,
        "routing_accuracy": round(route_ok / n, 3),
        "field_accuracy": round(field_hits / field_total, 3) if field_total else 1.0,
        "flag_recall": round(flag_hits / flag_total, 3) if flag_total else 1.0,
        "unsafe_auto_approvals": unsafe,
        "straight_through_rate": app.stats()["straight_through_rate"],
        "total_seconds": round(time.perf_counter() - started, 2),
        "audit_chain_valid": app.audit.verify()["valid"],
    }
    failures = [k for k, threshold in GATES.items() if (metrics[k] > threshold if k == "unsafe_auto_approvals" else metrics[k] < threshold)]
    if not metrics["audit_chain_valid"]:
        failures.append("audit_chain_valid")

    lines = [
        "# InvoiceGuard eval report",
        "",
        f"Extractor: `{metrics['extractor']}` · {n} cases · {metrics['total_seconds']} s",
        "",
        "| Metric | Value | Gate |",
        "|---|---|---|",
    ]
    for k in ("routing_accuracy", "field_accuracy", "flag_recall", "unsafe_auto_approvals"):
        gate = f"{'≤' if k == 'unsafe_auto_approvals' else '≥'} {GATES[k]}"
        lines.append(f"| {k} | {metrics[k]} | {gate} |")
    lines += [
        f"| straight_through_rate | {metrics['straight_through_rate']} | — |",
        f"| audit_chain_valid | {metrics['audit_chain_valid']} | true |",
        "",
        "| Case | Expected | Got | Pass | Notes |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        notes = "; ".join(row["wrong_fields"] + [f"missed {m}" for m in row["missed_flags"]]) or "—"
        lines.append(f"| {row['file']} | {row['expected']} | {row['got']} | {'✅' if row['pass'] else '❌'} | {notes} |")
    lines += ["", "**Result: " + ("PASS" if not failures else "FAIL — " + ", ".join(failures)) + "**"]

    (ROOT / "evals" / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "evals" / "report.json").write_text(json.dumps({"metrics": metrics, "cases": rows}, indent=2) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
