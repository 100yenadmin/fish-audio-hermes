"""hermes fish: local credential entry and bounded diagnostics."""
import getpass
from importlib import metadata
from pathlib import Path
import re
import sys
import tempfile
import time

from . import account, commands, settings
from .errors import FishAudioError, response_error
from .secrets import fish_api_key, redact
from .tool_support import require, ToolInputError
from .tts import FishAudioTTSProvider

RESTART_HINT = "If a Hermes gateway or the desktop app is already running for this profile, restart it to pick up the new key."


def setup(parser):
    sub = parser.add_subparsers(dest="fish_command")
    login = sub.add_parser("login", help="Save a Fish API key locally")
    login.add_argument("--key-stdin", action="store_true")
    login.add_argument("--yes", action="store_true")
    sub.add_parser("status", help="Show this profile's Fish configuration")
    doctor = sub.add_parser("doctor", help="Check configuration and a billed TTS round trip")
    doctor.add_argument("--no-synth", action="store_true")
    use = sub.add_parser("use", help="Select a Fish voice")
    use.add_argument("voice_id")
    parser.set_defaults(fish_parser=parser)


def _login(args):
    key = (sys.stdin.read() if args.key_stdin and not sys.stdin.isatty()
           else getpass.getpass("Fish Audio API key: ")).strip()
    require(bool(key), "No key supplied. Create one at https://fish.audio/app/api-keys")
    base = commands.base_url()
    require(account.get_wallet(key, base, strict=True) is not None, "Fish Audio returned an invalid wallet; no key was saved.")
    from hermes_cli import config
    save = getattr(config, "save_env_value_secure", None) or getattr(config, "save_env_value", None)
    require(save is not None, "Use hermes tools or Desktop ▸ Settings ▸ Plugins ▸ Fish Audio"
            " (Capabilities ▸ Plugins on older Desktop) to save the key.")
    saved = save("FISH_API_KEY", key)
    require(not isinstance(saved, dict) or saved.get("success", True), "Hermes could not save the key. Use hermes tools or Desktop.")
    read = getattr(config, "get_env_value", None)
    effective = read("FISH_API_KEY") if read is not None else fish_api_key()
    require(effective == key,
            "The Fish Audio key is pinned by an administrator or config; the new key is not active. Providers were not switched.")
    def change(cfg):
        for section, label in (("tts", "text-to-speech"), ("stt", "speech-to-text")):
            block = cfg.setdefault(section, {})
            provider = block.get("provider")
            switch = not provider or provider == "fish-audio" or args.yes
            if not switch and sys.stdin.isatty():
                switch = input(f"Switch {label} to Fish Audio? [y/N] ").strip().lower() in {"y", "yes"}
            if switch:
                block["provider"] = "fish-audio"
    commands.write_config(change)
    model, defaulted = settings.resolve_model(None, key=key, base_url=base)
    print(f"Key saved for this profile. Model: {model}")
    if defaulted and model == "s2.1-pro-free":
        print(settings.FREE_MODEL_NOTICE)
    print(RESTART_HINT)
    return 0


def _version():
    try:
        from hermes_cli import __version__
        version = __version__
    except ImportError:
        version = "unknown"
    if version in {"unknown", "0.0.0"}:
        try:
            version = metadata.version("hermes-agent")
        except metadata.PackageNotFoundError:
            pass
    return version


def _doctor(args):
    failed = False
    def line(level, text):
        nonlocal failed
        failed = failed or level == "fail"
        print(f"{level}: {text}")
    key, base, cfg = fish_api_key(), commands.base_url(), settings._config()
    line("ok" if key else "fail", "Key present" if key else "Key absent; run hermes fish login")
    if key:
        started = time.monotonic()
        try:
            wallet = account.get_wallet(key, base, strict=True)
            line("ok" if wallet else "fail", f"Wallet reachable ({(time.monotonic()-started)*1000:.0f} ms)" if wallet else "Wallet invalid; check Fish billing")
        except FishAudioError as exc:
            line("fail", redact(str(exc)))
    for section in ("tts", "stt"):
        selected = cfg.get(section, {}).get("provider") == "fish-audio"
        line("ok" if selected else "warn", f"{section}.provider {'is Fish Audio' if selected else 'not Fish Audio; select it with hermes tools'}")
    version = _version()
    if version in {"unknown", "0.0.0"}:
        line("warn", "Hermes version unknown (source checkout?)")
    else:
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
        supported = bool(match and tuple(map(int, match.groups())) >= (0, 21, 5))
        line("ok" if supported else "fail", f"Hermes {version}; {'floor 0.21.5 met' if supported else 'upgrade to 0.21.5 or newer'}")
    if args.no_synth:
        line("ok", "TTS round trip skipped (--no-synth)")
    elif key:
        try:
            with tempfile.TemporaryDirectory(prefix="fish-doctor-") as directory:
                path = FishAudioTTSProvider().synthesize("Fish Audio voice check.", str(Path(directory) / "check.ogg"))
                line("ok", f"TTS {Path(path).stat().st_size} bytes; billed: a few bytes of text; temporary file deleted")
        except Exception:
            line("fail", "TTS round trip failed; check hermes fish status and Fish billing")
    try:
        from agent.transcription_registry import get_provider
        registered = get_provider("fish-audio") is not None
    except ImportError:
        registered = False
    line("ok" if registered else "fail", "STT registered" if registered else "STT absent; enable the Fish Audio plugin")
    return int(failed)


def handle(args):
    with settings.operator_terminal():
        return _handle(args)


def _handle(args):
    try:
        command = args.fish_command
        if command == "login":
            return _login(args)
        if command == "doctor":
            return _doctor(args)
        if command == "status":
            print(commands.handle("status"))
        elif command == "use":
            key = fish_api_key()
            require(bool(key), commands.NO_KEY)
            print(commands.use(args.voice_id, key, commands.base_url()))
        else:
            args.fish_parser.print_help()
        return 0
    except (FishAudioError, ToolInputError) as exc:
        print(redact(str(exc)))
    except (Exception, SystemExit):
        print("Fish Audio command failed. Check hermes tools or Desktop settings.")
    return 1
