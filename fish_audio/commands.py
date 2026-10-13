"""Profile-aware /fish commands; credentials are never accepted in chat."""
import re
import threading

from . import account, client, models, settings, state, tools
from .errors import FishAudioError, response_error
from .models import MODEL_IDS
from .secrets import fish_api_key, redact
from .tool_support import require, voice_id, ToolInputError

_LOCK = threading.RLock()
HELP = "/fish status · voices [query] · use <id or name> · model <id> · preview <id or name> [text] (billed) · alias [list] · alias add <name> <voice id or name> · alias remove <name> · balance · help"
NO_KEY = ("Fish Audio isn't set up for this profile yet. Get a key at https://fish.audio/app/api-keys, then finish "
          "setup in the Desktop app (Settings ▸ Plugins ▸ Fish Audio; Capabilities ▸ Plugins on older Desktop) or run `hermes fish login` on the machine running Hermes. "
          "For safety, never paste API keys into chat.")
KEY_IN_CHAT = ("That looks like an API key. Keys pasted into chat may be stored in the conversation history; "
               "rotate it at https://fish.audio/app/api-keys and use `hermes fish login` instead.")


def base_url():
    return settings._base_url(settings.transport_settings().get("base_url", settings.DEFAULT_BASE_URL))


def write_config(change):
    from hermes_cli import config
    with _LOCK:
        require(not getattr(config, "is_managed", lambda: False)(),
                "This managed Hermes install does not allow config writes. Use hermes tools or Desktop settings.")
        cfg = config.read_raw_config()
        change(cfg)
        try:
            config.save_config(cfg, strip_defaults=False)
        except (SystemExit, PermissionError):
            raise ToolInputError("Hermes refused the config write; this install may be managed. Use hermes tools or Desktop settings.") from None
    return cfg


def status(key=None):
    key = fish_api_key() if key is None else key
    cfg, base = settings._config(), base_url()
    nested = settings._mapping(settings._mapping(cfg.get("tts")).get("fish-audio"))
    operator = settings.operator_account()
    wallet = account.cached_wallet(key, base) if key and not operator else None
    package = account.get_package(key, base) if key and not operator else None
    model, defaulted = settings.resolve_model(None, key="" if operator else key, base_url=base)
    managed = settings.transport_settings().get("allow_free_model", True) is False
    reason = ("managed pin" if managed and (defaulted or nested.get("model") == "s2.1-pro-free") else
              "nested" if not defaulted else "unknown wallet" if wallet is None else
              ("no API credit" if models.free_model_phase() == "ended" else "free tier") if model == "s2.1-pro-free" else
              "paid account")
    if operator:
        reason = "operator managed"
        if defaulted and not managed:
            model = "Fish default"  # The unpinned default depends on the operator's wallet, which isn't read here.
    tts, stt = cfg.get("tts", {}), cfg.get("stt", {})
    failure = state.last_failure()
    if operator and failure:
        failure = failure[:2]  # Stored pre-toggle messages can contain account links.
    free_warning = models.free_model_warning() if nested.get("model") == "s2.1-pro-free" and not managed else None
    return "\n".join((f"Key set: {'yes' if key else 'no'}", f"TTS: {tts.get('provider') or 'unset'} (Fish: {tts.get('provider') == 'fish-audio'})",
        f"STT: {stt.get('provider') or 'unset'} (Fish: {stt.get('provider') == 'fish-audio'})",
        f"Voice: {nested.get('voice') or tts.get('voice') or 'Fish default'}", f"Model: {model} ({reason})",
        *(("warn: " + models.s1_warning(),) if nested.get("model") == "s1" else ()),
        *(("warn: " + free_warning,) if free_warning else ()),
        *(("Account: managed by the operator",) if operator else
          (f"API credit: {wallet.credit if wallet else 'unknown'}", f"Plan: {package.get('type', 'unknown') if package else 'unknown'}")),
        f"Last failure: {' · '.join(failure) if failure else 'none'}", f"Plugin: {client.PLUGIN_VERSION}",
        "Docs: https://docs.fish.audio"))


