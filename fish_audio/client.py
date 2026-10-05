"""Lazy shared transport; authentication belongs only to individual requests."""
import base64
import itertools
import json
import random
import re
import threading
import time
from contextlib import contextmanager

import httpx

from .errors import FishAudioError, response_error
from .media import atomic_write
from .models import MODEL_IDS

PLUGIN_VERSION = "0.3.1"
MODEL_HEADER = "model"
_client = None
_lock = threading.Lock()


def _http_client():
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=httpx.Timeout(connect=10, read=60, write=60, pool=10), follow_redirects=False)
        return _client


def _user_agent():
    try:
        from hermes_cli import __version__
    except ImportError:
        __version__ = "unknown"
    return f"fish-audio-hermes/{PLUGIN_VERSION} (hermes-agent/{__version__})"


def request_headers(key, model=None):
    headers = {"Authorization": f"Bearer {key}", "User-Agent": _user_agent()}
    if model is not None:
        headers[MODEL_HEADER] = model
    try:
        from opentelemetry.trace import get_current_span
        context = get_current_span().get_span_context()
        if context.is_valid:
            headers["traceparent"] = f"00-{context.trace_id:032x}-{context.span_id:016x}-{int(context.trace_flags):02x}"
    except Exception:
        pass
    return headers


RESPONSE_CAP = 64 * 1024 * 1024


def _retryable(error):
    return error.status == 429 or error.status is not None and 500 <= error.status < 600


def _billed_retryable(error):
    """Billed POSTs retry only where Fish cannot have done the work: 502/504 come from a proxy that may have
    let the request through, so retrying them could bill twice."""
    return error.status in (429, 500, 503)


