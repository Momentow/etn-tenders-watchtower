from watchtower.agent.prune import outline, summarize_json

ROWS = "".join(
    f'<tr class="row"><td class="ref">R-{i}</td><td><a href="/t/{i}">Tender {i}</a></td></tr>' for i in range(12)
)
PAGE = f"""<html><head><title>Tenders</title><meta name="generator" content="PortalSoft 4.2">
<script>var x = 1;</script><style>p {{}}</style></head>
<body><div class="wrap"><div class="inner"><table class="list"><tbody>{ROWS}</tbody></table></div></div>
<form action="/search" method="post"><input type="hidden" name="__VIEWSTATE" value="{"A" * 500}"></form>
<a rel="next" href="?page=2">Next</a></body></html>"""


def test_outline_prunes_and_collapses_repeats():
    out = outline(PAGE, "https://portal.example/list")
    assert "title: Tenders" in out and "generator: PortalSoft 4.2" in out
    assert "var x" not in out and "script" not in out
    assert "div.wrap > div.inner > table.list > tbody" in out  # wrapper chain joined
    assert out.count("td.ref") == 2 and "… ×10 more tr.row" in out
    assert "a[href=/t/0]" in out and 'td.ref "R-0"' in out
    assert "A" * 30 not in out  # hidden input values are cut short
    assert "a[href=?page=2 rel=next]" in out
    assert len(out) < len(PAGE) / 2


def test_focus_counts_matches_and_reports_bad_selectors():
    out = outline(PAGE, "", focus="tr.row")
    assert out.startswith("focus 'tr.row': 12 match(es)") and "… 9 more matches" in out
    assert outline(PAGE, "", focus="tr[").startswith("Invalid CSS selector")


def test_outline_is_capped():
    out = outline(PAGE, "", max_chars=200)
    assert len(out) < 300 and "[truncated:" in out


def test_summarize_json_lists_object_arrays():
    value = {"data": {"items": [{"id": i, "title": f"T{i}", "buyer": {"name": "B"}} for i in range(30)]}, "total": 30}
    out = summarize_json(value)
    assert '"items": [30 × {"id": 0, "title": "T0"' in out
    assert "$.data.items[*]  (30 items; keys: id, title, buyer)" in out
