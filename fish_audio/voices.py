"""Voice library operations and opaque, short-lived design receipts."""
import base64
from collections import OrderedDict
import threading
import time
from uuid import uuid4

from . import client, hooks, media
from .tool_support import require, integer, voice_id

KINDS = {"mp3", "wav", "ogg", "webm", "flac", "mp4"}
MIMES = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
         "webm": "audio/webm", "flac": "audio/flac", "mp4": "audio/mp4"}
_DESIGNS = OrderedDict()
_lock = threading.Lock()
_clock = time.monotonic


def _prune():
    for token in list(_DESIGNS):
        if _clock() - _DESIGNS[token]["created"] >= 3600:
            del _DESIGNS[token]


def _item(item, detail=False):
    result = {"id": item.get("id", item.get("_id")), "title": item.get("title"),
              "description": (item.get("description") or "")[:200], "languages": item.get("languages", []),
              "tags": item.get("tags", [])[:8], "like_count": item.get("like_count", 0),
              "task_count": item.get("task_count", 0), "author": (item.get("author") or {}).get("nickname", "")}
    if detail:
        result.update({name: item.get(name) for name in ("visibility", "state", "source")})
        result["samples"] = [{"title": v.get("title"), "text": v.get("text")} for v in item.get("samples", [])]
    return result


def _summary(item, fields):
    return {"id": item.get("id", item.get("_id")), **{name: item.get(name) for name in fields}}


def _form(args):
    title = args.get("title")
    require(isinstance(title, str) and bool(title.strip()), "title is required.")
    data = {"title": title, "type": "tts", "train_mode": "fast", "visibility": "private"}
    data.update({name: args[name] for name in ("description", "tags") if name in args})
    return data


def _upload(paths):
    files = []
    for name in paths:
        path, kind, data = media.validate_input_file(name, max_bytes=20 * 1024 * 1024, kinds=KINDS)
        files.append(("voices", (path.name, data, MIMES[kind])))
    return files


def _features(value, signature):
    if isinstance(value, str):
        return value.replace(signature, "[withheld]")
    if isinstance(value, dict):
        return {k: _features(v, signature) for k, v in value.items() if k != "signature"}
    if isinstance(value, list):
        return [_features(v, signature) for v in value]
    return value


def execute(args, key, base, session):
    action = args.get("action")
    if action in {"search", "mine"}:
        page, size, sort = args.get("page", 1), args.get("page_size", 20), args.get("sort", "score")
        require(integer(page, 1) and integer(size, 1, 20), "page must be positive and page_size must be 1–20.")
        require(sort in {"score", "task_count", "created_at"}, "Invalid voice sort.")
        require((page - 1) * size < 1000, "Fish voice search is limited to the first 1000 results.")
        params = {"page_number": page, "page_size": size, "sort_by": sort}
        for src, dest in (("query", "title"), ("language", "language"), ("tags", "tag")):
            if src in args:
                params[dest] = args[src]
        if action == "mine":
            params["self"] = "true"
        data = client.get_json("/model", params, key, base)
        exact = data.get("total_is_exact", not data.get("window_limited", False))
        limited = data.get("window_limited", False) or exact is False
        return {"items": [_item(item) for item in data.get("items", [])],
                "total": "1000+" if limited else data.get("total", 0), "total_is_exact": False if limited else exact}
    if action in {"get", "update", "delete"}:
        ident = args.get("voice_id")
        require(voice_id(ident), "voice_id must be a valid Fish Audio id.")
        path = f"/model/{ident}"
        if action == "get":
            return _item(client.get_json(path, {}, key, base), detail=True)
        if action == "update":
            data = {name: args[name] for name in ("title", "description", "tags") if name in args}
            require(bool(data), "Provide title, description or tags to update.")
            client.patch_form(path, data, key, base)
        else:
            client.delete(path, key, base)
        return {"id": ident, "action": action}
    if action == "clone":
        require(args.get("consent") is True, "The user must confirm they have the speaker's permission to clone this voice.")
        paths, texts = args.get("sample_paths"), args.get("texts")
        require(isinstance(paths, list) and 1 <= len(paths) <= 20, "Provide 1–20 sample_paths.")
        require(texts is None or isinstance(texts, list) and len(texts) == len(paths) and all(isinstance(v, str) for v in texts),
                "texts must match the sample_paths length.")
        data = _form(args)
        enhance = args.get("enhance_audio_quality", True)
        require(type(enhance) is bool, "enhance_audio_quality must be boolean.")
        data["enhance_audio_quality"] = str(enhance).lower()
        if texts is not None:
            data["texts"] = texts
        item = client.post_multipart("/model", data, _upload(paths), key, base, 600)
        return _summary(item, ("title", "state"))
    if action == "design":
        instruction, n = args.get("instruction"), args.get("n", 2)
        require(isinstance(instruction, str) and 1 <= len(instruction.strip()) <= 500, "instruction must be 1–500 characters.")
        require(integer(n, 1, 4), "n must be 1–4.")
        body = {"instruction": instruction, "n": n}
        body.update({name: args[name] for name in ("reference_text", "language", "seed", "speed") if name in args})
        response = client.post_json("/v1/voice-design", body, key, base, 600)
        candidates = []
        for i, item in enumerate(response.get("candidates", [])[:n]):
            signature = item.get("signature")
            require(isinstance(signature, str) and bool(signature), "Fish Audio returned no design signature; design again.")
            path = media.audio_output_dir() / f"fish-design-{uuid4().hex}-{i}.wav"
            media.atomic_write(path, [base64.b64decode(item["audio_base64"], validate=True)])
            token = uuid4().hex
            with _lock:
                _prune()
                _DESIGNS[token] = {"signature": signature, "text": item.get("text") or args.get("reference_text", ""),
                                   "file_path": str(path), "created": _clock()}
                while len(_DESIGNS) > 64:
                    _DESIGNS.popitem(last=False)
            hooks.record(session, path, False)
            candidates.append({"index": item.get("index", i), "file_path": str(path), "media_tag": f"MEDIA:{path}",
                               "duration_ms": item.get("duration_ms"), "features": _features(item.get("features"), signature),
                               "design_token": token})
        return {"candidates": candidates}
    if action == "save":
        with _lock:
            _prune()
            receipt = _DESIGNS.get(args.get("design_token"))
        require(receipt is not None, "Unknown or expired design token; design again.")
        data = _form(args)
        data.update(texts=[receipt["text"]], voice_design_signatures=[receipt["signature"]])
        item = client.post_multipart("/model", data, _upload([receipt["file_path"]]), key, base, 600)
        return _summary(item, ("title", "source"))
    require(False, "Unknown fish_voices action.")
