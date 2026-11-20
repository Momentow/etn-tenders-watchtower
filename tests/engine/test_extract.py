"""Extraction rules and the placeholder normalizer (T-010 will bring its own, fuller tests)."""

import pytest

from watchtower.engine.extract import parse_html, parse_json, select, value
from watchtower.engine.normalize import normalize_fields
from watchtower.engine.template import Extract, Template

PAGE = """<html><body>
<table><tr class=row data-id=7><td class=ref> X-1 extra </td><td><a href="t/7;jsessionid=abc">Seven</a></td></tr>
<tr class=row data-id=8><td class=ref>X-2</td><td><a href="javascript:void(0)">Eight</a></td></tr></table>
<dl><dt>Frist</dt><dd>01.02.2027 10:00</dd></dl><span class=lab>Offenes Verfahren</span><span class=lab>Unknown</span>
</body></html>"""


def ex(**kw) -> Extract:
    return Extract.model_validate(kw)


@pytest.fixture
def page():
    return parse_html(PAGE, "https://p.example/list/")


def test_html5_parsing_inserts_tbody_like_a_browser(page):
    assert len(select(ex(by="css", expr="table tbody tr.row"), page)) == 2


def test_css_matches_the_scope_element_itself(page):
    rows = select(ex(by="css", expr="tr.row"), page)
    assert value(ex(by="css", expr="tr.row", attr="data-id"), rows[0]) == "7"


def test_css_items_with_xpath_fields(page):
    rows = select(ex(by="css", expr="tr.row"), page)
    assert value(ex(by="xpath", expr="./td[1]"), rows[1]) == "X-2"
    assert value(ex(by="xpath", expr="string(./td[2]/a)"), rows[1]) == "Eight"


def test_hrefs_are_made_absolute_and_non_links_dropped(page):
    rows = select(ex(by="css", expr="tr.row"), page)
    assert value(ex(by="css", expr="a", attr="href"), rows[0]) == "https://p.example/list/t/7;jsessionid=abc"
    assert value(ex(by="css", expr="a", attr="href"), rows[1]) is None


def test_pattern_map_and_join(page):
    assert value(ex(by="css", expr="td.ref", pattern=r"^(\S+)"), page) == "X-1"
    labels = ex(by="css", expr=".lab", many=True, map={"Offenes Verfahren": "open"})
    assert value(labels, page) == ["open"]  # unmapped label dropped
    assert value(ex(by="css", expr="td.ref", many=True, join=" | "), page) == "X-1 extra | X-2"


def test_regex_and_const(page):
    assert value(ex(by="regex", expr=r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})"), page) == "01.02.2027 10:00"
    assert value(ex(by="const", expr="DE"), page) == "DE"


def test_jsonpath_values_and_items():
    doc = parse_json('{"items": [{"id": 5, "cpv": ["45000000-7"], "ok": true}], "next": null}', "https://a.example/")
    items = select(ex(by="jsonpath", expr="$.items[*]"), doc)
    assert len(items) == 1
    assert value(ex(by="jsonpath", expr="$.id"), items[0]) == "5"
    assert value(ex(by="jsonpath", expr="$.ok"), items[0]) == "true"
    assert value(ex(by="jsonpath", expr="$.next"), doc) is None
    assert len(select(ex(by="jsonpath", expr="$.items"), doc)) == 1  # an array given without [*]


# ---------------------------------------------------------------- placeholder normalizer


def template(**fields) -> Template:
    return Template.model_validate(
        {
            "timezone": "Europe/Berlin",
            "list": {"kind": "html", "request": {"url": "https://p.example/"}, "items": {"by": "css", "expr": "li"}},
            "fields": {"id": {"by": "css", "expr": "li"}, "url": {"by": "css", "expr": "a", "attr": "href"}, **fields},
        }
    )


def test_local_times_get_the_portal_offset_across_dst():
    t = template(submissionDeadline={"by": "css", "expr": "x", "formats": ["%d.%m.%Y %H:%M"]})
    assert normalize_fields({"submissionDeadline": "15.01.2027 10:00"}, t)["submissionDeadline"] == (
        "2027-01-15T10:00:00+01:00"
    )
    assert normalize_fields({"submissionDeadline": "15.07.2027 10:00"}, t)["submissionDeadline"] == (
        "2027-07-15T10:00:00+02:00"
    )


def test_iso_values_keep_their_offset_and_dates_stay_dates():
    t = template(publicationDate={"by": "css", "expr": "x"}, submissionDeadline={"by": "css", "expr": "y"})
    out = normalize_fields({"publicationDate": "2027-01-15", "submissionDeadline": "2027-01-15T09:00:00Z"}, t)
    assert out["publicationDate"] == "2027-01-15"
    assert out["submissionDeadline"] == "2027-01-15T09:00:00+00:00"


def test_unreadable_or_date_only_deadline_is_none():
    t = template(submissionDeadline={"by": "css", "expr": "x", "formats": ["%d.%m.%Y"]})
    assert normalize_fields({"submissionDeadline": "15.01.2027"}, t)["submissionDeadline"] is None
    assert normalize_fields({"submissionDeadline": "bald"}, t)["submissionDeadline"] is None


def test_cpv_enums_country_and_session_ids():
    t = template()
    out = normalize_fields(
        {
            "url": "https://p.example/t/1;jsessionid=XYZ?x=1",
            "cpvCodes": ["45000000-7", "CPV 71000000-8 Planung", "45000000"],
            "procedureType": "offen",
            "contractNature": "works",
            "placeOfPerformance.countryCode": "de",
            "placeOfPerformance.city": " Berlin ",
        },
        t,
    )
    assert out["url"] == "https://p.example/t/1?x=1"
    assert out["cpvCodes"] == ["45000000", "71000000"]
    assert out["procedureType"] is None  # not a contract value: left out, never guessed
    assert out["contractNature"] == "works"
    assert out["placeOfPerformance"] == {"city": "Berlin", "countryCode": "DE"}