def _managed_provider():
    try:
        from hermes_cli.managed_scope import load_managed_config
        provider = settings._mapping(settings._mapping(load_managed_config()).get("tts")).get("provider")
    except Exception:
        return None
    return provider if isinstance(provider, str) and provider else None


def use(ident, key, base):
    return use_message(*use_result(ident, key, base))


def use_message(provider, by_operator):
    if provider is None:
        return "Saved."
    if by_operator:  # A managed pin overrides the profile; only its operator can switch it.
        return f"Saved. This agent's speech provider ({provider}) is set by its operator."
    return f"Saved. Your current TTS provider is {provider}. Switch with `hermes tools` ▸ Text-to-Speech ▸ Fish Audio."


def use_result(ident, key, base):
    """Saves the voice. Returns the non-Fish TTS provider still speaking (None when Fish speaks) and whether its operator pinned it."""
    require(voice_id(ident), "Use a valid Fish Audio voice id.")
    require_unmanaged("voice")
    verify_voice(ident, key, base)
    # A managed layer (such as the evaOS overlay) may pin the provider this profile's config.yaml leaves unset;
    # Use never competes with it. The merged config can't tell: Hermes's own default ("edge") is always there.
    pinned = _managed_provider()
    def change(cfg):
        tts = cfg.setdefault("tts", {})
        tts.setdefault("fish-audio", {})["voice"] = ident
        if not pinned and not tts.get("provider"):
            tts["provider"] = "fish-audio"
    cfg = write_config(change)
    provider = pinned or cfg["tts"].get("provider") or "fish-audio"
    if "fishaudio" in provider.casefold() or "fish-audio" in provider.casefold():
        return None, False
    return provider, bool(pinned) and settings.operator_account()


