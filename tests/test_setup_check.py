import copy
import json
import sys
from types import SimpleNamespace

import pytest
import respx

from fish_audio import commands, media, settings, setup_check, tool_support, tools
from fish_audio.tts import FishAudioTTSProvider
from test_commands import BASE, VOICE, config, wallet_routes

hermes_home = media._hermes_home


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(media, "_hermes_home", lambda: tmp_path)
    return tmp_path


def test_no_key_checklist_and_settings(config, monkeypatch, home):
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    monkeypatch.setattr(tool_support, "fish_api_key", lambda: "")
    with respx.mock(assert_all_called=True) as mock:
        reply = commands.handle("setup")
        result = json.loads(tools.fish_settings({"action": "get"}))
        assert not mock.calls
        assert not json.loads(tools.fish_settings({"action": "use", "voice": VOICE}))["success"]
    assert "✗ " + commands.NO_KEY in reply and reply.endswith("Next: " + commands.NO_KEY)
    assert "never paste API keys into chat" in reply
    assert result["success"] and not result["setup_ready"] and not result["setup"][0]["ok"]


@pytest.mark.parametrize("assignment,saved", [("synthetic-not-a-key", True), ("", False), ('""', False)])
def test_saved_key_restart(config, monkeypatch, home, assignment, saved):
    (home / ".env").write_text("OTHER=synthetic\nFISH_API_KEY=" + assignment + "\n")
    # Exercise the same profile-home resolver as media.py, including the Hermes API.
    monkeypatch.setattr(media, "_hermes_home", hermes_home)
    monkeypatch.setitem(sys.modules, "hermes_constants", SimpleNamespace(get_hermes_home=lambda: home))
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    reply = commands.handle("setup")
    assert ("restart the Hermes gateway or Desktop app" in reply) is saved
    assert "synthetic" not in reply


@pytest.mark.parametrize("provider", ["edge", None, "evaos-fishaudio", "fish-audio"])
def test_speech_and_optional_steps(config, provider):
    config[0]["tts"]["provider"] = provider
    with respx.mock(assert_all_called=True) as mock:
        steps = setup_check.checklist("test-key", BASE)
        reply = commands.handle("setup")
        assert not mock.calls
    assert [step["id"] for step in steps] == ["key", "speech", "voice", "transcription", "desktop"]
    assert steps[2]["ok"] and steps[2]["optional"] and "Fish's default voice" in steps[2]["say"]
    assert "· For Fish transcription" in reply and "✓ In Hermes Desktop" in reply
    if provider == "edge":
        assert "currently speaks with edge" in reply and "hermes config set tts.provider fish-audio" in reply
    elif provider is None:
        assert "`/fish use <voice>` makes Fish the provider" in reply
    else:
        assert reply.endswith("Ready. Hear your voice: `/fish preview`")


@pytest.mark.parametrize("key", ["", "test-key"])
def test_operator_pin(config, monkeypatch, managed_layer, key):
    managed_layer({"tts": {"provider": "edge"}})
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    monkeypatch.setattr(commands, "fish_api_key", lambda: key)
    reply = commands.handle("setup")
    assert ("Ask the operator of this agent to finish the Fish Audio setup." in reply) is (not key)
    assert commands.use_message("edge", True).removeprefix("Saved. ") in reply
    assert "hermes config set tts.provider" not in reply and "https://fish.audio" not in reply


def test_status_last_line(config):
    with respx.mock(assert_all_called=True) as mock:
        wallet_routes(mock)
        reply = commands.status("test-key")
        assert reply.splitlines()[-2] == "Docs: https://docs.fish.audio"
        assert reply.splitlines()[-1] == "Next step: " + setup_check.checklist("test-key", BASE)[1]["say"]
        config[0]["tts"]["provider"] = "fish-audio"
        assert commands.status("test-key").splitlines()[-1] == "Setup: ready"


@pytest.mark.parametrize("nested,legacy,expected", [(VOICE, "b" * 32, VOICE), (None, VOICE, VOICE), (None, None, None)])
def test_preview_current_or_default(config, nested, legacy, expected):
    config[0]["tts"] = {"voice": legacy, "fish-audio": {"voice": nested, "model": "s1"}}
    with respx.mock(assert_all_called=True) as mock:
        synth = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        assert commands.handle("preview").startswith("[[audio_as_voice]]")
        body = json.loads(synth.calls.last.request.content)
    assert body.get("reference_id") == expected
    assert ("reference_id" not in body) == (expected is None)
    if expected is None:
        assert FishAudioTTSProvider().default_voice() is None
    assert body["text"] == "Hi! This is how I sound."


def test_legacy_non_fish_voice_is_ignored(config, monkeypatch):
    # Synthesis ignores a top-level tts.voice that isn't a Fish id (e.g. one left by another provider); so do these.
    config[0]["tts"]["voice"] = "en-US-AriaNeural"
    steps = setup_check.checklist("test-key", BASE)
    assert steps[2]["say"].startswith("Using Fish's default voice")
    config[0]["tts"]["voice"] = "a" * 32
    assert setup_check.checklist("test-key", BASE)[2]["say"] == f"Voice selected: {'a' * 32}."


def test_hermes_default_edge_without_own_provider(config, monkeypatch):
    # Hermes merges its default tts.provider ("edge") into the config it loads; config.yaml itself has none.
    merged = copy.deepcopy(config[0])
    merged["tts"]["provider"] = "edge"
    monkeypatch.setattr(settings, "_config", lambda: merged)
    say = setup_check.checklist("test-key", BASE)[1]["say"]
    assert "currently speaks with edge" in say and "`/fish use <voice>` makes Fish the provider" in say
    assert "hermes config set" not in say
    config[0]["tts"]["provider"] = "edge"  # The profile chose edge itself: only a person can switch it.
    assert "hermes config set tts.provider fish-audio" in setup_check.checklist("test-key", BASE)[1]["say"]


@pytest.mark.parametrize("line,saved", [("export FISH_API_KEY=synthetic\n", True), ("FISH_API_KEY=  # later\n", False),
                                        ("FISH_API_KEY=\"\"\n", False), ("OTHER_FISH_API_KEY=x\n", False)])
def test_saved_key_line_forms(monkeypatch, tmp_path, line, saved):
    (tmp_path / ".env").write_text(line)
    monkeypatch.setattr(setup_check.media, "_hermes_home", lambda: tmp_path)
    assert setup_check._saved_key() is saved
