"""Single-worker Fish compat STT; only the abandonment watchdog closes from another thread."""
import base64
import contextvars
import json
import logging
import re
import struct
import threading
import time

from ._compat import TranscriptionStreamSession

FRAME_BYTES = 640  # 20 ms, 320 little-endian int16 samples at 16 kHz
SILENCE_RMS_THRESHOLD = 200  # Hermes voice_mode scale; no core import
FINALIZE_TIMEOUT = 15
IDLE_TIMEOUT = 30
MAX_SESSION_SECONDS = 3500
logger = logging.getLogger(__name__)
_CJK = re.compile(r"[\u2e80-\u30ff\u31f0-\u31ff\u3400-\u9fff\uac00-\ud7af\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\ud7b0-\ud7ff\uf900-\ufaff"
                  r"\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uffdc\U0001aff0-\U0001afff\U0001b000-\U0001b16f\U00020000-\U000323af]")


def join_text(segments):
    text = ""
    for segment in segments:
        segment = segment.strip()
        if segment:
            space = " " if text and not (_CJK.match(text[-1]) or _CJK.match(segment[0])) else ""
            text += space + segment
    return text


class PauseDetector:
    def __init__(self):
        self.frames = self.speech = self.silence = 0

    @property
    def has_speech(self):
        return self.speech >= 15

    def feed(self, frame):
        samples = struct.unpack("<320h", frame)
        speaking = sum(value * value for value in samples) >= 320 * SILENCE_RMS_THRESHOLD ** 2
        self.frames += 1
        self.speech += speaking
        self.silence = 0 if speaking else self.silence + 1
        commit = self.has_speech and ((self.speech >= 50 and self.silence >= 35)
                                     or (self.frames >= 1500 and not speaking) or self.frames >= 2250)
        if commit:
            self.frames = self.speech = self.silence = 0
        return commit


class FishLiveSession(TranscriptionStreamSession):
    def __init__(self, url, key, user_agent, errors):
        self._url, self._key, self._agent, self._errors = url, key, user_agent, errors
        self._socket = self._timer = None
        self._failed = None
        self._detector = PauseDetector()
        self._pending, self._held, self._texts = bytearray(), bytearray(), []
        self._commits = self._received = 0
        self._activity, self._connected = time.monotonic(), None

    def _watch(self):
        if self._socket is None:
            return
        remaining = min(self._activity + IDLE_TIMEOUT, self._connected + MAX_SESSION_SECONDS) - time.monotonic()
        if remaining <= 0:
            self._close()
        else:
            self._timer = threading.Timer(remaining, contextvars.copy_context().run, args=(self._watch,))
            self._timer.daemon = True
            self._timer.start()

    def _close(self):
        socket, self._socket = self._socket, None
        if self._timer is not None:
            self._timer.cancel()
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass

    def _fail(self, kind="unavailable"):
        self._failed = kind
        self._close()

    def _connect(self):
        from websockets.sync.client import connect
        self._socket = connect(self._url, additional_headers={"xi-api-key": self._key, "User-Agent": self._agent},
                               user_agent_header=None, open_timeout=8, close_timeout=1, max_size=None)
        self._connected = self._activity = time.monotonic()
        self._watch()
        event = json.loads(self._socket.recv(timeout=8))
        if event.get("message_type") != "session_started":
            raise ValueError("Fish live session did not start")

    def _send(self, audio, commit):
        self._socket.send(json.dumps({"message_type": "input_audio_chunk",
                                      "audio_base_64": base64.b64encode(audio).decode(),
                                      "commit": commit, "sample_rate": 16000}))
        self._commits += bool(commit)

    def push_audio(self, chunk):
        self._activity = time.monotonic()
        if self._failed or not chunk:
            return
        try:
            if self._socket is None:
                if self._connected is not None:
                    raise ValueError("Fish live session already closed")
                self._connect()
            self._pending.extend(chunk)
            while len(self._pending) >= FRAME_BYTES:
                frame = bytes(self._pending[:FRAME_BYTES])
                del self._pending[:FRAME_BYTES]
                self._held.extend(frame)
                if self._detector.feed(frame):
                    self._send(self._held, True)
                    self._held.clear()
                elif len(self._held) >= 6 * FRAME_BYTES:
                    self._send(self._held[:-FRAME_BYTES], False)
                    del self._held[:-FRAME_BYTES]  # always retain real audio for the final commit
        except Exception:
            self._fail()

    def _receive(self, timeout):
        event = json.loads(self._socket.recv(timeout=timeout))
        kind = event.get("message_type")
        if kind == "committed_transcript":
            self._texts.append(event["text"])
            self._received += 1
        elif kind == "transcriber_error":
            self._fail("auth" if "Invalid Token" in str(event.get("error", "")) else "unavailable")
        elif kind == "input_error":
            logger.debug("Fish live input error; continuing.")  # never log server-controlled text

    def partial_transcript(self):
        self._activity = time.monotonic()
        try:
            while self._socket is not None and not self._failed:
                self._receive(0)
        except TimeoutError:
            pass
        except Exception:
            self._fail()
        return join_text(self._texts)

    def _failure(self, kind):
        return {"success": False, "transcript": "", "provider": "fish-audio",
                "error": self._errors[kind], "error_kind": kind}

    def finalize(self):
        self._activity = time.monotonic()
        deadline = time.monotonic() + FINALIZE_TIMEOUT
        try:
            if not self._failed and self._detector.has_speech:
                self._held.extend(self._pending)
                self._send(self._held, True)
            while not self._failed and self._received < self._commits:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    self._receive(remaining)
                except TimeoutError:
                    break
            if self._failed:
                return self._failure(self._failed)
            text = join_text(self._texts)
            if text:
                return {"success": True, "transcript": text, "provider": "fish-audio"}
            return self._failure("timeout" if self._received < self._commits else "no_speech")
        except Exception:
            return self._failure("unavailable")
        finally:
            self._close()

    def __del__(self):
        self._close()
