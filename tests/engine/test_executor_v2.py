"""The v2 executor (ADR 0014, T-033) on the `app` mock portal, through its API and through a browser flow, and on
small stub sites for sessions, row groups, scope and URL normalisers."""

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from mockportals import data
from mockportals.app import create_app
from watchtower.engine import executor_v2, run_template
from watchtower.engine.examples import _DIR_V2, load_example_v2
from watchtower.engine.executor_v2 import _clean_url, _count
from watchtower.engine.template_v2 import TemplateV2, UrlRules
from watchtower.net import BrowserPool, HostRateLimiter, PoliteClient, SessionBudget

COMPARED = [
    "id", "title", "buyerName", "publicationDate", "submissionDeadline", "status", "referenceNumber", "description",
    "procedureType", "contractNature", "cpvCodes", "placeOfPerformance",
]  # fmt: skip


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


browser_only = pytest.mark.skipif(not _chromium_installed(), reason="Playwright Chromium not installed")


def example(name: str, base: str = "http://localhost:8081") -> dict:
    return json.loads((_DIR_V2 / f"{name}.json").read_text(encoding="utf-8").replace("http://localhost:8081", base))


def mock_client(variants: dict[str, str] | None = None) -> PoliteClient:
    return PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app(variants)))


def assert_exact(result, kind: str = "app") -> None:
    assert result.errors == []
    got = {t.id: t.contract_data() for t in result.tenders}
    expected = {t["id"]: t for t in data.expected_tenders(kind)}
    assert list(got) == list(expected)  # every open call, in portal order, nothing else
    for tid, exp in expected.items():
        for field in COMPARED:
            assert got[tid].get(field) == exp.get(field), f"{tid}.{field}"
    docs = {t.id: [d.fileName for d in t.documents] for t in result.tenders}
    assert docs == {t.id: [d.name for d in t.docs] for t in data.portal(kind).tenders if t.id in expected}
    assert all(t.complete and t.url and t.url.endswith(f"/app/aankondigingen/{t.id}") for t in result.tenders)


# ---------------------------------------------------------------- API mode: no browser


async def test_api_template_reads_every_open_call_exactly():
    async with mock_client() as client:
        result = await run_template(load_example_v2("app_api"), client, None)
    assert_exact(result)
    # The API's own filters keep 22 calls with a deadline from today; scope drops the one that was ended early.
    assert result.declared_total == 22 and result.scope_dropped == 1
    assert result.snapshot["kind"] == "json"


async def test_api_template_limit_is_a_dry_run():
    async with mock_client() as client:
        result = await run_template(load_example_v2("app_api"), client, None, limit=3)
    assert len(result.tenders) == 3 and result.stats.list_pages == 1 and result.stats.detail_pages == 3


async def test_api_moved_breaks_the_api_template_truthfully():
    async with mock_client({"app": "api-moved"}) as client:
        result = await run_template(load_example_v2("app_api"), client, None)
    assert result.tenders == [] and "HTTP 404" in result.errors[0]


async def test_today_placeholder_is_filled_in_the_template_zone():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("sluitingsDatumVanaf"))
        return httpx.Response(200, json={"content": [], "totalElements": 0})

    async with PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(handler)) as client:
        result = await run_template(load_example_v2("app_api"), client, None)
    assert result.tenders == [] and result.declared_total == 0
    assert seen and date.fromisoformat(seen[0]) in {date.today() + timedelta(days=d) for d in (-1, 0, 1)}


# ---------------------------------------------------------------- browser flow


@pytest.fixture
def fast_sessions(monkeypatch):
    """The mock settles instantly; a short idle wait keeps the browser tests quick."""
    monkeypatch.setattr(executor_v2, "READ_SESSION_BUDGET", SessionBudget(active_seconds=300, idle_ms=300))


@pytest.fixture
def served():
    from mockportals.testing import serve

    with serve(port=18084) as s:
        yield s


async def browser_read(template: TemplateV2, **kw):
    limiter = HostRateLimiter(0)
    browser = BrowserPool(limiter=limiter)
    try:
        async with PoliteClient(limiter=limiter) as client:
            return await run_template(template, client, browser, **kw)
    finally:
        await browser.aclose()


@browser_only
async def test_click_template_reads_like_a_person(served, fast_sessions):
    """Home → 'Alle aankondigingen' → 'Laad meer' until gone → each notice, then its 'Documenten' tab."""
    result = await browser_read(TemplateV2.model_validate(example("app_click", served.url)))
    assert_exact(result)
    assert result.declared_total == 30  # "30 resultaten": every notice type
    assert result.scope_dropped == 9  # 6 award notices, 1 prior notice, 1 early termination, 1 passed deadline
    assert result.stats.list_pages == 1 and not result.stats.hit_max_pages


@browser_only
async def test_relabelled_controls_break_the_flow_truthfully(served, fast_sessions):
    served.app.state.variants["app"] = "relabelled"
    result = await browser_read(TemplateV2.model_validate(example("app_click", served.url)), limit=2)
    assert result.tenders == []
    assert "list.flow step 1" in result.errors[0] and "Alle aankondigingen" in result.errors[0]


