"""hermes fish: profile setup, diagnostics and noninteractive scripting."""
import argparse
import getpass
from contextlib import redirect_stdout
from functools import partial
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

from . import account, commands, models, settings
from .errors import FishAudioError, response_error
from .secrets import fish_api_key, redact
from .tool_support import require, run, ToolInputError
from .tts import FishAudioTTSProvider

RESTART_HINT = "If a Hermes gateway or the desktop app is already running for this profile, restart it to pick up the new key."


def setup(parser):
    original_error = parser.error
    def error(message):
        if getattr(parser, "fish_json", False):
            print(json.dumps({"success": False, "error": redact(message)}, ensure_ascii=False))
            parser.exit(1)
        original_error(message)
    class ScriptParser(argparse.ArgumentParser):
        def parse_known_args(self, args=None, namespace=None):
            parser.fish_json = "--json" in (sys.argv[1:] if args is None else args)
            namespace, extras = super().parse_known_args(args, namespace)
            # Hermes's own top-level parser would reject leftovers with plain text and exit 2.
            if extras and parser.fish_json:
                self.error("unrecognized arguments: " + " ".join(extras))
            return namespace, extras
    parser.error = error
    sub = parser.add_subparsers(dest="fish_command", parser_class=ScriptParser)
    login = sub.add_parser("login", help="Save a Fish API key locally")
    login.add_argument("--key-stdin", action="store_true")
    login.add_argument("--yes", action="store_true")
    sub.add_parser("status", help="Show this profile's Fish configuration")
    doctor = sub.add_parser("doctor", help="Check configuration and a billed TTS round trip")
    doctor.add_argument("--no-synth", action="store_true")
    use = sub.add_parser("use", help="Select a Fish voice")
    use.add_argument("voice_id")
    speak = sub.add_parser("speak", help="Synthesize speech for scripts")
    speak.add_argument("text", nargs="?")
    for flag in ("text-file", "voice", "model", "out"):
        speak.add_argument("--" + flag)
    speak.add_argument("--format", choices=("mp3", "ogg", "wav"))
    for flag in ("stdin", "timestamps", "force"):
        speak.add_argument("--" + flag, action="store_true")
    transcribe = sub.add_parser("transcribe", help="Transcribe an audio file")
    transcribe.add_argument("file_path")
    transcribe.add_argument("--model", choices=("transcribe-1-pro", "transcribe-1"))
    transcribe.add_argument("--language")
    transcribe.add_argument("--diarize", action="store_const", const="true")
    transcribe.add_argument("--srt", action="store_true", default=None)
    voices = sub.add_parser("voices", help="Search voices or list your own")
    voices.add_argument("query", nargs="?")
    voices.add_argument("--mine", action="store_true")
    voices.add_argument("--page", type=int)
    voices.add_argument("--page-size", type=int)
    for command in (speak, transcribe, voices, sub.add_parser("balance", help="Show API credit and plan")):
        command.error = error
        command.add_argument("--json", action="store_true")
    parser.set_defaults(fish_parser=parser)


def _destination(path, force):
    # A symlinked folder (macOS /tmp) is fine: each file lands by link or replace, never written through a link.
    require(not path.is_symlink(), "Output path must not be a symlink.")
    require(path.parent.is_dir(), "Output parent directory is missing.")
    require(os.access(path.parent, os.W_OK), "Output directory is not writable.")
    require(not path.exists() or force and path.is_file(), "Output exists; use --force to replace a file.")


def _outputs(target, timestamps):
    """Every path --out may write: the audio, plus its subtitles when timestamps are on."""
    require(target.suffix.lower() not in {".srt", ".vtt"}, "Use an audio file name for --out (not .srt or .vtt).")
    return [target] + ([target.with_suffix(ext) for ext in (".srt", ".vtt")] if timestamps else [])


