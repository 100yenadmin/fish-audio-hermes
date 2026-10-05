"""fish_speak timestamps: SSE parsing, alignment merge, Ogg concatenation and SRT/VTT, over mocked HTTP."""
import base64
import json
from pathlib import Path
import re
import shutil
import subprocess

import httpx
import pytest
import respx

from fish_audio import hooks, media, settings, tools, transcribe

URL = "https://api.fish.audio/v1/tts/stream/with-timestamp"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sse_with_timestamp.json").read_text())["events"]
VOICE = "a" * 32


@pytest.fixture(autouse=True)
def context(monkeypatch, tmp_path):
    monkeypatch.setattr("fish_audio.tool_support.fish_api_key", lambda: "test-key")
    monkeypatch.setattr(settings, "_config", lambda: {})
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: None)
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    monkeypatch.setattr(hooks, "audio_output_dir", lambda: tmp_path)
    hooks._PENDING.clear()


def sse(events, audio_chunks, *, extra=""):
    """Encode events as an SSE body, attaching one audio chunk per event."""
    frames = []
    for event, audio in zip(events, audio_chunks):
        payload = {k: v for k, v in event.items() if k != "audio_bytes"}
        payload["audio_base64"] = base64.b64encode(audio).decode()
        frames.append(f"event: message\ndata: {json.dumps(payload)}\n\n")
    return (extra + "".join(frames)).encode()


def speak(body, **args):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(URL).mock(return_value=httpx.Response(200, content=iter([body[i:i + 777] for i in range(0, len(body), 777)]),
                                                                  headers={"content-type": "text/event-stream"}))
        result = json.loads(tools.fish_speak({"text": "One. Two words. Three more words here.", "timestamps": True,
                                              "voice": VOICE, **args}, session_id="s1"))
        return result, route.calls.last.request


def test_s3_events_merge_last_alignment_and_write_audio(tmp_path):
    chunks = [bytes([i]) * event["audio_bytes"] for i, event in enumerate(FIXTURE)]
    result, request = speak(sse(FIXTURE, chunks, extra=": keepalive\n\n"), format="mp3", pronunciations={"Fish": "fish"})
    assert result["success"], result
    assert request.headers["model"] == "s2.1-pro" and request.headers["authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {"text": "One. Two words. Three more words here.", "reference_id": VOICE,
        "format": "mp3", "prosody": {"speed": 1.0},
        "pronunciation_dictionary": [{"items": [{"key": "Fish", "value": "fish"}]}]}
    assert Path(result["file_path"]).read_bytes() == b"".join(chunks)
    assert result["segments"] == FIXTURE[-1]["alignment"]["segments"]
    assert [s["text"] for s in result["segments"]] == ["One", "Two", "words", "Three", "more", "words", "here"]
    srt = Path(result["srt_path"]).read_text()
    assert srt == ("1\n00:00:00,000 --> 00:00:00,560\nOne.\n\n2\n00:00:01,040 --> 00:00:01,760\nTwo words.\n\n"
                   "3\n00:00:02,320 --> 00:00:03,440\nThree more words here.\n")
    vtt = Path(result["vtt_path"]).read_text()
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:00.560\nOne.\n") and "," not in vtt.split("\n\n")[1]
    assert Path(result["srt_path"]).parent == tmp_path and "v0.2" not in result["note"]


