"""Session-local audio delivery and explicit approval for clone/delete."""
from collections import OrderedDict
import json
from pathlib import Path
import threading
import time
import unicodedata

from .media import audio_output_dir

_PENDING = OrderedDict()
_lock = threading.Lock()
_clock = time.monotonic


def _prune(now):
    for session in list(_PENDING):
        _PENDING[session] = [item for item in _PENDING[session] if now - item[2] < 1800]
        if not _PENDING[session]:
            del _PENDING[session]


def record(session_id, path, voice):
    with _lock:
        now = _clock()
        _prune(now)
        _PENDING.setdefault(session_id, []).append((str(path), bool(voice), now))
        _PENDING.move_to_end(session_id)
        while len(_PENDING) > 256:
            _PENDING.popitem(last=False)


def take(session_id):
    with _lock:
        _prune(_clock())
        return _PENDING.pop(session_id, [])


VOICE_MARKER = "[[audio_as_voice]]"
# Surfaces that play MEDIA: files inline and have no voice-bubble delivery. Messaging gateways strip the marker before
# sending; Desktop (0.23) and the classic CLI print it as literal text, so it never belongs in a reply there.
LOCAL_SURFACES = frozenset({"desktop", "cli", "tui"})


def _without_marker(text):
    lines = []
    for line in text.split("\n"):
        if VOICE_MARKER in line:
            kept = line.replace(VOICE_MARKER + " ", "").replace(VOICE_MARKER, "").rstrip()
            if kept.strip():
                lines.append(kept)  # the line's own indentation stays
            continue
        lines.append(line)
    return "\n".join(lines)


def on_transform_llm_output(response_text="", session_id="", turn_id="", platform="", **kwargs):
    items = take(session_id)
    if not items:
        return None
    local = str(platform or "").lower() in LOCAL_SURFACES
    original = response_text
    if local:
        response_text = _without_marker(response_text)
    root = audio_output_dir().resolve()
    additions, voice = [], False
    for name, is_voice, _ in items:
        path = Path(name)
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        tag = f"MEDIA:{path}"
        voice = voice or is_voice
        if tag not in response_text and tag not in additions:
            additions.append(tag)
    needs_marker = voice and not local and VOICE_MARKER not in response_text
    if not additions and not needs_marker:
        return response_text if response_text != original else None
    tail = "\n".join(additions)
    if needs_marker:
        tail = "\n".join(part for part in (VOICE_MARKER, tail) if part)
    return "\n".join(part for part in (response_text.rstrip(), tail) if part)


def _approval_string(value, limit=80):
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf"} else char for char in str(value))
    return " ".join(text.split())[:limit]


def on_pre_tool_call(tool_name="", args=None, **kwargs):
    try:
        if tool_name == "fish_pronunciations" and args.get("action") in {"delete", "delete_version"}:
            from . import settings  # Lazy: settings imports modules that import this one.
            if settings.operator_account():
                return None
            dictionary = _approval_string(args.get("dictionary_id", ""), 128)
            version = _approval_string(args.get("version_id", ""), 128) if args["action"] == "delete_version" else None
            target = f"dictionary {json.dumps(dictionary)}"
            if version is not None:
                target += f" version {json.dumps(version)}"
            return {"action": "approve", "message": f"Fish Audio: delete pronunciation {target}",
                    "rule_key": f"fish-audio:pronunciations:delete:{dictionary}" + (f":{version}" if version is not None else "")}
        if tool_name != "fish_voices":
            return None
        if args.get("action") == "clone":
            title = json.dumps(_approval_string(args.get("title", "")))
            paths = args.get("sample_paths")
            count = len(paths) if isinstance(paths, list) else "?"
            from . import settings  # Lazy, like errors.py: settings imports modules that import this one.
            account = "this agent's voice account" if settings.operator_account() else "your Fish account"
            message = (f'Fish Audio: clone a voice named {title} from '
                       f'{count} sample file(s) to {account}')
            rule = "fish-audio:clone"
        elif args.get("action") == "delete":
            from . import settings
            if settings.operator_account():
                from .voices import ACCOUNT_VOICES  # Lazy: voices imports this module.
                return {"action": "block", "message": ACCOUNT_VOICES}  # Never an approval prompt for a refused delete.
            voice = _approval_string(args.get("voice_id", ""))
            message, rule = f"Fish Audio: permanently delete voice {json.dumps(voice)}", f"fish-audio:delete:{voice}"
        else:
            return None
        return {"action": "approve", "message": message, "rule_key": rule}
    except Exception:
        return None
