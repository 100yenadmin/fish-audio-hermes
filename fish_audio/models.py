"""Fish model capabilities in picker order."""
MODELS = [
    {"id": "s2.1-pro", "display": "S2.1 Pro", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False},
    {"id": "s2.1-pro-free", "display": "S2.1 Pro Free", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": True, "preview": False},
    {"id": "s2-pro", "display": "S2 Pro", "family": "S2", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": False},
    {"id": "s1", "display": "S1", "family": "S1", "tag_style": "paren", "multi_speaker": False, "free": False, "preview": False},
    {"id": "drama-3-preview", "display": "Drama 3 Preview", "family": "Drama", "tag_style": "bracket", "multi_speaker": True, "free": False, "preview": True},
]
MODEL_IDS = frozenset(row["id"] for row in MODELS)
