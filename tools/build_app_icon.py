"""Gera o ícone do Char Factory (charfactory/gui/assets/charfactory.png e charfactory.ico) a partir da
arte em tools/icon-source.png (a fábrica verde sobre fundo escuro).

A arte vem com muita margem: o desenho é recortado pelo verde, centralizado com folga e
posto numa placa arredondada da cor do fundo, para preencher o ícone até em 16 px.

    python tools/build_app_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "charfactory" / "gui" / "assets"
SOURCE = ROOT / "tools" / "icon-source.png"
PAD = 0.15          # folga em volta do desenho, em fração do lado
RADIUS = 0.22       # canto da placa, em fração do lado


def artwork() -> tuple[Image.Image, tuple[int, int, int]]:
    """O desenho recortado (quadrado, com folga) e a cor do fundo da arte."""
    src = Image.open(SOURCE).convert("RGB")
    bg = src.getpixel((4, 4))
    # máscara do desenho: pixels bem mais claros que o fundo
    gray = src.convert("L")
    mask = gray.point(lambda v, b=sum(bg) // 3: 255 if v > b + 60 else 0)
    l, t, r, b = mask.getbbox()
    side = max(r - l, b - t)
    side = int(side / (1 - 2 * PAD))
    cx, cy = (l + r) // 2, (t + b) // 2
    box = (cx - side // 2, cy - side // 2, cx - side // 2 + side, cy - side // 2 + side)
    canvas = Image.new("RGB", (side, side), bg)
    canvas.paste(src.crop(box), (0, 0))
    return canvas, bg


def plate(art: Image.Image, size: int) -> Image.Image:
    """Arte reduzida dentro de uma placa arredondada (fora dela, transparente)."""
    big = size * 4
    img = art.resize((big, big), Image.Resampling.LANCZOS).convert("RGBA")
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * RADIUS), fill=255)
    img.putalpha(mask)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> int:
    if not SOURCE.exists():
        print(f"falta a arte: {SOURCE}")
        return 1
    art, _ = artwork()
    plate(art, 256).save(ASSETS / "charfactory.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [plate(art, n) for n in sizes]
    imgs[-1].save(ASSETS / "charfactory.ico", sizes=[(n, n) for n in sizes], append_images=imgs[:-1])
    print("icon saved to", ASSETS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
