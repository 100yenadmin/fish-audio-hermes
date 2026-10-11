"""Safe, actionable Fish failures; never forward arbitrary response messages."""
import json
import re

from .secrets import redact

KEY_URL = "https://fish.audio/app/api-keys"
BILLING_URL = "https://fish.audio/app/developers/billing"


class FishAudioError(Exception):
    def __init__(self, kind, status, request_id, message, *, trace_id=None, code=None, hint=None, standard=False, text=None):
        self.kind = kind
        self.status = status
        self.request_id = redact(request_id) if request_id else None
        self.message = redact(message)
        self.trace_id = redact(trace_id) if trace_id else None
        self.code = redact(code) if code else None
        self.hint = hint
        self.standard = standard
        # The Desktop bundle key of the sentence chosen above (operator wording, 401 vs 403), or None to keep this text.
        self.text = text
        super().__init__(self.message)

    def __str__(self):
        return (self.message
                + (f" (request id {self.request_id})" if self.request_id else "")
                + (f" (Fish trace {self.trace_id})" if self.trace_id else ""))


def response_error(status, headers=None, body=b"", model=None, key="", *, defaulted=False):
    try:
        from . import settings  # Lazy: settings also uses response_error through account.
        operator = settings.operator_account()
    except Exception:
        operator = False
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError, TypeError):
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
    # Each fixed sentence's Desktop bundle key, so Desktop shows the sentence chosen here in its own language.
    text_keys = {message: kind for kind, message in messages.values()}
    text_keys[messages[403][1]] = "credentialAccess"
    if operator:
        for status_code, text in ((401, "The voice service's credentials were rejected. Contact the operator of this agent."),
                                  (403, "The voice service's credentials were rejected. Contact the operator of this agent."),
                                  (402, "The voice service is out of credit. Contact the operator of this agent."),
                                  (429, "The voice service is busy. Try again shortly.")):
            messages[status_code] = (messages[status_code][0], text)
        text_keys.update({messages[401][1]: "credentialOperator", messages[402][1]: "quotaOperator",
                          messages[429][1]: "rateLimitOperator"})
    if status == 400 and model in {"transcribe-1", "transcribe-1-pro"}:
        message = "Fish Audio could not decode this audio."
        if model == "transcribe-1":
            message += " transcribe-1-pro accepts more formats (including WebM)."
        messages[400] = ("invalid_request", message)
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    kind, message = messages.get(status, ("availability", "Fish Audio is unavailable. Try again later."))
    code = next((v for v in (headers.get("x-fish-error-code"), data.get("code"))
                 if isinstance(v, str) and re.fullmatch(r"[a-z_]{1,64}", v)), None)
    by_kind = {k: m for k, m in messages.values()}
    by_kind["availability"] = "Fish Audio is unavailable. Try again later."
    by_kind["voice_not_found"] = ("The voice id was not found, or is private to another account. "
                                 "Browse voices at https://fish.audio/discovery.")
    resolved_kind = "credential" if code == "invalid_api_key" else code
    known_message = data.get("message")
    if resolved_kind in by_kind:
        kind, message = resolved_kind, by_kind[resolved_kind]
    elif status in {None, 400, 404} and isinstance(known_message, str) and known_message.lower() in {"reference not found", "model not found"}:
        kind, message = "voice_not_found", by_kind["voice_not_found"]
    text_keys.update({by_kind["availability"]: "availability", by_kind["voice_not_found"]: "voice_not_found"})
    text_key = text_keys.get(message)
    if code:
        message += f" Code: {code}."
    hint = None
    if not operator and ((model == "s2.1-pro-free" and status in {402, 403, 429}) or (defaulted and status == 402 and model != "s2.1-pro")):
        hint = "paid_model"
        message += f" Switch to s2.1-pro and top up: {BILLING_URL}"
    request_id = headers.get("x-request-id") or data.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        request_id = None
    trace_id = headers.get("x-fish-trace-id")
    if not trace_id:
        cloud_trace = headers.get("x-cloud-trace-context")
        trace_id = cloud_trace.split("/", 1)[0] if isinstance(cloud_trace, str) else None
        if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-fA-F]+", trace_id):
            trace_id = None
    if not isinstance(trace_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", trace_id):
        trace_id = None
    if key:
        message = message.replace(key, "[redacted]")
        code = code.replace(key, "[redacted]") if code else None
        request_id = request_id.replace(key, "[redacted]") if request_id else None
        trace_id = trace_id.replace(key, "[redacted]") if trace_id else None
    return FishAudioError(kind, status, request_id, message, trace_id=trace_id, code=code, hint=hint, standard=True,
                          text=text_key)
