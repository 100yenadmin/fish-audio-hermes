"""The gateway REST half (dashboard/plugin_api.py) through Starlette's TestClient, with Fish HTTP mocked.

Two fake profiles stand in for the dashboard's per-request scope: each has its own key, config, base URL and
Hermes home, and ``env.switch`` changes which one the next request runs in (as ``?profile=`` does upstream).
"""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import threading
from types import ModuleType, SimpleNamespace

import pytest
import respx

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/api/plugins/fish-audio"
VOICE = "a" * 32
OTHER = "c" * 32
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00synthetic-mp3"
WAV = b"RIFF\x10\x00\x00\x00WAVEsynthetic-audio"
PROFILES = {"a": ("sk-" + "A" * 40, "http://127.0.0.1:8501"), "b": ("sk-" + "B" * 40, "http://localhost:8502")}


def load_api():
    """Load the file the way Hermes's dashboard does: by path, with no parent package."""
    name = "hermes_dashboard_plugin_fish-audio"
    spec = importlib.util.spec_from_file_location(name, ROOT / "dashboard" / "plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(monkeypatch, tmp_path):
    api = load_api()
    current = {"name": "a"}
    profiles = {}
    for name, (key, base) in PROFILES.items():
        home = tmp_path / name
        (home / "audio").mkdir(parents=True)
        config = {"plugins": {"entries": {"fish-audio": {"settings": {"base_url": base}}}},
                  "tts": {"provider": "fish-audio", "fish-audio": {"model": "s2.1-pro"}}}
        profiles[name] = SimpleNamespace(key=key, base=base, home=home, config=config, saves=0)
    active = lambda: profiles[current["name"]]  # noqa: E731
    secrets, settings, media, voices = (api._fa(n) for n in ("secrets", "settings", "media", "voices"))
    monkeypatch.setattr(secrets, "fish_api_key", lambda: active().key)
    monkeypatch.setattr(settings, "_config", lambda: active().config)
    monkeypatch.setattr(media, "audio_output_dir", lambda: active().home / "audio")
    monkeypatch.setattr(media, "_hermes_home", lambda: active().home)
    config_module = ModuleType("hermes_cli.config")
    config_module.read_raw_config = lambda: copy.deepcopy(active().config)

    def save(cfg, **kwargs):
        assert kwargs == {"strip_defaults": False}
        active().config.clear()
        active().config.update(copy.deepcopy(cfg))
        active().saves += 1
    config_module.save_config = save
    config_module.is_managed = lambda: False
    parent = ModuleType("hermes_cli")
    parent.config = config_module
    monkeypatch.setitem(sys.modules, "hermes_cli", parent)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config_module)
    api._fa("account")._wallet_cache.clear()
    voices._DESIGNS.clear()
    app = FastAPI()
    app.include_router(api.router, prefix=PREFIX)
    client = TestClient(app)

    def request(method, route, **kwargs):
        response = client.request(method, PREFIX + route, **kwargs)
        assert response.status_code == 200, response.text
        for profile in profiles.values():
            assert profile.key not in response.text
        return response.json()

    def switch(name):
        current["name"] = name
        return profiles[name]
    return SimpleNamespace(api=api, profiles=profiles, active=active, switch=switch,
                           get=lambda route, **params: request("GET", route, params=params),
                           post=lambda route, **body: request("POST", route, json=body),
                           delete=lambda route: request("DELETE", route))


def refused(body, kind):
    assert body["ok"] is False and body["kind"] == kind and body["message"], body


def uploads(env):
    folder = env.active().home / "cache" / "fish-audio" / "uploads"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def upload(env, data, chunk=None):
    start = env.post("/clone/start", size=len(data))
    assert start["ok"], start
    step = chunk or start["chunk_bytes"]
    for offset in range(0, len(data), step):
        body = env.post("/clone/chunk", upload_id=start["upload_id"], offset=offset,
                        data=base64.b64encode(data[offset:offset + step]).decode())
        assert body == {"ok": True, "size": min(len(data), offset + step)}
    return {"upload_id": start["upload_id"], "size": len(data)}


