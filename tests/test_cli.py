import argparse
import copy
import io
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
            assert "s2.1-pro-free" not in output and settings.FREE_MODEL_NOTICE not in output
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
