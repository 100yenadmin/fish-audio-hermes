"""Streaming bridge: FishStreamer over mocked HTTP and a loopback WebSocket, plus bridge registration."""
import json
import socket
import sys
import threading
import time
from types import ModuleType

import httpx
import msgpack
import pytest
import respx

from fish_audio import registration, settings, state, streaming, tts
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
    return list(streaming.FishStreamer({}, {}).stream(text))


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
    streamer = streaming.FishStreamer({}, {})
    assert (streamer.sample_rate, streamer.channels, streamer.sample_width) == (24000, 1, 2)


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
        stream = streaming.FishStreamer({}, {}).stream("Hello there.")
        assert next(stream) == b"\x01\x02"
        stream.close()  # barge-in
    assert closed.is_set()


@pytest.fixture
def fake_registry(monkeypatch):
    provider = tts.FishAudioTTSProvider()
    module = ModuleType("agent.tts_registry")
    module.get_provider = lambda name: provider if name == "fish-audio" else None
    monkeypatch.setitem(sys.modules, "agent", ModuleType("agent"))
    monkeypatch.setitem(sys.modules, "agent.tts_registry", module)
    return module


def test_available_is_offline_and_checks_registry_key_and_setting(monkeypatch, fake_registry):
    def no_network(*args, **kwargs):
        raise AssertionError("available() must not open sockets")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert streaming.FishStreamer.available() is True
    for value in ("off", False):
        monkeypatch.setattr(settings, "_config", lambda value=value: config(streaming=value))
        assert streaming.FishStreamer.available() is False
    monkeypatch.setattr(settings, "_config", lambda: config(streaming="auto"))
    assert streaming.FishStreamer.available() is True
    monkeypatch.setattr(streaming, "fish_api_key", lambda: "")
    assert streaming.FishStreamer.available() is False
    monkeypatch.setattr(streaming, "fish_api_key", lambda: KEY)
    fake_registry.get_provider = lambda name: None
    assert streaming.FishStreamer.available() is False
    fake_registry.get_provider = lambda name: object()  # another plugin's provider under our name
    assert streaming.FishStreamer.available() is False
    def broken(name):
        raise RuntimeError("registry failure")
    fake_registry.get_provider = broken
    assert streaming.FishStreamer.available() is False
    monkeypatch.setitem(sys.modules, "agent.tts_registry", None)
    assert streaming.FishStreamer.available() is False


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
    stream = streaming.FishStreamer({}, {}).stream("Hello there.")
    assert next(stream) == b"\x01\x02"
    started = time.monotonic()
    stream.close()  # barge-in mid-sentence
    assert time.monotonic() - started < 2.5
    assert server.closed.wait(5)


@pytest.fixture
def hermes_streaming(monkeypatch):
    """A fake ``tools.tts_streaming`` exposing only the public ``register`` call."""
    module = ModuleType("tools.tts_streaming")
    module._REGISTRY = {}

    def register(name):
        def wrap(cls):
            module._REGISTRY[name] = cls
            return cls
        return wrap
    module.register = register
    package = ModuleType("tools")
    package.tts_streaming = module
    monkeypatch.setitem(sys.modules, "tools", package)
    monkeypatch.setitem(sys.modules, "tools.tts_streaming", module)
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS", raising=False)
    monkeypatch.delenv("FISH_AUDIO_HERMES_NO_BRIDGE", raising=False)
    monkeypatch.setattr(tts.FishAudioTTSProvider, "pcm_seam", False)
    return module


def test_bridge_registers_by_call_and_is_idempotent(hermes_streaming):
    registration._register_streaming()
    registration._register_streaming()
    assert hermes_streaming._REGISTRY == {"fish-audio": streaming.FishStreamer}
    assert not tts.FishAudioTTSProvider.pcm_seam


def test_bridge_skips_when_the_seam_has_landed(hermes_streaming, fake_registry):
    hermes_streaming._plugin_streamer = lambda name, cfg: None
    registration._register_streaming()
    assert hermes_streaming._REGISTRY == {}
    provider = tts.FishAudioTTSProvider()
    assert provider.pcm_seam and provider.streams_pcm and provider.stream_sample_rate == 24000
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://api.fish.audio/v1/tts").respond(content=b"\x01\x02\x03")
        assert list(provider.stream("Hello there.", voice=None, model=None, format="pcm")) == [b"\x01\x02"]


@pytest.mark.parametrize("env", ["HERMES_PLUGIN_HOST_PROCESS", "FISH_AUDIO_HERMES_NO_BRIDGE"])
def test_bridge_skips_in_host_process_and_with_the_build_switch(hermes_streaming, monkeypatch, env):
    monkeypatch.setenv(env, "1")
    registration._register_streaming()
    assert hermes_streaming._REGISTRY == {} and not tts.FishAudioTTSProvider.pcm_seam


def test_bridge_skips_without_module_and_never_raises(hermes_streaming, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "tools.tts_streaming", None)
    monkeypatch.delattr(sys.modules["tools"], "tts_streaming")
    registration._register_streaming()
    def broken(name):
        raise RuntimeError("registry is read-only")
    hermes_streaming.register = broken
    monkeypatch.setitem(sys.modules, "tools.tts_streaming", hermes_streaming)
    monkeypatch.setattr(sys.modules["tools"], "tts_streaming", hermes_streaming, raising=False)
    registration._register_streaming()
    assert "streaming voice unavailable" in caplog.text


def test_streams_pcm_is_off_without_the_seam(fake_registry, monkeypatch):
    monkeypatch.setattr(tts.FishAudioTTSProvider, "pcm_seam", False)
    assert tts.FishAudioTTSProvider().streams_pcm is False
