import pytest

from watchtower.agent.config import DEFAULT_MODEL, AgentConfigError, load_settings


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AgentConfigError, match="OPENAI_API_KEY"):
        load_settings()


def test_model_defaults_and_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("WATCHTOWER_MODEL", raising=False)
    assert load_settings().model == DEFAULT_MODEL
    monkeypatch.setenv("WATCHTOWER_MODEL", "gpt-other")
    assert load_settings().model == "gpt-other"
