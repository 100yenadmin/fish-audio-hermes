from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import httpx
import pytest
import respx

from fish_audio import client, stt

URL = "https://api.fish.audio/v1/asr"


def multipart(request):
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content)
    return {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}


@pytest.fixture(autouse=True)
def transport(monkeypatch):
    monkeypatch.setattr(stt, "fish_api_key", lambda: "test-key")
    monkeypatch.setattr(stt, "transport_settings", lambda: {})
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    monkeypatch.setattr(client.random, "uniform", lambda a, b: 0)
    stt._warned_models.clear()
    with httpx.Client() as http:
        monkeypatch.setattr(client, "_client", http)
        yield


@pytest.mark.parametrize("model,suffix,expected", [(None, "ogg", stt.MODEL_PRO),
    ("  Transcribe-1-PRO  ", "webm", stt.MODEL_PRO), ("unknown", "mp3", stt.MODEL_PRO),
    (stt.MODEL_T1, "webm", stt.MODEL_PRO), (stt.MODEL_T1, "mp3", stt.MODEL_T1)])
def test_model_header_fields_and_clean_transcript(tmp_path, model, suffix, expected):
    path = tmp_path / f"sample.{suffix}"
    path.write_bytes(b"synthetic audio")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(json={"text": "<|speaker:0|> Hello from the fish audio plugin for Hermes.\n <|speaker:12|> [laughter]"})
        result = stt.FishAudioTranscriptionProvider().transcribe(path, model=model, language="EN-us", prompt="ignore", extra="ignore")
        assert result == {"success": True, "transcript": "Hello from the fish audio plugin for Hermes. [laughter]", "provider": "fish-audio"}
        request = route.calls.last.request
        assert request.headers["model"].encode() == expected.encode()
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.extensions["timeout"] == {"connect": 10, "read": 300, "write": 60, "pool": 10}
        parts = multipart(request)
        assert parts["audio"].get_payload(decode=True) == path.read_bytes()
        assert parts["audio"].get_filename() == path.name
        assert parts["ignore_timestamps"].get_payload(decode=True) == b"true"
        assert parts["language"].get_payload(decode=True) == b"en"
        assert "diarize" not in parts and "prompt" not in parts and "extra" not in parts
        if expected == stt.MODEL_PRO:
            assert parts["tag_audio_events"].get_payload(decode=True) == b"false"
        else:
            assert "tag_audio_events" not in parts


@pytest.mark.parametrize("suffix,mime", [("mp3", "audio/mpeg"), ("mpga", "audio/mpeg"), ("mpeg", "audio/mpeg"),
    ("wav", "audio/wav"), ("ogg", "audio/ogg"), ("oga", "audio/ogg"), ("opus", "audio/ogg"),
    ("webm", "audio/webm"), ("m4a", "audio/mp4"), ("mp4", "audio/mp4"), ("aac", "audio/aac"),
    ("flac", "audio/flac"), ("other", "application/octet-stream")])
def test_mime_table_and_silence(tmp_path, suffix, mime):
    path = tmp_path / f"sample.{suffix}"
    path.write_bytes(b"synthetic audio")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(json={"text": ""})
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
        assert result["success"] and result["transcript"] == ""
        assert multipart(route.calls.last.request)["audio"].get_content_type() == mime


@pytest.mark.parametrize("language", ["english", "e", "en_us", "123", "en US", None])
def test_invalid_language_omitted(tmp_path, language):
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(json={"text": "hello"})
        assert stt.FishAudioTranscriptionProvider().transcribe(path, language=language)["success"]
        assert "language" not in multipart(route.calls.last.request)


def test_unknown_model_warning_once_no_echo(tmp_path, caplog):
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        mock.post(URL).respond(json={"text": ""})
        for _ in range(2):
            stt.FishAudioTranscriptionProvider().transcribe(path, model="test-key")
    assert len(caplog.records) == 1 and "test-key" not in caplog.text


def test_400_webm_error_is_safe_envelope(tmp_path):
    path = tmp_path / "sample.webm"
    path.write_bytes(b"\x1aE\xdf\xa3synthetic")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(400, json={"message": "Invalid audio input: private test-key"}, headers={"x-fish-trace-id": "fish-trace"})
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
        assert not result["success"] and result["transcript"] == ""
        assert "Fish trace fish-trace" in result["error"] and "private" not in result["error"]
        assert "test-key" not in result["error"] and route.call_count == 1


@pytest.mark.parametrize("failure", ["file", "transport", "json", "secret", "unexpected"])
def test_never_raises(tmp_path, monkeypatch, failure):
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL) if failure in {"transport", "json"} else None
        if failure == "file":
            path = tmp_path / "missing.ogg"
        elif failure == "transport":
            route.mock(side_effect=httpx.ReadTimeout("private test-key"))
        elif failure == "json":
            route.respond(text="invalid private body")
        elif failure == "secret":
            def failed():
                raise RuntimeError("private test-key")
            monkeypatch.setattr(stt, "fish_api_key", failed)
        else:
            def failed(*args, **kwargs):
                raise RuntimeError("private test-key")
            monkeypatch.setattr(client, "transcribe_audio", failed)
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
        assert not result["success"] and result["provider"] == "fish-audio"
        assert "test-key" not in result["error"] and "private" not in result["error"]


