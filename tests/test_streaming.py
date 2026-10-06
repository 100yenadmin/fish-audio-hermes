"""Streaming voice through Hermes's plugin hook: stream_pcm over mocked HTTP and a loopback WebSocket."""
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import ModuleType

import httpx
import msgpack
import pytest
import respx

from fish_audio import settings, state, streaming, tts
from fish_audio.errors import FishAudioError

VOICE = "a" * 32
KEY = "key-" + "s" * 24


def config(transport=None, base_url=None, **plugin):
    entries = dict(plugin)
    if transport:
        entries["transport"] = transport
    if base_url:
        entries["base_url"] = base_url
    return {"tts": {"provider": "fish-audio", "fish-audio": {"voice": VOICE, "model": "s2.1-pro"}},
            "plugins": {"entries": {"fish-audio": {"settings": entries}}}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(streaming, "fish_api_key", lambda: KEY)
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: None)
    monkeypatch.setattr(settings, "_config", lambda: config())
    monkeypatch.setattr(state, "_last", None)
    with httpx.Client(timeout=httpx.Timeout(connect=10, read=60, write=60, pool=10), follow_redirects=False) as http:
        monkeypatch.setattr("fish_audio.client._client", http)
        yield


def speak(text="Hello there."):
    return list(streaming.stream_pcm(text))


def test_pcm_alignment_with_odd_chunks():
    pieces = [b"\x01", b"\x02\x03\x04", b"\x05", b"", b"\x06\x07\x08\x09\x0a", b"\x0b"]
    out = list(streaming._aligned(iter(pieces)))
    assert all(len(chunk) % 2 == 0 and chunk for chunk in out)
    assert b"".join(out) == bytes(range(1, 11))  # the dangling 11th byte is not half a sample


def test_http_stream_yields_aligned_pcm_with_resolved_settings(monkeypatch):
    cfg = config()
    cfg["tts"]["fish-audio"].update(temperature=0.5, sample_rate=44100, opus_bitrate=32000, latency="low")
    monkeypatch.setattr(settings, "_config", lambda: cfg)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.fish.audio/v1/tts").mock(return_value=httpx.Response(
            200, content=iter([b"\x01", b"\x02\x03", b"\x04\x05\x06\x07"]), headers={"content-type": "audio/pcm"}))
        chunks = speak("[excited] Hello there.")
        request = route.calls.last.request
    assert chunks == [b"\x01\x02", b"\x03\x04\x05\x06"]
    assert request.headers["authorization"] == f"Bearer {KEY}" and request.headers["model"] == "s2.1-pro"
    body = json.loads(request.content)
    assert body == {"text": "[excited] Hello there.", "reference_id": VOICE, "format": "pcm", "sample_rate": 24000,
                    "latency": "low", "temperature": 0.5, "prosody": {"speed": 1.0}}
    assert streaming.SAMPLE_RATE == tts.FishAudioTTSProvider.stream_sample_rate == 24000


@pytest.mark.parametrize("model,expected", [("s2.1-pro-free", "s2.1-pro"), (None, "s2.1-pro")])
def test_stream_uses_the_one_resolver_and_allow_free_model_false(monkeypatch, model, expected):
    cfg = config(allow_free_model=False)
    cfg["tts"]["fish-audio"]["model"] = model
    monkeypatch.setattr(settings, "_config", lambda: cfg)
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: pytest.fail("no wallet call when free is off"))
    calls = []
    resolve = settings.resolve_model
    monkeypatch.setattr(settings, "resolve_model", lambda *a, **k: calls.append(a) or resolve(*a, **k))
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.fish.audio/v1/tts").respond(content=b"\x00\x00")
        speak()
    assert route.calls.last.request.headers["model"] == expected and len(calls) == 1


def test_s1_model_adapts_tags_and_empty_result_makes_no_request(monkeypatch):
    cfg = config()
    cfg["tts"]["fish-audio"]["model"] = "s1"
    monkeypatch.setattr(settings, "_config", lambda: cfg)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://api.fish.audio/v1/tts").respond(content=b"\x00\x00")
        assert speak("[not a cue]") == [] and not route.calls
        speak("[excited] Hi there.")
        assert json.loads(route.calls.last.request.content)["text"] == "(excited) Hi there."


def test_http_errors_are_mapped_redacted_and_recorded():
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.fish.audio/v1/tts").respond(
            401, json={"status": 401, "message": f"Invalid Token {KEY}"}, headers={"x-fish-error-code": "invalid_api_key"})
        with pytest.raises(FishAudioError) as exc:
            speak()
    assert exc.value.kind == "credential" and KEY not in str(exc.value)
    assert state.last_failure()[1] == "credential"
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.fish.audio/v1/tts").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(FishAudioError) as exc:
            speak()
        assert exc.value.kind == "availability" and route.call_count == 1  # never retried: no double bill


