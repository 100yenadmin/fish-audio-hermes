import json
import sys
from types import SimpleNamespace

import pytest
import respx

from fish_audio import cli, commands, settings, tools
from fish_audio.tool_support import ToolInputError
from fish_audio.tts import FishAudioTTSProvider
from test_commands import BASE, VOICE, config

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


def test_alias_writes_keep_entries_the_reader_skips(config):
    config[0]["tts"]["fish-audio"] = {"aliases": {"Short": "legacy-id", "x": VOICE, " Narrator ": VOICE, "host": OTHER}}
    with respx.mock as mock:
        mock.get(BASE + "/model/" + OTHER).respond(json={})
        commands.alias_add("narrator", OTHER, "test-key", BASE)
    assert config[0]["tts"]["fish-audio"]["aliases"] == {"Short": "legacy-id", "x": VOICE, "host": OTHER, "narrator": OTHER}
    commands.alias_remove("HOST")
    assert config[0]["tts"]["fish-audio"]["aliases"] == {"Short": "legacy-id", "x": VOICE, "narrator": OTHER}
    assert settings.aliases() == {"narrator": OTHER}


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
@pytest.mark.parametrize("rule", ["upstream", "fork"])
def test_managed_writes(config, managed_layer, leaf, operation, rule):
    seed(config)
    value = {"narrator": VOICE} if leaf == "aliases" else None
    managed_layer({"tts": {"fish-audio": {leaf: value}}}, rule=rule)
    raw = {"add": "alias add newname " + VOICE, "remove": "alias remove narrator",
           "model": "model s1", "use": "use narrator"}[operation]
    with respx.mock as mock:
        assert commands.handle(raw) == "This setting is set by the operator of this agent."
        assert not mock.calls
    assert not config[1]


@pytest.mark.parametrize("rule", ["upstream", "fork"])
@pytest.mark.parametrize("layer,leaf,upstream,fork", [
    pytest.param({"tts": None}, "model", True, True, id="tts-scalar"),
    pytest.param({"tts": {"fish-audio": None}}, "model", True, True, id="fish-scalar"),
    pytest.param({"tts": {"fish-audio": {}}}, "model", True, False, id="fish-empty"),
    pytest.param({}, "model", False, False, id="no-layer"),
    pytest.param({"tts": {"provider": "edge"}}, "model", False, False, id="provider-only"),
    pytest.param({"tts": {"fish-audio": {"model": "s1"}}}, "model", True, True, id="model-scalar"),
    pytest.param({"tts": {"fish-audio": {"voice": "x"}}}, "model", False, False, id="voice-only"),
    pytest.param({"tts": {"fish-audio": {"aliases": {"narrator": VOICE}}}}, "aliases", True, True, id="alias-child"),
])
def test_managed_parents(config, managed_layer, layer, leaf, upstream, fork, rule):
    managed_layer(layer, rule=rule)
    assert settings.managed_leaf("tts", "fish-audio", leaf) is (upstream if rule == "upstream" else fork)


@pytest.mark.parametrize("leaf,operation", [("aliases", "add"), ("aliases", "remove"),
    ("model", "model"), ("voice", "use")])
def test_fork_empty_mapping_allows_writes(config, managed_layer, leaf, operation):
    seed(config)
    managed_layer({"tts": {"fish-audio": {leaf: {}}}}, rule="fork")
    raw = {"add": "alias add newname " + VOICE, "remove": "alias remove narrator",
           "model": "model s2-pro", "use": "use narrator"}[operation]
    with respx.mock as mock:
        if operation in {"add", "use"}:
            mock.get(BASE + "/model/" + VOICE).respond(json={})
        assert commands.handle(raw) == ("Removed." if operation == "remove" else "Saved.")
    assert len(config[1]) == 1
    nested = config[0]["tts"]["fish-audio"]
    if operation == "remove":
        assert "narrator" not in nested["aliases"]
    elif operation == "add":
        assert nested["aliases"]["newname"] == VOICE
    else:
        assert nested[leaf] == ("s2-pro" if operation == "model" else VOICE)


@pytest.mark.parametrize("failure", ["import", "missing", "call"])
def test_managed_core_unavailable(config, monkeypatch, managed_layer, failure):
    module = managed_layer({"tts": None})
    if failure == "import":
        monkeypatch.setitem(sys.modules, "hermes_cli.managed_scope", None)
    elif failure == "missing":
        monkeypatch.delattr(module, "managed_config_keys")
    else:
        def fail():
            raise RuntimeError("synthetic managed config error")
        monkeypatch.setattr(module, "managed_config_keys", fail)
    assert settings.managed_leaf("tts", "fish-audio", "model") is False


def test_list_voices_current_legacy_and_malformed(config):
    nested = seed(config)
    nested["voice"] = "named_voice_123"
    assert FishAudioTTSProvider().list_voices()[-1] == {"id": "named_voice_123", "display": "Current voice"}
    for bad in (123, ["x"], "short"):
        nested["voice"] = bad
        assert len(FishAudioTTSProvider().list_voices()) == 2


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
