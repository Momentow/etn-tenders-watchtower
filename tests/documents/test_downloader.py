import hashlib
import io
import zipfile
from urllib.parse import unquote

import httpx
import pytest

from watchtower.documents.downloader import download_documents
from watchtower.documents.storage import FileStore
from watchtower.engine.result import DocumentRef
from watchtower.net import HostRateLimiter, PoliteClient

PDF = b"%PDF-1.4 Leistungsverzeichnis " + b"x" * 5000


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_zip(files: dict[str, bytes], *, utf8_names: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            if utf8_names:
                zf.writestr(name, data)
            else:  # an old Windows tool: cp850 bytes, no UTF-8 flag
                info = zipfile.ZipInfo(name.encode("cp850").decode("cp437"))
                zf.writestr(info, data)
    return buf.getvalue()


class Site:
    """Fake portal: path → (status, headers, body)."""

    def __init__(self, routes: dict[str, tuple[int, dict[str, str], bytes]]):
        self.routes = routes
        self.hits: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hits.append(request.url.path)
        status, headers, body = self.routes.get(request.url.path, (404, {}, b"not found"))
        return httpx.Response(status, headers=headers, content=body)


@pytest.fixture
def files(tmp_path):
    return FileStore(tmp_path / "files")


async def run(site, refs, files, **kw):
    client = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(site), backoff_base=0.001)
    try:
        return await download_documents([DocumentRef(**r) for r in refs], client, files=files, **kw)
    finally:
        await client.aclose()


def ref(path, name=None):
    return {"url": f"https://portal.example{path}", "fileName": name}


# ---------------------------------------------------------------- plain files and names


async def test_stores_file_by_sha256(files):
    site = Site({"/d/lv.pdf": (200, {"content-type": "application/pdf"}, PDF)})
    report = await run(site, [ref("/d/lv.pdf", "Leistungsverzeichnis")], files)
    [doc] = report.stored
    assert (doc.file_name, doc.size_bytes, doc.sha256) == ("lv.pdf", len(PDF), sha(PDF))
    assert (files.root / sha(PDF)).read_bytes() == PDF
    assert list(files.tmp_dir.iterdir()) == []
    assert not report.errors and not report.skipped


@pytest.mark.parametrize(
    "path, headers, link_text, expected",
    [
        (
            "/dl?id=7",
            {"content-disposition": 'attachment; filename="Vergabeunterlagen.pdf"'},
            None,
            "Vergabeunterlagen.pdf",
        ),
        (
            "/dl?id=7",
            {"content-disposition": "attachment; filename*=UTF-8''Pli%C3%A9go%20t%C3%A9cnico.pdf"},
            None,
            "Pliégo técnico.pdf",
        ),
        ("/dl?id=7", {"content-type": "application/pdf"}, "Leistungsbeschreibung", "Leistungsbeschreibung.pdf"),
        ("/dl?id=7", {}, "Anlage 1", "Anlage 1"),
        ("/dl?id=7", {}, None, "document"),
        ("/files/Ausschreibung%20A%2B.docx", {}, "Link text", "Ausschreibung A+.docx"),
        ("/dl?id=7", {"content-disposition": 'attachment; filename="..\\\\..\\\\evil.exe"'}, None, "evil.exe"),
    ],
)
async def test_file_names(files, path, headers, link_text, expected):
    site = Site({unquote(path.split("?")[0]): (200, headers, PDF)})
    report = await run(site, [ref(path, link_text)], files)
    assert report.stored[0].file_name == expected


async def test_same_content_stored_once_and_duplicate_refs_fetched_once(files):
    site = Site({"/a.pdf": (200, {}, PDF), "/b.pdf": (200, {}, PDF)})
    report = await run(site, [ref("/a.pdf"), ref("/b.pdf"), ref("/a.pdf")], files)
    assert [d.file_name for d in report.stored] == ["a.pdf", "b.pdf"]
    assert site.hits == ["/a.pdf", "/b.pdf"]
    assert [p.name for p in files.root.iterdir() if p.is_file()] == [sha(PDF)]


# ---------------------------------------------------------------- the 250 MB rule (scaled down)


async def test_skips_large_file_from_headers_without_reading(files):
    site = Site({"/big.zip": (200, {"content-length": str(10_000)}, b"x" * 10_000)})
    report = await run(site, [ref("/big.zip")], files, max_bytes=1000)
    assert report.stored == [] and len(report.skipped) == 1 and not report.errors


async def test_skips_large_file_while_streaming(files):
    async def body():
        yield b"y" * 600
        yield b"y" * 600

    def handler(request):
        return httpx.Response(200, content=body())  # streamed, no content-length

    client = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(handler))
    report = await download_documents([DocumentRef(url="https://p.example/x.pdf")], client, files=files, max_bytes=1000)
    await client.aclose()
    assert report.stored == [] and report.skipped and list(files.tmp_dir.iterdir()) == []


# ---------------------------------------------------------------- failures


