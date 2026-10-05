"""Single wiring point for everything the plugin registers (filled in lane by lane)."""

from __future__ import annotations


def register_all(ctx) -> None:
    """Register without reading credentials or opening a transport."""
    from .tts import FishAudioTTSProvider

    ctx.register_tts_provider(FishAudioTTSProvider())
