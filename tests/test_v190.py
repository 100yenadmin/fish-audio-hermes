"""Date-aware ASR retirement and account voice filters, without a live transport."""
from datetime import date, datetime, time
from types import SimpleNamespace
from pathlib import Path

import pytest
from fish_audio import cli, client, commands, models, settings, stt, transcribe, voices
from fish_audio.tool_support import ToolInputError


@pytest.mark.parametrize("today,expected", [("2026-12-30", "transcribe-1"), ("2026-12-31", "transcribe-1-pro"), ("2027-01-01", "transcribe-1-pro")])
def test_stt_retirement(monkeypatch, today, expected, tmp_path, capsys):
    day = date.fromisoformat(today)
    monkeypatch.setattr(models, "datetime", SimpleNamespace(now=lambda tz: datetime.combine(day, time(), tz)))
    calls = []
    def asr(*args, **kwargs):
        calls.append(kwargs["model"])
        return {"text": "Hello"}
    monkeypatch.setattr(client, "transcribe_audio", asr)
    monkeypatch.setattr(stt, "fish_api_key", lambda: "synthetic")
    cfg = {"stt": {"fish-audio": {"model": "transcribe-1"}}}
    monkeypatch.setattr(settings, "_config", lambda: cfg)
    path = tmp_path / "audio.ogg"
    path.write_bytes(b"OggSsynthetic")
    assert stt.FishAudioTranscriptionProvider().transcribe(path, model="transcribe-1")["success"]
    transcribe.execute({"file_path": str(Path(__file__).parent / "fixtures/synthetic.ogg"), "model": "transcribe-1"}, "synthetic", "unused", "")
    assert calls == [expected, expected]
    warning = ("transcribe-1 is deprecated: Fish retires it on 2026-12-31 and then serves it as transcribe-1-pro at the same price. Choose transcribe-1-pro."
               if expected == "transcribe-1" else "transcribe-1 has retired; requests use transcribe-1-pro.")
    monkeypatch.setattr(commands, "fish_api_key", lambda: "synthetic")
    monkeypatch.setattr(commands.account, "cached_wallet", lambda *a: None)
    monkeypatch.setattr(commands.account, "get_package", lambda *a: None)
    assert warning in commands.status()
    cli._handle(SimpleNamespace(fish_command="status"))
    assert warning in capsys.readouterr().out
    assert cfg["stt"]["fish-audio"]["model"] == "transcribe-1"


@pytest.mark.parametrize("visibility", ["public", "unlist", "private"])
def test_mine_visibility(monkeypatch, visibility):
    calls = []
    def get(path, params, *args):
        calls.append((path, params))
        return {"items": []}
    monkeypatch.setattr(client, "get_json", get)
    monkeypatch.setattr(settings, "operator_account", lambda: False)
    voices.execute({"action": "mine", "visibility": visibility}, "synthetic", "unused", "")
    assert calls[0] == ("/model", {"page_number": 1, "page_size": 20, "sort_by": "score", "self": "true", "visibility": visibility})
    voices.execute({"action": "search", "visibility": visibility}, "synthetic", "unused", "")
    assert "visibility" not in calls[-1][1]  # R2 preserves other actions' treatment.


@pytest.mark.parametrize("value", ["bad", None, [], {}])
def test_invalid_mine_visibility(monkeypatch, value):
    monkeypatch.setattr(settings, "operator_account", lambda: False)
    with pytest.raises(ToolInputError, match="visibility filter"):
        voices.execute({"action": "mine", "visibility": value}, "synthetic", "unused", "")
