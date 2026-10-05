"""W2 field bindings: captured requests and synthetic response projections."""
from dataclasses import asdict
from decimal import Decimal
from email.parser import BytesParser
from email.policy import default
import json
from urllib.parse import parse_qs

import httpx
import msgpack
import pytest
import respx

from fish_audio import account, client, hooks, media, settings, voices
from fish_audio.errors import FishAudioError

BASE = "https://api.fish.audio"
VOICE = "a" * 32
PACKAGE_PRIVATE = {
    "user_id": "synthetic-user", "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-02T00:00:00Z", "stripe_subscription_id": "synthetic-sub",
    "stripe_price_id": "synthetic-price", "current_period_end": "2026-02-01T00:00:00Z",
    "cancel_at": "2026-02-01T00:00:00Z", "scheduled_change": {"type": "synthetic"},
    "last_synced_at": "2026-01-02T00:00:00Z", "subscription_currency": "usd",
    "has_used_trial": True,
}
WALLET_PRIVATE = {
    "_id": "synthetic-wallet", "user_id": "synthetic-user",
    "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
    "has_phone_sha256": True,
}
MODEL_PRIVATE = {
    "type": "tts", "cover_image": "synthetic-cover", "train_mode": "fast",
    "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
    "lock_visibility": True, "dmca_taken_down": True, "takedown_category": "synthetic",
    "default_text": "synthetic text", "licensed": True, "pvc_release_state": "synthetic",
    "pvc_notice_period_months": 2, "pvc_released_at": "2026-01-01T00:00:00Z",
    "pvc_retire_requested_at": "2026-01-01T00:00:00Z",
    "pvc_retire_effective_at": "2026-02-01T00:00:00Z", "quality": "synthetic",
    "mark_count": 7, "shared_count": 8, "unliked": True, "liked": True, "marked": True,
}
MODEL_PUBLIC = {
    "_id": VOICE, "title": "Synthetic voice", "description": "d" * 210,
    "state": "trained", "tags": list("abcdefghij"), "languages": ["en", "ja"],
    "like_count": 3, "task_count": 4, "author": {"nickname": "Synthetic author", "_id": "hidden"},
    "visibility": "private", "samples": [{"title": "Preview", "text": "hi", "audio": "hidden"}],
}
CLONE_DROPPED = {**MODEL_PRIVATE, **{k: v for k, v in MODEL_PUBLIC.items()
    if k not in {"_id", "title", "state"}}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    monkeypatch.setenv("FISH_API_KEY", "synthetic-key")
    monkeypatch.setattr(settings, "_config", lambda: {})
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    hooks._PENDING.clear()
    voices._DESIGNS.clear()
    with httpx.Client() as http:
        monkeypatch.setattr(client, "_client", http)
        yield


@pytest.mark.parametrize("field", PACKAGE_PRIVATE)
def test_package_private_fields_dropped(field):
    with respx.mock() as mock:
        mock.get(BASE + "/wallet/self/package").respond(json={"type": "plus", field: PACKAGE_PRIVATE[field]})
        assert account.get_package("synthetic-key", BASE, strict=True) == {"type": "plus"}


@pytest.mark.parametrize("cancel", [True, False])
def test_package_cancel_at_period_end_retained(cancel):
    with respx.mock() as mock:
        mock.get(BASE + "/wallet/self/package").respond(json={"type": "plus", "cancel_at_period_end": cancel})
        assert account.get_package("synthetic-key", BASE, strict=True) == {"type": "plus", "cancel_at_period_end": cancel}


@pytest.mark.parametrize("field", WALLET_PRIVATE)
def test_wallet_private_fields_dropped(field):
    with respx.mock() as mock:
        mock.get(BASE + "/wallet/self/api-credit").respond(json={"credit": "2.5", "cumulative_top_up": "10",
            "has_free_credit": False, field: WALLET_PRIVATE[field]})
        assert asdict(account.get_wallet("synthetic-key", BASE, strict=True)) == {
            "credit": Decimal("2.5"), "cumulative_top_up": Decimal("10"), "has_free_credit": False}


def test_wallet_free_credit_query():
    with respx.mock() as mock:
        route = mock.get(BASE + "/wallet/self/api-credit").respond(json={"credit": "0", "cumulative_top_up": "0"})
        account.get_wallet("synthetic-key", BASE, strict=True)
        assert route.calls.last.request.url.params["check_free_credit"] == "true"


TTS_BODY = {
    "text": "Hello synthetic world", "temperature": 0.4, "top_p": 0.8,
    "references": [{"audio": b"RIFFsynthetic", "text": "Reference text"}], "reference_id": VOICE,
    "prosody": {"speed": 1.2, "volume": 0.7, "normalize_loudness": False}, "chunk_length": 200,
    "normalize": False, "format": "mp3", "sample_rate": 24000, "mp3_bitrate": 192,
    "opus_bitrate": 32000, "latency": "low", "max_new_tokens": 100, "repetition_penalty": 1.2,
    "min_chunk_length": 50, "condition_on_previous_chunks": False, "early_stop_threshold": 0.3,
    "features": ["synthetic"], "pronunciation_dictionary": [{"items": [{"key": "Fish", "value": "fish"}]}],
}


@pytest.mark.parametrize("field", TTS_BODY)
def test_tts_msgpack_fields_sent(field, tmp_path):
    with respx.mock() as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"ID3synthetic")
        output = tmp_path / "out.mp3"
        client.tts_to_file({**TTS_BODY, "model": "s2.1-pro"}, "synthetic-key", BASE, output)
        request = route.calls.last.request
        assert request.headers["content-type"] == "application/msgpack"
        assert msgpack.unpackb(request.content, raw=False)[field] == TTS_BODY[field]
        assert output.read_bytes() == b"ID3synthetic"


