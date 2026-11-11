"""Offline setup steps for the active profile; never return credential values."""
from . import commands, settings

# The key is read only through Hermes's secret scope (never the profile .env), so a key saved after this process
# started is invisible here; say how to pick it up instead of probing the file.
RESTART_HINT = "If you saved a key just now, restart the Hermes gateway or Desktop app so it is picked up."


def _own_provider():
    """This profile's own tts.provider from config.yaml; the merged config always carries Hermes's default ("edge")."""
    try:
        from hermes_cli import config
        return settings._mapping(settings._mapping(config.read_raw_config()).get("tts")).get("provider") or None
    except Exception:
        return None


def first_missing(steps):
    return next((step["say"] for step in steps if not step["ok"] and not step.get("optional")), None)


def checklist(key, base):
    cfg = settings._config()
    tts, stt = settings._mapping(cfg.get("tts")), settings._mapping(cfg.get("stt"))
    nested = settings._mapping(tts.get("fish-audio"))
    operator, pinned = settings.operator_account(), commands._managed_provider()
    provider = pinned or tts.get("provider") or "unset"
    fish = "fishaudio" in provider.casefold() or "fish-audio" in provider.casefold()
    key_say = "A Fish key is visible to this process." if key else commands.NO_KEY
    if operator and not key:
        key_say = "Ask the operator of this agent to finish the Fish Audio setup."
    elif not key:
        key_say = commands.NO_KEY + " " + RESTART_HINT
    speech_say = "This agent speaks with Fish Audio."
    if not fish:
        switch = ("`/fish use <voice>` makes Fish the provider." if not pinned and not _own_provider() else
                  "Switch in Desktop Settings ▸ Voice, or run `hermes config set tts.provider fish-audio` "
                  "on the Hermes machine, then restart the gateway.")
        if pinned and operator:
            switch = commands.use_message(provider, True).removeprefix("Saved. ")
        speech_say = f"Saved for Fish Audio, but this agent currently speaks with {provider}. " + switch
    voice = settings.resolve_voice_source(tts.get("voice"), nested)[0]  # The voice synthesis actually uses.
    transcription = stt.get("provider") or "unset"
    stt_fish = "fishaudio" in transcription.casefold() or "fish-audio" in transcription.casefold()
    return [
        {"id": "key", "ok": bool(key), "say": key_say},
        {"id": "speech", "ok": fish, "say": speech_say},
        {"id": "voice", "ok": True, "optional": True, "say": f"Voice selected: {voice}." if voice else
         "Using Fish's default voice. Pick one: `/fish voices <description>` then `/fish use <id>`."},
        {"id": "transcription", "ok": stt_fish, "optional": True, "say":
         "Transcription uses Fish Audio." if stt_fish else
         "For Fish transcription, run `hermes config set stt.provider fish-audio` or choose Fish Audio in Desktop Settings."},
        {"id": "desktop", "ok": True, "optional": True, "say":
         "In Hermes Desktop, the Voices page is in the sidebar (Desktop 0.21.5+)."},
    ]
