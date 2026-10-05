"""Real plugin commands, CLI attachment and namespaced skill resolution."""
import argparse
import importlib.util
import json

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")


def test_real_slash_help_and_no_key_status(installed_fish_home):
    home, _, _ = installed_fish_home
    from hermes_cli.plugins import get_plugin_command_handler
    from agent.secret_scope import set_secret_scope, reset_secret_scope
    handler = get_plugin_command_handler("fish")
    assert callable(handler)
    assert "preview" in handler("help")
    token = set_secret_scope({}, profile_home=str(home))
    try:
        reply = handler("status")
        assert "isn't set up for this profile" in reply and "never paste API keys into chat" in reply
    finally:
        reset_secret_scope(token)


def test_real_cli_subparser_help(installed_fish_home, capsys):
    from hermes_cli.plugins import get_plugin_manager
    from hermes_cli.main import _attach_plugin_cli_command
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    descriptor = get_plugin_manager()._cli_commands["fish"]
    _attach_plugin_cli_command(subs, descriptor)
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["fish", "--help"])
    assert exc.value.code == 0 and "doctor" in capsys.readouterr().out


def test_real_namespaced_skills_resolve(installed_fish_home):
    from tools.skills_tool import skill_view
    from hermes_cli.plugins import get_plugin_manager
    import yaml
    for name in ("fish-audio-setup", "fish-audio-expressive-speech", "fish-audio-voice-studio"):
        result = json.loads(skill_view("fish-audio:" + name, preprocess=False))
        assert result["success"], result
        assert result["name"] == "fish-audio:" + name
        assert len(result["content"].splitlines()) < 200
        entry = get_plugin_manager()._plugin_skills["fish-audio:" + name]
        frontmatter = yaml.safe_load(entry["path"].read_text().split("---", 2)[1])
        assert entry["description"] == frontmatter["description"] and entry["description"]
        if name == "fish-audio-setup":
            assert "required_environment_variables" in result["content"] and "FISH_API_KEY" in result["content"]
