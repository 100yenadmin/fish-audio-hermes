"""Unit-testable without Hermes; use the real ABC inside Hermes."""
try:
    from agent.tts_provider import TTSProvider
except ImportError:
    class TTSProvider:
        def list_voices(self):
            return []

        def default_model(self):
            models = self.list_models()
            return models[0]["id"] if models else None
