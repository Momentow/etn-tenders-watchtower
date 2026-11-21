"""Completeness (T-036): a partial read is never stored and never promoted to `serving`.

- The evergabe.nrw.de regression: a home-page "latest 20" box read as if it were every open tender.
- The `app` mock portal read without its notice-type filter, or only its first API page.
- The evidence (`engine/completeness.py`) and the thresholds (`validate/completeness.py`).
"""

import json
from pathlib import Path

import httpx
import pytest

from mockportals import data
from mockportals.app import create_app
from watchtower.agent.outcomes import TemplateFound
from watchtower.core import pipeline
from watchtower.core.pipeline import Scenarios, handle_attach
from watchtower.core.scheduler import JobContext
from watchtower.core.state_machine import StateMachine
from watchtower.core.store import Store
from watchtower.engine import ReadResult, run_template
from watchtower.engine.completeness import more_offered, other_notice
from watchtower.engine.examples import _DIR_V2
from watchtower.engine.extract import parse_html, parse_json, select
from watchtower.engine.result import ReadStats, TenderRecord
from watchtower.engine.template import Extract, Template
from watchtower.engine.template_v2 import TemplateV2
from watchtower.net import HostRateLimiter, PoliteClient
from watchtower.validate import check
from watchtower.validate.completeness import completeness_problems

FIXTURES = Path(__file__).parent / "fixtures"
EVERGABE = "https://www.evergabe.nrw.de/VMPCenter/company/welcome.do"

# What the first smoke run's attach effectively did on evergabe.nrw.de: the home page's table, no pagination.
EVERGABE_WIDGET = Template.model_validate(
    {
        "timezone": "Europe/Berlin",
        "country": "DE",
        "list": {
            "kind": "html",
            "request": {"url": EVERGABE},
            "items": {"by": "css", "expr": "#PROJECT_RESULT tbody tr"},
        },
        "fields": {
            "id": {"by": "css", "expr": "a.noTextDecorationLink", "attr": "href", "pattern": "pid=(\\d+)"},
            "url": {"by": "css", "expr": "a.noTextDecorationLink", "attr": "href"},
            "title": {"by": "css", "expr": "td.word-break"},
            "buyerName": {"by": "css", "expr": "td:nth-child(5)"},
            "publicationDate": {
                "by": "css", "expr": "td:nth-child(1) abbr", "attr": "title", "formats": ["%d.%m.%Y um %H:%M Uhr"]
            },
            "submissionDeadline": {
                "by": "css", "expr": "td:nth-child(2) abbr", "attr": "title", "formats": ["%d.%m.%Y um %H:%M Uhr"]
            },
        },
    }
)  # fmt: skip


def evergabe_client() -> PoliteClient:
    html = (FIXTURES / "evergabe_home_widget.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == EVERGABE:
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=html)
        return httpx.Response(404)

    return PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(handler), max_attempts=1)


def app_api(change=None) -> TemplateV2:
    d = json.loads((_DIR_V2 / "app_api.json").read_text(encoding="utf-8"))
    if change:
        change(d)
    return TemplateV2.model_validate(d)


def _unfiltered(d: dict) -> None:
    del d["list"]["request"]["params"]["publicatieType"]
    d["scope"] = {}


def _first_page_only(d: dict) -> None:
    d["list"]["pagination"] = {"kind": "none"}


# ---------------------------------------------------------------- the evergabe regression


async def test_evergabe_latest_box_fails_validation():
    async with evergabe_client() as client:
        result = await run_template(EVERGABE_WIDGET, client, None)
    assert result.errors == [] and len(result.tenders) == 20  # the read itself looks fine…
    problems = check(result).problems
    excerpt = next(p for p in problems if "offers more" in p)  # …but it is an excerpt
    assert "'Alle Bekanntmachungen'" in excerpt and "categoryOverview.do" in excerpt
    notices = next(p for p in problems if "other notice types" in p)  # and it mixes in awards and intended tenders
    assert notices.startswith("7 of 20 tenders")


