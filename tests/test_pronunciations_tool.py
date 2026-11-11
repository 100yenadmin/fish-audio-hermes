import json

import pytest
import respx

from fish_audio import hooks, pronunciations, settings, tool_support, tools, tts
from test_commands import BASE, config

ROOT = BASE + pronunciations.ROOT
REFS = [{"id": "dictionary-1", "version": "version-1"}]
RULES = [{"key": "ES", "value": "Electric Sheep", "type": "alias", "case_sensitive": True}]


@pytest.fixture(autouse=True)
def isolated(config, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(tool_support, "fish_api_key", lambda: "test-key")
    monkeypatch.setattr(tts, "fish_api_key", lambda: "test-key")
    config[0]["tts"]["fish-audio"] = {"model": "s2.1-pro"}


def call(action, **kwargs):
    return json.loads(tools.fish_pronunciations({"action": action, **kwargs}))


def speak(**kwargs):
    return json.loads(tools.fish_speak({"text": "ES is ready", **kwargs}))


@pytest.mark.parametrize("action,path,field", [("list", "", "dictionaries"),
                                              ("versions", "/dictionary-1/versions", "versions")])
@pytest.mark.parametrize("pagination", [{"cursor": "opaque", "page_size": 2, "include_total": True}, {"page": 2}])
def test_paginated_metadata(action, path, field, pagination):
    row = {"id": "dictionary-1", "name": "Company", "description": "Names", "latest_version_id": "version-1",
           "item_count": 1, "preview_voice_id": None, "preview_model": None,
           "created_at": "date", "updated_at": "date", "version_number": 1,
           "dictionary_id": "dictionary-1", "added_count": 1, "removed_count": 0, "changed_count": 0}
    page = {field: [row], "has_more": True, "next_cursor": "next", "total": 3}
    with respx.mock as mock:
        route = mock.get(ROOT + path).respond(json=page)
        result = call(action, dictionary_id="dictionary-1", **pagination)
    assert result == {**page, "success": True}
    assert dict(route.calls.last.request.url.params) == {k: str(v).lower() if type(v) is bool else str(v) for k, v in pagination.items()}


@pytest.mark.parametrize("action,suffix,data", [("get", "", {"id": "dictionary-1", "latest_version_id": "version-1"}),
    ("get_version", "/versions/version-1", {"id": "version-1", "items": RULES})])
def test_get(action, suffix, data):
    with respx.mock as mock:
        mock.get(ROOT + "/dictionary-1" + suffix).respond(json=data)
        assert call(action, dictionary_id="dictionary-1", version_id="version-1") == {**data, "success": True}


def test_create():
    with respx.mock as mock:
        route = mock.post(ROOT).respond(201, json={"id": "dictionary-1"})
        result = call("create", name="Company", description="Names")
    assert result["id"] == "dictionary-1" and result["success"]
    assert json.loads(route.calls.last.request.content) == {"name": "Company", "description": "Names"}


@pytest.mark.parametrize("latest", [None, "version-0"])
def test_publish_fetches_latest(latest):
    with respx.mock as mock:
        get = mock.get(ROOT + "/dictionary-1").respond(json={"latest_version_id": latest})
        post = mock.post(ROOT + "/dictionary-1/versions").respond(201, json={"id": "version-1", "items": RULES})
        result = call("publish", dictionary_id="dictionary-1", rules=RULES, description="New names")
    assert result["success"] and result["items"] == RULES and get.call_count == 1
    assert json.loads(post.calls.last.request.content) == {"expected_latest_version_id": latest, "items": RULES, "description": "New names"}


@pytest.mark.parametrize("expected", [None, "version-0"])
def test_publish_explicit_guard_and_conflict(expected):
    with respx.mock as mock:
        post = mock.post(ROOT + "/dictionary-1/versions").respond(409, json={"message": "untrusted server text"})
        result = call("publish", dictionary_id="dictionary-1", rules=RULES, expected_latest_version_id=expected)
        assert len(mock.calls) == 1
    assert result == {"success": False, "error": pronunciations.CONFLICT}
    assert json.loads(post.calls.last.request.content)["expected_latest_version_id"] == expected


def test_use_is_read_by_tool_and_ordinary_provider(config, tmp_path):
    with respx.mock as mock:
        get = mock.get(ROOT + "/dictionary-1/versions/version-1").respond(json={"id": "version-1", "items": RULES})
        assert call("use", refs=REFS)["changed"]
        post = mock.post(BASE + "/v1/tts").respond(content=b"synthetic-audio")
        assert speak()["success"]
        assert json.loads(post.calls.last.request.content)["pronunciation_dictionary"] == REFS
        tts.FishAudioTTSProvider().synthesize("ES", str(tmp_path / "speech.mp3"))
        assert json.loads(post.calls.last.request.content)["pronunciation_dictionary"] == REFS
        assert get.call_count == 1
    assert config[1][-1]["tts"]["fish-audio"]["pronunciation_dictionary"] == REFS
    assert "provider" not in config[0]["tts"]
    assert call("use", refs=[])["success"]
    assert config[0]["tts"]["fish-audio"]["pronunciation_dictionary"] == []


def test_call_refs_replace_configured_inline_and_leave_defaults(config):
    inline = [{"items": [{"key": "old", "value": "OW1 L D"}]}]
    config[0]["tts"]["fish-audio"]["pronunciation_dictionary"] = inline
    with respx.mock as mock:
        get = mock.get(ROOT + "/dictionary-1/versions/version-1").respond(json={"items": RULES})
        post = mock.post(BASE + "/v1/tts").respond(content=b"audio")
        assert speak(dictionaries=REFS)["success"]
        assert json.loads(post.calls.last.request.content)["pronunciation_dictionary"] == REFS
        assert get.call_count == 1
    assert config[0]["tts"]["fish-audio"]["pronunciation_dictionary"] == inline


@pytest.mark.parametrize("field", ["pronunciations", "spoken_as"])
def test_explicit_refs_inline_conflict(config, field):
    with respx.mock as mock:
        result = speak(**{field: {"ES": "Electric Sheep"}}, dictionaries=REFS)
        assert not mock.calls
    assert not result["success"] and "Publish those words" in result["error"]


@pytest.mark.parametrize("field", ["pronunciations", "spoken_as"])
def test_configured_refs_give_way_to_inline_words(config, field):
    config[0]["tts"]["fish-audio"]["pronunciation_dictionary"] = REFS
    with respx.mock as mock:
        post = mock.post(BASE + "/v1/tts").respond(content=b"audio")
        assert speak(**{field: {"ES": "Electric Sheep"}})["success"]
        sent = json.loads(post.calls.last.request.content)["pronunciation_dictionary"]
    assert sent and all("items" in group and "id" not in group for group in sent)


def test_drop_refs_allows_inline_and_preserves_merge(config):
    nested = config[0]["tts"]["fish-audio"]
    nested["pronunciation_dictionary"] = REFS
    with respx.mock as mock:
        post = mock.post(BASE + "/v1/tts").respond(content=b"audio")
        assert speak(dictionaries=[], spoken_as={"ES": "Electric Sheep"})["success"]
        assert json.loads(post.calls.last.request.content)["pronunciation_dictionary"] == [{"items": [{"key": "ES", "value": "Electric Sheep", "type": "alias"}]}]
        assert speak(dictionaries=[])["success"]
        assert json.loads(post.calls.last.request.content)["pronunciation_dictionary"] == []
        nested["pronunciation_dictionary"] = [{"items": [{"key": "old", "value": "OW1 L D"}, {"key": "ES", "value": "old"}]}]
        assert speak(spoken_as={"ES": "Electric Sheep"})["success"]
        assert json.loads(post.calls.last.request.content)["pronunciation_dictionary"] == [{"items": [
            {"key": "old", "value": "OW1 L D"}, {"key": "ES", "value": "Electric Sheep", "type": "alias"}]}]


@pytest.mark.parametrize("action", ["use", "speak"])
def test_unknown_ref_does_not_write_or_synthesize(config, action):
    with respx.mock as mock:
        route = mock.get(ROOT + "/dictionary-1/versions/version-1").respond(404)
        result = call("use", refs=REFS) if action == "use" else speak(dictionaries=REFS)
        assert len(mock.calls) == route.call_count == 1
    assert not result["success"] and "Unknown pronunciation dictionary or version" in result["error"]
    assert not config[1]


@pytest.mark.parametrize("refs", [None, {}, [{}], [{"items": []}], REFS * 4,
    [{"id": "../escape", "version": "v"}], [{"id": "ok", "version": "latest", "extra": True}]])
def test_invalid_refs(refs):
    with respx.mock as mock:
        assert not call("use", refs=refs)["success"]
        assert not speak(dictionaries=refs)["success"]
        assert not mock.calls


@pytest.mark.parametrize("rules", [None, {}, RULES * 5001, [{"key": "", "value": "x"}],
    [{"key": "x" * 257, "value": "v"}], [{"key": "x", "value": "v" * 1025}],
    [{"key": " x", "value": "v", "type": "alias"}], [{"key": "[x]", "value": "v", "type": "alias"}],
    [{"key": "x", "value": "<|x|>", "type": "alias"}], [{"key": "x", "value": "v", "case_sensitive": 1}],
    [{"key": "x", "value": "v", "type": "other"}], [{"key": "x", "value": "<|phoneme_start|>"}]])
def test_invalid_rules(rules):
    with respx.mock as mock:
        result = call("publish", dictionary_id="dictionary-1", rules=rules)
        assert not result["success"] and "Invalid rules" in result["error"] and not mock.calls


def test_upper_limits():
    rules = [{"key": str(i), "value": "V"} for i in range(5000)]
    refs = [{"id": "dictionary-1", "version": f"version-{i}"} for i in range(3)]
    with respx.mock as mock:
        mock.post(ROOT + "/dictionary-1/versions").respond(201, json={"id": "version-1"})
        assert call("publish", dictionary_id="dictionary-1", rules=rules, expected_latest_version_id=None)["success"]
        for ref in refs:
            mock.get(ROOT + f"/{ref['id']}/versions/{ref['version']}").respond(json={"id": ref["version"]})
        assert call("use", refs=refs)["success"]
        assert len(mock.calls) == 4


@pytest.mark.parametrize("operator", [False, True])
@pytest.mark.parametrize("action", ["delete", "delete_version"])
def test_delete_gate(operator, action, monkeypatch):
    monkeypatch.setattr(settings, "operator_account", lambda: operator)
    result = hooks.on_pre_tool_call("fish_pronunciations", {"action": action, "dictionary_id": "dictionary-1", "version_id": "version-0"})
    if operator:
        assert result is None
    else:
        assert result["action"] == "approve" and "dictionary-1" in result["message"]
        assert ("version-0" in result["rule_key"]) is (action == "delete_version")
    assert hooks.on_pre_tool_call("fish_pronunciations", {"action": "publish"}) is None


def test_latest_delete_refused_before_delete():
    with respx.mock as mock:
        mock.get(ROOT + "/dictionary-1").respond(json={"latest_version_id": "version-1"})
        result = call("delete_version", dictionary_id="dictionary-1", version_id="version-1")
        assert len(mock.calls) == 1
    assert not result["success"] and "latest dictionary version cannot be deleted" in result["error"]


@pytest.mark.parametrize("action", ["delete", "delete_version"])
def test_delete_transport(action):
    with respx.mock as mock:
        suffix = ""
        if action == "delete_version":
            mock.get(ROOT + "/dictionary-1").respond(json={"latest_version_id": "version-1"})
            suffix = "/versions/version-0"
        route = mock.delete(ROOT + "/dictionary-1" + suffix).respond(204)
        assert call(action, dictionary_id="dictionary-1", version_id="version-0")["deleted"]
        assert route.call_count == 1


@pytest.mark.parametrize("operator", [False, True])
def test_managed_defaults_refused(config, managed_layer, monkeypatch, operator):
    monkeypatch.setattr(settings, "operator_account", lambda: operator)
    managed_layer({"tts": {"fish-audio": {"pronunciation_dictionary": REFS}}})
    with respx.mock as mock:
        result = call("use", refs=[])
        assert not mock.calls
    assert not result["success"] and "set by the operator" in result["error"] and not config[1]


def test_managed_install_refused(config):
    config[2].is_managed = lambda: True
    assert not call("use", refs=[])["success"] and not config[1]


@pytest.mark.parametrize("action", ["list", "publish", "delete", "delete_version"])
def test_operator_account_management_refused_without_http(monkeypatch, action):
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    with respx.mock as mock:
        assert call(action, dictionary_id="dictionary-1", version_id="version-0", rules=RULES) == {
            "success": False, "error": pronunciations.ACCOUNT_DICTIONARIES}
        assert not mock.calls


@pytest.mark.parametrize("operator", [False, True])
def test_dictionary_description_explains_operator_limits(monkeypatch, fake_ctx, operator):
    monkeypatch.setattr(settings, "operator_account", lambda: operator)
    tools.register(fake_ctx)
    tool = fake_ctx.tools["fish_pronunciations"]
    assert "On operator-managed agents, listing, publishing and deleting dictionaries are refused." in tool["description"]
    assert tool["schema"]["description"] == tool["description"]


def test_operator_kept_actions_allowed(monkeypatch):
    monkeypatch.setattr(settings, "operator_account", lambda: True)
    with respx.mock as mock:
        mock.get(ROOT + "/dictionary-1").respond(json={"id": "dictionary-1", "latest_version_id": None})
        mock.get(ROOT + "/dictionary-1/versions").respond(json={"versions": [], "has_more": False})
        mock.get(ROOT + "/dictionary-1/versions/version-1").respond(json={"items": RULES})
        mock.post(ROOT).respond(201, json={"id": "dictionary-1"})
        for action, args in [("get", {"dictionary_id": "dictionary-1"}),
            ("versions", {"dictionary_id": "dictionary-1"}), ("create", {"name": "Company"}),
            ("get_version", {"dictionary_id": "dictionary-1", "version_id": "version-1"}), ("use", {"refs": REFS})]:
            assert call(action, **args)["success"]
