"""Read credentials in the current Hermes profile, never across profiles."""
import os
import re


def fish_api_key() -> str:
    try:
        from agent.secret_scope import UnscopedSecretError, get_secret
    except ImportError:
        return os.environ.get("FISH_API_KEY", "").strip()
    try:
        return (get_secret("FISH_API_KEY") or "").strip()
    except UnscopedSecretError:
        return ""


def redact(text: str) -> str:
    key = fish_api_key()
    if key:
        text = text.replace(key, "[redacted]")
    return re.sub(r"sk-fish-\S+", "[redacted]", text)