async def test_html_instead_of_file_is_an_error(files):
    site = Site({"/dl": (200, {"content-type": "text/html; charset=utf-8"}, b"<!doctype html><form>Login</form>")})
    report = await run(site, [ref("/dl", "Unterlagen")], files)
    assert report.stored == [] and "web page instead of a file" in report.errors[0]
    assert report.failed_urls == ["https://portal.example/dl"]


async def test_sniffs_html_even_with_a_wrong_content_type(files):
    site = Site({"/dl": (200, {"content-type": "application/octet-stream"}, b"  <html><body>Captcha</body></html>")})
    report = await run(site, [ref("/dl")], files)
    assert report.stored == [] and report.errors


async def test_real_html_document_is_kept(files):
    site = Site({"/notice.html": (200, {"content-type": "text/html"}, b"<html>Bekanntmachung</html>")})
    report = await run(site, [ref("/notice.html")], files)
    assert [d.file_name for d in report.stored] == ["notice.html"]


async def test_http_errors_and_empty_files(files):
    site = Site({"/empty.pdf": (200, {}, b"")})
    report = await run(site, [ref("/missing.pdf"), ref("/empty.pdf")], files)
    assert report.stored == []
    assert "HTTP 404" in report.errors[0] and "empty file" in report.errors[1]
    assert len(report.failed_urls) == 2


async def test_should_stop(files):
    site = Site({"/a.pdf": (200, {}, PDF)})

    async def stop():
        return True

    report = await run(site, [ref("/a.pdf")], files, should_stop=stop)
    assert report.stored == [] and site.hits == []


# ---------------------------------------------------------------- ZIPs


async def test_unpacks_zip_including_nested_and_skips_junk(files):
    inner = make_zip({"Pläne/Grundriss.dwg": b"dwg-bytes", "Anlage.pdf": PDF})
    outer = make_zip(
        {
            "Leistungsverzeichnis.pdf": b"%PDF lv",
            "Teil B/Formblätter.zip": inner,
            "__MACOSX/._Leistungsverzeichnis.pdf": b"junk",
            "Thumbs.db": b"junk",
        }
    )
    site = Site({"/alle.zip": (200, {"content-type": "application/zip"}, outer)})
    report = await run(site, [ref("/alle.zip", "Alle Unterlagen")], files)
    got = {(d.archive_member, d.file_name) for d in report.stored}
    assert got == {
        ("Leistungsverzeichnis.pdf", "Leistungsverzeichnis.pdf"),
        ("Teil B/Formblätter.zip/Pläne/Grundriss.dwg", "Grundriss.dwg"),
        ("Teil B/Formblätter.zip/Anlage.pdf", "Anlage.pdf"),
    }
    assert all(d.source_url == "https://portal.example/alle.zip" for d in report.stored)
    for d in report.stored:
        assert (files.root / d.sha256).is_file()
    stored_files = {p.name for p in files.root.iterdir() if p.is_file()}
    assert sha(outer) not in stored_files and sha(inner) not in stored_files  # archives themselves aren't kept
    assert list(files.tmp_dir.iterdir()) == []


async def test_zip_member_over_limit_skipped_rest_kept(files):
    data = make_zip({"small.pdf": b"%PDF small", "huge.bin": b"z" * 5000})
    site = Site({"/a.zip": (200, {}, data)})
    report = await run(site, [ref("/a.zip")], files, max_bytes=len(data) + 100 if len(data) < 4000 else 4000)
    assert [d.file_name for d in report.stored] == ["small.pdf"]
    assert any("huge.bin" in s for s in report.skipped)


async def test_cp850_member_names_are_decoded(files):
    data = make_zip({"Leistungsbeschreibung_Straßenbau_Köln.pdf": PDF}, utf8_names=False)
    site = Site({"/a.zip": (200, {}, data)})
    report = await run(site, [ref("/a.zip")], files)
    assert report.stored[0].file_name == "Leistungsbeschreibung_Straßenbau_Köln.pdf"


async def test_broken_zip_kept_as_file(files):
    broken = b"PK\x03\x04" + b"\x00" * 100
    site = Site({"/a.zip": (200, {}, broken)})
    report = await run(site, [ref("/a.zip")], files)
    [doc] = report.stored
    assert doc.file_name == "a.zip" and doc.sha256 == sha(broken)
    assert "not a readable ZIP" in report.errors[0]


async def test_password_protected_member_reported(files):
    data = bytearray(make_zip({"secret.pdf": PDF, "open.pdf": b"%PDF open"}))
    # set the "encrypted" flag on the first member in both headers
    lh = data.find(b"PK\x03\x04")
    data[lh + 6] |= 0x1
    ch = data.find(b"PK\x01\x02")
    data[ch + 8] |= 0x1
    site = Site({"/a.zip": (200, {}, bytes(data))})
    report = await run(site, [ref("/a.zip")], files)
    assert [d.file_name for d in report.stored] == ["open.pdf"]
    assert any("password-protected" in e for e in report.errors)
