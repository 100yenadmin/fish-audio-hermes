"""Single wiring point for everything the plugin registers (filled in lane by lane)."""

from __future__ import annotations
import logging


def register_all(ctx) -> None:
    """Register without reading credentials or opening a transport."""
    from .tts import FishAudioTTSProvider
    from .stt import FishAudioTranscriptionProvider

    for method, provider in (("register_tts_provider", FishAudioTTSProvider()),
                             ("register_transcription_provider", FishAudioTranscriptionProvider())):
        register = getattr(ctx, method, None)
        if register is None:
            logging.getLogger(__name__).warning("Hermes host lacks %s; skipping Fish Audio registration.", method)
        else:
            register(provider)
