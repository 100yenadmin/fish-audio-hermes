"""Real loader and core transcription dispatch; Fish HTTP is mocked."""
from email.parser import BytesParser
from email.policy import default
import importlib.util
from pathlib import Path

import pytest
import respx

pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("filename,mime", [("synthetic.ogg", "audio/ogg"), ("synthetic.webm", "audio/webm")])
def test_core_stt_clean_transcript_and_exact_pro_header(installed_fish_home, filename, mime):
    home, _, provider = installed_fish_home
    from tools.transcription_tools import transcribe_audio
    path = ROOT / "tests/fixtures" / filename
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.fish.audio/v1/asr").respond(json={
            "text": "<|speaker:0|> Hello from the fish audio plugin for Hermes.", "request_id": "synthetic-request"},
            headers={"x-fish-trace-id": "synthetic-trace"})
        result = transcribe_audio(str(path))
        assert result == {"success": True, "transcript": "Hello from the fish audio plugin for Hermes.", "provider": "fish-audio"}
        assert route.call_count == 1
        request = route.calls.last.request
        assert request.headers["model"].encode() == b"transcribe-1-pro"
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.extensions["timeout"]["read"] == 300
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content)
        parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
        assert parts["tag_audio_events"].get_payload(decode=True) == b"false"
        assert parts["ignore_timestamps"].get_payload(decode=True) == b"true"
        assert parts["audio"].get_content_type() == mime
        assert parts["audio"].get_filename() == filename
        assert parts["audio"].get_payload(decode=True) == path.read_bytes()
        assert "diarize" not in parts
