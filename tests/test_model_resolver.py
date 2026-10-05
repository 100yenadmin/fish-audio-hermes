from decimal import Decimal
import sys
from types import ModuleType

import pytest
import respx

from fish_audio import account, settings

BASE = "https://api.fish.audio"


@pytest.fixture
def config(monkeypatch):
    data = {"tts": {"fish-audio": {}}, "plugins": {"entries": {"fish-audio": {"settings": {}}}}}
    module = ModuleType("hermes_cli.config")
    module.load_config = lambda: data
    monkeypatch.setitem(sys.modules, "hermes_cli", ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.config", module)
    monkeypatch.setattr(settings._notice_logger, "_fish_notice_logged", False, raising=False)
    return data["tts"]["fish-audio"], data["plugins"]["entries"]["fish-audio"]["settings"]


@pytest.mark.parametrize("nested,call,allow,expected", [
    ("s1", "s2-pro", True, "s1"), ("bad", "s2-pro", True, "s2-pro"),
    (None, "s2.1-pro-free", False, "s2.1-pro"), (None, None, False, "s2.1-pro"),
])
def test_decided_models_do_not_request_wallet(config, monkeypatch, nested, call, allow, expected):
    config[0]["model"] = nested
    config[1]["allow_free_model"] = allow
    def forbidden(*args):
        raise AssertionError("wallet must not be requested")
    monkeypatch.setattr(settings, "cached_wallet", forbidden)
    with respx.mock(assert_all_called=True) as mock:
        assert settings.resolve_model(call, key="test-key", base_url=BASE) == (expected, call is None)
        assert not mock.calls


@pytest.mark.parametrize("wallet,expected", [
    (None, "s2.1-pro"),
    (account.Wallet(Decimal("0.01"), Decimal(0), False), "s2.1-pro"),
    (account.Wallet(Decimal(0), Decimal(10), False), "s2.1-pro"),
    (account.Wallet(Decimal(0), Decimal(0), True), "s2.1-pro"),
    (account.Wallet(Decimal(0), Decimal(0), False), "s2.1-pro-free"),
    (account.Wallet(Decimal(0), Decimal(0), None), "s2.1-pro-free"),
])
def test_wallet_decision_branches(config, monkeypatch, wallet, expected):
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: wallet)
    assert settings.resolve_model("not-a-model", key="test-key", base_url=BASE) == (expected, True)


def test_managed_empty_wallet_pro_and_no_lookup(config, monkeypatch):
    config[1]["allow_free_model"] = False
    calls = []
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: calls.append(args) or account.Wallet(Decimal(0), Decimal(0), False))
    assert settings.resolve_model(None, key="test-key", base_url=BASE) == ("s2.1-pro", True)
    assert not calls


def test_notice_once_and_tts_uses_one_resolver(config, monkeypatch, caplog):
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: account.Wallet(Decimal(0), Decimal(0), False))
    for _ in range(2):
        params, _ = settings.resolve_tts(None, None, None, "mp3", "out.mp3", key="test-key")
        assert params["model"] == "s2.1-pro-free" and params["model_defaulted"]
    assert [r.message for r in caplog.records] == [settings.FREE_MODEL_NOTICE]


def test_resolver_without_key_has_no_network(config, monkeypatch):
    def forbidden(*args):
        raise AssertionError("wallet must not be requested")
    monkeypatch.setattr(settings, "cached_wallet", forbidden)
    assert settings.resolve_model(None, key="", base_url=BASE) == ("s2.1-pro", True)


def test_explicit_nested_free_is_forbidden_once_per_process(config, monkeypatch, caplog):
    config[0]["model"] = "s2.1-pro-free"
    config[1]["allow_free_model"] = False
    monkeypatch.setattr(settings._notice_logger, "_fish_policy_logged", False, raising=False)
    for _ in range(2):
        assert settings.resolve_model("s1", key="test-key", base_url=BASE) == ("s2.1-pro", False)
    assert [r.message for r in caplog.records] == [
        "allow_free_model is false; using s2.1-pro instead of s2.1-pro-free"]
