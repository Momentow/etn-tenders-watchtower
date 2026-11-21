"""Template format v2 (ADR 0014 §2–3): v1's extraction plus browser flows, API mode, sessions, scope and row groups.

`format_version: 2`. v1 templates (`watchtower.engine.template.Template`) keep their meaning and keep running;
`load_template()` picks the class by `format_version`, and `run_template()` executes either.

What v2 adds over v1:
- `entry`: where reading starts, possibly deeper than the portal URL.
- `session`: anonymous state a read needs (a cookie, a token the portal's own page issues), obtained on every read and
  referenced as `{{session.<name>}}` in requests. Never stored.
- `list.kind: api` (JSON endpoints, plain HTTP) or `list.kind: page` (browser `flow` actions, then items on the page,
  with click / load-more / scroll pagination and `items.group` for tenders spread over sibling rows).
- `detail.kind: api` or `page` (with `views`: actions such as switching to a tab, whose page then is the scope
  `detail.<view>` for fields and documents).
- `scope`: which records are open tenders; `total`: the portal's own count (for completeness checks, T-036).
- `urls`: normalisers for volatile URL parts.

Placeholders in requests: `{{session.<name>}}` (a captured session value), `{{today}}` (today's date in the template's
time zone, `YYYY-MM-DD`, for "deadline from today" filters) and, in detail and document requests, `{field}` (an item
field's value, e.g. `{id}`).

Run `python -m watchtower.engine.template_v2` to print the JSON schema.
"""

from __future__ import annotations

import json
import re
import string
from ipaddress import ip_address
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from watchtower.engine.template import (
    _BY_FOR_SCOPE,
    _FORBIDDEN_HEADERS,
    CursorPagination,
    DateRule,
    Extract,
    FieldRule,
    Fields,
    FollowStep,
    NextLinkPagination,
    NoPagination,
    ParamPagination,
    PlaceRules,
    Scope,
    Template,
    _check_regex,
)
from watchtower.net.actions import Action, Goto, Target

FORMAT_VERSION = 2

# Where a rule runs: the list item, the detail page, or one of the detail page's `views` (`detail.documents`).
ON_PATTERN = r"^(item|detail|detail\.[A-Za-z][A-Za-z0-9_]*)$"
_DOUBLE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_.]*)\s*\}\}")
_SINGLE = re.compile(r"(?<!\{)\{([A-Za-z][A-Za-z0-9_.]*)\}(?!\})")
_CAPTURE_NAME = r"^[A-Za-z][A-Za-z0-9_]*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _jsonpath_shorthand(v: Any) -> Any:
    """`"$.content[*]"` is short for `{"by": "jsonpath", "expr": "$.content[*]"}` (ADR 0014 examples)."""
    return {"by": "jsonpath", "expr": v} if isinstance(v, str) else v


# --------------------------------------------------------------------------- field rules (`on` gains views)


class FieldRuleV2(FieldRule):
    on: str = Field("item", pattern=ON_PATTERN)  # type: ignore[assignment]


class DateRuleV2(DateRule):
    on: str = Field("item", pattern=ON_PATTERN)  # type: ignore[assignment]


class PlaceRulesV2(PlaceRules):
    postalCode: FieldRuleV2 | None = None  # noqa: N815
    city: FieldRuleV2 | None = None
    countryCode: FieldRuleV2 | None = None  # noqa: N815


class FieldsV2(Fields):
    id: FieldRuleV2
    url: FieldRuleV2 | None = None
    title: FieldRuleV2 | None = None
    buyerName: FieldRuleV2 | None = None  # noqa: N815
    publicationDate: DateRuleV2 | None = None  # noqa: N815
    submissionDeadline: DateRuleV2 | None = None  # noqa: N815
    referenceNumber: FieldRuleV2 | None = None  # noqa: N815
    description: FieldRuleV2 | None = None
    procedureType: FieldRuleV2 | None = None  # noqa: N815
    contractNature: FieldRuleV2 | None = None  # noqa: N815
    cpvCodes: FieldRuleV2 | None = None  # noqa: N815
    placeOfPerformance: PlaceRulesV2 | None = None  # noqa: N815


