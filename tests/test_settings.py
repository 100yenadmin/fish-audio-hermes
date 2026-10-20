import sys
import json
from types import ModuleType

import pytest
import respx

from fish_audio import client, settings

A, B = "a" * 32, "b" * 32


@pytest.fixture
def config(monkeypatch):
    data = {"tts": {"fish-audio": {}}, "plugins": {"entries": {"fish-audio": {"settings": {}}}}}
    module = ModuleType("hermes_cli.config")
    module.load_config = lambda: data
    monkeypatch.setitem(sys.modules, "hermes_cli", ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.config", module)
    settings._warned.clear()
    return data, data["tts"]["fish-audio"], data["plugins"]["entries"]["fish-audio"]["settings"]


def resolve(voice=None, model=None, speed=None, fmt="mp3", path="out.mp3"):
    return settings.resolve_tts(voice, model, speed, fmt, path)


def test_precedence_and_per_call_profile_read(config):
    _, nested, _ = config
    assert resolve(A, "s1")[0]["reference_id"] == A
    nested.update(voice=B, model="s2-pro")
    p, _ = resolve(A, "s1")
    assert p["reference_id"] == B and p["model"] == "s2-pro"
    nested["voice"] = "named_voice_123"
    assert resolve(A)[0]["reference_id"] == "named_voice_123"
    nested.update(voice="invalid!", model="invalid")
    assert resolve(A, "s1")[0]["model"] == "s1"


@pytest.mark.parametrize("voice", ["21m00Tcm4TlvDq8ikWAM", "A" * 32, "foreign_voice", [], None])
def test_foreign_legacy_voice_rejected(config, voice):
    assert "reference_id" not in resolve(voice)[0]


@pytest.mark.parametrize("model", ["drama-3", "drama-3-preview"])
def test_configured_drama_model_canonicalized(config, model):
    config[1]["model"] = model
    with respx.mock(assert_all_called=True) as mock:
        assert resolve()[0]["model"] == "drama-3"
        assert settings.resolve_model(None, key="", base_url=settings.DEFAULT_BASE_URL) == ("drama-3", False)
        assert settings.resolve_model_source(model, key="", base_url=settings.DEFAULT_BASE_URL, prefer_call=True) == ("drama-3", "configured")
        assert config[1]["model"] == model
        assert not mock.calls


def test_default_and_free_policy(config):
    _, nested, transport = config
    assert resolve()[0]["model"] == "s2.1-pro"
    transport["allow_free_model"] = False
    assert resolve()[0]["model"] == "s2.1-pro"
    nested["model"] = "s2.1-pro-free"
    # Stage 2's ordered resolver gives explicit valid settings priority.
    assert resolve(model="s2.1-pro-free")[0]["model"] == "s2.1-pro"


@pytest.mark.parametrize("suffix,call_format,fmt,final", [
    ("ogg", "mp3", "opus", "ogg"), ("opus", "wav", "opus", "opus"),
    ("mp3", "opus", "mp3", "mp3"), ("wav", "mp3", "wav", "wav"),
    ("flac", "mp3", "wav", "wav"), ("other", "wav", "wav", "wav"),
    ("other", "opus", "opus", "ogg"), ("other", "bad", "mp3", "mp3"),
])
def test_suffix_format(config, suffix, call_format, fmt, final):
    p, path = resolve(fmt=call_format, path=f"out.{suffix}")
    assert p["format"] == fmt and path == f"out.{final}"


def test_speed_precedence_and_clamp(config):
    _, nested, _ = config
    assert resolve()[0]["prosody"]["speed"] == 1
    nested["speed"] = 1.4
    assert resolve()[0]["prosody"]["speed"] == 1.4
    assert resolve(speed=0.1)[0]["prosody"]["speed"] == 0.5
    assert resolve(speed=4)[0]["prosody"]["speed"] == 2
    assert resolve(speed=float("nan"))[0]["prosody"]["speed"] == 1


VALID = {"temperature": 0.8, "top_p": 1, "latency": "balanced", "normalize": False,
         "chunk_length": 100, "min_chunk_length": 0, "sample_rate": 24000,
         "mp3_bitrate": 192, "opus_bitrate": -1000, "volume": -2.5,
         "normalize_loudness": True, "max_new_tokens": 1024, "repetition_penalty": 1.2,
         "condition_on_previous_chunks": False, "early_stop_threshold": 0.5,
         "features": ["quality-guard"], "pronunciation_dictionary": [{"id": "dict", "version": "v1"}]}


def test_every_valid_knob(config, tmp_path):
    config[1].update(VALID)
    p, _ = resolve()
    for name, value in VALID.items():
        assert (p["prosody"] if name in {"volume", "normalize_loudness"} else p)[name] == value
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(p["base_url"] + "/v1/tts").respond(content=b"ID3synthetic")
        client.tts_to_file({**p, "text": "hello"}, "test-key", p["base_url"], tmp_path / "out.mp3")
        body = json.loads(route.calls.last.request.content)
        for name, value in VALID.items():
            assert (body["prosody"] if name in {"volume", "normalize_loudness"} else body)[name] == value


@pytest.mark.parametrize("name,value", [
    ("language", "en!"), ("language", "a" * 36), ("language", None), ("language", 1),
    ("temperature", 1.1), ("top_p", float("inf")), ("latency", "fast"),
    ("normalize", 1), ("chunk_length", 99), ("min_chunk_length", 101),
    ("sample_rate", 24.0), ("mp3_bitrate", True), ("opus_bitrate", 128),
    ("volume", "loud"), ("normalize_loudness", "yes"), ("max_new_tokens", 1.5),
    ("repetition_penalty", float("nan")), ("condition_on_previous_chunks", []),
    ("early_stop_threshold", -1), ("features", [1]), ("pronunciation_dictionary", {}),
])
def test_invalid_knobs_dropped_once(config, caplog, name, value):
    config[1][name] = value
    for _ in range(2):
        p, _ = resolve()
        assert name not in p and name not in p["prosody"]
    assert len(caplog.records) == 1
    assert name in caplog.text and str(value) not in caplog.records[0].message


def test_dictionary_forms(config):
    config[1]["pronunciation_dictionary"] = [{"items": [{"key": "Eva", "value": "eh-va", "case_sensitive": True}]}]
    assert "pronunciation_dictionary" in resolve()[0]
    config[1]["pronunciation_dictionary"].append({"id": "d", "version": "v"})
    assert "pronunciation_dictionary" not in resolve()[0]


def test_large_numeric_config_does_not_raise(config):
    config[1].update(temperature=10 ** 1000, speed=10 ** 1000)
    params, _ = resolve()
    assert "temperature" not in params and params["prosody"]["speed"] == 2


@pytest.mark.parametrize("url,expected", [
    ("https://fish.example/", "https://fish.example"),
    ("http://localhost:8000", "http://localhost:8000"),
    ("http://127.0.0.2:8000", "http://127.0.0.2:8000"),
    ("http://[::1]:8000", "http://[::1]:8000"),
    ("http://fish.example", settings.DEFAULT_BASE_URL),
    ("http://localhost.evil", settings.DEFAULT_BASE_URL),
    ("ftp://127.0.0.1", settings.DEFAULT_BASE_URL),
    ("https://", settings.DEFAULT_BASE_URL),
    ("https://user:pass@fish.example", settings.DEFAULT_BASE_URL),
    ("https://fish.example:bad", settings.DEFAULT_BASE_URL),
    ([], settings.DEFAULT_BASE_URL),
])
def test_base_url_transport_home(config, url, expected):
    config[1]["base_url"] = "https://ignored.example"
    config[2]["base_url"] = url
    assert resolve()[0]["base_url"] == expected


def test_load_failure_defaults(config, monkeypatch):
    def fail():
        raise RuntimeError("synthetic config failure")
    monkeypatch.setattr(sys.modules["hermes_cli.config"], "load_config", fail)
    assert resolve()[0]["model"] == "s2.1-pro"


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (None, False), (1, False), ("true", False)])
def test_operator_account_strict_boolean(monkeypatch, value, expected):
    monkeypatch.setattr(settings, "_config", lambda: {"plugins": {"entries": {"fish-audio": {"settings": {"operator_account": value}}}}})
    assert settings.operator_account() is expected
    monkeypatch.setattr(settings, "_config", lambda: {})
    assert settings.operator_account() is False