class WriteRefusal(ToolInputError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def require_unmanaged(leaf):
    if settings.managed_leaf("tts", "fish-audio", leaf):
        raise WriteRefusal("managed", "This setting is set by the operator of this agent.")


def verify_voice(ident, key, base):
    try:
        client.get_json(f"/model/{ident}", {}, key, base)
    except FishAudioError as exc:
        if exc.status in {400, 404}:
            raise response_error(exc.status, body=b'{"code":"voice_not_found"}', key=key) from None
        raise


def _write_alias(name, ident):
    """Adds (ident) or removes (None) one name in the raw config mapping. Entries that settings.aliases() skips, such as a
    hand-edited name that breaks the rules, stay as they are."""
    def change(cfg):
        nested = cfg.setdefault("tts", {}).setdefault("fish-audio", {})
        raw = nested.get("aliases") if isinstance(nested.get("aliases"), dict) else {}
        raw = {k: v for k, v in raw.items() if not (isinstance(k, str) and k.strip().casefold() == name)}
        if ident is not None:
            raw[name] = ident
        nested["aliases"] = raw
    write_config(change)


def alias_add(name, voice, key, base):
    name = settings.alias_name(name)
    require_unmanaged("aliases")
    ident = settings.resolve_voice(voice)
    require(ident is not None, settings.UNKNOWN_VOICE)
    require(re.fullmatch(r"[0-9a-f]{32}", ident), "A saved name must point to a 32-hex voice id.")
    verify_voice(ident, key, base)
    with _LOCK:
        saved = settings.aliases()
        updated = name in saved
        if not updated and len(saved) >= settings.MAX_ALIASES:
            raise WriteRefusal("limit", "At most 50 voice names can be saved per profile.")
        saved[name] = ident
        _write_alias(name, ident)
    return saved, updated


def alias_remove(name):
    require_unmanaged("aliases")
    if not isinstance(name, str) or name.strip().casefold() not in settings.aliases():
        raise WriteRefusal("not_found", "No saved voice name found.")
    name = name.strip().casefold()
    with _LOCK:
        saved = settings.aliases()
        if name not in saved:
            raise WriteRefusal("not_found", "No saved voice name found.")
        del saved[name]
        _write_alias(name, None)
    return saved


def handle(raw_args=""):
    operator = settings.operator_account()
    try:
        if re.search(r"sk-[A-Za-z0-9_-]{20,}", raw_args):
            return "Never paste API keys into chat. Contact the operator of this agent." if operator else KEY_IN_CHAT
        command, _, rest = raw_args.strip().partition(" ")
        command = command or "status"
        if command == "help":
            return HELP
        if command == "balance" and operator:
            return "Voice billing for this agent is handled by its operator."
        key = fish_api_key()
        if not key:
            return "Ask the operator of this agent to finish the Fish Audio setup." if operator else NO_KEY
        base = base_url()
        if command == "status":
            return redact(status(key))
        if command == "voices":
            from .voices import execute
            result = execute({"action": "search", "query": rest, "page_size": 5}, key, base, "")
            return "\n".join(f"{v['id']} · {v['title']} · {', '.join(v['languages'])}" for v in result["items"]) + \
                "\nUse one: /fish use <id>. Browse more: https://fish.audio/discovery"
        if command == "use":
            ident = settings.resolve_voice(rest.strip())
            require(ident is not None, settings.UNKNOWN_VOICE)
            return use(ident, key, base)
        if command == "alias":
            action, _, args = rest.strip().partition(" ")
            if action in {"", "list"}:
                return "\n".join(f"{n} · {v}" for n, v in sorted(settings.aliases().items())) or "No saved voice names yet. Add one: /fish alias add <name> <voice id>"
            if action == "add":
                name, _, voice = args.partition(" ")
                _, updated = alias_add(name, voice.strip(), key, base)
                return "Updated." if updated else "Saved."
            if action == "remove":
                alias_remove(args)
                return "Removed."
            return HELP
        if command == "model":
            require(rest in MODEL_IDS, "Unknown Fish model. Choose: " + ", ".join(sorted(MODEL_IDS)))
            require_unmanaged("model")
            write_config(lambda cfg: cfg.setdefault("tts", {}).setdefault("fish-audio", {}).__setitem__("model", rest))
            model, _ = settings.resolve_model(rest, key=key, base_url=base)
            if rest == "s2.1-pro-free" and model != rest:
                return "Saved. This profile's policy uses paid s2.1-pro instead of s2.1-pro-free."
            if rest == "s1":
                return "Saved.\n" + models.s1_warning()
            warning = models.free_model_warning() if model == "s2.1-pro-free" else None
            return ("Saved." + ("\n" + warning if warning else "")
                    + ("\n" + settings.free_model_notice() if model == "s2.1-pro-free" and not warning and not settings.operator_account() else ""))
        if command == "preview":
            ident, _, text = rest.partition(" ")
            ident = settings.resolve_voice(ident)
            require(ident is not None, settings.UNKNOWN_VOICE)
            text = text or "Hi! This is how I sound."
            require(len(text) <= 200, "Preview text must be 200 characters or fewer.")
            result = tools._speak({"text": text, "voice": ident}, key, base, "", record_media=False)
            return result["media_tag"]
        if command == "balance":
            wallet, package = account.get_wallet(key, base), account.get_package(key, base) or {}
            return (f"API credit: {wallet.credit if wallet else 'unknown'}; cumulative top-up: {wallet.cumulative_top_up if wallet else 'unknown'}\n"
                    f"Plan: {package.get('type', 'unknown')}; {package.get('balance', '?')}/{package.get('total', '?')}; renewal: {package.get('finished_at', '?')}\n"
                    "App plan credits are separate from API credits.\nhttps://fish.audio/app/developers/billing\nhttps://fish.audio/plan")
        return HELP
    except (FishAudioError, ToolInputError) as exc:
        return redact(str(exc))
    except Exception:
        return "Fish Audio could not complete this command. Check setup with hermes fish doctor."
