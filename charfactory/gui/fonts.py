"""Fontes embutidas (JetBrains Mono, licença SIL OFL 1.1, ver fonts/OFL.txt)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

FONT_DIR = Path(__file__).parent / "fonts"
CODE_FAMILY = "JetBrains Mono"
_loaded = False


def load() -> None:
    """Registra as fontes embutidas no Qt (uma vez por processo)."""
    global _loaded
    if _loaded:
        return
    for f in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    _loaded = True


def code_font(size: float = 10.5) -> QFont:
    load()
    family = CODE_FAMILY if CODE_FAMILY in QFontDatabase.families() else "Consolas"
    f = QFont(family)
    f.setPointSizeF(size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f