@pytest.mark.parametrize("timestamps", [False, True])
@pytest.mark.parametrize("msgpack_body", [False, True])
def test_language_transport(config, tmp_path, timestamps, msgpack_body):
    import msgpack
    config[1]["language"] = "es-MX"
    params, _ = resolve()
    params.update(text="El 20 de octubre son 42.")
    if msgpack_body:
        params["references"] = [{"audio": b"synthetic", "text": "hi"}]
    with respx.mock() as mock:
        route = mock.post(settings.DEFAULT_BASE_URL + ("/v1/tts/stream/with-timestamp" if timestamps else "/v1/tts")).respond(
            content=b'data: {"audio_base64":"SUQz"}\n\n' if timestamps else b"ID3")
        client.tts_to_file(params, "synthetic", settings.DEFAULT_BASE_URL, tmp_path / "out.mp3", events=[] if timestamps else None)
        request = route.calls.last.request
        body = msgpack.unpackb(request.content, raw=False) if msgpack_body else json.loads(request.content)
        assert body["language"] == "es-MX"


def test_language_websocket_start(config, monkeypatch):
    import msgpack
    from types import SimpleNamespace
    config[1]["language"] = "es-MX"
    sent = []
    socket = SimpleNamespace(send=lambda raw: sent.append(msgpack.unpackb(raw, raw=False)), close=lambda: None,
                             recv=lambda **kw: msgpack.packb({"event": "finish"}))
    monkeypatch.setattr("websockets.sync.client.connect", lambda *a, **kw: socket)
    assert list(client.tts_live({**resolve()[0], "text": "hola"}, "synthetic", settings.DEFAULT_BASE_URL)) == []
    assert sent[0]["event"] == "start" and sent[0]["request"]["language"] == "es-MX"


@pytest.mark.parametrize("language", ["es-MX", "", "auto", "ZH_cn", "a" * 35, "en!", "a" * 36, None, 1])
def test_speak_language_override(config, tmp_path, monkeypatch, language):
    from fish_audio import media, tools, tool_support
    config[1]["language"] = "ja"
    monkeypatch.setattr(tool_support, "fish_api_key", lambda: "synthetic")
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(settings.DEFAULT_BASE_URL + "/v1/tts").respond(content=b"ID3")
        result = json.loads(tools.fish_speak({"text": "hola", "language": language}))
        if type(language) is str and language not in {"en!", "a" * 36}:
            assert result["success"] and json.loads(route.calls.last.request.content)["language"] == language
        else:
            assert result["error"] == "language must be a BCP-47 tag such as en, es-MX or zh-CN: letters, digits, - and _, at most 35 characters."
            assert not mock.calls


def test_model_alias_command(config, monkeypatch):
    from fish_audio import commands
    monkeypatch.setattr(commands, "fish_api_key", lambda: "synthetic")
    monkeypatch.setattr(commands, "write_config", lambda change: change(config[0]))
    assert commands.handle("model drama-3-preview") == "drama-3-preview was Fish's preview id; using drama-3."
    assert config[1]["model"] == "drama-3"