def test_missing_key_raises_setup_message(monkeypatch):
    monkeypatch.setattr(streaming, "fish_api_key", lambda: "")
    with respx.mock(assert_all_called=False) as mock:
        with pytest.raises(FishAudioError, match="hermes tools"):
            speak()
        assert not mock.calls
    assert state.last_failure()[1] == "credential"


def test_consumer_close_closes_the_http_response():
    closed = threading.Event()

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            yield b"\x01\x02"
            yield b"\x03\x04"

        def close(self):
            closed.set()

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.fish.audio/v1/tts").mock(return_value=httpx.Response(200, stream=Body()))
        stream = streaming.stream_pcm("Hello there.")
        assert next(stream) == b"\x01\x02"
        stream.close()  # barge-in
    assert closed.is_set()


def test_streaming_available_is_offline_and_checks_key_setting_and_host(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("streaming_available() must not open sockets")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS", raising=False)
    provider = tts.FishAudioTTSProvider()
    assert streaming.streaming_available() is True and provider.streams_pcm is True
    for value in ("off", False):
        monkeypatch.setattr(settings, "_config", lambda value=value: config(streaming=value))
        assert streaming.streaming_available() is False and provider.streams_pcm is False
    monkeypatch.setattr(settings, "_config", lambda: config(streaming="auto"))
    monkeypatch.setattr(streaming, "fish_api_key", lambda: "")
    assert streaming.streaming_available() is False
    monkeypatch.setattr(streaming, "fish_api_key", lambda: KEY)
    monkeypatch.setenv("HERMES_PLUGIN_HOST_PROCESS", "1")
    assert streaming.streaming_available() is False
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS")
    def broken():
        raise RuntimeError("secret scope failure")
    monkeypatch.setattr(streaming, "fish_api_key", broken)
    assert streaming.streaming_available() is False


class LiveServer:
    """Loopback /v1/tts/live: records headers and msgpack frames, answers with scripted events."""

    def __init__(self, events, reject=None):
        from websockets.sync.server import serve
        self.events, self.reject, self.frames, self.headers, self.paths = events, reject, [], [], []
        self.closed = threading.Event()
        self.server = serve(self.handle, "127.0.0.1", 0, process_request=self.process)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.socket.getsockname()[1]}"

    def process(self, connection, request):
        self.headers.append(request.headers)
        self.paths.append(request.path)
        if self.reject:
            response = connection.respond(self.reject, "Invalid Token")
            response.headers["x-fish-error-code"] = "invalid_api_key"
            return response
        return None

    def handle(self, connection):
        try:
            while True:
                frame = msgpack.unpackb(connection.recv(timeout=5), raw=False)
                self.frames.append(frame)
                if frame["event"] == "stop":
                    break
            for event in self.events:
                if event == "pause":
                    time.sleep(3)
                    continue
                connection.send(msgpack.packb(event, use_bin_type=True))
            connection.recv(timeout=5)
        except Exception:
            pass
        finally:
            self.closed.set()

    def shutdown(self):
        self.server.shutdown()


@pytest.fixture
def live(monkeypatch):
    servers = []

    def start(events, reject=None):
        server = LiveServer(events, reject)
        servers.append(server)
        monkeypatch.setattr(settings, "_config", lambda: config("ws", server.base_url))
        return server
    yield start
    for server in servers:
        server.shutdown()


def test_ws_transport_session_and_aligned_pcm(live):
    server = live([{"event": "audio", "audio": b"\x01\x02\x03", "time": 80.0},
                   {"event": "audio", "audio": b"\x04", "time": 90.0},
                   {"event": "audio", "audio": b"\x05\x06\x07", "time": 95.0},
                   {"event": "finish", "reason": "stop", "time": 99.0}])
    chunks = speak("Hello there.")
    assert chunks == [b"\x01\x02", b"\x03\x04", b"\x05\x06"]
    assert server.paths == ["/v1/tts/live"]
    headers = server.headers[0]
    assert headers["Authorization"] == f"Bearer {KEY}" and headers["model"] == "s2.1-pro"
    assert headers["User-Agent"].startswith("fish-audio-hermes/")
    start, text, flush, stop = server.frames
    assert start == {"event": "start", "request": {"text": "", "reference_id": VOICE, "format": "pcm",
                     "sample_rate": 24000, "latency": "balanced", "prosody": {"speed": 1.0}}}
    assert text == {"event": "text", "text": "Hello there."}
    assert (flush, stop) == ({"event": "flush"}, {"event": "stop"})
    assert server.closed.wait(5)


