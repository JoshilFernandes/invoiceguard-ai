import json

from invoiceguard.audit import AuditLog


def test_hash_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        log.append("C1", "step", n=i, iban="DE97200400600123456789")
    assert log.verify()["valid"]
    assert "DE97200400600123456789" not in (tmp_path / "audit.jsonl").read_text()

    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    entry = json.loads(lines[2])
    entry["data"]["n"] = 999
    lines[2] = json.dumps(entry)
    (tmp_path / "audit.jsonl").write_text("\n".join(lines) + "\n")
    result = log.verify()
    assert not result["valid"] and result["broken_at"] == 2
