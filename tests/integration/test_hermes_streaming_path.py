"""Real Hermes streaming resolver and the Desktop speak-stream WebSocket, with only Fish HTTP mocked.

Hermes with the plugin streaming hook (#133723) streams through it; older Hermes speaks sentence by sentence.
"""
import importlib.util
import json
from pathlib import Path
import shutil
from urllib.parse import urlencode

import httpx
import pytest
import respx

pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")
PCM = bytes(range(256)) * 8


def _has_streaming_hook():
    from tools import tts_streaming
    return hasattr(tts_streaming, "_plugin_streamer")


def test_resolver_streams_through_the_plugin_hook_or_falls_back(installed_fish_home):
    home, _, _ = installed_fish_home
    from tools.tts_streaming import _REGISTRY, resolve_streaming_provider
    from tools.tts_tool import _load_tts_config
    assert "fish-audio" not in _REGISTRY  # catalog rule 9: nothing registered into core internals
    streamer = resolve_streaming_provider(_load_tts_config())
    if not _has_streaming_hook():
        assert streamer is None  # whole-file synthesis per sentence
        return
    provider = streamer._provider
    assert type(streamer).__name__ == "_PluginPCMStreamer" and type(provider).__name__ == "FishAudioTTSProvider"
    assert Path(__import__(type(provider).__module__, fromlist=["__file__"]).__file__).is_relative_to(home / "plugins")
    assert (streamer.sample_rate, streamer.channels, streamer.sample_width) == (24000, 1, 2)
    (home / "config.yaml").write_text((home / "config.yaml").read_text().replace(
        "plugins:\n  enabled: [fish-audio]\n",
        "plugins:\n  enabled: [fish-audio]\n  entries:\n    fish-audio:\n      settings:\n        streaming: off\n"))
    assert resolve_streaming_provider(_load_tts_config()) is None


def test_speak_stream_websocket_uses_each_profiles_key(installed_fish_home):
    home, _, _ = installed_fish_home
    if not _has_streaming_hook():
        pytest.skip("this Hermes has no plugin streaming hook; Desktop speaks sentence by sentence")
    from agent.secret_scope import (build_profile_secret_scope, is_multiplex_active, reset_secret_scope,
                                    set_multiplex_active, set_secret_scope)
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from starlette.testclient import TestClient
    from hermes_cli import web_server
    profiles = {"a": ("key-a", "http://127.0.0.1:8501"), "b": ("key-b", "http://localhost:8502")}
    managers = set()
    for name, (key, base) in profiles.items():
        profile = home / "profiles" / name
        profile.mkdir(parents=True)
        shutil.copytree(home / "plugins", profile / "plugins")
        (profile / ".env").write_text(f"FISH_API_KEY={key}\n")
        (profile / "config.yaml").write_text(
            f'plugins:\n  enabled: [fish-audio]\n  entries:\n    fish-audio:\n      settings:\n        base_url: "{base}"\n'
            f'tts:\n  provider: fish-audio\n  fish-audio:\n    voice: "{name * 32}"\n    model: s2.1-pro\n')
        # Each profile loads its own plugin copy (the registry is scoped per home), as the dashboard does.
        home_token = set_hermes_home_override(str(profile))
        secret_token = set_secret_scope(build_profile_secret_scope(profile), profile_home=str(profile))
        try:
            discover_plugins()
            managers.add(get_plugin_manager())
        finally:
            reset_secret_scope(secret_token)
            reset_hermes_home_override(home_token)
    previous_multiplex, previous_auth = is_multiplex_active(), getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    try:
        client = TestClient(web_server.app)
        with respx.mock(assert_all_called=True) as mock:
            routes = {name: mock.post(base + "/v1/tts").mock(side_effect=lambda request: httpx.Response(
                200, content=iter([PCM[:1001], PCM[1001:]]), headers={"content-type": "audio/pcm"}))
                for name, (_, base) in profiles.items()}
            for name in ("a", "b", "a"):
                query = urlencode({"token": web_server._SESSION_TOKEN, "profile": name})
                with client.websocket_connect(f"/api/audio/speak-stream?{query}") as ws:
                    ws.send_text(json.dumps({"text": "Hello from the streaming hook.", "done": True}))
                    assert ws.receive_json() == {"type": "start", "sample_rate": 24000, "channels": 1}
                    frames = []
                    while (message := ws.receive()).get("bytes") is not None:
                        frames.append(message["bytes"])
                    assert json.loads(message["text"]) == {"type": "end"}
                assert b"".join(frames) == PCM and all(len(frame) % 2 == 0 for frame in frames)
                request = routes[name].calls.last.request
                assert request.headers["authorization"] == f"Bearer {profiles[name][0]}"
                body = json.loads(request.content)
                assert body["reference_id"] == name * 32 and body["format"] == "pcm" and body["sample_rate"] == 24000
                assert body["text"] == "Hello from the streaming hook."
            assert routes["a"].call_count == 2 and routes["b"].call_count == 1
    finally:
        for manager in managers:
            manager.unload()
        set_multiplex_active(previous_multiplex)
        if previous_auth is None:
            if hasattr(web_server.app.state, "auth_required"):
                delattr(web_server.app.state, "auth_required")
        else:
            web_server.app.state.auth_required = previous_auth
