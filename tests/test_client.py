import json
import re
import sys
from pathlib import Path
from types import ModuleType

import httpx
import msgpack
import pytest
import respx

from fish_audio import client, media
from fish_audio.errors import FishAudioError

URL = "https://api.fish.audio/v1/tts"
PARAMS = {"text": "hello", "model": "s2.1-pro", "format": "mp3", "reference_id": "a" * 32}


@pytest.fixture(autouse=True)
def transport(monkeypatch):
    # Close only test-owned transports; provider.release never owns the shared client.
    with httpx.Client(timeout=httpx.Timeout(connect=10, read=60, write=60, pool=10), follow_redirects=False) as http:
        monkeypatch.setattr(client, "_client", http)
        monkeypatch.setattr(client.random, "uniform", lambda a, b: 0)
        yield http


def call(path, params=None, sleep=None):
    return client.tts_to_file(params or PARAMS, "test-key", "https://api.fish.audio", path, sleep=sleep or (lambda _: None))


def test_json_headers_ua_and_output(tmp_path, transport, monkeypatch):
    module = ModuleType("hermes_cli")
    module.__version__ = "0.21.5"
    monkeypatch.setitem(sys.modules, "hermes_cli", module)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(content=b"ID3synthetic")
        path = tmp_path / "out.mp3"
        assert call(path) == str(path) and path.read_bytes() == b"ID3synthetic"
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer test-key"
        assert dict(request.headers)["model"] == "s2.1-pro"
        assert request.headers["User-Agent"] == f"fish-audio-hermes/{client.PLUGIN_VERSION} (hermes-agent/0.21.5)"
        assert request.headers["Content-Type"] == "application/json"
        assert json.loads(request.content) == {k: v for k, v in PARAMS.items() if k != "model"}
        assert "authorization" not in transport.headers
        assert not transport.follow_redirects


@pytest.mark.parametrize("refs,packed", [
    ([{"audio": b"RIFFsynthetic", "text": "sample"}], True),
    ([[{"audio": b"RIFFsynthetic", "text": "sample"}]], True),
    ([{"audio": "base64-text", "text": "sample"}], False), ([], False),
])
def test_msgpack_only_bytes_references(tmp_path, refs, packed):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(content=b"audio")
        call(tmp_path / "out.wav", {**PARAMS, "references": refs})
        r = route.calls.last.request
        assert r.headers["content-type"] == ("application/msgpack" if packed else "application/json")
        body = msgpack.unpackb(r.content, raw=False) if packed else json.loads(r.content)
        assert body["references"] == refs


@pytest.mark.parametrize("failure", [429, 500, 503, "transport"])
def test_retry_before_bytes(tmp_path, failure):
    delays = []
    if failure == "timeout":
        failed = httpx.ReadTimeout("test-key must not leak")
    elif failure == "transport":
        failed = httpx.ConnectError("test-key must not leak")
    else:
        failed = httpx.Response(failure, content=b"private")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(side_effect=[failed, failed, httpx.Response(200, content=b"audio")])
        call(tmp_path / "out.mp3", sleep=delays.append)
        assert route.call_count == 3 and delays == [0.5, 1]


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 413, 415, 302, 502, 504])
def test_nonretryable_status(tmp_path, status):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(status, content=b"private test-key", headers={"x-request-id": "fish-id"})
        with pytest.raises(FishAudioError) as exc:
            call(tmp_path / "out.mp3")
        assert route.call_count == 1 and "test-key" not in str(exc.value)
        assert exc.value.request_id == "fish-id"
        assert not list(tmp_path.iterdir())


class Stream(httpx.SyncByteStream):
    def __init__(self, first=b"", fail=False):
        self.first, self.fail, self.closed = first, fail, False

    def __iter__(self):
        if self.first:
            yield self.first
        if self.fail:
            raise httpx.ReadError("private test-key")

    def close(self):
        self.closed = True


def test_no_retry_after_partial_write(tmp_path):
    stream = Stream(b"audio", fail=True)
    path = tmp_path / "out.mp3"
    path.write_bytes(b"previous")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(200, stream=stream))
        with pytest.raises(FishAudioError, match="unavailable"):
            call(path)
        assert route.call_count == 1 and stream.closed
    assert path.read_bytes() == b"previous" and list(tmp_path.iterdir()) == [path]


def test_uploaded_request_read_failure_before_response_is_not_retried(tmp_path):
    stream = Stream(fail=True)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(stream=stream)
        with pytest.raises(FishAudioError):
            call(tmp_path / "out.mp3")
        assert route.call_count == 1 and stream.closed


def test_disconnected_credential_body_still_not_retried(tmp_path):
    stream = Stream(fail=True)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(401, headers={"x-request-id": "fish-id"}, stream=stream))
        with pytest.raises(FishAudioError) as exc:
            call(tmp_path / "out.mp3")
        assert exc.value.kind == "credential" and exc.value.request_id == "fish-id"
        assert "test-key" not in str(exc.value)
        assert route.call_count == 1 and stream.closed


