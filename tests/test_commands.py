import copy
from decimal import Decimal
from pathlib import Path
import sys
from types import ModuleType

import pytest
import respx

from fish_audio import account, commands, media, settings, state

BASE = "https://api.fish.audio"
VOICE = "a" * 32


@pytest.fixture
def config(monkeypatch, tmp_path):
    data, saves = {"tts": {}, "stt": {}}, []
    module = ModuleType("hermes_cli.config")
    module.read_raw_config = lambda: copy.deepcopy(data)
    def save(cfg, **kwargs):
        assert kwargs == {"strip_defaults": False}
        saves.append(copy.deepcopy(cfg))
        data.clear()
        data.update(copy.deepcopy(cfg))
    module.save_config = save
    module.is_managed = lambda: False
    parent = ModuleType("hermes_cli")
    parent.config = module
    monkeypatch.setitem(sys.modules, "hermes_cli", parent)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", module)
    monkeypatch.setattr(settings, "_config", lambda: data)
    monkeypatch.setattr(commands, "fish_api_key", lambda: "test-key")
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    account._wallet_cache.clear()
    monkeypatch.setattr(state, "_last", None)
    return data, saves, module


def wallet_routes(mock):
    mock.get(BASE + "/wallet/self/api-credit?check_free_credit=true").respond(
        json={"credit": "2.5", "cumulative_top_up": "10", "has_free_credit": False})
    mock.get(BASE + "/wallet/self/package").respond(json={"type": "plus", "balance": 20, "total": 30, "finished_at": "date"})


@pytest.mark.parametrize("raw", ["", "status", "voices", "use " + VOICE, "model s1", "preview " + VOICE, "balance"])
def test_no_key_any_non_help_command(config, monkeypatch, raw):
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    with respx.mock(assert_all_called=True) as mock:
        assert commands.handle(raw) == commands.NO_KEY
        assert not mock.calls


def test_help_and_key_shaped_arguments_never_echo(config, monkeypatch):
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    assert "preview" in commands.handle("help")
    token = "sk-" + "x" * 48
    for prefix in ("use ", "voices ", "help ", "preview " + VOICE + " "):
        result = commands.handle(prefix + token)
        assert result == commands.KEY_IN_CHAT and token not in result


@pytest.mark.parametrize("provider,expected", [(None, "fish-audio"), ("", "fish-audio"),
    ("fish-audio", "fish-audio"), ("evaos-fishaudio", "evaos-fishaudio"), ("elevenlabs", "elevenlabs")])
def test_use_keeps_fish_provider_and_only_sets_unset(config, provider, expected):
    data, saves, _ = config
    data["tts"]["provider"] = provider
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={"_id": VOICE})
        result = commands.handle("use " + VOICE)
    assert data["tts"]["provider"] == expected and data["tts"]["fish-audio"]["voice"] == VOICE
    assert len(saves) == 1
    assert ("Switch with" in result) == (provider == "elevenlabs")


def managed_layer(monkeypatch, layer):
    module = ModuleType("hermes_cli.managed_scope")
    module.load_managed_config = lambda: copy.deepcopy(layer)
    sys.modules["hermes_cli"].managed_scope = module
    monkeypatch.setitem(sys.modules, "hermes_cli.managed_scope", module)


@pytest.mark.parametrize("pinned,reply", [("evaos-fishaudio", "Saved."), ("elevenlabs", "provider is elevenlabs")])
def test_use_never_writes_a_provider_when_a_managed_layer_sets_one(config, monkeypatch, pinned, reply):
    data, saves, _ = config
    # The profile layer has no provider; the managed layer (the evaOS overlay) pins one.
    managed_layer(monkeypatch, {"tts": {"provider": pinned}})
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"provider": pinned}})
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={"_id": VOICE})
        result = commands.handle("use " + VOICE)
    assert reply in result
    assert len(saves) == 1 and "provider" not in saves[0]["tts"] and saves[0]["tts"]["fish-audio"]["voice"] == VOICE


