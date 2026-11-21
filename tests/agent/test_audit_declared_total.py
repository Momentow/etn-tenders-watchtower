"""Audits compare the read with the portal's own count of its list (T-041), on the mock portals."""

import httpx
import pytest

from mockportals.app import ENTRY, create_app
from watchtower.agent import evidence
from watchtower.engine import run_template
from watchtower.engine.examples import load_example
from watchtower.engine.result import ReadResult, TenderRecord
from watchtower.net import HostRateLimiter, PoliteClient
from watchtower.validate import PortalHistory, check

BASE = "http://localhost:8081"
SSR = load_example("ssr_list_detail")
JS = load_example("js_json_api")


@pytest.fixture
def mock():
    return create_app()


@pytest.fixture
async def client(mock):
    c = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=mock), max_attempts=1)
    yield c
    await c.aclose()


async def test_a_silently_capped_list_passes_validation_but_not_the_audit(client, mock):
    mock.state.variants["ssr"] = "silent-cut"
    result = await run_template(SSR, client, None)
    assert len(result.tenders) == 15
    assert check(result, PortalHistory(recent_counts=[20, 20, 20])).ok  # a 25 % drop: validation can't tell
    findings = await evidence.audit(client, None, BASE + ENTRY["ssr"], SSR, result)
    assert findings.suspicious
    assert any("says the list holds 20 ('20 Treffer')" in f and "only 15" in f for f in findings.findings), findings


@pytest.mark.parametrize("kind, template", [("ssr", SSR), ("js", JS)])
async def test_complete_reads_match_the_portal_count(client, mock, kind, template):
    result = await run_template(template, client, None)
    findings = await evidence.audit(client, None, BASE + ENTRY[kind], template, result)
    assert not findings.suspicious, findings.describe()
    n = len(result.tenders)
    assert any(f"own count ({n}" in c and f"matches the {n} tenders read" in c for c in findings.checked), findings


async def test_a_declared_total_on_the_read_needs_no_request():
    class NoFetch:
        async def get(self):
            raise AssertionError("no request expected")

    af = evidence.AuditFindings()
    tenders = [TenderRecord(id=str(i), url=None, title="t", buyerName=None, publicationDate=None,
                            submissionDeadline=None) for i in range(5)]  # fmt: skip
    await evidence._declared_total(NoFetch(), SSR, ReadResult(tenders=tenders, declared_total=5), af)
    assert af.checked == ["the portal's own count (5) was compared by the read (5 reached)"]


async def test_conflicting_counts_are_not_a_finding(client, mock, monkeypatch):
    mock.state.variants["ssr"] = "silent-cut"
    result = await run_template(SSR, client, None)
    monkeypatch.setattr(evidence, "declared_counts", lambda scope, items: [(20, "20 Treffer"), (900, "900 avis")])
    af = evidence.AuditFindings()
    await evidence._declared_total(evidence._FirstPage(SSR, client, None), SSR, result, af)
    assert not af.findings and "several different counts" in af.checked[0]
