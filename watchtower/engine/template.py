"""The template: how to read one portal, as data (ADR 0003).

The agent writes templates (its structured output uses `template_json_schema()`); the engine executes them with no
model call. Every expression is compiled when a template is loaded, and cross-references are checked, so a broken
template fails at `Template.model_validate(...)` with a readable error instead of halfway through a read.

Run `python -m watchtower.engine.template` to print the JSON schema.
"""

import json
import re
import string
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FORMAT_VERSION = 1

Scope = Literal["html", "json"]
_BY_FOR_SCOPE: dict[Scope, set[str]] = {
    "html": {"css", "xpath", "regex", "const"},
    "json": {"jsonpath", "regex", "const"},
}
_FORBIDDEN_HEADERS = {"authorization", "cookie", "proxy-authorization"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- extraction


class Extract(_Strict):
    """Pull a value out of a scope (an HTML element/page or a JSON value).

    - `css` / `xpath`: relative to the scope element. `attr` picks what to read: `text` (default, whitespace collapsed),
      `html`, or an attribute name such as `href`. URLs from `href`/`src` are made absolute by the engine.
    - `jsonpath`: relative to the scope JSON value, e.g. `$.title` or `$.lots[*].cpv`.
    - `regex`: searched in the scope's text (HTML) or serialised JSON; group 1 if the pattern has a group.
    - `const`: `expr` is the value itself.

    Then, in order: `pattern` (regex on each value, group 1 if present, no match → dropped), `map` (exact label → value,
    unmapped → dropped), and for `many` results `join` (into one string). A single-value extract takes the first match.
    """

    by: Literal["css", "xpath", "jsonpath", "regex", "const"]
    expr: str
    attr: str = "text"
    many: bool = False
    pattern: str | None = None
    map: dict[str, str] | None = None
    join: str | None = None

    @model_validator(mode="after")
    def _compile(self) -> "Extract":
        _check_expression(self.by, self.expr)
        if self.pattern is not None:
            _check_regex(self.pattern)
        if self.attr != "text" and self.by not in ("css", "xpath"):
            raise ValueError("`attr` only applies to css/xpath extracts")
        if self.join is not None and not self.many:
            raise ValueError("`join` needs `many: true`")
        return self


class FieldRule(Extract):
    """An `Extract` plus where it runs: the list item (default) or the tender's detail page."""

    on: Literal["item", "detail"] = "item"


class DateRule(FieldRule):
    """A date or date-time. `formats`: strptime patterns tried in order (e.g. `%d.%m.%Y %H:%M`); empty = ISO 8601.

    Times without an offset are read in the template's `timezone`.
    """

    formats: list[str] = []


class PlaceRules(_Strict):
    postalCode: FieldRule | None = None
    city: FieldRule | None = None
    countryCode: FieldRule | None = None


class Fields(_Strict):
    """Contract tender fields (names as in the API contract). Leave out what the portal doesn't show."""

    id: FieldRule
    url: FieldRule | None = None  # default: the detail page URL
    title: FieldRule | None = None
    buyerName: FieldRule | None = None
    publicationDate: DateRule | None = None
    submissionDeadline: DateRule | None = None
    referenceNumber: FieldRule | None = None
    description: FieldRule | None = None
    procedureType: FieldRule | None = None  # use `map` to turn portal labels into open/restricted/negotiated/other
    contractNature: FieldRule | None = None  # use `map` → works/services/supplies
    cpvCodes: FieldRule | None = None  # `many: true`; the normaliser strips check digits
    placeOfPerformance: PlaceRules | None = None

    def rules(self) -> dict[str, FieldRule]:
        """Every rule, flattened (`placeOfPerformance.city`, …)."""
        out: dict[str, FieldRule] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, PlaceRules):
                for sub in PlaceRules.model_fields:
                    if (rule := getattr(value, sub)) is not None:
                        out[f"placeOfPerformance.{sub}"] = rule
            elif value is not None:
                out[name] = value
        return out


# --------------------------------------------------------------------------- requests and navigation


class Request(_Strict):
    """An HTTP request. No credentials: login-protected portals are `Blocked`, not read."""

    url: str
    method: Literal["GET", "POST"] = "GET"
    params: dict[str, str] = {}
    headers: dict[str, str] = {}
    form: dict[str, str] | None = None
    json_body: Any | None = None

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not re.match(r"^https?://", v):
            raise ValueError("url must be absolute http(s)")
        return v

    @field_validator("headers")
    @classmethod
    def _no_credentials(cls, v: dict[str, str]) -> dict[str, str]:
        bad = _FORBIDDEN_HEADERS & {k.lower() for k in v}
        if bad:
            raise ValueError(f"credential headers are not allowed: {sorted(bad)}")
        return v

    @model_validator(mode="after")
    def _one_body(self) -> "Request":
        if self.form is not None and self.json_body is not None:
            raise ValueError("give `form` or `json_body`, not both")
        if (self.form is not None or self.json_body is not None) and self.method == "GET":
            raise ValueError("a request body needs method POST")
        return self


