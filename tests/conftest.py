"""Shared test fixtures.

``FakeCtx`` records what ``register`` registers so tests assert the plugin's public surface
without importing Hermes. Integration tests that mount through Hermes's real loader live in
``tests/integration/`` and run only inside a Hermes venv (see CI).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


class FakeCtx:
    def __init__(self) -> None:
        self.tts_providers: List[Any] = []
        self.stt_providers: List[Any] = []
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.hooks: Dict[str, List[Any]] = {}
        self.commands: Dict[str, Dict[str, Any]] = {}
        self.cli_commands: Dict[str, Dict[str, Any]] = {}
        self.skills: Dict[str, Path] = {}

    def register_tts_provider(self, provider: Any) -> None:
        self.tts_providers.append(provider)

    def register_transcription_provider(self, provider: Any) -> None:
        self.stt_providers.append(provider)

    def register_tool(self, name: str, **kwargs: Any) -> None:
        self.tools[name] = kwargs

    def register_hook(self, name: str, fn: Any) -> None:
        self.hooks.setdefault(name, []).append(fn)

    def register_command(self, name: str, **kwargs: Any) -> None:
        self.commands[name] = kwargs

    def register_cli_command(self, name: str, **kwargs: Any) -> None:
        self.cli_commands[name] = kwargs

    def register_skill(self, name: str, path: Any) -> None:
        self.skills[name] = Path(path)


def load_plugin_package():
    """Import the plugin root as a package named like Hermes's loader does (hyphen -> underscore)."""
    name = "hermes_plugins_test.fish_audio"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_ROOT / "__init__.py", submodule_search_locations=[str(PLUGIN_ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("hermes_plugins_test", type(sys)("hermes_plugins_test"))
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_ctx() -> FakeCtx:
    return FakeCtx()


@pytest.fixture
def plugin():
    return load_plugin_package()
