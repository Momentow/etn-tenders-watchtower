"""Deterministic document bytes: small valid PDFs, ZIP bundles, and a lazily streamed oversized file."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator

LARGE_SIZE = 300 * 1024 * 1024  # over the contract's 250 MB limit


def pdf_bytes(title: str) -> bytes:
    """A minimal one-page PDF whose content mentions `title` (so different files have different hashes)."""
    text = title.encode("latin-1", "replace").replace(b"(", b"[").replace(b")", b"]")
    stream = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def zip_bytes(members: dict[str, bytes]) -> bytes:
    """A ZIP with fixed timestamps, so the bytes (and sha256) are identical on every run."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return out.getvalue()


def document(tender_id: str, name: str) -> tuple[bytes, str]:
    """Bytes and media type of a tender document. ZIPs hold two PDFs and one nested ZIP (multi-level unpacking)."""
    if name.lower().endswith(".zip"):
        inner = zip_bytes({"Anlage/Plan.pdf": pdf_bytes(f"{tender_id} plan")})
        return (
            zip_bytes(
                {
                    f"{tender_id} Teil 1.pdf": pdf_bytes(f"{tender_id} part 1"),
                    f"{tender_id} Teil 2.pdf": pdf_bytes(f"{tender_id} part 2"),
                    "Anlagen.zip": inner,
                }
            ),
            "application/zip",
        )
    return pdf_bytes(f"{tender_id} {name}"), "application/pdf"


def large_stream(chunk: int = 1 << 20) -> Iterator[bytes]:
    """Zero bytes up to LARGE_SIZE, generated lazily: clients should skip it from the Content-Length header."""
    sent = 0
    block = b"\0" * chunk
    while sent < LARGE_SIZE:
        n = min(chunk, LARGE_SIZE - sent)
        sent += n
        yield block[:n]
