import sys
from types import ModuleType

import pytest

from fish_audio import settings

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


def test_every_valid_knob(config):
    config[1].update(VALID)
    p, _ = resolve()
    for name, value in VALID.items():
        assert (p["prosody"] if name in {"volume", "normalize_loudness"} else p)[name] == value


@pytest.mark.parametrize("name,value", [
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
