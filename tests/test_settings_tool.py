import json

import pytest
import respx

from fish_audio import commands, settings, tool_support, tools, voices
from test_commands import BASE, VOICE, config


@pytest.fixture(autouse=True)
def fake_key(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(tool_support, "fish_api_key", lambda: "test-key")


def call(action, **kwargs):
    return json.loads(tools.fish_settings({"action": action, **kwargs}))


@pytest.mark.parametrize("provider,fish", [("fish-audio", True), ("evaos-fishaudio", True), ("edge", False)])
def test_get_provider_and_names(config, provider, fish):
    data, saves, _ = config
    data["tts"] = {"provider": provider, "fish-audio": {"voice": VOICE, "model": "s2-pro", "aliases": {"narrator": VOICE}}}
    data["stt"]["provider"] = "whisper"
    with respx.mock:
        result = call("get")
    assert result["success"] and result["voice"] == VOICE and result["model"] == "s2-pro"
    assert result["model_source"] == "configured" and result["names"] == {"narrator": VOICE}
    assert result["speech_provider"] == provider and result["fish_is_speech_provider"] is fish
    assert result["transcription_provider"] == "whisper"
    assert ("note" in result) is not fish and not saves
    if not fish:
        assert "currently speaks with edge" in result["note"]


@pytest.mark.parametrize("reference", [VOICE, "narrator", "My own voice"])
@pytest.mark.parametrize("provider", [None, "edge", "fish-audio"])
def test_use_matches_command(config, monkeypatch, reference, provider):
    data, saves, _ = config
    data["tts"] = {"provider": provider, "fish-audio": {"model": "s2.1-pro", "aliases": {"narrator": VOICE}}}
    monkeypatch.setattr(voices, "find_own_by_title", lambda title, key, base: ([(VOICE, "My own voice")], True))
    with respx.mock as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={"_id": VOICE})
        result = call("use", voice=reference)
        tool_saved = saves[-1]
        assert commands.handle("use " + reference) == commands.use_message(provider if provider == "edge" else None, False)
    assert saves[-1] == tool_saved
    assert result["success"] and result["changed"] and result["voice"] == VOICE
    assert result["speech_provider"] == (provider or "fish-audio")


@pytest.mark.parametrize("leaf,action,kwargs,command", [
    ("voice", "use", {"voice": VOICE}, "use " + VOICE),
    ("model", "set_model", {"model": "s1"}, "model s1"),
    ("aliases", "add_name", {"name": "host", "voice": VOICE}, "alias add host " + VOICE),
    ("aliases", "remove_name", {"name": "host"}, "alias remove host")])
def test_managed_refusal_matches_command(config, managed_layer, leaf, action, kwargs, command):
    managed_layer({"tts": {"fish-audio": {leaf: VOICE}}})
    with respx.mock:
        result = call(action, **kwargs)
        assert result["success"] is False and result["error"] == commands.handle(command)
    assert not config[1]


def test_operator_pin_does_not_switch_provider(config, monkeypatch, managed_layer):
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    managed_layer({"tts": {"provider": "edge"}})
    with respx.mock as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={})
        result = call("use", voice=VOICE)
    assert result["success"] and result["speech_provider"] == "edge" and not result["fish_is_speech_provider"]
    assert "set by its operator" in result["note"] and "provider" not in config[0]["tts"]


def test_model_policy_matches_command(config):
    data, _, _ = config
    data["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": False}}}}
    with respx.mock:
        result = call("set_model", model="s2.1-pro-free")
        assert result["success"] and result["model"] == "s2.1-pro"
        assert commands.handle("model s2.1-pro-free") == "Saved. This profile's policy uses paid s2.1-pro instead of s2.1-pro-free."


def test_names_crud_validation_and_cap(config):
    data, _, _ = config
    nested = data["tts"]["fish-audio"] = {"model": "s2.1-pro"}
    with respx.mock as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={})
        assert call("add_name", name="Narrator", voice=VOICE)["names"] == {"narrator": VOICE}
        assert call("add_name", name="host", voice="narrator")["names"]["host"] == VOICE
        assert "narrator" not in call("remove_name", name="NARRATOR")["names"]
        for name in ("a", "add", "a" * 33):
            assert call("add_name", name=name, voice=VOICE)["error"] == commands.handle(f"alias add {name} {VOICE}")
        assert call("remove_name", name="missing")["error"] == "No saved voice name found."
        assert "Use a name" in call("add_name", name="bad name", voice=VOICE)["error"]
        data["tts"]["fish-audio"]["aliases"] = {f"voice{i}": VOICE for i in range(50)}
        assert "50" in call("add_name", name="extra", voice=VOICE)["error"]
        assert call("add_name", name="voice0", voice=VOICE)["success"]


def test_whole_install_refusal_matches_command(config):
    config[2].is_managed = lambda: True
    with respx.mock as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={})
        assert call("use", voice=VOICE)["error"] == commands.handle("use " + VOICE)
    assert not config[1]
