import argparse
import copy
import io
import os
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import httpx
import pytest
import respx

from fish_audio import account, cli, commands, settings

BASE = "https://api.fish.audio"
WALLET = BASE + "/wallet/self/api-credit?check_free_credit=true"


@pytest.fixture
def scripting(config, monkeypatch, tmp_path):
    from fish_audio import account_tool, secrets, tools, tool_support, transcribe, voices
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(tool_support, "fish_api_key", lambda: "synthetic-key")
    monkeypatch.setattr(secrets, "fish_api_key", lambda: "synthetic-key")
    config[0]["plugins"] = {"entries": {"fish-audio": {"settings": {"operator_account": True}}}}
    calls = []
    def execute(args, key, base, session, **kwargs):
        assert not settings.operator_account()
        calls.append((args, kwargs))
        return {"file_path": str(tmp_path / "cached.mp3"), "duration": 1, "text": "你好",
                "srt_path": "captions.srt", "items": [{"title": "Narrator", "id": "voice-id", "languages": ["en"], "tags": ["calm"]}],
                "credit": "1", "cumulative_top_up": "2", "plan": {"type": "free"}}
    for module, name in ((tools, "_speak"), (transcribe, "execute"), (voices, "execute"), (account_tool, "execute")):
        monkeypatch.setattr(module, name, execute)
    (tmp_path / "cached.mp3").write_bytes(b"ID3synthetic")
    return calls


@pytest.mark.parametrize("argv,expected", [(("speak", "你好"), "cached.mp3"),
    (("transcribe", "audio.wav"), "你好"), (("voices",), "Narrator — voice-id (en, calm)"),
    (("balance",), "API credit: 1")])
@pytest.mark.parametrize("mode", ["json", "human", "failure-json", "failure-human"])
def test_scripting_envelopes(scripting, monkeypatch, capsys, argv, expected, mode):
    if mode.startswith("failure"):
        from fish_audio import account_tool, tools, transcribe, voices
        def fail(*a, **kw):
            raise cli.ToolInputError("synthetic-key failed")
        module, name = {"speak": (tools, "_speak"), "transcribe": (transcribe, "execute"),
                        "voices": (voices, "execute"), "balance": (account_tool, "execute")}[argv[0]]
        monkeypatch.setattr(module, name, fail)
    assert run(*argv, *(["--json"] if mode.endswith("json") else [])) == int(mode.startswith("failure"))
    captured = capsys.readouterr()
    assert "synthetic-key" not in captured.out
    if mode.endswith("json"):
        assert len(captured.out.splitlines()) == 1
        assert json.loads(captured.out)["success"] is (not mode.startswith("failure"))
        assert not captured.err
    else:
        assert ("[redacted] failed" if mode.startswith("failure") else expected) in captured.out
        billed = argv[0] in {"speak", "transcribe"} and not mode.startswith("failure")
        assert ("billed to your Fish API credit" in captured.err) == billed


@pytest.mark.parametrize("source", ["positional", "file", "stdin", "none", "two", "both-flags", "missing-file"])
def test_speak_sources(scripting, tmp_path, monkeypatch, capsys, source):
    text = tmp_path / "text.txt"
    text.write_text("你好", encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO("你好"))
    flags = {"positional": ["你好"], "file": ["--text-file", str(text)], "stdin": ["--stdin"],
             "none": [], "two": ["你好", "--stdin"], "both-flags": ["--stdin", "--text-file", str(text)],
             "missing-file": ["--text-file", str(tmp_path / "absent")] }[source]
    failed = source in {"none", "two", "both-flags", "missing-file"}
    assert run("speak", *flags, "--json") == int(failed)
    assert json.loads(capsys.readouterr().out)["success"] is not failed
    assert bool(scripting) is not failed
    if not failed:
        assert scripting[0] == ({"text": "你好", "timestamps": False}, {"record_media": False})


