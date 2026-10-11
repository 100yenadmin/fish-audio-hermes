"""Unit-testable without Hermes; use the real ABC inside Hermes."""
from abc import ABC, abstractmethod
try:
    from agent.tts_provider import TTSProvider
except ImportError:
    class TTSProvider:
        def list_voices(self):
            return []

        def default_model(self):
            models = self.list_models()
            return models[0]["id"] if models else None

try:
    from agent.transcription_provider import TranscriptionProvider
except ImportError:
    class TranscriptionProvider:
        def list_models(self):
            return []

try:
    from agent.transcription_provider import TranscriptionStreamSession
    HAS_STT_STREAM_SEAM = True
except ImportError:
    HAS_STT_STREAM_SEAM = False

    class TranscriptionStreamSession(ABC):
        @abstractmethod
        def push_audio(self, chunk): ...

        @abstractmethod
        def finalize(self): ...

        def partial_transcript(self):
            return ""
