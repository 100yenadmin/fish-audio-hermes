"""Design receipt ownership and operation-local cleanup, with synthetic Fish responses only."""
import base64
import json
from pathlib import Path

import pytest
import respx

from fish_audio import media, settings, voices
from fish_audio.tool_support import ToolInputError
from test_commands import config

BASE = "https://api.fish.audio"
WAV = b"RIFF\x10\0\0\0WAVEsynthetic-audio"
CANDIDATE = {"signature": "synthetic-signature", "audio_base64": base64.b64encode(WAV).decode()}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    voices._DESIGNS.clear()


def test_design_accepts_and_sends_fish_character_limits():
    args = {"action": "design", "instruction": "x" * 2000, "reference_text": "r" * 150}
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/voice-design").respond(json={"candidates": []})
        voices.execute(args, "synthetic-A", BASE, "")
        sent = json.loads(route.calls.last.request.content)
        assert sent["instruction"] == args["instruction"] and sent["reference_text"] == args["reference_text"]


@pytest.mark.parametrize("fields,message", [
    ({"instruction": "x" * 2001}, "instruction must be 1–2000 characters."),
    ({"instruction": "x" * 2000 + " "}, "instruction must be 1–2000 characters."),
    ({"reference_text": "r" * 151}, "reference_text must be at most 150 characters."),
    ({"reference_text": 123}, "reference_text must be at most 150 characters."),
    ({"reference_text": None}, "reference_text must be at most 150 characters."),
])
def test_design_refuses_invalid_character_limits_before_request(fields, message):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(BASE + "/v1/voice-design").respond(json={"candidates": []})
        with pytest.raises(ToolInputError, match=message):
            voices.execute({"action": "design", "instruction": "warm", **fields}, "synthetic-A", BASE, "")
        assert not mock.calls


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


@pytest.mark.parametrize("args", [{"action": "update", "voice_id": "a" * 32, "title": "x"},
                                  {"action": "delete", "voice_id": "a" * 32}])
def test_operator_account_refuses_account_wide_voice_management(monkeypatch, args):
    # One operator key can serve many agents: its own voices, edits and deletes span all of them.
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    with respx.mock(assert_all_called=False) as mock:
        with pytest.raises(ToolInputError, match="managed by its operator"):
            voices.execute(args, "synthetic-A", BASE, "")
        assert not mock.calls
    monkeypatch.setattr(settings, "operator_account", lambda: False)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(BASE + "/model").respond(json={"items": [], "total": 0})
        mock.patch(BASE + "/model/" + "a" * 32).respond(json={})
        mock.delete(BASE + "/model/" + "a" * 32).respond(json={})
        voices.execute(args, "synthetic-A", BASE, "")
        assert len(mock.calls) == 1


@pytest.mark.parametrize("count,status", [(0, 200), (2, 200), (2, 404), (2, 503), (22, 200)])
def test_operator_mine_profile_only(monkeypatch, count, status):
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    saved = {f"voice{i:02}": f"{i:032x}" for i in range(count)}
    if count:
        saved["z-duplicate"] = saved["voice00"]
    monkeypatch.setattr(settings, "aliases", lambda: saved)
    monkeypatch.setattr(voices.client.time, "sleep", lambda *a: None)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(BASE + "/model").mock(side_effect=AssertionError("account list forbidden"))
        for i, ident in enumerate(list(dict.fromkeys(saved.values()))[:20]):
            mock.get(BASE + "/model/" + ident).respond(status, json={"_id": ident, "title": str(i), "languages": ["ja"]})
        result = voices.execute({"action": "mine"}, "synthetic-A", BASE, "")
        assert len(mock.calls) == min(count, 20) * (3 if status == 503 else 1)
        assert all(c.request.url.path.startswith("/model/") and not c.request.url.query for c in mock.calls)
    assert result["profile_voices"] and len(result["items"]) == count
    assert [r["name"] for r in result["items"]] == sorted(saved)[:count]
    for i, item in enumerate(result["items"]):
        assert item["id"] == saved[item["name"]]
        if status == 200 and i < 20:
            assert item["title"] == str(i) and item["languages"] == ["ja"]
        else:
            assert item == {"id": saved[item["name"]], "name": item["name"], "detail": "unavailable",
                            **({"names": ["voice00", "z-duplicate"]} if i == 0 else {})}
    if count:
        assert result["items"][0]["names"] == ["voice00", "z-duplicate"]
    else:
        assert "operator assigns voices with saved names" in result["note"]


@pytest.mark.parametrize("action", ["clone", "save"])
@pytest.mark.parametrize("title,condition,expected", [
    ("ナレーター", "ok", "ナレーター"), (" 123 !! Warm,  Voice!", "ok", "warm-voice-"),
    ("猫" * 40, "ok", "猫" * 32), ("ＷＡＲＭ", "taken", None),
    ("Warm", "limit", None), ("Warm", "managed", None), ("123!", "invalid", None),
    ("Warm", "personal", None), ("Warm", "no-id", None),
])
def test_new_voice_saved_names(config, monkeypatch, action, title, condition, expected):
    monkeypatch.setattr(settings, "operator_account", lambda: condition != "personal")
    monkeypatch.setattr(settings, "managed_leaf", lambda *p: condition == "managed")
    saved = {"Warm": "b" * 32} if condition == "taken" else (
        {f"voice{i}": "b" * 32 for i in range(settings.MAX_ALIASES)} if condition == "limit" else {})
    config[0]["tts"]["fish-audio"] = {"aliases": saved.copy()}
    with respx.mock(assert_all_called=True) as mock:
        mock.post(BASE + "/model").respond(json={"title": title, **({} if condition == "no-id" else {"_id": "a" * 32})})
        args = {"action": action, "title": title, "consent": True,
                "sample_paths": [str(Path(__file__).parent / "fixtures/synthetic.mp3")]}
        if action == "save":
            mock.post(BASE + "/v1/voice-design").respond(json={"candidates": [CANDIDATE]})
            args["design_token"] = voices.execute({"action": "design", "instruction": "warm"}, "synthetic-A", BASE, "")["candidates"][0]["design_token"]
        result = voices.execute(args, "synthetic-A", BASE, "")
    if expected:
        assert result["saved_name"] == expected and settings.aliases()[expected] == "a" * 32
    else:
        assert config[0]["tts"]["fish-audio"]["aliases"] == saved and "saved_name" not in result
        if condition not in {"personal", "no-id"}:
            assert "operator can add one" in result["saved_name_note"]
            assert {"taken": "taken", "limit": "limit", "managed": "managed", "invalid": "valid"}[condition] in result["saved_name_note"]
        else:
            assert "saved_name_note" not in result
