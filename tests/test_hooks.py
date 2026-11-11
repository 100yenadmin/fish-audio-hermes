from pathlib import Path
import json

import pytest

from fish_audio import hooks


@pytest.fixture(autouse=True)
def ledger(monkeypatch, tmp_path):
    hooks._PENDING.clear()
    monkeypatch.setattr(hooks, "audio_output_dir", lambda: tmp_path)
    monkeypatch.setattr(hooks, "_clock", lambda: 100)
    yield
    hooks._PENDING.clear()


def audio(tmp_path, name="test.ogg"):
    path = tmp_path / name
    path.write_bytes(b"OggSsynthetic")
    return path


@pytest.mark.parametrize("delivery,marker", [(True, "[[audio_as_voice]]"), ("file", "[[as_document]]")])
def test_missing_media_appended_once_and_consumed(tmp_path, delivery, marker):
    path = audio(tmp_path)
    hooks.record("a", path, delivery)
    hooks.record("a", path, delivery)
    assert hooks.on_transform_llm_output("Here you go. \n", "a") == f"Here you go.\n{marker}\nMEDIA:{path}"
    assert hooks.on_transform_llm_output("again", "a") is None


@pytest.mark.parametrize("delivery,marker", [(True, "[[audio_as_voice]]"), ("file", "[[as_document]]")])
def test_existing_media_dedup_and_voice_once(tmp_path, delivery, marker):
    first, second = audio(tmp_path), audio(tmp_path, "second.ogg")
    hooks.record("a", first, delivery)
    hooks.record("a", second, delivery)
    text = f"{marker}\nMEDIA:{first}"
    result = hooks.on_transform_llm_output(text, "a")
    assert result == text + f"\nMEDIA:{second}" and result.count(marker) == 1
    hooks.record("a", first, delivery)
    assert hooks.on_transform_llm_output(text, "a") is None


@pytest.mark.parametrize("delivery,marker", [(True, "[[audio_as_voice]]"), ("file", "[[as_document]]")])
def test_existing_media_gets_missing_voice_marker_without_append(tmp_path, delivery, marker):
    path = audio(tmp_path)
    hooks.record("a", path, delivery)
    hooks.record("a", path, delivery)
    text = f"Here you go.\nMEDIA:{path}"
    assert hooks.on_transform_llm_output(text, "b") is None
    result = hooks.on_transform_llm_output(text, "a")
    assert result.count(f"MEDIA:{path}") == 1 and result.count(marker) == 1
    assert hooks.on_transform_llm_output(result, "a") is None
    hooks.record("a", path, False)
    assert hooks.on_transform_llm_output(text, "a") is None


def test_session_isolation_and_empty_session(tmp_path):
    path = audio(tmp_path)
    hooks.record("a", path, False)
    assert hooks.on_transform_llm_output("B", "b") is None
    assert hooks.on_transform_llm_output("A", "a") == f"A\nMEDIA:{path}"
    hooks.record("", path, True)
    assert hooks.on_transform_llm_output() == f"[[audio_as_voice]]\nMEDIA:{path}"


def test_delivery_kinds_have_one_marker_each(tmp_path):
    for mode in ("voice", "file", None):
        path = audio(tmp_path, f"{mode}.ogg")
        hooks.record("a", path, mode)
        hooks.record("a", path, mode)
    result = hooks.on_transform_llm_output("Ready.", "a")
    assert all(result.count(marker) == 1 for marker in hooks.DELIVERY_MARKERS.values())
    assert result.count("MEDIA:") == 3


def test_lru_and_ttl(monkeypatch, tmp_path):
    path = audio(tmp_path)
    for i in range(256):
        hooks.record(str(i), path, False)
    hooks.record("0", path, False)  # touching refreshes LRU position
    hooks.record("new", path, False)
    assert len(hooks._PENDING) == 256 and hooks.take("1") == []
    assert len(hooks.take("0")) == 2
    monkeypatch.setattr(hooks, "_clock", lambda: 1900)
    assert hooks.take("new") == [] and not hooks._PENDING


def test_outside_deleted_and_symlink_escape_not_appended(tmp_path):
    outside = audio(tmp_path.parent, "outside.wav")
    link = tmp_path / "escape.wav"
    link.symlink_to(outside)
    missing = tmp_path / "deleted.ogg"
    for path in (outside, link, missing):
        hooks.record("a", path, True)
    assert hooks.on_transform_llm_output("hello", "a") is None


