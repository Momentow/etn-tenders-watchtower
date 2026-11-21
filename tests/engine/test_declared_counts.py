"""`declared_counts` (T-041): the portal's own count of its list, as proactive audits compare it with the read."""

import json

import pytest

from watchtower.engine.completeness import declared_counts
from watchtower.engine.extract import HtmlScope, css, parse_html, parse_json


def html_counts(body: str, items: str | None = None) -> list[int]:
    scope = parse_html(f"<html><body>{body}</body></html>", "https://x.test/list")
    found = [HtmlScope(el, scope.base_url) for el in css(items, scope.el)] if items else []
    return [n for n, _ in declared_counts(scope, found)]


@pytest.mark.parametrize(
    "text, n",
    [
        ("20 Treffer", 20),
        ("1 Ergebnis", 1),
        ("2.474 resultaten gevonden", 2474),
        ("Showing 1 to 10 of 2,474", 2474),
        ("1–10 von 20", 20),
        ("Ergebnisse: 33", 33),
        ("Résultats : 2 474", 2474),
        ("33 offene Ausschreibungen", 33),
        ("Znaleziono: 120", 120),
        ("(45 avis)", 45),
    ],
)
def test_counts_in_several_languages(text, n):
    assert html_counts(f"<p>{text}</p>") == [n]


@pytest.mark.parametrize(
    "body",
    [
        "<p>Seite 1 von 3</p>",
        "<p>10 Treffer pro Seite</p>",
        "<p>Show 25 results per page</p>",
        "<select><option>10 Treffer</option><option>50 Treffer</option></select>",
        "<p>Bauleistungen (12)</p>",
        "<p>Angebotsfrist 12.12.2026</p>",
        "<p>" + "Eine lange Beschreibung " * 5 + "mit 20 Treffern</p>",  # long text: never a count label
    ],
)
def test_not_counts(body):
    assert html_counts(body) == []


def test_counts_inside_list_items_are_ignored():
    body = "<ul><li class='t'>Lose: 3 Ergebnisse je Los</li><li class='t'>Rahmenvertrag</li></ul><p>2 Treffer</p>"
    assert html_counts(body, "li.t") == [2]


def test_distinct_counts_are_all_returned():
    assert sorted(html_counts("<p>20 Treffer</p><div>12.345 Bekanntmachungen insgesamt</div>")) == [20, 12345]


def test_json_total_keys():
    scope = parse_json(json.dumps({"content": [], "page": {"totalElements": 2474, "size": 50}}), "https://x.test/api")
    assert declared_counts(scope, []) == [(2474, '"page.totalElements": 2474')]
    assert declared_counts(parse_json('{"items": [], "count": 3}', "https://x.test/api"), []) == []
