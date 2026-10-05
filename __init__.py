"""Fish Audio plugin for Hermes Agent.

``register`` must stay import-light and network-free: ``hermes plugins doctor`` blocks
sockets during registration, and the picker calls ``is_available`` on every paint.
"""

from __future__ import annotations

__all__ = ["register"]


def register(ctx) -> None:
    """Register Fish Audio providers, tools, hooks, commands and skills."""
    from .fish_audio.registration import register_all

    register_all(ctx)