def test_available_reports_version_and_key_state(env, monkeypatch):
    assert env.get("/available") == {"ok": True, "plugin": "fish-audio", "version": env.api.VERSION, "key": True}
    monkeypatch.setattr(env.api._fa("secrets"), "fish_api_key", lambda: "")
    assert env.get("/available")["key"] is False


def test_versions_agree(env):
    manifest = json.loads((ROOT / "dashboard" / "manifest.json").read_text())
    package = json.loads((ROOT / "package.json").read_text())
    assert manifest["name"] == package["name"].removesuffix("-desktop") == "fish-audio"
    assert manifest["tab"]["hidden"] is True
    version = env.api.VERSION
    assert manifest["version"] == package["version"] == version
    assert f"version: {version}\n" in (ROOT / "plugin.yaml").read_text()
    assert f'version = "{version}"' in (ROOT / "pyproject.toml").read_text()
    assert env.api._fa("client").PLUGIN_VERSION == version
    assert f'PLUGIN_VERSION = "{version}"' in (ROOT / "fish_audio" / "__init__.py").read_text()
    assert "fish-audio" in (ROOT / "src" / "desktop" / "plugin.tsx").read_text()


def test_private_package_never_touches_hermes_plugins_namespace(env):
    assert not env.api._PACKAGE.startswith("hermes_plugins")
    assert "hermes_plugins" not in (ROOT / "dashboard" / "plugin_api.py").read_text().replace(
        "never ``hermes_plugins.*``", "")


@pytest.mark.parametrize("method,route,body", [
    ("GET", "/voices", None), ("GET", f"/voices/{VOICE}", None), ("POST", "/preview", {"voice": VOICE}),
    ("POST", "/use", {"voice": VOICE}), ("DELETE", f"/voices/{VOICE}", None), ("GET", "/account", None),
    ("POST", "/design", {"instruction": "warm"}), ("POST", "/design/save", {"design_token": "f" * 32, "title": "t"}),
    ("POST", "/clone/start", {"size": 10}), ("POST", "/clone/finish", {"consent": True, "title": "t", "files": []})])
def test_no_key_answers_in_band_without_any_request(env, monkeypatch, method, route, body):
    monkeypatch.setattr(env.api._fa("secrets"), "fish_api_key", lambda: "")
    with respx.mock(assert_all_called=True) as mock:
        refused({"GET": env.get, "DELETE": env.delete}[method](route) if body is None else env.post(route, **body),
                "no_key")
        assert not mock.calls


def test_voice_search_and_mine_projection(env):
    base = env.active().base
    item = {"_id": VOICE, "title": "Narrator", "languages": ["en"], "tags": ["calm"], "author": {"nickname": "n"},
            "cover_image": "https://example.invalid/x.png"}
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(base + "/model").respond(json={"items": [item], "total": 1})
        body = env.get("/voices", q=" narrator ", language="en", page=2)
        params = route.calls.last.request.url.params
        assert (params["title"], params["language"], params["page_number"], params["page_size"]) == ("narrator", "en", "2", "20")
        assert "self" not in params
        assert body["ok"] and body["page"] == 2 and body["items"][0]["id"] == VOICE and body["total"] == 1
        assert "cover_image" not in body["items"][0]
        env.get("/voices", **{"self": "true"})
        assert route.calls.last.request.url.params["self"] == "true"
        assert "title" not in route.calls.last.request.url.params


@pytest.mark.parametrize("params", [{"language": "en'; drop"}, {"page": 0}, {"page": 51}, {"q": "x" * 101}])
def test_voice_search_rejects_bad_input_without_request(env, params):
    with respx.mock(assert_all_called=True) as mock:
        refused(env.get("/voices", **params), "invalid")
        assert not mock.calls


def test_get_voice_detail_and_bad_id(env):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(env.active().base + f"/model/{VOICE}").respond(json={"_id": VOICE, "title": "N", "samples": [{"title": "s", "text": "hi", "audio": "u"}]})
        voice = env.get(f"/voices/{VOICE}")["voice"]
        assert voice["id"] == VOICE and voice["samples"] == [{"title": "s", "text": "hi"}]
    refused(env.get("/voices/short"), "invalid")
    refused(env.get("/voices/" + "a" * 31 + "!"), "invalid")


