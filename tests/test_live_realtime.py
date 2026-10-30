"""Native MessagePack protocol on loopback, with synthetic PCM and credentials."""
import socket
import threading
import time

import msgpack
import pytest
from websockets.exceptions import ConnectionClosed

from fish_audio import live_realtime, live_stt, stt
from test_live_stt import KEY, REAL_CONNECT, REAL_CREATE


class RealtimeServer:
    def __init__(self, audio_events=(), final=None, reject=None, close=None, start=None):
        from websockets.sync.server import serve
        self.frames, self.headers, self.paths = [], [], []
        self.closed = threading.Event()
        self.audio_events, self.close, self.start = list(audio_events), close, start
        self.final = final if final is not None else [self.commit(0, "Hello"), {"type": "session_done"}]
        self.seq = 0
        def process_request(connection, request):
            return connection.respond(reject, "Refused") if reject else None
        self.server = serve(self.handle, "127.0.0.1", 0, process_request=process_request)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.socket.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"

    @staticmethod
    def commit(index, text, **extra):
        return dict(type="committed", turn_index=index, text=text, degraded=False, **extra)

    def send(self, ws, events):
        for event in events:
            self.seq += 1
            ws.send(msgpack.packb({**event, "seq": self.seq}, use_bin_type=True) if isinstance(event, dict) else event)

    def handle(self, ws):
        self.headers.append(ws.request.headers)
        self.paths.append(ws.request.path)
        try:
            self.frames.append(msgpack.unpackb(ws.recv(), raw=False))
            self.send(ws, self.start or [{"type": "session_started"}])
            for message in ws:
                frame = msgpack.unpackb(message, raw=False)
                self.frames.append(frame)
                if frame["type"] == "audio" and self.audio_events:
                    self.send(ws, self.audio_events.pop(0))
                if frame["type"] == "audio_done":
                    self.send(ws, self.final)
                    if self.close:
                        ws.close(self.close)
        except ConnectionClosed:
            pass
        finally:
            self.closed.set()


@pytest.fixture
def realtime(monkeypatch):
    servers = []
    def loopback(sock, address):
        assert address[0] == "127.0.0.1" and address[1] in [s.port for s, _ in servers]
        return REAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback)
    monkeypatch.setattr(socket, "create_connection", REAL_CREATE)
    monkeypatch.setattr(stt, "fish_api_key", lambda: KEY)
    def start(**kwargs):
        server = RealtimeServer(**kwargs)
        monkeypatch.setattr(stt, "transport_settings", lambda: {"base_url": server.base})
        session = stt.FishAudioTranscriptionProvider().open_stream_session(language="ja", prompt="ignored")
        servers.append((server, session))
        return server, session
    yield start
    for server, session in servers:
        session._close()
        server.server.shutdown()


def caption(session, expected):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if session.partial_transcript() == expected:
            return
        time.sleep(.005)
    pytest.fail("expected caption did not arrive")


@pytest.mark.parametrize("first,second,joined", [("Hello", "world", "Hello world"), ("漢", "字", "漢字")])
def test_happy_path(realtime, first, second, joined):
    draft = lambda text: {"type": "draft", "turn_index": 0, "text": text}
    server, session = realtime(audio_events=[[draft("old")], [draft(first)],
        [RealtimeServer.commit(0, first), draft("ignored"), {"type": "draft", "turn_index": 1, "text": "replace me"}]],
        final=[RealtimeServer.commit(1, second), {"type": "session_done"}])
    session.push_audio(b"")
    assert not server.headers
    session.push_audio(bytes(3200))
    caption(session, "old")
    session.push_audio(bytes(3200))
    caption(session, first)
    for offset in range(0, 3217, 777):
        session.push_audio(bytes(min(777, 3217 - offset)))
    caption(session, live_stt.join_text([first, "replace me"]))
    assert session.finalize() == {"success": True, "transcript": joined, "provider": "fish-audio"}
    assert server.closed.wait(1)
    assert server.frames[0] == {"type": "config", "protocol_version": 1,
        "audio": {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1},
        "turn_detection": {"mode": "manual"}, "results": {"drafts": True, "diarize": False}}
    assert server.frames[1:] == [{"type": "audio", "data": bytes(n)} for n in (3200, 3200, 3200, 17)] + [{"type": "audio_done"}]
    assert server.headers[0]["Authorization"] == "Bearer " + KEY
    assert server.headers[0]["model"] == "transcribe-stream-1"
    assert server.headers[0]["User-Agent"].startswith("fish-audio-hermes/")
    assert server.paths == ["/v1/asr/realtime"]


