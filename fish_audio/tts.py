"""Hermes's ordinary text_to_speech provider."""
from ._compat import TTSProvider
from . import client
from .models import MODELS
from .secrets import fish_api_key
from .settings import FREE_MODEL_NOTICE, resolve_tts
from .tags import adapt_tags
from .errors import FishAudioError
from .state import record_failure

SETUP_MESSAGE = ("Set up Fish Audio with hermes tools or Desktop ▸ Plugins ▸ Fish Audio. "
                 "Get an API key at https://fish.audio/app/api-keys")


class FishAudioTTSProvider(TTSProvider):
    name = "fish-audio"
    display_name = "Fish Audio"
    voice_compatible = True
    # Hermes's plugin PCM seam (#120398) reads these; registration turns the seam on when it exists.
    pcm_seam = False
    stream_sample_rate = 24000

    @property
    def streams_pcm(self):
        from .streaming import FishStreamer
        return self.pcm_seam and FishStreamer.available()

    def stream(self, text, *, voice=None, model=None, format="pcm", **extra):
        """Seam contract: int16 mono PCM; per-call voice and model go through the one settings resolver."""
        from .streaming import FishStreamer
        return FishStreamer({}, {}).stream(text, voice=voice, model=model)

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
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Nothing to say: the text is empty.")
        key = fish_api_key()
        if not key:
            raise ValueError(SETUP_MESSAGE)
        params, final_path = resolve_tts(voice, model, speed, format, output_path, key=key)
        base_url = params.pop("base_url")
        family = next(row["family"] for row in MODELS if row["id"] == params["model"])
        params["text"] = adapt_tags(text, family)
        metadata = extra.get("result_metadata")
        if isinstance(metadata, dict) and params["model_defaulted"] and params["model"] == "s2.1-pro-free":
            metadata["fish_audio_notice"] = FREE_MODEL_NOTICE
        try:
            client.tts_to_file(params, key, base_url, final_path)
        except FishAudioError as exc:
            record_failure(exc.kind, str(exc))
            raise
        if isinstance(metadata, dict):
            metadata.update(primary_provider="fish-audio", fallback_active=False)
        return final_path
