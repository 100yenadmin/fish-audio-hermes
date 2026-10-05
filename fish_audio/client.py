"""Lazy shared transport; authentication belongs only to individual requests."""
import random
import threading
import time

import httpx
import msgpack

from .errors import FishAudioError, response_error
from .media import atomic_write

PLUGIN_VERSION = "0.0.1"
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


def tts_to_file(params, key, base_url, out_path, *, sleep=None):
    sleep = time.sleep if sleep is None else sleep
    body = {k: v for k, v in params.items() if k not in {"model", "base_url", "streaming"}}
    model = params["model"]
    headers = {"Authorization": f"Bearer {key}", MODEL_HEADER: model, "User-Agent": _user_agent()}
    if _has_bytes(body.get("references")):
        headers["Content-Type"] = "application/msgpack"
        payload = {"content": msgpack.packb(body, use_bin_type=True)}
    else:
        payload = {"json": body}
    for attempt in range(3):
        written = False
        try:
            with _http_client().stream("POST", base_url.rstrip("/") + "/v1/tts", headers=headers, **payload) as response:
                if not 200 <= response.status_code < 300:
                    try:
                        error_body = _error_body(response)
                    except httpx.TransportError:
                        # A known 4xx remains nonretryable even if its body disconnects.
                        error_body = b""
                    raise response_error(response.status_code, response.headers, error_body, model, key)

                def chunks():
                    nonlocal written
                    for chunk in response.iter_bytes():
                        if chunk:
                            written = True
                            yield chunk

                return atomic_write(out_path, chunks())
        except httpx.TransportError:
            error = FishAudioError("availability", None, None, "Fish Audio is unavailable. Try again later.")
            retryable = True
        except FishAudioError as exc:
            error = exc
            retryable = exc.status == 429 or exc.status is not None and 500 <= exc.status < 600
        if written or not retryable or attempt == 2:
            raise error from None
        sleep((0.5, 1.0, 2.0)[attempt] + random.uniform(0, 0.1))