# --------------------------------------------------------------------------- requests


class RequestV2(_Strict):
    """An HTTP request. Strings may hold placeholders (module docstring). A `Cookie`/`Authorization` header is allowed
    only when its value comes from the session (`{{session.x}}`): templates never carry credentials."""

    url: str
    method: Literal["GET", "POST"] = "GET"
    params: dict[str, str] = {}
    headers: dict[str, str] = {}
    form: dict[str, str] | None = None
    json_body: Any | None = Field(None, alias="json")

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not re.match(r"^https?://[^/{}\s]+", v):
            raise ValueError("url must be absolute http(s) with a literal host")
        return v

    @field_validator("headers")
    @classmethod
    def _no_credentials(cls, v: dict[str, str]) -> dict[str, str]:
        bad = sorted(k for k, val in v.items() if k.lower() in _FORBIDDEN_HEADERS and "{{session." not in val)
        if bad:
            raise ValueError(f"credential headers need a {{{{session.*}}}} value, not a literal: {bad}")
        return v

    @model_validator(mode="after")
    def _one_body(self) -> RequestV2:
        if self.form is not None and self.json_body is not None:
            raise ValueError("give `form` or `json`, not both")
        if (self.form is not None or self.json_body is not None) and self.method == "GET":
            raise ValueError("a request body needs method POST")
        return self

    def strings(self) -> list[str]:
        """Every string that may hold placeholders."""
        out = [self.url, *self.params.values(), *self.headers.values(), *(self.form or {}).values()]
        out.extend(_json_strings(self.json_body))
        return out


def _json_strings(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _json_strings(x)]
    if isinstance(v, list):
        return [s for x in v for s in _json_strings(x)]
    return []


# --------------------------------------------------------------------------- entry and session


class Entry(_Strict):
    url: str = Field(pattern=r"^https?://")


class Capture(_Strict):
    """One session value. `from`:
    - `cookie`: a cookie whose domain matches `match`; `key` is its name, or `*` for all of them as a `Cookie` header;
    - `request_header` (browser): a header the page's own request to a URL matching `match` carried (`key`: its name);
    - `response_header` (http): a header of the response from a URL matching `match`;
    - `response_json`: a JSONPath `key` in the response from a URL matching `match`.
    """

    name: str = Field(pattern=_CAPTURE_NAME)
    from_: Literal["cookie", "request_header", "response_header", "response_json"] = Field(alias="from")
    match: str
    key: str

    @model_validator(mode="after")
    def _check(self) -> Capture:
        _check_regex(self.match)
        if self.from_ == "response_json":
            from watchtower.engine.template import _check_expression

            _check_expression("jsonpath", self.key)
        return self


class Session(_Strict):
    """Anonymous state a read needs, obtained fresh on every read (ADR 0014 §2). `via: browser` runs `steps` (actions)
    in the read's browser session; `via: http` sends `steps` (requests) with the polite client."""

    via: Literal["http", "browser"]
    steps: list[Action] | list[RequestV2] = Field(min_length=1)
    capture: list[Capture] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Session:
        browser_steps = all(not isinstance(s, RequestV2) for s in self.steps)
        if (self.via == "browser") != browser_steps:
            raise ValueError("session.steps are actions for `via: browser` and requests for `via: http`")
        for c in self.capture:
            if self.via == "browser" and c.from_ == "response_header":
                raise ValueError(f"capture {c.name!r}: `response_header` needs `via: http`")
            if self.via == "http" and c.from_ == "request_header":
                raise ValueError(f"capture {c.name!r}: `request_header` needs `via: browser`")
        names = [c.name for c in self.capture]
        if len(set(names)) != len(names):
            raise ValueError("session.capture names must be unique")
        return self


# --------------------------------------------------------------------------- list


