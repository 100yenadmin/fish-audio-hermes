"""Model argument ergonomics, with synthetic files and no Fish traffic."""
import importlib
from collections import OrderedDict
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest
import respx

from fish_audio import client, hooks, media, settings, tools


@pytest.fixture(autouse=True)
def context(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("fish_audio.tool_support.fish_api_key", lambda: "test-key")
    monkeypatch.setattr(settings, "_config", lambda: {})
    monkeypatch.setattr(settings, "cached_wallet", lambda *a: None)
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(media, "audio_output_dir", lambda: cache)
    monkeypatch.setattr(hooks, "audio_output_dir", lambda: cache)
    monkeypatch.setattr(hooks, "_PENDING", OrderedDict())
    safety = ModuleType("agent.file_safety")
    safety.is_write_denied = lambda path: False
    monkeypatch.setitem(sys.modules, "agent.file_safety", safety)
    calls = []

    def synth(params, key, base, path, *, events=None):
        calls.append(params)
        Path(path).write_bytes(b"synthetic audio")
        if events is not None:
            events.append({"content": "hello", "alignment": {
                "segments": [{"text": "hello", "start": 0, "end": 1}]}})
    monkeypatch.setattr(client, "tts_to_file", synth)
    with respx.mock(assert_all_called=False) as mock:
        yield calls, safety, mock


def speak(**args):
    return json.loads(tools.fish_speak({"text": "hello", **args}, session_id="s1"))


def test_shared_copy_output(tmp_path):
    output = importlib.import_module("fish_audio.output")
    source, target = tmp_path / "cached.mp3", tmp_path / "out.mp3"
    source.write_bytes(b"audio")
    result = {"file_path": str(source)}
    output._copy_output(result, target, force=False)
    assert target.read_bytes() == b"audio" and result["out_path"] == str(target)


@pytest.mark.parametrize("suffix", ["ogg", "mp3", "wav", "MP3"])
@pytest.mark.parametrize("timestamps", [False, True])
def test_output_path_copies_and_keeps_cached_delivery(tmp_path, context, suffix, timestamps):
    target = tmp_path / ("out." + suffix)
    result = speak(output_path=str(target), timestamps=timestamps)
    assert result["success"], result
    assert target.read_bytes() == b"synthetic audio"
    assert result["out_path"] == str(target)
    cached = Path(result["file_path"])
    assert cached.parent == tmp_path / "cache" and cached != target
    assert result["media_tag"].endswith(f"MEDIA:{cached}")
    assert hooks.take("s1")[0][0] == str(cached)
    assert context[0][0]["format"] == ("opus" if suffix == "ogg" else suffix.lower())
    for ext in ("srt", "vtt"):
        if timestamps:
            assert target.with_suffix("." + ext).read_bytes() == cached.with_suffix("." + ext).read_bytes()
            assert result["out_" + ext + "_path"] == str(target.with_suffix("." + ext))
        else:
            assert "out_" + ext + "_path" not in result


@pytest.mark.parametrize("case,needle", [
    ("exists", "exists"), ("symlink", "symlink"), ("missing-parent", "missing"),
    ("parent", ".."), ("newline", "output_path"), ("separator", "output_path"),
    ("control", "output_path"), ("media", "output_path"), ("bad-suffix", "ogg"),
    ("mismatch", "does not match"), ("srt", "audio file name"), ("vtt", "audio file name"),
    ("empty", "output_path"), ("nonstring", "output_path"), ("unwritable", "not writable"),
    ("protected", "output_path is a protected location. Choose a normal folder."),
    ("protected-srt", "protected location"), ("protected-vtt", "protected location"),
    ("srt-exists", "exists"), ("vtt-exists", "exists"),
    ("old-hermes", "output_path needs a newer Hermes; copy the file from file_path instead."),
])
def test_output_path_refused_before_any_fish_call(tmp_path, monkeypatch, context, case, needle):
    calls, safety, mock = context
    target = tmp_path / "out.mp3"
    args = {"timestamps": True}
    if case == "exists":
        target.write_bytes(b"theirs")
    elif case == "symlink":
        target.symlink_to(tmp_path / "missing.mp3")
    elif case == "missing-parent":
        target = tmp_path / "missing" / "out.mp3"
    elif case == "parent":
        target = tmp_path / ".." / "out.mp3"
    elif case in {"newline", "separator", "control", "media"}:
        target = tmp_path / ({"newline": "a\nb", "separator": "a\u2028b", "control": "a\x7fb",
                              "media": "mEdIa:clip"}[case] + ".mp3")
    elif case in {"bad-suffix", "srt", "vtt"}:
        target = target.with_suffix({"bad-suffix": ".txt", "srt": ".srt", "vtt": ".vtt"}[case])
    elif case == "mismatch":
        args["format"] = "ogg"
    elif case in {"srt-exists", "vtt-exists"}:
        target.with_suffix("." + case.split("-")[0]).write_text("theirs")
    elif case.startswith("protected"):
        safety.is_write_denied = lambda path: case == "protected" or path.endswith("." + case.split("-")[1])
    elif case == "old-hermes":
        monkeypatch.setitem(sys.modules, "agent.file_safety", None)
    elif case == "unwritable":
        monkeypatch.setattr("os.access", lambda *a: False)
    value = "" if case == "empty" else 123 if case == "nonstring" else str(target)
    monkeypatch.setattr(settings, "resolve_model_source", lambda *a, **kw: pytest.fail("model lookup before refusal"))
    result = speak(output_path=value, **args)
    assert not result["success"] and needle in result["error"], result
    assert not calls and not mock.calls and not hooks.take("s1")


@pytest.mark.parametrize("kind", ["relative", "home"])
def test_output_path_expands_and_resolves(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = speak(output_path="out.mp3" if kind == "relative" else "~/out.mp3")
    assert result["success"] and result["out_path"] == str(tmp_path / "out.mp3")


def test_copy_failure_keeps_billed_cache_and_record(tmp_path, monkeypatch, context):
    synth = client.tts_to_file
    target = tmp_path / "out.mp3"

    def race(*args, **kwargs):
        synth(*args, **kwargs)
        target.write_bytes(b"theirs")
    monkeypatch.setattr(client, "tts_to_file", race)
    result = speak(output_path=str(target))
    assert result["success"] is False
    assert result["error"].startswith(f"The audio is billed and saved at {result['file_path']}; output_path failed:")
    assert "--force" not in result["error"]
    assert Path(result["file_path"]).read_bytes() == b"synthetic audio"
    assert hooks.take("s1") and target.read_bytes() == b"theirs" and len(context[0]) == 1


@pytest.mark.parametrize("args,error", [
    ({"path": "same.ogg"}, None), ({"path": "same.ogg", "file_path": "same.ogg"}, None),
    ({"path": "other.ogg", "file_path": "same.ogg"}, "Give file_path or path, not both."),
    ({}, "file_path is required (the audio file to transcribe)."),
])
def test_transcribe_path_alias(monkeypatch, args, error):
    from fish_audio import transcribe
    seen = []

    def execute(fields, *a):
        seen.append(fields)
        return {"text": "hello"}
    monkeypatch.setattr(transcribe, "execute", execute)
    result = json.loads(tools.fish_transcribe(args))
    if error:
        assert result == {"success": False, "error": error} and not seen
    else:
        assert result["success"] and seen == [{"file_path": "same.ogg"}]
    assert "path" not in args or args["path"].endswith(".ogg")


@pytest.mark.parametrize("name", list(tools.SCHEMAS))
def test_unknown_arguments_name_accepted_parameters(name, context):
    result = json.loads(getattr(tools, name)({"zzz": 1, "aaa": 2}))
    assert result == {"success": False, "error": f"Unknown parameter(s) for {name}: aaa, zzz. "
                      f"Accepted: {', '.join(sorted(tools.SCHEMAS[name]))}."}
    assert not context[0] and not context[2].calls


def test_tool_argument_schemas():
    assert tools.REQUIRED["fish_transcribe"] == []
    assert tools.SCHEMAS["fish_transcribe"]["path"] == {"type": "string", "description": "Same as file_path."}
    assert tools.SCHEMAS["fish_speak"]["output_path"] == {"type": "string", "description":
        "Optional file to also save the audio to (.ogg, .mp3 or .wav). Never overwrites an existing file. "
        "Voice delivery still sends the cached copy."}
