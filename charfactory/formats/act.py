"""Paletas ACT (Adobe Color Table): 256 cores RGB, 768 bytes.

No MUGEN as paletas de personagem (.act) são gravadas em ordem invertida:
a cor 0 do sprite (transparente) é o último trio do arquivo.
"""
from __future__ import annotations

from pathlib import Path

Color = tuple[int, int, int]


def load(path: str | Path, reverse: bool = True) -> list[Color]:
    data = Path(path).read_bytes()[:768].ljust(768, b"\0")
    cols = [(data[i], data[i + 1], data[i + 2]) for i in range(0, 768, 3)]
    return cols[::-1] if reverse else cols


def save(colors: list[Color], path: str | Path, reverse: bool = True) -> None:
    cols = (list(colors[:256]) + [(0, 0, 0)] * 256)[:256]
    if reverse:
        cols = cols[::-1]
    Path(path).write_bytes(bytes(c for rgb in cols for c in rgb))
