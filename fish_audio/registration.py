"""Single wiring point for everything the plugin registers (filled in lane by lane)."""

from __future__ import annotations
import logging
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
            ctx.register_skill(name, Path(__file__).resolve().parents[1] / "skills" / name / "SKILL.md")
