def test_register_is_network_free_and_importable(plugin, fake_ctx, monkeypatch):
    import socket

    def _no_network(*args, **kwargs):
        raise AssertionError("register() must not open sockets")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    plugin.register(fake_ctx)
    assert len(fake_ctx.tts_providers) == 1
    assert fake_ctx.tts_providers[0].name == "fish-audio"