def _has_bytes(value):
    if isinstance(value, (bytes, bytearray)):
        return True
    if isinstance(value, dict):
        return any(_has_bytes(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_bytes(v) for v in value)
    return False


def _error_body(response):
    # A single bounded chunk; do not call response.read() on an untrusted body.
    return next(response.iter_bytes(chunk_size=64 * 1024), b"")[:64 * 1024]


class _UploadStream(httpx.SyncByteStream):
    def __init__(self, stream, progress):
        self.stream, self.progress = stream, progress

    def __iter__(self):
        yield from self.stream
        self.progress["sent"] = True

    def close(self):
        self.stream.close()


@contextmanager
def _stream(method, url, progress, **kwargs):
    http = _http_client()
    built = http.build_request(method, url, **kwargs)
    request = httpx.Request(method, built.url, headers=built.headers, extensions=built.extensions,
                            stream=_UploadStream(built.stream, progress))
    response = http.send(request, stream=True)
    try:
        yield response
    finally:
        response.close()


def _transport_retry(exc, progress):
    return not progress.get("sent") or isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


def _tts_body(params):
    return {k: v for k, v in params.items() if k not in {"model", "model_defaulted", "base_url", "streaming"}}


# Base64 audio (4/3 of the 64 MiB audio cap) plus alignment metadata.
SSE_CAP = 2 * RESPONSE_CAP
_LINE_BREAK = re.compile(rb"\r\n|\r|\n")


def _bounded_lines(response, limit):
    """Split a response into lines on CR, LF or CRLF (as SSE allows), failing once more than ``limit`` bytes
    arrive, before any decoding."""
    pending, total, after_cr = [], 0, False
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            raise FishAudioError("too_large", None, None, "Fish Audio timestamp stream exceeds the size cap.")
        if not chunk:
            continue
        if after_cr and chunk[:1] == b"\n":  # the LF of a CRLF split across chunks
            chunk = chunk[1:]
        after_cr = chunk.endswith(b"\r")
        *complete, rest = _LINE_BREAK.split(chunk)
        for piece in complete:
            pending.append(piece)
            yield b"".join(pending).decode("utf-8", "replace")
            pending = []
        pending.append(rest)
    if any(pending):
        yield b"".join(pending).decode("utf-8", "replace")


def _sse_audio(response, events):
    """Decode a with-timestamp SSE body: yield each event's audio and keep the rest in ``events``."""
    data = []
    # A final empty line dispatches an event the server did not terminate.
    for line in itertools.chain(_bounded_lines(response, SSE_CAP), [""]):
        if line.startswith("data:"):
            data.append(line[6:] if line.startswith("data: ") else line[5:])
        elif not line and data:
            payload, data = "\n".join(data), []
            if payload.strip() == "[DONE]":
                return
            try:
                event = json.loads(payload)
                audio = base64.b64decode(event.pop("audio_base64", None) or b"", validate=True)
            except (ValueError, TypeError, AttributeError):
                raise FishAudioError("availability", None, None, "Fish Audio sent an unreadable timestamp stream.") from None
            events.append(event)
            if audio:
                yield audio


def tts_to_file(params, key, base_url, out_path, *, sleep=None, events=None):
    """Whole-file synthesis; with an ``events`` list, the SSE with-timestamp endpoint fills it."""
    if not isinstance(params.get("text"), str) or not params["text"].strip():
        raise ValueError("Nothing to say: the text is empty.")
    model = params.get("model")
    if not isinstance(model, str) or model not in MODEL_IDS:
        raise ValueError("Unknown Fish Audio TTS model.")
    sleep = time.sleep if sleep is None else sleep
    body = _tts_body(params)
    headers = request_headers(key, model)
    endpoint = "/v1/tts" if events is None else "/v1/tts/stream/with-timestamp"
    if _has_bytes(body.get("references")):
        import msgpack
        headers["Content-Type"] = "application/msgpack"
        payload = {"content": msgpack.packb(body, use_bin_type=True)}
    else:
        payload = {"json": body}
    for attempt in range(3):
        written = False
        progress = {}
        response_headers = None
        if events is not None:
            events.clear()
        try:
            with _stream("POST", base_url.rstrip("/") + endpoint, progress, headers=headers, **payload) as response:
                response_headers = response.headers
                if not 200 <= response.status_code < 300:
                    try:
                        error_body = _error_body(response)
                    except httpx.TransportError:
                        # A known 4xx remains nonretryable even if its body disconnects.
                        error_body = b""
                    raise response_error(response.status_code, response.headers, error_body, model, key,
                                         defaulted=params.get("model_defaulted", False))

                def chunks():
                    nonlocal written
                    for chunk in response.iter_bytes() if events is None else _sse_audio(response, events):
                        if chunk:
                            written = True
                            yield chunk

                return atomic_write(out_path, chunks())
        except httpx.TransportError as exc:
            error = response_error(None, response_headers, model=model, key=key)
            retryable = _transport_retry(exc, progress)
        except FishAudioError as exc:
            error = exc
            retryable = _billed_retryable(exc)
        if written or not retryable or attempt == 2:
            raise error from None
        sleep((0.5, 1.0, 2.0)[attempt] + random.uniform(0, 0.1))


def tts_pcm(params, key, base_url):
    """Chunked PCM over one unretried POST /v1/tts: a spoken sentence is never billed twice."""
    model = params["model"]
    response_headers = None
    try:
        with _http_client().stream("POST", base_url.rstrip("/") + "/v1/tts", headers=request_headers(key, model),
                                   json=_tts_body(params)) as response:
            response_headers = response.headers
            if not 200 <= response.status_code < 300:
                try:
                    body = _error_body(response)
                except httpx.TransportError:
                    body = b""
                raise response_error(response.status_code, response.headers, body, model, key,
                                     defaulted=params.get("model_defaulted", False))
            yield from response.iter_bytes()
    except httpx.TransportError:
        raise response_error(None, response_headers, model=model, key=key) from None


def tts_live(params, key, base_url, *, timeout=30):
    """One /v1/tts/live WebSocket session per sentence (start, text, flush, stop); yields each audio event."""
    import msgpack
    from websockets.exceptions import InvalidStatus, WebSocketException
    from websockets.sync.client import connect

    model = params["model"]
    headers = request_headers(key, model)
    agent = headers.pop("User-Agent")
    request = _tts_body(params)
    text, request["text"] = request["text"], ""
    url = re.sub(r"^http", "ws", base_url.rstrip("/")) + "/v1/tts/live"
    socket = None
    try:
        # A short close timeout keeps barge-in (the consumer closing this generator) prompt.
        socket = connect(url, additional_headers=headers, user_agent_header=agent, open_timeout=10,
                         close_timeout=1, max_size=16 * 1024 * 1024)
        for event in ({"event": "start", "request": request}, {"event": "text", "text": text},
                      {"event": "flush"}, {"event": "stop"}):
            socket.send(msgpack.packb(event, use_bin_type=True))
        while True:
            message = msgpack.unpackb(socket.recv(timeout=timeout), raw=False)
            kind = message.get("event")
            if kind == "audio" and isinstance(message.get("audio"), bytes):
                yield message["audio"]
            elif kind == "finish" and message.get("reason") != "error":
                return
            elif kind in {"finish", "error"}:
                status = message.get("status")
                raise response_error(status if type(status) is int else None, model=model, key=key)
    except InvalidStatus as exc:
        raise response_error(exc.response.status_code, exc.response.headers, exc.response.body or b"", model, key,
                             defaulted=params.get("model_defaulted", False)) from None
    except (OSError, TimeoutError, WebSocketException, ValueError, TypeError, AttributeError):
        raise response_error(None, model=model, key=key) from None
    finally:
        if socket is not None:
            socket.close()


def transcribe_audio(audio, filename, mime, fields, *, key, base_url, model, sleep=None, read_timeout=300):
    """Multipart ASR through the same client, with safe errors and bounded retries."""
    if model not in {"transcribe-1-pro", "transcribe-1"}:
        raise ValueError("Unknown Fish Audio ASR model.")
    sleep = time.sleep if sleep is None else sleep
    timeout = httpx.Timeout(connect=10, read=read_timeout, write=60, pool=10)
    for attempt in range(3):
        received = False
        progress = {}
        response_headers = None
        try:
            with _stream("POST", base_url.rstrip("/") + "/v1/asr", progress,
                                       headers=request_headers(key, model), data=fields,
                                       files={"audio": (filename, audio, mime)}, timeout=timeout) as response:
                response_headers = response.headers
                if not 200 <= response.status_code < 300:
                    try:
                        body = _error_body(response)
                    except httpx.TransportError:
                        body = b""
                    raise response_error(response.status_code, response.headers, body, model, key)
                body = bytearray()
                for chunk in response.iter_bytes():
                    if chunk:
                        received = True
                        if len(body) + len(chunk) > RESPONSE_CAP:
                            raise FishAudioError("too_large", None, None, "Fish Audio response exceeds the size cap.")
                        body.extend(chunk)
                try:
                    data = json.loads(body)
                    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                        raise ValueError("invalid ASR response")
                except (ValueError, UnicodeError):
                    raise response_error(response.status_code, response.headers, key=key) from None
                if response.headers.get("x-request-id") and not data.get("request_id"):
                    data["request_id"] = response.headers["x-request-id"]
                return data
        except httpx.TransportError as exc:
            error = response_error(None, response_headers, key=key)
            retryable = _transport_retry(exc, progress)
        except FishAudioError as exc:
            error, retryable = exc, _billed_retryable(exc)
        if received or not retryable or attempt == 2:
            raise error from None
        sleep((0.5, 1.0, 2.0)[attempt] + random.uniform(0, 0.1))


def _json_request(method, path, key, base_url, *, timeout=60, **payload):
    """Only GET is retried: mutations may have succeeded before a disconnect."""
    headers = request_headers(key, "voice-design-1" if path == "/v1/voice-design" else None)
    attempts = 3 if method == "GET" else 1
    for attempt in range(attempts):
        received = False
        response_headers = None
        try:
            limits = httpx.Timeout(connect=10, read=timeout, write=60, pool=10)
            with _http_client().stream(method, base_url.rstrip("/") + path,
                                       headers=headers, timeout=limits, **payload) as response:
                response_headers = response.headers
                if not 200 <= response.status_code < 300:
                    try:
                        body = _error_body(response)
                    except httpx.TransportError:
                        body = b""
                    raise response_error(response.status_code, response.headers, body, key=key)
                if response.status_code == 204:
                    return {}
                body = bytearray()
                for chunk in response.iter_bytes():
                    received = received or bool(chunk)
                    if len(body) + len(chunk) > RESPONSE_CAP:
                        raise FishAudioError("too_large", None, None, "Fish Audio response exceeds the size cap.")
                    body.extend(chunk)
                try:
                    result = json.loads(body) if body else {}
                    if not isinstance(result, dict):
                        raise ValueError()
                    return result
                except (ValueError, UnicodeError):
                    raise response_error(response.status_code, response.headers, key=key) from None
        except httpx.TransportError:
            error = response_error(None, response_headers, key=key)
        except FishAudioError as exc:
            error = exc
        if received or not _retryable(error) and error.status is not None or attempt == attempts - 1:
            raise error from None
        time.sleep((0.5, 1.0)[attempt] + random.uniform(0, 0.1))


def get_json(path, params, key, base_url):
    return _json_request("GET", path, key, base_url, params=params)


def post_multipart(path, data, files, key, base_url, timeout):
    return _json_request("POST", path, key, base_url, timeout=timeout, data=data, files=files)


def post_json(path, body, key, base_url, timeout):
    return _json_request("POST", path, key, base_url, timeout=timeout, json=body)


def patch_form(path, data, key, base_url):
    return _json_request("PATCH", path, key, base_url, data=data)


def patch_multipart(path, data, files, key, base_url):
    return _json_request("PATCH", path, key, base_url, data=data, files=files)


def delete(path, key, base_url):
    return _json_request("DELETE", path, key, base_url)
