import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from watchtower.engine.examples import EXAMPLES, load_example
from watchtower.engine.template import Template, template_json_schema

SCHEMA_FILE = Path(__file__).parents[2] / "docs" / "engine" / "template.schema.json"

MINIMAL = {
    "timezone": "Europe/Berlin",
    "list": {
        "kind": "html",
        "request": {"url": "https://portal.example/list"},
        "items": {"by": "css", "expr": "li.tender"},
    },
    "fields": {
        "id": {"by": "css", "expr": ".ref"},
        "url": {"by": "css", "expr": "a", "attr": "href"},
    },
}


def with_changes(**changes):
    t = copy.deepcopy(MINIMAL)
    for path, value in changes.items():
        node = t
        *parents, last = path.split("__")
        for p in parents:
            node = node.setdefault(p, {})
        node[last] = value
    return t


def error_of(data) -> str:
    with pytest.raises(ValidationError) as e:
        Template.model_validate(data)
    return str(e.value)


# ---------------------------------------------------------------- examples and schema


def test_every_mock_kind_has_an_example():
    assert set(EXAMPLES) >= {
        "ssr_list_detail",
        "js_json_api",
        "multi_step_download",
        "search_form",
        "captcha_bypassable",
    }


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_validate_and_round_trip(name):
    t = load_example(name)
    again = Template.model_validate_json(t.model_dump_json())
    assert again == t


def test_needs_browser():
    assert load_example("captcha_bypassable").needs_browser
    assert not load_example("js_json_api").needs_browser
    assert not load_example("ssr_list_detail").needs_browser


def test_minimal_template_is_valid_and_defaults_apply():
    t = Template.model_validate(MINIMAL)
    assert t.format_version == 1
    assert t.list.pagination.kind == "none"
    assert t.fields.id.many is False and t.fields.id.on == "item"


def test_checked_in_schema_is_current():
    """Regenerate with: uv run python -m watchtower.engine.template > docs/engine/template.schema.json"""
    assert json.loads(SCHEMA_FILE.read_text()) == template_json_schema()


def test_flattened_field_rules():
    rules = load_example("ssr_list_detail").fields.rules()
    assert "placeOfPerformance.city" in rules and "placeOfPerformance" not in rules
    assert rules["cpvCodes"].many


# ---------------------------------------------------------------- broken templates are rejected at load


@pytest.mark.parametrize(
    "data, message",
    [
        (with_changes(list__items={"by": "css", "expr": "div >"}), "invalid CSS selector"),
        (with_changes(fields__id={"by": "xpath", "expr": "//a[@"}), "invalid XPath"),
        (with_changes(fields__id={"by": "regex", "expr": "(unclosed"}), "invalid regex"),
        (with_changes(fields__id={"by": "css", "expr": ".ref", "pattern": "[a-"}), "invalid regex"),
        (with_changes(fields__id={"by": "jsonpath", "expr": "$.id"}), "`jsonpath` can't read html"),
        (with_changes(fields__title={"by": "css", "expr": "h1", "on": "detail"}), "has no `detail`"),
        (with_changes(fields__id={"by": "css", "expr": ".ref", "on": "detail"}), "has no `detail`"),
        (with_changes(fields__url=None), "fields.url is required"),
        (with_changes(fields__id={"by": "css", "expr": ".ref", "attr": "href", "join": ","}), "`join` needs"),
        (with_changes(list__request__headers={"Cookie": "s=1"}), "credential headers"),
        (with_changes(list__request__url="/relative"), "absolute http"),
        (with_changes(list__request__form={"a": "1"}), "needs method POST"),
        (with_changes(timezone="Mars/Olympus"), "unknown IANA time zone"),
        (with_changes(country="de"), "two upper-case letters"),
        (with_changes(fields__titel={"by": "css", "expr": "h1"}), "Extra inputs are not permitted"),
        (with_changes(list__items={"by": "const", "expr": "x"}), "can't be a const"),
        (
            with_changes(list__pagination={"kind": "cursor", "cursor": {"by": "jsonpath", "expr": "$.n"}, "name": "c"}),
            "cursor pagination needs a json list",
        ),
        (with_changes(list__pagination={"kind": "param", "name": "p", "location": "form"}), "needs a POST"),
        (with_changes(list__pagination={"kind": "param", "name": "p", "max_pages": 0}), "greater than or equal"),
        (with_changes(documents={"on": "item", "links": {"by": "css", "expr": "a", "attr": "href"}}), "many: true"),
        (
            with_changes(documents={"on": "item", "links": {"by": "css", "expr": "a", "many": True}, "include": "(["}),
            "invalid regex",
        ),
        (with_changes(status={"value": {"by": "css", "expr": ".s"}, "withdrawn": ["(?"]}), "invalid regex"),
        (with_changes(list__steps=[{"action": "submit", "form": "form >"}]), "invalid CSS selector"),
        (with_changes(list__steps=[{"action": "click", "link": {"by": "css", "expr": "a"}}]), "action"),
    ],
)
def test_invalid_templates_rejected(data, message):
    assert message in error_of(data)


def test_json_list_rules():
    data = {
        "timezone": "Europe/Paris",
        "list": {
            "kind": "json",
            "request": {"url": "https://portal.example/api"},
            "items": {"by": "jsonpath", "expr": "$.items[*]"},
        },
        "fields": {"id": {"by": "jsonpath", "expr": "$.id"}, "url": {"by": "jsonpath", "expr": "$.url"}},
    }
    Template.model_validate(data)

    bad = copy.deepcopy(data)
    bad["fields"]["title"] = {"by": "css", "expr": "h1"}
    assert "`css` can't read json" in error_of(bad)

    bad = copy.deepcopy(data)
    bad["fields"]["id"] = {"by": "jsonpath", "expr": "$..["}
    assert "invalid JSONPath" in error_of(bad)

    bad = copy.deepcopy(data)
    bad["fields"]["id"] = {"by": "jsonpath", "expr": "$.id", "attr": "href"}
    assert "`attr` only applies" in error_of(bad)


def test_detail_url_sources():
    data = with_changes(detail={"url_template": "https://portal.example/t/{id}"})
    del data["fields"]["url"]
    Template.model_validate(data)

    data["detail"] = {"url_template": "https://portal.example/t/{slug}"}
    assert "not read from the item" in error_of(data)

    data["detail"] = {}
    assert "exactly one of" in error_of(data)

    data["detail"] = {"url_template": "https://portal.example/t/{id}", "format": "json", "render": True}
    assert "`render` only applies" in error_of(data)


def test_detail_scope_follows_detail_format():
    data = with_changes(
        detail={"url_template": "https://portal.example/api/t/{id}", "format": "json"},
        fields__title={"by": "css", "expr": "h1", "on": "detail"},
    )
    assert "`css` can't read json" in error_of(data)
    data["fields"]["title"] = {"by": "jsonpath", "expr": "$.title", "on": "detail"}
    Template.model_validate(data)
