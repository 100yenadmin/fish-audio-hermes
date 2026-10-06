"""Fish Audio gateway routes, mounted by the Hermes dashboard at ``/api/plugins/fish-audio/``.

The dashboard loads this file by path, with no parent package. It loads the plugin's own ``fish_audio`` package
by file path under a private module name, so the Desktop Voices page runs the same code as the model tools and
``/fish`` commands. Authentication comes from the dashboard; a compatibility dependency enters its per-request profile scope
when the host does not already do so. Every route resolves the key and settings of the requesting profile on each call and answers
protocol errors in-band as ``{ok: false, kind, message}``.
"""

from __future__ import annotations

import base64
import binascii
from decimal import Decimal
from functools import wraps
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import threading
import time
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

PLUGIN_NAME = "fish-audio"
VERSION = "1.3.1"

_HOST_SCOPES = False


def _host_scopes_plugin_routes():
    global _HOST_SCOPES
    _HOST_SCOPES = _HOST_SCOPES or hasattr(sys.modules.get("hermes_cli.web_server_dashboard"),
                                          "_plugin_route_secret_scope")
    return _HOST_SCOPES


async def _request_scope(profile: Optional[str] = None):
    """Enter Hermes's home + secret scope on older dashboards; async like the upstream dependency."""
    if _host_scopes_plugin_routes():
        yield
        return
    try:
        from hermes_cli.web_server_profiles import _config_profile_scope
    except ImportError:  # older forks serve one profile per process
        yield
        return
    with _config_profile_scope(profile):
        yield


router = APIRouter(dependencies=[Depends(_request_scope)])

ROOT = Path(__file__).resolve().parents[1]
# A private name: never ``hermes_plugins.*`` (the agent-side loader owns that namespace).
_PACKAGE = "fish_audio_dashboard_" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
_LOAD_LOCK = threading.Lock()
_UPLOAD_LOCK = threading.Lock()
_FINISHING = set()

MiB = 1024 * 1024
PREVIEW_TEXT = "Hi! This is how I sound."
PREVIEW_MAX_BYTES = 4 * MiB
DESIGN_AUDIO_MAX_BYTES = 8 * MiB
CLONE_MAX_BYTES = 10 * MiB
CLONE_MAX_FILES = 3
CHUNK_BYTES = 1 * MiB
MAX_CHUNK_BYTES = 2 * MiB
UPLOAD_TTL_SECONDS = 15 * 60
UPLOAD_RE = re.compile(r"^[0-9a-f]{32}$")
TEMP_RE = re.compile(r"^[0-9a-f]{32}\.(part|mp3|wav|ogg|webm|flac|mp4)$")
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
LOW_CREDIT = Decimal("1")
EXTENSIONS = {"mp3": "mp3", "wav": "wav", "ogg": "ogg", "webm": "webm", "flac": "flac", "mp4": "mp4"}
LINKS = {"top_up": "https://fish.audio/app/developers/billing", "plans": "https://fish.audio/plan",
         "keys": "https://fish.audio/app/api-keys"}
NO_KEY = ("Fish Audio isn't set up for this profile yet. Add a key in Settings ▸ Plugins ▸ Fish Audio"
          " (Capabilities ▸ Plugins on older Desktop), "
          "or run `hermes fish login` on the machine running Hermes.")


def _fa(name: str):
    """A submodule of this plugin's ``fish_audio`` package, loaded once under the private package name."""
    with _LOAD_LOCK:
        if _PACKAGE not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                _PACKAGE, ROOT / "fish_audio" / "__init__.py", submodule_search_locations=[str(ROOT / "fish_audio")])
            package = importlib.util.module_from_spec(spec)
            sys.modules[_PACKAGE] = package
            try:
                spec.loader.exec_module(package)
            except BaseException:
                sys.modules.pop(_PACKAGE, None)
                raise
    return importlib.import_module(f"{_PACKAGE}.{name}")


class Refusal(Exception):
    def __init__(self, kind: str, message: str, **extra):
        super().__init__(message)
        self.kind, self.message, self.extra = kind, message, extra


def _redact(text: str) -> str:
    try:
        return _fa("secrets").redact(str(text))
    except Exception:
        return re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[redacted]", str(text))


