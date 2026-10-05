import sys
from types import ModuleType

import pytest
import respx
from decimal import Decimal

from fish_audio import media, settings, tts
from fish_audio.models import MODELS
from fish_audio.account import Wallet


@pytest.fixture(autouse=True)
def no_live_wallet(monkeypatch):
    monkeypatch.setattr("fish_audio.settings.cached_wallet", lambda *args: None)


def test_provider_surface_and_no_network(monkeypatch):
    provider = tts.FishAudioTTSProvider()
    assert provider.name == "fish-audio" and provider.display_name == "Fish Audio"
    assert provider.default_voice() is None and provider.voice_compatible is True
    assert provider.list_models() == MODELS
    assert [m["id"] for m in MODELS] == ["s2.1-pro", "s2.1-pro-free", "s2-pro", "s1", "drama-3-preview"]
    assert provider.get_setup_schema() == {"name": "Fish Audio", "badge": "free model",
        "tag": "Expressive voices, voice cloning, 80+ languages", "env_vars": [{"key": "FISH_API_KEY",
        "prompt": "Fish Audio API key", "url": "https://fish.audio/app/api-keys"}]}
    monkeypatch.setattr(tts, "fish_api_key", lambda: "test-key")
    assert provider.is_available()
    def fail():
        raise RuntimeError("synthetic secret failure")
    monkeypatch.setattr(tts, "fish_api_key", fail)
    assert not provider.is_available()
    provider.warm()
    provider.release()


def test_missing_key_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "fish_api_key", lambda: "")
    with pytest.raises(ValueError) as exc:
        tts.FishAudioTTSProvider().synthesize("hello", str(tmp_path / "out.mp3"))
    for hint in ("hermes tools", "Desktop ▸ Plugins ▸ Fish Audio", "https://fish.audio/app/api-keys"):
        assert hint in str(exc.value)


def test_synthesize_text_metadata_final_path_and_release(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "fish_api_key", lambda: "test-key")
    calls = []
    monkeypatch.setattr(tts.client, "tts_to_file", lambda *args: calls.append(args))
    metadata = {"other": 1}
    provider = tts.FishAudioTTSProvider()
    text = "[happy] untouched <|speaker:0|> (laugh)"
    output = provider.synthesize(text, str(tmp_path / "out.flac"), result_metadata=metadata)
    assert output.endswith("out.wav")
    assert calls[0][0]["text"] == text and calls[0][0]["format"] == "wav"
    assert calls[0][1:] == ("test-key", "https://api.fish.audio", output)
    assert metadata == {"other": 1, "primary_provider": "fish-audio", "fallback_active": False}
    original_client = tts.client._client
    provider.release()
    assert tts.client._client is original_client


def test_audio_dir_uses_active_hermes_home(monkeypatch, tmp_path):
    module = ModuleType("hermes_constants")
    homes = iter((tmp_path / "a", tmp_path / "b"))
    def get_dir(primary, legacy):
        assert (primary, legacy) == ("cache/audio", "audio_cache")
        return next(homes) / primary
    module.get_hermes_dir = get_dir
    monkeypatch.setitem(sys.modules, "hermes_constants", module)
    a, b = media.audio_output_dir(), media.audio_output_dir()
    assert a.is_dir() and b.is_dir() and a != b


@pytest.mark.parametrize("text", ["", " \t\n"])
def test_empty_provider_text_no_network(monkeypatch, tmp_path, text):
    monkeypatch.setattr(tts, "fish_api_key", lambda: "test-key")
    with respx.mock as mock:
        with pytest.raises(ValueError, match="Nothing to say: the text is empty."):
            tts.FishAudioTTSProvider().synthesize(text, str(tmp_path / "out.mp3"))
        assert not mock.calls


def test_resolved_family_and_free_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "fish_api_key", lambda: "test-key")
    monkeypatch.setattr(settings, "_config", lambda: {})
    monkeypatch.setattr(settings, "cached_wallet", lambda *args: Wallet(Decimal(0), Decimal(0), False))
    calls, metadata = [], {}
    monkeypatch.setattr(tts.client, "tts_to_file", lambda *args: calls.append(args))
    p = tts.FishAudioTTSProvider()
    p.synthesize("(happy) Hi", str(tmp_path / "out.mp3"), result_metadata=metadata)
    assert calls[-1][0]["text"] == "[happy] Hi"
    assert metadata["fish_audio_notice"] == settings.FREE_MODEL_NOTICE
    p.synthesize("[happy] Hi [free-form]", str(tmp_path / "out.mp3"), model="s1")
    assert calls[-1][0]["text"] == "(happy) Hi "


def test_static_surfaces_never_query_wallet(monkeypatch):
    def forbidden(*args):
        raise AssertionError("wallet must not be queried")
    monkeypatch.setattr(settings, "cached_wallet", forbidden)
    monkeypatch.setattr(tts, "fish_api_key", lambda: "test-key")
    provider = tts.FishAudioTTSProvider()
    assert provider.is_available()
    assert provider.default_model() == "s2.1-pro"
    assert provider.get_setup_schema() and provider.list_models()
