"""Single wiring point for everything the plugin registers (filled in lane by lane)."""

from __future__ import annotations
import logging
import os
from pathlib import Path


def register_all(ctx) -> None:
    """Register without reading credentials or opening a transport."""
    from .tts import FishAudioTTSProvider
    from .stt import FishAudioTranscriptionProvider
    from . import tools, hooks

    for method, provider in (("register_tts_provider", FishAudioTTSProvider()),
                             ("register_transcription_provider", FishAudioTranscriptionProvider())):
        register = getattr(ctx, method, None)
        if register is None:
            logging.getLogger(__name__).warning("Hermes host lacks %s; skipping Fish Audio registration.", method)
        else:
            register(provider)
    _register_streaming()
    if hasattr(ctx, "register_tool"):
        tools.register(ctx)
    if hasattr(ctx, "register_hook"):
        ctx.register_hook("transform_llm_output", hooks.on_transform_llm_output)
        ctx.register_hook("pre_tool_call", hooks.on_pre_tool_call)
    from . import commands, cli
    if hasattr(ctx, "register_command"):
        ctx.register_command("fish", handler=commands.handle, description="Fish Audio voices and account",
                             args_hint="[status|voices|use|model|preview|balance|help]")
    if hasattr(ctx, "register_cli_command"):
        ctx.register_cli_command("fish", help="Fish Audio setup and diagnostics", setup_fn=cli.setup, handler_fn=cli.handle)
    if hasattr(ctx, "register_skill"):
        for name in ("fish-audio-setup", "fish-audio-expressive-speech", "fish-audio-voice-studio"):
            path = Path(__file__).resolve().parents[1] / "skills" / name / "SKILL.md"
            ctx.register_skill(name, path, description=_skill_description(path))


def _register_streaming() -> None:
    """Join Hermes streaming voice: the plugin PCM seam when Hermes has it, else the bridge.

    The bridge adds ``FishStreamer`` to Hermes's streaming registry through its public
    ``register`` call. ``FISH_AUDIO_HERMES_NO_BRIDGE=1`` turns it off (catalog rule 9 review).
    """
    if os.environ.get("HERMES_PLUGIN_HOST_PROCESS") == "1" or os.environ.get("FISH_AUDIO_HERMES_NO_BRIDGE") == "1":
        return
    try:
        from tools import tts_streaming
    except Exception:
        return
    try:
        if hasattr(tts_streaming, "_plugin_streamer"):
            from .tts import FishAudioTTSProvider
            FishAudioTTSProvider.pcm_seam = True
            return
        from .streaming import FishStreamer
        tts_streaming.register("fish-audio")(FishStreamer)
    except Exception as exc:
        logging.getLogger(__name__).warning("Fish Audio streaming voice unavailable: %s", exc)


def _skill_description(path: Path) -> str:
    """Read the one-line ``description:`` of a shipped SKILL.md without a YAML dependency.

    Hermes main no longer ships PyYAML, so the plugin must not import it at load time.
    """
    for line in path.read_text(encoding="utf-8").split("---", 2)[1].splitlines():
        if line.startswith("description:"):
            value = line.split(":", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""
