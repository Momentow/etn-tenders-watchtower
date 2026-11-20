# HTTP API (`watchtower.api`)

Implements the [API contract](../spec/api-contract.md) exactly; the contract is the reference for fields and codes.
This page covers the choices the contract leaves open.

| Call | Implementation notes |
|---|---|
| `POST /portals` | `422` if the body has no usable `url` (missing, empty, not http/https). Always `201` with the id, also for an address already added (normalised, see [ADR 0004](../decisions/0004-storage.md)). Only a new or re-added portal queues an `attach`. |
| `DELETE /portals/{id}` | Marks the portal deleted and cancels its queued jobs. An already-deleted portal answers `404`. Re-adding the address revives the same id. |
| `POST /portals/{id}/refresh` | `202 {"runId"}`. Returns any unfinished run (read or repair). Otherwise queues a `read`, or an `attach` for a `failed` portal ([ADR 0005](../decisions/0005-run-requests.md)). |
| `GET /portals/{id}/tenders` | Required fields are always present (`null` when unknown). If-shown fields and unknown internal keys are left out. `?changedSince=` must carry an offset (`+01:00` or `Z`), otherwise `400`. An unencoded `+` (which arrives as a space) is accepted. Strictly later than the given time. |
| `GET /portals/{id}/runs` | Newest first. |
| `GET /files/{fileId}` | `fileId` is the file's sha256. Served from `$DATA_DIR/files/<sha256>` with a content type guessed from the file name. `404` if unknown or missing on disk. |

- All times are rendered with an explicit offset and second precision, e.g. `2026-11-20T09:00:00+00:00`.
- Errors are JSON `{"detail": "..."}`.
- FastAPI's Swagger and ReDoc pages are switched off: `/docs` belongs to the docs site (T-019).
- The API holds no logic of its own: it reads and requests work through `core.store`. Field names are mapped in
  `watchtower/api/serialize.py`.

Tests: `uv run pytest tests/api` (contract tests for every endpoint).
