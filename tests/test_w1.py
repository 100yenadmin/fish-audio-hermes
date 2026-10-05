"""W1 parity requests: synthetic inputs and respx only."""
import copy
import json
from pathlib import Path

import pytest
import respx

from fish_audio import hooks, media, settings, tools, transcribe, voices
from fish_audio.tool_support import ToolInputError
from scripts import parity_check as parity

BASE = "https://api.fish.audio"
VOICE = "a" * 32
PNG = b"\x89PNG\r\n\x1a\nsynthetic"
KNOBS = dict(temperature=0.4, top_p=0.8, latency="low", normalize=False, chunk_length=200,
             min_chunk_length=50, sample_rate=24000, mp3_bitrate=192, opus_bitrate=32000,
             max_new_tokens=100, repetition_penalty=1.2, condition_on_previous_chunks=False,
             early_stop_threshold=0.3, volume=0.7, normalize_loudness=False, features=["synthetic"],
             pronunciation_dictionary=[{"items": [{"key": "Fish", "value": "fish"}]}])


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"fish-audio": {"model": "s2.1-pro"}}})
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    hooks._PENDING.clear()


@pytest.mark.parametrize("timestamps", [False, True])
def test_speak_configured_knobs(timestamps, monkeypatch):
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"fish-audio": {**KNOBS, "model": "s2.1-pro", "speed": 1.4}}})
    endpoint = "/v1/tts/stream/with-timestamp" if timestamps else "/v1/tts"
    content = b'event: message\ndata: {"audio_base64":"SUQz"}\n\n' if timestamps else b"ID3"
    with respx.mock() as mock:
        route = mock.post(BASE + endpoint).respond(content=content)
        tools._speak({"text": "hi", "timestamps": timestamps, "speed": 0.9}, "synthetic", BASE, "")
        body = json.loads(route.calls.last.request.content)
        assert set(KNOBS) == set(settings.KNOBS)
        for name, value in KNOBS.items():
            assert (body["prosody"] if name in {"volume", "normalize_loudness"} else body)[name] == value
        assert body["prosody"]["speed"] == 0.9


@pytest.mark.parametrize("configured", [[{"items": [{"key": "Fish", "value": "old"}, {"key": "Keep", "value": "keep"}]}],
                                         [{"id": "synthetic", "version": "v1"}]])
def test_pronunciations_merge_or_replace(configured, monkeypatch):
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"fish-audio": {"model": "s2.1-pro", "pronunciation_dictionary": configured}}})
    with respx.mock() as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"ID3")
        tools._speak({"text": "Fish", "pronunciations": {"Fish": "new"}}, "synthetic", BASE, "")
        dictionary = json.loads(route.calls.last.request.content)["pronunciation_dictionary"]
        expected = {"Fish": "new", **({"Keep": "keep"} if "items" in configured[0] else {})}
        assert dictionary == [{"items": [{"key": k, "value": v} for k, v in expected.items()]}]
        assert settings._dictionary(dictionary)


def test_invalid_configured_knob_warns_and_drops(monkeypatch, caplog):
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"fish-audio": {"model": "s2.1-pro", "temperature": True}}})
    monkeypatch.setattr(settings, "_warned", set())
    with respx.mock() as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"ID3")
        tools._speak({"text": "hi"}, "synthetic", BASE, "")
        assert "temperature" not in json.loads(route.calls.last.request.content)
        assert "Ignoring invalid Fish Audio setting: temperature" in caplog.text


def test_design_knobs_sent_and_schema():
    knobs = dict(num_step=128, guidance_scale=1e20, instruct_guidance_scale=0)
    with respx.mock() as mock:
        route = mock.post(BASE + "/v1/voice-design").respond(json={"candidates": []})
        voices.execute({"action": "design", "instruction": "warm", **knobs}, "synthetic", BASE, "")
        assert json.loads(route.calls.last.request.content) == {"instruction": "warm", "n": 2, **knobs}
    assert all(name in tools.SCHEMAS["fish_voices"] for name in knobs)


@pytest.mark.parametrize("name,bad", [("num_step", v) for v in (0, 129, True, 1.5)] +
    [(n, v) for n in ("guidance_scale", "instruct_guidance_scale") for v in (-1, True, float("nan"), float("inf"))])
def test_design_knobs_refused(name, bad):
    with respx.mock() as mock:
        with pytest.raises(ToolInputError):
            voices.execute({"action": "design", "instruction": "warm", name: bad}, "synthetic", BASE, "")
        assert not mock.calls


@pytest.mark.parametrize("codes", ["en-US", ["en", "ja"]])
def test_search_filters_sent(codes):
    with respx.mock() as mock:
        route = mock.get(BASE + "/model").respond(json={"items": [], "has_more": True})
        result = voices.execute({"action": "search", "author_id": "author_-1", "title_language": codes, "licensed": False}, "synthetic", BASE, "")
        params = route.calls.last.request.url.params
        assert params["author_id"] == "author_-1" and params["licensed"] == "false"
        assert params.get_list("title_language") == ([codes] if isinstance(codes, str) else codes)
        assert result["has_more"] is True