def _copy_output(result, target, force):
    source = Path(result["file_path"])
    require(source.resolve() != target.resolve(), "Output must differ from the cached audio path.")
    pairs = [(source, target)] + [(source.with_suffix(ext), target.with_suffix(ext))
                                for ext in (".srt", ".vtt") if source.with_suffix(ext).is_file()]
    for _, dest in pairs:
        _destination(dest, force)
    staged, placed = [], []  # placed: (dest, identity of the file this run put there)
    try:
        for src, dest in pairs:  # Stage every file first; a failure then removes only what this run placed.
            temp_path, identity = _new_file(dest.parent / ".fish-tmp", src)
            staged.append((temp_path, identity, dest))
        for temp_path, identity, dest in staged:
            if force:
                os.replace(temp_path, dest)
                placed.append((dest, identity))
            else:
                placed.append((dest, identity))  # recorded first: the link shares the staged file's identity
                placed[-1] = (dest, _place_new(temp_path, dest, identity))
    except Exception as exc:
        if not force:
            _remove_own(placed)
        elif placed:
            raise ToolInputError("Only some outputs were replaced: " + ", ".join(str(d) for d, _ in placed)) from exc
        raise
    except BaseException:  # Ctrl-C and exits pass through unchanged after cleanup
        if not force:
            _remove_own(placed)
        raise
    finally:
        for temp_path, _, _ in staged:
            temp_path.unlink(missing_ok=True)
    result["out_path"] = str(target)


def _identity(stat):
    return stat.st_dev, stat.st_ino


def _new_file(base, src):
    """Create a fresh file next to its destination (mode 0666 minus the umask) holding src's bytes."""
    for attempt in range(100):
        path = base.with_name(f"{base.name}{attempt}")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as out, src.open("rb") as data:
                identity = _identity(os.fstat(out.fileno()))
                shutil.copyfileobj(data, out)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path, identity
    raise ToolInputError("Could not create a temporary output file.")


def _remove_own(placed):
    """Delete only files that are still exactly the ones this run created (same device and inode)."""
    for dest, identity in placed:
        try:
            # POSIX has no unlink-if-inode; a file swapped in between these two calls is the accepted residue.
            if identity is not None and _identity(os.lstat(dest)) == identity:
                dest.unlink()
        except OSError:  # cleanup never hides the original error
            pass


def _place_new(temp_path, dest, identity):
    """Put a staged file at dest without replacing a file that appeared meanwhile; return its identity."""
    try:
        os.link(temp_path, dest)  # atomic, refuses an existing name, keeps the staged file's identity
        return identity
    except FileExistsError:
        raise ToolInputError("Output exists; use --force to replace a file.") from None
    except OSError:  # no hard links (exFAT, some network drives): exclusive create, then copy
        pass
    try:
        _, created = _new_file_at(dest, temp_path)
    except FileExistsError:
        raise ToolInputError("Output exists; use --force to replace a file.") from None
    return created


def _new_file_at(path, src):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    identity = None
    try:
        with os.fdopen(descriptor, "wb") as out, src.open("rb") as data:
            identity = _identity(os.fstat(out.fileno()))
            shutil.copyfileobj(data, out)
    except BaseException:
        _remove_own([(path, identity)])
        raise
    return path, identity


def _script(args):
    from . import account_tool, tools, transcribe, voices
    command = args.fish_command
    called = False
    try:
        fields = {name: getattr(args, name) for name in
                  {"speak": ("voice", "model", "format", "timestamps"),
                   "transcribe": ("file_path", "model", "language", "diarize", "srt"),
                   "voices": ("query", "page", "page_size"), "balance": ()}[command]
                  if getattr(args, name) is not None}
        if command == "speak":
            require(sum((args.text is not None, args.text_file is not None, args.stdin)) == 1,
                    "Provide exactly one text source: TEXT, --text-file or --stdin.")
            fields["text"] = (Path(args.text_file).read_text(encoding="utf-8") if args.text_file is not None
                              else sys.stdin.read() if args.stdin else args.text)
            if args.out:
                suffix = Path(args.out).suffix.lower().lstrip(".")
                if suffix in {"mp3", "ogg", "wav"}:
                    require(fields.setdefault("format", suffix) == suffix,
                            f"--format {fields['format']} does not match the --out file name (.{suffix}).")
                for path in _outputs(Path(args.out), args.timestamps):
                    _destination(path, args.force)
        elif command in {"voices", "balance"}:
            fields["action"] = ("mine" if args.mine else "search") if command == "voices" else "balance"
        operation = {"speak": partial(tools._speak, record_media=False), "transcribe": transcribe.execute,
                     "voices": voices.execute, "balance": account_tool.execute}[command]
        called = command in {"speak", "transcribe"}
        with redirect_stdout(sys.stderr):
            result = json.loads(run(operation, fields))
        if result.get("success") and command == "speak" and args.out:
            try:
                _copy_output(result, Path(args.out), args.force)
            except Exception as exc:  # The speech is billed and cached: say where it is.
                result = {"success": False, "error": f"The audio is billed and saved at {result['file_path']}; --out failed: {exc}",
                          "file_path": result["file_path"]}
    except Exception as exc:
        result = {"success": False, "error": redact(str(exc))}
    if "error" in result:
        result["error"] = redact(str(result["error"]))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif not result.get("success"):
        print(result.get("error", "Fish Audio command failed."))
    elif command == "speak":
        for note in ("notice", "timestamps_error"):
            if result.get(note):
                print(result[note], file=sys.stderr)
        print(result["file_path"])
        if result.get("out_path"):
            print(result["out_path"])
        if result.get("duration") is not None:
            print(f"Duration: {result['duration']} s")
    elif command == "transcribe":
        print(result["text"])
        if result.get("srt_path"):
            print(result["srt_path"])
    elif command == "voices":
        for item in result.get("items", []):
            details = ", ".join((item.get("languages") or []) + (item.get("tags") or []))
            print(f"{item.get('title')} — {item.get('id')}" + (f" ({details})" if details else ""))
    else:
        plan = result.get("plan", {})
        print(f"API credit: {result.get('credit')}; cumulative top-up: {result.get('cumulative_top_up')}\n"
              f"Plan: {plan.get('type', 'unknown')}; {plan.get('balance', '?')}/{plan.get('total', '?')}; renewal: {plan.get('finished_at', '?')}\n"
              "App plan credits are separate from API credits.")
    if not args.json and called and (result.get("success") or result.get("file_path")):
        print("billed to your Fish API credit", file=sys.stderr)
    return 0 if result.get("success") else 1


