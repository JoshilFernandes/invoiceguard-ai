"""REST API + reviewer UI.

uvicorn invoiceguard.api:app --reload      →  http://localhost:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from .config import Settings
from .models import Decision
from .service import InvoiceGuard

UI = Path(__file__).parent / "ui" / "index.html"
MAX_UPLOAD = 15 * 1024 * 1024
ALLOWED = (".pdf", ".xml", ".eml", ".txt")


def create_app(settings: Settings | None = None) -> FastAPI:
    api = FastAPI(
        title="InvoiceGuard AI",
        version="1.0.0",
        description="Agentic accounts-payable automation for German e-invoicing (EN 16931 / XRechnung).",
    )
    api.state.guard = InvoiceGuard(settings)

    def guard(request: Request) -> InvoiceGuard:
        return request.app.state.guard

    @api.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(UI)

    @api.get("/health")
    def health(request: Request) -> dict:
        g = guard(request)
        return {"status": "ok", "extractor": g.extractor.name}

    @api.post("/api/invoices", status_code=201)
    async def upload(request: Request, file: UploadFile = File(...)) -> dict:
        name = file.filename or "upload"
        if not name.lower().endswith(ALLOWED):
            raise HTTPException(415, f"Unsupported file type; allowed: {', '.join(ALLOWED)}")
        data = await file.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "File too large")
        return guard(request).submit(name, data)

    @api.post("/api/demo/load-samples", status_code=201)
    def load_samples(request: Request) -> list[dict]:
        g = guard(request)
        folder = g.settings.data_dir / "samples"
        return [g.submit(p.name, p.read_bytes()) for p in sorted(folder.iterdir()) if p.suffix in ALLOWED]

    @api.get("/api/invoices")
    def list_invoices(request: Request, status: str | None = None) -> list[dict]:
        return guard(request).list_cases(status)

    @api.get("/api/invoices/{case_id}")
    def get_invoice(request: Request, case_id: str) -> dict:
        case = guard(request).get(case_id)
        if not case:
            raise HTTPException(404, "Unknown case")
        return case

    @api.post("/api/invoices/{case_id}/decision")
    def decide(request: Request, case_id: str, decision: Decision) -> dict:
        try:
            return guard(request).resume(case_id, decision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.get("/api/invoices/{case_id}/xrechnung")
    def xrechnung(request: Request, case_id: str) -> Response:
        xml = guard(request).xrechnung(case_id)
        if not xml:
            raise HTTPException(404, "No XRechnung available (case not posted)")
        return Response(xml, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="{case_id}.xml"'})

    @api.get("/api/invoices/{case_id}/audit")
    def case_audit(request: Request, case_id: str) -> list[dict]:
        return guard(request).audit.entries(case_id)

    @api.get("/api/audit/verify")
    def verify(request: Request) -> dict:
        return guard(request).audit.verify()

    @api.get("/api/stats")
    def stats(request: Request) -> dict:
        return guard(request).stats()

    @api.get("/api/vendors")
    def vendors(request: Request) -> list[dict]:
        return guard(request).erp.list_vendors()

    return api


app = create_app()