@pytest.mark.parametrize("layer", [{}, {"tts": {"voice": "x"}}, {"tts": {"provider": ""}}, {"tts": {"provider": 3}}])
def test_use_sets_fish_when_only_hermes_default_is_in_effect(config, monkeypatch, layer):
    data, saves, _ = config
    # Hermes's merged config always carries its default provider; with no managed pin Use still selects Fish.
    managed_layer(monkeypatch, layer)
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"provider": "edge"}})
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={"_id": VOICE})
        assert commands.handle("use " + VOICE) == "Saved."
    assert saves[0]["tts"]["provider"] == "fish-audio"


@pytest.mark.parametrize("status", [400, 404])
def test_use_not_found_or_managed_does_not_write(config, status):
    _, saves, _ = config
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/model/" + VOICE).respond(status)
        assert "Browse voices" in commands.handle("use " + VOICE)
    assert not saves


def test_managed_write_refused(config):
    _, saves, module = config
    module.is_managed = lambda: True
    assert "managed" in commands.handle("model s1") and not saves


def test_model_selection_and_unknown_rejected(config):
    data, saves, _ = config
    assert "Unknown" in commands.handle("model unknown") and not saves
    assert commands.handle("model s2.1-pro-free") == "Saved.\n" + settings.FREE_MODEL_NOTICE
    assert data["tts"]["fish-audio"]["model"] == "s2.1-pro-free"


def test_model_free_selection_under_paid_policy_is_honest(config):
    data = config[0]
    data["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": False}}}}
    with respx.mock(assert_all_called=True) as mock:
        reply = commands.handle("model s2.1-pro-free")
        assert reply == "Saved. This profile's policy uses paid s2.1-pro instead of s2.1-pro-free."
        assert settings.FREE_MODEL_NOTICE not in reply and not mock.calls
    assert data["tts"]["fish-audio"]["model"] == "s2.1-pro-free"
    assert settings.resolve_model(None, key="test-key", base_url=BASE) == ("s2.1-pro", False)


@pytest.mark.parametrize("allowed,expected", [(False, "s2.1-pro"), (True, "s2.1-pro-free")])
def test_chat_status_names_effective_model(config, allowed, expected):
    data = config[0]
    data["tts"]["fish-audio"] = {"model": "s2.1-pro-free"}
    data["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": allowed}}}}
    with respx.mock(assert_all_called=True) as mock:
        wallet_routes(mock)
        output = commands.handle("status")
        assert output.split("Model: ", 1)[1].split()[0] == expected
        assert expected == settings.resolve_model(None, key="test-key", base_url=BASE)[0]
        assert settings.FREE_MODEL_NOTICE not in output


def test_voices_top_five_and_status_balance(config):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(BASE + "/model").respond(json={"items": [{"_id": VOICE, "title": "Warm", "languages": ["en"]}], "total": 1})
        assert f"{VOICE} · Warm · en" in commands.handle("voices warm")
        assert route.calls.last.request.url.params["page_size"] == "5"
        wallet_routes(mock)
        status = commands.handle("status")
        assert len(status.splitlines()) <= 12 and "Key set: yes" in status and "paid account" in status
        assert "API credit: 2.5" in status and "Plan: plus" in status and "test-key" not in status
        balance = commands.handle("balance")
        assert "20/30" in balance and "date" in balance and "separate from API credits" in balance


def test_preview_native_voice_reply_and_bound(config):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/wallet/self/api-credit?check_free_credit=true").respond(
            json={"credit": "1", "cumulative_top_up": "0", "has_free_credit": False})
        route = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        reply = commands.handle("preview " + VOICE)
        assert reply.startswith("[[audio_as_voice]]\nMEDIA:")
        assert Path(reply.split("MEDIA:", 1)[1]).read_bytes() == b"OggSsynthetic"
        assert route.called
    assert "200 characters" in commands.handle("preview " + VOICE + " " + "a" * 201)


