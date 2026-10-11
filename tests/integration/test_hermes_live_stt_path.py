"""Real Hermes loader and streaming worker against fake Fish, never the live service."""
import socket
import threading
import pytest
from fish_audio._compat import HAS_STT_STREAM_SEAM
from test_live_stt import LiveServer, REAL_CONNECT, REAL_CREATE, SPEECH, SILENCE

@pytest.mark.skipif(not HAS_STT_STREAM_SEAM, reason="requires Hermes 0.21.6 STT seam")
def test_core_live_stt(installed_fish_home, monkeypatch):
    _, _, provider = installed_fish_home
    from tools.transcription_streaming import open_streaming_session
    module = __import__(type(provider).__module__, fromlist=["transport_settings"])
    server = LiveServer()
    def loopback(sock, address):
        assert address == ("127.0.0.1", server.server.socket.getsockname()[1])
        return REAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback)
    monkeypatch.setattr(socket, "create_connection", REAL_CREATE)
    monkeypatch.setattr(module, "transport_settings", lambda: {"base_url": server.base})
    partials, caption = [], threading.Event()
    session = None
    try:
        session = open_streaming_session(lambda text: (partials.append(text), caption.set()),
            stt_config={"streaming": True, "provider": "fish-audio"})
        assert session is not None
        session.push_audio(SPEECH * 50 + SILENCE * 35)
        assert caption.wait(2) and partials == ["Hello"]
        session.push_audio(SPEECH * 20)
        assert session.finalize()["transcript"] == "Hello world."
    finally:
        if session is not None:
            session._inner._close()
        server.server.shutdown()
