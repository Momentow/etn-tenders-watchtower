"""T-022 toolkit tools on the mock portals (no network, no model): each tool is called through the SDK tool interface,
as the model would call it."""

import json
import re

import httpx
import pytest
from agents.tool_context import ToolContext

from mockportals.app import create_app
from watchtower.agent.analysis.recognition import field_for_label, probe_dates
from watchtower.agent.context import AgentContext
from watchtower.agent.registry import tools_for
from watchtower.engine.examples import load_example
from watchtower.net import HostRateLimiter, PoliteClient

B = "http://localhost:8081"
TOOLKIT = {
    "find_repeating_blocks",
    "find_pagination",
    "classify_links",
    "resolve_tender_page",
    "summarize_json",
    "fingerprint_software",
    "find_template_for_software",
    "map_field_labels",
    "probe_dates",
    "detect_block",
    "test_selector",
    "probe_document",
}


@pytest.fixture
def ctx():
    def make(variants=None, **kw) -> AgentContext:
        client = PoliteClient(
            limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app(variants)), max_attempts=1
        )
        return AgentContext(f"{B}/", client, **kw)

    return make


async def call(c: AgentContext, name: str, **args) -> str:
    tools = {t.name: t for t in tools_for("attach") + tools_for("investigate")}
    raw = json.dumps(args)
    return await tools[name].on_invoke_tool(
        ToolContext(context=c, tool_name=name, tool_call_id="t", tool_arguments=raw), raw
    )


def test_toolkit_is_registered_per_scenario():
    attach = {t.name for t in tools_for("attach")}
    investigate = {t.name for t in tools_for("investigate")}
    assert TOOLKIT <= attach and TOOLKIT <= investigate
    assert "diff_outputs" in investigate - attach


async def test_find_repeating_blocks_gives_items_and_field_selectors(ctx):
    out = await call(ctx(), "find_repeating_blocks", url=f"{B}/ssr/ausschreibungen")
    assert "1. items 'tbody tr': 10 records, 10 with links, 10 with dates" in out
    assert "td.buyer: 'Stadt Musterstadt'" in out


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"{B}/ssr/ausschreibungen", '{"kind": "next_link", "link": {"by": "css", "expr": "a[rel=next]"'),
        # no page links shown: found by probing ?pagina=2
        (f"{B}/multistep/licitaciones", '{"kind": "param", "name": "pagina", "location": "query", "start": 1'),
        # zero-based API page numbers; the page-size parameter is not probed
        (
            f"{B}/js/api/consultations?etat=en_cours&taille=20",
            '{"kind": "param", "name": "page", "location": "query", "start": 0',
        ),
    ],
)
async def test_find_pagination_proves_the_page_parameter(ctx, url, expected):
    out = await call(ctx(), "find_pagination", url=url, items=None)
    assert "SUGGESTED: " + expected in out and "probe taille" not in out


async def test_resolve_tender_page_detects_the_summary_page_trap(ctx):
    c = ctx()
    out = await call(c, "resolve_tender_page", url=f"{B}/multistep/anuncio/EXP-2026-0001")
    assert 'SUMMARY PAGE. Add to detail: {"resolve": [{"action": "follow", "link": {"by": "css", "expr": "a.ver-' in out
    out = await call(c, "resolve_tender_page", url=f"{B}/ssr/ausschreibung/VG-2026-001")
    assert "this is the tender page itself" in out


async def test_classify_links_groups_documents_and_ignores_menu_words(ctx):
    c = ctx()
    out = await call(c, "classify_links", url=f"{B}/multistep/licitacion/EXP-2026-0001")
    assert "DOCUMENT ×2  localhost:8081/multistep/descarga/{}/{file}  css 'li a.pliego'" in out
    assert "a.descargar-todo" in out
    out = await call(c, "classify_links", url=f"{B}/ssr/ausschreibungen")
    assert "DETAIL ×10  localhost:8081/ssr/ausschreibung/{}  css 'td.title a'" in out
    assert "PAGINATION ×1" in out


async def test_summarize_json_gives_key_statistics(ctx):
    out = await call(ctx(), "summarize_json", url=f"{B}/js/api/consultations", items="$.resultats[*]")
    assert "$.acheteur.nom: str, 50/50, 2 distinct" in out and "$.dateLimiteRemise" in out