def test_preview_is_mp3_base64_and_leaves_no_file(env):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(env.active().base + "/v1/tts").respond(content=MP3)
        body = env.post("/preview", voice=VOICE, text="Hello there")
        sent = json.loads(route.calls.last.request.content)
    assert body["ok"] and body["billed"] is True and body["mime"] == "audio/mpeg"
    assert base64.b64decode(body["audio"]) == MP3
    assert (sent["format"], sent["reference_id"], sent["text"]) == ("mp3", VOICE, "Hello there")
    assert route.calls.last.request.headers["model"] == "s2.1-pro"
    assert list((env.active().home / "audio").iterdir()) == []


@pytest.mark.parametrize("body", [{"voice": VOICE, "text": "x" * 201}, {"voice": "../x"}, {"voice": VOICE, "text": 5}])
def test_preview_limits_without_request(env, body):
    with respx.mock(assert_all_called=True) as mock:
        refused(env.post("/preview", **body), "invalid")
        assert not mock.calls


def test_use_writes_only_the_active_profile(env):
    a, b = env.profiles["a"], env.profiles["b"]
    before = copy.deepcopy(b.config)
    with respx.mock(assert_all_called=True) as mock:
        mock.get(a.base + f"/model/{VOICE}").respond(json={"_id": VOICE})
        body = env.post("/use", voice=VOICE)
    assert body["ok"] and body["voice"] == VOICE and body["message"] == "Saved."
    assert a.config["tts"]["fish-audio"]["voice"] == VOICE and a.saves == 1
    assert b.config == before and b.saves == 0


def test_use_unknown_voice_writes_nothing(env):
    with respx.mock(assert_all_called=True) as mock:
        mock.get(env.active().base + f"/model/{VOICE}").respond(404)
        refused(env.post("/use", voice=VOICE), "voice_not_found")
    assert env.active().saves == 0


def test_two_profiles_a_b_a_keys_bases_and_voices_stay_isolated(env):
    with respx.mock(assert_all_called=True) as mock:
        for step, (name, voice) in enumerate((("a", VOICE), ("b", OTHER), ("a", VOICE))):
            profile = env.switch(name)
            mock.get(profile.base + "/model").respond(json={"items": []})
            mock.get(profile.base + f"/model/{voice}").respond(json={"_id": voice})
            tts = mock.post(profile.base + "/v1/tts").respond(content=MP3)
            assert env.get("/voices")["ok"]
            assert env.post("/use", voice=voice)["ok"]
            assert env.post("/preview", voice=voice)["ok"]
            for call in mock.calls[-3:]:
                assert call.request.headers["authorization"] == f"Bearer {profile.key}"
                assert str(call.request.url).startswith(profile.base)
            assert json.loads(tts.calls.last.request.content)["reference_id"] == voice
            start = env.post("/clone/start", size=4)
            assert start["ok"] and len(uploads(env)) == (2 if step == 2 else 1)
        assert len(mock.calls) == 9
    a, b = env.profiles["a"], env.profiles["b"]
    assert a.config["tts"]["fish-audio"]["voice"] == VOICE and b.config["tts"]["fish-audio"]["voice"] == OTHER
    assert (a.saves, b.saves) == (2, 1)
    # An upload started in one profile does not exist in the other.
    env.switch("b")
    b_upload = uploads(env)[0].removesuffix(".part")
    env.switch("a")
    refused(env.post("/clone/chunk", upload_id=b_upload, offset=0, data="AA=="), "gone")


def test_delete_refuses_voices_the_account_does_not_own(env):
    base = env.active().base
    with respx.mock(assert_all_called=True) as mock:
        mock.get(base + f"/model/{VOICE}").respond(json={"_id": VOICE, "title": "Public"})
        listing = mock.get(base + "/model").respond(json={"items": [{"_id": OTHER, "title": "Public"}]})
        refused(env.delete(f"/voices/{VOICE}"), "not_owner")
        params = listing.calls.last.request.url.params
        assert (params["self"], params["title"]) == ("true", "Public")
        assert not [c for c in mock.calls if c.request.method == "DELETE"]