@pytest.mark.parametrize("events", [[{"event": "error", "error": f"boom {KEY}"}],
                                    [{"event": "finish", "reason": "error"}]])
def test_ws_error_events_raise_mapped_errors(live, events):
    live(events)
    with pytest.raises(FishAudioError) as exc:
        speak()
    assert exc.value.kind == "availability" and KEY not in str(exc.value)
    assert state.last_failure()[1] == "availability"


def test_ws_error_event_status_maps_to_kind(live):
    live([{"event": "error", "status": 402, "error": "insufficient balance"}])
    with pytest.raises(FishAudioError) as exc:
        speak()
    assert exc.value.kind == "quota" and exc.value.status == 402


def test_ws_handshake_rejection_maps_status(live):
    live([], reject=401)
    with pytest.raises(FishAudioError) as exc:
        speak()
    assert exc.value.kind == "credential" and KEY not in str(exc.value)


def test_ws_consumer_close_is_prompt(live):
    server = live([{"event": "audio", "audio": b"\x01\x02", "time": 1.0}, "pause",
                   {"event": "finish", "reason": "stop", "time": 2.0}])
    stream = streaming.stream_pcm("Hello there.")
    assert next(stream) == b"\x01\x02"
    started = time.monotonic()
    stream.close()  # barge-in mid-sentence
    assert time.monotonic() - started < 2.5
    assert server.closed.wait(5)


def test_provider_meets_the_hermes_streaming_hook_contract():
    provider = tts.FishAudioTTSProvider()
    assert provider.streams_pcm is True and provider.stream_sample_rate == 24000
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.fish.audio/v1/tts").respond(content=b"\x01\x02\x03")
        assert list(provider.stream("Hello there.", voice=None, model=None, format="pcm")) == [b"\x01\x02"]


def test_hook_stream_forwards_call_voice_and_model(monkeypatch):
    other = "b" * 32
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"provider": "fish-audio"}})
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.fish.audio/v1/tts").respond(content=b"\x01\x02")
        chunks = list(tts.FishAudioTTSProvider().stream("Hi there.", voice=other, model="s1", format="pcm"))
    assert chunks == [b"\x01\x02"]
    request = route.calls.last.request
    assert json.loads(request.content)["reference_id"] == other and request.headers["model"] == "s1"
    # Configured Fish settings still outrank per-call values.
    monkeypatch.setattr(settings, "_config", lambda: config())
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.fish.audio/v1/tts").respond(content=b"\x01\x02")
        list(tts.FishAudioTTSProvider().stream("Hi there.", voice=other, model="s1", format="pcm"))
    request = route.calls.last.request
    assert json.loads(request.content)["reference_id"] == VOICE and request.headers["model"] == "s2.1-pro"


@pytest.mark.parametrize("body", [b"", b"\x01"])
def test_successful_stream_without_a_whole_sample_raises_and_records(body):
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.fish.audio/v1/tts").respond(content=body, headers={"content-type": "audio/pcm"})
        with pytest.raises(FishAudioError, match="no audio") as exc:
            speak()
    assert exc.value.kind == "availability" and state.last_failure()[1] == "availability"


def test_plugin_register_leaves_core_streaming_alone(plugin, fake_ctx, monkeypatch):
    """Catalog rule 9: listed plugins join streaming through the provider hook, never tools.tts_streaming."""
    def _no_network(*args, **kwargs):
        raise AssertionError("register() must not open sockets")

    core = ModuleType("tools.tts_streaming")
    def register(name):
        raise AssertionError("the plugin must not register into tools.tts_streaming")
    core.register, core._REGISTRY = register, {}
    package = ModuleType("tools")
    package.tts_streaming = core
    monkeypatch.setitem(sys.modules, "tools", package)
    monkeypatch.setitem(sys.modules, "tools.tts_streaming", core)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    plugin.register(fake_ctx)
    assert [p.name for p in fake_ctx.tts_providers] == ["fish-audio"]
    assert [p.name for p in fake_ctx.stt_providers] == ["fish-audio"]
    assert core._REGISTRY == {}
    source = "".join(path.read_text(encoding="utf-8") for path in Path(streaming.__file__).parent.glob("*.py"))
    assert "tts_streaming" not in source and "NO_BRIDGE" not in source


def test_operator_streaming_missing_key_presentation(monkeypatch):
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    monkeypatch.setattr(streaming, "fish_api_key", lambda: "")
    with pytest.raises(FishAudioError) as exc:
        speak()
    assert exc.value.kind == "credential"
    assert exc.value.message == "Ask the operator of this agent to finish the Fish Audio setup."