def test_exhaustion_is_safe(tmp_path):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(side_effect=httpx.ConnectError("private test-key"))
        with pytest.raises(FishAudioError) as exc:
            call(tmp_path / "out.mp3")
        assert route.call_count == 3 and "test-key" not in str(exc.value)


def test_error_body_bounded_and_closed(tmp_path):
    class ErrorStream(Stream):
        def __iter__(self):
            yield b"x" * (64 * 1024)
            raise AssertionError("must not consume more than 64 KiB")
    stream = ErrorStream()
    with respx.mock(assert_all_called=True) as mock:
        mock.post(URL).mock(return_value=httpx.Response(400, stream=stream))
        with pytest.raises(FishAudioError):
            call(tmp_path / "out.mp3")
    assert stream.closed


def test_client_zero_and_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(client, "atomic_write", lambda path, chunks: media.atomic_write(path, chunks, cap=4))
    for content in (b"", b"12345"):
        with respx.mock(assert_all_called=True) as mock:
            route = mock.post(URL).respond(content=content)
            with pytest.raises(ValueError):
                call(tmp_path / "out.mp3")
            assert route.call_count == 1
        assert not list(tmp_path.iterdir())


def test_atomic_replace_and_fsync(tmp_path, monkeypatch):
    path = tmp_path / "out.wav"
    path.write_bytes(b"old")
    events = []
    real_replace = media.os.replace
    monkeypatch.setattr(media.os, "fsync", lambda fd: events.append("fsync"))
    def replace(source, dest):
        assert path.read_bytes() == b"old"
        assert __import__("pathlib").Path(source).parent == path.parent
        events.append("replace")
        real_replace(source, dest)
    monkeypatch.setattr(media.os, "replace", replace)
    media.atomic_write(path, [b"new", b" audio"])
    assert path.read_bytes() == b"new audio" and events == ["fsync", "replace"]


def test_atomic_failure_preserves_destination(tmp_path, monkeypatch):
    path = tmp_path / "out.wav"
    path.write_bytes(b"old")
    def fail(source, dest):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(media.os, "replace", fail)
    with pytest.raises(OSError):
        media.atomic_write(path, [b"new"])
    assert path.read_bytes() == b"old" and list(tmp_path.iterdir()) == [path]


def test_lazy_timeout_and_unknown_ua(monkeypatch):
    monkeypatch.setattr(client, "_client", None)
    monkeypatch.setitem(sys.modules, "hermes_cli", None)
    assert client._user_agent() == f"fish-audio-hermes/{client.PLUGIN_VERSION} (hermes-agent/unknown)"
    http = client._http_client()
    try:
        assert http.timeout == httpx.Timeout(connect=10, read=60, write=60, pool=10)
        assert not http.follow_redirects and client._http_client() is http
        assert "authorization" not in http.headers
    finally:
        http.close()


@pytest.mark.parametrize("text", ["", " \t\n"])
def test_empty_text_and_unknown_model_never_request(tmp_path, text):
    with respx.mock(assert_all_called=True) as mock:
        with pytest.raises(ValueError, match="Nothing to say: the text is empty."):
            call(tmp_path / "out.mp3", {**PARAMS, "text": text})
        with pytest.raises(ValueError, match="Unknown Fish Audio TTS model"):
            call(tmp_path / "out.mp3", {**PARAMS, "model": "unknown-model"})
        assert not mock.calls


@pytest.mark.parametrize("valid,raises", [(True, False), (False, False), (True, True)])
def test_traceparent_optional_valid_context(tmp_path, monkeypatch, valid, raises):
    from types import SimpleNamespace
    module = ModuleType("opentelemetry.trace")
    def current():
        if raises:
            raise RuntimeError("synthetic tracing failure")
        context = SimpleNamespace(is_valid=valid, trace_id=int("4bf92f3577b34da6a3ce929d0e0e4736", 16),
                                  span_id=int("00f067aa0ba902b7", 16), trace_flags=1)
        return SimpleNamespace(get_span_context=lambda: context)
    module.get_current_span = current
    monkeypatch.setitem(sys.modules, "opentelemetry", ModuleType("opentelemetry"))
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", module)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(content=b"audio")
        call(tmp_path / "out.mp3")
        headers = route.calls.last.request.headers
        if valid and not raises:
            assert headers["traceparent"] == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        else:
            assert "traceparent" not in headers


def test_missing_otel_no_header_and_defaulted_flag_not_sent(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).respond(content=b"audio")
        call(tmp_path / "out.mp3", {**PARAMS, "model_defaulted": True})
        r = route.calls.last.request
        assert "traceparent" not in r.headers and "model_defaulted" not in json.loads(r.content)


