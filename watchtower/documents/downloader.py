"""Download a tender's documents: streamed to disk with sha256, ZIPs unpacked (nested too), the 250 MB rule.

`download_documents(refs, client)` returns a `DownloadReport`: the stored files plus plain-language notes on what was
skipped (too large) or failed. Nothing is ever held fully in memory: HTTP bodies and ZIP members are copied in chunks.
"""

from __future__ import annotations

import asyncio
import mimetypes
import re
import zipfile
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx

from watchtower.documents.storage import CHUNK_BYTES, MAX_FILE_BYTES, FileStore, MiB, TooLarge
from watchtower.engine.result import DocumentRef
from watchtower.net.client import PoliteClient

MAX_ZIP_DEPTH = 3
MAX_ZIP_MEMBERS = 5000
MAX_ZIP_TOTAL_BYTES = 4 * 1024 * MiB  # per archive, against zip bombs
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
_JUNK = re.compile(r"(^|/)(__MACOSX/|\._|\.DS_Store$|Thumbs\.db$|desktop\.ini$)", re.I)
_BAD_NAME_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


@dataclass
class StoredDocument:
    """One stored file of a tender. A ZIP gives one per unpacked member (`archive_member` is its path inside)."""

    source_url: str
    file_name: str
    size_bytes: int
    sha256: str
    archive_member: str = ""


@dataclass
class DownloadReport:
    stored: list[StoredDocument] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # left out on purpose (over 250 MB)
    errors: list[str] = field(default_factory=list)  # could not be downloaded or read
    failed_urls: list[str] = field(default_factory=list)  # refs that failed (worth retrying), not skipped ones


async def fetch_documents(tender_key: str, refs: list[DocumentRef], client: PoliteClient) -> list[StoredDocument]:
    """ADR 0002 interface. See `download_documents` for the full report."""
    return (await download_documents(refs, client)).stored


async def download_documents(
    refs: list[DocumentRef],
    client: PoliteClient,
    *,
    files: FileStore | None = None,
    max_bytes: int = MAX_FILE_BYTES,
    should_stop: Callable[[], Awaitable[bool]] | None = None,
) -> DownloadReport:
    files = files or FileStore()
    report = DownloadReport()
    seen: set[str] = set()
    for ref in refs:
        if ref.url in seen:
            continue
        seen.add(ref.url)
        if should_stop is not None and await should_stop():
            break
        await _download_one(ref, client, files, max_bytes, report)
    return report


async def _download_one(
    ref: DocumentRef, client: PoliteClient, files: FileStore, max_bytes: int, report: DownloadReport
) -> None:
    label = ref.fileName or ref.url
    writer = None
    try:
        async with client.stream("GET", ref.url) as response:
            if response.status_code >= 400:
                report.errors.append(f"{label}: HTTP {response.status_code} from {ref.url}.")
                report.failed_urls.append(ref.url)
                return
            length = _int(response.headers.get("content-length"))
            if length is not None and length > max_bytes:
                report.skipped.append(f"{label}: {length // MiB} MB, over the {max_bytes // MiB} MB limit.")
                return
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            writer = files.writer(max_bytes)
            head = b""
            async for chunk in response.aiter_bytes(CHUNK_BYTES):
                if not head:
                    head = chunk[:512]
                    if _looks_like_html(head, content_type, str(response.url)):
                        writer.discard()
                        writer = None
                        report.errors.append(
                            f"{label}: {ref.url} returned a web page instead of a file (a login, captcha or "
                            "download page?)."
                        )
                        report.failed_urls.append(ref.url)
                        return
                writer.write(chunk)
            name = choose_file_name(ref, response, content_type)
    except TooLarge:
        if writer is not None:
            writer.discard()
        report.skipped.append(f"{label}: over the {max_bytes // MiB} MB limit.")
        return
    except httpx.HTTPError as e:
        if writer is not None:
            writer.discard()
        report.errors.append(f"{label}: download failed ({type(e).__name__}).")
        report.failed_urls.append(ref.url)
        return

    if writer.size == 0:
        writer.discard()
        report.errors.append(f"{label}: {ref.url} returned an empty file.")
        report.failed_urls.append(ref.url)
        return
    if head.startswith(_ZIP_MAGIC):
        writer.close()
        try:
            members, notes = await asyncio.to_thread(_unpack, writer.tmp_path, files, max_bytes, 1, "")
        except zipfile.BadZipFile:
            members, notes = None, []
        if members is not None:
            writer.discard()
            report.stored += [
                StoredDocument(ref.url, n, blob_size, sha, archive_member=member)
                for member, n, blob_size, sha in members
            ]
            report.skipped += [f"{name}: {note}" for note in notes if note.startswith("skipped")]
            report.errors += [f"{name}: {note}" for note in notes if not note.startswith("skipped")]
            return
        report.errors.append(f"{name}: not a readable ZIP; kept as it is.")
    blob = writer.commit()
    report.stored.append(StoredDocument(ref.url, name, blob.size_bytes, blob.sha256))