@pytest.mark.parametrize("action", ["search", "mine"])
def test_search_query_fields_sent(action):
    args = dict(action=action, page=2, page_size=5, query="Warm voice", tags=["en", "calm"],
                language="en", sort="task_count")
    with respx.mock() as mock:
        route = mock.get(BASE + "/model").respond(json={"items": [], "total": 0})
        voices.execute(args, "synthetic-key", BASE, "")
        query = route.calls.last.request.url.params
        for name, value in {"page_number": "2", "page_size": "5", "title": "Warm voice",
                            "language": "en", "sort_by": "task_count"}.items():
            assert query[name] == value
        assert query.get_list("tag") == ["en", "calm"]
        assert query.get("self") == ("true" if action == "mine" else None)


@pytest.mark.parametrize("flags,total,exact", [({}, 42, True), ({"window_limited": True}, "1000+", False),
    ({"total_is_exact": False}, "1000+", False), ({"window_limited": False, "total_is_exact": True}, 42, True)],
    ids=["default", "window_limited", "inexact", "exact"])
def test_search_response_projection(flags, total, exact):
    with respx.mock() as mock:
        mock.get(BASE + "/model").respond(json={"items": [MODEL_PUBLIC], "total": 42, **flags})
        result = voices.execute({"action": "search"}, "synthetic-key", BASE, "")
        assert result["total"] == total and result["total_is_exact"] is exact
        assert result["items"] == [{"id": VOICE, "title": "Synthetic voice", "description": "d" * 200,
            "languages": ["en", "ja"], "tags": list("abcdefgh"), "like_count": 3,
            "task_count": 4, "author": "Synthetic author"}]


DESIGN_FIELDS = {"reference_text": "Hello preview", "language": "en", "speed": 1.2, "seed": 42}


@pytest.mark.parametrize("field", DESIGN_FIELDS)
def test_design_json_fields_sent(field):
    with respx.mock() as mock:
        route = mock.post(BASE + "/v1/voice-design").respond(json={"candidates": []})
        voices.execute({"action": "design", "instruction": "Warm", **DESIGN_FIELDS}, "synthetic-key", BASE, "")
        assert json.loads(route.calls.last.request.content)[field] == DESIGN_FIELDS[field]


@pytest.mark.parametrize("action", ["clone", "update"])
def test_voice_form_fields_sent(action, tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"ID3synthetic")
    fields = {"title": "Warm voice", "description": "Synthetic description", "tags": ["en", "calm"]}
    method, path = ("POST", "/model") if action == "clone" else ("PATCH", "/model/" + VOICE)
    with respx.mock() as mock:
        route = mock.request(method, BASE + path).respond(json={"_id": VOICE})
        voices.execute(dict(action=action, voice_id=VOICE, consent=True, sample_paths=[str(sample)], **fields),
                       "synthetic-key", BASE, "")
        request = route.calls.last.request
        if action == "clone":
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content)
            form = {}
            for part in message.iter_parts():
                form.setdefault(part.get_param("name", header="content-disposition"), []).append(
                    part.get_payload(decode=True).decode())
        else:
            form = parse_qs(request.content.decode())
        for field, value in fields.items():
            assert form[field] == (value if isinstance(value, list) else [value])