def test_chunk_offsets_shift_segments_and_later_snapshots_win():
    def event(seq, offset, segments, content):
        return {"content": content, "chunk_seq": seq, "chunk_audio_offset_sec": offset,
                "alignment": {"segments": segments, "audio_duration": 1.0} if segments is not None else None}
    events = [event(0, 0.0, None, "Hi there."),
              event(0, 0.0, [{"text": "Hi", "start": 0.0, "end": 0.3}], "Hi there."),
              event(1, 16.24, [{"text": "Bye", "start": 0.0, "end": 0.4}], "Bye now!"),
              event(0, 0.0, [{"text": "Hi", "start": 0.0, "end": 0.3}, {"text": "there", "start": 0.3, "end": 0.9}], "Hi there."),
              event(1, 16.24, None, "Bye now!"),  # a null snapshot never erases the stored one
              event(1, 16.24, [{"text": "Bye", "start": 0.0, "end": 0.4}, {"text": "now", "start": 0.4, "end": 0.8}], "Bye now!")]
    out = Path(media.audio_output_dir()) / "x.ogg"
    result = transcribe.speech_timestamps(events, out)
    assert result["segments"] == [{"text": "Hi", "start": 0.0, "end": 0.3}, {"text": "there", "start": 0.3, "end": 0.9},
                                  {"text": "Bye", "start": 16.24, "end": 16.64}, {"text": "now", "start": 16.64, "end": 17.04}]
    assert "00:00:16,240 --> 00:00:17,040\nBye now!" in Path(result["srt_path"]).read_text()


def test_no_alignment_returns_audio_without_subtitles():
    events = [{"content": "Hi.", "chunk_seq": 0, "chunk_audio_offset_sec": 0.0, "alignment": None, "audio_bytes": 4}]
    result, _ = speak(sse(events, [b"OggS"]), format="ogg")
    assert result["success"] and result["segments"] == [] and "srt_path" not in result


def test_srt_cues_stay_within_seven_seconds_and_close_at_sentences():
    words = [{"text": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(40)]
    words[5]["text"] = "w5."
    srt = transcribe._srt({"segments": words}, sentences=True)
    cues = []
    for block in srt.strip().split("\n\n"):
        _, timing, text = block.split("\n")
        start, end = [sum(float(x) * m for x, m in zip(re.split("[:,]", t)[:3], (3600, 60, 1))) + int(t[-3:]) / 1000
                      for t in timing.split(" --> ")]
        cues.append((start, end, text))
    assert cues[0][2].endswith("w5.") and cues[1][2].startswith("w6")
    assert all(0 < end - start <= 7 for start, end, _ in cues)
    assert all(a[1] <= b[0] for a, b in zip(cues, cues[1:]))
    # Transcription SRT keeps its M1 merging (no sentence split) unless asked.
    assert transcribe._srt({"segments": words[:8]}).count("-->") == 1


@pytest.mark.parametrize("payload", ['{"audio_base64": "!!!", "content": "x"}', "[1, 2]"])
def test_unreadable_stream_is_a_safe_error(payload):
    result, _ = speak(f"data: {payload}\n\n".encode())
    assert not result["success"] and "unreadable timestamp stream" in result["error"]


def test_done_marker_and_multiline_data_and_bad_flag():
    event = dict(FIXTURE[-1], audio_base64=base64.b64encode(b"ID3audio").decode())
    event.pop("audio_bytes")
    text = json.dumps(event, indent=1)
    body = ("".join(f"data: {line}\n" for line in text.splitlines()) + "\ndata: [DONE]\n\ndata: {broken\n\n").encode()
    result, _ = speak(body, format="mp3")
    assert result["success"] and len(result["segments"]) == 7
    with respx.mock(assert_all_called=False) as mock:
        result = json.loads(tools.fish_speak({"text": "hi", "timestamps": "yes"}, session_id="s1"))
        assert not result["success"] and "timestamps must be boolean" in result["error"] and not mock.calls


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg/ffprobe")
def test_ogg_pages_concatenate_to_a_valid_stream(tmp_path):
    source = tmp_path / "source.ogg"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5", "-ac", "1",
                    "-c:a", "libopus", "-b:a", "24k", str(source)], check=True)
    audio = source.read_bytes()
    # Fish's per-event sizes (the fixture), scaled onto this file, cut through Ogg pages.
    sizes = [e["audio_bytes"] for e in FIXTURE]
    cuts = [0] + [len(audio) * sum(sizes[:i + 1]) // sum(sizes) for i in range(len(sizes))]
    chunks = [audio[a:b] for a, b in zip(cuts, cuts[1:])]
    result, request = speak(sse(FIXTURE, chunks), format="ogg")
    assert json.loads(request.content)["format"] == "opus"
    path = Path(result["file_path"])
    assert path.suffix == ".ogg" and path.read_bytes() == audio
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name:format=format_name,duration",
                            "-of", "json", str(path)], capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    assert info["streams"][0]["codec_name"] == "opus" and info["format"]["format_name"] == "ogg"
    assert abs(float(info["format"]["duration"]) - 1.5) < 0.1
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    assert decode.returncode == 0 and not decode.stderr


