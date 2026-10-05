"""Hermes streaming voice: one sentence in, int16 mono 24 kHz PCM out, under the caller's profile."""
import logging

from . import PROVIDER_NAME, client, settings
from .errors import FishAudioError
from .models import MODELS
from .secrets import fish_api_key
from .state import record_failure
from .tags import adapt_tags
from .tts import SETUP_MESSAGE

logger = logging.getLogger(__name__)

try:
    from tools.tts_streaming import StreamingTTSProvider, _capped
except ImportError:  # Unit tests run without Hermes.
    class StreamingTTSProvider:
        sample_rate, channels, sample_width = 24000, 1, 2

        def __init__(self, tts_config, section):
            self.tts_config, self.section = tts_config, section

    def _capped(chunks, label):
        yield from chunks


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


class FishStreamer(StreamingTTSProvider):
    sample_rate = 24000
    channels = 1
    sample_width = 2

    @staticmethod
    def available():
        """Network-free: our provider is registered, the scoped key exists and streaming is on."""
        try:
            from agent.tts_registry import get_provider
            provider = get_provider(PROVIDER_NAME)
            return (type(provider).__name__ == "FishAudioTTSProvider" and bool(fish_api_key())
                    and streaming_enabled())
        except Exception:
            return False

    def stream(self, text):
        # Resolved on the first chunk, inside the consumer's profile scope (Desktop's producer
        # thread enters it too), so the key and settings are the requesting profile's.
        key = fish_api_key()
        if not key:
            raise FishAudioError("credential", None, None, SETUP_MESSAGE)
        params, _ = settings.resolve_tts(None, None, None, None, "stream.wav", key=key)
        base_url = params.pop("base_url")
        family = next(row["family"] for row in MODELS if row["id"] == params["model"])
        params.update(text=adapt_tags(text, family), format="pcm", sample_rate=self.sample_rate)
        params.setdefault("latency", "balanced")
        for codec_knob in ("mp3_bitrate", "opus_bitrate"):
            params.pop(codec_knob, None)
        if not params["text"].strip():
            return
        transport = TRANSPORTS.get(settings.transport_settings().get("transport"), TRANSPORTS[DEFAULT_TRANSPORT])
        logger.debug("FishStreamer: %d characters over %s with %s", len(params["text"]), transport, params["model"])
        chunks = getattr(client, transport)(params, key, base_url)
        try:
            yield from _capped(_aligned(chunks), "Fish Audio streaming TTS")
        except FishAudioError as exc:
            record_failure(exc.kind, str(exc))
            raise
        finally:
            chunks.close()