def _failure(exc: Exception) -> dict:
    if isinstance(exc, Refusal):
        return {"ok": False, "kind": exc.kind, "message": _redact(exc.message), **exc.extra}
    try:
        errors, media, support = _fa("errors"), _fa("media"), _fa("tool_support")
        if isinstance(exc, errors.FishAudioError):
            return {"ok": False, "kind": exc.kind, "message": _redact(str(exc))}
        if isinstance(exc, media.InputFileError):
            return {"ok": False, "kind": "bad_audio", "message": _redact(str(exc))}
        if isinstance(exc, support.ToolInputError):
            return {"ok": False, "kind": "invalid", "message": _redact(str(exc))}
    except Exception:
        pass
    return {"ok": False, "kind": "error", "message": "Fish Audio could not complete this request. Try again."}


# Token-shaped only, so ordinary voice titles ("Ring Bearer Narrator", "task-oriented-…") pass through untouched.
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*", re.I)
_SK_RE = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}")


def _scrub(value, key):
    if isinstance(value, dict):
        return {k: v if k == "audio" else _scrub(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, key) for v in value]
    if isinstance(value, str):
        value = value.replace(key, "[redacted]") if key else value
        value = _BEARER_RE.sub("Bearer [redacted]", value)
        return _SK_RE.sub("[redacted]", value)
    return value


