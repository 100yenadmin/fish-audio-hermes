"""Real loader, dispatch and hook delivery; Fish HTTP alone is mocked."""
import importlib.util
import json
from pathlib import Path

import pytest
import respx

pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")
BASE = "https://api.fish.audio"


def test_real_registry_tool_and_output_hook(installed_fish_home):
    home, _, _ = installed_fish_home
    from tools.registry import registry
    from hermes_cli.plugins import invoke_hook
    for name in ("fish_speak", "fish_voices", "fish_transcribe"):
        entry = registry.get_entry(name)
        assert entry is not None and entry.toolset == "fish_audio"
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic-audio")
        result = json.loads(registry.dispatch("fish_speak", {"text": "[excited] hello <|speaker:0|>", "model": "s2.1-pro"}, session_id="s1"))
        assert result["success"], result
        path = Path(result["file_path"])
        assert path.is_relative_to(home) and path.suffix == ".ogg" and path.read_bytes() == b"OggSsynthetic-audio"
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer test-key" and request.headers["model"] == "s2.1-pro"
        assert json.loads(request.content)["text"] == "[excited] hello <|speaker:0|>"
    assert all(value is None for value in invoke_hook("transform_llm_output", response_text="B", session_id="other"))
    results = invoke_hook("transform_llm_output", response_text="Here you go.", session_id="s1", model="m", platform="telegram", turn_id="t1")
    assert "Here you go.\n[[audio_as_voice]]\nMEDIA:" + str(path) in results


def test_real_clone_delete_gate_fails_closed_without_human(installed_fish_home, monkeypatch):
    from hermes_cli.plugins import _dispatch_pre_tool_call_hooks
    # No gateway callback or interactive terminal exists in this acceptance process.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    message, modified = _dispatch_pre_tool_call_hooks("fish_voices", {"action": "delete", "voice_id": "x" * 32})
    assert message and "approval" in message.lower() and modified is None
