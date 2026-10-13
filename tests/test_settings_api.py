import base64
import copy

import pytest
import respx

from test_dashboard_api import env, VOICE, OTHER, MP3


def expected(env, **overrides):
    return {"ok": True, "voice": None, "model": "s2.1-pro", "models": [
        {"id": "s2.1-pro", "display": "S2.1 Pro", "deprecated": False},
        {"id": "s2.1-pro-free", "display": "S2.1 Pro Free", "deprecated": False},
        {"id": "s2-pro", "display": "S2 Pro", "deprecated": False},
        {"id": "s1", "display": "S1 (deprecated)", "deprecated": True},
        {"id": "drama-3", "display": "Drama 3", "deprecated": False},
        {"id": "drama-3-preview", "display": "Drama 3 Preview", "deprecated": False}],
        "aliases": [], "managed": [], **overrides}


def test_settings_exact_offline_operator_and_profile(env):
    with respx.mock as mock:
        assert env.get("/settings") == expected(env)
        transport = env.active().config["plugins"]["entries"]["fish-audio"]["settings"]
        transport.update(operator_account=True, allow_free_model=False)
        rows = expected(env)["models"]
        rows.pop(1)
        assert env.get("/settings") == expected(env, models=rows)
        assert not mock.calls
    env.switch("b")
    assert env.get("/settings") == expected(env)


@pytest.mark.parametrize("voice,model,allow_free,shown", [
    (123, ["s1"], True, (None, None)),
    ("named_voice_123", "speech-9", True, ("named_voice_123", None)),
    ("abc", "s2.1-pro-free", True, (None, "s2.1-pro-free")),
    (VOICE, "s2.1-pro-free", False, (VOICE, "s2.1-pro")),
])
def test_settings_report_what_synthesis_uses(env, voice, model, allow_free, shown):
    env.active().config["tts"]["fish-audio"].update(voice=voice, model=model)
    env.active().config["plugins"]["entries"]["fish-audio"]["settings"]["allow_free_model"] = allow_free
    state = env.get("/settings")
    assert (state["voice"], state["model"]) == shown
    assert state["model"] is None or state["model"] in {row["id"] for row in state["models"]}


def test_settings_drama_3_listed_and_saved(env):
    assert {"id": "drama-3", "display": "Drama 3", "deprecated": False} in env.get("/settings")["models"]
    assert env.post("/settings", model="drama-3") == expected(env, model="drama-3")
    assert env.active().config["tts"]["fish-audio"]["model"] == "drama-3"


def test_settings_write_and_reset(env):
    assert env.post("/settings", model="s1") == expected(env, model="s1")
    env.active().config["tts"]["fish-audio"]["voice"] = VOICE
    assert env.post("/settings", model=None, voice=None) == expected(env, model=None)
    assert "model" not in env.active().config["tts"]["fish-audio"]
    assert "voice" not in env.active().config["tts"]["fish-audio"]


@pytest.mark.parametrize("body", [{"voice": "abc"}, {"model": "unknown"}, {"model": []}, {"extra": None}])
def test_settings_invalid_flat_envelope(env, body):
    before = copy.deepcopy(env.active().config)
    assert env.post("/settings", **body) == {"ok": False, "kind": "invalid",
        "message": "Set a known model or reset voice/model to null; voices are set through /use."}
    assert env.active().config == before and env.active().saves == 0


@pytest.mark.parametrize("leaf", ["voice", "model", "aliases"])
def test_managed_flat_envelope(env, managed_layer, leaf):
    managed_layer({"tts": {"fish-audio": {leaf: None}}})
    assert env.get("/settings") == expected(env, managed=[leaf])
    refusal = {"ok": False, "kind": "managed", "message": "This setting is set by the operator of this agent."}
    if leaf == "aliases":
        assert env.post("/aliases", name="narrator", voice=VOICE) == refusal
        assert env.delete("/aliases/narrator") == refusal
    else:
        assert env.post("/settings", **{leaf: None}) == refusal
    assert env.active().saves == 0


@pytest.mark.parametrize("rule,managed", [("upstream", ["model"]), ("fork", [])])
def test_settings_managed_list_follows_core_rule(env, managed_layer, rule, managed):
    managed_layer({"tts": {"fish-audio": {"model": {}}}}, rule=rule)
    assert env.get("/settings")["managed"] == managed


def test_alias_routes_and_use_exact(env):
    with respx.mock as mock:
        mock.get(env.active().base + "/model/" + VOICE).respond(json={})
        rows = [{"name": "narrator", "voice": VOICE}]
        assert env.post("/aliases", name="Narrator", voice=VOICE) == {"ok": True, "aliases": rows, "updated": False}
        assert env.post("/aliases", name="narrator", voice="narrator") == {"ok": True, "aliases": rows, "updated": True}
        assert env.get("/settings") == expected(env, aliases=rows)
        assert env.post("/use", voice="narrator") == {"ok": True, "voice": VOICE, "message": "Saved.",
            "provider": None, "operator_pinned": False}
    assert env.delete("/aliases/narrator") == {"ok": True, "aliases": []}
    assert env.delete("/aliases/narrator") == {"ok": False, "kind": "not_found", "message": "No saved voice name found."}
    for bad in ("default", "x", "a" * 32):
        assert env.delete(f"/aliases/{bad}") == {"ok": False, "kind": "not_found", "message": "No saved voice name found."}


def test_preview_resolves_before_speak_exact(env, monkeypatch):
    env.active().config["tts"]["fish-audio"]["aliases"] = {"narrator": VOICE}
    path = env.active().home / "audio" / "synthetic.mp3"
    def speak(args, *a, **kw):
        assert args["voice"] == VOICE
        path.write_bytes(MP3)
        return {"file_path": str(path), "model": "s2.1-pro"}
    monkeypatch.setattr(env.api._fa("tools"), "_speak", speak)
    assert env.post("/preview", voice="narrator") == {"ok": True, "voice": VOICE,
        "audio": base64.b64encode(MP3).decode(), "mime": "audio/mpeg", "model": "s2.1-pro", "billed": True}


def test_alias_refusals_wire(env):
    with respx.mock as mock:
        mock.get(env.active().base + "/model/" + VOICE).respond(json={})
        env.active().config["tts"]["fish-audio"]["aliases"] = {f"voice{i}": VOICE for i in range(50)}
        assert env.post("/aliases", name="extra", voice=VOICE) == {"ok": False, "kind": "limit",
            "message": "At most 50 voice names can be saved per profile."}
        mock.get(env.active().base + "/model/" + OTHER).respond(404)
        failure = env.post("/aliases", name="voice0", voice=OTHER)
        assert failure == {"ok": False, "kind": "voice_not_found", "message": str(
            env.api._fa("errors").response_error(404, body=b'{"code":"voice_not_found"}')),
            "detail": {"request_id": None, "trace_id": None, "code": "voice_not_found", "hint": None,
                       "text": "voice_not_found"}}
    for route in ("/use", "/preview"):
        assert env.post(route, voice="??") == {"ok": False, "kind": "invalid",
            "message": "Unknown voice or name. See `/fish alias`."}
