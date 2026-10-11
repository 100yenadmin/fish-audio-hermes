"""Fish compat protocol on loopback only; fake credentials and synthetic PCM."""
import base64
import json
import socket
import threading
import time

import pytest
from fish_audio import live_stt, secrets, settings, stt

KEY = "fake-live-key"
SPEECH, SILENCE = b"\xe8\x03" * 320, bytes(640)
REAL_CONNECT, REAL_CREATE = socket.socket.connect, socket.create_connection
MISSING = object()


class LiveServer:
    def __init__(self, error=None, answers=99, second="world."):
        from websockets.sync.server import serve
        self.frames, self.headers, self.paths = [], [], []
        self.closed = threading.Event()
        self.error, self.answers, self.second = error, answers, second
        self.server = serve(self.handle, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.socket.getsockname()[1]}"

    def handle(self, ws):
        self.headers.append(ws.request.headers)
        self.paths.append(ws.request.path)
        try:
            ws.send(json.dumps({"message_type": "session_started"}))
            if self.error == "input_error":
                ws.send(json.dumps({"message_type": "input_error", "error": KEY}))
            if self.error == "huge":
                ws.send(json.dumps({"message_type": "padding", "text": "x" * 2 ** 21}))
            for message in ws:
                frame = json.loads(message)
                self.frames.append(frame)
                if frame["commit"]:
                    if self.error and self.error not in ("input_error", "huge"):
                        ws.send(json.dumps({"message_type": "transcriber_error", "error": self.error + KEY}))
                    elif self.answers > 0:
                        text = "Hello" if self.answers == 99 else self.second
                        self.answers -= 1
                        answer = {"message_type": "committed_transcript"}
                        if text is not MISSING:
                            answer["text"] = text
                        ws.send(json.dumps(answer))
                        ws.send(json.dumps({"message_type": "committed_transcript_with_timestamps", "text": "ignored"}))
        finally:
            self.closed.set()


@pytest.fixture
def live(monkeypatch):
    servers = []
    monkeypatch.setattr(stt, "fish_api_key", lambda: KEY)
    monkeypatch.setattr(secrets, "fish_api_key", lambda: KEY)
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS", raising=False)
    def start(**kwargs):
        server = LiveServer(**kwargs)
        monkeypatch.setattr(settings, "_config", lambda: {
            "plugins": {"entries": {"fish-audio": {"settings": {"base_url": server.base}}}}})
        session = stt.FishAudioTranscriptionProvider().open_stream_session(language="ja-JP", prompt="ignored")
        servers.append((server, session))
        return server, session
    yield start
    for server, session in servers:
        session._close()
        server.server.shutdown()


def wait_partial(session):
    end = time.monotonic() + 2
    while time.monotonic() < end:
        text = session.partial_transcript()
        if text:
            return text
        time.sleep(.01)
    pytest.fail("no live caption before finalize")


def test_happy_path(live, monkeypatch):
    server, session = live()
    assert not server.headers  # opening is lazy
    def forbidden():
        pytest.fail("worker must use captured scope")
    monkeypatch.setattr(stt, "fish_api_key", forbidden)
    monkeypatch.setattr(secrets, "fish_api_key", forbidden)
    monkeypatch.setattr(settings, "_config", forbidden)
    pcm = SPEECH * 50 + SILENCE * 35
    for offset in range(0, len(pcm), 777):  # arbitrary capture chunk boundaries
        session.push_audio(pcm[offset:offset + 777])
    assert wait_partial(session) == "Hello"
    session.push_audio(SILENCE * 60 + SPEECH * 20)
    assert session.finalize() == {"success": True, "transcript": "Hello world.", "provider": "fish-audio"}
    commits = [frame for frame in server.frames if frame["commit"]]
    assert len(commits) == 2 and all(base64.b64decode(frame["audio_base_64"]) for frame in commits)
    assert all(frame["sample_rate"] == 16000 and frame["message_type"] == "input_audio_chunk" for frame in server.frames)
    assert server.headers[0]["xi-api-key"] == KEY and server.headers[0]["User-Agent"].startswith("fish-audio-hermes/")
    assert server.paths == ["/compat/elevenlabs/v1/speech-to-text/realtime?audio_format=pcm_16000&commit_strategy=manual"]
    assert server.closed.wait(2)


def test_silence_only(live):
    server, session = live()
    session.push_audio(SILENCE * 2300)
    assert session.finalize()["error_kind"] == "no_speech"
    assert server.closed.wait(2) and not server.frames  # silence alone is never streamed


@pytest.mark.parametrize("error,kind", [("Invalid Token", "auth"), ("broken", "unavailable"), ("input_error", None),
        ("huge", "unavailable")])
def test_server_errors(live, caplog, error, kind):
    _, session = live(error=error)
    session.push_audio(SPEECH * 20)
    result = session.finalize()
    assert (result["error_kind"] == kind) if kind else result["success"]
    assert KEY not in str(result) + caplog.text


@pytest.mark.parametrize("answers", [0, 1])
def test_timeout_keeps_received_commits(live, monkeypatch, answers):
    monkeypatch.setattr(live_stt, "FINALIZE_TIMEOUT", .05)
    _, session = live(answers=answers)
    if answers:
        session.push_audio(SPEECH * 50 + SILENCE * 35)
        assert wait_partial(session) == "world."
    session.push_audio(SPEECH * 20)
    started = time.monotonic()
    result = session.finalize()
    assert time.monotonic() - started < 1.5
    assert result["success"] if answers else result["error_kind"] == "timeout"