@browser_only
async def test_browser_session_captures_a_header_for_api_reads(served, fast_sessions):
    """The dtvp.de shape: a browser boot yields a value the page's own API calls carry; then plain HTTP."""
    d = example("app_api", served.url)
    d["session"] = {
        "via": "browser",
        "steps": [
            {"do": "goto", "url": served.url + "/app/aankondigingen"},
            {"do": "wait_for", "response": "/app/api/publicaties"},
        ],
        "capture": [{"name": "accept", "from": "request_header", "match": "/app/api/publicaties", "key": "Accept"}],
    }
    d["list"]["request"]["headers"] = {"Accept": "{{session.accept}}"}
    result = await browser_read(TemplateV2.model_validate(d), limit=3)
    assert result.errors == [] and len(result.tenders) == 3

    d["session"]["capture"][0]["key"] = "X-JWT"  # the page never sends one
    result = await browser_read(TemplateV2.model_validate(d), limit=3)
    assert result.tenders == [] and "anonymous session failed" in result.errors[0]


# ---------------------------------------------------------------- stub sites


def stub(pages: dict, seen: list | None = None) -> PoliteClient:
    """URL (without query) → (status, content type, body, headers); records requests in `seen`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        key = str(request.url.copy_with(query=None))
        status, ctype, body, headers = pages.get(key, (404, "text/html", "not found", {}))
        return httpx.Response(status, headers={"content-type": ctype, **headers}, text=body)

    return PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(handler), max_attempts=1)


def page_template(**over) -> dict:
    t = {
        "format_version": 2,
        "timezone": "Europe/Berlin",
        "entry": {"url": "https://p.example/list"},
        "list": {"kind": "page", "via": "http", "items": {"by": "css", "expr": "table tr"}},
        "fields": {
            "id": {"by": "css", "expr": "td.id"},
            "url": {"by": "css", "expr": "a", "attr": "href"},
            "title": {"by": "css", "expr": "a"},
            "submissionDeadline": {"by": "css", "expr": "td.frist", "formats": ["%d.%m.%Y %H:%M"]},
        },
    }
    t.update(over)
    return t


FUTURE = (date.today() + timedelta(days=30)).strftime("%d.%m.%Y")
PAST = (date.today() - timedelta(days=3)).strftime("%d.%m.%Y")
ROWS = f"""<table>
<tr class="head"><td class="id">A-1</td><td><a href="/t/1;jsessionid=XYZ?4">Erste</a></td></tr>
<tr class="more"><td class="frist">{FUTURE} 10:00</td><td class="art">Ausschreibung</td></tr>
<tr class="head"><td class="id">A-2</td><td><a href="/t/2">Zweite</a></td></tr>
<tr class="more"><td class="frist">{PAST} 10:00</td><td class="art">Ausschreibung</td></tr>
<tr class="head"><td class="id">A-3</td><td><a href="/t/3">Dritte</a></td></tr>
<tr class="more"><td class="frist">{FUTURE} 12:00</td><td class="art">Vergabebekanntmachung</td></tr>
</table>"""


@pytest.mark.parametrize("group", [{"size": 2}, {"starts_at": "tr.head"}])
async def test_row_groups_scope_and_url_normalisers(group):
    t = page_template(
        scope={"include": [{"source": {"by": "css", "expr": "td.art"}, "equals": "Ausschreibung"}]},
        urls={"drop_numeric_query": True},
    )
    t["list"]["items"]["group"] = group
    async with stub({"https://p.example/list": (200, "text/html", ROWS, {})}) as client:
        result = await run_template(TemplateV2.model_validate(t), client, None)
    assert result.errors == []
    # A-2's deadline has passed (open_by_deadline), A-3 is an award notice (scope): both dropped, never returned.
    assert [x.id for x in result.tenders] == ["A-1"] and result.scope_dropped == 2
    assert result.tenders[0].url == "https://p.example/t/1"
    assert result.tenders[0].submissionDeadline.startswith((date.today() + timedelta(days=30)).isoformat())


async def test_open_by_deadline_can_be_turned_off():
    t = page_template(scope={"open_by_deadline": False})
    t["list"]["items"]["group"] = {"size": 2}
    async with stub({"https://p.example/list": (200, "text/html", ROWS, {})}) as client:
        result = await run_template(TemplateV2.model_validate(t), client, None)
    assert [x.id for x in result.tenders] == ["A-1", "A-2", "A-3"] and result.scope_dropped == 0


async def test_deadline_read_on_the_detail_page_is_scoped_after_the_detail():
    rows = (
        "<ul><li><span class='id'>1</span><a href='/d/1'>x</a></li>"
        "<li><span class='id'>2</span><a href='/d/2'>y</a></li></ul>"
    )
    detail = "<p class='frist'>{} 09:00</p>"
    t = page_template(
        list={"kind": "page", "via": "http", "items": {"by": "css", "expr": "li"}},
        detail={"kind": "page", "link": {"by": "css", "expr": "a", "attr": "href"}},
        fields={
            "id": {"by": "css", "expr": "span.id"},
            "submissionDeadline": {"by": "css", "expr": "p.frist", "on": "detail", "formats": ["%d.%m.%Y %H:%M"]},
        },
    )
    pages = {
        "https://p.example/list": (200, "text/html", rows, {}),
        "https://p.example/d/1": (200, "text/html", detail.format(FUTURE), {}),
        "https://p.example/d/2": (200, "text/html", detail.format(PAST), {}),
    }
    async with stub(pages) as client:
        result = await run_template(TemplateV2.model_validate(t), client, None)
    assert [x.id for x in result.tenders] == ["1"] and result.scope_dropped == 1
    assert result.tenders[0].url == "https://p.example/d/1"


async def test_http_session_captures_a_token_and_a_cookie():
    """The BOSA shape: the portal's own page fetches an anonymous token first; requests then carry it."""
    seen: list[httpx.Request] = []
    api = {"items": [{"id": "7", "title": "T", "url": "https://p.example/t/7", "deadline": None}], "total": 1}
    pages = {
        "https://p.example/token": (
            200,
            "application/json",
            '{"access_token": "abc"}',
            {"set-cookie": "sid=s1; Path=/"},
        ),
        "https://api.p.example/tenders": (200, "application/json", json.dumps(api), {}),
    }
    t = {
        "format_version": 2,
        "timezone": "Europe/Brussels",
        "entry": {"url": "https://p.example/"},
        "session": {
            "via": "http",
            "steps": [
                {"url": "https://p.example/token", "method": "POST", "form": {"grant_type": "client_credentials"}}
            ],
            "capture": [
                {"name": "token", "from": "response_json", "match": "/token", "key": "$.access_token"},
                {"name": "cookies", "from": "cookie", "match": "p\\.example", "key": "*"},
            ],
        },
        "list": {
            "kind": "api",
            "request": {
                "url": "https://api.p.example/tenders",
                "headers": {"Authorization": "Bearer {{session.token}}", "Cookie": "{{session.cookies}}"},
            },
            "items": "$.items[*]",
            "total": "$.total",
        },
        "fields": {"id": {"by": "jsonpath", "expr": "$.id"}, "url": {"by": "jsonpath", "expr": "$.url"}},
    }
    async with stub(pages, seen) as client:
        result = await run_template(TemplateV2.model_validate(t), client, None)
    assert result.errors == [] and [x.id for x in result.tenders] == ["7"] and result.declared_total == 1
    list_call = next(r for r in seen if r.url.host == "api.p.example")
    assert list_call.headers["authorization"] == "Bearer abc" and "sid=s1" in list_call.headers["cookie"]
    assert "abc" not in json.dumps(result.model_dump())  # session values never leave the read