class ItemGroup(_Strict):
    """One tender spread over sibling elements: every `size` matches form one tender, or each match of `starts_at`
    (css, tested on the matched element itself) starts a new tender that runs until the next one."""

    size: int | None = Field(None, ge=2, le=50)
    starts_at: str | None = None

    @model_validator(mode="after")
    def _one(self) -> ItemGroup:
        if (self.size is None) == (self.starts_at is None):
            raise ValueError("items.group needs exactly one of `size` or `starts_at`")
        if self.starts_at is not None:
            from watchtower.engine.template import _check_expression

            _check_expression("css", self.starts_at)
        return self


class PageItems(Extract):
    """css/xpath selecting one element per tender (or per row, with `group`)."""

    group: ItemGroup | None = None


class ClickPagination(_Strict):
    """Click a "next page" control and read the new page, until it's gone or a page brings no new tenders."""

    kind: Literal["click"]
    target: Target
    max_pages: int = Field(50, ge=1, le=500)


class LoadMorePagination(_Strict):
    """Click "load more" until it's gone or disabled, nothing new appears, or `max_clicks`; then read the page once."""

    kind: Literal["load_more"]
    target: Target
    max_clicks: int = Field(50, ge=1, le=500)


class ScrollPagination(_Strict):
    """Infinite list: scroll to the bottom `times` times, then read the page once."""

    kind: Literal["scroll"]
    times: int = Field(10, ge=1, le=50)


ApiPagination = Annotated[
    NoPagination | NextLinkPagination | ParamPagination | CursorPagination, Field(discriminator="kind")
]
PagePagination = Annotated[
    NoPagination | NextLinkPagination | ParamPagination | ClickPagination | LoadMorePagination | ScrollPagination,
    Field(discriminator="kind"),
]


class ApiList(_Strict):
    """Tender list from JSON endpoints over plain HTTP: no browser on regular reads."""

    kind: Literal["api"]
    request: RequestV2
    items: Extract  # jsonpath to the tender objects
    total: Extract | None = None  # jsonpath to the portal's own count (`$.totalElements`)
    pagination: ApiPagination = NoPagination(kind="none")

    @field_validator("items", "total", mode="before")
    @classmethod
    def _shorthand(cls, v: Any) -> Any:
        return _jsonpath_shorthand(v)


class PageList(_Strict):
    """Tender list on a page. `via: browser` (default): open `entry` in the read's browser session and run `flow`;
    `via: http`: fetch `entry` and read it (then `flow` must be empty)."""

    kind: Literal["page"]
    via: Literal["browser", "http"] = "browser"
    flow: list[Action] = []
    items: PageItems
    total: Extract | None = None  # on the whole page, e.g. ".result-count" with a pattern
    pagination: PagePagination = NoPagination(kind="none")


ListV2 = Annotated[ApiList | PageList, Field(discriminator="kind")]


# --------------------------------------------------------------------------- detail and documents


class ApiDetail(_Strict):
    """One JSON request per tender, with `{field}` placeholders from item fields. `page_url` is the tender's public
    page (`https://portal/…/{id}`), used as its `url` unless `fields.url` is given."""

    kind: Literal["api"]
    request: RequestV2
    page_url: str | None = None


class PageDetail(_Strict):
    """The tender's page, from `link` (on the item) or `url_template` (`{field}` placeholders). `via: browser` loads it
    in the read's browser session; then `views` name action lists (switch to a tab…) whose resulting page is the scope
    `detail.<view>`. `resolve` (http) hops from a summary page to the real tender page."""

    kind: Literal["page"]
    link: Extract | None = None
    url_template: str | None = None
    via: Literal["http", "browser"] = "http"
    views: dict[str, list[Action]] = {}
    resolve: list[FollowStep] = []

    @model_validator(mode="after")
    def _check(self) -> PageDetail:
        if (self.link is None) == (self.url_template is None):
            raise ValueError("give exactly one of `link` or `url_template`")
        if self.views and self.via != "browser":
            raise ValueError("detail.views need `via: browser`")
        if self.resolve and self.via != "http":
            raise ValueError("detail.resolve needs `via: http`")
        for name, actions in self.views.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                raise ValueError(f"view name {name!r} must be a plain identifier")
            if not actions:
                raise ValueError(f"view {name!r} has no actions")
        return self


