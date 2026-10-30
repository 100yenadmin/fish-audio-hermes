"""Install the plugin in an isolated real Hermes home, without network access."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
VOICE = "b" * 32
# Ports of this test's own loopback Fish stubs (fish_stub): the only connections the offline guard lets through.
STUB_PORTS = set()


@pytest.fixture
def installed_fish_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    mount = home / "plugins" / "fish-audio"
    mount.mkdir(parents=True)
    for name in ("__init__.py", "plugin.yaml", "pyproject.toml", "README.md", "LICENSE", ".gitignore", "parity.yaml"):
        shutil.copy2(ROOT / name, mount / name)
    for name in ("fish_audio", "dashboard", "tests", ".github", "skills", "scripts", "parity"):
        shutil.copytree(ROOT / name, mount / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (home / "config.yaml").write_text(
        "plugins:\n  enabled: [fish-audio]\ntts:\n  provider: fish-audio\n"
        f"  fish-audio:\n    voice: {VOICE}\n"
        "stt:\n  enabled: true\n  provider: fish-audio\n  fish-audio:\n    model: transcribe-1-pro\n",
        encoding="utf-8")
    (home / ".env").write_text("FISH_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("FISH_API_KEY", "")
    monkeypatch.delenv("HERMES_SAFE_MODE", raising=False)
    monkeypatch.delenv("HERMES_PLUGIN_HOST_PROCESS", raising=False)
    monkeypatch.delenv("HERMES_SESSION_PLATFORM", raising=False)
    import socket
    real_connect, real_create_connection = socket.socket.connect, socket.create_connection

    def stub(address):
        return isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost") and address[1] in STUB_PORTS

    def guarded_connect(sock, address):
        if not stub(address):
            raise AssertionError("integration must remain offline")
        return real_connect(sock, address)

    def guarded_create_connection(address, *args, **kwargs):
        if not stub(address):
            raise AssertionError("integration must remain offline")
        return real_create_connection(address, *args, **kwargs)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)

    # Deferred until a non-skipped integration test uses the fixture.
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.secret_scope import set_secret_scope, reset_secret_scope, load_env_file
    token = set_hermes_home_override(home)
    secret_token = set_secret_scope(load_env_file(home / ".env"), profile_home=str(home))
    try:
        from hermes_cli.plugins import discover_plugins, get_plugin_manager
        discover_plugins()
        from agent.tts_registry import get_provider as get_tts
        from agent.transcription_registry import get_provider as get_stt
        from agent.tts_provider import TTSProvider
        from agent.transcription_provider import TranscriptionProvider
        tts, stt = get_tts("fish-audio"), get_stt("fish-audio")
        for provider, base, expected in ((tts, TTSProvider, "FishAudioTTSProvider"),
                                         (stt, TranscriptionProvider, "FishAudioTranscriptionProvider")):
            assert provider is not None and isinstance(provider, base)
            assert provider.__class__.__name__ == expected
            module = __import__(provider.__class__.__module__, fromlist=["__file__"])
            assert Path(module.__file__).is_relative_to(mount)
            assert provider.is_available()
        yield home, tts, stt
    finally:
        get_plugin_manager().unload()
        reset_secret_scope(secret_token)
        reset_hermes_home_override(token)


@pytest.fixture
def fish_stub():
    """Start loopback Fish stubs: ``start(routes)`` -> base URL. Every request lands in ``calls`` as
    ``(base, method, path, headers)``. They serve this process and Hermes's plugin hosts alike (Hermes
    >= 0.22.5 runs a secondary profile's dashboard routes in that profile's own plugin host process)."""
    calls, servers = [], []

    def start(routes, host="127.0.0.1"):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.split("?", 1)[0]
                calls.append((base, "GET", path, {k.lower(): v for k, v in self.headers.items()}))
                status, payload = (200, routes[path]) if path in routes else (404, {"message": "not found"})
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        base = f"http://{host}:{port}"
        STUB_PORTS.add(port)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return base

    yield start, calls
    for server in servers:
        server.shutdown()
        server.server_close()
        STUB_PORTS.discard(server.server_address[1])