# --------------------------------------------------------------------------- ZIP


def _unpack(
    path: Path, files: FileStore, max_bytes: int, depth: int, prefix: str
) -> tuple[list[tuple[str, str, int, str]], list[str]]:
    """Unpack a ZIP into the file store. Returns (member path, file name, size, sha256) per file, and notes."""
    out: list[tuple[str, str, int, str]] = []
    notes: list[str] = []
    total = 0
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and not _JUNK.search(_member_name(i))]
        if len(infos) > MAX_ZIP_MEMBERS:
            notes.append(f"skipped: {len(infos)} files in one archive, over the limit of {MAX_ZIP_MEMBERS}.")
            return out, notes
        for info in infos:
            member = _member_name(info)
            full = f"{prefix}{member}"
            if info.file_size > max_bytes:
                notes.append(f"skipped {full}: {info.file_size // MiB} MB, over the {max_bytes // MiB} MB limit.")
                continue
            if info.flag_bits & 0x1:
                notes.append(f"{full} is password-protected.")
                continue
            if total + info.file_size > MAX_ZIP_TOTAL_BYTES:
                notes.append(f"skipped {full}: the archive unpacks to more than {MAX_ZIP_TOTAL_BYTES // MiB} MB.")
                continue
            writer = files.writer(max_bytes)
            head = b""
            try:
                with zf.open(info) as src:
                    while chunk := src.read(CHUNK_BYTES):
                        head = head or chunk[:8]
                        writer.write(chunk)
            except TooLarge:
                writer.discard()
                notes.append(f"skipped {full}: over the {max_bytes // MiB} MB limit.")
                continue
            except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError, OSError) as e:
                writer.discard()
                notes.append(f"{full} could not be unpacked ({type(e).__name__}).")
                continue
            total += writer.size
            if head.startswith(_ZIP_MAGIC) and depth < MAX_ZIP_DEPTH:
                writer.close()
                try:
                    inner, inner_notes = _unpack(writer.tmp_path, files, max_bytes, depth + 1, f"{full}/")
                except zipfile.BadZipFile:
                    inner = None
                if inner is not None:
                    writer.discard()
                    out += inner
                    notes += inner_notes
                    continue
            blob = writer.commit()
            out.append((full, _safe_name(PurePosixPath(member).name) or "document", blob.size_bytes, blob.sha256))
    return out, notes


def _member_name(info: zipfile.ZipInfo) -> str:
    """Member path with the right characters: names without the UTF-8 flag were decoded as cp437 by zipfile, but
    are often UTF-8 or cp850 (Windows in Western Europe)."""
    name = info.filename
    if not info.flag_bits & 0x800:
        raw = name.encode("cp437", errors="replace")
        for encoding in ("utf-8", "cp850"):
            try:
                name = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    return name.replace("\\", "/").lstrip("/")


# --------------------------------------------------------------------------- names and sniffing


def choose_file_name(ref: DocumentRef, response: httpx.Response, content_type: str) -> str:
    """Content-Disposition, else the URL's file name (if it has an extension), else the portal's link text with an
    extension from the content type, else `document`."""
    ext = _extension_for(content_type)
    candidates = [
        _disposition_name(response.headers.get("content-disposition")),
        _url_name(str(response.url)),
        _url_name(ref.url),
    ]
    for c in candidates:
        if c:
            return c
    base = _safe_name(ref.fileName or "") or "document"
    return base if PurePosixPath(base).suffix or not ext else base + ext


def _disposition_name(header: str | None) -> str | None:
    if not header:
        return None
    msg = Message()
    msg["content-disposition"] = header
    name = msg.get_filename()
    if not name:
        return None
    try:  # servers sending raw UTF-8 bytes arrive as latin-1 text
        name = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return _safe_name(PurePosixPath(name.replace("\\", "/")).name)


def _url_name(url: str) -> str | None:
    last = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    name = _safe_name(last)
    return name if name and PurePosixPath(name).suffix and len(PurePosixPath(name).suffix) <= 6 else None


def _safe_name(name: str) -> str:
    cleaned = _BAD_NAME_CHARS.sub("_", " ".join(name.split())).strip(" ._")
    if len(cleaned) > 200:
        stem, suffix = PurePosixPath(cleaned).stem, PurePosixPath(cleaned).suffix
        cleaned = stem[: 200 - len(suffix)] + suffix
    return cleaned


def _extension_for(content_type: str) -> str:
    if not content_type or content_type in ("application/octet-stream", "binary/octet-stream"):
        return ""
    return mimetypes.guess_extension(content_type) or ""


def _looks_like_html(head: bytes, content_type: str, url: str) -> bool:
    if urlsplit(url).path.lower().endswith((".htm", ".html")):
        return False  # the portal really offers an HTML document
    if content_type in ("text/html", "application/xhtml+xml"):
        return True
    start = head.lstrip()[:15].lower()
    return start.startswith((b"<!doctype html", b"<html"))


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
