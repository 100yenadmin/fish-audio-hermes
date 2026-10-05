from pathlib import Path


def test_readme_documents_delivery_and_core_tts_limitations():
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    limitations = text.split("## Known limitations\n", 1)[1].split("## License", 1)[0]
    for phrase in ("per session", "Mid-turn context compression", "`media_tag`", "`.mp3`", "Opus",
                   "`<|speaker:N|>`", "`fish_speak`", "multi-speaker",
                   "https://github.com/NousResearch/hermes-agent/issues/133133",
                   "https://github.com/NousResearch/hermes-agent/issues/133131"):
        assert phrase in limitations