def test_delete_own_voice(env):
    base = env.active().base
    with respx.mock(assert_all_called=True) as mock:
        mock.get(base + f"/model/{VOICE}").respond(json={"_id": VOICE, "title": "Mine"})
        mock.get(base + "/model").respond(json={"items": [{"_id": VOICE}]})
        deleted = mock.delete(base + f"/model/{VOICE}").respond(204)
        assert env.delete(f"/voices/{VOICE}") == {"ok": True, "id": VOICE}
        assert deleted.call_count == 1


@pytest.mark.parametrize("credit,low", [("0.5", True), ("12.25", False)])
def test_account_projection(env, credit, low):
    base = env.active().base
    with respx.mock(assert_all_called=True) as mock:
        mock.get(base + "/wallet/self/api-credit?check_free_credit=true").respond(
            json={"credit": credit, "cumulative_top_up": "10", "has_free_credit": False, "user_id": "u-1"})
        mock.get(base + "/wallet/self/package").respond(
            json={"type": "plus", "total": 30, "balance": 20, "finished_at": "2026-11-01", "user_id": "u-1"})
        body = env.get("/account")
    assert body["ok"] and body["credit"] == credit and body["low"] is low and body["cumulative_top_up"] == "10"
    assert body["package"] == {"type": "plus", "total": 30, "balance": 20, "finished_at": "2026-11-01"}
    assert body["links"]["top_up"] == "https://fish.audio/app/developers/billing"
    assert "?" not in "".join(body["links"].values())


def test_account_error_is_mapped_and_redacted(env):
    key = env.active().key
    with respx.mock(assert_all_called=True) as mock:
        mock.get(env.active().base + "/wallet/self/api-credit?check_free_credit=true").respond(
            401, json={"message": f"bad key {key}"})
        body = env.get("/account")
    refused(body, "credential")
    assert "fish.audio/app/api-keys" in body["message"]


def test_design_returns_audio_and_tokens_but_never_the_signature(env):
    base = env.active().base
    candidate = {"index": 0, "text": "preview text", "signature": "server-signature-xyz",
                 "audio_base64": base64.b64encode(WAV).decode(), "duration_ms": 1200}
    with respx.mock(assert_all_called=True) as mock:
        design = mock.post(base + "/v1/voice-design").respond(json={"candidates": [candidate, {**candidate, "index": 1}]})
        body = env.post("/design", instruction="A warm narrator", n=2, language="en")
        sent = json.loads(design.calls.last.request.content)
        assert (sent["instruction"], sent["n"], sent["language"]) == ("A warm narrator", 2, "en")
        assert body["ok"] and len(body["candidates"]) == 2
        assert "server-signature-xyz" not in json.dumps(body)
        first = body["candidates"][0]
        assert base64.b64decode(first["audio"]) == WAV and first["billed"] is True and first["duration_ms"] == 1200
        assert list((env.active().home / "audio").iterdir()) == []
        save = mock.post(base + "/model").respond(json={"_id": OTHER, "title": "Warm", "source": "design"})
        assert env.post("/design/save", design_token=first["design_token"], title="Warm")["voice"] == {"id": OTHER, "title": "Warm"}
        assert b"server-signature-xyz" in save.calls.last.request.content and WAV in save.calls.last.request.content
        refused(env.post("/design/save", design_token=first["design_token"], title="Again"), "invalid")


@pytest.mark.parametrize("body", [{"instruction": ""}, {"instruction": "x" * 501}, {"instruction": "ok", "n": 4},
                                  {"instruction": "ok", "n": "2"}, {"instruction": "ok", "language": "e n"}])
def test_design_limits_without_request(env, body):
    with respx.mock(assert_all_called=True) as mock:
        refused(env.post("/design", **body), "invalid")
        assert not mock.calls


