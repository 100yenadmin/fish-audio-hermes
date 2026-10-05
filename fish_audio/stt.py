"""Fish transcription for ordinary Hermes voice notes and dictation."""
import hashlib
import logging
from pathlib import Path
import re

from ._compat import TranscriptionProvider
from . import client
from .errors import FishAudioError
from .secrets import fish_api_key, redact
from .settings import DEFAULT_BASE_URL, _base_url, transport_settings
from .tts import FishAudioTTSProvider, setup_message
from .state import record_failure

MODEL_PRO = "transcribe-1-pro"
MODEL_T1 = "transcribe-1"
logger = logging.getLogger(__name__)
_warned_models = set()
MIMES = {
    ".mp3": "audio/mpeg", ".mpga": "audio/mpeg", ".mpeg": "audio/mpeg",
    ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
    ".webm": "audio/webm", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac",
}


class FishAudioTranscriptionProvider(TranscriptionProvider):
    name = "fish-audio"
    display_name = "Fish Audio"

    def is_available(self):
        try:
            return bool(fish_api_key())
        except Exception:
            return False

    def get_setup_schema(self):
        return FishAudioTTSProvider().get_setup_schema()

    def list_models(self):
        return [{"id": MODEL_PRO, "display": "Transcribe 1 Pro (recommended)"},
                {"id": MODEL_T1, "display": "Transcribe 1"}]

    def default_model(self):
        return MODEL_PRO

    def transcribe(self, file_path, *, model=None, language=None, **extra):
        result = {"success": False, "transcript": "", "provider": "fish-audio"}
        try:
            key = fish_api_key()
            if not key:
                result.update(error=setup_message(), error_kind="credential")
                return result
            path = Path(file_path).expanduser()
            chosen = MODEL_PRO if model is None else str(model).strip().lower()
            if chosen not in {MODEL_PRO, MODEL_T1}:
                fingerprint = hashlib.sha256(chosen.encode()).hexdigest()
                if fingerprint not in _warned_models:
                    _warned_models.add(fingerprint)
                    logger.warning("Unknown Fish Audio transcription model; using %s.", MODEL_PRO)
                chosen = MODEL_PRO
            if chosen == MODEL_T1 and path.suffix.lower() == ".webm":
                logger.debug("Upgrading WebM transcription to transcribe-1-pro.")
                chosen = MODEL_PRO
            fields = {"ignore_timestamps": "true"}
            if chosen == MODEL_PRO:
                fields["tag_audio_events"] = "false"
            if isinstance(language, str):
                primary = language.split("-", 1)[0].lower()
                if re.fullmatch(r"[a-z]{2,3}", primary):
                    fields["language"] = primary
            base_url = _base_url(transport_settings().get("base_url", DEFAULT_BASE_URL))
            try:
                audio = path.read_bytes()
            except OSError:
                # A missing or unreadable local file is the caller's input, not a Fish outage.
                result.update(error_kind="invalid_request", error=str(FishAudioError(
                    "invalid_request", None, None, "Could not read the audio file. Check the path and try again.")))
                return result
            data = client.transcribe_audio(audio, path.name,
                                           MIMES.get(path.suffix.lower(), "application/octet-stream"),
                                           fields, key=key, base_url=base_url, model=chosen)
            transcript = re.sub(r"<\|speaker:\d+\|>", " ", data["text"])
            result.update(success=True, transcript=" ".join(transcript.split()))
        except FishAudioError as exc:
            record_failure(exc.kind, str(exc))
            result.update(error=redact(str(exc)), error_kind=exc.kind)
        except Exception:
            # Unknown exception messages may contain file contents, paths or credentials.
            result["error_kind"] = "availability"
            result["error"] = str(FishAudioError("availability", None, None,
                                                "Fish Audio transcription failed. Check the audio file and try again."))
        return result
