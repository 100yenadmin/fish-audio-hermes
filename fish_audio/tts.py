"""Hermes's ordinary text_to_speech provider."""
from ._compat import TTSProvider
from . import client
from .models import MODELS
from .secrets import fish_api_key
from .settings import resolve_tts


class FishAudioTTSProvider(TTSProvider):
    name = "fish-audio"
    display_name = "Fish Audio"
    voice_compatible = True

    def is_available(self):
        try:
            return bool(fish_api_key())
        except Exception:
            return False

    def get_setup_schema(self):
        return {"name": "Fish Audio", "badge": "free model", "tag": "Expressive voices, voice cloning, 80+ languages",
                "env_vars": [{"key": "FISH_API_KEY", "prompt": "Fish Audio API key", "url": "https://fish.audio/app/api-keys"}]}

    def list_models(self):
        return [dict(row) for row in MODELS]

    def default_voice(self):
        return None

    def warm(self):
        pass

    def release(self):
        pass

    def synthesize(self, text, output_path, *, voice=None, model=None, speed=None, format="mp3", **extra):
        key = fish_api_key()
        if not key:
            raise ValueError("Set up Fish Audio with hermes tools or Desktop ▸ Plugins ▸ Fish Audio. Get an API key at https://fish.audio/app/api-keys")
        params, final_path = resolve_tts(voice, model, speed, format, output_path)
        base_url = params.pop("base_url")
        params["text"] = text
        client.tts_to_file(params, key, base_url, final_path)
        metadata = extra.get("result_metadata")
        if isinstance(metadata, dict):
            metadata.update(primary_provider="fish-audio", fallback_active=False)
        return final_path
