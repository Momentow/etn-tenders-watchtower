# 0015 — Agent v2: explorer and model tiers

- **Status:** accepted
- **Date:** 2026-11-21
- **Task:** T-031 (epic T-030); implemented by T-034 (explorer), T-035 (tiers), T-036 (completeness and blocks)
- **Amends:** [0010](0010-agent-runtime-and-tools.md) (budgets per tier, new tools), [0012](0012-agent-toolkit.md)
  (block detection rule). Scenario outcomes ([0009](0009-pipeline-and-agent-outcomes.md)) are unchanged.

## Context

In the first live run ([registry → Smoke runs](../portals/registry.md#smoke-runs)) the attach agent:

- gave up after 1–9 minutes on 11 of 12 portals; 6 of those could have been read;
- never clicked anything: its only browser tool renders one URL, and three JS apps timed out before their API calls
  appeared;
- reported `Blocked` after one tender's document link redirected to a login (dtvp.de, PLACE), though documents download
  anonymously after a session cookie or through an anonymous-download option;
- accepted a "latest 20" widget as the full list.

A person finds a portal's tenders by exploring: open the site, click *All tenders*, see "2,474 results", press *Load
more*, open a tender, look at its tabs. Watchtower should explore the same way, then write the cheapest template that
reproduces what it found ([ADR 0014](0014-template-format-v2.md)). Exploring and judging need a strong model; checking
40 tenders field by field doesn't.

## Decision

### Scenarios and outcomes stay

`generate_template` (attach) and `investigate` keep their signatures and ADR 0009 outcomes. They may now return v2
templates. Regular reads still make no model call.

### Explorer tools (T-034)

Each scenario run holds **one browser session** (ADR 0014 §1) for its whole life, created on first use and closed at
the end; tools act on it. All tools return compact text (as 0010).

| Tool | What it does |
|---|---|
| `open(url)` | `goto` in the run's session; returns the snapshot |
| `snapshot()` | Current page: headings, links/buttons by role and name, forms, repeated blocks with counts, visible totals |
| `act(action)` | Any ADR 0014 action (`click`, `fill`, `select`, `press`, `wait_for`, `load_more`, `scroll`, `tab`); returns what changed: URL, new snapshot summary, network calls it caused |
| `network(filter?)` | Exchanges so far (method, URL, status, type, size, a short body sample), newest first |
| `replay(exchange_id, changes?)` | Re-sends a captured call over `PoliteClient` (optionally with another page number or size) and compares the records with what the page showed. "Same data without a browser" is the signal to use `kind: api` |
| `try_template(template)` | Dry run of a v1 or v2 template (as 0010), now also reporting declared total vs. delivered, records dropped by `scope`, and whether pagination was exhausted |
| `detect_block(...)` | Rewritten (below) |

`fetch`, `render`, the toolkit analysis tools (0012) and the closing tools stay. Tools still reach portals only through
`client`/`browser`, so the 1 req/s rule holds for exploration too.

**Method** (in the attach prompt): find the page a person would use to see *all* tenders, and the portal's own total;
make the list complete (pagination, load more, scroll); open at least two tenders and their tabs; read the network log;
prefer an API that `replay` confirms; decide which notice types are open tenders (`scope`); then write and dry-run the
template.

### Model tiers (T-035)

| Tier | Env var | Default | Does |
|---|---|---|---|
| strong | `WATCHTOWER_MODEL_STRONG` | `WATCHTOWER_MODEL`, then the built-in default | Runs the scenario: explores, plans, writes templates, judges results, decides outcomes |
| light | `WATCHTOWER_MODEL_LIGHT` | the strong model | Narrow, repetitive tasks the strong model hands out |

`WATCHTOWER_MODEL` keeps working (it sets both). The two new variables go into the ADR 0001 table and the README when
T-035 lands.

**Light tasks are tools of the strong model** (`delegate_*`), each a single model call with a fixed prompt and a
structured output, run in parallel where they are independent:

| Light task | Input | Output |
|---|---|---|
| `map_fields` | a detail page's label/value pairs (toolkit) and the contract fields | `Extract` rules per field |
| `verify_tender` | one tender's page text and the values a dry run extracted | per field: right / wrong / not on the page, with the evidence |
| `classify_notices` | the distinct notice types in a list sample, with examples | which ones are open calls for competition |
| `describe_page` | a snapshot | where the tender list probably is and how to reach it |

Rules: the strong model reviews every light result before using it, and anything checkable is checked
deterministically first (a mapped rule is dry-run before it's believed). A light task never closes a scenario.

**Budgets per tier.** `Budget` becomes `TieredBudget(strong=Budget(...), light=Budget(...))`. Attach defaults: strong
40 turns / 600,000 tokens; light 60 calls / 400,000 tokens; 1,800 s wall time for the run (exploring at 1 req/s is
slow). Investigate keeps its current budget for strong and gets light 30 / 200,000. Running out still never raises
(0010).

`run_agent()` stays the single place that calls a model and now takes `tier`; usage is recorded per tier, so T-099
can show cost per portal and per tier.

### Truthful blocks (T-036)

`Blocked` (login or captcha) is accepted by `report_blocked` only when the run's evidence shows all of:
1. the block on content that matters (the list, or the documents) for **at least two different tenders**;
2. the attempt made **inside the session that loaded the list** (cookies kept);
3. any anonymous route the page offers tried (links or options such as "download anonymously", "sans
   identification", "anoniem");
4. or a strong signal on the entry page itself (the whole site is behind a login or challenge).

A constant HTTP 403 from a gateway or WAF on every path is reported as a block ("access refused by the portal's
firewall"), not as `report_transient`. Otherwise `report_blocked` answers with what's still missing, and the model goes on.

### Completeness before `serving` (T-036)

`submit_template` (and the pipeline's first-read validation) refuse a template when:
- the portal shows a total and the delivered count in scope is below it by more than 5 % (after `scope` drops);
- the list is a "latest N" view: no pagination while the page offers more, or a count equal to a page size with a
  link to a fuller list;
- the records carry a notice type field and `scope` doesn't use it.

The refusal says which check failed, so the model can fix it.

## Consequences

- **T-034** builds the explorer tools on T-032's session and teaches the attach prompt the method above; investigate
  reuses them in T-037.
- **T-035** adds the tier settings, `TieredBudget`, `delegate_*` tools and per-tier usage; it can be built in parallel
  with T-034 against the interfaces here.
- **T-036** owns the block rule and the completeness checks, in `validate` and the `report_blocked`/`submit_template`
  tools.
- A good attach costs more model calls than before (exploration plus light checks); a wrong `failed` or a wrong
  `serving` costs more with the judges. T-099 will show the price per portal.
- Tests keep using scripted models; a scripted run can now script light-tier answers too.