def test_connect_refused(live):
    server, session = live()
    server.server.shutdown()
    session.push_audio(SPEECH * 20)
    session.push_audio(SPEECH * 20)
    assert session.finalize()["error_kind"] == "unavailable" and not server.headers


@pytest.mark.parametrize("limit", ["IDLE_TIMEOUT", "MAX_SESSION_SECONDS"])
def test_watchdog(live, monkeypatch, limit):
    monkeypatch.setattr(live_stt, limit, .05)
    server, session = live()
    session.push_audio(SPEECH)
    assert server.closed.wait(1)
    session.push_audio(SPEECH)  # expiry never reconnects
    assert len(server.headers) == 1


def test_polling_is_not_activity(live, monkeypatch):
    monkeypatch.setattr(live_stt, "IDLE_TIMEOUT", .2)
    server, session = live()
    session.push_audio(SPEECH)
    end = time.monotonic() + 2
    while not server.closed.is_set() and time.monotonic() < end:
        session.partial_transcript()  # Hermes polls even when no audio arrives
        time.sleep(.02)
    assert server.closed.is_set()


def test_pauses_are_not_streamed(live):
    server, session = live()
    session.push_audio(SILENCE * 500 + SPEECH * 50 + SILENCE * 35)  # a 10 s pause before the first phrase
    assert wait_partial(session) == "Hello"
    session.push_audio(SILENCE * 1000 + SPEECH * 20)  # a 20 s pause before the last one
    assert session.finalize()["transcript"] == "Hello world."
    sent = sum(len(base64.b64decode(frame["audio_base_64"])) for frame in server.frames)
    lead_in = live_stt.LEAD_IN_FRAMES
    assert sent == (lead_in + 50 + 35 + lead_in + 20) * live_stt.FRAME_BYTES


@pytest.mark.parametrize("base,url", [("https://api.fish.audio", "wss://api.fish.audio/"),
        ("http://127.0.0.1:9", "ws://127.0.0.1:9/"), ("https://localhost:8443", "wss://localhost:8443/")])
def test_live_url_scheme(monkeypatch, base, url):
    monkeypatch.setattr(stt, "transport_settings", lambda: {"base_url": base})
    monkeypatch.setattr(stt, "fish_api_key", lambda: KEY)
    assert stt.FishAudioTranscriptionProvider().open_stream_session()._url.startswith(url + "compat/")


def test_capability_matrix(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("capability is offline"))
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS", raising=False)
    for seam, host, key, isolated, expected in [(True, "api.fish.audio", KEY, "0", True),
            (True, "localhost", KEY, "0", True), (True, "127.0.0.2", KEY, "0", True),
            (True, "self.example", KEY, "0", False),
            (True, "api.fish.audio", "", "0", False), (True, "api.fish.audio", KEY, "1", False),
            (False, "api.fish.audio", KEY, "0", False)]:
        monkeypatch.setattr(stt, "HAS_STT_STREAM_SEAM", seam)
        monkeypatch.setattr(stt, "transport_settings", lambda: {"base_url": "https://" + host})
        monkeypatch.setattr(stt, "fish_api_key", lambda: key)
        monkeypatch.setenv("HERMES_PLUGIN_HOST_PROCESS", isolated)
        assert stt.FishAudioTranscriptionProvider().streaming_capable is expected


@pytest.mark.parametrize("tail,commits,transcript", [(10, 2, "Hello world."), (3, 1, "Hello")])
def test_short_last_word_after_a_phrase(live, tail, commits, transcript):
    server, session = live()
    session.push_audio(SPEECH * 50 + SILENCE * 35)
    assert wait_partial(session) == "Hello"
    session.push_audio(SILENCE * 20 + SPEECH * tail)  # a 200 ms "No." is kept; a 60 ms click is not
    assert session.finalize()["transcript"] == transcript
    assert sum(frame["commit"] for frame in server.frames) == commits


@pytest.mark.parametrize("second", [None, 123, MISSING])
def test_malformed_transcript_falls_back(live, second):
    _, session = live(second=second)
    session.push_audio(SPEECH * 50 + SILENCE * 35)
    assert wait_partial(session) == "Hello"
    session.push_audio(SPEECH * 20)
    assert session.finalize()["error_kind"] == "unavailable"  # Hermes then transcribes the whole file


def test_long_pause_never_splits_a_word():
    detector = live_stt.PauseDetector()
    assert not any(detector.feed(SILENCE) for _ in range(1600))  # 32 s pause before speaking
    words = [SPEECH] * 30 + [SILENCE] * 2 + [SPEECH] * 30  # a 40 ms gap between words
    assert not any(detector.feed(frame) for frame in words)
    assert any(detector.feed(SILENCE) for _ in range(35))


def test_join_and_long_guard():
    for segments, expected in [(["Hello", "world"], "Hello world"), (["漢", "字"], "漢字"),
            (["a", "かな"], "aかな"), (["한", "a"], "한a"), (["a。", "b"], "a。b"), (["a", "Ａ"], "a Ａ"), (["a", "𛀁"], "a𛀁")]:
        assert live_stt.join_text(segments) == expected
    for forced in (False, True):
        detector = live_stt.PauseDetector()
        assert not any(detector.feed(SPEECH) for _ in range(1499))
        if forced:
            assert not any(detector.feed(SPEECH) for _ in range(750))
        assert detector.feed(SPEECH if forced else SILENCE)
