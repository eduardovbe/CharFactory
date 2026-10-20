"""Primeira abertura: a pessoa escolhe o idioma e o tema, e o app já abre assim.

Roda antes da janela principal ser importada (os rótulos dela são traduzidos ao importar), por
isso não importa nada da interface: lê os temas embutidos direto dos .json e se recolore com as
cores do tema escolhido, para a escolha ser vista na hora.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from .. import i18n
from ..i18n import tr

THEMES_DIR = Path(__file__).parent / "themes"
DEFAULT_FILE = "default"


def builtin_themes() -> list[dict]:
    """Os temas embutidos (o padrão primeiro), como estão no .json."""
    out = []
    for p in sorted(THEMES_DIR.glob("*.json"), key=lambda p: (p.stem != DEFAULT_FILE, p.name)):
        try:
            d = json.loads(p.read_text("utf-8"))
            out.append({"name": d["name"], "ui": d.get("ui", {})})
        except (OSError, ValueError, KeyError):
            continue
    return out


def preview(ui: dict, size: QSize) -> QPixmap:
    """Miniatura de uma janela no tema: barra, painel lateral, linhas de texto e o destaque."""
    w, h = size.width(), size.height()
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = lambda k, d: QColor(ui.get(k, d))            # noqa: E731
    p.setPen(col("border", "#444"))
    p.setBrush(col("window", "#202020"))
    p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 6, 6)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col("panel", "#2a2a2a"))
    p.drawRoundedRect(QRectF(6, 6, w * 0.3, h - 12), 3, 3)
    p.setBrush(col("accent", "#6366f1"))
    p.drawRoundedRect(QRectF(w * 0.3 + 12, 8, w * 0.32, 6), 3, 3)
    p.setBrush(col("text", "#eeeeee"))
    for k, frac in enumerate((0.55, 0.45, 0.5)):
        p.drawRoundedRect(QRectF(w * 0.3 + 12, 22 + k * 10, (w * 0.7 - 18) * frac, 4), 2, 2)
    p.setBrush(col("text_dim", "#999999"))
    for k in range(3):
        p.drawRoundedRect(QRectF(11, 12 + k * 9, w * 0.3 - 10, 3), 1.5, 1.5)
    p.end()
    return pm


class FirstRunDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.themes = builtin_themes()
        self.language = i18n.language()
        self.theme = self.themes[0]["name"] if self.themes else "Default"

        self.title = QLabel()
        f = QFont(self.font())
        f.setPointSizeF(f.pointSizeF() * 1.6)
        f.setBold(True)
        self.title.setFont(f)
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setObjectName("dim")
        self.lang_label = QLabel()
        self.theme_label = QLabel()
        for lbl in (self.lang_label, self.theme_label):
            lbl.setObjectName("section")

        lang_row = QHBoxLayout()
        lang_row.setSpacing(8)
        self.lang_group = QButtonGroup(self)
        self.lang_buttons = {}
        for code, name in i18n.LANGUAGES.items():
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(code == self.language)
            b.setMinimumHeight(34)
            b.clicked.connect(lambda _=False, c=code: self.pick_language(c))
            self.lang_group.addButton(b)
            self.lang_buttons[code] = b
            lang_row.addWidget(b)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.theme_group = QButtonGroup(self)
        self.theme_buttons = {}
        for k, t in enumerate(self.themes):
            b = QToolButton()
            b.setCheckable(True)
            b.setChecked(t["name"] == self.theme)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIcon(QIcon(preview(t["ui"], QSize(132, 78))))
            b.setIconSize(QSize(132, 78))
            b.setMinimumSize(150, 112)
            b.clicked.connect(lambda _=False, n=t["name"]: self.pick_theme(n))
            self.theme_group.addButton(b)
            self.theme_buttons[t["name"]] = b
            grid.addWidget(b, k // 4, k % 4)

        self.ok = QPushButton()
        self.ok.setDefault(True)
        self.ok.setMinimumHeight(36)
        self.ok.setMinimumWidth(140)
        self.ok.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(self.ok)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(10)
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)
        lay.addSpacing(8)
        lay.addWidget(self.lang_label)
        lay.addLayout(lang_row)
        lay.addSpacing(8)
        lay.addWidget(self.theme_label)
        holder = QWidget()
        holder.setLayout(grid)
        lay.addWidget(holder)
        lay.addSpacing(6)
        lay.addLayout(foot)
        self.retranslate()
        self.restyle()

    # ------------------------------------------------------------ escolhas

    def pick_language(self, code: str) -> None:
        self.language = code
        i18n.set_language(code)
        self.retranslate()

    def pick_theme(self, name: str) -> None:
        self.theme = name
        self.restyle()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("Welcome to Char Factory"))
        self.title.setText(tr("Welcome to Char Factory"))
        self.subtitle.setText(tr("Choose the language and the look. You can change both later in the View menu."))
        self.lang_label.setText(tr("Language"))
        self.theme_label.setText(tr("Theme"))
        self.ok.setText(tr("Get started"))
        for t in self.themes:
            self.theme_buttons[t["name"]].setText(tr(t["name"]))

    def restyle(self) -> None:
        """A própria tela no tema escolhido."""
        ui = next((t["ui"] for t in self.themes if t["name"] == self.theme), {})
        g = lambda k, d: ui.get(k, d)                  # noqa: E731
        self.setStyleSheet(f"""
            QDialog {{ background: {g('window', '#1e1f1a')}; }}
            QLabel {{ color: {g('text', '#eeeeee')}; }}
            QLabel#dim {{ color: {g('text_dim', '#999999')}; }}
            QLabel#section {{ color: {g('text_dim', '#999999')}; font-weight: bold; }}
            QPushButton, QToolButton {{
                background: {g('button', '#2a2a2a')}; color: {g('text', '#eeeeee')};
                border: 1px solid {g('border', '#444444')}; border-radius: 8px; padding: 6px 14px; }}
            QPushButton:hover, QToolButton:hover {{ background: {g('button_hover', '#3a3a3a')}; }}
            QPushButton:checked, QToolButton:checked {{
                border: 2px solid {g('accent', '#6366f1')}; background: {g('accent_soft', '#2a2a40')}; }}
            QPushButton:default {{
                background: {g('accent', '#6366f1')}; color: {g('accent_text', '#ffffff')};
                border: none; font-weight: bold; }}
        """)


def save_choice(language: str, theme: str) -> None:
    """Grava idioma e tema no arquivo de configurações (mantendo o resto que já houver)."""
    path = i18n.settings_path()
    try:
        cfg = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg.update(language=language, theme=theme)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=1, ensure_ascii=False), "utf-8")


def needed() -> bool:
    """Primeira abertura: o idioma ainda não foi escolhido."""
    return i18n.saved_language() is None


def run() -> None:
    """Mostra a escolha e grava; fechar sem escolher fica com o que estava selecionado."""
    dlg = FirstRunDialog()
    dlg.exec()
    i18n.set_language(dlg.language)
    save_choice(dlg.language, dlg.theme)
