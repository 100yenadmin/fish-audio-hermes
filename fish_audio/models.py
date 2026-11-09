"""Fish model capabilities in picker order."""
from datetime import date, datetime, timezone

MODEL_CATALOG = [
    {"id": "s2.1-pro", "display": "S2.1 Pro", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False, "kind": "tts", "use": "Expressive speech", "usd": 15, "unit": "1,000,000 UTF-8 bytes"},
    {"id": "s2.1-pro-free", "display": "S2.1 Pro Free", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": True, "preview": False, "ends": "2026-11-30", "kind": "tts", "use": "Expressive speech", "usd": 0, "unit": "1,000,000 UTF-8 bytes"},
    {"id": "s2-pro", "display": "S2 Pro", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False, "kind": "tts", "use": "Expressive speech", "usd": 15, "unit": "1,000,000 UTF-8 bytes"},
    {"id": "s1", "display": "S1 (deprecated)", "family": "S1", "tag_style": "paren", "multi_speaker": False, "free": False, "preview": False, "deprecated": True, "retires": "2026-12-31", "kind": "tts", "use": "Expressive speech", "usd": 15, "unit": "1,000,000 UTF-8 bytes"},
    {"id": "drama-3", "display": "Drama 3", "family": "Drama", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False, "kind": "tts", "use": "Expressive speech", "usd": 50, "unit": "1,000,000 UTF-8 bytes"},
    {"id": "transcribe-1-pro", "kind": "asr", "use": "Transcription with speaker turns and timestamps", "usd": 0.36, "unit": "audio hour"},
    {"id": "transcribe-1", "kind": "asr", "use": "Legacy transcription", "usd": 0.36, "unit": "audio hour", "retires": "2026-12-31"},
    {"id": "transcribe-stream-1", "kind": "realtime_stt", "use": "Realtime transcription (Preview)", "usd": 0.36, "unit": "session hour"},
    {"id": "voice-design-1", "kind": "voice_design", "use": "Voice design", "usd": 0.01, "unit": "successful request"},
]
# TTS picker view of the single catalog; other model kinds never enter synthesis.
MODELS = [row for row in MODEL_CATALOG if row["kind"] == "tts"]
PRICE_AS_OF = "2026-11-09"
PRICE_SOURCE = "https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits"
MODEL_ALIASES = {"drama-3-preview": "drama-3"}


def canonical_model(model_id):
    return MODEL_ALIASES.get(model_id, model_id)


MODEL_IDS = frozenset(row["id"] for row in MODELS)


def free_model_phase(today=None):
    today = today if today is not None else datetime.now(timezone.utc).date()
    ends = date.fromisoformat(next(row["ends"] for row in MODELS if row["id"] == "s2.1-pro-free"))
    return "ended" if today > ends else "ending" if today >= ends.replace(day=1) else "active"


def display(row, today=None):
    """The picker name; after the free period it says so, so the name alone does not advertise a free model."""
    if row["id"] == "s2.1-pro-free" and free_model_phase(today) == "ended":
        return row["display"] + " (free period ended)"
    return row["display"]


def free_model_warning(today=None):
    if free_model_phase(today) == "ended":
        return ("s2.1-pro-free: Fish announced its free period would end on 30 November 2026, so Fish may refuse these requests or "
                "serve and bill them as S2.1 Pro. Choose s2.1-pro.")
    return None


def effective_model(model_id, today=None):
    model_id = canonical_model(model_id)
    today = today if today is not None else datetime.now(timezone.utc).date()
    return "s2.1-pro" if model_id == "s1" and today.isoformat() >= "2026-12-31" else model_id


def s1_warning():
    return ("S1 has retired; requests use S2.1 Pro." if effective_model("s1") != "s1" else
            "S1 is deprecated: Fish retires it on 2026-12-31 and then serves it as S2.1 Pro. Choose s2.1-pro.")


def effective_stt_model(model_id, today=None):
    today = today if today is not None else datetime.now(timezone.utc).date()
    return "transcribe-1-pro" if model_id == "transcribe-1" and today.isoformat() >= "2026-12-31" else model_id


def transcribe_1_warning():
    return ("transcribe-1 has retired; requests use transcribe-1-pro." if effective_stt_model("transcribe-1") != "transcribe-1" else
            "transcribe-1 is deprecated: Fish retires it on 2026-12-31 and then serves it as transcribe-1-pro at the same price. Choose transcribe-1-pro.")


def pricing(now=None, *, include_prices=True):
    now = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    rows = []
    for row in MODEL_CATALOG:
        ident = row["id"]
        notes = ("List price $50; launch price $10 until 2026-12-01T08:00:00Z." if ident == "drama-3" else
                 "Free announced through 2026-11-30; requests may improve Fish's models." if ident == "s2.1-pro-free" else
                 "From 2026-12-31 served and billed as s2.1-pro." if ident == "s1" else
                 "From 2026-12-31 use transcribe-1-pro." if ident == "transcribe-1" else
                 "Preview." if row["kind"] == "realtime_stt" else "")
        result = {k: row[k] for k in ("id", "kind", "use", "retires", "ends") if k in row}
        result["notes"] = notes if include_prices else (
            "Deprecated; from 2026-12-31 use s2.1-pro." if ident == "s1" else
            "Free period announced through 2026-11-30; requests may improve Fish's models." if ident == "s2.1-pro-free" else
            "Launch period ends 2026-12-01T08:00:00Z." if ident == "drama-3" else notes)
        if include_prices:
            usd = 10 if ident == "drama-3" and now.isoformat() < "2026-12-01T08:00:00+00:00" else row["usd"]
            result.update(price={"usd": usd, "unit": row["unit"]}, price_as_of=PRICE_AS_OF, source=PRICE_SOURCE)
        rows.append(result)
    return rows
