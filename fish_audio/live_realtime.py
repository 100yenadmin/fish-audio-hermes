"""Fish native realtime STT; a final result requires every turn and session_done."""
import re
import time

import msgpack
from websockets.exceptions import ConnectionClosed

from .live_stt import FINALIZE_TIMEOUT, _LiveSession, join_text


def _clean(text):
    return " ".join(re.sub(r"<\|speaker:\d+\|>|\[[^\[\]]*\]", " ", text).split())


class FishRealtimeSession(_LiveSession):
    def __init__(self, url, key, user_agent, errors):
        super().__init__(url, key, user_agent, errors)
        self._pending, self._texts, self._draft = bytearray(), {}, None
        self._done = self._degraded = False
        self._noise = 0

    def _send(self, event):
        self._socket.send(msgpack.packb(event, use_bin_type=True))

    def _connect(self):
        from websockets.sync.client import connect
        self._socket = connect(self._url, additional_headers={"Authorization": "Bearer " + self._key,
                               "model": "transcribe-stream-1", "User-Agent": self._agent}, user_agent_header=None,
                               open_timeout=8, close_timeout=1, max_size=1024 * 1024, ping_interval=None)
        self._connected = self._activity = time.monotonic()
        self._watch()
        self._send({"type": "config", "protocol_version": 1,
                    "audio": {"format": "pcm_s16le", "sample_rate": 16000, "channels": 1},
                    "turn_detection": {"mode": "manual"}, "results": {"drafts": True, "diarize": False}})
        deadline = time.monotonic() + 8
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            kind = self._receive(remaining)
            if kind == "session_started":
                return
            if kind == "error":
                raise ValueError("Fish realtime session did not start")

    def push_audio(self, chunk):
        if self._failed or not chunk:
            return
        self._activity = time.monotonic()
        try:
            if self._socket is None:
                if self._connected is not None:
                    raise ValueError("Fish realtime session already closed")
                self._connect()
            self._pending.extend(chunk)
            while len(self._pending) >= 3200:
                self._send({"type": "audio", "data": bytes(self._pending[:3200])})
                del self._pending[:3200]
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            self._fail("auth" if status in (401, 403) else "unavailable")

    def _receive(self, timeout):
        message = self._socket.recv(timeout=timeout)
        try:
            event = msgpack.unpackb(message, raw=False) if isinstance(message, bytes) else None
        except Exception:
            event = None
        if not isinstance(event, dict):
            self._noise += 1
            return None
        kind = event.get("type")
        if kind in ("draft", "committed"):
            index, text = event.get("turn_index"), event.get("text")
            if type(index) is not int or not isinstance(text, str):
                self._fail()  # A known transcript event must never silently lose a turn.
            elif kind == "committed":
                self._texts[index] = _clean(text)
                self._degraded |= event.get("degraded") is True
                if self._draft and self._draft[0] == index:
                    self._draft = None
            elif index not in self._texts:
                self._draft = (index, _clean(text))
        elif kind == "error" and event.get("recoverable") is not True:
            self._fail()
        elif kind == "session_done":
            self._done = True
        return kind

    def _text(self, drafts=False):
        return join_text([self._texts[i] for i in sorted(self._texts)] +
                         ([self._draft[1]] if drafts and self._draft else []))

    def partial_transcript(self):
        try:
            while self._socket is not None and not self._failed and not self._done:
                self._receive(0)
        except TimeoutError:
            pass
        except Exception:
            self._fail()
        return self._text(drafts=True)

    def finalize(self):
        self._activity = time.monotonic()
        deadline = time.monotonic() + FINALIZE_TIMEOUT
        try:
            if self._failed:
                return self._failure(self._failed)
            if self._connected is None:
                return self._failure("no_speech")
            if self._socket is not None:
                if self._pending:
                    self._send({"type": "audio", "data": bytes(self._pending)})
                    self._pending.clear()
                self._send({"type": "audio_done"})
            while self._socket is not None and not self._failed and not self._done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    self._receive(remaining)
                except (TimeoutError, ConnectionClosed):
                    break
            if self._failed:
                return self._failure(self._failed)
            if not self._done:
                return self._failure("timeout")
            if self._degraded:
                return self._failure("unavailable")
            text = self._text()
            return {"success": True, "transcript": text, "provider": "fish-audio"} if text else self._failure("no_speech")
        except Exception:
            return self._failure(self._failed or "unavailable")
        finally:
            self._close()