def test_captions_keep_apostrophes_and_sentence_punctuation():
    content = "I’ve said it’s fine. Don't worry!"
    words = ["Ive", "said", "its", "fine", "Dont", "worry"]
    segments = [{"text": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(words)]
    events = [{"content": content, "chunk_seq": 0, "chunk_audio_offset_sec": 0.0,
               "alignment": {"segments": segments, "audio_duration": 2.4}}]
    result = transcribe.speech_timestamps(events, Path(media.audio_output_dir()) / "x.ogg")
    assert [s["text"] for s in result["segments"]] == words  # raw alignment is unchanged
    srt = Path(result["srt_path"]).read_text()
    assert "\nI’ve said it’s fine.\n" in srt and srt.rstrip().endswith("Don't worry!")


def test_subtitle_failure_keeps_the_billed_audio(tmp_path):
    events = [{"content": "Hi.", "chunk_seq": 0, "chunk_audio_offset_sec": 0.0,
               "alignment": {"segments": [{"text": "Hi"}]}, "audio_bytes": 4}]
    result, _ = speak(sse(events, [b"OggS"]), format="ogg")
    assert result["success"] and Path(result["file_path"]).read_bytes() == b"OggS"
    assert "timestamps could not be processed" in result["timestamps_error"] and "srt_path" not in result


def test_oversized_timestamp_stream_is_refused_before_decoding(monkeypatch):
    from fish_audio import client
    monkeypatch.setattr(client, "SSE_CAP", 2048)
    events = [dict(FIXTURE[-1], audio_bytes=4096)]
    result, _ = speak(sse(events, [b"\x00" * 4096]), format="mp3")
    assert not result["success"] and "exceeds the size cap" in result["error"]


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_timestamp_stream_accepts_every_sse_line_break(newline):
    event = dict(FIXTURE[-1], audio_base64=base64.b64encode(b"ID3audio").decode())
    event.pop("audio_bytes")
    body = f": keepalive{newline}{newline}event: message{newline}data: {json.dumps(event)}{newline}{newline}".encode()
    result, _ = speak(body, format="mp3")  # speak() re-chunks every 777 bytes, splitting CRLF pairs too
    assert result["success"] and Path(result["file_path"]).read_bytes() == b"ID3audio"
    assert len(result["segments"]) == 7


def test_bounded_lines_join_a_crlf_split_across_chunks():
    from fish_audio import client

    class Response:
        def iter_bytes(self):
            yield from [b"a\r", b"", b"\nb\r", b"\rc", b"\xe4\xb8", b"\xad\n", b"d"]
    assert list(client._bounded_lines(Response(), 100)) == ["a", "b", "", "c中", "d"]


@pytest.mark.parametrize("content,token,caption", [("café.", "café", "café."),
                                                  ("हिन्दी।", "हिन्दी", "हिन्दी")])
def test_captions_keep_combining_marks(content, token, caption):
    events = [{"content": content, "chunk_seq": 0, "chunk_audio_offset_sec": 0.0,
               "alignment": {"segments": [{"text": token, "start": 0.0, "end": 0.6}], "audio_duration": 0.6}}]
    result = transcribe.speech_timestamps(events, Path(media.audio_output_dir()) / "x.ogg")
    assert Path(result["srt_path"]).read_text().split("\n")[2] == caption
