"""Command line entry point.

invoiceguard demo                 # run the 10 sample documents, print outcomes
invoiceguard process FILE         # run one document
invoiceguard serve [--port 8000]  # API + review UI
invoiceguard mcp [--http PORT]    # MCP server
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _print_case(r: dict) -> None:
    inv = r.get("invoice") or {}
    print(f"  {r['filename']:<38} {r['status']:<16} risk={str(r.get('risk_score')):<4} gross={inv.get('gross_total', '—')}")
    for reason in r.get("reasons", [])[:3]:
        print(f"      · {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="invoiceguard")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    p = sub.add_parser("process")
    p.add_argument("file", type=Path)
    p.add_argument("--json", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    m = sub.add_parser("mcp")
    m.add_argument("--http", type=int)
    args = parser.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("invoiceguard.api:app", host=args.host, port=args.port)
        return
    if args.cmd == "mcp":
        from .mcp_server import mcp

        mcp.run(transport="http", host="0.0.0.0", port=args.http) if args.http else mcp.run()
        return

    from .service import get_app

    app = get_app()
    if args.cmd == "process":
        r = app.submit(args.file.name, args.file.read_bytes())
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str)) if args.json else _print_case(r)
        return

    folder = app.settings.data_dir / "samples"
    print(f"InvoiceGuard demo · extractor={app.extractor.name}\n")
    for path in sorted(folder.iterdir()):
        _print_case(app.submit(path.name, path.read_bytes()))
    print("\n", json.dumps(app.stats()), "\n audit:", json.dumps(app.audit.verify()))


if __name__ == "__main__":
    main()
