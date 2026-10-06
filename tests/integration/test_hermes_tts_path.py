"""Real Hermes loader -> registry -> ordinary TTS tool, with only HTTP mocked."""
import importlib.util
import json
import re
from pathlib import Path

import httpx
import pytest
import respx

# Collection in the standalone unit venv must not import Hermes.
pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")
ROOT = Path(__file__).resolve().parents[2]
PLUGIN_VERSION = re.search(r"^version: (\S+)$", (ROOT / "plugin.yaml").read_text(), re.M).group(1)
VOICE = "b" * 32


@pytest.fixture
def installed(installed_fish_home):
    home, provider, _ = installed_fish_home
    return home, provider


def test_ordinary_tts_delivers_mocked_fish_audio(installed, monkeypatch):
    home, provider = installed
    audio = (ROOT / "tests" / "fixtures" / "synthetic.mp3").read_bytes()
    # Deliberately undecodable: this fixture proves provider output, not ffmpeg conversion.
    written = []
    synthesize = provider.synthesize
    def capture(*args, **kwargs):
        path = synthesize(*args, **kwargs)
        written.append(Path(path))
        return path
    monkeypatch.setattr(provider, "synthesize", capture)
    from hermes_cli import __version__
    from tools.tts_tool import text_to_speech_tool
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.fish.audio/wallet/self/api-credit?check_free_credit=true").respond(
            json={"credit": "0", "cumulative_top_up": "0", "has_free_credit": False})
        route = mock.post("https://api.fish.audio/v1/tts").respond(content=audio, headers={"content-type": "audio/mpeg"})
        result = json.loads(text_to_speech_tool(text="hello"))
        assert result["success"] is True, result
        assert result["provider"] == "fish-audio"
        delivered = Path(result["file_path"])
        assert delivered.is_relative_to(home)
        assert written[0].read_bytes() == audio
        assert result["voice_compatible"] is False
        assert delivered.suffix == ".mp3"
        assert f"MEDIA:{delivered}" in result["media_tag"]
        assert route.call_count == 1
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer test-key"
        assert dict(request.headers)["model"] == "s2.1-pro-free"
        assert request.headers["user-agent"] == f"fish-audio-hermes/{PLUGIN_VERSION} (hermes-agent/{__version__})"
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
        with respx.mock(assert_all_called=True) as mock:
            result = json.loads(text_to_speech_tool(text="hello"))
            assert result["success"] is False
            for hint in ("hermes tools", "Desktop ▸ Settings ▸ Plugins ▸ Fish Audio", "Capabilities ▸ Plugins on older Desktop", "https://fish.audio/app/api-keys"):
                assert hint in result["error"]
            assert not mock.calls
    finally:
        reset_secret_scope(token)