def test_no_key_and_surface(monkeypatch):
    provider = stt.FishAudioTranscriptionProvider()
    assert provider.name == "fish-audio" and provider.display_name == "Fish Audio"
    assert provider.default_model() == stt.MODEL_PRO
    assert provider.list_models() == [{"id": stt.MODEL_PRO, "display": "Transcribe 1 Pro (recommended)"},
                                      {"id": stt.MODEL_T1, "display": "Transcribe 1"}]
    assert provider.is_available()
    monkeypatch.setattr(stt, "fish_api_key", lambda: "")
    with respx.mock(assert_all_called=True) as mock:
        assert not provider.is_available()
        result = provider.transcribe("unused.ogg")
        assert not result["success"] and "hermes tools" in result["error"] and not mock.calls


@pytest.mark.parametrize("status", [429, 500, 503])
def test_asr_retries_before_response_bytes(tmp_path, status):
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(side_effect=[httpx.Response(status), httpx.Response(200, json={"text": "ok"})])
        assert stt.FishAudioTranscriptionProvider().transcribe(path)["success"]
        assert route.call_count == 2


@pytest.mark.parametrize("status", [502, 504])
def test_asr_gateway_errors_are_not_retried(tmp_path, status):
    # A proxy 502/504 may follow a request Fish already processed and billed.
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(status))
        assert not stt.FishAudioTranscriptionProvider().transcribe(path)["success"]
        assert route.call_count == 1


def test_asr_success_body_is_capped(tmp_path):
    class Huge(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"text":"' + b"a" * (64 * 1024 * 1024)
            yield b'"}'
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(200, stream=Huge()))
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
        assert not result["success"] and route.call_count == 1


@pytest.mark.parametrize("tail, ok", [(b'"}', True), (b'"}x', False)])
def test_asr_body_cap_boundary(tmp_path, monkeypatch, tail, ok):
    # A body of exactly the cap is read; one byte more is refused before it is buffered.
    from fish_audio import client
    head = b'{"text":"abc'
    monkeypatch.setattr(client, "RESPONSE_CAP", len(head) + 2)

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield head
            yield tail
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(200, stream=Body()))
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
        assert result["success"] is ok and route.call_count == 1


def test_asr_no_retry_after_partial_success_response(tmp_path):
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"text":"'
            raise httpx.ReadError("private test-key")
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(200, stream=Stream()))
        assert not stt.FishAudioTranscriptionProvider().transcribe(path)["success"]
        assert route.call_count == 1


def test_adjacent_speaker_markers_preserve_word_boundary(tmp_path):
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock(assert_all_called=True) as mock:
        mock.post(URL).respond(json={"text": "Hi.<|speaker:1|>Yo"})
        assert stt.FishAudioTranscriptionProvider().transcribe(path)["transcript"] == "Hi. Yo"


@pytest.mark.parametrize("status, kind", [
    (401, "credential"), (403, "credential"), (402, "quota"), (429, "rate_limit"),
    (400, "invalid_request"), (404, "not_found"), (413, "too_large"), (415, "unsupported_media"),
    (503, "availability")])
def test_failure_reports_error_kind(tmp_path, status, kind):
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock() as mock:
        mock.post(URL).respond(status, json={"message": "private test-key"})
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
    assert not result["success"] and result["error_kind"] == kind and "test-key" not in result["error"]


@pytest.mark.parametrize("failure, kind", [("no_key", "credential"), ("transport", "availability"),
                                           ("unexpected", "availability")])
def test_failure_without_status_reports_error_kind(tmp_path, monkeypatch, failure, kind):
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock() as mock:
        if failure == "no_key":
            monkeypatch.setattr(stt, "fish_api_key", lambda: "")
        elif failure == "transport":
            mock.post(URL).mock(side_effect=httpx.ConnectError("private test-key"))
        else:
            def failed(*args, **kwargs):
                raise RuntimeError("private test-key")
            monkeypatch.setattr(client, "transcribe_audio", failed)
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
    assert not result["success"] and result["error_kind"] == kind


def test_success_has_no_error_kind(tmp_path):
    path = tmp_path / "sample.ogg"
    path.write_bytes(b"OggSsynthetic")
    with respx.mock() as mock:
        mock.post(URL).respond(json={"text": "hello"})
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
    assert result["success"] and "error_kind" not in result


@pytest.mark.parametrize("kind_of_path", ["missing", "directory"])
def test_unreadable_local_file_is_invalid_request_not_availability(tmp_path, kind_of_path):
    path = tmp_path / ("missing.ogg" if kind_of_path == "missing" else "folder.ogg")
    if kind_of_path == "directory":
        path.mkdir()
    with respx.mock() as mock:
        result = stt.FishAudioTranscriptionProvider().transcribe(path)
        assert not mock.calls
    assert not result["success"] and result["error_kind"] == "invalid_request"
    assert "Could not read the audio file" in result["error"] and str(tmp_path) not in result["error"]
