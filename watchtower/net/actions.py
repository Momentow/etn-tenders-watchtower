"""Browser actions (ADR 0014 §1): what a person does on a portal, as data.

One definition for everything that drives a browser: `BrowserSession.act()` executes these, v2 templates embed them
(T-033) and agent tools take them (T-034). There is deliberately no action that runs JavaScript: templates stay data.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROLES = frozenset(
    {
        "button", "link", "tab", "checkbox", "radio", "combobox", "textbox", "searchbox", "option", "menuitem",
        "heading", "listitem", "row", "cell", "navigation", "region", "dialog", "switch", "treeitem",
    }
)  # fmt: skip


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Target(_Strict):
    """Exactly one way to find an element. Prefer role + accessible name: it survives class-name churn."""

    role: str | None = None
    name: str | None = None  # accessible name for `role`; substring, case-insensitive
    text: str | None = None  # visible text; substring, case-insensitive
    css: str | None = None
    xpath: str | None = None
    nth: int = Field(0, ge=0, le=500)  # which match, when several

    @model_validator(mode="after")
    def _one_kind(self) -> Target:
        kinds = [k for k in ("role", "text", "css", "xpath") if getattr(self, k)]
        if len(kinds) != 1:
            raise ValueError("a target needs exactly one of role, text, css, xpath")
        if self.name and not self.role:
            raise ValueError("`name` goes with `role`")
        if self.role and self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}; use one of {sorted(ROLES)}")
        return self

    def describe(self) -> str:
        if self.role:
            what = f"{self.role} {self.name!r}" if self.name else self.role
        elif self.text:
            what = f"text {self.text!r}"
        else:
            what = f"{'css' if self.css else 'xpath'} {self.css or self.xpath}"
        return what + (f" #{self.nth}" if self.nth else "")


class Goto(_Strict):
    do: Literal["goto"]
    url: str = Field(pattern=r"^https?://")


class Click(_Strict):
    do: Literal["click"]
    target: Target


class Fill(_Strict):
    do: Literal["fill"]
    target: Target
    value: str = Field(max_length=500)


class Select(_Strict):
    do: Literal["select"]
    target: Target
    value: str = Field(max_length=500)  # option value or label


class Press(_Strict):
    do: Literal["press"]
    target: Target
    key: str = Field(pattern=r"^[A-Za-z0-9+]{1,30}$")  # Playwright key names: Enter, Tab, ArrowDown…


class WaitFor(_Strict):
    do: Literal["wait_for"]
    target: Target | None = None
    text: str | None = None
    response: str | None = None  # regex on the URL of a network response

    @model_validator(mode="after")
    def _one_kind(self) -> WaitFor:
        if sum(x is not None for x in (self.target, self.text, self.response)) != 1:
            raise ValueError("wait_for needs exactly one of target, text, response")
        return self


class LoadMore(_Strict):
    """Click a "load more" control until it's gone or disabled, nothing new appears, or a limit is reached."""

    do: Literal["load_more"]
    target: Target
    max_clicks: int = Field(20, ge=1, le=500)
    until_count: str | None = None  # css: stop once this many-matching selector stops growing (default: page size)


class Scroll(_Strict):
    do: Literal["scroll"]
    times: int = Field(1, ge=1, le=50)


class Tab(_Strict):
    """Open a tab or accordion section and wait for its content."""

    do: Literal["tab"]
    target: Target


Action = Annotated[Goto | Click | Fill | Select | Press | WaitFor | LoadMore | Scroll | Tab, Field(discriminator="do")]