def test_preview_uses_explicit_tool_model_precedence(config, monkeypatch):
    config[0]["tts"]["fish-audio"] = {"model": "s1"}
    resolve = settings.resolve_model
    preferences = []
    def capture(model, **kwargs):
        preferences.append(kwargs.get("prefer_call"))
        return resolve(model, **kwargs)
    monkeypatch.setattr(settings, "resolve_model", capture)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        assert commands.handle("preview " + VOICE).startswith("[[audio_as_voice]]")
        assert route.calls.last.request.headers["model"] == "s1"
        assert preferences == [True]


@pytest.mark.parametrize("nested,managed,wallet,expected", [
    ("s1", False, None, "nested"), (None, True, None, "managed pin"),
    (None, False, None, "unknown wallet"),
    (None, False, account.Wallet(Decimal(0), Decimal(0), False), "free tier"),
])
def test_status_model_reason(config, monkeypatch, nested, managed, wallet, expected):
    data, _, _ = config
    data["tts"]["fish-audio"] = {"model": nested}
    data["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": not managed}}}}
    monkeypatch.setattr(account, "cached_wallet", lambda *a: wallet)
    monkeypatch.setattr(settings, "cached_wallet", lambda *a: wallet)
    monkeypatch.setattr(account, "get_package", lambda *a: None)
    assert f"({expected})" in commands.status("test-key")


def test_operator_chat_hides_account_without_wallet_reads(config, monkeypatch):
    config[0]["plugins"] = {"entries": {"fish-audio": {"settings": {"operator_account": True}}}}
    def forbidden(*args, **kwargs):
        pytest.fail("operator chat read the account")
    for name in ("get_wallet", "cached_wallet", "get_package"):
        monkeypatch.setattr(account, name, forbidden)
    monkeypatch.setattr(settings, "cached_wallet", forbidden)
    assert commands.handle("balance") == "Voice billing for this agent is handled by its operator."
    state.record_failure("quota", "Top up https://fish.audio/app/developers/billing")
    output = commands.status()
    assert "Top up" not in output and "fish.audio/app" not in output
    assert "quota" in output
    assert "Account: managed by the operator" in output
    assert "API credit" not in output and "Plan:" not in output and "paid account" not in output
    # The unpinned default follows the operator's wallet, which status doesn't read: it names no model.
    assert "Model: Fish default (operator managed)" in output and "s2.1-pro" not in output
    config[0]["tts"]["fish-audio"] = {"model": "s1"}
    assert "Model: s1 (operator managed)" in commands.status()
    assert commands.handle("model s2.1-pro-free") == "Saved."
    assert "http" not in commands.handle("sk-" + "x" * 30)
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    assert commands.handle("balance") == "Voice billing for this agent is handled by its operator."
    assert commands.handle("status") == "Ask the operator of this agent to finish the Fish Audio setup."
    config[0]["plugins"]["entries"]["fish-audio"]["settings"]["operator_account"] = False
    assert commands.handle("balance") == commands.NO_KEY


def test_operator_terminal_status_keeps_the_account_view(config, monkeypatch):
    config[0]["plugins"] = {"entries": {"fish-audio": {"settings": {"operator_account": True}}}}
    with respx.mock(assert_all_called=True) as mock:
        wallet_routes(mock)
        output = commands.handle("status", end_user=False)
    assert "API credit: 2.5" in output and "Plan: plus" in output and "operator" not in output
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    assert commands.handle("status", end_user=False) == commands.NO_KEY
    assert commands.handle("status") == "Ask the operator of this agent to finish the Fish Audio setup."


def test_model_notice_reads_the_operator_setting_after_the_write(config):
    data, _, module = config
    data["plugins"] = {"entries": {"fish-audio": {"settings": {"operator_account": False}}}}
    save = module.save_config
    def save_then_turn_on_operator_mode(cfg, **kwargs):
        save(cfg, **kwargs)
        data["plugins"]["entries"]["fish-audio"]["settings"]["operator_account"] = True
    module.save_config = save_then_turn_on_operator_mode
    assert commands.handle("model s2.1-pro-free") == "Saved."
    assert commands.handle("model s2.1-pro-free", end_user=False).startswith("Saved.\n")
