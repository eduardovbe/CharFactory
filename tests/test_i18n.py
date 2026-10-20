"""Idiomas: catálogos completos, troca de idioma, nada de português fora do tr() e a escolha da
primeira abertura (idioma e tema) gravada como a pessoa clicou."""
import ast
import json
import os
import re
import sys
from pathlib import Path

import pytest

from charfactory import i18n

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def lang():
    """Troca o idioma no teste e volta ao inglês no fim."""
    yield i18n.set_language
    i18n.set_language("en")


def test_catalogs_cover_every_text_with_the_same_placeholders():
    sys.path.insert(0, str(ROOT / "tools"))
    import i18n_check
    r = i18n_check.check()
    for lang_ in i18n_check.LOCALES:
        assert not r["missing"][lang_], r["missing"][lang_][:10]
        assert not r["holes"][lang_], r["holes"][lang_][:10]
        assert not r["stale"][lang_], r["stale"][lang_][:10]


def test_switching_language_translates(lang):
    assert i18n.tr("Open…") == "Open…"
    lang("pt_BR")
    assert i18n.tr("Open…") == "Abrir…" and i18n.tr("Sprite {0},{1} edited").format(1, 2) == "Sprite 1,2 editado"
    lang("es")
    assert i18n.tr("Open…") == "Abrir…" and i18n.tr("Save") == "Guardar"
    assert i18n.tr("text that has no translation") == "text that has no translation"
    lang("xx")                                    # idioma desconhecido cai no inglês
    assert i18n.language() == "en"


def test_language_choice_order(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("CHARFACTORY_LANG", raising=False)
    monkeypatch.setattr(i18n, "system_language", lambda: "es")
    assert i18n.resolve_language() == "es"                       # sem escolha salva: o do sistema
    (tmp_path / "charfactory").mkdir()
    (tmp_path / "charfactory" / "config.json").write_text(json.dumps({"language": "pt_BR"}), "utf-8")
    assert i18n.resolve_language() == "pt_BR"                    # a escolha salva ganha
    monkeypatch.setenv("CHARFACTORY_LANG", "en")
    assert i18n.resolve_language() == "en"                       # e a variável ganha de todas


def test_no_portuguese_left_outside_tr():
    """Texto com acento no código só pode estar dentro de tr() (catálogo) ou em docstring;
    a exceção é o mapa que migra os nomes antigos dos temas."""
    accent = re.compile(r"[áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]")
    allowed = {"Escuro", "Claro", "Meia-noite", "Esmeralda", "Clássico", "Alto contraste", "Português (Brasil)",
               "Español"}
    found = []
    for p in (ROOT / "charfactory").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        tree = ast.parse(p.read_text("utf-8"))
        skip = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and n.body:
                b = n.body[0]
                if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant):
                    skip.add(id(b.value))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" and n.args:
                skip |= {id(d) for d in ast.walk(n.args[0])}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip
                    and accent.search(n.value) and n.value not in allowed):
                found.append(f"{p.name}:{n.lineno}: {n.value[:60]!r}")
    assert not found, found


def test_old_theme_names_are_migrated():
    pytest.importorskip("PySide6")
    from charfactory.gui import theme
    assert theme.migrate_name("Escuro") == "Dark" and theme.migrate_name("Monokai") == "Default"
    assert theme.migrate_name("Meu tema") == "Meu tema"
    names = list(theme.load_all())
    assert names[0] == theme.DEFAULT_THEME                      # o padrão vem primeiro


def test_first_run_picks_language_and_theme_with_clicks(monkeypatch, tmp_path, lang):
    """Primeira abertura: a tela troca o texto ao clicar no idioma, se recolore com o tema e
    grava os dois; com o idioma salvo ela não aparece mais."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from charfactory.gui import firstrun
    monkeypatch.setenv("APPDATA", str(tmp_path))
    QApplication.instance() or QApplication([])
    assert firstrun.needed()
    dlg = firstrun.FirstRunDialog()
    dlg.show()
    QTest.mouseClick(dlg.lang_buttons["es"], Qt.MouseButton.LeftButton)
    assert dlg.language == "es" and dlg.title.text() == "Bienvenido a Char Factory"
    assert dlg.theme_buttons["Dark"].text() == "Oscuro"
    QTest.mouseClick(dlg.theme_buttons["Dark"], Qt.MouseButton.LeftButton)
    assert dlg.theme == "Dark" and "#" in dlg.styleSheet()
    QTest.mouseClick(dlg.lang_buttons["pt_BR"], Qt.MouseButton.LeftButton)
    assert dlg.ok.text() == "Começar"
    monkeypatch.setattr(firstrun.FirstRunDialog, "exec", lambda self: (self.pick_language("es"),
                                                                      self.pick_theme("Light"), 1)[-1])
    firstrun.run()
    cfg = json.loads((tmp_path / "charfactory" / "config.json").read_text("utf-8"))
    assert cfg["language"] == "es" and cfg["theme"] == "Light" and i18n.language() == "es"
    assert not firstrun.needed()
