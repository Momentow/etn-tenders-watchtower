#!/usr/bin/env python3
"""
ted_portals.py - discover European procurement portals through the TED Search API (v3)
and turn them into a reusable test set for the "Watchtower" project.

Pure standard library (Python 3.9+). No API key needed for TED search.

Pipeline (each step can be re-run on its own):

  collect  -> queries TED for ACTIVE competition notices per country and stores the raw
              API pages unchanged in  <out>/raw/<CC>/page-NNNN.json
  build    -> reads the raw pages (no network) and writes
                <out>/notices.jsonl          one normalised TED notice per line
                <out>/portals.json           notices grouped by portal (host) + family guess
                <out>/portals.csv            the same, flat, for a spreadsheet
                <out>/families.json          portals grouped by software family / URL pattern
                <out>/fixtures/<portal>.json expected tender values per portal (from TED),
                                             in the shape of the hackathon tender schema
                <out>/testset.json           proposed test set (countries x families coverage)
  probe    -> optional, touches the real portals politely (>= 1.1 s between requests to one
              host, no logins): portal root + one document URL. Appends to <out>/probes.jsonl.
              Run `build` again afterwards to merge the probe results into portals.json.

Examples
  python ted_portals.py collect --countries DE,FR,ES,NL,PL,AT,BE,IT --days 30
  python ted_portals.py build
  python ted_portals.py probe --testset-only
  python ted_portals.py build
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path

API_URL = "https://api.ted.europa.eu/v3/notices/search"
USER_AGENT = "watchtower-hackathon-testset/0.1 (research; 1 req/s)"
TED_MAX_PAGE = 250  # API limit per page
TED_MAX_PAGINATION = 15000  # API limit in PAGE_NUMBER mode

# Fields requested from TED. Names come from the v3 Search API field list.
# Unknown names are detected and dropped automatically before collecting (see validate_fields).
DEFAULT_FIELDS = [
    "publication-number",
    "publication-date",
    "notice-type",
    "form-type",
    "buyer-country",
    "notice-title",
    "title-proc",
    "buyer-name",
    "internal-identifier-proc",
    "procedure-type",
    "contract-nature",
    "classification-cpv",
    "deadline-receipt-tender-date-lot",
    "deadline-receipt-tender-time-lot",
    "place-of-performance-city-lot",
    "place-of-performance-post-code-lot",
    "place-of-performance-country-lot",
    "document-url-lot",
    "document-restricted-url-lot",
    "submission-url-lot",
    "description-proc",
]

# Default expert query. {country} = ISO alpha-3, {since} = yyyymmdd.
DEFAULT_QUERY = (
    "buyer-country={country} AND form-type=competition AND publication-date>={since} SORT BY publication-number DESC"
)

ISO2_TO_ISO3 = {
    "AT": "AUT",
    "BE": "BEL",
    "BG": "BGR",
    "CY": "CYP",
    "CZ": "CZE",
    "DE": "DEU",
    "DK": "DNK",
    "EE": "EST",
    "ES": "ESP",
    "FI": "FIN",
    "FR": "FRA",
    "GR": "GRC",
    "HR": "HRV",
    "HU": "HUN",
    "IE": "IRL",
    "IT": "ITA",
    "LT": "LTU",
    "LU": "LUX",
    "LV": "LVA",
    "MT": "MLT",
    "NL": "NLD",
    "PL": "POL",
    "PT": "PRT",
    "RO": "ROU",
    "SE": "SWE",
    "SI": "SVN",
    "SK": "SVK",
    "NO": "NOR",
    "IS": "ISL",
    "LI": "LIE",
    "CH": "CHE",
    "UA": "UKR",
}
ISO3_TO_ISO2 = {v: k for k, v in ISO2_TO_ISO3.items()}

# Official language(s) per country (ISO 639-2/B codes as used by TED), to pick the
# original-language title, which is what the portal itself will show.
COUNTRY_LANGS = {
    "DE": ["deu"],
    "AT": ["deu"],
    "LU": ["fra", "deu"],
    "CH": ["deu", "fra", "ita"],
    "FR": ["fra"],
    "BE": ["nld", "fra"],
    "ES": ["spa"],
    "NL": ["nld"],
    "PL": ["pol"],
    "IT": ["ita"],
    "PT": ["por"],
    "CZ": ["ces"],
    "SK": ["slk"],
    "SI": ["slv"],
    "HR": ["hrv"],
    "HU": ["hun"],
    "RO": ["ron"],
    "BG": ["bul"],
    "GR": ["ell"],
    "CY": ["ell"],
    "DK": ["dan"],
    "SE": ["swe"],
    "FI": ["fin", "swe"],
    "EE": ["est"],
    "LV": ["lav"],
    "LT": ["lit"],
    "IE": ["eng", "gle"],
    "MT": ["eng", "mlt"],
    "NO": ["nor"],
}

# TED procedure-type codes -> hackathon enum (open | restricted | negotiated | other)
PROCEDURE_MAP = {
    "open": "open",
    "restricted": "restricted",
    "neg-w-call": "negotiated",
    "neg-wo-call": "negotiated",
    "comp-dial": "other",
    "innovation": "other",
    "oth-single": "other",
    "oth-mult": "other",
}
NATURE_SET = {"works", "services", "supplies"}

# Known portal software families, matched against the full document URL.
# This is a starting point; extend it as you discover more. Order matters (first match wins).
FAMILY_RULES = [
    ("vmp-satellite", r"/(vmp)?satellite/", "DE"),
    ("netserver-54321", r"(/NetServer/|54321-Tender-)", "DE"),
    ("evergabe-online", r"evergabe-online\.de", "DE"),
    ("rib-meinauftrag", r"meinauftrag\.rib\.de", "DE"),
    ("xvergabe", r"xvergabe", "DE"),
    ("deutsche-evergabe", r"deutsche-evergabe\.de", "DE"),
    ("staatsanzeiger", r"vergabe\.(bw|staatsanzeiger)", "DE"),
    ("ausschreibungen-deutschland", r"ausschreibungen-deutschland\.de", "DE"),
    ("atexo-mpe", r"/entreprise/consultation/|/index\.php\?page=entreprise\.EntrepriseDetailsConsultation", "FR"),
    ("place-gouv", r"marches-publics\.gouv\.fr", "FR"),
    ("achatpublic", r"achatpublic\.com", "FR"),
    ("marches-securises", r"marches-securises\.fr", "FR"),
    ("e-marchespublics", r"e-marchespublics\.com", "FR"),
    ("klekoon", r"klekoon\.com", "FR"),
    ("placsp", r"contrataciondelestado\.es", "ES"),
    ("tenderned", r"tenderned\.nl", "NL"),
    ("mercell-negometrix", r"(negometrix|mercell\.com|s2c\.mercell)", "*"),
    ("eu-supply", r"eu-supply\.com", "*"),
    ("ezamowienia", r"ezamowienia\.gov\.pl", "PL"),
    ("platformazakupowa", r"platformazakupowa\.pl", "PL"),
    ("propublico", r"propublico", "PL"),
    ("smartpzp", r"smartpzp\.pl", "PL"),
    ("vergabeportal-at", r"(auftrag\.at|vemap\.com|vergabeportal\.at)", "AT"),
    ("anko-at", r"ankoe\.at|portal\.ankoe", "AT"),
    ("e-procurement-be", r"publicprocurement\.be|enot\.publicprocurement", "BE"),
    ("acquistinretepa", r"acquistinretepa\.it", "IT"),
    ("sintel-it", r"(ariaspa\.it|sintel)", "IT"),
    ("tuttogare", r"tuttogare", "IT"),
    ("doffin", r"doffin\.no", "NO"),
    ("etenders-ie", r"etenders\.gov\.ie", "IE"),
    ("jaggaer", r"jaggaer|bravosolution", "*"),
    ("ted-eu-institutions", r"etendering\.ted\.europa\.eu|ec\.europa\.eu/info/funding-tenders", "*"),
]

BLOCK_PATTERNS = {
    "captcha": r"captcha|recaptcha|hcaptcha|cf-turnstile|are you a robot",
    "login": (
        r"type=[\"']password[\"']|\blogin\b|\banmelden\b|\bregistrier|\bconnexion\b|"
        r"iniciar sesi[oó]n|\binloggen\b|\bzaloguj|\baccedi\b"
    ),
}


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).astimezone().isoformat(timespec="seconds")


def dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def flat_strings(v) -> list[str]:
    """TED field values can be str | number | list | {lang: str|list}. Flatten to strings."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, (int, float)):
        return [str(v)]
    out: list[str] = []
    if isinstance(v, list):
        for x in v:
            out += flat_strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            out += flat_strings(x)
    return out