def _protocol(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
            try:
                key = _fa("secrets").fish_api_key()
            except Exception:
                key = ""
            return _scrub(result, key)
        except Exception as exc:
            return _failure(exc)
    return wrapped


def _scope():
    """The requesting profile's key and API base URL, read fresh for this call."""
    key = _fa("secrets").fish_api_key()
    if not key:
        raise Refusal("no_key", "Ask the operator of this agent to finish the Fish Audio setup." if _fa("settings").operator_account() else NO_KEY)
    return key, _fa("commands").base_url()


def _voice_id(value: Any) -> str:
    if not _fa("tool_support").voice_id(value):
        raise Refusal("invalid", "Use a valid Fish Audio voice id.")
    return value


def _text(value: Any, name: str, limit: int, *, required: bool = True) -> Optional[str]:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise Refusal("invalid", f"{name} must be 1–{limit} characters." if required else f"{name} is too long.")
    return value.strip()


@router.get("/available")
def available() -> dict:
    """Desktop feature detection: 200 here means the routes are installed, enabled and mounted."""
    try:
        key = bool(_fa("secrets").fish_api_key())
    except Exception:
        key = False
    return {"ok": True, "plugin": PLUGIN_NAME, "version": VERSION, "key": key, "account": not _fa("settings").operator_account()}


@router.get("/voices")
@_protocol
def list_voices(q: str = "", mine: bool = Query(False, alias="self"), page: int = 1, language: str = ""):
    if mine and _fa("settings").operator_account():
        raise Refusal("operator_account", _fa("voices").ACCOUNT_VOICES)
    key, base = _scope()
    if len(q) > 100 or (language and not LANGUAGE_RE.fullmatch(language)) or not 1 <= page <= 50:
        raise Refusal("invalid", "Use a shorter search, a language code like en or ja, and a page from 1 to 50.")
    args = {"action": "mine" if mine else "search", "page": page, "page_size": 20}
    if q.strip():
        args["query"] = q.strip()
    if language:
        args["language"] = language
    result = _fa("voices").execute(args, key, base, "")
    return {"ok": True, "page": page, **result}


@router.get("/voices/{voice_id}")
@_protocol
def get_voice(voice_id: str):
    key, base = _scope()
    return {"ok": True, "voice": _fa("voices").execute({"action": "get", "voice_id": _voice_id(voice_id)}, key, base, "")}


class Preview(BaseModel):
    voice: Any = None
    text: Any = None


@router.post("/preview")
@_protocol
def preview(body: Preview):
    """One billed synthesis, the same path as ``/fish preview``: mp3, at most 200 characters."""
    key, base = _scope()
    text = PREVIEW_TEXT if body.text in (None, "") else _text(body.text, "Preview text", 200)
    result = _fa("tools")._speak({"text": text, "voice": _voice_id(body.voice), "format": "mp3"}, key, base, "",
                                record_media=False)
    path = Path(result["file_path"])
    try:
        if path.stat().st_size > PREVIEW_MAX_BYTES:
            raise Refusal("too_large", "The preview audio is too large to play here.")
        audio = path.read_bytes()
    finally:
        path.unlink(missing_ok=True)
    return {"ok": True, "audio": base64.b64encode(audio).decode("ascii"), "mime": "audio/mpeg",
            **({"model": result["model"]} if "model" in result else {}), "billed": True}


class Use(BaseModel):
    voice: Any = None


@router.post("/use")
@_protocol
def use_voice(body: Use):
    """Write ``tts.fish-audio.voice`` for the requesting profile with the writer ``/fish use`` uses."""
    key, base = _scope()
    voice = _voice_id(body.voice)
    commands = _fa("commands")
    provider, by_operator = commands.use_result(voice, key, base)
    # provider/operator_pinned let Desktop word the note in its own language; message stays for older clients.
    return {"ok": True, "voice": voice, "message": commands.use_message(provider, by_operator),
            "provider": provider, "operator_pinned": by_operator}


def _author_owned(ident: str, key: str, base: str) -> bool:
    client = _fa("client")
    detail = client.get_json(f"/model/{ident}", {}, key, base)
    params = {"self": "true", "page_size": 100, "page_number": 1}
    if isinstance(detail.get("title"), str) and detail["title"]:
        params["title"] = detail["title"]
    for page_number in range(1, 11):
        params["page_number"] = page_number
        mine = client.get_json("/model", params, key, base)
        items = mine.get("items", [])
        if any(isinstance(item, dict) and item.get("_id", item.get("id")) == ident for item in items):
            return True
        total, more = mine.get("total"), mine.get("has_more")
        if not items or more is False:
            break
        # Fish flags a lower-bound total (total_is_exact false / window_limited); only an exact total ends the scan.
        exact = type(total) is int and mine.get("total_is_exact") is not False and mine.get("window_limited") is not True
        if exact:
            if page_number * 100 >= total:
                break
        elif more is not True and len(items) < 100:
            break
    return False


@router.delete("/voices/{voice_id}")
@_protocol
def delete_voice(voice_id: str):
    """Delete one of the account's own voices; anything else is refused before the delete request."""
    if _fa("settings").operator_account():
        raise Refusal("operator_account", _fa("voices").ACCOUNT_VOICES)
    key, base = _scope()
    ident = _voice_id(voice_id)
    if not _author_owned(ident, key, base):
        raise Refusal("not_owner", "You can delete only voices your Fish Audio account created.")
    _fa("voices").execute({"action": "delete", "voice_id": ident}, key, base, "")
    return {"ok": True, "id": ident}


@router.get("/account")
@_protocol
def account():
    if _fa("settings").operator_account():
        return {"ok": False, "kind": "operator_account",
                "message": "Billing for this agent's voice service is handled by its operator."}
    key, base = _scope()
    module = _fa("account")
    wallet = module.get_wallet(key, base, strict=True)
    if wallet is None:
        raise Refusal("availability", "Fish Audio returned an unreadable wallet. Try again later.")
    package_unavailable = False
    try:
        package = module.get_package(key, base, strict=True) or {}
    except Exception:
        package, package_unavailable = {}, True
    package = {k: v for k, v in package.items() if
               (k in {"type", "finished_at", "subscription_status"} and isinstance(v, str)) or
               (k in {"total", "balance"} and type(v) in (int, float)) or
               (k == "cancel_at_period_end" and type(v) is bool)}
    return {"ok": True, "credit": str(wallet.credit), "cumulative_top_up": str(wallet.cumulative_top_up),
            "has_free_credit": wallet.has_free_credit, "low": wallet.credit < LOW_CREDIT,
            "package": package or None, "package_unavailable": package_unavailable, "links": LINKS}


class Design(BaseModel):
    instruction: Any = None
    n: Any = 2
    language: Any = None
    reference_text: Any = None


@router.post("/design")
@_protocol
def design(body: Design):
    """Billed voice design: candidates carry their audio and an opaque, short-lived design token."""
    key, base = _scope()
    if type(body.n) is not int or not 1 <= body.n <= 3:
        raise Refusal("invalid", "Ask for 1–3 candidates.")
    args = {"action": "design", "instruction": _text(body.instruction, "The description", 500), "n": body.n}
    if body.language not in (None, ""):
        if not isinstance(body.language, str) or not LANGUAGE_RE.fullmatch(body.language):
            raise Refusal("invalid", "Use a language code like en or ja.")
        args["language"] = body.language
    reference = _text(body.reference_text, "The sample text", 500, required=False)
    if reference:
        args["reference_text"] = reference
    voices = _fa("voices")
    result = voices.execute(args, key, base, "")
    candidates = []
    for item in result["candidates"]:
        with voices._lock:
            receipt = voices._DESIGNS.get(item["design_token"])
        audio = receipt["audio"] if receipt else b""
        Path(item["file_path"]).unlink(missing_ok=True)  # the receipt keeps the audio the save needs
        candidate = {"design_token": item["design_token"], "index": item["index"],
                     "duration_ms": item.get("duration_ms"), "mime": "audio/wav", "billed": True}
        if 0 < len(audio) <= DESIGN_AUDIO_MAX_BYTES:
            candidate["audio"] = base64.b64encode(audio).decode("ascii")
        candidates.append(candidate)
    return {"ok": True, "candidates": candidates}


class DesignSave(BaseModel):
    design_token: Any = None
    title: Any = None
    description: Any = None


@router.post("/design/save")
@_protocol
def design_save(body: DesignSave):
    key, base = _scope()
    if not isinstance(body.design_token, str) or not UPLOAD_RE.fullmatch(body.design_token):
        raise Refusal("invalid", "Unknown or expired design; design again.")
    args = {"action": "save", "design_token": body.design_token, "title": _text(body.title, "The title", 100)}
    description = _text(body.description, "The description", 500, required=False)
    if description:
        args["description"] = description
    saved = _fa("voices").execute(args, key, base, "")
    return {"ok": True, "voice": {"id": saved.get("id"), "title": saved.get("title")}}


# Clone uploads: chunked JSON + base64 into per-upload temp files under the profile's cache. Names are generated
# here, never taken from the request; a client only ever names an upload by its 32-hex id.

def _home() -> Path:
    return _fa("media")._hermes_home()


def _uploads() -> Path:
    home = path = _home()
    for component in ("cache", "fish-audio", "uploads"):
        path = path / component
        try:
            path.mkdir(exist_ok=True, mode=0o700)
        except FileExistsError:
            pass  # lstat below maps a non-directory to the same plain-folder refusal
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise Refusal("io_error", "The plugin's upload folder is not a plain folder.")
    if not path.resolve().is_relative_to(home.resolve()):
        raise Refusal("io_error", "The plugin's upload folder is not a plain folder.")
    return path


def _sweep(folder: Path) -> int:
    """Remove expired upload temps; return how many live ones remain."""
    live, cutoff = 0, time.time() - UPLOAD_TTL_SECONDS
    with os.scandir(folder) as scan:
        for item in scan:
            if not TEMP_RE.fullmatch(item.name):
                continue
            try:
                info = os.lstat(item.path)
                if not stat.S_ISREG(info.st_mode):
                    continue
                if item.name[:32] in _FINISHING:
                    live += 1
                elif info.st_mtime < cutoff:
                    os.unlink(item.path)
                else:
                    live += 1
            except OSError:  # cleanup is incidental: a file gone or locked never blocks an upload
                pass
    return live


def _temp(upload_id: Any) -> tuple[Path, os.stat_result]:
    if not isinstance(upload_id, str) or not UPLOAD_RE.fullmatch(upload_id):
        raise Refusal("bad_upload_id", "The upload identifier is invalid.")
    temp = _uploads() / f"{upload_id}.part"
    try:
        info = os.lstat(temp)
    except FileNotFoundError:
        raise Refusal("gone", "The upload is no longer available; add the file again.") from None
    if not stat.S_ISREG(info.st_mode):
        raise Refusal("gone", "The upload is no longer available; add the file again.")
    return temp, info


def _discard(upload_id: str) -> None:
    folder = _uploads()
    for name in [f"{upload_id}.part", *(f"{upload_id}.{ext}" for ext in EXTENSIONS.values())]:
        path = folder / name
        try:
            if stat.S_ISREG(os.lstat(path).st_mode):
                os.unlink(path)
        except OSError:
            pass


def _upload_locked(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with _UPLOAD_LOCK:
            return fn(*args, **kwargs)
    return wrapped


class CloneStart(BaseModel):
    size: Any = None


@router.post("/clone/start")
@_protocol
@_upload_locked
def clone_start(body: CloneStart):
    _scope()
    if type(body.size) is not int or not 0 < body.size <= CLONE_MAX_BYTES:
        raise Refusal("too_large", "Each sample must be under 10 MB.", max_file_bytes=CLONE_MAX_BYTES)
    folder = _uploads()
    if _sweep(folder) >= CLONE_MAX_FILES:
        raise Refusal("busy", "Another voice upload is in progress. Finish it, or try again in a few minutes.")
    upload_id = secrets.token_hex(16)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    os.close(os.open(folder / f"{upload_id}.part", flags, 0o600))
    return {"ok": True, "upload_id": upload_id, "chunk_bytes": CHUNK_BYTES, "max_file_bytes": CLONE_MAX_BYTES,
            "max_files": CLONE_MAX_FILES}


class CloneChunk(BaseModel):
    upload_id: Any = None
    offset: Any = None
    data: Any = None


@router.post("/clone/chunk")
@_protocol
@_upload_locked
def clone_chunk(body: CloneChunk):
    _scope()
    temp, info = _temp(body.upload_id)
    if not isinstance(body.data, str) or len(body.data) > (MAX_CHUNK_BYTES * 4) // 3 + 8:
        raise Refusal("chunk_too_large", "The upload chunk is too large.")
    try:
        decoded = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise Refusal("bad_data", "The chunk must contain valid base64 data.") from None
    if len(decoded) > MAX_CHUNK_BYTES:
        raise Refusal("chunk_too_large", "The upload chunk is too large.")
    if type(body.offset) is not int or body.offset != info.st_size:
        raise Refusal("offset", "The offset does not match the uploaded bytes.", size=info.st_size)
    if not decoded:
        raise Refusal("bad_data", "The upload chunk is empty.")
    if info.st_size + len(decoded) > CLONE_MAX_BYTES:
        _discard(body.upload_id)
        raise Refusal("too_large", "Each sample must be under 10 MB.", max_file_bytes=CLONE_MAX_BYTES)
    fd = os.open(temp, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise Refusal("gone", "The upload is no longer available; add the file again.")
        os.lseek(fd, body.offset, os.SEEK_SET)
        view = memoryview(decoded)
        while view:
            written = os.write(fd, view)
            if written == 0:
                raise OSError("No write progress")
            view = view[written:]
    finally:
        os.close(fd)
    return {"ok": True, "size": body.offset + len(decoded)}


class CloneAbort(BaseModel):
    upload_id: Any = None


@router.post("/clone/abort")
@_protocol
@_upload_locked
def clone_abort(body: CloneAbort):
    if isinstance(body.upload_id, str) and UPLOAD_RE.fullmatch(body.upload_id) and body.upload_id not in _FINISHING:
        _discard(body.upload_id)
    return {"ok": True}


class CloneFile(BaseModel):
    upload_id: Any = None
    size: Any = None


class CloneFinish(BaseModel):
    files: List[CloneFile] = []
    title: Any = None
    description: Any = None
    consent: Any = None


@router.post("/clone/finish")
@_protocol
def clone_finish(body: CloneFinish):
    """Clone from finished uploads. The human click in Desktop is the confirmation, with a consent flag that
    must be exactly ``true``. The upload temps are removed whatever the outcome."""
    ids = [f.upload_id for f in body.files]
    owned = []
    try:
        with _UPLOAD_LOCK:
            if any(isinstance(i, str) and i in _FINISHING for i in ids):
                raise Refusal("busy", "This upload is already finishing.")
            owned = [i for i in ids if isinstance(i, str) and UPLOAD_RE.fullmatch(i)]
            _FINISHING.update(owned)
            key, base = _scope()
            if body.consent is not True:
                raise Refusal("consent", "Confirm that you have the speaker's permission to clone this voice.")
            title = _text(body.title, "The title", 100)
            description = _text(body.description, "The description", 500, required=False)
            if not 1 <= len(ids) <= CLONE_MAX_FILES or len(set(map(str, ids))) != len(ids):
                raise Refusal("invalid", f"Add 1–{CLONE_MAX_FILES} different samples.")
            paths = []
            media = _fa("media")
            for item in body.files:
                temp, info = _temp(item.upload_id)
                if type(item.size) is not int or item.size != info.st_size:
                    raise Refusal("size_mismatch", "A sample did not finish uploading; add it again.", size=info.st_size)
                fd = os.open(temp, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
                with os.fdopen(fd, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise Refusal("gone", "The upload is no longer available; add the file again.")
                    kind = media._audio_kind(handle.read(64))
                if kind not in EXTENSIONS:
                    raise Refusal("bad_audio", "Use an MP3, WAV, OGG, WebM, FLAC or MP4 audio sample.")
                final = temp.with_name(f"{item.upload_id}.{EXTENSIONS[kind]}")
                os.replace(temp, final)
                paths.append(str(final))
        args = {"action": "clone", "consent": True, "title": title, "sample_paths": paths}
        if description:
            args["description"] = description
        cloned = _fa("voices").execute(args, key, base, "")
        return {"ok": True, "voice": {"id": cloned.get("id"), "title": cloned.get("title"), "state": cloned.get("state")}}
    finally:
        with _UPLOAD_LOCK:
            for upload_id in owned:
                try:
                    _discard(upload_id)
                except Exception:
                    pass
            _FINISHING.difference_update(owned)
