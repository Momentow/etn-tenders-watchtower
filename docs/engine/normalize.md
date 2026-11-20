# Normalisers (`watchtower.engine.normalize`, `watchtower.engine.dates`)

They turn the raw strings a template extracts into contract values. The executor calls `normalize_fields(raw,
template)`; the pipeline calls `content_hash(tender)`. **A value we can't read with certainty becomes `null` (required
fields) or is left out (optional ones).**

## Dates ([ADR 0007](../decisions/0007-date-parsing.md))
Tried in order:
1. **The template's `formats`** (strptime). Local month names (DE, FR, ES, NL, PL, IT, PT, EN, including Polish
   genitive forms such as *grudnia*) are translated first, so `%B` / `%b` work.
2. **ISO 8601**: `2026-12-09`, `2026-12-09T10:00:00+01:00`, `…Z`, `2026-12-09 10:00`.
3. **Common European forms, always day-first:**
   - numeric dates: `09.12.2026`, `09/12/2026`, `09-12-26`;
   - written dates: `9. Dezember 2026`, `9 de diciembre de 2026`, `9 grudnia 2026 r.`, `1er juillet 2026`;
   - times: `10:00`, `10.00`, `10h00`, `10 Uhr`, `godz. 10:00`, `3:00 pm`, and `24:00` (the end of that day).

Rules:
- A time without an offset is local to the template's `timezone`, DST-aware. An offset, `Z`, `UTC` or `GMT` in the
  text wins.
- `publicationDate` becomes a date. A date-time is first converted to the portal's local date.
- A `submissionDeadline` with no time of day becomes `null`, because we can't state the time truthfully.
- Impossible values (32nd day, 25:00) and years outside 2000–2100 become `null`.
- Only the first date in a text counts. A later one (`bis 15.12.2026`) is never read as a time.

## Other fields
| Field | Rule |
|---|---|
| text fields | Whitespace collapsed, non-breaking and zero-width characters removed, empty → `null` |
| `url` | `;jsessionid=…` removed |
| `cpvCodes` | 8-digit codes, check digit (`-7`) stripped, in order, duplicates removed; 9-digit numbers ignored |
| `procedureType` | A contract value as-is, else multilingual phrases: *Offenes Verfahren*, *procédure ouverte*, *procedimiento abierto*, *openbare procedure*, *przetarg nieograniczony* → `open`; restricted, negotiated (*Freihändige Vergabe*, *procédure négociée*…) and `other` (competitive dialogue, innovation partnership, MAPA). Unknown → left out |
| `contractNature` | Same approach: *Bauleistung / travaux / obras / werken / roboty budowlane* → `works`, services, supplies. Unknown → left out |
| `countryCode` | ISO alpha-2 from a code (`de`, `DEU`, EU shorthand `EL`→`GR`, `UK`→`GB`) or a country name in several languages; unknown → left out |
| `postalCode` | As shown, minus a country prefix (`D-50667` → `50667`) |

A template's `map` still runs first. These rules only catch labels it didn't map.

## Content hash
`content_hash(tender)` is the sha256 of the contract fields (status excluded: the store compares status on its own) and
the documents as a set of (URL, name).
- Document URLs lose session ids and cache-busting parameters (`sid`, `jsessionid`, `_`, `t`, `token`…), and their
  query parameters are sorted.
- Unchanged tenders hash the same on every read. Any change in content, including a new document version, changes
  the hash and so `lastChangedAt`.

Tests: `uv run pytest tests/engine/test_normalize.py` (DE, FR, ES, NL and PL formats, DST, enums, countries, hash).