DetailV2 = Annotated[ApiDetail | PageDetail, Field(discriminator="kind")]


class DocumentsV2(_Strict):
    """As v1 `documents`, plus `on: detail.<view>` and `request`: a JSON call (`{field}` placeholders) returning the
    file list, which is then the scope of `links`/`names`/`bundle`."""

    on: str = Field("detail", pattern=ON_PATTERN)
    request: RequestV2 | None = None
    links: Extract
    names: Extract | None = None
    bundle: Extract | None = None
    include: str | None = None
    exclude: str | None = None
    per_link: list[FollowStep] = []

    @model_validator(mode="after")
    def _check(self) -> DocumentsV2:
        if not self.links.many:
            raise ValueError("documents.links needs `many: true`")
        if self.names is not None and not self.names.many:
            raise ValueError("documents.names needs `many: true`")
        for rx in (self.include, self.exclude):
            if rx is not None:
                _check_regex(rx)
        return self


class StatusRuleV2(_Strict):
    value: FieldRuleV2
    withdrawn: list[str] = []
    closed: list[str] = []

    @model_validator(mode="after")
    def _regexes(self) -> StatusRuleV2:
        for rx in [*self.withdrawn, *self.closed]:
            _check_regex(rx)
        return self


# --------------------------------------------------------------------------- scope and urls


class Condition(_Strict):
    """A test on a list item: `source` read from the item, then exactly one of `in` (any of the values), `matches`
    (regex search) or `equals`. Values compare as text; JSON booleans read as `true`/`false`."""

    source: Extract
    in_: list[str | int | float | bool] | None = Field(None, alias="in")
    matches: str | None = None
    equals: str | int | float | bool | None = None

    @model_validator(mode="after")
    def _one(self) -> Condition:
        given = [k for k in ("in_", "matches", "equals") if getattr(self, k) is not None]
        if len(given) != 1:
            raise ValueError("a condition needs exactly one of `in`, `matches`, `equals`")
        if self.matches is not None:
            _check_regex(self.matches)
        if self.source.by == "const":
            raise ValueError("a condition's source can't be a const")
        return self


class ScopeRules(_Strict):
    """Which records are open tenders. A record is kept when it meets every `include` condition (a missing value
    doesn't meet it) and no `exclude` condition. `open_by_deadline` drops records whose deadline has passed.

    Prefer the API's own filters (query parameters on `list.request`) and use `scope` for what they can't express."""

    include: list[Condition] = []
    exclude: list[Condition] = []
    open_by_deadline: bool = True


class UrlRules(_Strict):
    """Volatile URL parts removed from tender URLs and document links."""

    drop_params: list[str] = []  # query parameters, e.g. ["sid", "cHash"]
    drop_matrix: list[str] = ["jsessionid"]  # `;name=value` path parameters
    drop_numeric_query: bool = False  # Wicket page versions: `?4`, `?4-1.ILinkListener-…`


# --------------------------------------------------------------------------- template