async def test_map_field_labels_builds_detail_rules(ctx):
    out = await call(ctx(), "map_field_labels", url=f"{B}/ssr/ausschreibung/VG-2026-002")
    # Mock dates move with today's date, so match the mapping and the rule, not the value.
    assert re.search(
        r"submissionDeadline ← 'Angebotsfrist' = '\d\d\.\d\d\.\d{4} \d\d:\d\d' \| "
        + re.escape('{"by": "xpath", "expr": "//dt[normalize-space()=\'Angebotsfrist\']/following-sibling::dd[1]"}'),
        out,
    )
    out = await call(ctx(), "map_field_labels", url=f"{B}/form/przetarg/ZP.271.001")
    assert '"pattern": "Zamawiający:?\\\\s*(.+)"' in out


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("Ablauf der Angebotsfrist:", "submissionDeadline"),
        ("Date limite de remise des offres", "submissionDeadline"),
        ("Statut", None),
        ("Plazo de presentación", "submissionDeadline"),
        ("Órgano de contratación", "buyerName"),
        ("Termin składania ofert", "submissionDeadline"),
        ("Aanbestedende dienst", "buyerName"),
        ("CPV", "cpvCodes"),
    ],
)
def test_field_labels(label, field):
    assert field_for_label(label) == field


def test_probe_dates_finds_formats_zone_and_ambiguity():
    r = probe_dates(["24/11/2026 12:00", "05/12/2026 09:00"], "FR")
    assert r.zone == "Europe/Paris" and r.formats == ["%d/%m/%Y %H:%M"]
    assert r.parsed[0] == ("24/11/2026 12:00", "2026-11-24T12:00:00+01:00")
    assert any("ambiguous" in n for n in probe_dates(["05/12/2026", "03/04/2026"], "FR").notes)


async def test_detect_block_tries_a_document_before_deciding(ctx):
    c = ctx()
    out = await call(c, "detect_block", url=f"{B}/login/verfahren/W-2026-001")
    assert "VERDICT: BLOCKED — documents need a login" in out
    out = await call(c, "detect_block", url=f"{B}/ssr/ausschreibung/VG-2026-001")
    assert "VERDICT: NOT BLOCKED" in out


async def test_test_selector_runs_rules_like_the_engine(ctx):
    c = ctx()
    out = await call(
        c,
        "test_selector",
        url=f"{B}/js/api/consultations",
        by="jsonpath",
        expr="$.acheteur.nom",
        attr="text",
        pattern=None,
        items="$.resultats[*]",
    )
    assert "50 scope(s)" in out and "empty in 0/50" in out
    out = await call(
        c, "test_selector", url=f"{B}/ssr/ausschreibungen", by="css", expr="td[", attr="text", pattern=None, items=None
    )
    assert out.startswith("Invalid rule")


async def test_probe_document_suggests_per_link_on_download_pages(ctx):
    out = await call(ctx(), "probe_document", url=f"{B}/multistep/descarga/EXP-2026-0001/0")
    assert "an HTML page, not a file" in out
    assert 'per_link: {"action": "follow", "link": {"by": "css", "expr": "a.descarga-directa"' in out
    out = await call(ctx(), "probe_document", url=f"{B}/files/ssr/VG-2026-001/Leistungsverzeichnis.pdf")
    assert "application/pdf" in out and "file OK" in out


async def test_template_reuse_by_software(ctx):
    known = {"mock-ssr": load_example("ssr_list_detail")}
    c = ctx(known_templates=known)
    c.portal_url = "https://other-portal.example/"
    out = await call(c, "find_template_for_software", software="mock-ssr")
    assert "https://other-portal.example/ssr/ausschreibungen" in out and "localhost:8081" not in out
    assert "No template known" in await call(c, "find_template_for_software", software="cosinex")


async def test_diff_outputs_compares_two_templates(ctx):
    old = load_example("ssr_list_detail")
    new = json.loads(old.model_dump_json(exclude_defaults=True))
    new["fields"]["buyerName"] = {"by": "css", "expr": "td.status"}
    c = ctx()
    c.extra["template"] = old
    out = await call(c, "diff_outputs", new_template=json.dumps(new), old_template=None)
    assert "A: 5 tenders, B: 5 tenders, 5 shared" in out and "buyerName: differs on 5/5" in out
