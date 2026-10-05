"""S6: real task-local home and secret scope, A -> B -> A, with mocked HTTP."""
import importlib.util
import json

import pytest
import respx

pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")
BASE = "https://api.fish.audio"


def test_two_profile_scope_isolation_and_unscoped_fail_closed(tmp_path, monkeypatch):
    from agent.secret_scope import (build_profile_secret_scope, set_secret_scope, reset_secret_scope,
                                   set_multiplex_active, is_multiplex_active)
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from fish_audio.tts import FishAudioTTSProvider
    from fish_audio.stt import FishAudioTranscriptionProvider
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError("S6 must remain offline")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("FISH_API_KEY", "launch-key")
    profiles = []
    for name, key, voice in (("a", "key-a", "a" * 32), ("b", "key-b", "b" * 32)):
        home = tmp_path / name
        home.mkdir()
        (home / ".env").write_text(f"FISH_API_KEY={key}\n")
        # Explicit model avoids a wallet call and keeps this proof on config + credentials.
        (home / "config.yaml").write_text(f'tts:\n  provider: fish-audio\n  fish-audio:\n    voice: "{voice}"\n    model: s2.1-pro\n')
        profiles.append((home, key, voice))
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        with respx.mock as mock:
            route = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic-audio")
            for i, (home, key, voice) in enumerate((profiles[0], profiles[1], profiles[0])):
                # Same contextvar mechanism as web_server_profiles._config_profile_scope:
                # _hermes_home_scope installs/resets the home, paired with the profile's secret mapping.
                home_token = set_hermes_home_override(str(home))
                secret_token = set_secret_scope(build_profile_secret_scope(home), profile_home=str(home))
                try:
                    path = home / f"proof-{i}.ogg"
                    assert FishAudioTTSProvider().synthesize("hello", str(path)) == str(path)
                    request = route.calls.last.request
                    assert request.headers["authorization"] == f"Bearer {key}"
                    assert json.loads(request.content)["reference_id"] == voice
                finally:
                    reset_secret_scope(secret_token)
                    reset_hermes_home_override(home_token)
            assert route.call_count == 3
        unscoped = set_secret_scope(None)
        try:
            with respx.mock as mock:
                with pytest.raises(ValueError, match="hermes tools"):
                    FishAudioTTSProvider().synthesize("hello", str(tmp_path / "unscoped.ogg"))
                result = FishAudioTranscriptionProvider().transcribe(str(tmp_path / "not-read.ogg"))
                assert not result["success"] and "hermes tools" in result["error"]
                assert "launch-key" not in result["error"] and not mock.calls
        finally:
            reset_secret_scope(unscoped)
    finally:
        set_multiplex_active(previous)