def first(seq):
    return seq[0] if seq else None


def is_lang_dict(v) -> bool:
    return isinstance(v, dict) and bool(v) and all(isinstance(k, str) and len(k) == 3 for k in v)


def pick_lang(v, preferred: list[str]):
    """Return (value, lang) choosing the portal's language first, then English, then any."""
    if is_lang_dict(v):
        keys = {k.lower(): k for k in v}
        for lang in preferred + ["eng"]:
            if lang in keys:
                return first(flat_strings(v[keys[lang]])), lang
        k = next(iter(v))
        return first(flat_strings(v[k])), k.lower()
    return first(flat_strings(v)), None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:120]


# --------------------------------------------------------------------------------------
# TED API
# --------------------------------------------------------------------------------------


def post_json(body: dict, timeout: int = 60, retries: int = 4):
    data = json.dumps(body).encode("utf-8")
    for attempt in range(retries):
        req = urllib.request.Request(
            API_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = e.headers.get("Retry-After")
                wait = int(wait) if wait and wait.isdigit() else 3 * 2**attempt
                log(f"  HTTP {e.code}, retrying in {wait}s")
                time.sleep(wait)
                continue
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, {"raw": text[:2000]}
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                log(f"  network error {e}, retrying")
                time.sleep(3 * 2**attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def check_query(query: str) -> None:
    status, resp = post_json({"query": query, "fields": ["publication-number"], "limit": 1, "page": 1})
    if status != 200:
        log(f"TED rejected the query (HTTP {status}):\n  {query}\n  {json.dumps(resp)[:1500]}")
        log(
            "Adjust it with --query (placeholders {country} and {since}); "
            "build and test queries at https://ted.europa.eu/en/expert-search"
        )
        sys.exit(2)


def validate_fields(fields: list[str], query: str, pause: float) -> list[str]:
    status, resp = post_json({"query": query, "fields": fields, "limit": 1, "page": 1})
    if status == 200:
        return fields
    log(f"TED rejected the field list (HTTP {status}): {json.dumps(resp)[:600]}")
    log("Testing fields one by one ...")
    ok = []
    for f in fields:
        time.sleep(pause)
        s, r = post_json({"query": query, "fields": [f], "limit": 1, "page": 1})
        if s == 200:
            ok.append(f)
        else:
            log(f"  dropping unknown field: {f}")
    if "publication-number" not in ok:
        log("publication-number is not accepted - cannot continue.")
        sys.exit(2)
    return ok


def cmd_collect(args) -> None:
    out = Path(args.out)
    countries = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    for c in countries:
        if c not in ISO2_TO_ISO3:
            log(f"Unknown country code {c}; use ISO alpha-2 like DE, FR")
            sys.exit(2)
    since = (dt.date.today() - dt.timedelta(days=args.days)).strftime("%Y%m%d")
    fields = [f.strip() for f in args.fields.split(",")] if args.fields else list(DEFAULT_FIELDS)

    first_q = args.query.format(country=ISO2_TO_ISO3[countries[0]], since=since)
    check_query(first_q)
    time.sleep(args.pause)
    fields = validate_fields(fields, first_q, args.pause)
    log(f"Using {len(fields)} fields.")

    run_meta = {
        "collectedAt": now_iso(),
        "since": since,
        "queryTemplate": args.query,
        "fields": fields,
        "countries": countries,
        "scope": args.scope,
    }
    write_json(out / "raw" / "_run.json", run_meta)

    for cc in countries:
        q = args.query.format(country=ISO2_TO_ISO3[cc], since=since)
        cdir = out / "raw" / cc
        cdir.mkdir(parents=True, exist_ok=True)
        for old in cdir.glob("page-*.json"):
            old.unlink()
        got, page = 0, 1
        limit = min(TED_MAX_PAGE, max(1, 10000 // max(1, len(fields))))
        log(f"[{cc}] {q}")
        while got < args.max_per_country and page * limit <= TED_MAX_PAGINATION:
            body = {
                "query": q,
                "fields": fields,
                "limit": limit,
                "page": page,
                "scope": args.scope,
                "paginationMode": "PAGE_NUMBER",
            }
            status, resp = post_json(body)
            if status != 200:
                log(f"  HTTP {status}: {json.dumps(resp)[:600]}")
                break
            notices = resp.get("notices") or []
            write_json(
                cdir / f"page-{page:04d}.json",
                {"country": cc, "fetchedAt": now_iso(), "request": body, "response": resp},
            )
            got += len(notices)
            total = resp.get("totalNoticeCount")
            log(f"  page {page}: {len(notices)} notices (total matching: {total})")
            if len(notices) < limit:
                break
            page += 1
            time.sleep(args.pause)
        time.sleep(args.pause)
    log("Collect done. Building ...")
    cmd_build(args)


# --------------------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------------------

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(Z|[+-]\d{2}:\d{2})?$")
TIME_RE = re.compile(r"^(\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:\d{2})?$")


def norm_offset(off: str | None) -> str | None:
    if off is None:
        return None
    return "+00:00" if off == "Z" else off


def combine_deadlines(dates: list[str], times: list[str]):
    """Return (earliest full deadline with offset or None, earliest date or None).
    A full value is only produced when TED gives a time; we never invent one."""
    full, only_dates = [], []
    for i, d in enumerate(dates):
        m = DATE_RE.match(d)
        if not m:
            continue
        date, doff = m.group(1), norm_offset(m.group(2))
        only_dates.append(date)
        t = times[i] if i < len(times) else (times[0] if len(times) == 1 else None)
        tm = TIME_RE.match(t) if t else None
        if not tm:
            continue
        hms = tm.group(1) if len(tm.group(1)) == 8 else tm.group(1) + ":00"
        off = norm_offset(tm.group(2)) or doff
        if not off:
            continue
        try:
            full.append(dt.datetime.fromisoformat(f"{date}T{hms}{off}"))
        except ValueError:
            continue
    earliest = min(full).isoformat() if full else None
    return earliest, (min(only_dates) if only_dates else None)


def clean_url(u: str) -> str | None:
    u = re.sub(r"\s+", "", u or "")
    if not u or u.lower().startswith("mailto:"):
        return None
    if not re.match(r"^https?://", u, re.I):
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", u, re.I):
            u = "https://" + u
        else:
            return None
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return None
    if not p.hostname or "ted.europa.eu" == p.hostname.lower():
        return None
    return u


def portal_key(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def base_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme.lower()}://{p.netloc.lower()}"


def path_signature(url: str) -> str:
    """URL shape with ids replaced, e.g. /satellite/notice/{id}/documents.
    Same signature on different hosts usually means the same portal software."""
    p = urllib.parse.urlsplit(url)
    segs = []
    for s in p.path.split("/"):
        if not s:
            continue
        if re.fullmatch(r"\d+", s):
            segs.append("{n}")
        elif (
            len(s) >= 10
            and re.search(r"\d", s)
            and re.fullmatch(r"[A-Za-z0-9_.~-]+", s)
            and not re.search(r"\.(html?|php|aspx?|jsp|do)$", s, re.I)
        ):
            segs.append("{id}")
        else:
            segs.append(s.lower())
    sig = "/" + "/".join(segs)
    keys = sorted(urllib.parse.parse_qs(p.query, keep_blank_values=True).keys())
    if keys:
        sig += "?" + "&".join(keys)
    return sig


def family_for(url: str):
    for name, rx, _ in FAMILY_RULES:
        if re.search(rx, url, re.I):
            return name
    return None


def normalise_notice(n: dict, fetched_at: str) -> dict:
    pubno = first(flat_strings(n.get("publication-number")))
    country3 = first(flat_strings(n.get("buyer-country")))
    cc = ISO3_TO_ISO2.get((country3 or "").upper(), country3)
    langs = COUNTRY_LANGS.get(cc or "", [])

    title, title_lang = pick_lang(n.get("notice-title"), langs)
    proc_title, _ = pick_lang(n.get("title-proc"), langs)
    buyer, _ = pick_lang(n.get("buyer-name"), langs)
    desc, _ = pick_lang(n.get("description-proc"), langs)

    pub_raw = first(flat_strings(n.get("publication-date")))
    pub_date = pub_raw[:10] if pub_raw and re.match(r"^\d{4}-\d{2}-\d{2}", pub_raw) else None

    proc_raw = first(flat_strings(n.get("procedure-type")))
    nature_raw = dedupe(x.lower() for x in flat_strings(n.get("contract-nature")))
    nature = first([x for x in nature_raw if x in NATURE_SET])

    cpvs = dedupe(m.group(0) for c in flat_strings(n.get("classification-cpv")) for m in [re.match(r"\d{8}", c)] if m)

    dl_dates = flat_strings(n.get("deadline-receipt-tender-date-lot"))
    dl_times = flat_strings(n.get("deadline-receipt-tender-time-lot"))
    deadline, deadline_date = combine_deadlines(dl_dates, dl_times)

    city = first(flat_strings(n.get("place-of-performance-city-lot")))
    postal = first(flat_strings(n.get("place-of-performance-post-code-lot")))
    pcountry3 = first(flat_strings(n.get("place-of-performance-country-lot")))
    place = {
        k: v
        for k, v in {
            "postalCode": postal,
            "city": city,
            "countryCode": ISO3_TO_ISO2.get((pcountry3 or "").upper()),
        }.items()
        if v
    }

    doc_urls = dedupe(u for u in map(clean_url, flat_strings(n.get("document-url-lot"))) if u)
    restricted = dedupe(u for u in map(clean_url, flat_strings(n.get("document-restricted-url-lot"))) if u)
    submit = dedupe(u for u in map(clean_url, flat_strings(n.get("submission-url-lot"))) if u)

    is_open = None
    if deadline:
        is_open = dt.datetime.fromisoformat(deadline) > dt.datetime.now(dt.UTC)
    elif deadline_date:
        is_open = deadline_date >= dt.date.today().isoformat()

    portal_urls = doc_urls or restricted or submit
    return {
        "publicationNumber": pubno,
        "tedUrl": f"https://ted.europa.eu/en/notice/-/detail/{pubno}" if pubno else None,
        "tedXmlUrl": f"https://ted.europa.eu/en/notice/{pubno}/xml" if pubno else None,
        "links": n.get("links"),
        "buyerCountry": cc,
        "noticeType": first(flat_strings(n.get("notice-type"))),
        "formType": first(flat_strings(n.get("form-type"))),
        "publicationDate": pub_date,
        "title": proc_title or title,
        "titleLang": title_lang,
        "noticeTitle": title,
        "buyerName": buyer,
        "referenceNumber": first(flat_strings(n.get("internal-identifier-proc"))),
        "procedureType": PROCEDURE_MAP.get((proc_raw or "").lower()) if proc_raw else None,
        "procedureTypeRaw": proc_raw,
        "contractNature": nature,
        "contractNatureRaw": nature_raw,
        "cpvCodes": cpvs,
        "submissionDeadline": deadline,
        "submissionDeadlineDate": deadline_date,
        "deadlineRaw": {"dates": dl_dates, "times": dl_times},
        "placeOfPerformance": place or None,
        "description": desc,
        "documentUrls": doc_urls,
        "restrictedDocumentUrls": restricted,
        "submissionUrls": submit,
        "documentsRestricted": bool(restricted) and not doc_urls,
        "portalKeys": dedupe(portal_key(u) for u in portal_urls),
        "isOpen": is_open,
        "fetchedAt": fetched_at,
    }


# --------------------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------------------


def load_probes(out: Path) -> dict:
    probes = {}
    p = out / "probes.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                probes[rec["portalKey"]] = rec  # last one wins
    return probes


def select_testset(portals: list[dict], size: int, min_open: int, max_per_family: int):
    cands = [p for p in portals if p["openNoticeCount"] >= min_open]
    chosen, countries, families, per_family = [], set(), set(), Counter()
    while cands and len(chosen) < size:

        def score(p, countries=countries, families=families):
            fam = p["family"]
            s = math.log1p(p["openNoticeCount"])
            s += 3 if not set(p["countries"]) <= countries else 0
            s += 3 if fam not in families else 0
            # No penalty for probe.blockSuspected: its login heuristic fires on any page
            # with a "login" button, which dropped good portals and made the set unstable.
            return s

        cands = [p for p in cands if per_family[p["family"]] < max_per_family]
        if not cands:
            break
        best = max(cands, key=score)
        cands.remove(best)
        chosen.append(best)
        countries |= set(best["countries"])
        families.add(best["family"])
        per_family[best["family"]] += 1
    return chosen, countries, families


PIN_ROLES = ("working", "blocked", "holdout")


def load_pins(path: Path) -> list[tuple[str, str, str]]:
    """Read a pin file: one `portalKey [role] [# note]` per line, role in PIN_ROLES."""
    pins, seen = [], set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        body, _, note = line.partition("#")
        parts = body.split()
        if not parts:
            continue
        key, role = parts[0], (parts[1] if len(parts) > 1 else "working")
        if role not in PIN_ROLES:
            log(f"{path}:{n}: unknown role {role!r} (use {', '.join(PIN_ROLES)}); skipped")
            continue
        if key in seen:
            continue
        seen.add(key)
        pins.append((key, role, note.strip()))
    return pins


def pinned_testset(portals: list[dict], pins: list[tuple[str, str, str]]):
    by_key = {p["portalKey"]: p for p in portals}
    chosen, countries, families = [], set(), set()
    for key, role, note in pins:
        p = by_key.get(key)
        if not p:
            log(f"Pinned portal {key!r} is not in portals.json; skipped")
            continue
        chosen.append({**p, "role": role, "note": note})
        if role == "working":
            countries |= set(p["countries"])
            families.add(p["family"])
    return chosen, countries, families


def to_fixture_tender(n: dict, doc_url: str) -> dict:
    """Expected values in the hackathon tender schema, as far as TED knows them.
    id/url/documents are portal-specific and unknown here."""
    t = {
        "id": None,
        "url": None,
        "title": n["title"],
        "buyerName": n["buyerName"],
        "publicationDate": n["publicationDate"],
        "submissionDeadline": n["submissionDeadline"],
        "status": "open" if n["isOpen"] else ("closed" if n["isOpen"] is False else None),
        "documents": None,
    }
    for k in ("referenceNumber", "description", "procedureType", "contractNature"):
        if n.get(k):
            t[k] = n[k]
    if n["cpvCodes"]:
        t["cpvCodes"] = n["cpvCodes"]
    if n["placeOfPerformance"]:
        t["placeOfPerformance"] = n["placeOfPerformance"]
    t["_match"] = {
        "tedPublicationNumber": n["publicationNumber"],
        "tedUrl": n["tedUrl"],
        "documentUrl": doc_url,
        "submissionDeadlineDate": n["submissionDeadlineDate"],
        "titleLang": n["titleLang"],
        "note": "Values come from the TED notice, not from the portal. Match a portal tender "
        "by documentUrl/referenceNumber/title; wording and lot-level dates may differ.",
    }
    return t


def cmd_build(args) -> None:
    out = Path(args.out)
    raw_dir = out / "raw"
    pages = sorted(raw_dir.glob("*/page-*.json"))
    if not pages:
        log(f"No raw pages in {raw_dir}. Run `collect` first.")
        sys.exit(1)

    notices: dict[str, dict] = {}
    for pf in pages:
        page = json.loads(pf.read_text(encoding="utf-8"))
        for raw in (page.get("response") or {}).get("notices") or []:
            n = normalise_notice(raw, page.get("fetchedAt"))
            if n["publicationNumber"]:
                notices[n["publicationNumber"]] = n
    log(f"{len(notices)} unique notices from {len(pages)} pages")

    with (out / "notices.jsonl").open("w", encoding="utf-8") as f:
        for n in sorted(notices.values(), key=lambda x: x["publicationNumber"]):
            f.write(json.dumps(n, ensure_ascii=False) + "\n")

    # group by portal
    groups: dict[str, dict] = {}
    for n in notices.values():
        urls = n["documentUrls"] or n["restrictedDocumentUrls"] or n["submissionUrls"]
        for u in urls:
            key = portal_key(u)
            g = groups.setdefault(
                key,
                {
                    "urls": [],
                    "notices": {},
                    "countries": Counter(),
                    "hosts": Counter(),
                    "families": Counter(),
                    "signatures": Counter(),
                    "restricted": 0,
                },
            )
            g["urls"].append(u)
            g["hosts"][base_url(u)] += 1
            g["signatures"][path_signature(u)] += 1
            fam = family_for(u)
            if fam:
                g["families"][fam] += 1
            if n["publicationNumber"] not in g["notices"]:
                g["notices"][n["publicationNumber"]] = (n, u)
                if n["buyerCountry"]:
                    g["countries"][n["buyerCountry"]] += 1
                if n["documentsRestricted"]:
                    g["restricted"] += 1

    # signatures shared by several hosts -> implicit family
    sig_hosts = defaultdict(set)
    for key, g in groups.items():
        for sig in g["signatures"]:
            sig_hosts[sig].add(key)

    probes = load_probes(out)
    portals = []
    fixtures_dir = out / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for old in fixtures_dir.glob("*.json"):
        old.unlink()

    for key, g in groups.items():
        top_sig = g["signatures"].most_common(1)[0][0]
        if g["families"]:
            family, source = g["families"].most_common(1)[0][0], "rule"
        elif len(sig_hosts[top_sig]) > 1:
            family, source = "sig:" + top_sig, "shared-url-pattern"
        else:
            family, source = "unknown:" + key, "none"
        items = list(g["notices"].values())
        open_items = [x for x in items if x[0]["isOpen"]]
        p = {
            "portalKey": key,
            "baseUrl": g["hosts"].most_common(1)[0][0],
            "hosts": [h for h, _ in g["hosts"].most_common()],
            "countries": [c for c, _ in g["countries"].most_common()],
            "family": family,
            "familySource": source,
            "pathSignatures": dict(g["signatures"].most_common(5)),
            "activeNoticeCount": len(items),
            "openNoticeCount": len(open_items),
            "restrictedDocsNoticeCount": g["restricted"],
            "sampleDocumentUrls": dedupe(x[1] for x in (open_items or items))[:5],
            "tedPublicationNumbers": sorted(g["notices"].keys()),
            "fixtureFile": f"fixtures/{safe_name(key)}.json",
        }
        if key in probes:
            p["probe"] = probes[key]
        portals.append(p)
        write_json(
            fixtures_dir / f"{safe_name(key)}.json",
            {
                "portalKey": key,
                "baseUrl": p["baseUrl"],
                "family": family,
                "generatedAt": now_iso(),
                "expectedTenders": [
                    to_fixture_tender(n, u) for n, u in sorted(items, key=lambda x: x[0]["publicationNumber"])
                ],
            },
        )

    portals.sort(key=lambda p: (-p["openNoticeCount"], p["portalKey"]))
    write_json(out / "portals.json", portals)

    with (out / "portals.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "portalKey",
                "baseUrl",
                "family",
                "familySource",
                "countries",
                "openNotices",
                "activeNotices",
                "restrictedDocs",
                "sampleDocumentUrl",
                "probeStatus",
                "probePageKind",
                "probeBlockSuspected",
            ]
        )
        for p in portals:
            pr = p.get("probe", {})
            w.writerow(
                [
                    p["portalKey"],
                    p["baseUrl"],
                    p["family"],
                    p["familySource"],
                    "|".join(p["countries"]),
                    p["openNoticeCount"],
                    p["activeNoticeCount"],
                    p["restrictedDocsNoticeCount"],
                    first(p["sampleDocumentUrls"]) or "",
                    pr.get("root", {}).get("status", ""),
                    pr.get("pageKind", ""),
                    pr.get("blockSuspected", ""),
                ]
            )

    fams = defaultdict(lambda: {"portals": [], "countries": set(), "openNoticeCount": 0})
    for p in portals:
        fam = fams[p["family"]]
        fam["portals"].append(p["portalKey"])
        fam["countries"] |= set(p["countries"])
        fam["openNoticeCount"] += p["openNoticeCount"]
    fam_list = sorted(
        (
            {
                "family": k,
                "portalCount": len(v["portals"]),
                "countries": sorted(v["countries"]),
                "openNoticeCount": v["openNoticeCount"],
                "portals": v["portals"],
            }
            for k, v in fams.items()
        ),
        key=lambda x: (-x["portalCount"], -x["openNoticeCount"]),
    )
    write_json(out / "families.json", fam_list)

    pin_path = Path(args.pin) if args.pin else out / "testset.pinned.txt"
    if args.pin and not pin_path.exists():
        log(f"Pin file {pin_path} not found")
        sys.exit(1)
    if pin_path.exists() and not args.no_pin:
        chosen, countries, families = pinned_testset(portals, load_pins(pin_path))
        criteria = {"pinnedFrom": str(pin_path), "coverageCounts": "working portals only"}
        log(f"Test set pinned from {pin_path}")
    else:
        chosen, countries, families = select_testset(portals, args.testset_size, args.min_open, args.max_per_family)
        criteria = {"minOpenNotices": args.min_open, "size": args.testset_size, "maxPerFamily": args.max_per_family}
    write_json(
        out / "testset.json",
        {
            "generatedAt": now_iso(),
            "criteria": criteria,
            "coverage": {
                "countries": sorted(countries),
                "families": sorted(families),
                "meetsCountryGoal(>=4)": len(countries) >= 4,
                "meetsFamilyGoal(>=3)": len(families) >= 3,
            },
            "todo": [
                "Run `probe --testset-only`, then check by hand that each portal lists >= 5 "
                "open tenders whose documents download without login.",
                "Make sure the set mixes server-rendered pages, JavaScript apps and "
                "multi-step downloads (see probe.pageKind).",
                "Add 1-2 portals that need login/captcha for documents (block reporting).",
                "Keep a few portals aside, unseen, for the final test.",
            ],
            "portals": [
                {
                    "portalKey": p["portalKey"],
                    "baseUrl": p["baseUrl"],
                    **({"role": p["role"], "note": p["note"]} if "role" in p else {}),
                    "family": p["family"],
                    "countries": p["countries"],
                    "openNoticeCount": p["openNoticeCount"],
                    "sampleDocumentUrls": p["sampleDocumentUrls"][:3],
                    "fixtureFile": p["fixtureFile"],
                }
                for p in chosen
            ],
        },
    )

    log(
        f"{len(portals)} portals, {len(fam_list)} families. Test set: {len(chosen)} portals, "
        f"{len(countries)} countries, {len(families)} families."
    )
    log(f"Outputs in {out.resolve()}")
    for p in portals[:15]:
        log(f"  {p['openNoticeCount']:4d} open  {p['family']:<28.28} {p['baseUrl']}")


# --------------------------------------------------------------------------------------
# probe (optional, touches real portals)
# --------------------------------------------------------------------------------------


class PageScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.generator = "", None
        self.scripts = 0
        self.links: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            if tag == "script":
                self.scripts += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or "").lower() == "generator":
            self.generator = a.get("content")
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.text.append(data.strip())


class PoliteFetcher:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.last: dict[str, float] = {}

    def get(self, url: str, max_bytes: int = 600_000, timeout: int = 25) -> dict:
        host = urllib.parse.urlsplit(url).hostname or ""
        wait = self.last.get(host, 0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        rec = {"url": url}
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(max_bytes)
                rec.update(
                    status=r.status,
                    finalUrl=r.geturl(),
                    contentType=r.headers.get("Content-Type"),
                    contentLength=r.headers.get("Content-Length"),
                    server=r.headers.get("Server"),
                    poweredBy=r.headers.get("X-Powered-By"),
                )
                rec["_body"] = body
        except urllib.error.HTTPError as e:
            rec.update(status=e.code, finalUrl=e.geturl(), error=f"HTTP {e.code}")
        except Exception as e:  # noqa: BLE001 - we want to record any failure
            rec.update(status=None, error=f"{type(e).__name__}: {e}"[:300])
        finally:
            self.last[host] = time.monotonic()
        return rec


def analyse_html(rec: dict) -> dict:
    body = rec.pop("_body", None)
    ctype = (rec.get("contentType") or "").lower()
    if body is None or "html" not in ctype:
        return rec
    html = body.decode("utf-8", "replace")
    scan = PageScan()
    try:
        scan.feed(html)
    except Exception:  # noqa: BLE001
        pass
    text = " ".join(scan.text)
    low = html.lower()
    rec.update(
        title=scan.title.strip()[:200],
        generator=scan.generator,
        scriptTags=scan.scripts,
        visibleTextChars=len(text),
        linkCount=len(scan.links),
        fileLinks=sum(bool(re.search(r"\.(pdf|zip|docx?|xlsx?)(\?|$)", h, re.I)) for h in scan.links),
        footerText=text[-300:],
        captchaHint=bool(re.search(BLOCK_PATTERNS["captcha"], low)),
        loginHint=bool(re.search(BLOCK_PATTERNS["login"], low)),
        jsAppHint=(len(text) < 800 and scan.scripts >= 3)
        or bool(re.search(r"<(app-root|div id=[\"'](app|root)[\"'])", low)),
    )
    return rec


def cmd_probe(args) -> None:
    out = Path(args.out)
    portals = json.loads((out / "portals.json").read_text(encoding="utf-8"))
    if args.testset_only:
        keys = {p["portalKey"] for p in json.loads((out / "testset.json").read_text(encoding="utf-8"))["portals"]}
        portals = [p for p in portals if p["portalKey"] in keys]
    if args.only:
        wanted = {k.strip() for k in args.only.split(",")}
        portals = [p for p in portals if p["portalKey"] in wanted]
    portals = portals[: args.limit]
    done = set(load_probes(out)) if args.skip_done else set()
    fetcher = PoliteFetcher(args.interval)
    log(f"Probing {len(portals)} portals (>= {args.interval}s between requests per host) ...")
    with (out / "probes.jsonl").open("a", encoding="utf-8") as f:
        for p in portals:
            if p["portalKey"] in done:
                continue
            root = analyse_html(fetcher.get(p["baseUrl"] + "/"))
            doc = None
            if p["sampleDocumentUrls"]:
                doc = analyse_html(fetcher.get(p["sampleDocumentUrls"][0]))
            pages = [x for x in (root, doc) if x]
            kind = "unknown"
            if any(x.get("jsAppHint") for x in pages):
                kind = "js-app-suspected"
            elif any(x.get("visibleTextChars", 0) > 800 for x in pages):
                kind = "server-rendered-suspected"
            if doc and doc.get("finalUrl") and portal_key(doc["finalUrl"]) != p["portalKey"]:
                kind += "+redirects-to-other-host"
            rec = {
                "portalKey": p["portalKey"],
                "probedAt": now_iso(),
                "root": root,
                "sampleDocument": doc,
                "pageKind": kind,
                "blockSuspected": bool(
                    doc
                    and (
                        doc.get("captchaHint")
                        or (doc.get("loginHint") and doc.get("fileLinks", 0) == 0)
                        or doc.get("status") in (401, 403)
                    )
                ),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            log(
                f"  {p['portalKey']:<40.40} root={root.get('status')} "
                f"doc={doc.get('status') if doc else '-'} {kind}"
                f"{' BLOCK?' if rec['blockSuspected'] else ''}"
            )
    log("Probe done. Run `build` again to merge the results into portals.json.")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data", help="output directory (default: data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_build_opts(p):
        p.add_argument("--testset-size", type=int, default=12)
        p.add_argument(
            "--min-open", type=int, default=5, help="minimum open TED notices for a portal to be a test-set candidate"
        )
        p.add_argument("--max-per-family", type=int, default=2)
        p.add_argument(
            "--pin",
            default=None,
            help="pin file with the test set (`portalKey [working|blocked|holdout]` "
            "per line); default <out>/testset.pinned.txt if it exists",
        )
        p.add_argument(
            "--no-pin", action="store_true", help="ignore the pin file and select the test set automatically"
        )

    c = sub.add_parser("collect", help="query TED and store raw pages, then build")
    c.add_argument("--countries", default="DE,FR,ES,NL,PL,AT,BE,IT", help="ISO alpha-2 codes, comma separated")
    c.add_argument("--days", type=int, default=30, help="publication window in days")
    c.add_argument("--max-per-country", type=int, default=1000)
    c.add_argument("--scope", default="ACTIVE", choices=["ACTIVE", "LATEST", "ALL"])
    c.add_argument(
        "--query", default=DEFAULT_QUERY, help="expert query template with {country} (alpha-3) and {since} (yyyymmdd)"
    )
    c.add_argument("--fields", default=None, help="comma separated TED field names")
    c.add_argument("--pause", type=float, default=1.0, help="seconds between TED API calls")
    add_build_opts(c)
    c.set_defaults(func=cmd_collect)

    b = sub.add_parser("build", help="rebuild all outputs from raw pages (offline)")
    add_build_opts(b)
    b.set_defaults(func=cmd_build)

    p = sub.add_parser("probe", help="politely fetch portal roots and a document page")
    p.add_argument("--testset-only", action="store_true")
    p.add_argument("--only", help="comma separated portalKeys")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--interval", type=float, default=1.1, help="seconds between requests per host")
    p.add_argument("--skip-done", action="store_true", help="skip portals already in probes.jsonl")
    p.set_defaults(func=cmd_probe)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
