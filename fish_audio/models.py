"""Fish model capabilities in picker order."""
from datetime import date, datetime, timezone

MODELS = [
    {"id": "s2.1-pro", "display": "S2.1 Pro", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False},
    {"id": "s2.1-pro-free", "display": "S2.1 Pro Free", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": True, "preview": False, "ends": "2026-11-30"},
    {"id": "s2-pro", "display": "S2 Pro", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False},
    {"id": "s1", "display": "S1 (deprecated)", "family": "S1", "tag_style": "paren", "multi_speaker": False, "free": False, "preview": False, "deprecated": True, "retires": "2026-12-31"},
    {"id": "drama-3", "display": "Drama 3", "family": "Drama", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False},
]
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