async def test_evergabe_latest_box_is_never_promoted(tmp_path):
    store = await Store.open(tmp_path / "w.db")
    saved = pipeline._config
    client = evergabe_client()

    async def generate_template(url):
        return TemplateFound(template=EVERGABE_WIDGET, notes="the home page lists tenders")

    pipeline._config = pipeline._Config(client=client, scenarios=Scenarios(generate_template, None))
    try:
        p, _ = await store.add_portal("https://www.evergabe.nrw.de")
        await attach(store, p.id)
        portal = await store.get_portal(p.id)
        assert portal.state == "failed" and portal.current_template_version is None
        assert "offers more" in portal.reason
        assert await store.list_tenders(p.id) == []
    finally:
        pipeline._config = saved
        await client.aclose()
        await store.close()


async def attach(store, pid):
    run_, _ = await store.request_run(pid, "attach", trigger="attach")
    job = next(j for j in await store.active_jobs(pid) if j.run_id == run_.id)
    await handle_attach(JobContext(store, StateMachine(store), job))
    await store.finish_job(job.id)
    return await store.get_run(run_.id)


# ---------------------------------------------------------------- the app mock portal through the pipeline


@pytest.fixture
async def world(tmp_path):
    store = await Store.open(tmp_path / "w.db")
    client = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app()))
    saved = pipeline._config
    templates: list = []

    async def generate_template(url):
        # model_construct: TemplateFound still types v1 templates until the agent writes v2 (T-034); the pipeline
        # itself stores and runs either format.
        return TemplateFound.model_construct(template=templates.pop(0), notes="learned")

    pipeline._config = pipeline._Config(client=client, scenarios=Scenarios(generate_template, None))
    yield store, templates
    pipeline._config = saved
    await client.aclose()
    await store.close()


@pytest.mark.parametrize(
    "change, reason",
    [
        (_unfiltered, "other notice types, not calls for competition"),
        (_first_page_only, "The portal says its list holds 22, but the read reached only 10"),
    ],
)
async def test_partial_app_reads_end_failed_not_serving(world, change, reason):
    store, templates = world
    templates.append(app_api(change))
    p, _ = await store.add_portal("http://localhost:8081/app/")
    r = await attach(store, p.id)
    portal = await store.get_portal(p.id)
    assert portal.state == "failed" and reason in portal.reason
    assert r.result == "failed" and await store.list_tenders(p.id) == []


async def test_v1_json_list_without_pagination_ends_failed(world):
    """The js mock's API answers 50 of 54 consultations per page and says so in `total`."""
    from watchtower.engine.examples import load_example

    store, templates = world
    d = load_example("js_json_api").model_dump(mode="json", exclude_none=True)
    d["list"]["pagination"] = {"kind": "none"}
    templates.append(Template.model_validate(d))
    p, _ = await store.add_portal("http://localhost:8081/js/")
    await attach(store, p.id)
    portal = await store.get_portal(p.id)
    assert portal.state == "failed" and '"total": 54 while the response holds 50' in portal.reason


async def test_complete_app_read_is_served(world):
    store, templates = world
    templates.append(app_api())
    p, _ = await store.add_portal("http://localhost:8081/app/")
    await attach(store, p.id)
    portal = await store.get_portal(p.id)
    assert portal.state == "serving"
    assert len(await store.list_tenders(p.id)) == len(data.expected_tenders("app"))


async def test_unfiltered_read_names_the_problem_and_its_evidence():
    async with PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app())) as client:
        result = await run_template(app_api(_unfiltered), client, None)
    # 30 notices: 6 awards and a prior notice are flagged; the early termination is a call labelled as such only
    # by a flag, and the passed deadline is dropped by open_by_deadline.
    assert sorted(result.other_notices.values()) == ["Aankondiging gegunde opdracht"] * 6 + ["Vooraankondiging"]
    assert result.declared_total == 29 and result.scope_dropped == 0  # the API's date filter is still on


async def test_dry_runs_are_not_judged_on_completeness():
    async with PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app())) as client:
        result = await run_template(app_api(), client, None, limit=3)
    assert result.stats.limited and check(result).ok


# ---------------------------------------------------------------- thresholds


def tender(i: int) -> TenderRecord:
    return TenderRecord(
        id=str(i),
        url=f"https://p/{i}",
        title="t",
        buyerName="b",
        publicationDate="2026-11-20",
        submissionDeadline="2026-12-20T10:00:00+01:00",
    )


def result(n: int, **kw) -> ReadResult:
    stats = kw.pop("stats", ReadStats())
    return ReadResult(tenders=[tender(i) for i in range(n)], stats=stats, **kw)