def test_scripting_flag_mapping(scripting, capsys):
    assert run("speak", "hello", "--voice", "Eva", "--model", "s2.1-pro", "--format", "wav", "--timestamps", "--json") == 0
    assert scripting[-1] == ({"text": "hello", "voice": "Eva", "model": "s2.1-pro", "format": "wav", "timestamps": True}, {"record_media": False})
    assert run("transcribe", "a.wav", "--model", "transcribe-1", "--language", "ja", "--diarize", "--srt", "--json") == 0
    assert scripting[-1][0] == {"file_path": "a.wav", "model": "transcribe-1", "language": "ja", "diarize": "true", "srt": True}
    assert run("voices", "clone", "--mine", "--page", "2", "--page-size", "3", "--json") == 0
    assert scripting[-1][0] == {"action": "mine", "query": "clone", "page": 2, "page_size": 3}
    assert run("voices", "--json") == 0 and scripting[-1][0] == {"action": "search"}
    assert run("balance", "--json") == 0 and scripting[-1][0] == {"action": "balance"}
    capsys.readouterr()


@pytest.mark.parametrize("flags", [("--format", "bad"), ("--voice",), ("--unknown",), ("--delete", "id")])
def test_scripting_parse_errors_are_json(scripting, capsys, flags):
    with pytest.raises(SystemExit) as exc:
        run("speak", "hello", *flags, "--json")
    assert exc.value.code == 1 and not scripting
    assert json.loads(capsys.readouterr().out)["success"] is False


@pytest.mark.parametrize("case", ["copy", "exists", "force", "symlink", "parent-link", "missing-parent", "sidecar-exists"])
def test_speak_output(scripting, monkeypatch, tmp_path, capsys, case):
    source, target = tmp_path / "cached.mp3", tmp_path / "out.mp3"
    for ext in (".srt", ".vtt"):
        source.with_suffix(ext).write_text("captions")
    if case in {"exists", "force"}:
        target.write_bytes(b"old")
    elif case == "symlink":
        target.symlink_to(source)
    elif case == "parent-link":
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        target = link / "out.mp3"
    elif case == "missing-parent":
        target = tmp_path / "missing" / "out.mp3"
    elif case == "sidecar-exists":
        target.with_suffix(".srt").write_text("old captions")
    failed = case not in {"copy", "force", "parent-link"}
    replaced = []
    def atomic(real):
        def place(src, dest):
            assert Path(src).parent == Path(dest).parent
            replaced.append(dest)
            real(src, dest)
        return place
    monkeypatch.setattr(cli.os, "replace", atomic(cli.os.replace))
    monkeypatch.setattr(cli.os, "link", atomic(cli.os.link))
    assert run("speak", "hello", "--timestamps", "--out", str(target), "--json",
               *(["--force"] if case == "force" else [])) == int(failed)
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is not failed and bool(scripting) is not failed
    assert len(replaced) == (0 if failed else 3)
    assert source.read_bytes() == b"ID3synthetic"
    if not failed:
        assert result["out_path"] == str(target) and target.read_bytes() == source.read_bytes()
        assert all(target.with_suffix(ext).read_text() == "captions" for ext in (".srt", ".vtt"))


def test_speak_does_not_record_media(config, monkeypatch, tmp_path, capsys):
    from fish_audio import client, hooks, media, secrets, tool_support
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(tool_support, "fish_api_key", lambda: "synthetic-key")
    monkeypatch.setattr(secrets, "fish_api_key", lambda: "synthetic-key")
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    monkeypatch.setattr(hooks, "_PENDING", {})
    def synth(params, key, base, path, **kwargs):
        Path(path).write_bytes(b"ID3synthetic")
        print("a notice")
    monkeypatch.setattr(client, "tts_to_file", synth)
    assert run("speak", "hello", "--model", "s2.1-pro", "--format", "mp3", "--json") == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["success"] and not hooks._PENDING
    assert "a notice" in captured.err


