# Mock portals

Local fake portals for testing Attach, Notice and Repair offline. They live in `mockportals/` (test infrastructure, never
production logic). Real portals are in the [registry](../portals/registry.md); the mocks copy their quirks.

```bash
uv run python -m mockportals                         # all portals on http://localhost:8081 (index at /)
MOCK_VARIANTS="ssr=markup,js=renamed-keys" uv run python -m mockportals
MOCK_PORT=9000 MOCK_TODAY=2026-12-01 uv run python -m mockportals
```

The [example templates](../decisions/0003-template-format.md) (`watchtower/engine/examples/*.json`) point at these
portals; `tests/mockportals/` checks that their selectors read the mocks correctly.

## Portals

| Kind | Entry URL | Country / tz | Example template | Expected state | What it tests |
|---|---|---|---|---|---|
| `ssr` | `/ssr/` → `/ssr/ausschreibungen` | DE, Europe/Berlin | `ssr_list_detail` | `serving` | List table + detail pages, `rel=next` pagination (23 tenders, 10 per page) |
| `js` | `/js/` (empty shell) → `/js/api/consultations` | FR, Europe/Paris | `js_json_api` | `serving` | JS app whose script calls a JSON API (find it in captured XHR, then read it directly); 54 tenders |
| `multistep` | `/multistep/` → `/multistep/licitaciones` | ES, Europe/Madrid | `multi_step_download` | `serving` | Summary-page trap, per-document download pages, a "download all" ZIP, a 300 MB file |
| `form` | `/form/` → `POST /form/przetargi` | PL, Europe/Warsaw | `search_form` | `serving` | List behind a POSTed search form, paged by a form field |
| `captcha` | `/captcha/` → `/captcha/aanbestedingen` | NL, Europe/Amsterdam | `captcha_bypassable` | `serving` | Captcha widget shown, but list (built by JS) and documents work without it |
| `login` | `/login/` → `/login/ausschreibungen` | AT, Europe/Vienna | — | `failed` | Public list, documents redirect to a login form (like provia.at) |
| `robot` | `/robot/` → `/robot/opdrachten` | BE, Europe/Brussels | — | `failed` | Documents behind a robot check that fails without solving (like cloud.3p.eu) |
| `app` | `/app/` → click *Alle aankondigingen* → `/app/aankondigingen` (SPA) | NL, Europe/Amsterdam | `v2/app_api`, `v2/app_click` | `serving` | Engine v2 (like tenderned.nl): click-through, *Laad meer*, detail tabs, a JSON API behind every page, mixed notice types (30 notices, 21 open calls for competition) |

### Built-in quirks (from real portals)
- Deadlines in local time without an offset (`29.12.2026 10:00`, `24/11/2026 12:00`); the expected value has the offset.
- `;jsessionid=…` in SSR detail links, different on every response: the tender `url` must not depend on it.
- CPV codes printed with a check digit (`45212225-1`); the contract wants `45212225`.
- One SSR tender whose documents are "only for registered companies" (no links): `documents` is `[]`, not a failure.
- Withdrawn (`aufgehoben`, `ANNULEE`) and closed (`abgelaufen`, `CLOTUREE`) tenders.
- ZIP documents contain two PDFs and a nested ZIP (`Anlagen.zip` → `Anlage/Plan.pdf`): unpack recursively.
- `multistep` lists a 300 MB `Planos.zip` (`Content-Length` known up front, body streamed lazily): may be skipped.
- `multistep` also lists an `Ayuda.html` link among the documents: it isn't a file (the example excludes `.html`).
- File bytes are deterministic, so sha256 values are stable across runs.
- `app` lists 30 notices but only 21 are open tenders. Leave out 6 award notices (`AGO`), 1 prior notice (`VAK`), 1
  call ended early (`isVroegtijdigeBeeindiging`) and 1 call whose deadline passed. `expected_tenders("app")` gives the 21
  that a scoped v2 read delivers. Every `app` page is a JS shell: the list API is `/app/api/publicaties?page=&size=`,
  filterable by `publicatieType` and `sluitingsDatumVanaf`, and the documents load only when the *Documenten* tab is
  clicked.

## Variants (mutations)

Switch a running portal to see whether the system notices and repairs:

```bash
curl -X POST localhost:8081/_mock/variants/ssr -H 'content-type: application/json' -d '{"variant": "markup"}'
curl localhost:8081/_mock/variants        # current, available, entry URLs, expected states
```

| Variant | Portals | What changes | What a correct system does |
|---|---|---|---|
| `markup` | ssr, multistep | Class names and labels change (`table.tenders` → `table.ergebnisse`, `Angebotsfrist` → `Ablauf der Angebotsfrist`, `div.licitacion` → `article.expediente-card`) | Read fails (zero tenders) → `healing` → repair → `serving` |
| `renamed-keys` | js | JSON keys renamed (`resultats` → `items`, `objet` → `intitule`, `acheteur.nom` → `organisme.libelle`, `dateLimiteRemise` → `dateLimite`) | Same |
| `pagination` | ssr, js, form | Pagination moves (`?start=` offsets; `offset` instead of `page`; `?page=` instead of the form field) | Sharp drop in tender count → repair |
| `silent-missed` | ssr, js | 3 new tenders appear where the template doesn't look (a second table; only with `etat=tous`); still parses | Only a proactive audit catches it |
| `silent-swap` | ssr | The buyer column shows the city; still parses, values wrong | Only an audit catches it |
| `relabelled` | app | The link, button and tab are renamed (*Zoeken in publicaties*, *Toon meer resultaten*, *Stukken*); the API is unchanged | A browser flow on the old names fails → repair the flow step (or switch to the API) |
| `api-moved` | app | The API moves to `/app/api/v3/…`, and the old paths answer 404; the pages use the new API | An API-mode read fails → repair the endpoint |
| `gone` | all | Every page answers 410 | `failed` (gone) |
| `login-wall` | all | Every page redirects to a login form | `failed` (blocked by login) |

The variant endpoint is test infrastructure for the demo and tests (it stands in for "the portal changed"); Watchtower
itself never calls it.

## In tests

```python
from mockportals import create_app, expected_tenders, ENTRY, EXPECTED_STATE, VARIANTS
from mockportals.testing import serve

# No socket: an ASGI transport with the address the example templates use.
transport = httpx.ASGITransport(app=create_app({"ssr": "markup"}))
client = httpx.AsyncClient(transport=transport, base_url="http://localhost:8081")

# A real server (for the engine through PoliteClient, or the browser):
with serve(port=8081) as s:
    s.app.state.variants["js"] = "renamed-keys"

expected_tenders("ssr")                    # contract-shaped ground truth for a correct read
expected_tenders("js", "silent-missed")    # what the portal really holds in that variant
```

`expected_tenders()` gives `id`, `title`, `buyerName`, `publicationDate`, `submissionDeadline` (ISO with offset),
`status`, `documents` (file names after unpacking, without the 300 MB file) and the optional fields each portal shows.
`url` is `None` because it depends on how the engine follows links.
