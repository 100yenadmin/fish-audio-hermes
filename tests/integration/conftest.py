"""Install the plugin in an isolated real Hermes home, without network access."""
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[2]
VOICE = "b" * 32


@pytest.fixture
def installed_fish_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    mount = home / "plugins" / "fish-audio"
    mount.mkdir(parents=True)
    for name in ("__init__.py", "plugin.yaml", "pyproject.toml", "README.md", "LICENSE", ".gitignore", "parity.yaml"):
        shutil.copy2(ROOT / name, mount / name)
    for name in ("fish_audio", "tests", ".github", "skills", "scripts", "parity"):
        shutil.copytree(ROOT / name, mount / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (home / "config.yaml").write_text(
        "plugins:\n  enabled: [fish-audio]\ntts:\n  provider: fish-audio\n"
        f"  fish-audio:\n    voice: {VOICE}\n"
        "stt:\n  enabled: true\n  provider: fish-audio\n  fish-audio:\n    model: transcribe-1-pro\n",
        encoding="utf-8")
    (home / ".env").write_text("FISH_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("FISH_API_KEY", "")
    monkeypatch.delenv("HERMES_SAFE_MODE", raising=False)
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS", raising=False)
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError("integration must remain offline")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    # Deferred until a non-skipped integration test uses the fixture.
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import set_secret_scope, reset_secret_scope, load_env_file
    token = set_hermes_home_override(home)
    secret_token = set_secret_scope(load_env_file(home / ".env"), profile_home=str(home))
    try:
        from hermes_cli.plugins import discover_plugins, get_plugin_manager
        discover_plugins()
        from agent.tts_registry import get_provider as get_tts
        from agent.transcription_registry import get_provider as get_stt
        from agent.tts_provider import TTSProvider
        from agent.transcription_provider import TranscriptionProvider
        tts, stt = get_tts("fish-audio"), get_stt("fish-audio")
        for provider, base, expected in ((tts, TTSProvider, "FishAudioTTSProvider"),
                                         (stt, TranscriptionProvider, "FishAudioTranscriptionProvider")):
            assert provider is not None and isinstance(provider, base)
            assert provider.__class__.__name__ == expected
            module = __import__(provider.__class__.__module__, fromlist=["__file__"])
            assert Path(module.__file__).is_relative_to(mount)
            assert provider.is_available()
        yield home, tts, stt
    finally:
        get_plugin_manager().unload()
        reset_secret_scope(secret_token)
        reset_hermes_home_override(token)
