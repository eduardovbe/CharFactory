"""Um desenho por função: dois ícones com nomes diferentes não podem sair iguais."""
import hashlib
import os
import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from charfactory.gui import icons  # noqa: E402


def _names() -> list[str]:
    src = Path(icons.__file__).read_text("utf-8")
    out = []
    for m in re.finditer(r'name == "([a-z_0-9]+)"|name in \(([^)]*)\)', src):
        out += [m.group(1)] if m.group(1) else re.findall(r'"([a-z_0-9]+)"', m.group(2))
    return list(dict.fromkeys(out))


def test_every_icon_draws_something_and_no_two_are_the_same():
    QApplication.instance() or QApplication([])
    seen: dict[str, str] = {}
    blank = None
    dupes = []
    for name in _names() + ["__nenhum__"]:
        img = icons.icon(name).pixmap(QSize(24, 24)).toImage()
        digest = hashlib.sha1(bytes(img.constBits())).hexdigest()
        if name == "__nenhum__":
            blank = digest                       # nome desconhecido: o ponto de reserva
            continue
        if digest in seen:
            dupes.append((seen[digest], name))
        seen.setdefault(digest, name)
    assert not dupes, f"ícones iguais: {dupes}"
    fallback = [n for n, d in ((v, k) for k, v in seen.items()) if d == blank]
    assert not fallback, f"sem desenho próprio (caem no ponto de reserva): {fallback}"