@pytest.mark.parametrize("name", ["author_id", "title_language", "licensed"])
def test_mine_refuses_search_filters(name):
    with respx.mock() as mock:
        with pytest.raises(ToolInputError, match="apply to search only"):
            voices.execute({"action": "mine", name: "en"}, "synthetic", BASE, "")
        assert not mock.calls


@pytest.mark.parametrize("action", ["search", "mine"])
@pytest.mark.parametrize("value", [True, False, 1, "true", None])
def test_has_more_boolean_only(action, value):
    with respx.mock() as mock:
        mock.get(BASE + "/model").respond(json={"items": [], "has_more": value})
        result = voices.execute({"action": action}, "synthetic", BASE, "")
        assert ("has_more" in result) == (type(value) is bool)
        if type(value) is bool:
            assert result["has_more"] is value


@pytest.mark.parametrize("args", [{"author_id": "a" * 65}, {"author_id": True}, {"author_id": "a/b"},
    {"title_language": []}, {"title_language": ["en"] * 11}, {"title_language": ["bad_code"]}, {"licensed": 1}])
def test_search_invalid_filters_refused(args):
    with respx.mock() as mock:
        with pytest.raises(ToolInputError):
            voices.execute({"action": "search", **args}, "synthetic", BASE, "")
        assert not mock.calls


@pytest.mark.parametrize("action", ["clone", "update"])
@pytest.mark.parametrize("kind,header", [("png", PNG), ("jpeg", b"\xff\xd8\xffsynthetic"), ("webp", b"RIFFxxxxWEBPsynthetic")])
def test_cover_visibility_and_generate_sample_sent(tmp_path, action, kind, header):
    cover = tmp_path / ("cover." + kind)
    cover.write_bytes(header)
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"ID3synthetic")
    args = dict(action=action, voice_id=VOICE, title="Voice", visibility="unlist", cover_image_path=str(cover),
                consent=True, sample_paths=[str(sample)], generate_sample=False)
    with respx.mock() as mock:
        route = mock.request("POST" if action == "clone" else "PATCH", BASE + ("/model" if action == "clone" else "/model/" + VOICE)).respond(json={"_id": VOICE})
        voices.execute(args, "synthetic", BASE, "")
        request = route.calls.last.request
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert b'name="visibility"\r\n\r\nunlist' in request.content
        assert f'name="cover_image"; filename="cover.{kind}"'.encode() in request.content
        assert ("Content-Type: image/" + kind).encode() in request.content and header in request.content
        if action == "clone":
            assert b'name="generate_sample"\r\n\r\nfalse' in request.content


@pytest.mark.parametrize("value", [True, False])
def test_clone_generate_sample_boolean(value, tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"ID3synthetic")
    with respx.mock() as mock:
        route = mock.post(BASE + "/model").respond(json={"_id": VOICE})
        voices.execute(dict(action="clone", title="Voice", consent=True, sample_paths=[str(sample)], generate_sample=value), "synthetic", BASE, "")
        assert f'name="generate_sample"\r\n\r\n{str(value).lower()}'.encode() in route.calls.last.request.content


@pytest.mark.parametrize("action", ["clone", "update"])
def test_public_visibility_refused(action):
    with respx.mock() as mock:
        with pytest.raises(ToolInputError, match="Publish publicly from the Fish Audio website"):
            voices.execute(dict(action=action, voice_id=VOICE, title="Voice", visibility="public", consent=True, sample_paths=["unused"]), "synthetic", BASE, "")
        assert not mock.calls


@pytest.mark.parametrize("bad", ["nonimage", "oversized", "symlink", "env", "secrets"])
@pytest.mark.parametrize("action", ["clone", "update"])
def test_unsafe_cover_refused(tmp_path, bad, action):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"ID3synthetic")
    home = tmp_path / "hh"
    (home / "secrets").mkdir(parents=True)
    cover = home / ".env" if bad == "env" else home / "secrets" / "cover.png" if bad == "secrets" else tmp_path / "cover.png"
    if bad == "symlink":
        cover.symlink_to(sample)
    else:
        cover.write_bytes(b"ID3not-image" if bad == "nonimage" else PNG)
        if bad == "oversized":
            with cover.open("ab") as handle:
                handle.truncate(5 * 1024 * 1024 + 1)
    with respx.mock() as mock:
        with pytest.raises(media.InputFileError):
            voices.execute(dict(action=action, voice_id=VOICE, title="Voice", cover_image_path=str(cover), consent=True, sample_paths=[str(sample)]), "synthetic", BASE, "")
        assert not mock.calls


