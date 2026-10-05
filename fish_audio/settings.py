"""Resolve synthesis settings afresh for each active profile and call."""
import ipaddress
import logging
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

from .models import MODEL_IDS

DEFAULT_BASE_URL = "https://api.fish.audio"
logger = logging.getLogger(__name__)
_warned = set()
# (type, minimum, maximum, choices); bounds follow the vendored OpenAPI.
KNOBS = {
    "temperature": (float, 0, 1, None),
    "top_p": (float, 0, 1, None),
    "latency": (str, None, None, {"normal", "balanced", "low"}),
    "normalize": (bool, None, None, None),
    "chunk_length": (int, 100, 300, None),
    "min_chunk_length": (int, 0, 100, None),
    "sample_rate": (int, None, None, None),
    "mp3_bitrate": (int, None, None, {64, 128, 192}),
    "opus_bitrate": (int, None, None, {-1000, 24000, 32000, 48000, 64000}),
    "volume": (float, None, None, None),
    "normalize_loudness": (bool, None, None, None),
    "max_new_tokens": (int, None, None, None),
    "repetition_penalty": (float, None, None, None),
    "condition_on_previous_chunks": (bool, None, None, None),
    "early_stop_threshold": (float, 0, 1, None),
    "features": (list, None, None, None),
    "pronunciation_dictionary": (list, None, None, None),
}


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _config():
    try:
        from hermes_cli.config import load_config
        return _mapping(load_config())
    except Exception:
        return {}


def _warn(key):
    if key not in _warned:
        _warned.add(key)
        logger.warning("Ignoring invalid Fish Audio setting: %s", key)


def _dictionary(value):
    if len(value) > 3:
        return False
    forms = set()
    for entry in value:
        if not isinstance(entry, dict):
            return False
        if set(entry) == {"id", "version"}:
            forms.add("ref")
            if not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v) for v in entry.values()):
                return False
        elif set(entry) == {"items"} and isinstance(entry["items"], list):
            forms.add("inline")
            if len(entry["items"]) > 5000:
                return False
            for item in entry["items"]:
                if not isinstance(item, dict) or not {"key", "value"} <= item.keys() or item.keys() - {"key", "value", "case_sensitive"}:
                    return False
                for name, limit in (("key", 256), ("value", 1024)):
                    v = item[name]
                    if not isinstance(v, str) or not 1 <= len(v) <= limit or any(tag in v for tag in ("<|phoneme_start|>", "<|phoneme_end|>")):
                        return False
                if "case_sensitive" in item and type(item["case_sensitive"]) is not bool:
                    return False
        else:
            return False
    return len(forms) <= 1


def _valid(key, value):
    typ, lo, hi, choices = KNOBS[key]
    if typ is float:
        valid = _number(value)
    else:
        valid = type(value) is typ
    if not valid:
        return False
    if lo is not None and value < lo or hi is not None and value > hi:
        return False
    if choices is not None and value not in choices:
        return False
    if key == "features":
        return all(isinstance(v, str) for v in value)
    if key == "pronunciation_dictionary":
        return _dictionary(value)
    return True


def _number(value):
    # Integers are finite without converting enormous YAML values to float.
    return type(value) is int or type(value) is float and math.isfinite(value)


def _base_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return DEFAULT_BASE_URL
    try:
        url = urlsplit(value)
        # Check the port too: urlsplit otherwise accepts invalid port strings.
        url.port
        if not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment:
            return DEFAULT_BASE_URL
        if url.scheme == "https":
            return value.rstrip("/")
        loopback = url.hostname.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            pass
        if url.scheme == "http" and loopback:
            return value.rstrip("/")
    except ValueError:
        pass
    return DEFAULT_BASE_URL


def resolve_tts(call_voice, call_model, call_speed, call_format, output_path):
    config = _config()
    nested = _mapping(_mapping(config.get("tts")).get("fish-audio"))
    transport = _mapping(_mapping(_mapping(_mapping(config.get("plugins")).get("entries")).get("fish-audio")).get("settings"))
    voice = nested.get("voice")
    if not isinstance(voice, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", voice):
        voice = call_voice if isinstance(call_voice, str) and re.fullmatch(r"[0-9a-f]{32}", call_voice) else None
    allow_free = transport.get("allow_free_model", True) is not False
    model = next((v for v in (nested.get("model"), call_model) if isinstance(v, str) and v in MODEL_IDS), None)
    if model is None or model == "s2.1-pro-free" and not allow_free:
        model = "s2.1-pro-free" if allow_free else "s2.1-pro"
    speed = call_speed if call_speed is not None else nested.get("speed", 1.0)
    if not _number(speed):
        _warn("speed")
        speed = 1.0
    path = Path(output_path)
    suffix = path.suffix.lower()
    formats = {".ogg": "opus", ".opus": "opus", ".mp3": "mp3", ".wav": "wav", ".flac": "wav"}
    fmt = formats.get(suffix)
    if fmt is None:
        fmt = call_format if isinstance(call_format, str) and call_format in {"mp3", "wav", "opus"} else "mp3"
        path = path.with_suffix(".ogg" if fmt == "opus" else f".{fmt}")
    elif suffix == ".flac":
        path = path.with_suffix(".wav")
    params = {"model": model, "format": fmt, "prosody": {"speed": max(0.5, min(2.0, speed))}, "base_url": _base_url(transport.get("base_url", DEFAULT_BASE_URL))}
    if voice is not None:
        params["reference_id"] = voice
    for key in KNOBS:
        if key not in nested:
            continue
        value = nested[key]
        if not _valid(key, value):
            _warn(key)
        elif key in {"volume", "normalize_loudness"}:
            params["prosody"][key] = value
        else:
            params[key] = value
    # Whole-file REST synthesis; the streaming bridge is a later stage.
    return params, str(path)
