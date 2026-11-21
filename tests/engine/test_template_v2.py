"""Template format v2 (ADR 0014 §2–3): loading, load-time checks, v1 compatibility."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from watchtower.core.pipeline import _dump
from watchtower.engine import load_template
from watchtower.engine.examples import _DIR_V2 as V2_DIR
from watchtower.engine.examples import EXAMPLES, EXAMPLES_V2, load_example, load_example_v2
from watchtower.engine.template import Template
from watchtower.engine.template_v2 import TemplateV2, _site, template_v2_json_schema


def example(name: str) -> dict:
    return json.loads((V2_DIR / f"{name}.json").read_text(encoding="utf-8"))


def broken(name: str, change) -> dict:
    d = copy.deepcopy(example(name))
    change(d)
    return d


def _no_detail(d: dict) -> None:
    d.pop("detail")
    for name in ("description", "cpvCodes", "placeOfPerformance"):
        d["fields"].pop(name)


def test_examples_load_and_dump_round_trip():
    assert EXAMPLES_V2 == ["app_api", "app_click", "tenderned"]
    for name in EXAMPLES_V2:
        t = load_example_v2(name)
        again = load_template(_dump(t))  # what the pipeline stores and reads back
        assert again == t, name
    assert load_example_v2("app_click").needs_browser
    assert not load_example_v2("app_api").needs_browser


def test_load_template_picks_the_format():
    assert all(isinstance(load_template(_dump(load_example(n))), Template) for n in EXAMPLES)
    assert isinstance(load_template(json.dumps(example("app_api"))), TemplateV2)
    assert isinstance(load_template(example("tenderned")), TemplateV2)


def test_jsonpath_shorthand_for_api_items_and_total():
    t = load_example_v2("tenderned")
    assert (t.list.items.by, t.list.items.expr) == ("jsonpath", "$.content[*]")
    assert t.list.total.expr == "$.totalElements"


def test_schema_file_is_current():
    """Regenerate with: uv run python -m watchtower.engine.template_v2 > docs/engine/template-v2.schema.json"""
    path = Path(__file__).parents[2] / "docs" / "engine" / "template-v2.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == template_v2_json_schema()


def test_schema_uses_the_json_names():
    text = json.dumps(template_v2_json_schema())
    assert '"from"' in text and '"in"' in text and '"json"' in text and "from_" not in text


@pytest.mark.parametrize(
    "name, change, message",
    [
        # placeholders
        ("app_api", lambda d: d["list"]["request"]["params"].update(x="{{session.jwt}}"), "names no session capture"),
        ("app_api", lambda d: d["list"]["request"]["params"].update(x="{{now}}"), "unknown placeholder"),
        ("app_api", lambda d: d["list"]["request"]["params"].update(x="{id}"), "only work per tender"),
        ("app_api", lambda d: d["detail"]["request"].update(url="http://localhost:8081/x/{nope}"), "not read from"),
        # credentials
        ("app_api", lambda d: d["list"]["request"]["headers"].update(Cookie="a=b"), "credential headers"),
        # views and scopes
        ("app_click", lambda d: d["documents"].update(on="detail.files"), "has no 'files'"),
        ("app_click", lambda d: d["detail"].update(via="http"), "views need `via: browser`"),
        ("app_api", lambda d: d["fields"]["title"].update(by="css", expr="h1"), "can't read json"),
        ("app_click", lambda d: d["scope"]["include"][0].update(source={"by": "jsonpath", "expr": "$.x"}), "read html"),
        # list
        ("app_click", lambda d: d["list"].update(via="http"), "need `via: browser`"),
        ("app_click", lambda d: d["list"]["items"].update(group={"size": 2, "starts_at": "tr"}), "exactly one of"),
        ("app_api", lambda d: _no_detail(d), "fields.url is required"),
        ("app_api", lambda d: d["detail"].pop("page_url"), "needs `page_url`"),
        # conditions
        ("app_api", lambda d: d["scope"]["include"][0].update(matches="A"), "exactly one of"),
        # hosts
        ("app_api", lambda d: d["detail"]["request"].update(url="https://evil.example/{id}"), "leaves the portal"),
        ("tenderned", lambda d: d["list"]["request"].update(url="https://api.other.nl/x"), "leaves the portal"),
    ],
)
def test_load_time_checks(name, change, message):
    with pytest.raises(ValidationError, match=message):
        TemplateV2.model_validate(broken(name, change))


def test_session_references_and_credential_headers_from_the_session_are_allowed():
    def with_session(d):
        d["session"] = {
            "via": "browser",
            "steps": [{"do": "goto", "url": "http://localhost:8081/app/aankondigingen"}],
            "capture": [
                {"name": "jwt", "from": "request_header", "match": "/app/api/", "key": "accept"},
                {"name": "cookies", "from": "cookie", "match": "localhost", "key": "*"},
            ],
        }
        d["list"]["request"]["headers"].update({"X-JWT": "{{session.jwt}}", "Cookie": "{{session.cookies}}"})

    t = TemplateV2.model_validate(broken("app_api", with_session))
    assert t.needs_browser and t.session.capture[0].from_ == "request_header"


def test_session_steps_must_match_via():
    def bad(d):
        d["session"] = {
            "via": "http",
            "steps": [{"do": "goto", "url": "http://localhost:8081/app/"}],
            "capture": [{"name": "c", "from": "cookie", "match": ".", "key": "*"}],
        }

    with pytest.raises(ValidationError, match="requests for `via: http`"):
        TemplateV2.model_validate(broken("app_api", bad))


@pytest.mark.parametrize(
    "url, site",
    [
        ("https://www.tenderned.nl/x", "tenderned.nl"),
        ("https://papi.tenderned.nl/x", "tenderned.nl"),
        ("https://www.contractsfinder.service.gov.uk/", "service.gov.uk"),
        ("https://a.b.co.uk/", "b.co.uk"),
        ("http://localhost:8081/app/", "localhost"),
        ("http://127.0.0.1:8081/", "127.0.0.1"),
    ],
)
def test_site(url, site):
    assert _site(url) == site
