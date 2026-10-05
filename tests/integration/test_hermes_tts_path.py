"""Real Hermes loader -> registry -> ordinary TTS tool, with only HTTP mocked."""
import importlib.util
import json
from pathlib import Path
import shutil

import httpx
import pytest
import respx

# Collection in the standalone unit venv must not import Hermes.
pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")
ROOT = Path(__file__).resolve().parents[2]
VOICE = "b" * 32


@pytest.fixture
def installed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    mount = home / "plugins" / "fish-audio"
    mount.mkdir(parents=True)
    for name in ("__init__.py", "plugin.yaml", "pyproject.toml", "README.md", "LICENSE", ".gitignore"):
        shutil.copy2(ROOT / name, mount / name)
    # Include the whole publishable tree, while keeping ignored local harnesses out.
    for name in ("fish_audio", "tests", ".github"):
        shutil.copytree(ROOT / name, mount / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (home / "config.yaml").write_text(
        "plugins:\n  enabled: [fish-audio]\ntts:\n  provider: fish-audio\n"
        f"  fish-audio:\n    voice: {VOICE}\n", encoding="utf-8")
    (home / ".env").write_text("FISH_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("FISH_API_KEY", "")
    monkeypatch.delenv("HERMES_SAFE_MODE", raising=False)
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS", raising=False)
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    # Fail immediately if an unmocked socket is attempted, including registration.
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError("integration must remain offline")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import set_secret_scope, reset_secret_scope, load_env_file
    token = set_hermes_home_override(home)
    secret_token = set_secret_scope(load_env_file(home / ".env"), profile_home=str(home))
    try:
        from hermes_cli.plugins import discover_plugins, get_plugin_manager
        discover_plugins()
        from agent.tts_registry import get_provider
        from agent.tts_provider import TTSProvider
        provider = get_provider("fish-audio")
        assert provider is not None and isinstance(provider, TTSProvider)
        assert provider.__class__.__name__ == "FishAudioTTSProvider"
        assert Path(__import__(provider.__class__.__module__, fromlist=["__file__"]).__file__).is_relative_to(mount)
        assert provider.is_available()
        yield home, provider
    finally:
        get_plugin_manager().unload()
        reset_secret_scope(secret_token)
        reset_hermes_home_override(token)


def test_ordinary_tts_delivers_mocked_fish_audio(installed):
    home, provider = installed
    audio = (ROOT / "tests" / "fixtures" / "synthetic.mp3").read_bytes()
    from tools.tts_tool import text_to_speech_tool
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.fish.audio/v1/tts").respond(content=audio, headers={"content-type": "audio/mpeg"})
        result = json.loads(text_to_speech_tool(text="hello"))
        assert result["success"] is True, result
        assert result["provider"] == "fish-audio"
        delivered = Path(result["file_path"])
        assert delivered.is_relative_to(home)
        assert delivered.read_bytes() == audio
        assert f"MEDIA:{delivered}" in result["media_tag"]
        assert route.call_count == 1
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer test-key"
        assert dict(request.headers)["model"] == "s2.1-pro-free"
        assert request.headers["user-agent"] == "fish-audio-hermes/0.0.1 (hermes-agent/0.21.5)"
        payload = json.loads(request.content)
        assert payload["reference_id"] == VOICE and payload["text"] == "hello"
        assert payload["format"] == "mp3"


def test_missing_profile_key_returns_setup_envelope(installed):
    home, provider = installed
    (home / ".env").write_text("", encoding="utf-8")
    from agent.secret_scope import set_secret_scope, reset_secret_scope, load_env_file
    from tools.tts_tool import text_to_speech_tool
    token = set_secret_scope(load_env_file(home / ".env"), profile_home=str(home))
    try:
        assert not provider.is_available()
        with respx.mock as mock:
            result = json.loads(text_to_speech_tool(text="hello"))
            assert result["success"] is False
            for hint in ("hermes tools", "Desktop ▸ Plugins ▸ Fish Audio", "https://fish.audio/app/api-keys"):
                assert hint in result["error"]
            assert not mock.calls
    finally:
        reset_secret_scope(token)
