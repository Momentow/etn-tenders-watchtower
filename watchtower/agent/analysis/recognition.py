"""Recognition: label → contract field, date formats and time zones, portal-software fingerprints.

All tables are multilingual (DE, AT, FR, BE, NL, ES, PL, IT, PT, EN and the Nordics) because portals are.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from watchtower.engine.dates import parse_when
from watchtower.engine.extract import HtmlScope, collapse

# --------------------------------------------------------------------------- field labels

FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "submissionDeadline": (
        "angebotsfrist",
        "ablauf der angebotsfrist",
        "abgabefrist",
        "schlusstermin",
        "einreichungsfrist",
        "frist",
        "date limite",
        "date limite de remise",
        "date et heure limite",
        "plazo de presentacion",
        "fecha limite",
        "fin del plazo",
        "termin skladania ofert",
        "termin skladania",
        "uiterste indiendatum",
        "sluitingsdatum",
        "termijn",
        "deadline",
        "submission deadline",
        "closing date",
        "tender deadline",
        "scadenza",
        "termine presentazione",
        "prazo",
        "data limite",
        "sista anbudsdag",
        "tilbudsfrist",
    ),
    "publicationDate": (
        "veroffentlicht",
        "veroffentlichung",
        "veroffentlichungsdatum",
        "datum der veroffentlichung",
        "date de publication",
        "publie le",
        "mise en ligne",
        "fecha de publicacion",
        "publicado",
        "data publikacji",
        "publicatiedatum",
        "gepubliceerd",
        "published",
        "publication date",
        "date published",
        "data pubblicazione",
        "data de publicacao",
        "publiceringsdatum",
    ),
    "buyerName": (
        "vergabestelle",
        "auftraggeber",
        "offentlicher auftraggeber",
        "ausschreibende stelle",
        "acheteur",
        "pouvoir adjudicateur",
        "organisme",
        "entite adjudicatrice",
        "organo de contratacion",
        "organo",
        "entidad adjudicadora",
        "zamawiajacy",
        "aanbestedende dienst",
        "opdrachtgever",
        "aanbestedende overheid",
        "buyer",
        "contracting authority",
        "contracting entity",
        "authority",
        "stazione appaltante",
        "amministrazione",
        "entidade adjudicante",
        "upphandlande myndighet",
        "oppdragsgiver",
    ),
    "referenceNumber": (
        "aktenzeichen",
        "vergabenummer",
        "referenznummer",
        "az",
        "reference",
        "reference de la consultation",
        "numero de reference",
        "expediente",
        "numero de expediente",
        "numer postepowania",
        "znak sprawy",
        "kenmerk",
        "referentienummer",
        "reference number",
        "ref",
        "notice number",
        "cig",
        "numero gara",
        "referencia",
        "diarienummer",
        "saksnummer",
    ),
    "title": (
        "bezeichnung",
        "titel",
        "gegenstand",
        "auftragsgegenstand",
        "objet",
        "intitule",
        "objeto",
        "titulo",
        "nazwa",
        "przedmiot zamowienia",
        "titel opdracht",
        "omschrijving",
        "title",
        "subject",
        "oggetto",
        "rubrik",
        "tittel",
    ),
    "procedureType": (
        "verfahrensart",
        "vergabeart",
        "art des verfahrens",
        "type de procedure",
        "procedure",
        "procedimiento",
        "tipo de procedimiento",
        "tryb udzielenia zamowienia",
        "tryb",
        "procedure type",
        "type procedure",
        "aanbestedingsprocedure",
        "procedura",
        "tipo di procedura",
        "tipo de procedimento",
        "forfarande",
    ),
    "contractNature": (
        "art des auftrags",
        "auftragsart",
        "leistungsart",
        "type de marche",
        "nature du marche",
        "categorie",
        "tipo de contrato",
        "rodzaj zamowienia",
        "type opdracht",
        "contract type",
        "type of contract",
        "nature",
        "tipologia",
        "tipo di appalto",
        "kontraktstyp",
    ),
    "cpvCodes": ("cpv", "cpv-code", "cpv code", "cpv-codes", "code cpv", "codigo cpv", "kod cpv", "cpv codes"),
    "placeOfPerformance": (
        "erfullungsort",
        "ausfuhrungsort",
        "leistungsort",
        "lieu d'execution",
        "lieu d execution",
        "lugar de ejecucion",
        "miejsce realizacji",
        "plaats van uitvoering",
        "place of performance",
        "location",
        "luogo di esecuzione",
        "local de execucao",
        "ort",
    ),
    "description": (
        "beschreibung",
        "kurzbeschreibung",
        "leistungsbeschreibung",
        "description",
        "descripcion",
        "opis",
        "beschrijving",
        "descrizione",
        "descricao",
        "summary",
        "beskrivning",
    ),
}
_LABEL_TO_FIELD = {label: f for f, labels in FIELD_SYNONYMS.items() for label in labels}
_LABEL_CHARS = 60


def fold(s: str) -> str:
    """Lower-case, accents removed, punctuation and colons trimmed: `Ablauf der Angebotsfrist:` → `ablauf der …`."""
    s = unicodedata.normalize("NFKD", s.replace("ł", "l").replace("Ł", "L"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w' -]", " ", s)).strip()


def field_for_label(label: str) -> str | None:
    key = fold(label)
    if key in _LABEL_TO_FIELD:
        return _LABEL_TO_FIELD[key]
    for syn, f in sorted(_LABEL_TO_FIELD.items(), key=lambda kv: -len(kv[0])):
        if len(syn) >= 5 and (key.startswith(syn + " ") or key.endswith(" " + syn)):
            return f
    return None


@dataclass
class LabelledValue:
    label: str
    value: str
    field: str | None
    xpath: str  # absolute on the page; works as a detail-page rule
    pattern: str | None = None  # regex that drops the label when the xpath's text includes it


def labelled_values(scope: HtmlScope, limit: int = 40) -> list[LabelledValue]:
    """Label/value pairs on a page: `dt`/`dd`, `th`/`td` rows, `<b>Label:</b> value` and `Label: value` paragraphs."""
    out: list[LabelledValue] = []
    seen: set[str] = set()

    def add(label: str, value: str, xpath: str, *, label_in_text: bool = False) -> None:
        label, value = collapse(label).rstrip(":").strip(), collapse(value)
        if not label or not value or len(label) > _LABEL_CHARS or label in seen:
            return
        seen.add(label)
        pattern = re.escape(label).replace("\\ ", " ") + r":?\s*(.+)" if label_in_text else None
        out.append(LabelledValue(label, value[:120], field_for_label(label), xpath, pattern))

    root = scope.el
    for dt in root.iter("dt"):
        dd = dt.getnext()
        if dd is not None and dd.tag == "dd":
            lab = collapse(dt.text_content())
            add(lab, dd.text_content(), f"//dt[normalize-space()={_xq(lab)}]/following-sibling::dd[1]")
    for th in root.iter("th"):
        td = th.getnext()
        if td is not None and td.tag == "td":
            lab = collapse(th.text_content())
            add(lab, td.text_content(), f"//th[normalize-space()={_xq(lab)}]/following-sibling::td[1]")
    for tr in root.iter("tr"):
        cells = [c for c in tr if isinstance(c.tag, str) and c.tag == "td"]
        if len(cells) == 2 and len(collapse(cells[0].text_content())) <= _LABEL_CHARS:
            lab = collapse(cells[0].text_content())
            add(lab, cells[1].text_content(), f"//tr[td[1][normalize-space()={_xq(lab)}]]/td[2]")
    for el in root.iter("p", "li", "div", "span"):
        own = collapse(el.text or "")
        strong = next((c for c in el if isinstance(c.tag, str) and c.tag in ("b", "strong", "label", "span")), None)
        if strong is not None and not own and collapse(strong.text_content()).endswith(":"):
            lab = collapse(strong.text_content())
            rest = collapse(el.text_content())[len(lab) :]
            add(lab, rest, f"//{el.tag}[{strong.tag}[normalize-space()={_xq(lab)}]]", label_in_text=True)
        elif ":" in own and len(own.split(":", 1)[0]) <= _LABEL_CHARS and len(el) <= 2:
            lab, _, rest = collapse(el.text_content()).partition(":")
            if rest.strip():
                add(lab, rest, f"//{el.tag}[starts-with(normalize-space(), {_xq(lab + ':')})]", label_in_text=True)
        if len(out) >= limit:
            break
    return out


def _xq(s: str) -> str:
    """An XPath string literal."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    return "concat(" + ', "\'", '.join(f"'{p}'" for p in s.split("'")) + ")"


# --------------------------------------------------------------------------- dates

COUNTRY_ZONES = {
    "AT": "Europe/Vienna",
    "BE": "Europe/Brussels",
    "BG": "Europe/Sofia",
    "CH": "Europe/Zurich",
    "CY": "Asia/Nicosia",
    "CZ": "Europe/Prague",
    "DE": "Europe/Berlin",
    "DK": "Europe/Copenhagen",
    "EE": "Europe/Tallinn",
    "ES": "Europe/Madrid",
    "FI": "Europe/Helsinki",
    "FR": "Europe/Paris",
    "GB": "Europe/London",
    "UK": "Europe/London",
    "GR": "Europe/Athens",
    "HR": "Europe/Zagreb",
    "HU": "Europe/Budapest",
    "IE": "Europe/Dublin",
    "IS": "Atlantic/Reykjavik",
    "IT": "Europe/Rome",
    "LI": "Europe/Vaduz",
    "LT": "Europe/Vilnius",
    "LU": "Europe/Luxembourg",
    "LV": "Europe/Riga",
    "MT": "Europe/Malta",
    "NL": "Europe/Amsterdam",
    "NO": "Europe/Oslo",
    "PL": "Europe/Warsaw",
    "PT": "Europe/Lisbon",
    "RO": "Europe/Bucharest",
    "SE": "Europe/Stockholm",
    "SI": "Europe/Ljubljana",
    "SK": "Europe/Bratislava",
}
_TLD_COUNTRY = {
    "at": "AT",
    "be": "BE",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "nl": "NL",
    "pl": "PL",
    "it": "IT",
    "pt": "PT",
    "se": "SE",
    "no": "NO",
    "dk": "DK",
    "fi": "FI",
    "ie": "IE",
    "uk": "GB",
    "ch": "CH",
    "cz": "CZ",
    "sk": "SK",
    "lu": "LU",
    "gr": "GR",
    "hu": "HU",
    "ro": "RO",
    "si": "SI",
    "hr": "HR",
    "lt": "LT",
    "lv": "LV",
    "ee": "EE",
    "bg": "BG",
    "mt": "MT",
    "cy": "CY",
}
_LANG_COUNTRY = {
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "nl": "NL",
    "pl": "PL",
    "it": "IT",
    "pt": "PT",
    "sv": "SE",
    "da": "DK",
    "fi": "FI",
    "cs": "CZ",
    "sk": "SK",
    "hu": "HU",
    "ro": "RO",
    "el": "GR",
    "bg": "BG",
}

CANDIDATE_FORMATS = (
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%d.%m.%Y, %H:%M",
    "%d/%m/%Y à %Hh%M",
    "%d/%m/%Y %Hh%M",
    "%d.%m.%Y %H:%M Uhr",
    "%d %B %Y",
    "%d %B %Y %H:%M",
    "%d %b %Y",
    "%B %d, %Y",
    "%m/%d/%Y",
)


def guess_country(url: str, lang: str | None) -> str | None:
    from urllib.parse import urlsplit

    tld = (urlsplit(url).hostname or "").rsplit(".", 1)[-1].lower()
    if tld in _TLD_COUNTRY:
        return _TLD_COUNTRY[tld]
    if lang:
        return _LANG_COUNTRY.get(lang.split("-")[0].lower())
    return None


@dataclass
class DateProbe:
    zone: str
    formats: list[str]  # strptime formats that read every sample (best first)
    parsed: list[tuple[str, str | None]]  # (sample, ISO result with the engine's default parsing)
    notes: list[str]


def probe_dates(samples: list[str], country: str | None) -> DateProbe:
    """Which strptime formats read all samples, and what the engine makes of them in the country's zone."""
    zone = COUNTRY_ZONES.get((country or "").upper(), "UTC")
    tz = ZoneInfo(zone)
    samples = [collapse(s) for s in samples if collapse(s)]
    parsed = []
    for s in samples:
        v = parse_when(s, [], tz)
        parsed.append((s, v.isoformat() if v is not None else None))
    fitting = []
    for fmt in CANDIDATE_FORMATS:
        if samples and all(_fits(s, fmt, tz) for s in samples):
            fitting.append(fmt)
    notes = []
    if country is None:
        notes.append("country unknown: times are read as UTC; give the country for the right zone")
    numeric = [re.findall(r"\d+", s)[:2] for s in samples]
    if numeric and all(len(n) == 2 and int(n[0]) <= 12 and int(n[1]) <= 12 for n in numeric):
        notes.append(
            "day and month are ambiguous in every sample: European portals are day-first; check one date "
            "against the page (e.g. a publication date in the past)"
        )
    if "%m/%d/%Y" in fitting and "%d/%m/%Y" in fitting:
        fitting.remove("%m/%d/%Y")
    return DateProbe(zone, fitting[:3], parsed, notes)


def _fits(sample: str, fmt: str, tz: ZoneInfo) -> bool:
    from watchtower.engine.dates import _english_months  # month names in local languages → English

    for candidate in (sample, _english_months(sample), _english_months(sample, abbreviated=True)):
        try:
            datetime.strptime(candidate, fmt)
            return True
        except ValueError:
            continue
    return False


# --------------------------------------------------------------------------- software fingerprints

# (name, regex over URL + generator + page text/markup). Real portal packages seen across EU procurement.
SOFTWARE_SIGNATURES: list[tuple[str, str]] = [
    ("cosinex-vergabemarktplatz", r"vergabemarktplatz|cosinex|VMPCenter|/VMPSatellite/"),
    ("deutsche-evergabe", r"deutsche-evergabe|dashboard\.deutsche-evergabe"),
    ("subreport-elvis", r"subreport|ELViS"),
    ("dtvp", r"dtvp\.de|Deutsches Vergabeportal"),
    ("evergabe-online", r"evergabe-online"),
    ("administration-intelligence-ai", r"vergabe\.ai|Administration Intelligence|AI-VERGABEMANAGER"),
    ("provia", r"provia|ProVia"),
    ("vergabeportal-at", r"vergabeportal\.at|auftrag\.at"),
    ("atexo-localtrust", r"atexo|LocalTrust|/entreprise/consultation|marches-publics\.info"),
    ("achatpublic", r"achatpublic"),
    ("place-marches-publics-gouv", r"marches-publics\.gouv\.fr|PLACE"),
    ("maximilien", r"maximilien\.fr"),
    ("klekoon-dematis", r"dematis|klekoon"),
    ("placsp", r"contrataciondelestado|PLACSP|Plataforma de Contrataci[oó]n"),
    ("tenderned", r"tenderned"),
    ("negometrix-mercell", r"negometrix|mercell"),
    ("3p-cloud", r"3p\.eu|cloud\.3p"),
    ("ebiz4all", r"ebiz4all"),
    ("e-procurement-be", r"publicprocurement\.be|enot\.publicprocurement|e-procurement\.be"),
    ("platformazakupowa", r"platformazakupowa"),
    ("ezamowienia", r"ezamowienia\.gov\.pl|e-Zam[oó]wienia"),
    ("miniportal-uzp", r"miniportal\.uzp"),
    ("ted", r"ted\.europa\.eu"),
    ("doffin", r"doffin"),
    ("hilma", r"hankintailmoitukset|hilma"),
    ("tendsign-visma", r"tendsign|opic\.com|Visma"),
    ("etendersie", r"etenders\.gov\.ie"),
    ("find-a-tender", r"find-tender\.service\.gov\.uk|Find a Tender"),
    ("contracts-finder", r"contractsfinder"),
    ("acquistinretepa", r"acquistinretepa"),
    ("sintel", r"sintel\.regione"),
    ("base-gov-pt", r"base\.gov\.pt"),
    ("vortal", r"vortal"),
    ("jaggaer", r"jaggaer|bravosolution|bravo solution"),
    ("sap-ariba", r"ariba"),
    ("wordpress", r"wp-content|WordPress"),
    ("drupal", r"Drupal|/sites/default/files/"),
    ("typo3", r"TYPO3|typo3conf|typo3temp"),
    ("liferay", r"Liferay|/web/guest/"),
    ("asp-net-webforms", r"__VIEWSTATE|\.aspx"),
    ("jsf", r"javax\.faces|\.xhtml|\.jsf\b"),
]


@dataclass
class Fingerprint:
    software: str | None
    evidence: list[str]
    platform: list[str]  # generic web stack hints (not a procurement package)


_GENERIC = {"wordpress", "drupal", "typo3", "liferay", "asp-net-webforms", "jsf"}


def fingerprint(url: str, scope: HtmlScope | None, raw: str) -> Fingerprint:
    generator = ""
    footer = ""
    if scope is not None:
        for meta in scope.el.iterfind(".//meta[@name]"):
            if meta.get("name", "").lower() == "generator":
                generator = meta.get("content") or ""
        foot = scope.el.find(".//footer")
        footer = collapse(foot.text_content()) if foot is not None else ""
    haystack = " ".join([url, generator, footer, raw[:200_000]])
    found, platform, evidence = [], [], []
    for name, rx in SOFTWARE_SIGNATURES:
        m = re.search(rx, haystack, re.I)
        if m:
            (platform if name in _GENERIC else found).append(name)
            evidence.append(f"{name}: {m.group(0)!r}")
    if generator:
        evidence.insert(0, f"meta generator: {generator!r}")
    return Fingerprint(found[0] if found else None, evidence[:6], platform)


def page_lang(scope: HtmlScope | None) -> str | None:
    if scope is None:
        return None
    html: Any = scope.el if scope.el.tag == "html" else scope.el.getroottree().getroot()
    return html.get("lang") or None
