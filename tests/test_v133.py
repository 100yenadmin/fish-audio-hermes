"""UTC retirement boundary exercised through each public synthesis route."""
from datetime import date, datetime, time
from types import SimpleNamespace
import json

import pytest
import respx

from fish_audio import cli, client, commands, media, models, settings, streaming, tools, tts
from test_cli import config  # noqa: F401 — reuse isolated CLI config


@pytest.fixture(params=["2026-12-30", "2026-12-31", "2027-01-01"])
def retirement(request, monkeypatch):
    today = date.fromisoformat(request.param)
    monkeypatch.setattr(models, "datetime", SimpleNamespace(now=lambda tz: datetime.combine(today, time(), tz)))
    return ("s1", "Hello (happy) there.") if today < date(2026, 12, 31) else ("s2.1-pro", "Hello [happy] there.")


@pytest.mark.parametrize("path", ["synthesize", "stream", "speak", "speak_ts", "chat", "direct", "direct_pcm"])
def test_retirement_request(retirement, path, config, monkeypatch, tmp_path):
    cfg = config[0]
    cfg["tts"]["fish-audio"] = {"model": "s1"}
    monkeypatch.setattr(settings, "_config", lambda: cfg)
    monkeypatch.setattr(tts, "fish_api_key", lambda: "synthetic")
    monkeypatch.setattr(streaming, "fish_api_key", lambda: "synthetic")
    monkeypatch.setattr(commands, "fish_api_key", lambda: "synthetic")
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    with respx.mock() as mock:
        endpoint = "/v1/tts/stream/with-timestamp" if path == "speak_ts" else "/v1/tts"
        route = mock.post("https://api.fish.audio" + endpoint).respond(
            content=b'data: {"audio_base64":"SUQz"}\n\n' if path == "speak_ts" else b"ID3audio")
        if path == "synthesize":
            tts.FishAudioTTSProvider().synthesize("Hello [happy] there.", str(tmp_path / "out.mp3"))
        elif path == "stream":
            assert list(streaming.stream_pcm("Hello [happy] there."))
        elif path in {"speak", "speak_ts"}:
            tools._speak({"text": "Hello [happy] there.", "model": "s1", "timestamps": path == "speak_ts"},
                         "synthetic", "https://api.fish.audio", "")
        elif path == "chat":
            assert "MEDIA:" in commands.handle("preview " + "a" * 32 + " Hello [happy] there.")
        elif path == "direct_pcm":
            assert list(client.tts_pcm({"model": "s1", "text": "Hello [happy] there."}, "synthetic", "https://api.fish.audio"))
        else:
            client.tts_to_file({"model": "s1", "text": "Hello [happy] there."}, "synthetic", "https://api.fish.audio", tmp_path / "out.mp3")
        assert route.calls.last.request.headers["model"] == retirement[0]
        assert json.loads(route.calls.last.request.content)["text"] == retirement[1]


def test_retirement_speakers(retirement, config, monkeypatch, tmp_path):
    monkeypatch.setattr(media, "audio_output_dir", lambda: tmp_path)
    args = {"model": "s1", "text": "Hello [happy] there.", "speakers": ["a" * 32, "b" * 32]}
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://api.fish.audio/v1/tts").respond(content=b"ID3audio")
        if retirement[0] == "s1":
            with pytest.raises(ValueError, match="Multi-speaker speech"):
                tools._speak(args, "synthetic", "https://api.fish.audio", "")
            assert not route.calls
        else:
            tools._speak(args, "synthetic", "https://api.fish.audio", "")
            request = route.calls.last.request
            assert request.headers["model"] == retirement[0]
            assert json.loads(request.content)["text"] == retirement[1]
            assert json.loads(request.content)["reference_id"] == args["speakers"]


@pytest.mark.parametrize("path", ["direct", "stream"])
def test_retirement_ws(retirement, path, config, monkeypatch):
    from test_streaming import LiveServer
    server = LiveServer([{"event": "audio", "audio": b"\x00\x00"}, {"event": "finish", "reason": "stop"}])
    cfg = config[0]
    cfg["tts"]["fish-audio"] = {"model": "s1"}
    cfg["plugins"] = {"entries": {"fish-audio": {"settings": {"transport": "ws", "base_url": server.base_url}}}}
    monkeypatch.setattr(settings, "_config", lambda: cfg)
    monkeypatch.setattr(streaming, "fish_api_key", lambda: "synthetic")
    try:
        chunks = (client.tts_live({"model": "s1", "text": "Hello [happy] there."}, "synthetic", server.base_url)
                  if path == "direct" else streaming.stream_pcm("Hello [happy] there."))
        assert list(chunks) == [b"\x00\x00"]
        assert server.headers[0]["model"] == retirement[0]
        assert server.frames[1] == {"event": "text", "text": retirement[1]}
    finally:
        server.shutdown()


def test_retirement_warnings(retirement, config, monkeypatch, capsys):
    cfg = config[0]
    cfg["tts"]["fish-audio"] = {"model": "s1"}
    monkeypatch.setattr(settings, "_config", lambda: cfg)
    monkeypatch.setattr(commands, "fish_api_key", lambda: "synthetic")
    monkeypatch.setattr(cli, "fish_api_key", lambda: "")
    monkeypatch.setattr(commands, "write_config", lambda change: change(cfg))
    monkeypatch.setattr(commands.account, "cached_wallet", lambda *a: None)
    monkeypatch.setattr(commands.account, "get_package", lambda *a: None)
    warning = ("S1 is deprecated: Fish retires it on 2026-12-31 and then serves it as S2.1 Pro. Choose s2.1-pro."
               if retirement[0] == "s1" else "S1 has retired; requests use S2.1 Pro.")
    assert commands.handle("model s1") == "Saved.\n" + warning
    cli._handle(SimpleNamespace(fish_command="status"))
    assert "warn: " + warning in capsys.readouterr().out
    cli._doctor(SimpleNamespace(no_synth=True))
    assert "warn: " + warning in capsys.readouterr().out
    assert cfg["tts"]["fish-audio"]["model"] == "s1"
