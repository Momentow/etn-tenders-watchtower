# Agent toolkit

The deterministic tools the agent calls while attaching or repairing a portal ([ADR 0002 → toolkit](../decisions/0002-architecture.md#agent-toolkit-deterministic-first),
[ADR 0012](../decisions/0012-agent-toolkit.md)). The model makes the judgement calls; these tools compute selectors,
pagination, field rules and verdicts, **check them on the live page**, and answer with short text the model can paste
into a template. The core tools (`fetch`, `render`, `try_template`, `submit_template`, `report_*`) are described in
[agent runtime](runtime.md).

All tools read pages through the per-run cache in `watchtower/agent/pages.py`, so politeness (`net`) applies and a
page looked at twice costs one request. The analysis behind them is pure code in `watchtower/agent/analysis/` (no I/O),
built on the engine's own parser and CSS implementation, so a selector a tool suggests is one the engine runs.

## Discovery

| Tool | What it answers |
|---|---|
| `find_repeating_blocks(url)` | Candidate tender lists: groups of ≥ 3 same-shaped siblings scored by text, links and dates (page chrome and menus ranked down). Gives a verified `list.items` selector, the record count, and the values in the first record with selectors relative to it. |
| `find_pagination(url, items?)` | How the list pages, **proved live**. A `rel=next` or "next" link → `next_link`. Otherwise it probes likely page parameters (names from the page's links and form fields, then by page language: `seite`, `pagina`, `strona`, `page`, `start`/`offset`…; size parameters such as `taille` are skipped) and accepts one when page 2 lists new records. Then it checks whether pages count from 0 or 1. Answers with the `pagination` JSON. At most 5 probes plus one. |
| `classify_links(url)` | Every link grouped as **detail**, **document**, **pagination** or other, with the URL pattern (ids as `{}`, document names as `{file}`), a selector and samples. Documents are recognised by extension, `download` attribute and multilingual words (Vergabeunterlagen, DCE, pliego, dokumentacja, bijlage…); words alone don't count in the header, nav or footer. |
| `resolve_tender_page(url)` | Is this the tender page or a notice/summary page (a known trap)? Scores the page and its "see the full tender" links by labelled fields, dates and document links; a much richer onward page → the `detail.resolve` step. |
| `summarize_json(url, items)` | Per key under the records of a JSON API: type, how many records have it, distinct values, samples (nested keys as `parent.child`). |

## Recognition

| Tool | What it answers |
|---|---|
| `fingerprint_software(url)` | The portal package from URL, `meta generator`, footer and markup (about 30 procurement packages plus web platforms), a country guess from domain and page language, and whether a template for that software is known. |
| `find_template_for_software(software)` | A known template for the same software with its URLs moved to this portal's host, to dry-run and adjust. Templates come from `AgentContext.known_templates` (the scenario fills it). |
| `map_field_labels(url)` | Label/value pairs (`dt`/`dd`, `th`/`td`, two-cell rows, `<b>Label:</b> value`, `Label: value`) mapped to contract fields with a multilingual synonym table, each with a ready XPath rule (and `pattern` when the label is in the same text). |
| `probe_dates(samples, country?)` | strptime `formats` that read every sample, the time zone of the country, what the engine makes of each sample (ISO with offset), and a warning when day and month are ambiguous. |
| `detect_block(url)` | Login/captcha signals on the page **and** a download of a document linked from it: documents that download → NOT BLOCKED even with a captcha widget; a document that lands on a login or captcha page → BLOCKED HERE, with how many tenders are blocked so far (a block needs two; see [truthful blocks](blocks.md)). |

## Verification

| Tool | What it answers |
|---|---|
| `test_selector(url, by, expr, attr, pattern, items?)` | Runs one rule exactly as the engine does (css, xpath, jsonpath, regex; attributes; pattern), inside each list record when `items` is given, else on the whole page. Values per scope and how many scopes come out empty. |
| `probe_document(url)` | The first bytes of a document link: content type, size, file name, or what it is instead (a download page, a login or captcha page, an error). On a download page it gives the `documents.per_link` step to the file. |
| `try_template` | Core tool: the full dry run with field coverage, document check and warnings. |

## Repair (investigate only)

| Tool | What it answers |
|---|---|
| `diff_outputs(new_template, old_template?)` | Reads the first tenders with two templates (default old: the template in use, `ctx.extra["template"]`) and compares: ids only one finds, and per field how many shared tenders differ, with an example. |

The structure diff against the last good snapshot (`diff_structure`), `show_read` and `count_list` belong to the
investigate scenario ([investigate](../repair/investigate.md)).

## Adding a tool

Add a module to `watchtower/agent/tools/` with an `@tool(scenarios=…)` async function (see [agent runtime](runtime.md#adding-tools)).
Put the computation in `watchtower/agent/analysis/` as a pure function with a unit test, and keep the tool a thin wrapper
that loads pages through `agent/pages.py` and formats the answer. Tests: `tests/agent/test_toolkit.py` calls every
tool through the SDK interface on the mock portals.
