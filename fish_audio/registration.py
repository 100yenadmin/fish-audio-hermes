"""Single wiring point for everything the plugin registers (filled in lane by lane)."""

from __future__ import annotations


def register_all(ctx) -> None:
    """Lanes (b)-(d) add providers, tools, hooks, commands and skills here."""