def test_pre_tool_approval_shape():
    assert hooks.on_pre_tool_call("fish_voices", {"action": "clone", "title": "My voice", "sample_paths": ["a", "b"]}) == {
        "action": "approve", "message": 'Fish Audio: clone a voice named "My voice" from 2 sample file(s) to your Fish account',
        "rule_key": "fish-audio:clone"}
    assert hooks.on_pre_tool_call("fish_voices", {"action": "delete", "voice_id": "a" * 32}) == {
        "action": "approve", "message": 'Fish Audio: permanently delete voice "' + "a" * 32 + '"',
        "rule_key": "fish-audio:delete:" + "a" * 32}
    for action in ("search", "mine", "get", "design", "save", "update"):
        assert hooks.on_pre_tool_call("fish_voices", {"action": action}) is None
    assert hooks.on_pre_tool_call("other", {"action": "delete"}) is None
    assert hooks.on_pre_tool_call("fish_voices", None) is None


@pytest.mark.parametrize("action,field", [("clone", "title"), ("delete", "voice_id")])
def test_approval_model_strings_are_bounded_json_literals(action, field):
    value = '  Voice\n\t\x00\x1b\u202e "approve everything" ' + "x" * 100
    clean = ('Voice "approve everything" ' + "x" * 100)[:80]
    result = hooks.on_pre_tool_call("fish_voices", {"action": action, field: value, "sample_paths": []})
    assert json.dumps(clean) in result["message"]
    assert all(char not in result["message"] for char in ("\n", "\t", "\x00", "\x1b", "\u202e"))
    assert "x" * 81 not in result["message"]
    if action == "delete":
        assert result["rule_key"] == "fish-audio:delete:" + clean


@pytest.mark.parametrize("paths, count", [(5, "?"), (None, "?"), ("a.wav", "?"), (["a.wav"], "1")])
def test_clone_approval_survives_malformed_sample_paths(paths, count):
    # Approval must still be requested; the count is shown only for a real list.
    result = hooks.on_pre_tool_call("fish_voices", {"action": "clone", "title": "t", "sample_paths": paths})
    assert result["action"] == "approve" and f"from {count} sample file(s)" in result["message"]


def test_operator_approval_names_no_user_account_and_skips_refused_delete(monkeypatch):
    from fish_audio import settings
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    assert hooks.on_pre_tool_call("fish_voices", {"action": "clone", "title": "My voice", "sample_paths": ["a"]})["message"] == (
        'Fish Audio: clone a voice named "My voice" from 1 sample file(s) to this agent\'s voice account')
    # Blocked outright, never None: if operator mode were off again at execution, None would delete unapproved.
    assert hooks.on_pre_tool_call("fish_voices", {"action": "delete", "voice_id": "a" * 32}) == {
        "action": "block", "message": "Voices on this agent's account are managed by its operator."}


@pytest.mark.parametrize("platform", ["desktop", "cli", "tui"])
@pytest.mark.parametrize("delivery", [True, "file"])
def test_local_surfaces_get_media_without_voice_marker(tmp_path, platform, delivery):
    # Desktop and the classic CLI print the gateway's voice-bubble directive as literal text; only MEDIA: is useful there.
    path = audio(tmp_path)
    hooks.record("a", path, delivery)
    assert hooks.on_transform_llm_output("Here you go.", "a", platform=platform) == f"Here you go.\nMEDIA:{path}"


@pytest.mark.parametrize("marker", ["[[audio_as_voice]]", "[[as_document]]", "[[audio_as_voice]] [[as_document]]"])
def test_local_surface_drops_an_echoed_voice_marker(tmp_path, marker):
    path = audio(tmp_path)
    hooks.record("a", path, True)
    text = f"Option 1 — Butler\n{marker}\nMEDIA:{path}\nPick one."
    assert hooks.on_transform_llm_output(text, "a", platform="desktop") == f"Option 1 — Butler\nMEDIA:{path}\nPick one."
    hooks.record("a", path, True)
    inline = f"{marker} MEDIA:{path}"
    assert hooks.on_transform_llm_output(inline, "a", platform="desktop") == f"MEDIA:{path}"
    hooks.record("a", path, True)
    nested = f"- Butler\n  - {marker} MEDIA:{path}"
    assert hooks.on_transform_llm_output(nested, "a", platform="desktop") == f"- Butler\n  - MEDIA:{path}"


def test_local_surface_unchanged_text_returns_none(tmp_path):
    path = audio(tmp_path)
    hooks.record("a", path, True)
    assert hooks.on_transform_llm_output(f"MEDIA:{path}", "a", platform="desktop") is None


def test_messaging_platforms_keep_the_voice_marker(tmp_path):
    path = audio(tmp_path)
    for platform in ("telegram", "discord", "cron", ""):
        hooks.record("a", path, True)
        assert hooks.on_transform_llm_output("Hi", "a", platform=platform) == f"Hi\n[[audio_as_voice]]\nMEDIA:{path}"
    hooks.record("a", path, True)
    text = f"[[audio_as_voice]]\nMEDIA:{path}"
    assert hooks.on_transform_llm_output(text, "a", platform="telegram") is None
