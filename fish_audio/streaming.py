"""Hermes streaming voice: one sentence in, int16 mono 24 kHz PCM out, under the caller's profile.

Hermes's plugin streaming hook (#133723) calls ``FishAudioTTSProvider.stream``, which runs ``stream_pcm``.
Hermes builds without the hook never call it and speak each sentence with whole-file synthesis.
"""
import logging
import os

from . import client, settings
from .errors import FishAudioError
from .models import MODELS
from .secrets import fish_api_key
from .state import record_failure
from .tags import adapt_tags
from .tts import setup_message

logger = logging.getLogger(__name__)
SAMPLE_RATE = 24000


# Measured on the live API (2026-10-05, p50 time to first PCM byte): HTTP 243 ms on the shared
# keep-alive client, WebSocket 887 ms with a fresh connection per sentence. ``transport: ws``
# keeps the other path for diagnosis.
TRANSPORTS = {"http": "tts_pcm", "ws": "tts_live"}
DEFAULT_TRANSPORT = "http"


def streaming_enabled():
    # YAML 1.1 reads a bare ``off`` as False.
    return settings.transport_settings().get("streaming", "auto") not in {"off", False}


def _aligned(chunks):
    """Re-cut network chunks on int16 sample boundaries; a dangling final byte is dropped."""
    carry = b""
    for chunk in chunks:
        chunk = carry + chunk
        cut = len(chunk) & ~1
        carry = chunk[cut:]
        if cut:
            yield chunk[:cut]


def streaming_available():
    """Network-free: the scoped key exists and streaming is on. Off in the plugin host process."""
    if os.environ.get("HERMES_PLUGIN_HOST_PROCESS") == "1":
        return False
    try:
        return bool(fish_api_key()) and streaming_enabled()
    except Exception:
        return False


def stream_pcm(text, *, voice=None, model=None):
    # Resolved on the first chunk, inside the consumer's profile scope (Desktop's producer
    # thread enters it too), so the key and settings are the requesting profile's.
    chunks = None
    try:
        key = fish_api_key()
        if not key:
            raise FishAudioError("credential", None, None, setup_message())
        params, _ = settings.resolve_tts(voice, model, None, None, "stream.wav", key=key)
        base_url = params.pop("base_url")
        family = next(row["family"] for row in MODELS if row["id"] == params["model"])
        params.update(text=adapt_tags(text, family), format="pcm", sample_rate=SAMPLE_RATE)
        params.setdefault("latency", "balanced")
        for codec_knob in ("mp3_bitrate", "opus_bitrate"):
            params.pop(codec_knob, None)
        if not params["text"].strip():
            return
        transport = TRANSPORTS.get(settings.transport_settings().get("transport"), TRANSPORTS[DEFAULT_TRANSPORT])
        logger.debug("Fish Audio streaming: %d characters over %s with %s", len(params["text"]), transport, params["model"])
        chunks = getattr(client, transport)(params, key, base_url)
        silent = True
        for pcm in _aligned(chunks):
            silent = False
            yield pcm
        # A 2xx with no whole sample would otherwise play as silence with nothing recorded.
        if silent:
            raise FishAudioError("availability", None, None, "Fish Audio returned no audio for this sentence.")
    except FishAudioError as exc:
        record_failure(exc.kind, str(exc))
        raise
    finally:
        if chunks is not None:
            chunks.close()