def test_clone_happy_path_uploads_in_chunks_and_cleans_up(env):
    sample = MP3 + b"\x00" * (3 * 1024 * 1024)
    files = [upload(env, sample), upload(env, WAV)]
    assert len(uploads(env)) == 2
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(env.active().base + "/model").respond(json={"_id": OTHER, "title": "Me", "state": "trained"})
        body = env.post("/clone/finish", files=files, title="Me", consent=True)
        content = route.calls.last.request.content
    assert body == {"ok": True, "voice": {"id": OTHER, "title": "Me", "state": "trained"}}
    assert sample in content and WAV in content and b'name="visibility"\r\n\r\nprivate' in content
    assert f'{files[0]["upload_id"]}.mp3'.encode() in content
    assert uploads(env) == []


@pytest.mark.parametrize("consent", [None, False, "true", 1])
def test_clone_requires_literal_consent_and_still_cleans_up(env, consent):
    files = [upload(env, MP3)]
    with respx.mock(assert_all_called=True) as mock:
        refused(env.post("/clone/finish", files=files, title="Me", consent=consent), "consent")
        assert not mock.calls
    assert uploads(env) == []


def test_clone_limits(env):
    max_bytes = env.api.CLONE_MAX_BYTES
    refused(env.post("/clone/start", size=max_bytes + 1), "too_large")
    refused(env.post("/clone/start", size=0), "too_large")
    refused(env.post("/clone/start", size="5"), "too_large")
    files = [upload(env, MP3) for _ in range(3)]
    refused(env.post("/clone/start", size=10), "busy")
    refused(env.post("/clone/finish", files=files + [files[0]], title="t", consent=True), "invalid")
    assert uploads(env) == []
    files = [upload(env, MP3)]
    refused(env.post("/clone/finish", files=files * 2, title="t", consent=True), "invalid")
    big = env.post("/clone/start", size=max_bytes)
    chunk = base64.b64encode(b"\x00" * env.api.MAX_CHUNK_BYTES).decode()
    for offset in range(0, max_bytes, env.api.MAX_CHUNK_BYTES):
        assert env.post("/clone/chunk", upload_id=big["upload_id"], offset=offset, data=chunk)["ok"]
    refused(env.post("/clone/chunk", upload_id=big["upload_id"], offset=max_bytes, data="AA=="), "too_large")
    assert uploads(env) == []  # an over-limit upload is discarded at once
    oversized = env.post("/clone/start", size=10)
    refused(env.post("/clone/chunk", upload_id=oversized["upload_id"], offset=0,
                     data=base64.b64encode(b"\x00" * (env.api.MAX_CHUNK_BYTES + 3)).decode()), "chunk_too_large")


def test_clone_chunk_offset_and_data_checks(env):
    start = env.post("/clone/start", size=8)
    upload_id = start["upload_id"]
    assert env.post("/clone/chunk", upload_id=upload_id, offset=0, data=base64.b64encode(b"ID3a").decode())["size"] == 4
    assert env.post("/clone/chunk", upload_id=upload_id, offset=0, data="AA==") == {
        "ok": False, "kind": "offset", "message": "The offset does not match the uploaded bytes.", "size": 4}
    refused(env.post("/clone/chunk", upload_id=upload_id, offset=4, data="not base64!"), "bad_data")
    refused(env.post("/clone/chunk", upload_id=upload_id, offset=4, data=""), "bad_data")
    refused(env.post("/clone/finish", files=[{"upload_id": upload_id, "size": 8}], title="t", consent=True), "size_mismatch")
    assert uploads(env) == []


@pytest.mark.parametrize("upload_id", ["../../etc/passwd", "A" * 32, "a" * 31, "a" * 32 + "/x", 7, None])
def test_clone_ids_are_never_paths(env, upload_id):
    refused(env.post("/clone/chunk", upload_id=upload_id, offset=0, data="AA=="), "bad_upload_id")
    assert env.post("/clone/abort", upload_id=upload_id) == {"ok": True}


