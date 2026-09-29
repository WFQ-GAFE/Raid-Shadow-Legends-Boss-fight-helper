"""Battle damage in the game's own K/M/B notation (both languages)."""
from __future__ import annotations


def damage_text(value: float) -> str:
    """Same notation as the UI's damageText: 2.09B, 123.5M, 12.3K."""
    value = float(value)
    size = abs(value)
    if size >= 1e9:
        return f"{value / 1e9:.2f}B"
    if size >= 1e6:
        return f"{value / 1e6:.1f}M"
    if size >= 1e3:
        return f"{value / 1e3:.1f}K"
    return f"{value:.0f}"
