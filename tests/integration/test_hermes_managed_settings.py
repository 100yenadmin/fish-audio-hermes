"""Fish lock decisions match the leaves Hermes strips from profile config."""
import copy
import importlib.util

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("hermes_cli") is None, reason="requires the real Hermes venv")

VOICE = "a" * 32


@pytest.mark.parametrize("layer", [
    pytest.param({"tts": None}, id="tts-scalar"),
    pytest.param({"tts": {"fish-audio": {}}}, id="fish-empty"),
    pytest.param({"tts": {"fish-audio": {"model": "s1"}}}, id="model-scalar"),
    pytest.param({"tts": {"fish-audio": {"voice": {}}}}, id="voice-empty"),
    pytest.param({"tts": {"fish-audio": {"aliases": {"a": VOICE}}}}, id="alias-child"),
    pytest.param({"stt": {"provider": "x"}}, id="unrelated-stt"),
])
@pytest.mark.parametrize("leaf", ["voice", "model", "aliases"])
def test_real_managed_leaf_matches_config_strip(installed_fish_home, tmp_path, monkeypatch, layer, leaf):
    import yaml
    from hermes_cli import config, managed_scope
    from fish_audio import settings

    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "config.yaml").write_text(yaml.safe_dump(layer), encoding="utf-8")
    monkeypatch.setenv("HERMES_MANAGED_DIR", str(managed))
    managed_scope.invalidate_managed_cache()
    try:
        profile = {"tts": {"fish-audio": {"voice": VOICE, "model": "s2-pro", "aliases": {"a": VOICE}}}}
        stripped = copy.deepcopy(profile)
        config._strip_dotted_keys(stripped, managed_scope.managed_config_keys())
        remaining = stripped.get("tts", {}).get("fish-audio", {})
        changed = leaf not in remaining or remaining[leaf] != profile["tts"]["fish-audio"][leaf]
        assert settings.managed_leaf("tts", "fish-audio", leaf) is changed
    finally:
        monkeypatch.delenv("HERMES_MANAGED_DIR", raising=False)
        managed_scope.invalidate_managed_cache()