def test_clone_refuses_a_temp_replaced_by_a_symlink(env, tmp_path):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"keep")
    start = env.post("/clone/start", size=4)
    temp = env.active().home / "cache" / "fish-audio" / "uploads" / f"{start['upload_id']}.part"
    temp.unlink()
    temp.symlink_to(outside)
    refused(env.post("/clone/chunk", upload_id=start["upload_id"], offset=0, data="AA=="), "gone")
    refused(env.post("/clone/finish", files=[{"upload_id": start["upload_id"], "size": 4}], title="t", consent=True), "gone")
    assert outside.read_bytes() == b"keep" and temp.is_symlink()


def test_clone_refuses_a_symlinked_upload_folder(env, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    folder = env.active().home / "cache" / "fish-audio"
    folder.mkdir(parents=True)
    (folder / "uploads").symlink_to(elsewhere)
    refused(env.post("/clone/start", size=4), "io_error")
    assert list(elsewhere.iterdir()) == []


def test_clone_rejects_non_audio_and_cleans_up(env):
    files = [upload(env, b"#!/bin/sh\necho not audio\n")]
    with respx.mock(assert_all_called=True) as mock:
        refused(env.post("/clone/finish", files=files, title="t", consent=True), "bad_audio")
        assert not mock.calls
    assert uploads(env) == []


def test_clone_fish_failure_still_cleans_up(env):
    files = [upload(env, MP3)]
    with respx.mock(assert_all_called=True) as mock:
        mock.post(env.active().base + "/model").respond(402)
        refused(env.post("/clone/finish", files=files, title="t", consent=True), "quota")
    assert uploads(env) == []


def test_abort_and_expiry_remove_temps(env):
    start = env.post("/clone/start", size=4)
    assert env.post("/clone/abort", upload_id=start["upload_id"]) == {"ok": True}
    assert uploads(env) == []
    stale = [env.post("/clone/start", size=4)["upload_id"] for _ in range(3)]
    old = time.time() - env.api.UPLOAD_TTL_SECONDS - 5
    folder = env.active().home / "cache" / "fish-audio" / "uploads"
    for upload_id in stale:
        os.utime(folder / f"{upload_id}.part", (old, old))
    (folder / "notes.txt").write_text("not ours")
    fresh = env.post("/clone/start", size=4)
    assert fresh["ok"] and uploads(env) == sorted([f"{fresh['upload_id']}.part", "notes.txt"])


def test_unexpected_errors_are_generic_and_in_band(env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError(f"internal detail {env.active().key}")
    monkeypatch.setattr(env.api._fa("voices"), "execute", boom)
    body = env.get("/voices")
    refused(body, "error")
    assert "internal detail" not in body["message"]


@pytest.mark.parametrize("host_scopes", [False, True])
def test_request_scope_uses_query_or_defers_to_host(env, monkeypatch, host_scopes):
    dashboard = ModuleType("hermes_cli.web_server_dashboard")
    if host_scopes:
        dashboard._plugin_route_secret_scope = object()
    monkeypatch.setitem(sys.modules, dashboard.__name__, dashboard)
    profiles = ModuleType("hermes_cli.web_server_profiles")
    entered = []

    @contextmanager
    def scope(profile):
        entered.append(("enter", profile))
        previous = env.active()
        env.switch(profile)
        try:
            yield
        finally:
            env.switch("a" if previous is env.profiles["a"] else "b")
            entered.append(("exit", profile))
    profiles._config_profile_scope = scope
    monkeypatch.setitem(sys.modules, profiles.__name__, profiles)
    assert env.get("/available", profile="b")["key"] is True
    # Abort has no _scope() call, so this also proves router-wide coverage.
    app = FastAPI()
    app.include_router(env.api.router, prefix=PREFIX)
    assert TestClient(app).post(PREFIX + "/clone/abort?profile=b", json={}).json()["ok"]
    assert entered == ([] if host_scopes else [("enter", "b"), ("exit", "b")] * 2)
    if host_scopes:
        del dashboard._plugin_route_secret_scope
        assert env.api._host_scopes_plugin_routes() is True  # successful detection is cached


def test_request_scope_falls_back_without_profile_helper(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_cli.web_server_profiles", ModuleType("hermes_cli.web_server_profiles"))
    async def consume():
        async for _ in env.api._request_scope("b"):
            pass
    asyncio.run(consume())


def test_gateway_design_cannot_be_saved_with_another_profiles_key(env):
    a = env.active()
    candidate = {"signature": "synthetic-signature", "audio_base64": base64.b64encode(WAV).decode()}
    with respx.mock(assert_all_called=True) as mock:
        mock.post(a.base + "/v1/voice-design").respond(json={"candidates": [candidate]})
        token = env.post("/design", instruction="warm", n=1)["candidates"][0]["design_token"]
        env.switch("b")
        refused(env.post("/design/save", design_token=token, title="Wrong account"), "invalid")
        assert not [c for c in mock.calls if c.request.url.path == "/model"]
        env.switch("a")
        saved = mock.post(a.base + "/model").respond(json={"_id": OTHER, "title": "Original"})
        assert env.post("/design/save", design_token=token, title="Original")["ok"]
        assert saved.call_count == 1


def concurrent_pair(action):
    barrier = threading.Barrier(2)
    def run():
        barrier.wait(timeout=5)
        return action()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        return [f.result(timeout=5) for f in futures]


def test_concurrent_chunks_accept_exactly_one_offset(env, monkeypatch):
    upload_id = env.post("/clone/start", size=4)["upload_id"]
    lstat = os.lstat
    def slow_stat(path, *args, **kwargs):
        info = lstat(path, *args, **kwargs)
        if str(path).endswith(".part"):
            time.sleep(0.03)  # expose the old stat/write race; the fixed stat is lock-held
        return info
    monkeypatch.setattr(os, "lstat", slow_stat)
    results = concurrent_pair(lambda: env.post("/clone/chunk", upload_id=upload_id, offset=0, data="SUQzYQ=="))
    assert sum(r["ok"] for r in results) == 1
    refused(next(r for r in results if not r["ok"]), "offset")
    assert (env.api._uploads() / f"{upload_id}.part").read_bytes() == b"ID3a"


def test_concurrent_starts_admit_exactly_one_upload(env, monkeypatch):
    for _ in range(2):
        assert env.post("/clone/start", size=4)["ok"]
    sweep = env.api._sweep
    def slow_sweep(folder):
        live = sweep(folder)
        time.sleep(0.03)  # old starts both see the same count; fixed admission is atomic
        return live
    monkeypatch.setattr(env.api, "_sweep", slow_sweep)
    results = concurrent_pair(lambda: env.post("/clone/start", size=4))
    assert sum(r["ok"] for r in results) == 1
    refused(next(r for r in results if not r["ok"]), "busy")
    assert len(uploads(env)) == 3


def test_finishing_upload_survives_abort_sweep_and_second_finish(env):
    upload_id = env.post("/clone/start", size=4)["upload_id"]
    folder = env.api._uploads()
    temp = folder / f"{upload_id}.part"
    old = time.time() - env.api.UPLOAD_TTL_SECONDS - 5
    os.utime(temp, (old, old))
    env.api._FINISHING.add(upload_id)
    try:
        assert env.post("/clone/abort", upload_id=upload_id)["ok"]
        assert env.post("/clone/start", size=4)["ok"]
        refused(env.post("/clone/finish", files=[{"upload_id": upload_id, "size": 0}], title="t", consent=True), "busy")
        assert temp.exists() and upload_id in env.api._FINISHING
    finally:
        env.api._FINISHING.remove(upload_id)
    assert env.post("/clone/abort", upload_id=upload_id)["ok"]
    assert not temp.exists()


@pytest.mark.parametrize("component", ["cache", "fish-audio"])
def test_clone_refuses_symlinked_upload_parents(env, tmp_path, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = env.active().home
    if component == "fish-audio":
        parent = parent / "cache"
        parent.mkdir()
    (parent / component).symlink_to(outside, target_is_directory=True)
    refused(env.post("/clone/start", size=4), "io_error")
    assert list(outside.iterdir()) == []


def test_clone_finish_checks_open_descriptor_identity(env, monkeypatch):
    file = upload(env, MP3)
    fstat = os.fstat
    def swapped(fd):
        info = fstat(fd)
        return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
    monkeypatch.setattr(os, "fstat", swapped)
    with respx.mock(assert_all_called=True) as mock:
        refused(env.post("/clone/finish", files=[file], title="t", consent=True), "gone")
        assert not mock.calls
    assert not uploads(env) and not env.api._FINISHING


@pytest.mark.parametrize("detail", [False, True])
def test_successful_voice_strings_are_scrubbed(env, detail):
    key = env.active().key
    item = {"_id": VOICE, "title": f"Voice {key}", "description": f"Bearer {key}",
            "samples": [{"title": "Bearer opaque-token-0123456789", "text": key}]}
    route = f"/model/{VOICE}" if detail else "/model"
    with respx.mock(assert_all_called=True) as mock:
        mock.get(env.active().base + route).respond(json=item if detail else {"items": [item]})
        body = env.get(f"/voices/{VOICE}" if detail else "/voices")
    voice = body["voice"] if detail else body["items"][0]
    assert voice["title"] == "Voice [redacted]"
    assert voice["description"] == "Bearer [redacted]"
    if detail:
        assert voice["samples"] == [{"title": "Bearer [redacted]", "text": "[redacted]"}]


def test_account_drops_non_scalar_package_fields_and_scrubs_text(env):
    base = env.active().base
    with respx.mock(assert_all_called=True) as mock:
        mock.get(base + "/wallet/self/api-credit").respond(json={"credit": "1", "cumulative_top_up": "2"})
        package = mock.get(base + "/wallet/self/package").respond(json={
            "type": "Bearer opaque-token-0123456789", "total": {"nested": "Bearer secret"}, "balance": True,
            "finished_at": env.active().key, "billing_period": {"path": "/outside/private"}})
        body = env.get("/account")
        assert body["package"] == {"type": "Bearer [redacted]", "finished_at": "[redacted]"}
        package.respond(json={"type": {}, "total": True, "balance": [], "finished_at": 123})
        assert env.get("/account")["package"] is None


def test_scrub_leaves_preview_audio_unchanged(env, monkeypatch):
    # A short synthetic key also present in the base64 payload must not alter audio bytes.
    monkeypatch.setattr(env.api._fa("secrets"), "fish_api_key", lambda: "SUQz")
    with respx.mock(assert_all_called=True) as mock:
        mock.post(env.active().base + "/v1/tts").respond(content=MP3)
        assert env.post("/preview", voice=VOICE)["audio"] == base64.b64encode(MP3).decode()
    assert env.api._scrub({"nested": ["Bearer opaque-token-0123456789", "sk-" + "Z" * 25]}, "") == {
        "nested": ["Bearer [redacted]", "[redacted]"]}


def test_scrub_leaves_ordinary_voice_text_alone(env):
    # Library titles and descriptions are upstream prose: only token-shaped strings are redacted.
    text = {"title": "Ring Bearer Narrator", "description": "A task-oriented-narration-voice for bearer bonds"}
    assert env.api._scrub(text, "") == text


def test_gateway_partial_design_leaves_no_files_or_receipts(env):
    candidate = {"signature": "synthetic-signature", "audio_base64": base64.b64encode(WAV).decode()}
    voices = env.api._fa("voices")
    before = set(voices._DESIGNS)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(env.active().base + "/v1/voice-design").respond(json={"candidates": [candidate, {"audio_base64": candidate["audio_base64"]}]})
        refused(env.post("/design", instruction="warm", n=2), "invalid")
    assert list((env.active().home / "audio").glob("fish-design-*.wav")) == []
    assert set(voices._DESIGNS) == before


@pytest.mark.parametrize("component", ["cache", "fish-audio", "uploads"])
def test_clone_refuses_non_directory_upload_components(env, component):
    path = env.active().home
    for name in ("cache", "fish-audio", "uploads"):
        path = path / name
        if name == component:
            path.write_bytes(b"keep")
            break
        path.mkdir()
    refused(env.post("/clone/start", size=4), "io_error")
    assert path.read_bytes() == b"keep"