def test_clean_and_late_turns(realtime):
    server, session = realtime(audio_events=[[{"type": "draft", "turn_index": 0, "text": "<|speaker:1|> [pause] Hello"}]],
        final=[RealtimeServer.commit(2, "[laughter]"), RealtimeServer.commit(1, "world [pause]", user_turn=False, turn_kind="other"),
               RealtimeServer.commit(0, "<|speaker:2|> Hello\n"), {"type": "session_done"}])
    session.push_audio(bytes(3200))
    caption(session, "Hello")
    assert session.finalize()["transcript"] == "Hello world"
    assert server.closed.wait(1)


@pytest.mark.parametrize("mode,kind", [("timeout", "timeout"), ("close", "timeout"), ("degraded", "unavailable"), ("error", "unavailable")])
def test_incomplete(realtime, monkeypatch, mode, kind):
    monkeypatch.setattr(live_realtime, "FINALIZE_TIMEOUT", .05)
    final = [] if mode in ("timeout", "close") else [{"type": "session_done"}]
    if mode == "degraded":
        final.insert(0, {**RealtimeServer.commit(1, "lossy"), "degraded": True})
    if mode == "error":
        final.insert(0, {"type": "error", "recoverable": False, "message": KEY})
    server, session = realtime(audio_events=[[RealtimeServer.commit(0, "partial")]], final=final, close=1012 if mode == "close" else None)
    session.push_audio(bytes(3200))
    caption(session, "partial")
    result = session.finalize()
    assert result["success"] is False and result["transcript"] == "" and result["error_kind"] == kind
    assert server.closed.wait(1)


def test_noise_and_recoverable_error(realtime, caplog):
    server, session = realtime(audio_events=[["server-private-text", b"\xc1", msgpack.packb([]),
        {"type": "unknown", "message": KEY}, {"type": "error", "recoverable": True, "message": "server-private-text"}]])
    session.push_audio(bytes(3200))
    assert session.finalize()["success"]
    assert session._noise == 3
    assert KEY not in caplog.text and "server-private-text" not in caplog.text


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (402, "unavailable"), (429, "unavailable"), (503, "unavailable")])
def test_upgrade_refusal(realtime, caplog, status, kind):
    _, session = realtime(reject=status)
    session.push_audio(b"\0\0")
    result = session.finalize()
    assert result["error_kind"] == kind and not result["success"]
    assert KEY not in caplog.text + str(result)


@pytest.mark.parametrize("audio", [False, True])
def test_no_speech(realtime, audio):
    server, session = realtime(final=[{"type": "session_done"}])
    session.push_audio(bytes(3200) if audio else b"")
    assert session.finalize()["error_kind"] == "no_speech"
    assert bool(server.headers) is audio
    if audio:
        assert server.closed.wait(1)


def test_error_before_started(realtime):
    server, session = realtime(start=[{"type": "error", "recoverable": True, "message": KEY}])
    session.push_audio(bytes(3200))
    assert session.finalize()["error_kind"] == "unavailable"
    assert server.closed.wait(1)


@pytest.mark.parametrize("limit", ["IDLE_TIMEOUT", "MAX_SESSION_SECONDS"])
def test_watchdog(realtime, monkeypatch, limit):
    monkeypatch.setattr(live_stt, limit, .05)
    server, session = realtime()
    session.push_audio(b"\0\0")
    deadline = time.monotonic() + 1
    while not server.closed.is_set() and time.monotonic() < deadline:
        session.partial_transcript()
        time.sleep(.005)
    assert server.closed.is_set()
    session.push_audio(b"\0\0")
    assert not session.finalize()["success"] and len(server.headers) == 1


@pytest.mark.parametrize("base,url", [("https://api.fish.audio", "wss://api.fish.audio"),
    ("http://127.0.0.1:9", "ws://127.0.0.1:9"), ("https://localhost:8443", "wss://localhost:8443"),
    ("http://localhost:8000/fish/", "ws://localhost:8000/fish")])
@pytest.mark.parametrize("setting", [None, "unknown", "compat"])
def test_selection(monkeypatch, base, url, setting):
    monkeypatch.setattr(stt, "fish_api_key", lambda: KEY)
    monkeypatch.setattr(stt, "transport_settings", lambda: {"base_url": base, "live_transcription": setting})
    session = stt.FishAudioTranscriptionProvider().open_stream_session()
    assert isinstance(session, live_stt.FishLiveSession if setting == "compat" else live_realtime.FishRealtimeSession)
    assert session._url == url + ("/compat/elevenlabs/v1/speech-to-text/realtime?audio_format=pcm_16000&commit_strategy=manual" if setting == "compat" else "/v1/asr/realtime")
