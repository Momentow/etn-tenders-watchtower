"""The `download` job (ADR 0006): download one tender's documents and record them in the store.

The read pipeline (T-017) queues it with `enqueue_downloads` for new or changed tenders, after upserting the tender.

Payload: `{"tender_id": "...", "documents": [{"url": "...", "fileName": "..." | null}, ...]}`.
"""

from __future__ import annotations

import logging

from watchtower.core.handlers import register
from watchtower.core.scheduler import JobContext
from watchtower.core.store import Store
from watchtower.documents.downloader import download_documents
from watchtower.documents.storage import FileStore
from watchtower.engine.result import DocumentRef
from watchtower.net.client import PoliteClient

log = logging.getLogger(__name__)

_client: PoliteClient | None = None


def use_client(client: PoliteClient) -> None:
    """Set the process's shared PoliteClient (one limiter for all portal traffic, see docs/net/network.md)."""
    global _client
    _client = client


def _shared_client() -> PoliteClient:
    global _client
    if _client is None:
        _client = PoliteClient()
    return _client


class DownloadIncomplete(RuntimeError):
    """Some documents failed for reasons worth retrying; the scheduler retries the job."""


async def enqueue_downloads(store: Store, portal_id: str, tender_id: str, refs: list[DocumentRef]) -> None:
    payload = {"tender_id": tender_id, "documents": [r.model_dump() for r in refs]}
    await store.enqueue_job("download", portal_id=portal_id, payload=payload)


@register("download")
async def handle_download(ctx: JobContext) -> None:
    await run_download(ctx, _shared_client(), FileStore())


async def run_download(ctx: JobContext, client: PoliteClient, files: FileStore) -> None:
    portal_id = ctx.job.portal_id
    tender_id = ctx.job.payload["tender_id"]
    refs = [DocumentRef.model_validate(d) for d in ctx.job.payload.get("documents", [])]
    if portal_id is None or await ctx.store.get_tender(portal_id, tender_id) is None:
        return  # the tender is gone (portal deleted or never stored): nothing to attach files to

    report = await download_documents(refs, client, files=files, should_stop=ctx.portal_deleted)
    if await ctx.portal_deleted():
        return
    # Replace the tender's documents. Ones the portal no longer lists go; ones that failed this time keep their
    # earlier copy. (If the process dies in between, the job is retried and restores them.)
    failed = [r.url for r in refs if r.url in report.failed_urls]
    await ctx.store.remove_documents(portal_id, tender_id, keep_source_urls=failed)
    for doc in report.stored:
        await ctx.store.add_document(
            portal_id,
            tender_id,
            source_url=doc.source_url,
            file_name=doc.file_name,
            size_bytes=doc.size_bytes,
            sha256=doc.sha256,
            archive_member=doc.archive_member,
        )
    for note in report.skipped:
        log.info("tender %s/%s: skipped %s", portal_id, tender_id, note)
    if report.errors:
        message = "; ".join(report.errors)
        if report.failed_urls:
            raise DownloadIncomplete(message)
        log.warning("tender %s/%s: %s", portal_id, tender_id, message)
