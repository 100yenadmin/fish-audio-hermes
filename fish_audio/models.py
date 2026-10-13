"""Fish model capabilities in picker order."""
from datetime import datetime, timezone

MODELS = [
    {"id": "s2.1-pro", "display": "S2.1 Pro", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False},
    {"id": "s2.1-pro-free", "display": "S2.1 Pro Free", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": True, "preview": False},
    {"id": "s2-pro", "display": "S2 Pro", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False},
    {"id": "s1", "display": "S1 (deprecated)", "family": "S1", "tag_style": "paren", "multi_speaker": False, "free": False, "preview": False, "deprecated": True, "retires": "2026-12-31"},
    {"id": "drama-3", "display": "Drama 3", "family": "Drama", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False},
    {"id": "drama-3-preview", "display": "Drama 3 Preview", "family": "Drama", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": True},
]
MODEL_IDS = frozenset(row["id"] for row in MODELS)


def effective_model(model_id, today=None):
    today = today if today is not None else datetime.now(timezone.utc).date()
    return "s2.1-pro" if model_id == "s1" and today.isoformat() >= "2026-12-31" else model_id


def s1_warning():
    return ("S1 has retired; requests use S2.1 Pro." if effective_model("s1") != "s1" else
            "S1 is deprecated: Fish retires it on 2026-12-31 and then serves it as S2.1 Pro. Choose s2.1-pro.")
