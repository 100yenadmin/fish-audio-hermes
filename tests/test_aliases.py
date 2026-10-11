import json
from types import SimpleNamespace

import pytest
import respx

from fish_audio import cli, commands, settings, tools
from fish_audio.tool_support import ToolInputError
from fish_audio.tts import FishAudioTTSProvider
from test_commands import BASE, VOICE, config, managed_layer

OTHER = "c" * 32


@pytest.mark.parametrize("raw,expected", [("ab", "ab"), (" NaRRaTor ", "narrator"),
    ("a" + "z" * 31, "a" + "z" * 31), ("host_2-x", "host_2-x")])
def test_name_valid(raw, expected):
    assert settings.alias_name(raw) == expected


@pytest.mark.parametrize("raw", ["a", "a" * 33, "2host", "a" * 32, "add", "REMOVE", "list",
    "current", "default", "two words", "", None])
def test_name_invalid(raw):
    with pytest.raises(ToolInputError):
        settings.alias_name(raw)


def seed(config):
    config[0]["tts"]["fish-audio"] = {"model": "s2.1-pro", "aliases": {"narrator": VOICE, "host": OTHER}}
    return config[0]["tts"]["fish-audio"]


def test_alias_crud(config, monkeypatch):
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    assert commands.handle("alias") == "No saved voice names yet. Add one: /fish alias add <name> <voice id>"
    with respx.mock as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={})
        mock.get(BASE + "/model/" + OTHER).respond(json={})
        assert commands.handle("alias add Narrator " + VOICE) == "Saved."
        assert commands.handle("alias add host narrator") == "Saved."
        assert commands.handle("alias add narrator " + OTHER) == "Updated."
    assert settings.aliases() == {"narrator": OTHER, "host": VOICE}
    assert commands.handle("alias list") == f"host · {VOICE}\nnarrator · {OTHER}"
    assert commands.handle("alias remove narrator") == "Removed."
    assert config[0]["tts"]["fish-audio"]["aliases"] == {"host": VOICE}
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    assert "finish" in commands.handle("alias")


def test_alias_limit_and_unknown_voice(config):
    nested = seed(config)
    nested["aliases"] = {f"voice{i}": VOICE for i in range(50)}
    with respx.mock as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={})
        with pytest.raises(commands.WriteRefusal, match="50") as exc:
            commands.alias_add("extra", VOICE, "test-key", BASE)
        assert exc.value.kind == "limit"
        assert commands.alias_add("voice0", VOICE, "test-key", BASE)[1] is True
        mock.get(BASE + "/model/" + OTHER).respond(404)
        with pytest.raises(commands.FishAudioError) as exc:
            commands.alias_add("voice0", OTHER, "test-key", BASE)
        assert exc.value.kind == "voice_not_found"


def test_resolver_order_and_filtered_config(config, monkeypatch):
    seed(config)["aliases"].update({" NaME ": VOICE, "bad name": VOICE, "short": "bad", "default": VOICE})
    assert settings.aliases() == {"narrator": VOICE, "host": OTHER, "name": VOICE}
    assert settings.resolve_voice(" NARRATOR ") == VOICE
    assert settings.resolve_voice("legacy-id") == "legacy-id"
    assert settings.resolve_voice("??") is None
    monkeypatch.setattr(settings, "aliases", lambda: {VOICE: OTHER})
    assert settings.resolve_voice(VOICE) == VOICE


def test_command_use_and_cli_use(config, monkeypatch):
    nested = seed(config)
    monkeypatch.setattr(cli, "fish_api_key", lambda: "test-key")
    with respx.mock as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={})
        assert commands.handle("use narrator") == "Saved."
        assert nested["aliases"]["narrator"] == VOICE
        assert config[0]["tts"]["fish-audio"]["voice"] == VOICE
        mock.get(BASE + "/model/" + OTHER).respond(json={})
        assert cli.handle(SimpleNamespace(fish_command="use", voice_id="host")) == 0
        assert config[0]["tts"]["fish-audio"]["voice"] == OTHER


def test_command_preview_resolves_before_speak(config, monkeypatch):
    seed(config)
    def speak(args, *a, **kw):
        assert args["voice"] == VOICE
        return {"media_tag": "MEDIA:synthetic"}
    monkeypatch.setattr(tools, "_speak", speak)
    assert commands.handle("preview narrator") == "MEDIA:synthetic"


@pytest.mark.parametrize("args,expected", [({"voice": "narrator"}, VOICE),
    ({"speakers": ["narrator", "host"]}, [VOICE, OTHER])])
def test_speak_resolves_request(config, args, expected, monkeypatch):
    from fish_audio import tool_support
    monkeypatch.setattr(tool_support, "fish_api_key", lambda: "test-key")
    seed(config)
    with respx.mock as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        result = json.loads(tools.fish_speak({"text": "hello", **args}))
    assert json.loads(route.calls.last.request.content)["reference_id"] == expected
    assert result["voice"] == expected


@pytest.mark.parametrize("leaf,operation", [("aliases", "add"), ("aliases", "remove"),
    ("model", "model"), ("voice", "use")])
def test_managed_writes(config, monkeypatch, leaf, operation):
    seed(config)
    managed_layer(monkeypatch, {"tts": {"fish-audio": {leaf: {}}}})
    raw = {"add": "alias add newname " + VOICE, "remove": "alias remove narrator",
           "model": "model s1", "use": "use narrator"}[operation]
    with respx.mock as mock:
        assert commands.handle(raw) == "This setting is set by the operator of this agent."
        assert not mock.calls
    assert not config[1]


@pytest.mark.parametrize("layer,expected", [({"tts": None}, True),
    ({"tts": {"fish-audio": None}}, True), ({"tts": {"fish-audio": {}}}, True), ({}, False),
    ({"tts": {"provider": "edge"}}, False)])
def test_managed_parents(config, monkeypatch, layer, expected):
    managed_layer(monkeypatch, layer)
    assert settings.managed_leaf("tts", "fish-audio", "model") is expected


def test_list_voices_offline_and_error(config, monkeypatch):
    nested = seed(config)
    nested["voice"] = "b" * 32
    provider = FishAudioTTSProvider()
    with respx.mock as mock:
        assert provider.list_voices() == [{"id": OTHER, "display": "host"}, {"id": VOICE, "display": "narrator"},
                                          {"id": "b" * 32, "display": "Current voice"}]
        nested["voice"] = VOICE
        assert len(provider.list_voices()) == 2 and not mock.calls
    def fail():
        raise RuntimeError("synthetic config error")
    monkeypatch.setattr(settings, "_config", fail)
    assert provider.list_voices() == []
