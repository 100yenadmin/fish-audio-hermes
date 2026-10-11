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


TEXT = {400: "invalid_request", 401: "credential", 403: "credentialAccess", 402: "quota", 404: "not_found",
        413: "too_large", 415: "unsupported_media", 429: "rate_limit", 503: "availability", None: "availability"}
OPERATOR_TEXT = {401: "credentialOperator", 403: "credentialOperator", 402: "quotaOperator", 429: "rateLimitOperator"}


@pytest.mark.parametrize("operator", [False, True])
@pytest.mark.parametrize("status", TEXT)
def test_error_names_the_sentence_it_chose(monkeypatch, status, operator):
    """Desktop shows detail.text's sentence, so it must name exactly the wording chosen here: operator and 401 vs 403."""
    monkeypatch.setattr(settings, "operator_account", lambda: operator)
    assert response_error(status, model="s2.1-pro").text == (OPERATOR_TEXT.get(status, TEXT[status]) if operator else TEXT[status])


@pytest.mark.parametrize("operator", [False, True])
@pytest.mark.parametrize("status, body, model, expected, expected_operator", [
    (401, b'{"code":"invalid_api_key"}', "s2.1-pro", "credentialAccess", "credentialOperator"),
    (500, b'{"code":"quota"}', "s2.1-pro", "quota", "quotaOperator"),
    (500, b'{"code":"rate_limit"}', "s2.1-pro", "rate_limit", "rateLimitOperator"),
    (404, b'{"code":"voice_not_found"}', "s2.1-pro", "voice_not_found", "voice_not_found"),
    (404, b'{"message":"Reference not found"}', "s2.1-pro", "voice_not_found", "voice_not_found"),
    (400, b"", "transcribe-1", None, None),
    (400, b"", "transcribe-1-pro", None, None),
    (403, b"", "s2.1-pro-free", "credentialAccess", "credentialOperator"),
])
def test_error_sentence_names_follow_codes_and_models(monkeypatch, operator, status, body, model, expected, expected_operator):
    monkeypatch.setattr(settings, "operator_account", lambda: operator)
    assert response_error(status, body=body, model=model).text == (expected_operator if operator else expected)


def _english_errors():
    """The Desktop English bundle's errors.* sentences, read from the source the bundle is built from."""
    import re
    source = (Path(__file__).parents[1] / "devtools/src/desktop/locales/en.ts").read_text()
    block = source[source.index("  errors: {"):source.index("  refusals: {")]
    return {m.group(1): m.group(2) for m in re.finditer(r"^    (\w+): (?:\(\w+: string\) => )?`([^`]*)`,$", block, re.M)}


@pytest.mark.parametrize("operator", [False, True])
@pytest.mark.parametrize("status", [None, 400, 401, 402, 403, 404, 413, 415, 429, 500, 503])
@pytest.mark.parametrize("body", [b"", b'{"code":"invalid_api_key"}', b'{"code":"quota"}', b'{"code":"voice_not_found"}',
                                  b'{"code":"custom_code"}', b'{"message":"model not found"}'])
@pytest.mark.parametrize("model", ["s2.1-pro", "s2.1-pro-free", "transcribe-1-pro"])
def test_named_sentence_is_the_gateway_sentence(monkeypatch, operator, status, body, model):
    """Every name points at the English bundle sentence equal to the gateway's own base text, so a translation of that
    entry says what the gateway said."""
    monkeypatch.setattr(settings, "operator_account", lambda: operator)
    error = response_error(status, body=body, model=model)
    if error.text is None:
        assert model == "transcribe-1-pro" and status == 400 and error.kind == "invalid_request"
        return
    url = KEY_URL if error.kind == "credential" else BILLING_URL if error.kind == "quota" else "https://fish.audio/discovery"
    base = error.message
    if error.code:
        base = base.replace(f" Code: {error.code}.", "", 1)
    if error.hint == "paid_model":
        base = base.replace(f" Switch to s2.1-pro and top up: {BILLING_URL}", "", 1)
    assert _english_errors()[error.text].replace("${url}", url) == base


def test_no_key_refusal_names_its_sentence(monkeypatch):
    pytest.importorskip("fastapi")
    spec = importlib.util.spec_from_file_location("v151_gateway_no_key", Path(__file__).parents[1] / "dashboard/plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    secrets = __import__("fish_audio.secrets", fromlist=["secrets"])
    monkeypatch.setattr(api, "_fa", lambda name: __import__("fish_audio." + name, fromlist=[name]))
    monkeypatch.setattr(secrets, "fish_api_key", lambda: "")
    for operator, text in ((False, "no_key"), (True, "no_keyOperator")):
        monkeypatch.setattr(settings, "operator_account", lambda operator=operator: operator)
        with pytest.raises(api.Refusal) as refused:
            api._scope()
        assert api._failure(refused.value) == {"ok": False, "kind": "no_key", "message": refused.value.message,
                                               "detail": {"text": text}}


def test_failure_wire_contract(monkeypatch):
    pytest.importorskip("fastapi")
    spec = importlib.util.spec_from_file_location("v151_gateway", Path(__file__).parents[1] / "dashboard/plugin_api.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "_fa", lambda name: __import__("fish_audio." + name, fromlist=[name]))
    monkeypatch.setattr(settings, "operator_account", lambda: False)
    standard = response_error(400)
    assert api._failure(standard) == {"ok": False, "kind": "invalid_request", "message": standard.message,
                                     "detail": {"request_id": None, "trace_id": None, "code": None, "hint": None,
                                                "text": "invalid_request"}}
    detailed = response_error(402, {"x-request-id": "r", "x-fish-trace-id": "t"}, b'{"code":"quota"}', model="s2.1-pro-free")
    assert api._failure(detailed) == {"ok": False, "kind": "quota", "message": str(detailed),
                                     "detail": {"request_id": "r", "trace_id": "t", "code": "quota", "hint": "paid_model",
                                                "text": "quota"}}
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
