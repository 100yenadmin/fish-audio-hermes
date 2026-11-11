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


def record(session_id, path, delivery=None):
    if isinstance(delivery, bool):  # Existing preview/audition callers.
        delivery = "voice" if delivery else None
    with _lock:
        now = _clock()
        _prune(now)
        _PENDING.setdefault(session_id, []).append((str(path), delivery, now))
        _PENDING.move_to_end(session_id)
        while len(_PENDING) > 256:
            _PENDING.popitem(last=False)


def take(session_id):
    with _lock:
        _prune(_clock())
        return _PENDING.pop(session_id, [])


VOICE_MARKER = "[[audio_as_voice]]"
# Hermes applies the voice marker to every audio file in the reply, so a "file" delivery is simply audio without it
# (Hermes 0.23's [[as_document]] routes images only). Local surfaces also lose a document marker a model writes itself.
STRIPPED_MARKERS = (VOICE_MARKER, "[[as_document]]")
# Surfaces that play MEDIA: files inline. Messaging gateways strip delivery markers before sending;
# Desktop (0.23) and the classic CLI print them as literal text, so neither belongs in a reply there.
LOCAL_SURFACES = frozenset({"desktop", "cli", "tui"})


def _without_marker(text, markers=STRIPPED_MARKERS):
    lines = []
    for line in text.split("\n"):
        if any(marker in line for marker in markers):
            kept = line
            for marker in markers:
                kept = kept.replace(marker + " ", "").replace(marker, "")
            kept = kept.rstrip()
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
    additions, deliveries = [], set()
    for name, delivery, _ in items:
        path = Path(name)
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        tag = f"MEDIA:{path}"
        deliveries.add(delivery)
        if tag not in response_text and tag not in additions:
            additions.append(tag)
    # The marker covers the whole reply: an explicit file request this turn must not become a voice note, including
    # through a marker the model copied from a voice clip's media_tag.
    if "file" in deliveries and not local:
        response_text = _without_marker(response_text, (VOICE_MARKER,))
    needs_marker = "voice" in deliveries and "file" not in deliveries and not local and VOICE_MARKER not in response_text
    if not additions and not needs_marker:
        return response_text if response_text != original else None
    tail = "\n".join(([VOICE_MARKER] if needs_marker else []) + additions)
    return "\n".join(part for part in (response_text.rstrip(), tail) if part)


def _approval_string(value):
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf"} else char for char in str(value))
    return " ".join(text.split())[:80]


def on_pre_tool_call(tool_name="", args=None, **kwargs):
    try:
        if tool_name == "fish_pronunciations" and args.get("action") in {"delete", "delete_version"}:
            dictionary = _approval_string(args.get("dictionary_id", ""))
            version = _approval_string(args.get("version_id", "")) if args["action"] == "delete_version" else None
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
