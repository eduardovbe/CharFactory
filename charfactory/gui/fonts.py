"""Fontes embutidas (JetBrains Mono, licença SIL OFL 1.1, ver fonts/OFL.txt) e o tamanho do texto
escolhido nas opções: um para a interface e outro para os editores de código."""
from __future__ import annotations

import re
import weakref
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

FONT_DIR = Path(__file__).parent / "fonts"
CODE_FAMILY = "JetBrains Mono"
_loaded = False

UI_DEFAULT = 9.0          # pt da interface (a folha de estilo foi desenhada para este tamanho)
CODE_DEFAULT = 10.5       # pt dos editores de código
UI_RANGE = (7.0, 16.0)
CODE_RANGE = (7.0, 28.0)
_sizes = {"ui": UI_DEFAULT, "code": CODE_DEFAULT}
_code_widgets: "weakref.WeakSet" = weakref.WeakSet()


def load() -> None:
    """Registra as fontes embutidas no Qt (uma vez por processo)."""
    global _loaded
    if _loaded:
        return
    for f in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    _loaded = True


def _clamp(v, lo_hi, default) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return min(max(v, lo_hi[0]), lo_hi[1])


def set_sizes(ui=None, code=None) -> None:
    if ui is not None:
        _sizes["ui"] = _clamp(ui, UI_RANGE, UI_DEFAULT)
    if code is not None:
        _sizes["code"] = _clamp(code, CODE_RANGE, CODE_DEFAULT)


def ui_size() -> float:
    return _sizes["ui"]


def code_size() -> float:
    return _sizes["code"]


def ui_scale() -> float:
    """Quanto a interface está maior (ou menor) que o tamanho para o qual o estilo foi feito."""
    return _sizes["ui"] / UI_DEFAULT


_PT = re.compile(r"(\d+(?:\.\d+)?)pt\b")


def scale_qss(qss: str, scale: float) -> str:
    """Escala os tamanhos em pt da folha de estilo (títulos, dicas) junto com a fonte da interface."""
    if abs(scale - 1.0) < 1e-3:
        return qss
    return _PT.sub(lambda m: f"{float(m.group(1)) * scale:.1f}pt", qss)


def ui_family() -> str:
    """Fonte da interface: a Segoe UI no Windows; nos outros sistemas, a fonte padrão deles
    (San Francisco no macOS, a do ambiente gráfico no Linux)."""
    families = QFontDatabase.families()
    for name in ("Segoe UI Variable Text", "Segoe UI"):
        if name in families:
            return name
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def code_font(size: float | None = None) -> QFont:
    load()
    if CODE_FAMILY in QFontDatabase.families():
        f = QFont(CODE_FAMILY)
    else:
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPointSizeF(code_size() if size is None else size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def track_code_widget(w) -> None:
    """Widget de código que acompanha a troca de tamanho (precisa ter apply_code_font())."""
    _code_widgets.add(w)


def refresh_code_widgets() -> None:
    for w in list(_code_widgets):
        try:
            w.apply_code_font()
        except RuntimeError:          # o objeto C++ já foi destruído
            pass