@pytest.fixture
def config(monkeypatch):
    data, saved_keys = {"tts": {}, "stt": {}}, []
    module = ModuleType("hermes_cli.config")
    module.read_raw_config = lambda: copy.deepcopy(data)
    def save(cfg, **kwargs):
        data.clear()
        data.update(cfg)
    module.save_config = save
    module.is_managed = lambda: False
    module.save_env_value_secure = lambda name, value: saved_keys.append((name, value))
    module.get_env_value = lambda name: next((value for key, value in reversed(saved_keys) if key == name), None)
    parent = ModuleType("hermes_cli")
    parent.config = module
    parent.__version__ = "0.21.5"
    monkeypatch.setitem(sys.modules, "hermes_cli", parent)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", module)
    stt = ModuleType("agent.transcription_registry")
    stt.get_provider = lambda name: object()
    monkeypatch.setitem(sys.modules, "agent.transcription_registry", stt)
    monkeypatch.setattr(settings, "_config", lambda: data)
    monkeypatch.setattr(cli, "fish_api_key", lambda: "test-key")
    monkeypatch.setattr(commands, "fish_api_key", lambda: "test-key")
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: " test-key ")
    account._wallet_cache.clear()
    return data, saved_keys, module


def run(*argv):
    parser = argparse.ArgumentParser()
    cli.setup(parser)
    return cli.handle(parser.parse_args(argv))


@pytest.mark.parametrize("failure", [401, "timeout"])
def test_login_bad_key_saves_nothing(config, failure, capsys):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(WALLET)
        if failure == 401:
            route.respond(401, json={"message": "test-key private"})
        else:
            route.mock(side_effect=httpx.ReadTimeout("test-key private"))
        assert run("login") == 1
    assert config[1] == [] and config[0] == {"tts": {}, "stt": {}}
    output = capsys.readouterr().out
    assert "test-key" not in output and "private" not in output
    assert ("api-keys" in output) == (failure == 401)


def test_login_yes_with_stdin_switches_providers(config, monkeypatch, capsys):
    data, saved, _ = config
    data["tts"]["provider"] = "elevenlabs"
    data["stt"]["provider"] = "other"
    monkeypatch.setattr(sys, "stdin", io.StringIO("test-key\n"))
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0", "has_free_credit": False})
        assert run("login", "--key-stdin", "--yes") == 0
    assert saved == [("FISH_API_KEY", "test-key")]
    assert data["tts"]["provider"] == data["stt"]["provider"] == "fish-audio"
    output = capsys.readouterr().out
    assert "test-key" not in output and cli.RESTART_HINT in output


def test_login_noninteractive_preserves_existing_and_sets_unset(config, monkeypatch):
    data, _, _ = config
    data["tts"]["provider"] = "evaos-fishaudio"
    monkeypatch.setattr(sys, "stdin", io.StringIO("test-key\n"))
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0", "has_free_credit": False})
        assert run("login", "--key-stdin") == 0
    assert data["tts"]["provider"] == "evaos-fishaudio" and data["stt"]["provider"] == "fish-audio"


def test_login_interactive_prompt_declined_and_legacy_saver(config, monkeypatch):
    data, saved, module = config
    data["tts"]["provider"] = data["stt"]["provider"] = "other"
    monkeypatch.delattr(module, "save_env_value_secure")
    module.save_env_value = lambda name, value: saved.append((name, value))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0", "has_free_credit": False})
        assert run("login") == 0
    assert len(prompts) == 2 and all("[y/N]" in p for p in prompts)
    assert data["tts"]["provider"] == data["stt"]["provider"] == "other" and saved


@pytest.mark.parametrize("result", [None, {"success": True}])
def test_login_readback_pinned_key_does_not_switch(config, monkeypatch, capsys, result):
    data, _, module = config
    data["tts"]["provider"] = "elevenlabs"
    data["stt"]["provider"] = "other"
    before = copy.deepcopy(data)
    events = []
    def save(name, value):
        events.append("save")
        return result
    def read(name):
        assert name == "FISH_API_KEY"
        events.append("read")
        return "pinned-test-key"
    module.save_env_value_secure = save
    module.get_env_value = read
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0"})
        assert run("login", "--yes") == 1
    assert events == ["save", "read"] and data == before
    output = capsys.readouterr().out
    assert "pinned by an administrator or config" in output and "Providers were not switched" in output
    assert "test-key" not in output and cli.RESTART_HINT not in output and "Key saved" not in output


