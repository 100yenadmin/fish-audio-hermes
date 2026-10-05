"""Explicit tools exercised through mocked Fish HTTP, without importing Hermes."""
import base64
from decimal import Decimal
import json
from pathlib import Path

import pytest
import respx

from fish_audio import account, client, hooks, media, settings, tools, voices

BASE = "https://api.fish.audio"
VOICE = "a" * 32
FIXTURES = Path(__file__).parent / "fixtures"
WAV = b"RIFF\x10\x00\x00\x00WAVEsynthetic-audio"


@pytest.fixture(autouse=True)
def context(monkeypatch, tmp_path):
    monkeypatch.setattr("fish_audio.tool_support.fish_api_key", lambda: "test-key")
    monkeypatch.setattr(settings, "_config", lambda: {})
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: None)
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    monkeypatch.setattr(hooks, "audio_output_dir", lambda: tmp_path)
    hooks._PENDING.clear()
    voices._DESIGNS.clear()


def call(handler, **args):
    return json.loads(handler(args, session_id="s1"))


@pytest.mark.parametrize("fmt,wire,voice", [("ogg", "opus", True), ("mp3", "mp3", True), ("wav", "wav", False)])
def test_speak_media_tag_and_native_output(tmp_path, fmt, wire, voice):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"synthetic audio")
        result = call(tools.fish_speak, text="[excited] hello <|speaker:0|>", voice=VOICE, format=fmt)
        assert result["success"], result
        path = Path(result["file_path"])
        assert path.parent == tmp_path and path.suffix == "." + fmt
        assert path.read_bytes() == b"synthetic audio"
        assert result["media_tag"] == ("[[audio_as_voice]]\n" if voice else "") + f"MEDIA:{path}"
        assert result["billing"] == "fish-audio"
        body = json.loads(route.calls.last.request.content)
        assert body["format"] == wire and body["reference_id"] == VOICE
        assert body["text"] == "[excited] hello <|speaker:0|>"
        assert hooks.take("other") == []
        assert hooks.take("s1")[0][:2] == (str(path), voice)


def test_speak_multi_pronunciations_and_timestamp_note():
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        result = call(tools.fish_speak, text="hello", speakers=[VOICE, "b" * 32],
                      pronunciations={"Codex": "code ex"}, timestamps=True)
        body = json.loads(route.calls.last.request.content)
        assert body["reference_id"] == [VOICE, "b" * 32]
        assert body["pronunciation_dictionary"] == [{"items": [{"key": "Codex", "value": "code ex"}]}]
        assert "coming in v0.2" in result["note"] and result["success"]
    with respx.mock(assert_all_called=True) as mock:
        result = call(tools.fish_speak, text="hello", speakers=[VOICE, VOICE], model="s1")
        assert not result["success"] and "s2.1-pro" in result["error"]
        assert not mock.calls


def test_speak_voice_precedence_and_free_notice(monkeypatch):
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"fish-audio": {"voice": "b" * 32}}})
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: account.Wallet(Decimal(0), Decimal(0), False))
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"audio")
        result = call(tools.fish_speak, text="hello", voice=VOICE)
        assert result["notice"] == settings.FREE_MODEL_NOTICE and result["voice"] == VOICE
        assert json.loads(route.calls.last.request.content)["reference_id"] == VOICE
        result = call(tools.fish_speak, text="hello")
        assert result["voice"] == "b" * 32


@pytest.mark.parametrize("args", [{"text": " "}, {"text": "hi", "voice": "bad"},
    {"text": "hi", "speed": 3}, {"text": "hi", "pronunciations": {"x": 1}},
    {"text": "hi", "pronunciations": {str(i): "a" for i in range(201)}}])
def test_speak_invalid_args_never_raise_or_request(args):
    with respx.mock(assert_all_called=True) as mock:
        assert json.loads(tools.fish_speak(args))["success"] is False
        assert not mock.calls


def test_speak_fish_error_is_safe_envelope():
    with respx.mock(assert_all_called=True) as mock:
        mock.post(BASE + "/v1/tts").respond(401, json={"message": "test-key secret body"})
        result = tools.fish_speak({"text": "hi"})
        assert not json.loads(result)["success"] and "test-key" not in result and "secret body" not in result


def library_item():
    return {"_id": VOICE, "title": "Voice", "description": "d" * 300, "languages": ["en"],
            "tags": list("abcdefghij"), "like_count": 3, "task_count": 4,
            "author": {"nickname": "name", "_id": "hidden-id", "avatar": "blob"},
            "samples": [{"title": "Preview", "text": "hello", "audio": "huge-blob"}],
            "audio": "huge-blob", "visibility": "private", "state": "trained", "source": "voice_design"}


@pytest.mark.parametrize("action,limited", [("search", False), ("search", True), ("mine", False)])
def test_voice_search_mapping(action, limited):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(BASE + "/model").respond(json={"items": [library_item()], "total": 99,
                            "window_limited": limited, "total_is_exact": not limited})
        raw = tools.fish_voices({"action": action, "query": "warm", "tags": ["en"],
                                 "language": "en", "sort": "task_count", "page": 2, "page_size": 5})
        result = json.loads(raw)
        assert result["success"] and result["total"] == ("1000+" if limited else 99)
        item = result["items"][0]
        assert item["id"] == VOICE and item["author"] == "name" and len(item["tags"]) == 8
        assert len(item["description"]) == 200
        assert all(v not in raw for v in ("huge-blob", "hidden-id", '"samples"', '"avatar"'))
        params = route.calls.last.request.url.params
        assert params["title"] == "warm" and params["sort_by"] == "task_count"
        assert params["page_number"] == "2" and params["tag"] == "en"
        assert (params.get("self") == "true") == (action == "mine")


