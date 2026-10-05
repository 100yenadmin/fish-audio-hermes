def test_register_is_network_free_and_importable(plugin, fake_ctx, monkeypatch):
    import socket

    def _no_network(*args, **kwargs):
        raise AssertionError("register() must not open sockets")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    plugin.register(fake_ctx)
    assert len(fake_ctx.tts_providers) == 1
    assert fake_ctx.tts_providers[0].name == "fish-audio"
    assert len(fake_ctx.stt_providers) == 1
    assert fake_ctx.stt_providers[0].name == "fish-audio"


def test_old_host_missing_methods_only_logs(plugin, caplog):
    plugin.register(object())
    assert "register_tts_provider" in caplog.text
    assert "register_transcription_provider" in caplog.text


def test_tool_and_hook_surface(plugin, fake_ctx, monkeypatch):
    plugin.register(fake_ctx)
    assert set(fake_ctx.tools) == {"fish_speak", "fish_voices", "fish_transcribe"}
    assert set(fake_ctx.hooks) == {"transform_llm_output", "pre_tool_call"}
    for name, tool in fake_ctx.tools.items():
        assert tool["toolset"] == "fish_audio" and tool["schema"]["name"] == name
        assert "billed to the user's Fish Audio account" in tool["description"]
        module = __import__(tool["handler"].__module__, fromlist=["fish_api_key"])
        monkeypatch.setattr(module, "fish_api_key", lambda: "")
        assert not tool["check_fn"]()
        monkeypatch.setattr(module, "fish_api_key", lambda: "test-key")
        assert tool["check_fn"]()


def test_registered_skill_descriptions_match_frontmatter(plugin, fake_ctx):
    import yaml
    plugin.register(fake_ctx)
    assert len(fake_ctx.skills) == 3
    for name, path in fake_ctx.skills.items():
        frontmatter = yaml.safe_load(path.read_text().split("---", 2)[1])
        assert fake_ctx.skill_descriptions[name] == frontmatter["description"]
        assert fake_ctx.skill_descriptions[name]


def test_register_needs_no_yaml(plugin, fake_ctx, monkeypatch):
    # Hermes main no longer ships PyYAML; loading the plugin must not import it.
    import sys
    monkeypatch.setitem(sys.modules, "yaml", None)
    plugin.register(fake_ctx)
    assert len(fake_ctx.skills) == 3 and all(fake_ctx.skill_descriptions.values())


def test_setup_skill_contains_exact_login_restart_hint(plugin, fake_ctx):
    from fish_audio.cli import RESTART_HINT
    plugin.register(fake_ctx)
    assert RESTART_HINT in fake_ctx.skills["fish-audio-setup"].read_text()
