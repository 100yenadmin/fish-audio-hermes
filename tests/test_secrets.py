import sys
from types import ModuleType

import pytest

from fish_audio import secrets


@pytest.fixture
def scope(monkeypatch):
    module = ModuleType("agent.secret_scope")
    class UnscopedSecretError(Exception):
        pass
    module.UnscopedSecretError = UnscopedSecretError
    monkeypatch.setitem(sys.modules, "agent", ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.secret_scope", module)
    return module


def test_scoped_hit(scope, monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "other-profile")
    scope.get_secret = lambda name: " scoped-key " if name == "FISH_API_KEY" else None
    assert secrets.fish_api_key() == "scoped-key"


def test_multiplex_unscoped_is_closed(scope, monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "other-profile")
    def unscoped(name):
        raise scope.UnscopedSecretError("unscoped")
    scope.get_secret = unscoped
    assert secrets.fish_api_key() == ""


def test_single_profile_env_via_scope(scope, monkeypatch):
    import os
    monkeypatch.setenv("FISH_API_KEY", " env-key ")
    scope.get_secret = lambda name: os.environ.get(name)
    assert secrets.fish_api_key() == "env-key"


def test_absent_hermes_env_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    monkeypatch.setenv("FISH_API_KEY", " env-key ")
    assert secrets.fish_api_key() == "env-key"


def test_missing_scoped_key_never_falls_back(scope, monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "other-profile")
    scope.get_secret = lambda name: None
    assert secrets.fish_api_key() == ""


def test_redact_live_and_pattern(scope):
    scope.get_secret = lambda name: "test-key"
    synthetic = "sk-" + "aB_9-" * 9 + "xyz"
    assert len(synthetic) == 51
    text = secrets.redact(f"live test-key and {synthetic} more")
    assert "test-key" not in text and synthetic not in text


def test_redaction_survives_secret_backend_failure(scope):
    def failed(name):
        raise RuntimeError("synthetic backend failure")
    scope.get_secret = failed
    synthetic = "sk-" + "a" * 48
    assert synthetic not in secrets.redact(synthetic)
