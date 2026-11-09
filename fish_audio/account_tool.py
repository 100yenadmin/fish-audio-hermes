"""Free account reads and offline estimates from Fish's dated pricing catalog."""
import math

from . import commands, models, settings, tags
from .tool_support import require

NOTE = "Estimate from Fish's published price; your bill is what Fish charges."


def estimate(args, *, operator=False, now=None):
    fields = [name for name in ("text", "characters", "bytes", "audio_seconds") if name in args]
    require(len(fields) == 1, "Provide exactly one of text, characters, bytes or audio_seconds.")
    field = fields[0]
    value = args[field]
    notes = []
    if field == "audio_seconds":
        require(type(value) in {int, float} and math.isfinite(value) and value >= 0,
                "audio_seconds must be a finite non-negative number.")
        model = args.get("asr_model", "transcribe-1-pro")
        require(model in {"transcribe-1-pro", "transcribe-1"}, "Choose a supported ASR model.")
        model = models.effective_stt_model(model, now.date() if now else None)
        result, units = {"model": model, "audio_seconds": value}, value / 3600
    else:
        requested = args.get("model")
        require(requested is None or requested in models.MODEL_IDS, "Choose a supported Fish model.")
        # A blank key makes the shared resolver strictly offline, even for an unconfigured wallet default.
        model, source = settings.resolve_model_source(requested, key="", base_url="", prefer_call=True)
        model = models.effective_model(model, now.date() if now else None)
        if source == "wallet":
            notes.append("Wallet default was not read; using s2.1-pro.")
        if field == "text":
            require(isinstance(value, str), "text must be a string.")
            row = next(row for row in models.MODELS if row["id"] == model)
            count = len(tags.adapt_tags(value, row["family"]).encode("utf-8"))
            notes.append("Counts UTF-8 bytes after the same emotion-tag adaptation as fish_speak; server normalization and pronunciation replacements may change billing.")
        else:
            require(type(value) is int and value >= 0, f"{field} must be a non-negative integer.")
            count = value
            if field == "characters":
                notes.append("Assumes one UTF-8 byte per character; pass text or bytes for non-ASCII accuracy.")
        result, units = {"model": model, "billable_bytes": count}, count / 1_000_000
    if not operator:
        price = next(row for row in models.pricing(now) if row["id"] == model)
        result.update(usd_estimate=units * price["price"]["usd"], unit_price=price["price"],
                      price_as_of=price["price_as_of"], source=price["source"])
        if model == "s2.1-pro-free":
            notes.append(price["notes"])
        result["note"] = " ".join([NOTE, *notes])
    else:
        result["note"] = " ".join(notes) or "Billable units only; voice billing is handled by the operator."
    return result


def execute(args, key, base, session):
    action = args.get("action")
    require(action in {"balance", "models", "estimate"}, "Unknown fish_account action.")
    operator = settings.operator_account()
    if action == "models":
        return {"models": models.pricing(include_prices=not operator)}
    if action == "estimate":
        return estimate(args, operator=operator)
    if operator:
        return {"operator_managed": True, "message": "Voice billing for this agent is handled by its operator."}
    return commands.balance_result(key, base)
