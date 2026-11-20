"""The mock portals serve what the T-008 example templates (watchtower/engine/examples/*.json) expect, and each variant
breaks them the way it is documented to. Selectors below are copied from those examples; there is no engine yet (T-009),
so they are applied directly with selectolax / lxml / jsonpath-ng."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from jsonpath_ng import parse as jp
from lxml import html as lxml_html
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from mockportals import ENTRY, EXPECTED_STATE, VARIANTS, create_app, expected_tenders
from mockportals.files import LARGE_SIZE


@pytest.fixture
def client():
    return TestClient(create_app(), base_url="http://localhost:8081", follow_redirects=False)


def set_variant(client: TestClient, kind: str, variant: str) -> None:
    r = client.post(f"/_mock/variants/{kind}", json={"variant": variant})
    assert r.status_code == 200, r.text


def local(text: str, fmt: str, tz: str) -> str:
    return datetime.strptime(text.strip(), fmt).replace(tzinfo=ZoneInfo(tz)).isoformat()


# --- ssr (examples/ssr_list_detail.json) -------------------------------------------------------------------------


def read_ssr(client: TestClient) -> list[dict]:
    out, url = [], "/ssr/ausschreibungen"
    for _ in range(50):
        doc = HTMLParser(client.get(url).text)
        for row in doc.css("table.tenders tbody tr"):
            link = row.css_first("td.title a").attributes["href"]
            det = client.get(link)
            tree = lxml_html.fromstring(det.text)
            dsel = HTMLParser(det.text)
            out.append(
                {
                    "id": row.css_first("td.ref").text().split()[0],
                    "url": link,
                    "title": row.css_first("td.title a").text(),
                    "buyerName": row.css_first("td.buyer").text(),
                    "deadline": local(
                        tree.xpath("//dt[normalize-space()='Angebotsfrist']/following-sibling::dd[1]")[
                            0
                        ].text_content(),
                        "%d.%m.%Y %H:%M",
                        "Europe/Berlin",
                    ),
                    "docs": [a.text() for a in dsel.css("ul.documents a")],
                    "cpv": [li.text()[:8] for li in dsel.css("ul.cpv li")],
                }
            )
        nxt = doc.css_first("a[rel=next]")
        if not nxt:
            break
        url = nxt.attributes["href"]
    return out


def test_ssr_base_matches_example_template(client):
    got = {t["id"]: t for t in read_ssr(client)}
    want = {t["id"]: t for t in expected_tenders("ssr")}
    assert got.keys() == want.keys() and len(got) == 20  # 23 tenders, 3 of them only appear in silent-missed
    for tid, w in want.items():
        g = got[tid]
        assert (g["title"], g["buyerName"], g["deadline"]) == (w["title"], w["buyerName"], w["submissionDeadline"])
        assert g["cpv"] == w["cpvCodes"]
        assert ";jsessionid=" in g["url"]  # quirk: the url must be normalised by the engine
    statuses = {t["status"] for t in want.values()}
    assert {"open", "withdrawn", "closed"} <= statuses
    assert any(not t["documents"] for t in want.values())  # documents only for registered companies


def test_ssr_markup_breaks_selectors(client):
    set_variant(client, "ssr", "markup")
    assert read_ssr(client) == []


def test_ssr_pagination_reads_only_first_page(client):
    set_variant(client, "ssr", "pagination")
    assert len(read_ssr(client)) == 10


def test_ssr_silent_missed_still_parses_but_misses_new_tenders(client):
    set_variant(client, "ssr", "silent-missed")
    got = read_ssr(client)
    assert len(got) == 20
    assert len(expected_tenders("ssr", "silent-missed")) == 23


def test_ssr_silent_swap_gives_wrong_buyer(client):
    set_variant(client, "ssr", "silent-swap")
    got = read_ssr(client)
    want = {t["id"]: t["buyerName"] for t in expected_tenders("ssr")}
    assert len(got) == 20 and all(g["buyerName"] != want[g["id"]] for g in got)


# --- js (examples/js_json_api.json) ------------------------------------------------------------------------------


def read_js(client: TestClient) -> list[dict]:
    out, seen = [], set()
    for page in range(50):
        body = client.get("/js/api/consultations", params={"etat": "en_cours", "taille": "50", "page": page}).json()
        items = [m.value for m in jp("$.resultats[*]").find(body)]
        new = [i for i in items if i["id"] not in seen]
        if not new:
            break
        for item in new:
            seen.add(item["id"])
            det = client.get(f"/js/api/consultations/{item['id']}").json()
            out.append(
                {
                    "id": item["id"],
                    "title": item["objet"],
                    "buyerName": jp("$.acheteur.nom").find(item)[0].value,
                    "deadline": local(item["dateLimiteRemise"], "%d/%m/%Y %H:%M", "Europe/Paris"),
                    "docs": [m.value for m in jp("$.dce[*].nomFichier").find(det)],
                    "docUrls": [m.value for m in jp("$.dce[*].url").find(det)],
                }
            )
    return out


def test_js_base_matches_example_template(client):
    got = {t["id"]: t for t in read_js(client)}
    want = {t["id"]: t for t in expected_tenders("js")}
    assert got.keys() == want.keys() and len(got) == 54
    for tid, w in want.items():
        assert (got[tid]["title"], got[tid]["buyerName"], got[tid]["deadline"]) == (
            w["title"],
            w["buyerName"],
            w["submissionDeadline"],
        )
    assert client.get(got[next(iter(got))]["docUrls"][0].replace("http://localhost:8081", "")).status_code == 200


def test_js_shell_is_empty_and_calls_the_api(client):
    shell = client.get("/js/").text
    assert "Chargement" in shell and "/js/app.js" in shell
    assert "/js/api/consultations" in client.get("/js/app.js").text


def test_js_renamed_keys_breaks_template(client):
    set_variant(client, "js", "renamed-keys")
    assert read_js(client) == []


def test_js_pagination_returns_repeats(client):
    set_variant(client, "js", "pagination")
    assert len(read_js(client)) == 20


def test_js_silent_missed(client):
    set_variant(client, "js", "silent-missed")
    assert len(read_js(client)) == 54
    assert len(expected_tenders("js", "silent-missed")) == 57
    assert "etat=tous" in client.get("/js/app.js").text  # the live site shows them


# --- multistep (examples/multi_step_download.json) ---------------------------------------------------------------


def read_multistep(client: TestClient, card: str = "div.licitacion", link: str = "a.anuncio") -> list[dict]:
    out = []
    for pagina in range(1, 50):
        cards = HTMLParser(client.get("/multistep/licitaciones", params={"pagina": pagina}).text).css(card)
        if not cards:
            break
        for c in cards:
            summary = HTMLParser(client.get(c.css_first(link).attributes["href"]).text)  # the trap: a notice summary
            tender_url = summary.css_first("a.ver-licitacion").attributes["href"]
            det = HTMLParser(client.get(tender_url).text)
            files = []
            for a in det.css("a.pliego"):
                href = a.attributes["href"]
                if href.lower().endswith((".html", ".htm")):
                    continue
                dl = HTMLParser(client.get(href).text).css_first("a.descarga-directa").attributes["href"]
                files.append((a.text(), dl))
            out.append(
                {
                    "id": c.css_first("span.expediente").text(),
                    "deadline": local(det.css_first("td.plazo").text(), "%d/%m/%Y %H:%M", "Europe/Madrid"),
                    "files": files,
                    "bundle": det.css_first("a.descargar-todo").attributes["href"],
                }
            )
    return out


def test_multistep_base_matches_example_template(client):
    got = {t["id"]: t for t in read_multistep(client)}
    want = {t["id"]: t for t in expected_tenders("multistep")}
    assert got.keys() == want.keys() and len(got) == 12
    for tid, w in want.items():
        assert got[tid]["deadline"] == w["submissionDeadline"]
        bundle = client.get(got[tid]["bundle"])
        assert bundle.headers["content-type"] == "application/zip"
        assert sorted(zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()) == w["documents"]
    # one tender links a 300 MB file: its size is known from the headers, before reading the body
    large = [dl for t in got.values() for name, dl in t["files"] if dl.startswith("/files/large/")]
    assert len(large) == 1
    with client.stream("GET", large[0]) as r:
        assert int(r.headers["content-length"]) == LARGE_SIZE > 250 * 1024 * 1024


def test_multistep_markup_breaks_selectors(client):
    set_variant(client, "multistep", "markup")
    assert read_multistep(client) == []
    assert len(read_multistep(client, "article.expediente-card", "a.ver-anuncio")) == 12


# --- form (examples/search_form.json) ----------------------------------------------------------------------------


def read_form(client: TestClient) -> list[dict]:
    out, seen = [], set()
    for strona in range(1, 50):
        tree = lxml_html.fromstring(client.post("/form/przetargi", data={"status": "otwarte", "strona": strona}).text)
        rows = tree.xpath("//table[@id='wyniki']/tbody/tr")
        ids = [r.xpath("./td[1]")[0].text_content() for r in rows]
        if not rows or set(ids) <= seen:
            break
        for r, tid in zip(rows, ids, strict=True):
            seen.add(tid)
            out.append(
                {
                    "id": tid,
                    "deadline": local(r.xpath("./td[5]")[0].text_content(), "%Y-%m-%d %H:%M", "Europe/Warsaw"),
                    "docs": [a.get("href") for a in r.xpath("./td[6]//a")],
                }
            )
    return out


def test_form_base_matches_example_template(client):
    got = {t["id"]: t for t in read_form(client)}
    want = {t["id"]: t for t in expected_tenders("form")}
    assert got.keys() == want.keys() and len(got) == 14
    assert all(got[k]["deadline"] == want[k]["submissionDeadline"] for k in want)
    first = next(iter(got.values()))["docs"][0]
    assert client.get(first).content.startswith(b"%PDF")


def test_form_pagination_moved(client):
    set_variant(client, "form", "pagination")
    assert len(read_form(client)) == 5


# --- captcha (examples/captcha_bypassable.json) ------------------------------------------------------------------


def test_captcha_shown_but_list_and_documents_work(client):
    search = client.get("/captcha/aanbestedingen").text
    assert "g-recaptcha" in search and "id='zoeken'" in search
    results = client.get("/captcha/zoeken", params={"q": ""}).text
    assert "g-recaptcha" in results
    assert HTMLParser(results).css("article.aanbesteding") == []  # list is built by JS (render: true)
    det = HTMLParser(client.get("/captcha/aanbesteding/" + expected_tenders("captcha")[0]["id"]).text)
    assert det.css_first("time.sluiting").attributes["datetime"] == expected_tenders("captcha")[0]["submissionDeadline"]
    link = det.css_first("a[download]").attributes["href"]
    assert client.get(link).status_code == 200
    assert EXPECTED_STATE["captcha"] == "serving"


# --- blocked portals and generic variants ------------------------------------------------------------------------


def test_login_portal_documents_redirect_to_login(client):
    det = client.get("/login/verfahren/" + expected_tenders("login")[0]["id"])
    link = HTMLParser(det.text).css_first("ul.unterlagen a").attributes["href"]
    r = client.get(link)
    assert r.status_code == 302 and "/login/anmelden" in r.headers["location"]
    assert "type='password'" in client.get(r.headers["location"]).text
    assert EXPECTED_STATE["login"] == "failed"


def test_robot_portal_documents_need_captcha(client):
    det = client.get("/robot/opdracht/" + expected_tenders("robot")[0]["id"].replace("/", "%2F"))
    assert det.status_code == 200
    link = HTMLParser(det.text).css_first("ul.unterlagen a").attributes["href"]
    r = client.get(link)
    assert r.status_code == 302
    gate = client.get(r.headers["location"]).text
    assert "g-recaptcha" in gate
    assert "mislukt" in client.post("/robot/verificatie", data={"doc": "x"}).text


@pytest.mark.parametrize("kind", list(VARIANTS))
def test_gone_and_login_wall_apply_to_every_portal(client, kind):
    set_variant(client, kind, "gone")
    assert client.get(ENTRY[kind]).status_code == 410
    set_variant(client, kind, "login-wall")
    r = client.get(ENTRY[kind])
    assert r.status_code == 302 and r.headers["location"].startswith("/_auth/anmelden")


def test_documents_are_deterministic_and_zips_nest(client):
    t = next(t for t in expected_tenders("ssr") if len(t["documents"]) > 1)
    path = f"/files/ssr/{t['id']}/Vergabeunterlagen.zip"
    a, b = client.get(path).content, create_client_bytes(path)
    assert a == b
    z = zipfile.ZipFile(io.BytesIO(a))
    assert "Anlagen.zip" in z.namelist()
    assert zipfile.ZipFile(io.BytesIO(z.read("Anlagen.zip"))).namelist() == ["Anlage/Plan.pdf"]


def create_client_bytes(path: str) -> bytes:
    return TestClient(create_app(), base_url="http://localhost:8081").get(path).content


def test_unknown_variant_rejected(client):
    assert client.post("/_mock/variants/ssr", json={"variant": "nope"}).status_code == 400
    with pytest.raises(ValueError):
        create_app({"ssr": "nope"})


def test_serve_runs_a_real_server():
    import httpx

    from mockportals.testing import serve

    with serve(port=18081) as s:
        assert httpx.get(s.url + "/ssr/ausschreibungen").status_code == 200
        s.app.state.variants["ssr"] = "gone"
        assert httpx.get(s.url + "/ssr/ausschreibungen").status_code == 410