def test_voice_get_samples_are_text_only():
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/model/" + VOICE).respond(json=library_item())
        result = call(tools.fish_voices, action="get", voice_id=VOICE)
        assert result["samples"] == [{"title": "Preview", "text": "hello"}]
        assert result["visibility"] == "private" and result["source"] == "voice_design"


def test_clone_consent_and_symlink_rejected(tmp_path):
    link = tmp_path / "linked.mp3"
    link.symlink_to(FIXTURES / "synthetic.mp3")
    with respx.mock(assert_all_called=True) as mock:
        result = call(tools.fish_voices, action="clone", title="Voice", sample_paths=[str(link)])
        assert "speaker's permission" in result["error"]
        result = call(tools.fish_voices, action="clone", title="Voice", sample_paths=[str(link)], consent=True)
        assert "symlink" in result["error"]
        assert not mock.calls


def test_clone_private_fast_and_never_retried(monkeypatch):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/model").respond(201, json={"_id": VOICE, "title": "Voice", "state": "trained"})
        args = dict(action="clone", title="Voice", sample_paths=[str(FIXTURES / "synthetic.mp3")], consent=True, texts=["hello"])
        result = call(tools.fish_voices, **args)
        assert result == {"success": True, "id": VOICE, "title": "Voice", "state": "trained"}
        body = route.calls.last.request.content
        for field, value in (("visibility", "private"), ("train_mode", "fast"), ("type", "tts"),
                              ("enhance_audio_quality", "true"), ("texts", "hello")):
            assert f'name="{field}"\r\n\r\n{value}'.encode() in body
        assert (FIXTURES / "synthetic.mp3").read_bytes() in body
        route.respond(503, json={"message": "not echoed"})
        before = route.call_count
        assert not call(tools.fish_voices, **args)["success"]
        assert route.call_count == before + 1


def test_design_save_signature_stays_server_side(monkeypatch):
    signature = "v1:synthetic-private-signature"
    with respx.mock(assert_all_called=True) as mock:
        design = mock.post(BASE + "/v1/voice-design").respond(json={"candidates": [{"index": 0, "text": "preview text",
            "signature": signature, "audio_base64": base64.b64encode(WAV).decode(), "duration_ms": 123,
            "features": {"tone": "warm", "signature": signature, "nested": [signature]}}]})
        save = mock.post(BASE + "/model").respond(201, json={"_id": VOICE, "title": "Saved", "source": "voice_design"})
        raw = tools.fish_voices({"action": "design", "instruction": "warm", "n": 1})
        assert signature not in raw
        result = json.loads(raw)
        candidate = result["candidates"][0]
        assert Path(candidate["file_path"]).read_bytes() == WAV
        assert candidate["media_tag"] == "MEDIA:" + candidate["file_path"]
        assert design.calls.last.request.headers["model"] == "voice-design-1"
        raw_save = tools.fish_voices({"action": "save", "design_token": candidate["design_token"], "title": "Saved"})
        assert signature not in raw_save and json.loads(raw_save)["source"] == "voice_design"
        body = save.calls.last.request.content
        assert signature.encode() in body and b'name="voice_design_signatures"' in body and b'preview text' in body
        monkeypatch.setattr(voices, "_clock", lambda: 1e20)
        assert "design again" in call(tools.fish_voices, action="save", design_token=candidate["design_token"], title="Saved")["error"]
        assert save.call_count == 1
        assert "design again" in call(tools.fish_voices, action="save", design_token="unknown", title="Saved")["error"]


@pytest.mark.parametrize("action", ["update", "delete"])
@pytest.mark.parametrize("status", [204, 404, 503])
def test_voice_mutation_status_and_no_retry(action, status):
    with respx.mock(assert_all_called=True) as mock:
        method = "PATCH" if action == "update" else "DELETE"
        route = mock.request(method, BASE + "/model/" + VOICE).respond(status)
        result = call(tools.fish_voices, action=action, voice_id=VOICE, title="Updated", tags=["en"])
        assert result["success"] == (status == 204)
        assert route.call_count == 1
        if action == "update":
            assert route.calls.last.request.headers["content-type"] == "application/x-www-form-urlencoded"
            assert b"title=Updated" in route.calls.last.request.content


def test_get_retry_only_before_success_bytes(monkeypatch):
    import httpx
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(BASE + "/model").mock(side_effect=[httpx.Response(503), httpx.Response(429),
                                                          httpx.Response(200, json={"items": [], "total": 0})])
        assert call(tools.fish_voices, action="search")["success"] and route.call_count == 3
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"items":'
            raise httpx.ReadError("synthetic failure test-key")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(BASE + "/model").respond(stream=Broken())
        result = tools.fish_voices({"action": "search"})
        assert not json.loads(result)["success"] and "test-key" not in result and route.call_count == 1


def test_clone_upload_uses_validated_fd_bytes_after_path_replacement(tmp_path, monkeypatch):
    path = tmp_path / "voice.mp3"
    original = b"ID3safe-synthetic"
    path.write_bytes(original)
    validate = media.validate_input_file
    def replace_after_validation(*args, **kwargs):
        result = validate(*args, **kwargs)
        path.write_bytes(b"ID3replacement")
        return result
    monkeypatch.setattr(media, "validate_input_file", replace_after_validation)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/model").respond(201, json={"_id": VOICE})
        assert call(tools.fish_voices, action="clone", title="Voice", sample_paths=[str(path)], consent=True)["success"]
        assert original in route.calls.last.request.content and b"replacement" not in route.calls.last.request.content
