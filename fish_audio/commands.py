"""Profile-aware /fish commands; credentials are never accepted in chat."""
import re
import threading

from . import account, client, settings, state, tools
from .errors import FishAudioError, response_error
from .models import MODEL_IDS
from .secrets import fish_api_key, redact
from .tool_support import require, voice_id, ToolInputError

_LOCK = threading.RLock()
HELP = "/fish status · voices [query] · use <id> · model <id> · preview <id> [text] (billed) · balance · help"
NO_KEY = ("Fish Audio isn't set up for this profile yet. Get a key at https://fish.audio/app/api-keys, then finish "
          "setup in the Desktop app (Plugins ▸ Fish Audio) or run `hermes fish login` on the machine running Hermes. "
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
              "free tier" if model == "s2.1-pro-free" else "paid account")
    if operator:
        reason = "operator managed"
        if defaulted and not managed:
            model = "Fish default"  # The unpinned default depends on the operator's wallet, which isn't read here.
    tts, stt = cfg.get("tts", {}), cfg.get("stt", {})
    failure = state.last_failure()
    if operator and failure:
        failure = failure[:2]  # Stored pre-toggle messages can contain account links.
    return "\n".join((f"Key set: {'yes' if key else 'no'}", f"TTS: {tts.get('provider') or 'unset'} (Fish: {tts.get('provider') == 'fish-audio'})",
        f"STT: {stt.get('provider') or 'unset'} (Fish: {stt.get('provider') == 'fish-audio'})",
        f"Voice: {nested.get('voice') or tts.get('voice') or 'Fish default'}", f"Model: {model} ({reason})",
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
    require(voice_id(ident), "Use a valid Fish Audio voice id.")
    try:
        client.get_json(f"/model/{ident}", {}, key, base)
    except FishAudioError as exc:
        if exc.status in {400, 404}:
            raise response_error(exc.status, body=b'{"code":"voice_not_found"}', key=key) from None
        raise
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
        return "Saved."
    if pinned:  # A managed pin overrides the profile, so hermes tools can't switch it.
        return f"Saved. This agent's speech provider ({provider}) is set by its operator."
    return f"Saved. Your current TTS provider is {provider}. Switch with `hermes tools` ▸ Text-to-Speech ▸ Fish Audio."


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
            return use(rest.strip(), key, base)
        if command == "model":
            require(rest in MODEL_IDS, "Unknown Fish model. Choose: " + ", ".join(sorted(MODEL_IDS)))
            write_config(lambda cfg: cfg.setdefault("tts", {}).setdefault("fish-audio", {}).__setitem__("model", rest))
            model, _ = settings.resolve_model(rest, key=key, base_url=base)
            if rest == "s2.1-pro-free" and model != rest:
                return "Saved. This profile's policy uses paid s2.1-pro instead of s2.1-pro-free."
            return "Saved." + ("\n" + settings.FREE_MODEL_NOTICE if model == "s2.1-pro-free" and not settings.operator_account() else "")
        if command == "preview":
            ident, _, text = rest.partition(" ")
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
