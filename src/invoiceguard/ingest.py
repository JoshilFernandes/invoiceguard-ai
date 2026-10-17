"""Multi-channel intake: PDF, e-invoice XML (XRechnung/ZUGFeRD), e-mail (.eml) and plain text."""

from __future__ import annotations

import email
import hashlib
import io
from dataclasses import dataclass, field
from email import policy

from pypdf import PdfReader

from .xrechnung import is_e_invoice


@dataclass
class IngestedDocument:
    filename: str
    channel: str  # pdf | xml | email | text
    text: str
    sha256: str
    xml: str | None = None  # structured e-invoice payload, if any
    email_meta: dict = field(default_factory=dict)
    attachments: list[str] = field(default_factory=list)


def _pdf_text(data: bytes) -> tuple[str, str | None]:
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    xml = None
    # ZUGFeRD / Factur-X: hybrid PDF/A-3 with an embedded CII XML file.
    try:
        for name, payloads in (reader.attachments or {}).items():
            if name.lower().endswith(".xml"):
                candidate = payloads[0].decode("utf-8", errors="replace")
                if is_e_invoice(candidate):
                    xml = candidate
                    break
    except Exception:  # pragma: no cover - malformed attachment tables
        pass
    return text, xml


def ingest(filename: str, data: bytes) -> IngestedDocument:
    sha = hashlib.sha256(data).hexdigest()
    lower = filename.lower()

    if lower.endswith(".pdf") or data[:5] == b"%PDF-":
        text, xml = _pdf_text(data)
        return IngestedDocument(filename, "pdf", text, sha, xml=xml)

    if lower.endswith(".xml"):
        xml = data.decode("utf-8", errors="replace")
        return IngestedDocument(filename, "xml", xml, sha, xml=xml if is_e_invoice(xml) else None)

    if lower.endswith(".eml"):
        msg = email.message_from_bytes(data, policy=policy.default)
        meta = {
            "from": str(msg.get("From", "")),
            "reply_to": str(msg.get("Reply-To", "")),
            "subject": str(msg.get("Subject", "")),
        }
        body_part = msg.get_body(preferencelist=("plain", "html"))
        body = body_part.get_content() if body_part else ""
        texts = [f"E-Mail subject: {meta['subject']}\n{body}"]
        xml = None
        names = []
        for part in msg.iter_attachments():
            name = part.get_filename() or "attachment"
            payload = part.get_payload(decode=True) or b""
            names.append(name)
            if name.lower().endswith(".pdf"):
                t, embedded = _pdf_text(payload)
                texts.append(t)
                xml = xml or embedded
            elif name.lower().endswith(".xml"):
                candidate = payload.decode("utf-8", errors="replace")
                if is_e_invoice(candidate):
                    xml = candidate
        return IngestedDocument(filename, "email", "\n\n".join(texts), sha, xml=xml, email_meta=meta, attachments=names)

    return IngestedDocument(filename, "text", data.decode("utf-8", errors="replace"), sha)
