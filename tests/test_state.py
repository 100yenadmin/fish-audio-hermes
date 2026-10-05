from datetime import datetime

import pytest

from fish_audio import client, commands, settings, state, stt, tts
from fish_audio.errors import FishAudioError


@pytest.mark.parametrize("provider", ["tts", "stt"])
def test_provider_records_fish_failure(provider, monkeypatch, tmp_path):
    monkeypatch.setattr(state, "_last", None)
    monkeypatch.setattr(tts, "fish_api_key", lambda: "test-key")
    monkeypatch.setattr(stt, "fish_api_key", lambda: "test-key")
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: None)
    monkeypatch.setattr(settings, "_config", lambda: {})
    def fail(*args, **kwargs):
        raise FishAudioError("quota", 402, "fish-id", "Top up Fish API credits.")
    if provider == "tts":
        monkeypatch.setattr(client, "tts_to_file", fail)
        with pytest.raises(FishAudioError):
            tts.FishAudioTTSProvider().synthesize("hello", str(tmp_path / "output.ogg"))
    else:
        path = tmp_path / "input.ogg"
        path.write_bytes(b"OggSsynthetic")
        monkeypatch.setattr(client, "transcribe_audio", fail)
        assert not stt.FishAudioTranscriptionProvider().transcribe(path)["success"]
    receipt = state.last_failure()
    assert datetime.fromisoformat(receipt[0]).tzinfo is not None
    assert receipt[1] == "quota" and "request id fish-id" in receipt[2]
    monkeypatch.setattr(commands.account, "get_package", lambda *args: None)
    monkeypatch.setattr(commands.account, "cached_wallet", lambda *args: None)
    assert "quota" in commands.status("test-key")


def test_failure_receipt_redacts_live_key(monkeypatch):
    monkeypatch.setenv("FISH_API_KEY", "sk-" + "x" * 48)
    state.record_failure("credential", "Do not show " + "sk-" + "x" * 48)
    assert "sk-" + "x" * 48 not in state.last_failure()[2]
