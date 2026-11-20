"""validate.check: the real engine on the mock portals (base and mutated variants), plus unit cases."""

from datetime import date

import httpx
import pytest

from mockportals.app import create_app
from watchtower.core.models import TenderUpsert
from watchtower.core.store import Store
from watchtower.engine import ReadResult, TenderRecord, run_template
from watchtower.engine.examples import load_example
from watchtower.engine.normalize import content_hash
from watchtower.engine.result import ReadStats
from watchtower.net import HostRateLimiter, PoliteClient
from watchtower.validate import Health, PortalHistory, check

EXAMPLES = {"ssr_list_detail": "ssr", "js_json_api": "js", "multi_step_download": "multistep", "search_form": "form"}


async def read(example: str, variants: dict[str, str] | None = None) -> ReadResult:
    transport = httpx.ASGITransport(app=create_app(variants))
    async with PoliteClient(limiter=HostRateLimiter(0), transport=transport) as client:
        return await run_template(load_example(example), client, None)


def history_of(result: ReadResult, reads: int = 3) -> PortalHistory:
    """History as if `result` had been the last `reads` good reads."""
    n = len(result.tenders)
    return PortalHistory(
        recent_counts=[n] * reads,
        field_fill={
            f: sum(getattr(t, f) is not None for t in result.tenders) / n
            for f in ("url", "title", "buyerName", "publicationDate", "submissionDeadline")
        },
        deadlines={t.id: t.submissionDeadline for t in result.tenders if t.submissionDeadline},
    )


@pytest.fixture
async def ssr_base() -> ReadResult:
    return await read("ssr_list_detail")


# ---------------------------------------------------------------- good reads pass


@pytest.mark.parametrize("example", EXAMPLES)
async def test_good_read_passes_first_time_and_against_its_own_history(example):
    result = await read(example)
    assert check(result) == Health(ok=True, problems=[])
    assert check(result, history_of(result)).ok


# ---------------------------------------------------------------- broken portals are noticed


async def test_moved_pagination_is_a_sharp_drop(ssr_base):
    result = await read("ssr_list_detail", {"ssr": "pagination"})
    assert result.errors == []  # the engine itself sees nothing wrong
    health = check(result, history_of(ssr_base))
    assert not health.ok
    assert health.problems == [
        "Only 10 tenders were found; recent reads found 20. Some tenders are probably being missed."
    ]


async def test_changed_markup_finds_no_tenders(ssr_base):
    result = await read("ssr_list_detail", {"ssr": "markup"})
    health = check(result, history_of(ssr_base))
    assert health.problems == ["The read found no tenders; recent reads found 20."]


async def test_renamed_json_keys_find_no_tenders():
    base = await read("js_json_api")
    health = check(await read("js_json_api", {"js": "renamed-keys"}), history_of(base))
    assert health.problems == [f"The read found no tenders; recent reads found {len(base.tenders)}."]


async def test_repeating_pages_are_noticed_even_without_history():
    result = await read("search_form", {"form": "pagination"})
    health = check(result)
    assert not health.ok
    assert any("list pages repeat" in p for p in health.problems)


async def test_login_wall_reports_the_engine_error(ssr_base):
    health = check(await read("ssr_list_detail", {"ssr": "login-wall"}), history_of(ssr_base))
    assert not health.ok
    assert "login" in health.problems[0]
    assert health.problems[-1] == "The read found no tenders; recent reads found 20."


async def test_gone_portal(ssr_base):
    health = check(await read("ssr_list_detail", {"ssr": "gone"}), history_of(ssr_base))
    assert any("HTTP 410" in p for p in health.problems)


async def test_first_read_with_nothing_found_fails():
    health = check(ReadResult())
    assert health.problems == ["The read found no tenders on the list page."]


# ---------------------------------------------------------------- unit cases


def tender(tid: str, **kw) -> TenderRecord:
    base = {
        "url": f"https://p.example/t/{tid}",
        "title": f"Tender {tid}",
        "buyerName": "Stadt",
        "publicationDate": "2026-11-10",
        "submissionDeadline": "2026-12-15T10:00:00+01:00",
    }
    return TenderRecord(id=tid, **(base | kw))


def result_of(tenders, **stats) -> ReadResult:
    return ReadResult(tenders=tenders, stats=ReadStats(**stats))


TODAY = date(2026, 11, 20)
FULL = {f: 1.0 for f in ("url", "title", "buyerName", "publicationDate", "submissionDeadline")}


def test_small_drops_are_normal():
    tenders = [tender(str(i)) for i in range(17)]
    assert check(result_of(tenders), PortalHistory(recent_counts=[20, 20, 19]), today=TODAY).ok


def test_drop_uses_the_median_of_recent_reads():
    tenders = [tender(str(i)) for i in range(10)]
    history = PortalHistory(recent_counts=[10, 30, 30])  # one earlier read found 10: the usual count is still 30
    assert check(result_of(tenders), history, today=TODAY).problems == [
        "Only 10 tenders were found; recent reads found 30. Some tenders are probably being missed."
    ]