@pytest.mark.parametrize(
    "n, dropped, declared, ok",
    [(95, 0, 100, True), (94, 0, 100, False), (80, 15, 100, True), (2582, 302, 2884, True), (3, 0, 0, True)],
)
def test_declared_total_tolerance(n, dropped, declared, ok):
    problems = completeness_problems(result(n, scope_dropped=dropped, declared_total=declared or None))
    assert (problems == []) is ok


def test_other_notices_threshold():
    one_of_twenty = {"3": "Vooraankondiging"}
    assert completeness_problems(result(20, other_notices=one_of_twenty)) == []
    two_of_twenty = {"3": "Vooraankondiging", "5": "Vergebener Auftrag"}
    assert completeness_problems(result(20, other_notices=two_of_twenty))
    three_of_fifty = {str(i): "Vergebener Auftrag" for i in range(3)}
    assert completeness_problems(result(50, other_notices=three_of_fifty)) == []


def test_limited_reads_skip_every_completeness_check():
    r = result(5, declared_total=100, more_offered=["a link 'Alle'"], stats=ReadStats(limited=True))
    assert completeness_problems(r) == []


# ---------------------------------------------------------------- evidence


def _items(html: str, css: str):
    scope = parse_html(html, "https://p.example/")
    return scope, select(Extract(by="css", expr=css), scope)


@pytest.mark.parametrize(
    "control, found",
    [
        ("<a href='/alle'>Alle Ausschreibungen</a>", True),
        ("<a href='/all'>View all</a>", True),
        ("<button>Laad meer</button>", True),
        ("<a href='?page=2'>2</a>", True),
        ("<a rel='next' href='/p2'>›</a>", True),
        ("<a href='/x' aria-label='Next'>→</a>", True),
        ("<a href='/kontakt'>Kontakt</a>", False),
        ("<a href='/alle'>Alle</a>", False),  # a filter chip, not a link to more tenders
        ("<a href='/hilfe'>Mehr erfahren</a>", False),
    ],
)
def test_more_offered_on_the_page(control, found):
    scope, items = _items(f"<ul><li>a</li><li>b</li></ul><nav>{control}</nav>", "li")
    assert bool(more_offered(scope, items)) is found


def test_controls_inside_items_are_not_evidence():
    scope, items = _items("<ul><li>a <a href='/t/1'>Alle Details</a> <a href='?page=2'>2</a></li></ul>", "li")
    assert more_offered(scope, items) == []


def test_json_totals_and_next_pointers():
    scope = parse_json(json.dumps({"items": [1, 2], "page": {"totalElements": 40}}), "https://p/")
    assert more_offered(scope, select(Extract(by="jsonpath", expr="$.items[*]"), scope)) == [
        '"page.totalElements": 40 while the response holds 2'
    ]
    scope = parse_json(json.dumps({"items": [1, 2], "total": 2, "next": None}), "https://p/")
    assert more_offered(scope, select(Extract(by="jsonpath", expr="$.items[*]"), scope)) == []


@pytest.mark.parametrize(
    "item, label",
    [
        ({"type": {"omschrijving": "Aankondiging gegunde opdracht"}}, "Aankondiging gegunde opdracht"),
        ({"noticeType": "Contract award notice"}, "Contract award notice"),
        ({"type": "Avis d'attribution"}, "Avis d'attribution"),
        ({"type": "Aankondiging opdracht"}, None),
        # A call whose description mentions an earlier prior notice is still a call.
        ({"type": "Ausschreibung", "beschreibung": "Siehe die Vorinformation vom 1.10.; " + "x" * 100}, None),
    ],
)
def test_other_notice_labels(item, label):
    assert other_notice(parse_json(json.dumps(item), "https://p/")) == label


@pytest.mark.parametrize("name", ["js_json_api", "multi_step_download", "search_form", "ssr_list_detail", "v2/app_api"])
async def test_complete_mock_reads_carry_no_completeness_evidence(name):
    """The base mock portals are complete reads: nothing here may fire on them (a guard against false alarms)."""
    from watchtower.engine.examples import load_example, load_example_v2

    template = load_example_v2(name[3:]) if name.startswith("v2/") else load_example(name)
    async with PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app())) as client:
        r = await run_template(template, client, None)
    assert r.tenders and r.more_offered == [] and r.other_notices == {}
    assert completeness_problems(r) == []
