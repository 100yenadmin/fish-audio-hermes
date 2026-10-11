"""v1.5.1 wire and reply provenance: synthetic data, no live Fish calls."""
import importlib.util
import json
import logging
from pathlib import Path

import pytest

from fish_audio import client, settings
from fish_audio.errors import BILLING_URL, KEY_URL, FishAudioError, response_error

BASES = {
    400: "Fish Audio rejected the synthesis request. Check the voice and settings.",
    401: f"Set a valid Fish Audio API key: {KEY_URL}",
    403: f"Check your Fish Audio API key and access: {KEY_URL}",
    402: f"Top up Fish Audio API credits: {BILLING_URL}; app plan credits are separate from API credits.",
    404: "Fish Audio could not find the requested voice or model.",
    413: "The Fish Audio request is too large. Use less text or smaller references.",
    415: "Fish Audio does not support this audio format.",
    429: "Fish Audio concurrency limit reached. Top-up tiers: <$100: 5, ≥$100: 15, ≥$1k: 50 concurrent requests. Retry later.",
    503: "Fish Audio is unavailable. Try again later.",
}


@pytest.mark.parametrize("status", BASES)
@pytest.mark.parametrize("code", [None, "custom_failure", "BAD!"])
@pytest.mark.parametrize("free", [False, True])
def test_error_bytes_and_metadata(monkeypatch, status, code, free):
    monkeypatch.setattr(settings, "operator_account", lambda: False)
    error = response_error(status, {"x-request-id": "req-1", "x-fish-trace-id": "trace-1"},
                           json.dumps({"code": code}).encode(), model="s2.1-pro-free" if free else "s2.1-pro")
    expected = BASES[status] + (" Code: custom_failure." if code == "custom_failure" else "")
    hint = free and status in {402, 403, 429}
    if hint:
        expected += f" Switch to s2.1-pro and top up: {BILLING_URL}"
    assert error.message == expected
    assert str(error) == expected + " (request id req-1) (Fish trace trace-1)"
    assert error.standard is True
    assert error.code == (code if code == "custom_failure" else None)
    assert error.hint == ("paid_model" if hint else None)


def test_metadata_redaction_and_defaulted_hint(monkeypatch):
    monkeypatch.setattr(settings, "operator_account", lambda: False)
    error = response_error(402, body=b'{"code":"synthetic_key"}', key="synthetic_key")
    assert error.code == "[redacted]" and "synthetic_key" not in error.message
    assert response_error(402, model="s1", defaulted=True).hint == "paid_model"
    assert response_error(402, model="s2.1-pro", defaulted=True).hint is None
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    assert response_error(402, model="s2.1-pro-free").hint is None


def test_failure_wire_contract(monkeypatch):
    pytest.importorskip("fastapi")
    spec = importlib.util.spec_from_file_location("v151_gateway", Path(__file__).parents[1] / "dashboard/plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "_fa", lambda name: __import__("fish_audio." + name, fromlist=[name]))
    monkeypatch.setattr(settings, "operator_account", lambda: False)
    standard = response_error(400)
    assert api._failure(standard) == {"ok": False, "kind": "invalid_request", "message": standard.message,
                                     "detail": {"request_id": None, "trace_id": None, "code": None, "hint": None}}
    detailed = response_error(402, {"x-request-id": "r", "x-fish-trace-id": "t"}, b'{"code":"quota"}', model="s2.1-pro-free")
    assert api._failure(detailed) == {"ok": False, "kind": "quota", "message": str(detailed),
                                     "detail": {"request_id": "r", "trace_id": "t", "code": "quota", "hint": "paid_model"}}
    direct = FishAudioError("availability", None, "r", "Direct failure", trace_id="t")
    assert not direct.standard and direct.code is None and direct.hint is None
    from fish_audio.media import InputFileError
    from fish_audio.tool_support import ToolInputError
    from fish_audio.commands import WriteRefusal
    for error, kind, message, extra in [(direct, "availability", "Direct failure (request id r) (Fish trace t)", {}),
        (api.Refusal("offset", "Offset failure", size=4), "offset", "Offset failure", {"size": 4}),
        (InputFileError("Bad audio"), "bad_audio", "Bad audio", {}),
        (ToolInputError("Bad input"), "invalid", "Bad input", {}),
        (WriteRefusal("managed", "Managed"), "managed", "Managed", {}),
        (ValueError("private"), "error", "Fish Audio could not complete this request. Try again.", {})]:
        assert api._failure(error) == {"ok": False, "kind": kind, "message": message, **extra}


@pytest.mark.parametrize("call,nested,expected", [
    ("a" * 32, {"voice": "legacy_voice"}, ("legacy_voice", "nested")),
    ("a" * 32, {}, ("a" * 32, "call")),
    ("a" * 32, {"voice": "bad"}, ("a" * 32, "call")),
    ("foreign_voice", {}, (None, "default")),
    (None, {"voice": 123}, (None, "default")),
    (None, {}, (None, "default")),
])
def test_voice_source(call, nested, expected):
    assert settings.resolve_voice_source(call, nested) == expected


def test_voice_log_shared_dedupe_and_exact_body(monkeypatch, caplog):
    logger = logging.getLogger("fish_audio.voice")
    monkeypatch.setattr(logger, "_fish_last_voice", None, raising=False)
    config = {"tts": {"fish-audio": {"model": "s2.1-pro"}}}
    monkeypatch.setattr(settings, "_config", lambda: config)
    caplog.set_level(logging.INFO, logger="fish_audio.voice")
    for voice in ["a" * 32, "a" * 32, "a" * 8 + "b" * 24, "b" * 32, None, None]:
        params, path = settings.resolve_tts(voice, None, None, None, "reply.mp3")
        assert path == "reply.mp3"
        assert client._tts_body({**params, "text": "hello"}) == {
            "text": "hello", "format": "mp3", "prosody": {"speed": 1.0},
            **({"reference_id": voice} if voice else {})}
    config["tts"]["fish-audio"]["voice"] = "b" * 32
    settings.resolve_tts("a" * 32, None, None, None, "reply.mp3")
    # Reload under another module name: both copies must share the named logger's last value.
    spec = importlib.util.spec_from_file_location("fish_audio.v151_settings_copy", settings.__file__)
    copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copy)
    monkeypatch.setattr(copy, "_config", lambda: config)
    copy.resolve_tts(None, None, None, None, "reply.mp3")
    assert [r.message for r in caplog.records if r.name == "fish_audio.voice"] == [
        "Fish Audio voice: aaaaaaaa… (from call)", "Fish Audio voice: bbbbbbbb… (from call)",
        "Fish Audio voice: Fish default (from default)", "Fish Audio voice: bbbbbbbb… (from nested)"]