def test_required_field_newly_missing():
    tenders = [tender(str(i), submissionDeadline=None) for i in range(4)] + [tender("x")]
    health = check(result_of(tenders), PortalHistory(recent_counts=[5], field_fill=FULL), today=TODAY)
    assert health.problems == ["submissionDeadline is missing on 4 of 5 tenders; before, it was shown on 100% of them."]


def test_field_the_portal_never_showed_is_fine():
    tenders = [tender(str(i), buyerName=None) for i in range(5)]
    history = PortalHistory(recent_counts=[5], field_fill=FULL | {"buyerName": 0.0})
    assert check(result_of(tenders), history, today=TODAY).ok


def test_first_read_without_titles_fails():
    tenders = [tender(str(i), title=None) for i in range(5)]
    assert check(result_of(tenders), today=TODAY).problems == ["title is missing on 5 of 5 tenders."]


def test_invalid_and_implausible_dates():
    tenders = [
        tender("a", publicationDate="10.11.2026"),
        tender("b", publicationDate="2027-03-01", submissionDeadline="2027-04-01T10:00:00+02:00"),
        tender("c", submissionDeadline="2026-12-15T10:00:00"),  # no offset
        tender("d", publicationDate="2026-11-10", submissionDeadline="2026-10-01T10:00:00+02:00"),
    ]
    assert check(result_of(tenders), today=TODAY).problems == [
        "publicationDate is not a valid date on 1 tender(s) (a).",
        "publicationDate lies in the future on 1 tender(s) (b).",
        "submissionDeadline is not a date-time with an offset on 1 tender(s) (c).",
        "submissionDeadline is before publicationDate on 1 tender(s) (d); the dates are probably misread.",
    ]


def test_duplicate_ids_in_the_result():
    tenders = [tender("a"), tender("a"), tender("b")]
    assert check(result_of(tenders), today=TODAY).problems == ["1 tender id(s) appear more than once (a)."]


def test_deadlines_jumping_together_is_noticed_one_change_is_not():
    known = {str(i): "2026-12-05T10:00:00+01:00" for i in range(6)}
    history = PortalHistory(recent_counts=[6], field_fill=FULL, deadlines=known)

    one_extended = [tender(str(i), submissionDeadline="2026-12-05T10:00:00+01:00") for i in range(5)]
    one_extended.append(tender("5", submissionDeadline="2026-12-19T10:00:00+01:00"))
    assert check(result_of(one_extended), history, today=TODAY).ok

    swapped = [tender(str(i), submissionDeadline="2027-05-12T10:00:00+02:00") for i in range(6)]
    problems = check(result_of(swapped), history, today=TODAY).problems
    assert problems == [
        "The deadline changed on 6 of 6 known tenders at once (0, 1, 2 and 3 more); "
        "the date format has probably changed."
    ]


def test_same_instant_in_another_offset_is_not_a_change():
    history = PortalHistory(recent_counts=[3], deadlines={str(i): "2026-12-05T10:00:00+01:00" for i in range(3)})
    tenders = [tender(str(i), submissionDeadline="2026-12-05T09:00:00+00:00") for i in range(3)]
    assert check(result_of(tenders), history, today=TODAY).ok


def test_page_limit_and_many_errors_are_summarised():
    result = ReadResult(
        tenders=[tender("a")],
        errors=[f"Tender {i}: GET x returned HTTP 500." for i in range(5)],
        stats=ReadStats(hit_max_pages=True, list_pages=50),
    )
    assert check(result, today=TODAY).problems == [
        "Tender 0: GET x returned HTTP 500.",
        "Tender 1: GET x returned HTTP 500.",
        "Tender 2: GET x returned HTTP 500.",
        "…and 2 more read errors like these.",
        "The read stopped at the page limit after 50 list pages, so tenders may be missing.",
    ]


# ---------------------------------------------------------------- history from the real store


async def test_history_from_the_store(tmp_path, ssr_base):
    store = await Store.open(tmp_path / "w.db")
    try:
        portal, _ = await store.add_portal("http://localhost:8081/ssr/")
        for count in (20, 21):
            run = await store.start_run(portal.id, "read")
            await store.finish_run(run.id, "ok", tender_count=count)
        failed = await store.start_run(portal.id, "read")
        await store.finish_run(failed.id, "failed", tender_count=0)
        await store.upsert_tenders(
            portal.id,
            [
                TenderUpsert(tender_id=t.id, data=t.contract_data(), content_hash=content_hash(t), status=t.status)
                for t in ssr_base.tenders
            ],
        )
        history = PortalHistory.from_store(await store.list_runs(portal.id), await store.list_tenders(portal.id))
    finally:
        await store.close()

    assert history.recent_counts == [21, 20]  # newest first, failed read ignored
    assert history.field_fill["title"] == 1.0
    assert len(history.deadlines) == len([t for t in ssr_base.tenders if t.status != "closed"])
    assert check(ssr_base, history).ok