def test_login_readback_falls_back_to_scoped_key(config, monkeypatch, capsys):
    data, _, module = config
    monkeypatch.delattr(module, "get_env_value")
    monkeypatch.setattr(cli, "fish_api_key", lambda: "pinned-test-key")
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0"})
        assert run("login", "--yes") == 1
    assert data == {"tts": {}, "stt": {}}
    assert "pinned by an administrator or config" in capsys.readouterr().out


def test_login_key_stdin_tty_uses_getpass_without_reading_or_echo(config, monkeypatch, capsys):
    class Terminal(io.StringIO):
        def isatty(self):
            return True
        def read(self, *args):
            pytest.fail("a TTY key must use getpass")
    monkeypatch.setattr(sys, "stdin", Terminal())
    prompts = []
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: prompts.append(prompt) or "test-key")
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0"})
        assert run("login", "--key-stdin", "--yes") == 0
    assert prompts == ["Fish Audio API key: "] and config[1] == [("FISH_API_KEY", "test-key")]
    assert "test-key" not in capsys.readouterr().out


def test_login_key_stdin_pipe_never_uses_getpass(config, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("test-key\n"))
    monkeypatch.setattr(cli.getpass, "getpass", lambda *args: pytest.fail("pipe input must read stdin"))
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0"})
        assert run("login", "--key-stdin", "--yes") == 0
    assert config[1] == [("FISH_API_KEY", "test-key")]


@pytest.mark.parametrize("allowed,expected", [(False, "s2.1-pro"), (True, "s2.1-pro-free")])
def test_cli_status_and_login_name_effective_model(config, monkeypatch, capsys, allowed, expected):
    data = config[0]
    data["tts"]["fish-audio"] = {"model": "s2.1-pro-free"}
    data["plugins"] = {"entries": {"fish-audio": {"settings": {"allow_free_model": allowed}}}}
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "0", "cumulative_top_up": "0", "has_free_credit": False})
        mock.get(BASE + "/wallet/self/package").respond(json={"type": "free"})
        assert run("status") == 0
        output = capsys.readouterr().out
        assert output.split("Model: ", 1)[1].split()[0] == expected
        assert expected == settings.resolve_model(None, key="test-key", base_url=BASE)[0]
        assert run("login", "--yes") == 0
        output = capsys.readouterr().out
        assert f"Key saved for this profile. Model: {expected}\n" in output
        if not allowed:
            assert "s2.1-pro-free" not in output and settings.free_model_notice() not in output
        assert cli.RESTART_HINT in output and "test-key" not in output


@pytest.mark.parametrize("missing", [False, True])
def test_doctor_no_synth_exit_and_no_tts(config, monkeypatch, capsys, missing):
    data, _, _ = config
    data["tts"]["provider"] = data["stt"]["provider"] = "fish-audio"
    if missing:
        monkeypatch.setattr(cli, "fish_api_key", lambda: "")
    with respx.mock(assert_all_called=True) as mock:
        if not missing:
            mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0", "has_free_credit": False})
        assert run("doctor", "--no-synth") == int(missing)
        assert all(r.request.method == "GET" for r in mock.calls)
    output = capsys.readouterr().out
    assert "round trip skipped" in output
    assert all(line.startswith(("ok:", "warn:", "fail:")) for line in output.splitlines())


def _raise():
    raise RuntimeError("malformed install stamp")


@pytest.mark.parametrize("info,stamp,expected", [("0.21.6", "0.0.0", "0.21.6"), ("unknown", "0.21.4", "unknown"),
    (_raise, "0.21.4", "unknown"), (None, "0.21.5", "0.21.5")])
def test_version_reports_what_the_requires_hermes_gate_compares(monkeypatch, info, stamp, expected):
    # Hermes 0.21.6+: exactly version_info's base version, even "unknown" (the gate loads the plugin then); never a
    # stale __version__ or package metadata. Older builds without version_info fall back to __version__.
    parent = ModuleType("hermes_cli")
    parent.__version__ = stamp
    monkeypatch.setitem(sys.modules, "hermes_cli", parent)
    monkeypatch.setattr(cli.metadata, "version", lambda name: "0.21.4")
    if info is None:
        monkeypatch.setitem(sys.modules, "hermes_cli.version_info", None)
    else:
        version_info = ModuleType("hermes_cli.version_info")
        version_info.get_version_info = info if callable(info) else (lambda: SimpleNamespace(base_version=info))
        monkeypatch.setitem(sys.modules, "hermes_cli.version_info", version_info)
    assert cli._version() == expected


