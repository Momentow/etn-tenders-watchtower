# Document downloader (`watchtower.documents`)

Downloads a tender's documents through `PoliteClient`, stores them content-addressed under `$DATA_DIR/files/<sha256>`,
and records them in the store, where `GET /files/{sha256}` serves them.

## For the read pipeline (T-017)
After upserting a new or changed tender, queue its documents:

```python
from watchtower.documents.handler import enqueue_downloads
await enqueue_downloads(store, portal_id, tender.id, tender.documents)  # DocumentRef(url, fileName?) from the engine
```

That queues a `download` job with the payload `{"tender_id": …, "documents": [{"url": …, "fileName": …}, …]}`. The
registered handler then:
1. downloads every reference once, in order, checking `portal_deleted()` before each one;
2. **replaces** the tender's document rows: references the portal no longer lists are removed, and ones that failed
   this time keep their earlier copy;
3. raises (so the scheduler retries) when a download failed in a way worth retrying. Files skipped for size are not
   retried.

`fetch_documents(tender_key, refs, client)` (the ADR 0002 signature) and `download_documents(...)` (returns a report
with `stored`, `skipped`, `errors`, `failed_urls`) can be called directly, e.g. by the agent's document probe.

## Rules
| What | Rule |
|---|---|
| Streaming | Bodies and ZIP members are copied in 256 KB chunks and hashed while written to `files/.tmp`, then moved to `files/<sha256>` in one step. Identical content is stored once. |
| 250 MB | Files over 250 MiB are skipped: from `Content-Length` before reading, or as soon as the stream passes the limit. The same applies to each ZIP member. Skipped files are left out of `documents[]`, as the contract allows. |
| ZIPs | Detected by content (`PK\x03\x04`), not by name. Unpacked recursively (3 levels); the archive itself isn't kept. `__MACOSX`, `._*`, `Thumbs.db` and `.DS_Store` are dropped. Member names from old Windows tools (cp850) are decoded correctly. `archive_member` holds the path inside (`Teil B/Formblätter.zip/Anlage.pdf`). |
| ZIP limits | At most 5000 members and 4 GiB unpacked per archive. Password-protected members are reported. A broken ZIP is kept as the file it is. |
| Web page instead of a file | A `text/html` answer (or HTML in the first bytes) for a URL that isn't an `.html` document is an error, not a document: usually a login, captcha or download page. |
| Failures | HTTP ≥ 400, network errors and empty bodies are errors for that reference only. |
| File name | `Content-Disposition` (RFC 5987 too), else the URL's file name if it has an extension, else the portal's link text plus an extension from the content type, else `document`. Path parts and unsafe characters are removed. |

Tests: `uv run pytest tests/documents`.
