"""S6: real task-local home and secret scope, A -> B -> A, with mocked HTTP."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest
import respx

pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")
def test_two_profile_scope_isolation_and_unscoped_fail_closed(tmp_path, monkeypatch, installed_fish_home):
    from agent.secret_scope import (build_profile_secret_scope, set_secret_scope, reset_secret_scope,
                                   set_multiplex_active, is_multiplex_active)
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from fish_audio.tts import FishAudioTTSProvider
    from fish_audio.stt import FishAudioTranscriptionProvider
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from tools.registry import registry
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError("S6 must remain offline")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("FISH_API_KEY", "launch-key")
    profiles = []
    launch_home = installed_fish_home[0]
    managers = set()
    for name, key, voice, base in (("a", "key-a", "a" * 32, "http://127.0.0.1:8501"),
                                   ("b", "key-b", "b" * 32, "http://localhost:8502")):
        home = tmp_path / name
        home.mkdir()
        shutil.copytree(launch_home / "plugins", home / "plugins")
        (home / ".env").write_text(f"FISH_API_KEY={key}\n")
        # Explicit model avoids a wallet call and keeps this proof on config + credentials.
        (home / "config.yaml").write_text(
            f'plugins:\n  enabled: [fish-audio]\n  entries:\n    fish-audio:\n      settings:\n        base_url: "{base}"\n'
            f'tts:\n  provider: fish-audio\n  fish-audio:\n    voice: "{voice}"\n    model: s2.1-pro\n')
        profiles.append((home, key, voice, base))
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        with respx.mock(assert_all_called=True) as mock:
            routes = {base: mock.post(base + "/v1/tts").respond(content=b"OggSsynthetic-audio")
                      for _, _, _, base in profiles}
            for i, (home, key, voice, base) in enumerate((profiles[0], profiles[1], profiles[0])):
                # Same contextvar mechanism as web_server_profiles._config_profile_scope:
                # _hermes_home_scope installs/resets the home, paired with the profile's secret mapping.
                home_token = set_hermes_home_override(str(home))
                secret_token = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
                try:
                    discover_plugins()
                    managers.add(get_plugin_manager())
                    entry = registry.get_entry("fish_speak")
                    assert entry is not None
                    module = __import__(entry.handler.__module__, fromlist=["__file__"])
                    assert Path(module.__file__).is_relative_to(home / "plugins" / "fish-audio")
                    route = routes[base]
                    path = home / f"proof-{i}.ogg"
                    assert FishAudioTTSProvider().synthesize("hello", str(path)) == str(path)
                    request = route.calls.last.request
                    assert request.headers["authorization"] == f"Bearer {key}"
                    assert json.loads(request.content)["reference_id"] == voice
                    assert request.url == base + "/v1/tts"
                    raw = registry.dispatch("fish_speak", {"text": "hello", "model": "s2-pro"}, session_id=f"profile-{home.name}")
                    result = json.loads(raw) if isinstance(raw, str) else raw
                    assert result["success"] and result["model"] == "s2-pro", result
                    assert Path(result["file_path"]).is_relative_to(home)
                    assert Path(result["file_path"]).read_bytes() == b"OggSsynthetic-audio"
                    request = route.calls.last.request
                    assert request.url == base + "/v1/tts"
                    assert request.headers["authorization"] == f"Bearer {key}"
                    assert request.headers["model"] == "s2-pro"
                    assert json.loads(request.content)["reference_id"] == voice
                finally:
                    reset_secret_scope(secret_token)
                    reset_hermes_home_override(home_token)
            assert routes[profiles[0][3]].call_count == 4
            assert routes[profiles[1][3]].call_count == 2
            assert len(mock.calls) == 6
        unscoped = set_secret_scope(None)
        try:
            with respx.mock(assert_all_called=True) as mock:
                with pytest.raises(ValueError, match="hermes tools"):
                    FishAudioTTSProvider().synthesize("hello", str(tmp_path / "unscoped.ogg"))
                result = FishAudioTranscriptionProvider().transcribe(str(tmp_path / "not-read.ogg"))
                assert not result["success"] and "hermes tools" in result["error"]
                assert "launch-key" not in result["error"] and not mock.calls
                raw = registry.dispatch("fish_speak", {"text": "hello"}, session_id="unscoped")
                result = json.loads(raw) if isinstance(raw, str) else raw
                assert not result["success"] and "hermes tools" in result["error"]
                assert "launch-key" not in result["error"] and not mock.calls
        finally:
            reset_secret_scope(unscoped)
    finally:
        for manager in managers:
            manager.unload()
        set_multiplex_active(previous)