def test_defaulted_paid_402_carries_hint_and_trace(tmp_path):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(URL).respond(402, headers={"x-fish-trace-id": "fish-trace"})
        with pytest.raises(FishAudioError) as exc:
            call(tmp_path / "out.mp3", {**PARAMS, "model_defaulted": True})
        assert "Switch to s2.1-pro" not in str(exc.value)
        assert "https://fish.audio/app/developers/billing" in str(exc.value)
        assert exc.value.trace_id == "fish-trace"


def test_atomic_audio_permissions(tmp_path):
    import stat
    path = tmp_path / "audio.ogg"
    media.atomic_write(path, [b"OggSsynthetic"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_client_import_without_msgpack(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    monkeypatch.setitem(sys.modules, "msgpack", None)
    spec = importlib.util.spec_from_file_location("fish_audio._client_without_msgpack", Path(client.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.tts_to_file) and module._client is None


@pytest.mark.parametrize("endpoint", ["tts", "asr", "model", "voice-design"])
def test_read_timeout_after_upload_never_retries(tmp_path, endpoint):
    def uploaded(request):
        assert request.read()
        raise httpx.ReadTimeout("synthetic test-key", request=request)
    with respx.mock(assert_all_called=True) as mock:
        url = "https://api.fish.audio/" + ("model" if endpoint == "model" else "v1/" + endpoint)
        route = mock.post(url).mock(side_effect=uploaded)
        with pytest.raises(FishAudioError) as exc:
            if endpoint == "tts":
                call(tmp_path / "out.mp3")
            elif endpoint == "asr":
                client.transcribe_audio(b"OggSsynthetic", "sample.ogg", "audio/ogg", {},
                                       key="test-key", base_url="https://api.fish.audio", model="transcribe-1-pro")
            elif endpoint == "model":
                client.post_multipart("/model", {"title": "x"}, {"voices": ("sample.ogg", b"OggS", "audio/ogg")},
                                      "test-key", "https://api.fish.audio", 60)
            else:
                client.post_json("/v1/voice-design", {"instruction": "warm"}, "test-key", "https://api.fish.audio", 60)
        assert route.call_count == 1 and "test-key" not in str(exc.value)


def test_plugin_versions_agree():
    import fish_audio
    root = Path(__file__).resolve().parents[1]
    manifest = re.search(r"^version: (\S+)$", (root / "plugin.yaml").read_text(), re.M).group(1)
    project = re.search(r'^version = "([^"]+)"$', (root / "pyproject.toml").read_text(), re.M).group(1)
    assert manifest == project == client.PLUGIN_VERSION == fish_audio.PLUGIN_VERSION


def test_desktop_disclosure_worded_identically():
    # SECURITY.md, the README and plugin.yaml carry the same Desktop-routes disclosure sentence.
    root = Path(__file__).resolve().parents[1]
    texts = [" ".join((root / name).read_text().split()) for name in ("SECURITY.md", "README.md", "plugin.yaml")]
    start = "The Desktop Voices page adds gateway routes under /api/plugins/fish-audio/"
    end = "until it closes."
    sentence = texts[0][texts[0].index(start):].split(end, 1)[0] + end
    expected = "The Desktop Voices page adds gateway routes under /api/plugins/fish-audio/, behind the Hermes dashboard's existing authentication. On that page, previews, clones and voice designs are billed; cloning a voice or saving a designed one creates a voice in your Fish account; cloning and deleting are confirmed on the page (a speaker-consent box, a typed voice name) instead of through Hermes's approval gate; and Use sets the profile's Fish voice, and its speech provider when none is set. Preview and design audio files are deleted from the gateway once returned; a design's audio is held in gateway memory so you can save it, until it is saved, a later design or save finds it over an hour old, or the gateway stops; clone uploads are deleted after the clone, or by the next upload once idle for 15 minutes. With a key set, Desktop reads your Fish wallet when an agent is selected and about every five minutes, for the status-bar credit; favourites stay in Desktop's plugin storage on that computer, and fetched previews stay in the window's memory until it closes."
    assert sentence == expected
    assert "approval gate" in sentence and "billed" in sentence
    assert all(sentence in text for text in texts)


@pytest.mark.parametrize("tail, ok", [(b'}', True), (b'} ', False)])
def test_json_body_cap_boundary(monkeypatch, tail, ok):
    # A JSON body of exactly the cap is read; one byte more is refused before it is buffered.
    head = b'{"a": 1'
    monkeypatch.setattr(client, "RESPONSE_CAP", len(head) + 1)

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield head
            yield tail
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://api.fish.audio/model").mock(return_value=httpx.Response(200, stream=Body()))
        if ok:
            assert client.get_json("/model", {}, "test-key", "https://api.fish.audio") == {"a": 1}
        else:
            with pytest.raises(FishAudioError) as exc:
                client.get_json("/model", {}, "test-key", "https://api.fish.audio")
            assert exc.value.kind == "too_large"
        assert route.call_count == 1
