import json
from pathlib import Path

import pytest
import respx

from fish_audio import media, settings, tools

BASE = "https://api.fish.audio"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def context(monkeypatch, tmp_path):
    monkeypatch.setattr("fish_audio.tool_support.fish_api_key", lambda: "test-key")
    monkeypatch.setattr(settings, "_config", lambda: {})
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)


def call(**args):
    return json.loads(tools.fish_transcribe({"file_path": str(FIXTURES / "synthetic.ogg"), **args}))


@pytest.mark.parametrize("args", [
    {"num_speakers": 2, "min_speakers": 1}, {"num_speakers": 2, "max_speakers": 3},
    {"num_speakers": 2, "model": "transcribe-1"}, {"num_speakers": 2, "diarize": "false"},
    {"min_speakers": 3, "max_speakers": 2}, {"num_speakers": 0}, {"num_speakers": True},
    {"max_speakers": 2.0}, {"model": "Transcribe-1-PRO"}, {"diarize": "bad"},
    {"timestamps": "true"}, {"srt": 1}, {"tag_audio_events": None},
])
def test_field_validation_matrix(args):
    with respx.mock(assert_all_called=True) as mock:
        result = call(**args)
        assert not result["success"] and not mock.calls


@pytest.mark.parametrize("model,file,wire", [("transcribe-1-pro", "synthetic.ogg", "transcribe-1-pro"),
    ("transcribe-1", "synthetic.ogg", "transcribe-1"), ("transcribe-1", "synthetic.webm", "transcribe-1-pro")])
def test_asr_fields_exact_model_timeout_and_raw_markers(model, file, wire):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/asr").respond(json={"text": "<|speaker:0|> [laughter] hello", "duration": 1,
                                "segments": [], "language_code": "en"}, headers={"x-request-id": "from-header"})
        result = call(model=model, file_path=str(FIXTURES / file), language="en", timestamps=False, tag_audio_events=False)
        assert result["success"] and result["text"] == "<|speaker:0|> [laughter] hello"
        assert result["request_id"] == "from-header" and result["language_code"] == "en"
        request = route.calls.last.request
        assert dict(request.headers)["model"] == wire
        assert request.extensions["timeout"]["read"] == 600
        assert b'name="ignore_timestamps"\r\n\r\ntrue' in request.content
        if wire == "transcribe-1-pro":
            assert b'name="tag_audio_events"\r\n\r\nfalse' in request.content
            assert b'name="diarize"\r\n\r\nauto' in request.content
        else:
            assert b'name="tag_audio_events"' not in request.content
            assert b'name="diarize"' not in request.content
        assert (FIXTURES / file).read_bytes() in request.content


@pytest.mark.parametrize("hints", [{"num_speakers": 2}, {"min_speakers": 1, "max_speakers": 3}, {"min_speakers": 1}])
def test_valid_speaker_hints_sent(hints):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/asr").respond(json={"text": "hi", "segments": []})
        assert call(diarize="true", **hints)["success"]
        for key, value in hints.items():
            assert f'name="{key}"\r\n\r\n{value}'.encode() in route.calls.last.request.content


def test_srt_prefers_turns_and_preserves_text(tmp_path):
    data = {"text": "<|speaker:0|> hello", "request_id": "body-id", "duration": 2,
        "speaker_turns": [{"speaker": "speaker:0", "text": "[happy] Hello!", "start": .25, "end": 1.5}],
        "segments": [{"text": "wrong segment", "start": 0, "end": 2}]}
    with respx.mock(assert_all_called=True) as mock:
        mock.post(BASE + "/v1/asr").respond(json=data)
        result = call(srt=True)
        assert result["request_id"] == "body-id"
        path = Path(result["srt_path"])
        assert path.parent == tmp_path and path.suffix == ".srt"
        assert path.read_text() == "1\n00:00:00,250 --> 00:00:01,500\nspeaker:0: [happy] Hello!\n"
        assert result["text"] == data["text"]


def test_srt_groups_segments_to_seven_seconds():
    with respx.mock(assert_all_called=True) as mock:
        mock.post(BASE + "/v1/asr").respond(json={"text": "one two three", "segments": [
            {"text": "one", "start": 0, "end": 2}, {"text": "two", "start": 2, "end": 6.9},
            {"text": "three", "start": 6.9, "end": 8}]})
        result = call(srt=True)
        assert Path(result["srt_path"]).read_text() == (
            "1\n00:00:00,000 --> 00:00:06,900\none two\n\n2\n00:00:06,900 --> 00:00:08,000\nthree\n")


def test_segment_truncation_keeps_count_and_full_srt():
    segments = [{"text": f"word{i}", "start": i, "end": i+1} for i in range(501)]
    with respx.mock(assert_all_called=True) as mock:
        mock.post(BASE + "/v1/asr").respond(json={"text": "long", "segments": segments})
        result = call(srt=True)
        assert len(result["segments"]) == 500 and result["segments_total"] == 501 and result["segments_truncated"]
        assert "word500" in Path(result["srt_path"]).read_text()


def test_transcription_uses_validated_fd_bytes(tmp_path, monkeypatch):
    path = tmp_path / "voice.ogg"
    original = b"OggSsafe-synthetic"
    path.write_bytes(original)
    validate = media.validate_input_file
    def replace_after_validation(*args, **kwargs):
        result = validate(*args, **kwargs)
        path.write_bytes(b"OggSreplacement")
        return result
    monkeypatch.setattr(media, "validate_input_file", replace_after_validation)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(BASE + "/v1/asr").respond(json={"text": "hi"})
        assert call(file_path=str(path))["success"]
        assert original in route.calls.last.request.content and b"replacement" not in route.calls.last.request.content
