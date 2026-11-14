"""Shared validation and JSON envelopes for model-facing tools."""
import json
import re

from . import settings
from .errors import FishAudioError
from .media import InputFileError
from .secrets import fish_api_key, redact
from .tts import setup_message


class ToolInputError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ToolInputError(message)


def integer(value, lo, hi=None):
    return type(value) is int and value >= lo and (hi is None or value <= hi)


def voice_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value) is not None


def run_tool(name, operation, args, session_id="", **options):
    """Validate model-facing keys without restricting the CLI's internal arguments."""
    from .tools import SCHEMAS

    def checked(fields, key, base, session):
        accepted = SCHEMAS[name]
        unknown = sorted(fields.keys() - accepted.keys())
        require(not unknown, f"Unknown parameter(s) for {name}: {', '.join(unknown)}. "
                f"Accepted: {', '.join(sorted(accepted))}.")
        return operation(fields, key, base, session)
    return run(checked, args, session_id, **options)


def run(operation, args, session_id="", *, allow_no_key=False):
    try:
        key = fish_api_key()
        require(bool(key) or allow_no_key, setup_message())
        require(isinstance(args, dict), "Tool arguments must be an object.")
        base = settings._base_url(settings.transport_settings().get("base_url", settings.DEFAULT_BASE_URL))
        result = operation(args, key, base, session_id)
        result.setdefault("success", True)
    except (FishAudioError, InputFileError, ToolInputError) as exc:
        result = {"success": False, "error": redact(str(exc))}
    except Exception:
        result = {"success": False, "error": "Fish Audio could not complete this call. Check the inputs and try again."}
    return json.dumps(result, ensure_ascii=False)