@pytest.mark.parametrize("field", CLONE_DROPPED)
def test_clone_response_fields_dropped(field, tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"ID3synthetic")
    with respx.mock() as mock:
        mock.post(BASE + "/model").respond(json={"_id": VOICE, "title": "Warm", "state": "trained",
                                                  field: CLONE_DROPPED[field]})
        result = voices.execute(dict(action="clone", title="Warm", consent=True, sample_paths=[str(sample)]),
                                "synthetic-key", BASE, "")
        assert result == {"id": VOICE, "title": "Warm", "state": "trained"}


@pytest.mark.parametrize("field", MODEL_PRIVATE)
def test_voice_detail_private_fields_dropped(field):
    with respx.mock() as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json={**MODEL_PUBLIC, field: MODEL_PRIVATE[field]})
        result = voices.execute({"action": "get", "voice_id": VOICE}, "synthetic-key", BASE, "")
        assert field not in result
        assert result["id"] == VOICE and result["title"] == "Synthetic voice"


def test_voice_detail_public_fields_projected():
    with respx.mock() as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json=MODEL_PUBLIC)
        result = voices.execute({"action": "get", "voice_id": VOICE}, "synthetic-key", BASE, "")
        assert result == {"id": VOICE, "title": "Synthetic voice", "description": "d" * 200,
            "languages": ["en", "ja"], "tags": list("abcdefgh"), "like_count": 3, "task_count": 4,
            "author": "Synthetic author", "visibility": "private", "state": "trained", "source": None,
            "samples": [{"title": "Preview", "text": "hi"}]}


ERROR_ENDPOINTS = ["package", "wallet", "asr", "tts", "timestamps", "design", "clone", "get", "update", "delete"]


@pytest.mark.parametrize("endpoint", ERROR_ENDPOINTS)
@pytest.mark.parametrize("field", ["status", "message", "reason"])
def test_endpoint_error_fields_safely_projected(endpoint, field, tmp_path):
    method, path = {
        "package": ("GET", "/wallet/self/package"), "wallet": ("GET", "/wallet/self/api-credit"),
        "asr": ("POST", "/v1/asr"), "tts": ("POST", "/v1/tts"),
        "timestamps": ("POST", "/v1/tts/stream/with-timestamp"), "design": ("POST", "/v1/voice-design"),
        "clone": ("POST", "/model"), "get": ("GET", "/model/" + VOICE),
        "update": ("PATCH", "/model/" + VOICE), "delete": ("DELETE", "/model/" + VOICE),
    }[endpoint]
    # HTTP status is authoritative; arbitrary prose is withheld, including reason.
    body = {field: 200 if field == "status" else "synthetic upstream prose"}
    with respx.mock() as mock:
        route = mock.request(method, BASE + path).respond(403, json=body)
        with pytest.raises(FishAudioError) as raised:
            if endpoint == "package":
                account.get_package("synthetic-key", BASE, strict=True)
            elif endpoint == "wallet":
                account.get_wallet("synthetic-key", BASE, strict=True)
            elif endpoint == "asr":
                client.transcribe_audio(b"ID3synthetic", "sample.mp3", "audio/mpeg", {},
                    key="synthetic-key", base_url=BASE, model="transcribe-1-pro")
            elif endpoint in {"tts", "timestamps"}:
                client.tts_to_file({"text": "Hi", "model": "s2.1-pro"}, "synthetic-key", BASE,
                                  tmp_path / "out.mp3", events=[] if endpoint == "timestamps" else None)
            else:
                sample = tmp_path / "sample.mp3"
                sample.write_bytes(b"ID3synthetic")
                voices.execute(dict(action=endpoint, voice_id=VOICE, title="Warm", instruction="Warm",
                    consent=True, sample_paths=[str(sample)]), "synthetic-key", BASE, "")
        error = raised.value
        assert error.status == 403 and error.kind == "credential"
        assert str(error) == "Check your Fish Audio API key and access: https://fish.audio/app/api-keys"
        assert route.call_count == 1