class FollowStep(_Strict):
    """Go to the URL a link on the current page points to."""

    action: Literal["follow"]
    link: Extract  # css/xpath with attr href (or jsonpath for JSON pages)


class SubmitStep(_Strict):
    """Submit a form on the current page: the engine keeps its hidden inputs (e.g. ASP.NET __VIEWSTATE), applies
    `values`, and sends it to the form's action with its method."""

    action: Literal["submit"]
    form: str  # CSS selector of the <form>
    values: dict[str, str] = {}

    @field_validator("form")
    @classmethod
    def _css(cls, v: str) -> str:
        _check_expression("css", v)
        return v


Step = Annotated[FollowStep | SubmitStep, Field(discriminator="action")]


# --------------------------------------------------------------------------- pagination


class NoPagination(_Strict):
    kind: Literal["none"]


class NextLinkPagination(_Strict):
    """Follow a "next page" link (HTML) or a next-page URL in the response (JSON) until there is none."""

    kind: Literal["next_link"]
    link: Extract
    max_pages: int = Field(50, ge=1, le=500)


class ParamPagination(_Strict):
    """Count a parameter up: page numbers (`start: 1, step: 1`) or offsets (`start: 0, step: 50`).

    `location` says where it goes: the query string, the form body, or a top-level key of the JSON body.
    Stops at the first page with no items, or with no item ids that weren't seen already.
    """

    kind: Literal["param"]
    name: str
    location: Literal["query", "form", "json"] = "query"
    start: int = 1
    step: int = Field(1, ge=1)
    max_pages: int = Field(50, ge=1, le=500)


class CursorPagination(_Strict):
    """JSON APIs that return a cursor/token for the next page; it's sent back in parameter `name`."""

    kind: Literal["cursor"]
    cursor: Extract  # jsonpath on the whole response
    name: str
    location: Literal["query", "form", "json"] = "query"
    max_pages: int = Field(50, ge=1, le=500)


Pagination = Annotated[
    NoPagination | NextLinkPagination | ParamPagination | CursorPagination, Field(discriminator="kind")
]


# --------------------------------------------------------------------------- list, detail, documents, status


class HtmlList(_Strict):
    """Tender list in HTML. `items` selects one element per tender; item fields are relative to it."""

    kind: Literal["html"]
    request: Request
    steps: list[Step] = []  # navigation from `request` to the list page (search form, landing → list…)
    items: Extract  # css/xpath, one match per tender
    pagination: Pagination = NoPagination(kind="none")
    render: bool = False  # True: load pages with the browser (JS-only portals)


class JsonList(_Strict):
    """Tender list from a JSON API (often found through the browser's captured XHR). Fetched directly, no browser."""

    kind: Literal["json"]
    request: Request
    items: Extract  # jsonpath to the tender objects, e.g. `$.data.items[*]`
    pagination: Pagination = NoPagination(kind="none")


ListSource = Annotated[HtmlList | JsonList, Field(discriminator="kind")]


class Detail(_Strict):
    """One page per tender.

    Its URL comes from `link` (on the list item) or `url_template` (from item fields, e.g.
    `https://portal.example/api/tenders/{id}`). `resolve` hops from a summary/notice page to the real tender page.
    """

    link: Extract | None = None
    url_template: str | None = None
    format: Scope = "html"
    render: bool = False
    resolve: list[FollowStep] = []

    @model_validator(mode="after")
    def _one_url_source(self) -> "Detail":
        if (self.link is None) == (self.url_template is None):
            raise ValueError("give exactly one of `link` or `url_template`")
        if self.render and self.format == "json":
            raise ValueError("`render` only applies to html detail pages")
        return self


class Documents(_Strict):
    """Where a tender's documents are.

    `links` (`many: true`) gives the document URLs; `names`, if given, gives file names in the same order.
    `bundle`, if it finds a link ("download all as ZIP"), is used instead of `links`.
    `include` / `exclude` are regexes on the URL or name. `per_link` hops from each link through intermediate
    pages (a download page, a disclaimer) to the file itself.
    """

    on: Literal["item", "detail"] = "detail"
    links: Extract
    names: Extract | None = None
    bundle: Extract | None = None
    include: str | None = None
    exclude: str | None = None
    per_link: list[FollowStep] = []

    @model_validator(mode="after")
    def _check(self) -> "Documents":
        if not self.links.many:
            raise ValueError("documents.links needs `many: true`")
        if self.names is not None and not self.names.many:
            raise ValueError("documents.names needs `many: true`")
        for rx in (self.include, self.exclude):
            if rx is not None:
                _check_regex(rx)
        return self


class StatusRule(_Strict):
    """How the portal marks a tender's status. `value` reads the label; `withdrawn` / `closed` are case-insensitive
    regexes on it. No match → `open` (the pipeline still closes tenders past their deadline or no longer listed)."""

    value: FieldRule
    withdrawn: list[str] = []
    closed: list[str] = []

    @model_validator(mode="after")
    def _regexes(self) -> "StatusRule":
        for rx in [*self.withdrawn, *self.closed]:
            _check_regex(rx)
        return self


# --------------------------------------------------------------------------- template


