"""run_template on the mock portals (ground truth: mockportals.data.expected_tenders) and on edge cases."""

import json
from pathlib import Path

import httpx
import pytest

from mockportals import data
from mockportals.app import create_app
from watchtower.engine import run_template
from watchtower.engine.examples import _DIR as EXAMPLES_DIR
from watchtower.engine.examples import load_example
from watchtower.engine.template import Template
from watchtower.net import BrowserPool, HostRateLimiter, PoliteClient

HTTP_EXAMPLES = {
    "ssr_list_detail": "ssr",
    "js_json_api": "js",
    "multi_step_download": "multistep",
    "search_form": "form",
}
COMPARED = [
    "id",
    "title",
    "buyerName",
    "publicationDate",
    "submissionDeadline",
    "status",
    "referenceNumber",
    "description",
    "procedureType",
    "contractNature",
    "cpvCodes",
    "placeOfPerformance",
]


def mock_client(variants: dict[str, str] | None = None) -> PoliteClient:
    return PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app(variants)))


def stub_client(pages: dict[str, tuple[int, str, str]]) -> PoliteClient:
    """URL → (status, content type, body); anything else is a 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        status, ctype, body = pages.get(str(request.url), (404, "text/html", "not found"))
        return httpx.Response(status, headers={"content-type": ctype}, text=body)

    return PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(handler), max_attempts=1)


def example_dict(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


async def read(template: Template, variants: dict[str, str] | None = None, **kw):
    async with mock_client(variants) as client:
        return await run_template(template, client, None, **kw)


# ---------------------------------------------------------------- the mock portals, base variant


@pytest.mark.parametrize("example,kind", HTTP_EXAMPLES.items())
async def test_example_reads_its_mock_portal_exactly(example, kind):
    result = await read(load_example(example))

    assert result.errors == []
    got = {t.id: t.contract_data() for t in result.tenders}
    expected = {t["id"]: t for t in data.expected_tenders(kind)}
    assert list(got) == list(expected)  # every tender, in portal order, no extras
    for tid, exp in expected.items():
        for field in COMPARED:
            assert got[tid].get(field) == exp.get(field), f"{tid}.{field}"
        assert got[tid]["url"], tid
    assert all(t.complete for t in result.tenders)


@pytest.mark.parametrize("example,kind", HTTP_EXAMPLES.items())
async def test_document_refs_point_at_the_files(example, kind):
    result = await read(load_example(example))
    by_id = {t.id: t for t in data.visible(kind)}
    for tender in result.tenders:
        urls = [d.url for d in tender.documents]
        mock = by_id[tender.id]
        if kind == "multistep":  # the "download all" ZIP is preferred over the per-document download pages
            assert urls == [f"{data.BASE}/files/multistep/{tender.id}/Documentacion completa.zip"]
        else:
            assert sorted(urls) == sorted(d.url for d in mock.docs), tender.id
        if kind in ("ssr", "js"):
            assert sorted(d.fileName for d in tender.documents) == sorted(d.name for d in mock.docs)


async def test_summary_page_trap_resolves_to_the_real_tender_page():
    result = await read(load_example("multi_step_download"))
    assert all("/multistep/licitacion/" in t.url for t in result.tenders)


async def test_session_ids_are_stripped_from_tender_urls():
    result = await read(load_example("ssr_list_detail"))
    assert all(";jsessionid" not in t.url for t in result.tenders)


async def test_restricted_documents_give_an_empty_list():
    result = await read(load_example("ssr_list_detail"))
    restricted = next(t.id for t in data.visible("ssr") if t.docs_restricted)
    assert next(t for t in result.tenders if t.id == restricted).documents == []


async def test_per_link_hops_reach_the_file_when_there_is_no_bundle():
    raw = example_dict("multi_step_download")
    del raw["documents"]["bundle"]
    result = await read(Template.model_validate(raw), limit=2)
    assert result.errors == []
    first = data.visible("multistep")[0]
    tender = result.tenders[0]
    assert [d.url for d in tender.documents] == [d.url for d in first.docs]  # Ayuda.html excluded
    assert [d.fileName for d in tender.documents] == [d.name for d in first.docs]


async def test_limit_stops_early_for_dry_runs():
    result = await read(load_example("ssr_list_detail"), limit=3)
    assert len(result.tenders) == 3
    assert result.stats.list_pages == 1
    assert result.stats.detail_pages == 3


async def test_stats_count_pages_and_requests():
    result = await read(load_example("ssr_list_detail"))
    assert result.stats.list_pages == 2
    assert result.stats.detail_pages == 20
    assert result.stats.requests == 22
    assert result.stats.total_seconds >= 0


# ---------------------------------------------------------------- snapshots


async def test_html_snapshot_is_a_collapsed_outline():
    result = await read(load_example("ssr_list_detail"))
    snap = result.snapshot
    assert snap["kind"] == "html"
    assert "table.tenders" in snap["outline"]
    assert "tr ×10" in snap["outline"]
    assert "Sporthalle" not in snap["outline"]  # structure only, no text


async def test_json_snapshot_is_the_key_shape():
    result = await read(load_example("js_json_api"))
    shape = result.snapshot["shape"]
    assert result.snapshot["kind"] == "json"
    assert shape["resultats"][0]["acheteur"] == {"nom": "str", "siret": "str"}
    assert shape["total"] == "int"


async def test_snapshot_changes_when_the_markup_changes():
    base = await read(load_example("ssr_list_detail"))
    changed = await read(load_example("ssr_list_detail"), {"ssr": "markup"})
    assert base.snapshot["outline"] != changed.snapshot["outline"]


# ---------------------------------------------------------------- mutated variants: the engine reports, never crashes


async def test_changed_markup_reads_zero_tenders():
    result = await read(load_example("ssr_list_detail"), {"ssr": "markup"})
    assert result.tenders == []


async def test_renamed_json_keys_read_zero_tenders():
    result = await read(load_example("js_json_api"), {"js": "renamed-keys"})
    assert result.tenders == []


async def test_moved_pagination_reads_only_the_first_page():
    result = await read(load_example("ssr_list_detail"), {"ssr": "pagination"})
    assert len(result.tenders) == 10
    assert result.errors == []  # a silent drop: validate.check (T-011) compares the count with history


async def test_param_pagination_that_repeats_stops_on_no_new_ids():
    result = await read(load_example("search_form"), {"form": "pagination"})
    assert len(result.tenders) == 5  # the `strona` field is ignored, so page 2 repeats page 1
    assert result.stats.duplicate_ids == 5
    assert result.stats.list_pages == 2


async def test_login_wall_is_reported():
    result = await read(load_example("ssr_list_detail"), {"ssr": "login-wall"})
    assert result.tenders == []
    assert any("login" in e for e in result.errors)


async def test_gone_portal_is_reported_with_the_status():
    result = await read(load_example("ssr_list_detail"), {"ssr": "gone"})
    assert result.tenders == []
    assert any("HTTP 410" in e for e in result.errors)


# ---------------------------------------------------------------- edge cases on stub pages

LIST = """<html><body><ul>
<li class=t data-id=A><a href="/d/A">A</a></li>
<li class=t data-id=B><a href="/d/B">B</a></li>
</ul><a rel=next href="/list">again</a></body></html>"""


def stub_template(**changes) -> Template:
    raw = {
        "timezone": "Europe/Berlin",
        "list": {
            "kind": "html",
            "request": {"url": "https://p.example/list"},
            "items": {"by": "css", "expr": "li.t"},
            "pagination": {"kind": "next_link", "link": {"by": "css", "expr": "a[rel=next]", "attr": "href"}},
        },
        "detail": {"link": {"by": "css", "expr": "a", "attr": "href"}},
        "fields": {
            "id": {"by": "css", "expr": "li.t", "attr": "data-id"},
            "title": {"by": "css", "expr": "h1", "on": "detail"},
        },
    }
    raw.update(changes)
    return Template.model_validate(raw)


async def test_next_link_loop_stops_and_a_failed_detail_keeps_the_tender():
    pages = {
        "https://p.example/list": (200, "text/html", LIST),
        "https://p.example/d/A": (200, "text/html", "<h1>Tender A</h1>"),
        # /d/B → 404
    }
    async with stub_client(pages) as client:
        result = await run_template(stub_template(), client, None)
    assert [t.id for t in result.tenders] == ["A", "B"]
    assert result.stats.list_pages == 1  # the next link points back at the list: visited, stop
    a, b = result.tenders
    assert (a.title, a.complete) == ("Tender A", True)
    assert (b.title, b.complete) == (None, False)
    assert result.errors == ["Tender B: GET https://p.example/d/B returned HTTP 404."]


async def test_items_without_id_are_skipped_and_reported():
    body = LIST.replace("data-id=B", "")
    pages = {
        "https://p.example/list": (200, "text/html", body),
        "https://p.example/d/A": (200, "text/html", "<h1>A</h1>"),
    }
    async with stub_client(pages) as client:
        result = await run_template(stub_template(), client, None)
    assert [t.id for t in result.tenders] == ["A"]
    assert result.errors == ["1 list item(s) had no id and were skipped."]


async def test_unreachable_list_is_an_error_not_an_exception():
    async with stub_client({}) as client:
        result = await run_template(stub_template(), client, None)
    assert result.tenders == []
    assert result.errors == ["Reading the tender list failed: GET https://p.example/list returned HTTP 404."]


async def test_render_template_without_browser_is_an_error():
    async with mock_client() as client:
        result = await run_template(load_example("captcha_bypassable"), client, None)
    assert result.errors and "browser" in result.errors[0]


async def test_json_body_pagination_and_cursor():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        page = {None: ([{"id": 1}, {"id": 2}], "c2"), "c2": ([{"id": 3}], None)}[body.get("cursor")]
        return httpx.Response(
            200, json={"rows": [{"id": i, "u": f"/t/{i}"} for i in [r["id"] for r in page[0]]], "next": page[1]}
        )

    t = Template.model_validate(
        {
            "timezone": "UTC",
            "list": {
                "kind": "json",
                "request": {"url": "https://api.example/search", "method": "POST", "json_body": {"q": "x"}},
                "items": {"by": "jsonpath", "expr": "$.rows[*]"},
                "pagination": {
                    "kind": "cursor",
                    "cursor": {"by": "jsonpath", "expr": "$.next"},
                    "name": "cursor",
                    "location": "json",
                },
            },
            "fields": {"id": {"by": "jsonpath", "expr": "$.id"}, "url": {"by": "jsonpath", "expr": "$.u"}},
        }
    )
    client = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(handler))
    async with client:
        result = await run_template(t, client, None)
    assert [x.id for x in result.tenders] == ["1", "2", "3"]
    assert result.tenders[0].url == "https://api.example/t/1"
    assert calls == [{"q": "x"}, {"q": "x", "cursor": "c2"}]


# ---------------------------------------------------------------- browser (captcha mock: list built by JS)


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_installed(), reason="Playwright Chromium not installed")
async def test_rendered_portal_behind_a_captcha_widget_still_reads():
    from mockportals.testing import serve

    with serve(port=18082) as served:
        raw = json.loads(
            (EXAMPLES_DIR / "captcha_bypassable.json")
            .read_text(encoding="utf-8")
            .replace("http://localhost:8081", served.url)
        )
        limiter = HostRateLimiter(0)
        browser = BrowserPool(limiter=limiter)
        try:
            async with PoliteClient(limiter=limiter) as client:
                result = await run_template(Template.model_validate(raw), client, browser)
        finally:
            await browser.aclose()

    assert result.errors == []
    got = {t.id: t.contract_data() for t in result.tenders}
    expected = {t["id"]: t for t in data.expected_tenders("captcha")}
    assert list(got) == list(expected)
    for tid, exp in expected.items():
        for field in ("title", "buyerName", "publicationDate", "submissionDeadline", "status"):
            assert got[tid][field] == exp[field], f"{tid}.{field}"
    assert all(t.documents for t in result.tenders)
