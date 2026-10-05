"""Design receipt ownership and operation-local cleanup, with synthetic Fish responses only."""
import base64
from pathlib import Path

import pytest
import respx

from fish_audio import media, voices
from fish_audio.tool_support import ToolInputError

BASE = "https://api.fish.audio"
WAV = b"RIFF\x10\0\0\0WAVEsynthetic-audio"
CANDIDATE = {"signature": "synthetic-signature", "audio_base64": base64.b64encode(WAV).decode()}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    voices._DESIGNS.clear()


@pytest.mark.parametrize("other_key,other_base", [("synthetic-B", BASE), ("synthetic-A", "http://localhost:8502")])
def test_design_receipt_refuses_other_key_or_base_then_saves_owner(other_key, other_base):
    with respx.mock(assert_all_called=True) as mock:
        mock.post(BASE + "/v1/voice-design").respond(json={"candidates": [CANDIDATE]})
        result = voices.execute({"action": "design", "instruction": "warm", "n": 1}, "synthetic-A", BASE, "")
        args = {"action": "save", "design_token": result["candidates"][0]["design_token"], "title": "Warm"}
        with pytest.raises(ToolInputError, match="Unknown or expired design token; design again"):
            voices.execute(args, other_key, other_base, "")
        assert not [c for c in mock.calls if c.request.url.path == "/model"]
        saved = mock.post(BASE + "/model").respond(json={"_id": "a" * 32, "title": "Warm"})
        assert voices.execute(args, "synthetic-A", BASE, "")["title"] == "Warm"
        assert saved.call_count == 1


@pytest.mark.parametrize("bad", [{"audio_base64": CANDIDATE["audio_base64"]}, {"signature": "s", "audio_base64": "invalid!"}])
def test_partial_design_validates_all_candidates_before_writing(tmp_path, bad):
    before = dict(voices._DESIGNS)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(BASE + "/v1/voice-design").respond(json={"candidates": [CANDIDATE, bad]})
        with pytest.raises((ToolInputError, ValueError)):
            voices.execute({"action": "design", "instruction": "warm", "n": 2}, "synthetic-A", BASE, "")
    assert list(tmp_path.glob("fish-design-*.wav")) == []
    assert dict(voices._DESIGNS) == before


def test_partial_design_write_failure_removes_only_its_files_and_receipts(tmp_path, monkeypatch):
    other = tmp_path / "fish-design-existing.wav"
    other.write_bytes(b"keep")
    voices._DESIGNS["existing"] = {"created": voices._clock()}
    write = media.atomic_write
    writes = 0
    def fail_second(path, data):
        nonlocal writes
        writes += 1
        write(path, data)
        if writes == 2:
            raise OSError("synthetic write failure")
    monkeypatch.setattr(media, "atomic_write", fail_second)
    with respx.mock(assert_all_called=True) as mock:
        mock.post(BASE + "/v1/voice-design").respond(json={"candidates": [CANDIDATE, CANDIDATE]})
        with pytest.raises(OSError, match="synthetic write failure"):
            voices.execute({"action": "design", "instruction": "warm", "n": 2}, "synthetic-A", BASE, "")
    assert list(tmp_path.glob("fish-design-*.wav")) == [other]
    assert other.read_bytes() == b"keep" and set(voices._DESIGNS) == {"existing"}
