"""The gateway REST half mounted by Hermes's own dashboard plugin loader, A -> B -> A, with Fish HTTP mocked.

Builds whose loader scopes plugin routes per request (``_plugin_route_secret_scope``) get ``?profile=``; older
builds do no plugin scoping, so the test binds the same contextvars ``_config_profile_scope`` binds.
"""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys

import pytest
import respx

pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")
PREFIX = "/api/plugins/fish-audio"
VOICES = {"a": "a" * 32, "b": "c" * 32}


def test_dashboard_loader_mounts_routes_and_keeps_profiles_apart(installed_fish_home, monkeypatch):
    home = installed_fish_home[0]
    from agent.secret_scope import (build_profile_secret_scope, is_multiplex_active, reset_secret_scope,
                                   set_multiplex_active, set_secret_scope)
    from fastapi.testclient import TestClient
    from hermes_cli import web_server as server
    from hermes_cli import web_server_dashboard as dashboard
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    assert any(p["name"] == "fish-audio" for p in server._get_dashboard_plugins(force_rescan=True))
    if not any(getattr(r, "path", None) == PREFIX + "/available" for r in server.app.routes):
        dashboard._mount_plugin_api_routes()
    paths = {getattr(r, "path", None) for r in server.app.routes}
    for route in ("/available", "/voices", "/voices/{voice_id}", "/preview", "/use", "/account", "/design",
                  "/design/save", "/clone/start", "/clone/chunk", "/clone/finish", "/clone/abort"):
        assert PREFIX + route in paths
    module = sys.modules["hermes_dashboard_plugin_fish-audio"]
    assert Path(module.__file__).is_relative_to(home / "plugins" / "fish-audio")
    assert not any(name.startswith("hermes_plugins") and "fish_audio_dashboard" in name for name in sys.modules)

    assert TestClient(server.app).get(PREFIX + "/available").status_code == 401  # the dashboard's auth, not ours
    client = TestClient(server.app, headers={server._SESSION_HEADER_NAME: server._SESSION_TOKEN})
    available = client.get(PREFIX + "/available")
    assert available.status_code == 200
    assert available.json() == {"ok": True, "plugin": "fish-audio", "version": module.VERSION, "key": True}
    # The routes load the plugin's own package under a private name, beside (not inside) the agent loader's.
    assert module._PACKAGE in sys.modules and not module._PACKAGE.startswith("hermes_plugins")
    assert Path(sys.modules[module._PACKAGE].__file__).is_relative_to(home / "plugins" / "fish-audio")

    profiles = {}
    for name, key, base in (("a", "key-a", "http://127.0.0.1:8501"), ("b", "key-b", "http://localhost:8502")):
        profile_home = home / "profiles" / name
        profile_home.mkdir(parents=True)
        (profile_home / ".env").write_text(f"FISH_API_KEY={key}\n")
        (profile_home / "config.yaml").write_text(
            f'plugins:\n  enabled: [fish-audio]\n  entries:\n    fish-audio:\n      settings:\n        base_url: "{base}"\n'
            "tts:\n  provider: fish-audio\n  fish-audio:\n    model: s2.1-pro\n")
        profiles[name] = (profile_home, key, base)

    per_request = hasattr(dashboard, "_plugin_route_secret_scope")
    if per_request:
        import tui_gateway.launch_profile_policy as policy
        monkeypatch.setattr(policy, "_snapshot", None)

    @contextmanager
    def scope(name):
        profile_home = profiles[name][0]
        if per_request:
            yield {"profile": name}
            return
        home_token = set_hermes_home_override(str(profile_home))
        secret_token = set_secret_scope(build_profile_secret_scope(profile_home), profile_home=str(profile_home))
        try:
            yield {}
        finally:
            reset_secret_scope(secret_token)
            reset_hermes_home_override(home_token)

    launch_config = (home / "config.yaml").read_text()
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        with respx.mock(assert_all_called=True) as mock:
            for name in ("a", "b", "a"):
                profile_home, key, base = profiles[name]
                voice = VOICES[name]
                mock.get(base + "/model").respond(json={"items": [{"_id": voice, "title": name}]})
                mock.get(base + f"/model/{voice}").respond(json={"_id": voice})
                with scope(name) as params:
                    body = client.get(PREFIX + "/voices", params=params).json()
                    assert body["ok"] and body["items"][0]["id"] == voice, body
                    body = client.post(PREFIX + "/use", params=params, json={"voice": voice}).json()
                    assert body["ok"] and body["voice"] == voice, body
                for call in mock.calls[-2:]:
                    assert call.request.headers["authorization"] == f"Bearer {key}"
                    assert str(call.request.url).startswith(base)
                assert key not in body["message"]
            assert len(mock.calls) == 6
    finally:
        set_multiplex_active(previous)
    for name, (profile_home, _, _) in profiles.items():
        written = (profile_home / "config.yaml").read_text()
        assert VOICES[name] in written and VOICES["b" if name == "a" else "a"] not in written
    assert (home / "config.yaml").read_text() == launch_config  # the launch profile was never written