async def test_missing_session_value_stops_the_read_with_a_reason():
    t = {
        "format_version": 2,
        "timezone": "Europe/Brussels",
        "entry": {"url": "https://p.example/"},
        "session": {
            "via": "http",
            "steps": [{"url": "https://p.example/token"}],
            "capture": [{"name": "token", "from": "response_json", "match": "/token", "key": "$.access_token"}],
        },
        "list": {
            "kind": "api",
            "request": {"url": "https://p.example/x", "params": {"t": "{{session.token}}"}},
            "items": "$[*]",
        },  # fmt: skip
        "fields": {"id": {"by": "jsonpath", "expr": "$.id"}, "url": {"by": "jsonpath", "expr": "$.url"}},
    }
    async with stub({"https://p.example/token": (200, "application/json", "{}", {})}) as client:
        result = await run_template(TemplateV2.model_validate(t), client, None)
    assert result.tenders == [] and "anonymous session failed" in result.errors[0]


async def test_needs_a_browser_when_none_is_given():
    result = await run_template(load_example_v2("app_click"), mock_client(), None)
    assert result.tenders == [] and "needs a browser" in result.errors[0]


# ---------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    "text, n",
    [
        ("2.474 resultaten", 2474),
        ("1,234 results", 1234),
        ("2 474", 2474),
        ("147334", 147334),
        ("geen", None),
        (None, None),
    ],
)
def test_count(text, n):
    assert _count(text) == n


def test_clean_url():
    rules = UrlRules(drop_params=["sid"], drop_numeric_query=True)
    assert _clean_url("https://p/x;jsessionid=AB12?4", rules) == "https://p/x"
    assert _clean_url("https://p/x;JSESSIONID=AB12/y?sid=1&a=2", rules) == "https://p/x/y?a=2"
    assert _clean_url("https://p/x?4-1.ILinkListener-list&id=9", rules) == "https://p/x?id=9"
    assert _clean_url("https://p/x?page=4", rules) == "https://p/x?page=4"
