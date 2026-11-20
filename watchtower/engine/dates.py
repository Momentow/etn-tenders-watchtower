"""Locale-aware date/time parsing for European procurement portals.

`parse_when(text, formats, tz)` returns a `date` (no time of day shown), an aware `datetime`, or None. Order:

1. the template's strptime `formats` (month names in DE/FR/ES/NL/PL/IT/PT/EN are translated first, so `%B`/`%b` work);
2. ISO 8601;
3. common European forms, always day-first: `09.12.2026 10:00 Uhr`, `09/12/2026 à 10h00`, `9 de diciembre de 2026`,
   `9 grudnia 2026 r. godz. 10:00`, `9 dec. 2026 om 10:00`…

Times without an offset are local to the portal's zone `tz` (DST-aware). An explicit offset, `Z`, `UTC` or `GMT` in the
text wins. Anything ambiguous or implausible gives None: a wrong date is worse than a missing one.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

_MONTHS: dict[int, tuple[str, ...]] = {
    1: (
        "january",
        "jan",
        "januar",
        "janner",
        "janvier",
        "janv",
        "enero",
        "ene",
        "januari",
        "styczen",
        "stycznia",
        "sty",
        "gennaio",
        "gen",
        "janeiro",
    ),
    2: (
        "february",
        "feb",
        "februar",
        "fevrier",
        "fevr",
        "fev",
        "febrero",
        "februari",
        "luty",
        "lutego",
        "lut",
        "febbraio",
        "fevereiro",
    ),
    3: ("march", "mar", "marz", "mrz", "mars", "marzo", "maart", "mrt", "marzec", "marca", "marco"),
    4: ("april", "apr", "avril", "avr", "abril", "abr", "kwiecien", "kwietnia", "kwi", "aprile"),
    5: ("may", "mai", "mayo", "mei", "maj", "maja", "maggio", "mag", "maio"),
    6: ("june", "jun", "juni", "juin", "junio", "czerwiec", "czerwca", "cze", "giugno", "giu", "junho"),
    7: ("july", "jul", "juli", "juillet", "juil", "julio", "lipiec", "lipca", "lip", "luglio", "lug", "julho"),
    8: ("august", "aug", "aout", "agosto", "ago", "augustus", "sierpien", "sierpnia", "sie"),
    9: (
        "september",
        "sep",
        "sept",
        "septembre",
        "septiembre",
        "setiembre",
        "wrzesien",
        "wrzesnia",
        "wrz",
        "settembre",
        "set",
        "setembro",
    ),
    10: (
        "october",
        "oct",
        "oktober",
        "okt",
        "octobre",
        "octubre",
        "pazdziernik",
        "pazdziernika",
        "paz",
        "ottobre",
        "ott",
        "outubro",
        "out",
    ),
    11: ("november", "nov", "novembre", "noviembre", "listopad", "listopada", "lis", "novembro"),
    12: (
        "december",
        "dec",
        "dezember",
        "dez",
        "decembre",
        "diciembre",
        "dic",
        "grudzien",
        "grudnia",
        "gru",
        "dicembre",
        "dezembro",
    ),
}
MONTH_NUMBERS: dict[str, int] = {name: n for n, names in _MONTHS.items() for name in names}
_ENGLISH = {n: names[0] for n, names in _MONTHS.items()}

_WORD = re.compile(r"[a-z]+")
_OFFSET = re.compile(r"(?<![\d:])([+-])(\d{2}):?(\d{2})\b|\b(utc|gmt|z)\b")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,4})\s*([./-])\s*(\d{1,2})\s*\2\s*(\d{2,4})(?!\d)")
_TEXT_DATE = re.compile(
    r"(?<!\d)(\d{1,2})(?:st|nd|rd|th|er|e|o|\.)?\s*(?:de\s+)?([a-z]{3,})\.?,?\s*(?:de\s+)?(\d{4})(?!\d)"
)
_TEXT_DATE_MONTH_FIRST = re.compile(r"\b([a-z]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)")
_TIME = re.compile(r"(?<![\d.])(\d{1,2})\s*(?::|h|\.)\s*(\d{2})(?:\s*[:.]\s*(\d{2}))?(?!\d)\s*(am|pm)?")
_HOUR_ONLY = re.compile(r"(?<![\d.:])(\d{1,2})\s*(?:h|uhr|horas?|uur|godz)\b\s*(am|pm)?")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_DIRECTIVES = re.compile(r"%[HIMSXcTRp]")
_MIN_YEAR, _MAX_YEAR = 2000, 2100


def parse_when(text: str | None, formats: list[str] | tuple[str, ...], tz: tzinfo) -> date | datetime | None:
    if text is None:
        return None
    raw = " ".join(text.replace(" ", " ").replace(" ", " ").split())
    if not raw:
        return None
    for parse in (lambda: _with_formats(raw, formats, tz), lambda: _iso(raw, tz), lambda: _european(raw, tz)):
        try:
            value = parse()
        except (ValueError, OverflowError):
            value = None
        if value is not None:
            return value if _plausible(value) else None
    return None


def _plausible(value: date) -> bool:
    return _MIN_YEAR <= value.year <= _MAX_YEAR


def _with_formats(raw: str, formats: list[str] | tuple[str, ...], tz: tzinfo) -> date | datetime | None:
    if not formats:
        return None
    candidates = [raw, _english_months(raw), _english_months(raw, abbreviated=True)]
    for fmt in formats:
        for candidate in dict.fromkeys(candidates):
            try:
                parsed = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            if not _TIME_DIRECTIVES.search(fmt):
                return parsed.date()
            return parsed if parsed.tzinfo is not None else _localize(parsed, tz)
    return None


def _iso(raw: str, tz: tzinfo) -> date | datetime | None:
    s = raw.strip()
    if _ISO_DATE.match(s):
        return date.fromisoformat(s)
    if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", s):
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    parsed = datetime.fromisoformat(s)
    return parsed if parsed.tzinfo is not None else _localize(parsed, tz)


def _european(raw: str, tz: tzinfo) -> date | datetime | None:
    s = _fold(raw)
    found = _find_date(s)
    if found is None:
        return None
    day_value, end = found
    # Further dates ("bis 15.12.2026") must not be read as a time of day.
    rest = _TEXT_DATE.sub(" ", _NUMERIC_DATE.sub(" ", s[end:]))
    clock = _find_time(rest)
    if clock is None:
        return day_value
    hour, minute, second = clock
    extra = timedelta(0)
    if hour == 24 and minute == 0 and second == 0:  # "24:00" = end of that day
        hour, extra = 0, timedelta(days=1)
    naive = datetime(day_value.year, day_value.month, day_value.day, hour, minute, second) + extra
    offset = _find_offset(rest)
    if offset is not None:
        return naive.replace(tzinfo=offset)
    return _localize(naive, tz)


def _find_date(s: str) -> tuple[date, int] | None:
    best: tuple[int, date, int] | None = None  # (start, value, end): the earliest date in the text wins
    m = _NUMERIC_DATE.search(s)
    if m:
        a, _, b, c = m.groups()
        if len(a) == 4:
            y, mo, d = int(a), int(b), int(c)
        elif len(a) <= 2:
            d, mo, y = int(a), int(b), int(c)
            if len(c) == 2:
                y += 2000
            elif len(c) != 4:
                y = -1
        else:
            y = -1
        if y > 0:
            best = (m.start(), date(y, mo, d), m.end())
    m = _TEXT_DATE.search(s)
    if m and (n := MONTH_NUMBERS.get(m.group(2))) and (best is None or m.start() < best[0]):
        best = (m.start(), date(int(m.group(3)), n, int(m.group(1))), m.end())
    m = _TEXT_DATE_MONTH_FIRST.search(s)
    if m and (n := MONTH_NUMBERS.get(m.group(1))) and (best is None or m.start() < best[0]):
        best = (m.start(), date(int(m.group(3)), n, int(m.group(2))), m.end())
    return None if best is None else (best[1], best[2])


def _find_time(rest: str) -> tuple[int, int, int] | None:
    m = _TIME.search(rest)
    if m:
        hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        hour = _twelve_hour(hour, m.group(4))
    else:
        m = _HOUR_ONLY.search(rest)
        if not m:
            return None
        hour, minute, second = _twelve_hour(int(m.group(1)), m.group(2)), 0, 0
    if hour > 24 or minute > 59 or second > 59 or (hour == 24 and (minute or second)):
        raise ValueError("not a time of day")
    return hour, minute, second


def _twelve_hour(hour: int, marker: str | None) -> int:
    if marker == "pm" and hour < 12:
        return hour + 12
    if marker == "am" and hour == 12:
        return 0
    return hour


def _find_offset(rest: str) -> tzinfo | None:
    m = _OFFSET.search(rest)
    if not m:
        return None
    if m.group(4):
        return UTC
    sign = 1 if m.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))


def _localize(naive: datetime, tz: tzinfo) -> datetime:
    return naive.replace(tzinfo=tz)


def _fold(text: str) -> str:
    """Lower-case and strip accents (październik → pazdziernik, décembre → decembre)."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _english_months(text: str, *, abbreviated: bool = False) -> str:
    """Swap local month names for English ones so strptime's %B / %b (C locale) can read them."""
    folded = _fold(text)
    out, last = [], 0
    for m in _WORD.finditer(folded):
        n = MONTH_NUMBERS.get(m.group())
        if n is None:
            continue
        english = _ENGLISH[n][:3].title() if abbreviated else _ENGLISH[n].title()
        out.append(text[last : m.start()] + english)
        last = m.end()
    if not out:
        return text
    # folding keeps string length except for characters that decompose; fall back to the folded text then
    if len(folded) != len(text):
        return _english_months_folded(folded, abbreviated)
    return "".join(out) + text[last:]


def _english_months_folded(folded: str, abbreviated: bool) -> str:
    def swap(m: re.Match) -> str:
        n = MONTH_NUMBERS.get(m.group())
        if n is None:
            return m.group()
        return _ENGLISH[n][:3].title() if abbreviated else _ENGLISH[n].title()

    return _WORD.sub(swap, folded)
