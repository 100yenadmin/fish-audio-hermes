import argparse
import copy
import io
from pathlib import Path
import sys
from types import ModuleType

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
    assert "test-key" not in capsys.readouterr().out


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


@pytest.mark.parametrize("missing", [False, True])
def test_doctor_no_synth_exit_and_no_tts(config, monkeypatch, capsys, missing):
    data, _, _ = config
    data["tts"]["provider"] = data["stt"]["provider"] = "fish-audio"
    data["plugins"] = {"isolation": "host"}
    if missing:
        monkeypatch.setattr(cli, "fish_api_key", lambda: "")
    with respx.mock(assert_all_called=True) as mock:
        if not missing:
            mock.get(WALLET).respond(json={"credit": "1", "cumulative_top_up": "0", "has_free_credit": False})
        assert run("doctor", "--no-synth") == int(missing)
        assert all(r.request.method == "GET" for r in mock.calls)
    output = capsys.readouterr().out
    assert "round trip skipped" in output and "Host isolation" in output
    assert all(line.startswith(("ok:", "warn:", "fail:")) for line in output.splitlines())


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