def test_update_visibility_without_cover_is_form_and_gate_unchanged():
    args = dict(action="update", voice_id=VOICE, visibility="unlist")
    assert hooks.on_pre_tool_call("fish_voices", args) is None
    assert hooks.on_pre_tool_call("fish_voices", {**args, "action": "clone"})["rule_key"] == "fish-audio:clone"
    with respx.mock() as mock:
        route = mock.patch(BASE + "/model/" + VOICE).respond(204)
        voices.execute(args, "synthetic", BASE, "")
        assert route.calls.last.request.headers["content-type"] == "application/x-www-form-urlencoded"
        assert route.calls.last.request.content == b"visibility=unlist"


@pytest.mark.parametrize("value", ["English", None, 1])
def test_transcribe_language_passes_string_only(value, tmp_path):
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"ID3synthetic")
    with respx.mock() as mock:
        mock.post(BASE + "/v1/asr").respond(json={"text": "hi", "language": value, "language_code": "en"})
        result = transcribe.execute({"file_path": str(sample)}, "synthetic", BASE, "")
        assert result["language_code"] == "en"
        assert ("language" in result) == isinstance(value, str)
        if isinstance(value, str):
            assert result["language"] == value


def parity_inputs():
    root = Path(__file__).resolve().parents[1]
    import yaml
    return parity.load_spec(root / "parity/fish-openapi.pruned.json"), yaml.safe_load((root / "parity.yaml").read_text())


def test_parity_equivalent_target_and_exclusion():
    spec, mapping = parity_inputs()
    entry = next(e for e in mapping["entries"] if e["path"] == "/v1/asr" and e["location"] == "msgpack" and e["field"] == "audio")
    entry.update(status="equivalent", equivalent_to="form")
    assert parity.check(spec, mapping) == []
    entry["equivalent_to"] = "missing"
    assert any("Equivalent needs" in error for error in parity.check(spec, mapping))
    del entry["equivalent_to"]
    assert any("Equivalent needs" in error for error in parity.check(spec, mapping))
    entry.update(status="excluded", reason="not sent")
    assert parity.check(spec, mapping) == []
    entry["reason"] = " "
    assert any("Excluded needs reason" in error for error in parity.check(spec, mapping))
    entry.pop("reason")
    assert any("Excluded needs reason" in error for error in parity.check(spec, mapping))
    entry.update(status="equivalent", equivalent_to="form")
    target = next(e for e in mapping["entries"] if e["path"] == "/v1/asr" and e["location"] == "form" and e["field"] == "audio")
    target["status"] = "planned"
    assert any("Equivalent needs" in error for error in parity.check(spec, mapping))


def test_large_inline_config_plus_call_pronunciations_split_into_groups(monkeypatch):
    configured = [{"items": [{"key": f"g{g}k{i}", "value": "v"} for i in range(2000)]} for g in range(3)]
    monkeypatch.setattr(settings, "_config", lambda: {"tts": {"fish-audio": {"model": "s2.1-pro",
                                                                             "pronunciation_dictionary": configured}}})
    with respx.mock() as mock:
        route = mock.post(BASE + "/v1/tts").respond(content=b"ID3")
        tools._speak({"text": "hi", "pronunciations": {"Fish": "fish", "g0k0": "new"}}, "synthetic", BASE, "")
        groups = json.loads(route.calls.last.request.content)["pronunciation_dictionary"]
    assert [len(g["items"]) for g in groups] == [5000, 1001]
    items = {i["key"]: i["value"] for g in groups for i in g["items"]}
    assert items["g0k0"] == "new" and items["Fish"] == "fish" and len(items) == 6001


def test_visibility_schema_has_no_default():
    assert "default" not in tools.SCHEMAS["fish_voices"]["visibility"]


@pytest.mark.parametrize("value", [["private"], {"v": "unlist"}, 3, None])
def test_non_string_visibility_is_a_tool_input_error(value):
    with respx.mock() as mock:
        with pytest.raises(ToolInputError, match="visibility must be private or unlist"):
            voices.execute({"action": "update", "voice_id": "a" * 32, "visibility": value}, "synthetic", BASE, "")
        assert not mock.calls


@pytest.mark.parametrize("code", ["zh-Hant-TW", "en", "yue", "sr-Latn"])
def test_title_language_accepts_bcp47_tags(code):
    with respx.mock() as mock:
        route = mock.get(BASE + "/model").respond(json={"items": [], "total": 0})
        voices.execute({"action": "search", "title_language": code}, "synthetic", BASE, "")
        assert route.calls.last.request.url.params.get_list("title_language") == [code]


@pytest.mark.parametrize("code", ["", "e", "en_US", "-en", "en-", "en--US"])
def test_title_language_rejects_malformed_tags(code):
    with respx.mock() as mock:
        with pytest.raises(ToolInputError, match="Invalid title_language"):
            voices.execute({"action": "search", "title_language": code}, "synthetic", BASE, "")
        assert not mock.calls
