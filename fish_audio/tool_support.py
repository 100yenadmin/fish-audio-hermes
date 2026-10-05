"""Shared validation and JSON envelopes for model-facing tools."""
import json
import re

from . import settings
from .errors import FishAudioError
from .media import InputFileError
from .secrets import fish_api_key, redact
from .tts import SETUP_MESSAGE


class ToolInputError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ToolInputError(message)


def integer(value, lo, hi=None):
    return type(value) is int and value >= lo and (hi is None or value <= hi)


def voice_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value) is not None


def run(operation, args, session_id=""):
    try:
        key = fish_api_key()
        require(bool(key), SETUP_MESSAGE)
        require(isinstance(args, dict), "Tool arguments must be an object.")
        base = settings._base_url(settings.transport_settings().get("base_url", settings.DEFAULT_BASE_URL))
        result = operation(args, key, base, session_id)
        result["success"] = True
    except (FishAudioError, InputFileError, ToolInputError) as exc:
        result = {"success": False, "error": redact(str(exc))}
    except Exception:
        result = {"success": False, "error": "Fish Audio could not complete this call. Check the inputs and try again."}
    return json.dumps(result, ensure_ascii=False)
