"""Session-local audio delivery and explicit approval for clone/delete."""
from collections import OrderedDict
from pathlib import Path
import threading
import time

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


def on_transform_llm_output(response_text="", session_id="", turn_id="", **kwargs):
    items = take(session_id)
    if not items:
        return None
    root = audio_output_dir().resolve()
    additions, voice = [], False
    for name, is_voice, _ in items:
        path = Path(name)
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        tag = f"MEDIA:{path}"
        if tag not in response_text and tag not in additions:
            additions.append(tag)
            voice = voice or is_voice
    if not additions:
        return None
    tail = "\n".join(additions)
    if voice and "[[audio_as_voice]]" not in response_text:
        tail = "[[audio_as_voice]]\n" + tail
    return "\n".join(part for part in (response_text.rstrip(), tail) if part)


def on_pre_tool_call(tool_name="", args=None, **kwargs):
    try:
        if tool_name != "fish_voices":
            return None
        if args.get("action") == "clone":
            message = (f'Fish Audio: clone a voice named "{args.get("title", "")}" from '
                       f'{len(args.get("sample_paths", []))} sample file(s) to your Fish account')
            rule = "fish-audio:clone"
        elif args.get("action") == "delete":
            voice = args.get("voice_id", "")
            message, rule = f"Fish Audio: permanently delete voice {voice}", f"fish-audio:delete:{voice}"
        else:
            return None
        return {"action": "approve", "message": message, "rule_key": rule}
    except Exception:
        return None
