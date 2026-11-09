"""Agent access to the same profile settings operations as /fish."""
from . import commands, models, settings
from .tool_support import require
from .setup_check import checklist, first_missing


def current(key, base):
    cfg = settings._config()
    tts, stt = settings._mapping(cfg.get("tts")), settings._mapping(cfg.get("stt"))
    nested = settings._mapping(tts.get("fish-audio"))
    operator = settings.operator_account()
    model, source = settings.resolve_model_source(None, key="" if operator else key, base_url=base)
    provider = commands._managed_provider() or tts.get("provider") or "unset"
    fish = "fishaudio" in provider.casefold() or "fish-audio" in provider.casefold()
    result = {"voice": nested.get("voice") or tts.get("voice") or None,
              "model": models.effective_model(model), "model_source": source,
              "names": settings.aliases(), "speech_provider": provider, "fish_is_speech_provider": fish,
              "transcription_provider": stt.get("provider") or "unset"}
    if operator and source == "wallet":
        result.update(model_source="fallback", note="Wallet default is not read; showing s2.1-pro.")
    if not fish:
        # use_result/use_message own the provider and operator rules; mirror their facts without switching it.
        result["note"] = ((result.get("note", "") + " ") if result.get("note") else "") + (f"Saved for Fish Audio, but this agent currently speaks with {provider}. "
                          "If this profile has no speech provider of its own and no operator setting pins one, `use` makes Fish Audio its provider; "
                          "otherwise a person switches it in Settings ▸ Voice or with `hermes tools` ▸ Text-to-Speech ▸ Fish Audio.")
        if commands._managed_provider() and operator:
            result["note"] += " " + commands.use_message(provider, True).removeprefix("Saved. ")
    result["setup"] = checklist(key, base)
    result["setup_ready"] = first_missing(result["setup"]) is None
    return result


def execute(args, key, base, session):
    action = args.get("action")
    message = None
    require(action in {"get", "use", "set_model", "add_name", "remove_name"}, "Unknown fish_settings action.")
    if action == "use":
        voice = args.get("voice")
        require(isinstance(voice, str), "voice is required.")
        commands.use_result(commands.resolve_title(voice.strip(), key, base), key, base)
    elif action == "set_model":
        require(args.get("model") in models.MODEL_IDS, "Choose a supported Fish model.")
        message = commands.set_model(args["model"], key, base)["message"]
    elif action == "add_name":
        commands.alias_add(args.get("name"), args.get("voice"), key, base)
    elif action == "remove_name":
        commands.alias_remove(args.get("name"))
    result = current(key, base)
    if action == "set_model" and message != "Saved.":
        result["model_message"] = message.removeprefix("Saved.").strip() or message
    if action != "get":
        result["changed"] = True
    return result