@pytest.mark.parametrize("version,level,code", [("unknown", "warn", 0), ("0.0.0", "warn", 0),
    ("0.21.4", "fail", 1), ("0.21.5", "ok", 0)])
def test_doctor_unknown_version_warns_but_known_old_version_fails(config, monkeypatch, capsys, version, level, code):
    config[0]["tts"]["provider"] = config[0]["stt"]["provider"] = "fish-audio"
    monkeypatch.setattr(cli, "_version", lambda: version)
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0"})
        assert run("doctor", "--no-synth") == code
    output = capsys.readouterr().out
    if level == "warn":
        assert "warn: Hermes version unknown (source checkout?)" in output and "fail:" not in output
    else:
        assert f"{level}: Hermes {version}" in output


def test_doctor_synth_temp_file_deleted(config, monkeypatch, capsys):
    written = []
    def synth(self, text, path):
        assert len(text) <= 60 and Path(path).suffix == ".ogg"
        Path(path).write_bytes(b"OggSsynthetic")
        written.append(Path(path))
        return path
    monkeypatch.setattr(cli.FishAudioTTSProvider, "synthesize", synth)
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0", "has_free_credit": False})
        assert run("doctor") == 0
    assert written and not written[0].exists() and "billed: a few bytes of text" in capsys.readouterr().out


def test_cli_status_use_and_no_subcommand_help(config, monkeypatch, capsys):
    monkeypatch.setattr(commands, "status", lambda *args: "Key set: yes")
    assert run("status") == 0 and "Key set: yes" in capsys.readouterr().out
    assert run() == 0 and "doctor" in capsys.readouterr().out
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/model/" + "a" * 32).respond(json={"_id": "a" * 32})
        assert run("use", "a" * 32) == 0
    assert config[0]["tts"]["fish-audio"]["voice"] == "a" * 32


def test_cli_status_without_key_matches_chat_setup(config, monkeypatch, capsys):
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    with respx.mock(assert_all_called=True) as mock:
        assert run("status") == 0
        assert capsys.readouterr().out.strip() == commands.NO_KEY
        assert not mock.calls


def test_cli_use_own_title(config, capsys):
    ident = "a" * 32
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/model").respond(json={"items": [{"id": ident, "title": "Eva"}]})
        mock.get(BASE + "/model/" + ident).respond(json={})
        assert run("use", " eVA ") == 0
    assert config[0]["tts"]["fish-audio"]["voice"] == ident
    assert capsys.readouterr().out.strip() == "Saved."


def test_cli_use_single_word_own_title(config, capsys):
    ident = "a" * 32
    with respx.mock(assert_all_called=True) as mock:
        listing = mock.get(BASE + "/model").respond(json={"items": [{"id": ident, "title": "Storyteller"}]})
        mock.get(BASE + "/model/" + ident).respond(json={})
        assert run("use", "Storyteller") == 0
        assert listing.call_count == 1 and listing.calls.last.request.url.params["self"] == "true"
    assert config[0]["tts"]["fish-audio"]["voice"] == ident
    assert capsys.readouterr().out.strip() == "Saved."


def test_cli_title_ambiguous_no_write(config, capsys):
    before = copy.deepcopy(config[0])
    with respx.mock(assert_all_called=True) as mock:
        mock.get(BASE + "/model").respond(json={"items": [
            {"id": "a" * 32, "title": "Eva"}, {"id": "b" * 32, "title": "Eva"}]})
        assert run("use", "Eva") == 1
    assert "hermes fish use <id>" in capsys.readouterr().out and config[0] == before