class TemplateV2(_Strict):
    format_version: Literal[2]
    timezone: str
    country: str | None = None
    software: str | None = None
    entry: Entry
    session: Session | None = None
    list: ListV2
    detail: DetailV2 | None = None
    fields: FieldsV2
    documents: DocumentsV2 | None = None
    status: StatusRuleV2 | None = None
    scope: ScopeRules = ScopeRules()
    urls: UrlRules = UrlRules()
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
        return (
            (self.session is not None and self.session.via == "browser")
            or (self.list.kind == "page" and self.list.via == "browser")
            or (isinstance(self.detail, PageDetail) and self.detail.via == "browser")
        )

    def requests(self) -> list[tuple[str, RequestV2]]:
        out: list[tuple[str, RequestV2]] = []
        if self.session is not None and self.session.via == "http":
            out += [(f"session.steps[{i}]", s) for i, s in enumerate(self.session.steps)]  # type: ignore[misc]
        if isinstance(self.list, ApiList):
            out.append(("list.request", self.list.request))
        if isinstance(self.detail, ApiDetail):
            out.append(("detail.request", self.detail.request))
        if self.documents is not None and self.documents.request is not None:
            out.append(("documents.request", self.documents.request))
        return out

    @model_validator(mode="after")
    def _cross_checks(self) -> TemplateV2:
        item_scope: Scope = "json" if self.list.kind == "api" else "html"
        detail_scope: Scope | None = None if self.detail is None else ("json" if self.detail.kind == "api" else "html")
        views = set(self.detail.views) if isinstance(self.detail, PageDetail) else set()
        item_fields = {n for n, r in self.fields.rules().items() if r.on == "item"}

        def scope_of(where: str, on: str) -> Scope:
            if on == "item":
                return item_scope
            if detail_scope is None:
                raise ValueError(f"{where} runs on the detail page, but the template has no `detail`")
            if on != "detail" and on.removeprefix("detail.") not in views:
                raise ValueError(f"{where} runs on `{on}`, but detail.views has no {on.removeprefix('detail.')!r}")
            return detail_scope

        def check(where: str, ex: Extract, scope: Scope) -> None:
            if ex.by not in _BY_FOR_SCOPE[scope]:
                allowed = ", ".join(sorted(_BY_FOR_SCOPE[scope]))
                raise ValueError(f"{where}: `{ex.by}` can't read {scope}; use one of {allowed}")

        def placeholders(where: str, strings: list[str], *, item: bool) -> None:
            captures = {c.name for c in self.session.capture} if self.session else set()
            for s in strings:
                for name in _DOUBLE.findall(s):
                    if name == "today":
                        continue
                    if not name.startswith("session."):
                        raise ValueError(f"{where}: unknown placeholder {{{{{name}}}}}; use session.<name> or today")
                    if name.removeprefix("session.") not in captures:
                        raise ValueError(f"{where}: {{{{{name}}}}} names no session capture")
                singles = set(_SINGLE.findall(s))
                if singles and not item:
                    raise ValueError(f"{where}: item placeholders {sorted(singles)} only work per tender")
                if missing := singles - item_fields:
                    raise ValueError(f"{where} uses fields not read from the item: {sorted(missing)}")

        # list
        lst = self.list
        if lst.items.by == "const":
            raise ValueError("list.items can't be a const")
        check("list.items", lst.items, item_scope)
        if lst.total is not None:
            check("list.total", lst.total, item_scope)
        pg = lst.pagination
        if isinstance(pg, NextLinkPagination):
            check("list.pagination.link", pg.link, item_scope)
        if isinstance(lst, ApiList):
            placeholders("list.request", lst.request.strings(), item=False)
            if isinstance(pg, ParamPagination) and pg.location != "query" and lst.request.method != "POST":
                raise ValueError(f"pagination in the {pg.location} body needs a POST list request")
        else:
            if isinstance(pg, CursorPagination):
                raise ValueError("cursor pagination needs an api list")
            if isinstance(pg, ParamPagination) and pg.location != "query":
                raise ValueError("a page list can only page with a query parameter")
            browser_only = isinstance(pg, ClickPagination | LoadMorePagination | ScrollPagination)
            if lst.via == "http" and (lst.flow or browser_only):
                raise ValueError("list.flow and click/load_more/scroll pagination need `via: browser`")

        # fields, detail, documents, status, scope
        for name, rule in self.fields.rules().items():
            check(f"fields.{name}", rule, scope_of(f"fields.{name}", rule.on))
        if self.fields.id.on != "item":
            raise ValueError("fields.id must be read from the list item (it identifies the tender before any detail)")
        if self.fields.url is None:
            if self.detail is None:
                raise ValueError("fields.url is required when there is no `detail`")
            if isinstance(self.detail, ApiDetail) and self.detail.page_url is None:
                raise ValueError("an api detail needs `page_url` or `fields.url`: the API URL isn't the tender's page")

        if isinstance(self.detail, ApiDetail):
            placeholders("detail.request", self.detail.request.strings(), item=True)
            if self.detail.page_url is not None:
                placeholders("detail.page_url", [self.detail.page_url], item=True)
        elif isinstance(self.detail, PageDetail):
            if self.detail.link is not None:
                check("detail.link", self.detail.link, item_scope)
            if self.detail.url_template is not None:
                names = {f for _, f, _, _ in string.Formatter().parse(self.detail.url_template) if f}
                if missing := names - item_fields:
                    raise ValueError(f"detail.url_template uses fields not read from the item: {sorted(missing)}")
            for i, step in enumerate(self.detail.resolve):
                check(f"detail.resolve[{i}].link", step.link, "html")

        if self.documents is not None:
            docs = self.documents
            if docs.request is not None:
                placeholders("documents.request", docs.request.strings(), item=True)
                scope: Scope = "json"
            else:
                scope = scope_of("documents", docs.on)
            for part in ("links", "names", "bundle"):
                if (ex := getattr(docs, part)) is not None:
                    check(f"documents.{part}", ex, scope)
            for i, step in enumerate(docs.per_link):
                check(f"documents.per_link[{i}].link", step.link, "html")

        if self.status is not None:
            check("status.value", self.status.value, scope_of("status.value", self.status.value.on))
        for kind in ("include", "exclude"):
            for i, cond in enumerate(getattr(self.scope, kind)):
                check(f"scope.{kind}[{i}].source", cond.source, item_scope)

        self._check_hosts()
        return self

    def _check_hosts(self) -> None:
        """Every request stays on the portal's site (the entry's registrable domain) or a site the session's own flow
        opens (ADR 0014 §3)."""
        sites = {_site(self.entry.url)}
        if self.session is not None:
            for s in self.session.steps:
                url = s.url if isinstance(s, RequestV2 | Goto) else None
                if url:
                    sites.add(_site(url))
        urls: list[tuple[str, str]] = [(where, r.url) for where, r in self.requests()]
        if isinstance(self.list, PageList):
            urls += [(f"list.flow[{i}]", a.url) for i, a in enumerate(self.list.flow) if isinstance(a, Goto)]
        if isinstance(self.detail, ApiDetail) and self.detail.page_url:
            urls.append(("detail.page_url", self.detail.page_url))
        if isinstance(self.detail, PageDetail) and self.detail.url_template:
            urls.append(("detail.url_template", self.detail.url_template))
        for where, url in urls:
            if _site(url) not in sites:
                host = urlsplit(url).hostname
                raise ValueError(f"{where} leaves the portal's site ({host} is not on {sorted(sites)})")


AnyTemplate = Template | TemplateV2


def load_template(data: AnyTemplate | dict[str, Any] | str | bytes) -> AnyTemplate:
    """A template of either format, by `format_version` (missing → 1). Raises pydantic's ValidationError."""
    if isinstance(data, Template | TemplateV2):
        return data
    if isinstance(data, str | bytes):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError("a template is a JSON object")
    if data.get("format_version") == 2:
        return TemplateV2.model_validate(data)
    return Template.model_validate(data)


def template_v2_json_schema() -> dict[str, Any]:
    return TemplateV2.model_json_schema(by_alias=True)


def _site(url: str) -> str:
    """The registrable domain, roughly: `www.tenderned.nl` → `tenderned.nl`, `x.gov.uk` → `x.gov.uk`; IPs and
    single-label hosts (localhost) as they are."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    try:
        ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    # Second-level public suffixes such as co.uk, gov.pl, com.es: keep three labels.
    if len(labels[-1]) == 2 and labels[-2] in {"co", "com", "gov", "gob", "org", "net", "ac", "edu", "gv", "or"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


if __name__ == "__main__":
    print(json.dumps(template_v2_json_schema(), indent=2, ensure_ascii=False))
