import json

import pytest

from fish_audio.errors import FishAudioError, response_error


@pytest.mark.parametrize("status,kind", [(400, "invalid_request"), (401, "credential"),
    (403, "credential"), (402, "quota"), (404, "not_found"), (413, "too_large"),
    (415, "unsupported_media"), (429, "rate_limit"), (500, "availability"), (503, "availability")])
def test_status_messages(status, kind, monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "test-key")
    body = json.dumps({"message": "DO NOT ECHO test-key", "detail": "private", "code": "bad_request"}).encode()
    error = response_error(status, body=body, key="test-key")
    assert error.kind == kind and error.status == status
    assert "DO NOT ECHO" not in str(error) and "private" not in str(error) and "test-key" not in str(error)
    assert "bad_request" in str(error) and "request id" not in str(error)
    if status in {401, 403}:
        assert "https://fish.audio/app/api-keys" in str(error)
    if status == 402:
        assert "https://fish.audio/app/developers/billing" in str(error)
        assert "app plan credits are separate from API credits" in str(error)
    if status == 429:
        for tier in ("<$100: 5", "≥$100: 15", "≥$1k: 50"):
            assert tier in str(error)


@pytest.mark.parametrize("status", [402, 403, 429])
def test_free_hint(status):
    assert "Switch to s2.1-pro and top up" in str(response_error(status, model="s2.1-pro-free"))
    assert "Switch" not in str(response_error(status, model="s2.1-pro"))


def test_request_id_header_then_body():
    body = b'{"request_id":"body-id"}'
    assert str(response_error(400, {"x-request-id": "header-id"}, body)).endswith("(request id header-id)")
    assert str(response_error(400, body=body)).endswith("(request id body-id)")
    assert response_error(400).request_id is None


@pytest.mark.parametrize("body", [b"not json private", b"[]", b'{"code":"sk-fish-synthetic"}', b'{"code":"UPPER"}', b'{"code":[1]}'])
def test_untrusted_body_never_echoed(body):
    assert str(response_error(400, body=body)) == str(response_error(400))


def test_request_id_and_code_redacted(monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "synthetic_key")
    error = response_error(402, {"x-request-id": "synthetic_key"}, b'{"code":"synthetic_key"}', key="synthetic_key")
    assert "synthetic_key" not in str(error)
    synthetic = "sk-" + "a" * 48
    assert synthetic not in str(FishAudioError("availability", None, None, f"failed {synthetic}"))


@pytest.mark.parametrize("headers,trace", [
    ({"x-fish-trace-id": "fish-trace"}, "fish-trace"),
    ({"x-cloud-trace-context": "4bf92f3577b34da6a3ce929d0e0e4736/123;o=1"}, "4bf92f3577b34da6a3ce929d0e0e4736"),
    ({"x-cloud-trace-context": "4bf92f"}, "4bf92f"),
    ({"x-fish-trace-id": "primary", "x-cloud-trace-context": "abcd/123"}, "primary"),
])
def test_trace_forms_and_both_references(headers, trace):
    error = response_error(400, headers, b'{"request_id":"supplied"}')
    assert error.trace_id == trace and error.request_id == "supplied"
    assert f"(Fish trace {trace})" in str(error) and "request id supplied" in str(error)
    absent = response_error(400)
    assert absent.trace_id is None and absent.request_id is None
    assert "Fish trace" not in str(absent) and "request id" not in str(absent)


def test_header_code_wins_and_bad_header_falls_to_body():
    error = response_error(500, {"x-fish-error-code": "invalid_api_key"}, b'{"code":"quota"}')
    assert error.kind == "credential" and "invalid_api_key" in str(error)
    assert "Code: quota" not in str(error)
    assert response_error(500, {"x-fish-error-code": "BAD!"}, b'{"code":"quota"}').kind == "quota"
    assert response_error(401, body=b"No permission -- see authorization schemes").kind == "credential"


@pytest.mark.parametrize("status,message", [(400, "Reference not found"), (404, "Model not found"), (400, "rEfErEnCe NoT fOuNd")])
def test_known_voice_failure(status, message):
    error = response_error(status, body=json.dumps({"message": message}).encode())
    assert error.kind == "voice_not_found"
    assert "private to another account" in str(error) and "https://fish.audio/discovery" in str(error)
    assert message not in str(error)


def test_defaulted_paid_quota_hint_and_safe_trace(monkeypatch):
    assert "Switch to s2.1-pro" not in str(response_error(402, model="s2.1-pro", defaulted=True))
    key = "sk-" + "a" * 48
    monkeypatch.setenv("FISH_API_KEY", key)
    assert key not in str(response_error(400, {"x-fish-trace-id": key}, key=key))
    assert response_error(400, body=None).kind == "invalid_request"


@pytest.mark.parametrize("model,hint", [("transcribe-1", True), ("transcribe-1-pro", False)])
def test_asr_decode_wording_is_specific(model, hint):
    message = str(response_error(400, model=model))
    assert message.startswith("Fish Audio could not decode this audio.") and "synthesis" not in message
    assert ("transcribe-1-pro accepts more formats (including WebM)." in message) == hint


def test_paid_quota_keeps_link_without_redundant_switch():
    message = str(response_error(402, model="s2.1-pro", defaulted=True))
    assert "Switch to s2.1-pro" not in message and "https://fish.audio/app/developers/billing" in message
    assert "Switch to s2.1-pro" in str(response_error(402, model="s2.1-pro-free"))