class Template(_Strict):
    format_version: Literal[1] = FORMAT_VERSION
    timezone: str  # IANA name, e.g. Europe/Berlin: the zone of times shown without an offset
    country: str | None = None  # ISO 3166-1 alpha-2 of the portal, e.g. DE
    software: str | None = None  # portal software fingerprint, for template reuse (T-022)
    list: ListSource
    detail: Detail | None = None
    fields: Fields
    documents: Documents | None = None
    status: StatusRule | None = None
    notes: str = ""

    @field_validator("timezone")
    @classmethod
    def _zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown IANA time zone: {v!r}") from e
        return v

    @field_validator("country")
    @classmethod
    def _country(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[A-Z]{2}", v):
            raise ValueError("country must be two upper-case letters, e.g. DE")
        return v

    @property
    def needs_browser(self) -> bool:
        return (self.list.kind == "html" and self.list.render) or (self.detail is not None and self.detail.render)

    @model_validator(mode="after")
    def _cross_checks(self) -> "Template":
        item_scope: Scope = self.list.kind
        detail_scope: Scope | None = self.detail.format if self.detail else None

        def check(where: str, ex: Extract, scope: Scope | None) -> None:
            if scope is None:
                raise ValueError(f"{where} runs on the detail page, but the template has no `detail`")
            if ex.by not in _BY_FOR_SCOPE[scope]:
                allowed = ", ".join(sorted(_BY_FOR_SCOPE[scope]))
                raise ValueError(f"{where}: `{ex.by}` can't read {scope}; use one of {allowed}")

        def scope_of(on: str) -> Scope | None:
            return item_scope if on == "item" else detail_scope

        if self.list.items.by == "const":
            raise ValueError("list.items can't be a const")
        check("list.items", self.list.items, item_scope)
        for i, step in enumerate(getattr(self.list, "steps", [])):
            if isinstance(step, FollowStep):
                check(f"list.steps[{i}].link", step.link, "html")
        pg = self.list.pagination
        if isinstance(pg, NextLinkPagination):
            check("list.pagination.link", pg.link, item_scope)
        if isinstance(pg, CursorPagination) and item_scope != "json":
            raise ValueError("cursor pagination needs a json list")
        if isinstance(pg, ParamPagination) and pg.location != "query" and self.list.request.method != "POST":
            raise ValueError(f"pagination in the {pg.location} body needs a POST list request")

        for name, rule in self.fields.rules().items():
            check(f"fields.{name}", rule, scope_of(rule.on))
        if self.fields.id.on != "item":
            raise ValueError(
                "fields.id must be read from the list item (it identifies the tender before any detail fetch)"
            )
        if self.fields.url is None and self.detail is None:
            raise ValueError("fields.url is required when there is no `detail` (it defaults to the detail page URL)")

        if self.detail is not None:
            if self.detail.link is not None:
                check("detail.link", self.detail.link, item_scope)
            if self.detail.url_template is not None:
                names = {f for _, f, _, _ in string.Formatter().parse(self.detail.url_template) if f}
                item_fields = {n for n, r in self.fields.rules().items() if r.on == "item"}
                if missing := names - item_fields:
                    raise ValueError(f"detail.url_template uses fields not read from the item: {sorted(missing)}")
            for i, step in enumerate(self.detail.resolve):
                check(f"detail.resolve[{i}].link", step.link, detail_scope)

        if self.documents is not None:
            scope = scope_of(self.documents.on)
            for part in ("links", "names", "bundle"):
                if (ex := getattr(self.documents, part)) is not None:
                    check(f"documents.{part}", ex, scope)
            for i, step in enumerate(self.documents.per_link):
                check(f"documents.per_link[{i}].link", step.link, "html")

        if self.status is not None:
            check("status.value", self.status.value, scope_of(self.status.value.on))
        return self


def template_json_schema() -> dict[str, Any]:
    """JSON schema of `Template`, for the agent's structured output and docs/engine/template.schema.json."""
    return Template.model_json_schema()


# --------------------------------------------------------------------------- expression checks


def _check_regex(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"invalid regex {pattern!r}: {e}") from e


def _check_expression(by: str, expr: str) -> None:
    if by == "const":
        return
    if not expr.strip():
        raise ValueError(f"empty {by} expression")
    if by == "regex":
        _check_regex(expr)
    elif by == "css":
        from selectolax.lexbor import LexborHTMLParser, SelectolaxError

        try:
            LexborHTMLParser("<p></p>").css(expr)
        except SelectolaxError as e:
            raise ValueError(f"invalid CSS selector {expr!r}") from e
    elif by == "xpath":
        from lxml import etree

        try:
            etree.XPath(expr)
        except etree.XPathSyntaxError as e:
            raise ValueError(f"invalid XPath {expr!r}: {e}") from e
    elif by == "jsonpath":
        from jsonpath_ng.ext import parse

        try:
            parse(expr)
        except Exception as e:  # jsonpath-ng raises several lexer/parser error types
            raise ValueError(f"invalid JSONPath {expr!r}: {e}") from e


if __name__ == "__main__":
    print(json.dumps(template_json_schema(), indent=2, ensure_ascii=False))
