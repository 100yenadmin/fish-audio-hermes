"""Auditions use the speech path and retain every completed clip for chat delivery."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from fish_audio import client, hooks, media, settings, tools

BASE = "https://api.fish.audio"
VOICES = [c * 32 for c in "abc"]


@pytest.fixture(autouse=True)
def context(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("fish_audio.tool_support.fish_api_key", lambda: "test-key")
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"fish-audio": {"aliases": {"warm": VOICES[0]}}}})
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: None)
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    monkeypatch.setattr(hooks, "audio_output_dir", lambda: tmp_path)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    hooks._PENDING.clear()


def audition(**args):
    return json.loads(tools.fish_voices({"action": "audition", **args}, session_id="audition"))


def test_two_voices_and_saved_name_record_all_clips():
    with respx.mock as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        result = audition(voices=["warm", VOICES[1]], text="  Our own passage.  ", model="s2.1-pro", language="en-GB")
        assert result["success"] and "failed" not in result
        assert result["text"] == "Our own passage." and result["billing"] == "fish-audio"
        assert result["model"] == "s2.1-pro"
        bodies = [json.loads(c.request.content) for c in route.calls]
        assert [b["reference_id"] for b in bodies] == VOICES[:2]
        assert all(b["text"] == result["text"] and b["language"] == "en-GB" for b in bodies)
        assert all(c.request.headers["model"] == "s2.1-pro" for c in route.calls)
        assert [c["voice"] for c in result["clips"]] == VOICES[:2]
        paths = [c["file_path"] for c in result["clips"]]
        assert len(set(paths)) == 2 and all(Path(p).read_bytes() == b"OggSsynthetic" for p in paths)
        assert [p for p, voice, _ in hooks.take("audition") if voice] == paths
        assert all(c["media_tag"] == "[[audio_as_voice]]\nMEDIA:" + c["file_path"] for c in result["clips"])
        assert "each media_tag verbatim on its own line" in result["note"]
        assert "voice name" in result["note"] and "one short line of text" in result["note"]


@pytest.mark.parametrize("args,error", [
    ({"voices": [VOICES[0], "no"]}, settings.UNKNOWN_VOICE),
    ({"voices": VOICES + ["d" * 32, "e" * 32]}, "1–4"),
    ({"voices": ["warm", VOICES[0]]}, "distinct voices"),
    ({"voices": []}, "1–4"),
    ({"voices": "warm"}, "1–4"),
    ({}, "1–4"),
    ({"voices": [VOICES[0]], "text": "x" * 201}, "1–200"),
    ({"voices": [VOICES[0]], "text": "  "}, "1–200"),
    ({"voices": [VOICES[0]], "text": None}, "1–200"),
    ({"voices": VOICES[:2], "language": "!"}, "BCP-47"),
])
def test_invalid_inputs_refused_before_any_http(args, error):
    with respx.mock as mock:
        result = audition(**args)
        assert not result["success"] and error in result["error"]
        assert not mock.calls and hooks.take("audition") == []


@pytest.mark.parametrize("text", [None, " " + "x" * 200 + " "])
def test_default_text_and_stripped_limit(text):
    with respx.mock as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        result = audition(voices=[VOICES[0]], **({} if text is None else {"text": text}))
        expected = "Hi! This is how I sound." if text is None else text.strip()
        assert result["success"] and result["text"] == expected
        assert json.loads(route.calls.last.request.content)["text"] == expected


@pytest.mark.parametrize("status,attempts,error", [(402, 1, "Top up"), (500, 3, "unavailable")])
def test_partial_failure_keeps_first_clip_and_stops(status, attempts, error):
    with respx.mock as mock:
        route = mock.post(BASE + "/v1/tts").mock(side_effect=[httpx.Response(200, content=b"OggSfirst")] + [
            httpx.Response(status, json={"message": "raw-body-must-not-leak"}) for _ in range(attempts)])
        result = audition(voices=VOICES, model="s2.1-pro")
        assert result["success"] and len(result["clips"]) == 1
        assert result["failed"]["voice"] == VOICES[1] and error in result["failed"]["error"]
        assert "raw-body-must-not-leak" not in json.dumps(result)
        assert [json.loads(c.request.content)["reference_id"] for c in route.calls] == [VOICES[0]] + [VOICES[1]] * attempts
        assert hooks.take("audition")[0][0] == result["clips"][0]["file_path"]


@pytest.mark.parametrize("model", [None, "s2.1-pro"])
def test_operator_allowed_and_model_visibility(monkeypatch, model, fake_ctx):
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    with respx.mock as mock:
        mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        result = audition(voices=VOICES[:2], **({} if model is None else {"model": model}))
        assert result["success"] and len(result["clips"]) == 2
        assert ("model" in result) is (model is not None)
        if model is not None:
            assert result["model"] == model
    assert hooks.on_pre_tool_call("fish_voices", {"action": "audition"}) is None
    tools.register(fake_ctx)
    description = fake_ctx.tools["fish_voices"]["description"]
    assert "audition plays up to 4 voices" in description and "each clip is a billed synthesis" in description
    assert tools.BILLING not in description
    schema = tools.SCHEMAS["fish_voices"]
    assert "audition" in schema["action"]["enum"]
    assert schema["voices"]["minItems"] == 1 and schema["voices"]["maxItems"] == 4


def test_first_voice_failure_is_a_plain_failure():
    # Nothing was made: the result is an ordinary failure, not success with an empty clip list.
    with respx.mock as mock:
        route = mock.post(BASE + "/v1/tts").respond(402, json={"message": "raw-body-must-not-leak"})
        result = audition(voices=VOICES[:2], model="s2.1-pro")
        assert not result["success"] and "Top up" in result["error"] and "clips" not in result
        assert "raw-body-must-not-leak" not in json.dumps(result)
        assert route.call_count == 1
    assert hooks.take("audition") == []


def test_free_model_notice_carried(monkeypatch):
    monkeypatch.setattr(settings, "resolve_model_source", lambda *a, **k: ("s2.1-pro-free", "wallet"))
    with respx.mock as mock:
        mock.post(BASE + "/v1/tts").respond(content=b"OggSsynthetic")
        result = audition(voices=VOICES[:2])
    assert result["success"] and result["model"] == "s2.1-pro-free"
    assert result["notice"] == settings.free_model_notice()