def test_cli_title_search_too_large_no_write(config, capsys):
    before = copy.deepcopy(config[0])
    page = {"items": [{"id": "a" * 32, "title": "Eva"}] + [{"id": f"{i:032x}", "title": f"Eva {i}"} for i in range(19)],
            "has_more": True}
    with respx.mock(assert_all_called=True) as mock:
        listing = mock.get(BASE + "/model").respond(json=page)
        assert run("use", "Eva") == 1 and listing.call_count == 5
    out = capsys.readouterr().out
    assert "More than 100 of your voices match that title" in out and "hermes fish use <id>" in out
    assert config[0] == before


@pytest.mark.parametrize("failure", ["missing", "no match", "error"])
def test_cli_title_failures_no_write(config, monkeypatch, capsys, failure):
    before = copy.deepcopy(config[0])
    with respx.mock(assert_all_called=True) as mock:
        if failure == "missing":
            monkeypatch.setattr(cli, "fish_api_key", lambda: "")
        else:
            mock.get(BASE + "/model").respond(503 if failure == "error" else 200,
                                              json={"items": [], "message": "private response body"})
        assert run("use", "Eva") == 1
    reply = capsys.readouterr().out.strip()
    expected = {"missing": commands.NO_KEY, "no match": settings.UNKNOWN_VOICE}
    assert reply == expected[failure] if failure in expected else "unavailable" in reply
    assert "private response body" not in reply and config[0] == before


def test_cli_status_is_the_operator_view_in_operator_mode(config, monkeypatch, capsys):
    config[0]["plugins"] = {"entries": {"fish-audio": {"settings": {"operator_account": True}}}}
    monkeypatch.setattr(commands, "fish_api_key", lambda: "")
    assert run("status") == 0
    assert capsys.readouterr().out.strip() == commands.NO_KEY


@pytest.mark.parametrize("status,link", [(401, "api-keys"), (402, "developers/billing")])
def test_operator_terminal_errors_keep_their_links(config, status, link, capsys):
    # operator_account hides the account from the agent's users, never from the operator's own terminal.
    config[0]["plugins"] = {"entries": {"fish-audio": {"settings": {"operator_account": True}}}}
    with respx.mock(assert_all_called=True) as mock:
        mock.get(WALLET).respond(status, json={})
        assert run("login") == 1
    output = capsys.readouterr().out
    assert link in output and "operator" not in output
    assert settings.operator_account() is True  # outside the terminal the setting applies again


def hermes_nested(*argv):
    # Hermes nests the plugin parser under its own top-level parser and calls parse_args there.
    top = argparse.ArgumentParser(prog="hermes")
    fish = top.add_subparsers(dest="command").add_parser("fish")
    cli.setup(fish)
    return cli.handle(top.parse_args(["fish", *argv]))


