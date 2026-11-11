"""Free, account-scoped pronunciation dictionary management."""
import re

from . import client, settings
from .errors import FishAudioError
from .tool_support import integer, require

ROOT = "/v1/pronunciation-dictionaries"
CONFLICT = "Someone published a newer version; fetch it, merge, and publish again"
ACCOUNT_DICTIONARIES = "Pronunciation dictionaries on this agent's account are managed by its operator."


def _ident(value, name):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value),
            f"{name} must be a dictionary or version id (1–128 letters, digits, _ or -).")
    return value


def validate_refs(refs):
    require(isinstance(refs, list) and settings._dictionary(refs)
            and all(set(ref) == {"id", "version"} for ref in refs),
            "refs/dictionaries must contain at most 3 {id, version} references, or [] to clear.")


def verify_refs(refs, key, base):
    validate_refs(refs)
    for ref in refs:
        try:
            client.get_json(f"{ROOT}/{ref['id']}/versions/{ref['version']}", {}, key, base)
        except FishAudioError as exc:
            if exc.status == 404:
                raise FishAudioError("not_found", 404, exc.request_id,
                                     "Unknown pronunciation dictionary or version; fetch its published ids and try again.") from None
            raise


def _pagination(args):
    params = {k: args[k] for k in ("cursor", "page", "page_size", "include_total") if k in args}
    require(not (params.get("cursor") is not None and params.get("page") is not None),
            "Use cursor or page, not both.")
    require("cursor" not in params or isinstance(params["cursor"], str), "cursor must be a string.")
    require("page" not in params or integer(params["page"], 1), "page must be a positive integer.")
    require("page_size" not in params or integer(params["page_size"], 1, 100), "page_size must be 1–100.")
    require("include_total" not in params or type(params["include_total"]) is bool, "include_total must be boolean.")
    return params


def execute(args, key, base, session):
    from . import commands
    action = args.get("action")
    require(action in {"list", "get", "versions", "get_version", "create", "publish", "delete", "delete_version", "use"},
            "Unknown fish_pronunciations action.")
    require(action not in {"list", "create", "publish", "delete", "delete_version"} or not settings.operator_account(),
            ACCOUNT_DICTIONARIES)
    if action == "use":
        refs = args.get("refs")
        validate_refs(refs)
        commands.require_unmanaged("pronunciation_dictionary")
        verify_refs(refs, key, base)
        def change(cfg):
            cfg.setdefault("tts", {}).setdefault("fish-audio", {})["pronunciation_dictionary"] = refs
        commands.write_config(change)
        return {"changed": True, "refs": refs}
    if action == "list":
        return client.get_json(ROOT, _pagination(args), key, base)
    if action in {"create", "publish"}:
        description = args.get("description", "")
        require(isinstance(description, str) and len(description) <= 1024, "description must be at most 1024 characters.")
    if action == "create":
        name = args.get("name")
        require(isinstance(name, str) and 1 <= len(name) <= 128, "name must contain 1–128 characters.")
        return client.post_json(ROOT, {"name": name, "description": description}, key, base, 60)
    path = ROOT + "/" + _ident(args.get("dictionary_id"), "dictionary_id")
    if action == "get":
        return client.get_json(path, {}, key, base)
    if action == "versions":
        return client.get_json(path + "/versions", _pagination(args), key, base)
    if action == "publish":
        rules = args.get("rules")
        require(isinstance(rules, list) and settings._dictionary([{"items": rules}]),
                "Invalid rules: at most 5000; keys 1–256 characters, values 1–1024; follow phoneme/alias rules.")
        if "expected_latest_version_id" in args:
            expected = args["expected_latest_version_id"]
            if expected is not None:
                _ident(expected, "expected_latest_version_id")
        else:
            expected = client.get_json(path, {}, key, base).get("latest_version_id")
        try:
            return client.post_json(path + "/versions", {"items": rules, "description": description,
                                    "expected_latest_version_id": expected}, key, base, 60)
        except FishAudioError as exc:
            if exc.status == 409:
                raise FishAudioError("conflict", 409, exc.request_id, CONFLICT) from None
            raise
    if action in {"get_version", "delete_version"}:
        version = _ident(args.get("version_id"), "version_id")
        if action == "delete_version":
            latest = client.get_json(path, {}, key, base).get("latest_version_id")
            require(version != latest, "The latest dictionary version cannot be deleted. Publish a newer version first.")
        path += "/versions/" + version
    if action == "get_version":
        return client.get_json(path, {}, key, base)
    client.delete(path, key, base)
    return {"deleted": True}
