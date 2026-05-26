import pytest

from speclint.llm import (
    TransportNotAvailable,
    probe_transports,
    select_transport,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def test_auto_returns_none_when_nothing_available(monkeypatch):
    # Force CLI probes to fail by using binaries that won't exist
    chosen = select_transport("auto", claude_bin="nope-claude", codex_bin="nope-codex")
    assert chosen is None


def test_auto_prefers_anthropic_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    chosen = select_transport("auto", claude_bin="nope", codex_bin="nope")
    assert chosen == "api"


def test_auto_falls_back_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    chosen = select_transport("auto", claude_bin="nope", codex_bin="nope")
    assert chosen == "openai"


def test_auto_supports_foundry_via_openai_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foundry.example/openai/")
    chosen = select_transport("auto", claude_bin="nope", codex_bin="nope")
    assert chosen == "openai"


def test_explicit_api_raises_without_key(monkeypatch):
    with pytest.raises(TransportNotAvailable):
        select_transport("api")


def test_explicit_openai_raises_without_key_or_base(monkeypatch):
    with pytest.raises(TransportNotAvailable):
        select_transport("openai")


def test_explicit_cli_raises_when_binary_missing(monkeypatch):
    with pytest.raises(TransportNotAvailable):
        select_transport("cli", claude_bin="nope-claude")


def test_explicit_codex_raises_when_binary_missing(monkeypatch):
    with pytest.raises(TransportNotAvailable):
        select_transport("codex", codex_bin="nope-codex")


def test_probe_reports_each_transport(monkeypatch):
    probes = probe_transports(claude_bin="nope", codex_bin="nope")
    names = {p.name for p in probes}
    assert names == {"api", "openai", "cli", "codex"}