@pytest.mark.parametrize("argv", [("speak", "Hello", "world"), ("speak", "hi", "--unknown"), ("balance", "extra")])
def test_nested_leftover_arguments_are_json(scripting, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        hermes_nested(*argv, "--json")
    out = capsys.readouterr().out
    assert exc.value.code == 1 and not scripting and len(out.splitlines()) == 1
    result = json.loads(out)
    assert result["success"] is False and "unrecognized arguments" in result["error"]


def test_nested_json_success(scripting, capsys):
    assert hermes_nested("balance", "--json") == 0 and json.loads(capsys.readouterr().out)["success"] is True


@pytest.mark.parametrize("name", ["clip.srt", "clip.vtt", "clip.SRT"])
@pytest.mark.parametrize("timestamps", [True, False])
def test_subtitle_named_output_refused_before_billing(scripting, tmp_path, capsys, name, timestamps):
    assert run("speak", "hi", *(["--timestamps"] if timestamps else []), "--out", str(tmp_path / name), "--json") == 1
    assert not scripting and "audio file name" in json.loads(capsys.readouterr().out)["error"]


def test_output_suffix_sets_format_and_mismatch_is_refused(scripting, tmp_path, capsys):
    assert run("speak", "hi", "--out", str(tmp_path / "a.mp3"), "--json") == 0
    assert scripting[-1][0]["format"] == "mp3"
    capsys.readouterr()
    calls = len(scripting)
    assert run("speak", "hi", "--format", "ogg", "--out", str(tmp_path / "b.mp3"), "--json") == 1
    assert len(scripting) == calls and "does not match" in json.loads(capsys.readouterr().out)["error"]


def test_unwritable_output_folder_refused_before_billing(scripting, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.os, "access", lambda path, mode: False)
    assert run("speak", "hi", "--out", str(tmp_path / "a.mp3"), "--json") == 1
    assert not scripting and "not writable" in json.loads(capsys.readouterr().out)["error"]


def test_file_created_meanwhile_is_not_overwritten(scripting, tmp_path, monkeypatch, capsys):
    target = tmp_path / "a.mp3"
    real = cli._destination
    def late(path, force):
        real(path, force)
        if path == target and scripting:  # the copy-time check passes, then another writer creates the file
            target.write_bytes(b"theirs")
    monkeypatch.setattr(cli, "_destination", late)
    assert run("speak", "hi", "--out", str(target), "--json") == 1
    result = json.loads(capsys.readouterr().out)
    assert target.read_bytes() == b"theirs" and result["file_path"].endswith("cached.mp3")
    assert "--out failed" in result["error"]
    assert sorted(p.name for p in tmp_path.iterdir() if "fish-tmp" in p.name) == []


def test_no_hard_links_falls_back_to_exclusive_create(scripting, tmp_path, monkeypatch, capsys):
    def no_links(src, dest):
        raise PermissionError("hard links not supported")
    monkeypatch.setattr(cli.os, "link", no_links)
    target = tmp_path / "a.mp3"
    assert run("speak", "hi", "--out", str(target), "--json") == 0
    assert target.read_bytes() == b"ID3synthetic" and json.loads(capsys.readouterr().out)["out_path"] == str(target)


def test_failed_later_output_removes_the_files_this_run_placed(scripting, tmp_path, monkeypatch, capsys):
    for ext in (".srt", ".vtt"):
        (tmp_path / "cached").with_suffix(ext).write_text("captions")
    target = tmp_path / "out.mp3"
    real = cli._place_new
    def fail_vtt(temp_path, dest):
        if dest.suffix == ".vtt":
            raise OSError("disk full")
        real(temp_path, dest)
    monkeypatch.setattr(cli, "_place_new", fail_vtt)
    assert run("speak", "hi", "--timestamps", "--out", str(target), "--json") == 1
    result = json.loads(capsys.readouterr().out)
    assert "--out failed" in result["error"] and result["file_path"].endswith("cached.mp3")
    assert not target.exists() and not target.with_suffix(".srt").exists()
    assert not [p for p in tmp_path.iterdir() if "fish-tmp" in p.name]


def test_rollback_never_deletes_a_file_another_writer_put_there(scripting, tmp_path, monkeypatch, capsys):
    for ext in (".srt", ".vtt"):
        (tmp_path / "cached").with_suffix(ext).write_text("captions")
    target = tmp_path / "out.mp3"
    real = cli._place_new
    def swap_then_fail(temp_path, dest, identity):
        if dest.suffix == ".vtt":
            target.unlink()
            target.write_bytes(b"theirs")  # another writer replaced our placed audio
            raise OSError("disk full")
        return real(temp_path, dest, identity)
    monkeypatch.setattr(cli, "_place_new", swap_then_fail)
    assert run("speak", "hi", "--timestamps", "--out", str(target), "--json") == 1
    assert target.read_bytes() == b"theirs" and not target.with_suffix(".srt").exists()


def test_interrupt_during_forced_replace_passes_through(scripting, tmp_path, monkeypatch):
    target = tmp_path / "out.mp3"
    target.write_bytes(b"old")
    def interrupt(src, dest):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run("speak", "hi", "--out", str(target), "--force", "--json")
    assert target.read_bytes() == b"old" and not [p for p in tmp_path.iterdir() if "fish-tmp" in p.name]


def test_output_mode_follows_the_umask(scripting, tmp_path, capsys):
    previous = os.umask(0o077)
    try:
        assert run("speak", "hi", "--out", str(tmp_path / "a.mp3"), "--json") == 0
    finally:
        os.umask(previous)
    assert (tmp_path / "a.mp3").stat().st_mode & 0o777 == 0o600