def _login(args):
    key = (sys.stdin.read() if args.key_stdin and not sys.stdin.isatty()
           else getpass.getpass("Fish Audio API key: ")).strip()
    require(bool(key), "No key supplied. Create one at https://fish.audio/app/api-keys")
    base = commands.base_url()
    require(account.get_wallet(key, base, strict=True) is not None, "Fish Audio returned an invalid wallet; no key was saved.")
    from hermes_cli import config
    save = getattr(config, "save_env_value_secure", None) or getattr(config, "save_env_value", None)
    require(save is not None, "Use hermes tools or Desktop ▸ Settings ▸ Plugins ▸ Fish Audio ▸ Agent settings"
            " (on older Desktop, the gear on the fish-audio row in Capabilities ▸ Plugins) to save the key.")
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
        print(settings.free_model_notice())
    elif model == "s2.1-pro-free" and (warning := models.free_model_warning()):
        print("warn: " + warning)
    print(RESTART_HINT)
    return 0


def _version():
    # Hermes 0.21.6+ gates requires_hermes on version_info's base version, "unknown" included (which loads); report
    # exactly that. Its __version__ is "0.0.0" without an install stamp, so the fallbacks are for older builds only.
    try:
        from hermes_cli.version_info import get_version_info
    except ImportError:
        pass
    else:
        try:
            return get_version_info().base_version
        except Exception:
            return "unknown"
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
    if settings._mapping(settings._mapping(cfg.get("tts")).get("fish-audio")).get("model") == "s1":
        line("warn", models.s1_warning())
    if settings._mapping(settings._mapping(cfg.get("stt")).get("fish-audio")).get("model") == "transcribe-1":
        line("warn", models.transcribe_1_warning())
    if (settings._mapping(settings._mapping(cfg.get("tts")).get("fish-audio")).get("model") == "s2.1-pro-free"
            and settings.transport_settings().get("allow_free_model", True) is not False):
        warning = models.free_model_warning()
        if warning:
            line("warn", warning)
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
        if command in {"speak", "transcribe", "voices", "balance"}:
            return _script(args)
        if command == "login":
            return _login(args)
        if command == "doctor":
            return _doctor(args)
        if command == "status":
            print(commands.handle("status"))
        elif command == "use":
            key = fish_api_key()
            require(bool(key), commands.NO_KEY)
            base = commands.base_url()
            ident = commands.resolve_title(args.voice_id.strip(), key, base, terminal=True)
            print(commands.use(ident, key, base))
        else:
            args.fish_parser.print_help()
        return 0
    except (FishAudioError, ToolInputError) as exc:
        print(redact(str(exc)))
    except (Exception, SystemExit):
        print("Fish Audio command failed. Check hermes tools or Desktop settings.")
    return 1
