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
    assert "sk-fish-" not in str(FishAudioError("availability", None, None, "failed sk-fish-synthetic"))
