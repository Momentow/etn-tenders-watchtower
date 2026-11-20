# API contract

Authoritative copy of the brief's appendix. Change it only via an ADR (see [decisions](../decisions/README.md)).

## General

- Everything is served by one container on port **8080**.
- Every response is JSON, except the dashboard (`GET /`), the docs (`/docs`) and a document's bytes. A list is a JSON array.
- No authentication.
- An unknown `id` answers **404**.
- Every time is a date-time with an offset, e.g. `2026-12-09T10:00:00+01:00`.

## Endpoints

| Call | Answer |
|---|---|
| `POST /portals` | Body `{ "url": "…" }`. Answer **at once** with **201** and `{ "id": "…" }`, then attach the portal in the background. The same address twice returns the same `id`. |
| `GET /portals` | **200**, a list of portal entries (below). |
| `GET /portals/{id}` | **200**, one portal entry. |
| `DELETE /portals/{id}` | **204**. The system stops calling the portal within a minute. |
| `POST /portals/{id}/refresh` | Answer **at once** with **202** and `{ "runId": "…" }`. If a read is already running, answer with that read's `runId`. May be called every few minutes. |
| `GET /portals/{id}/tenders` | **200**, a list of tenders. With `?changedSince=<time>`, only tenders whose `lastChangedAt` is later. |
| `GET /portals/{id}/runs` | **200**, a list of runs (below). |
| `GET /files/{fileId}` | The document's bytes (the path is referenced from `documents[].path`). |
| `GET /` | The dashboard (HTML). |

### Portal entry

| Field | Type |
|---|---|
| `id` | string |
| `url` | string |
| `state` | `integrating` \| `serving` \| `healing` \| `failed` |
| `reason` | string, in words |
| `tenderCount` | integer |
| `lastSuccessAt` | date-time with offset, or `null` |

### Portal states

| State | Meaning |
|---|---|
| `integrating` | Being attached. Nothing delivered yet. |
| `serving` | Delivering correctly. |
| `healing` | Broken; a repair is running. **Keep serving the last correct tenders.** |
| `failed` | Cannot deliver; a person is needed. A login or captcha block is `failed`, and `reason` says so. |

### Run

| Field | Type |
|---|---|
| `id` | string |
| `kind` | `read` \| `repair` (the first attach is a `read`) |
| `startedAt` | date-time with offset |
| `finishedAt` | date-time with offset, or `null` |
| `result` | `ok` \| `failed`, or `null` while running |

## Tender

Send `null` for a **required** field the portal doesn't show. Leave out **if-shown** fields the portal doesn't show.
**A wrong value is worse than a missing one.**

| Field | Req. | Meaning |
|---|---|---|
| `id` | required | The portal's own identifier for the tender. Stays the same on every read. |
| `url` | required | Address of the tender's page. |
| `title` | required | As the portal states it. |
| `buyerName` | required | The public body that buys. |
| `publicationDate` | required | A date, e.g. `2026-11-20`. |
| `submissionDeadline` | required | Date-time with offset. |
| `status` | required | `open`; `closed` once the deadline has passed or the portal no longer lists the tender; `withdrawn` when the buyer cancelled it. |
| `documents` | required | A list of `{ fileName, sizeBytes, sha256, path }`, where `path` is our API path to the bytes, e.g. `/files/ab12`. Unpack ZIP files. Files over 250 MB (ZIPs included) may be left out. |
| `lastChangedAt` | required | When our system last saw this tender change. |
| `referenceNumber` | if shown | The buyer's file number. |
| `description` | if shown | What is bought. |
| `procedureType` | if shown | `open` \| `restricted` \| `negotiated` \| `other` |
| `contractNature` | if shown | `works` \| `services` \| `supplies` |
| `cpvCodes` | if shown | A list of 8-digit strings **without** the check digit: `45000000`, not `45000000-7`. |
| `placeOfPerformance` | if shown | `{ postalCode, city, countryCode }`; `countryCode` is two letters, e.g. `DE`. |
