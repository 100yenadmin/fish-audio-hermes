"""Safe, actionable Fish failures; never forward arbitrary response messages."""
import json
import re

from .secrets import redact

KEY_URL = "https://fish.audio/app/api-keys"
BILLING_URL = "https://fish.audio/app/developers/billing"


class FishAudioError(Exception):
    def __init__(self, kind, status, request_id, message):
        self.kind = kind
        self.status = status
        self.request_id = redact(request_id) if request_id else None
        self.message = redact(message)
        super().__init__(self.message)

    def __str__(self):
        return self.message + (f" (request id {self.request_id})" if self.request_id else "")


def response_error(status, headers=None, body=b"", model=None, key=""):
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    messages = {
        400: ("invalid_request", "Fish Audio rejected the synthesis request. Check the voice and settings."),
        401: ("credential", f"Set a valid Fish Audio API key: {KEY_URL}"),
        403: ("credential", f"Check your Fish Audio API key and access: {KEY_URL}"),
        402: ("quota", f"Top up Fish Audio API credits: {BILLING_URL}; app plan credits are separate from API credits."),
        404: ("not_found", "Fish Audio could not find the requested voice or model."),
        413: ("too_large", "The Fish Audio request is too large. Use less text or smaller references."),
        415: ("unsupported_media", "Fish Audio does not support this audio format."),
        429: ("rate_limit", "Fish Audio concurrency limit reached. Top-up tiers: <$100: 5, ≥$100: 15, ≥$1k: 50 concurrent requests. Retry later."),
    }
    kind, message = messages.get(status, ("availability", "Fish Audio is unavailable. Try again later."))
    code = data.get("code")
    if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code):
        message += f" Code: {code}."
    if model == "s2.1-pro-free" and status in {402, 403, 429}:
        message += f" Switch to s2.1-pro and top up: {BILLING_URL}"
    request_id = (headers or {}).get("x-request-id") or data.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        request_id = None
    if key:
        message = message.replace(key, "[redacted]")
        request_id = request_id.replace(key, "[redacted]") if request_id else None
    return FishAudioError(kind, status, request_id, message)
