"""Temas da interface.

Um tema tem duas camadas:
- colorset: as 20 cores do canvas e do realce de código (colorsets .xml com esses nomes
  podem ser importados diretamente);
- ui: cores-base da interface, das quais a folha de estilo (QSS) é gerada.
Temas embutidos ficam em charfactory/gui/themes/*.json; os do usuário em %APPDATA%/charfactory/themes.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from ..i18n import tr

BUILTIN_DIR = Path(__file__).parent / "themes"
DEFAULT_THEME, DEFAULT_FILE = "Default", "default"
OLD_NAMES = {"Escuro": "Dark", "Claro": "Light", "Meia-noite": "Midnight", "Esmeralda": "Emerald",
             "Monokai": "Default", "Clássico": "Classic", "Alto contraste": "High contrast"}


def user_dir() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) / "charfactory" / "themes"
    base.mkdir(parents=True, exist_ok=True)
    return base


# nomes de cor do colorset e uma descrição para o editor de temas
COLORSET_KEYS = {
    "back_start": tr("Canvas background (top of the gradient)"),
    "back_end": tr("Canvas background (bottom of the gradient)"),
    "attackbox": tr("Attack box (Clsn1)"),
    "collisionbox": tr("Collision box (Clsn2)"),
    "sound_wave": tr("Waveform"),
    "block_range": tr("Code: current block"),
    "line": tr("Code: current line"),
    "paren": tr("Code: matching parentheses"),
    "paren_range": tr("Code: range between parentheses"),
    "comment": tr("Code: comment"),
    "block": tr("Code: section header"),
    "number": tr("Code: number"),
    "string": tr("Code: quoted text"),
    "keyword": tr("Code: parameter"),
    "keyword_value": tr("Code: recognized value"),
    "block_type": tr("Code: controller type"),
    "function": tr("Code: trigger"),
    "const": tr("Code: constant/redirection"),
    "background": tr("Code: editor background"),
    "foreground": tr("Code: text"),
    "operator": tr("Code: operators and punctuation"),
    "key_trigger": tr("Code: trigger1/triggerall"),
    "label": tr("Code: section label"),
}

UI_KEYS = {
    "window": tr("Window"),
    "panel": tr("Panels"),
    "panel_alt": tr("Panels (alternate)"),
    "input": tr("Text fields"),
    "text": tr("Text"),
    "text_dim": tr("Secondary text"),
    "border": tr("Borders"),
    "accent": tr("Accent"),
    "accent_text": tr("Text on accent"),
    "accent_soft": tr("Soft accent (active items)"),
    "icon": tr("Icons"),
    "selection": tr("Selection"),
    "button": tr("Buttons"),
    "button_hover": tr("Buttons (hover)"),
    "error": tr("Error"),
    "warning": tr("Warning"),
}


@dataclass
class ColorSpec:
    rgb: tuple[int, int, int]
    bold: bool = False
    italic: bool = False

    @property
    def hex(self) -> str:
        return "#%02x%02x%02x" % self.rgb


@dataclass
class Theme:
    name: str
    colorset: dict[str, ColorSpec] = field(default_factory=dict)
    ui: dict[str, str] = field(default_factory=dict)  # "#rrggbb"
    builtin: bool = False
    path: Path | None = None

    def c(self, key: str) -> ColorSpec:
        return self.colorset.get(key) or ColorSpec((128, 128, 128))

    def to_json(self) -> dict:
        return {"name": self.name,
                "colorset": {k: {"rgb": list(v.rgb), "bold": v.bold, "italic": v.italic}
                             for k, v in self.colorset.items()},
                "ui": self.ui}

    def save(self, path: Path | None = None) -> Path:
        path = path or (user_dir() / f"{_slug(self.name)}.json")
        path.write_text(json.dumps(self.to_json(), indent=1, ensure_ascii=False), "utf-8")
        self.path = path
        return path

    def stylesheet(self) -> str:
        return build_qss({**self.ui, "code_bg": self.c("background").hex}, write_assets(self))


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name).strip("_") or "theme"


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb[:3])


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex([round(x + (y - x) * t) for x, y in zip(ra, rb)])


def _luma(h: str) -> float:
    r, g, b = _rgb(h)
    return 0.299 * r + 0.587 * g + 0.114 * b


def from_json(d: dict, path: Path | None = None, builtin: bool = False) -> Theme:
    cs = {k: ColorSpec(tuple(v["rgb"]), v.get("bold", False), v.get("italic", False))
          for k, v in d.get("colorset", {}).items()}
    t = Theme(d["name"], cs, dict(d.get("ui", {})), builtin, path)
    complete_ui(t)
    return t


def complete_ui(t: Theme) -> None:
    """Preenche cores de interface faltantes a partir do colorset (fundo/texto)."""
    bg = t.ui.get("window") or t.c("background").hex
    fg = t.ui.get("text") or t.c("foreground").hex
    dark = _luma(bg) < 128
    accent = t.ui.get("accent") or (t.c("block").hex if "block" in t.colorset else "#6366f1")
    defaults = {
        "window": bg,
        "panel": _mix(bg, fg, 0.04),
        "panel_alt": _mix(bg, fg, 0.09),
        "input": _mix(bg, "#000000" if dark else "#ffffff", 0.35),
        "text": fg,
        "text_dim": _mix(fg, bg, 0.42),
        "border": _mix(bg, fg, 0.14),
        "accent": accent,
        "selection": _mix(bg, accent, 0.30),
        "accent_soft": _mix(bg, accent, 0.18),
        "button": _mix(bg, fg, 0.07),
        "button_hover": _mix(bg, fg, 0.13),
        "error": "#f87171" if dark else "#dc2626",
        "warning": "#fbbf24" if dark else "#d97706",
    }
    for k, v in defaults.items():
        t.ui.setdefault(k, v)
    t.ui.setdefault("icon", _mix(t.ui["text"], t.ui["text_dim"], 0.35))
    # cores de código que não existem nos colorsets .xml: derivadas das existentes
    fgc, com = t.c("foreground").hex if "foreground" in t.colorset else fg, t.c("comment").hex
    t.colorset.setdefault("operator", ColorSpec(_rgb(_mix(fgc, com, 0.55))))
    t.colorset.setdefault("key_trigger", ColorSpec(_rgb(t.c("function").hex) if "function" in t.colorset
                                                   else _rgb(accent), True))
    t.colorset.setdefault("label", ColorSpec(_rgb(_mix(fgc, com, 0.25)), False, True))
    t.ui.setdefault("accent_text", "#000000" if _luma(t.ui["accent"]) > 150 else "#ffffff")
    _ensure_contrast(t.ui)


def _contrast(a: str, b: str) -> float:
    """Razão de contraste WCAG entre duas cores."""
    def lum(h):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b_ = _rgb(h)
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b_)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _ensure_contrast(u: dict) -> None:
    """Texto secundário, aviso e erro legíveis (4,5:1) em todos os fundos de painel, e texto do
    botão primário legível sobre o destaque. Puxa a cor na direção do texto principal até passar;
    no tema claro o âmbar de aviso ficava em 3:1."""
    grounds = [u["window"], u["panel"], u["panel_alt"]]
    target = "#000000" if _luma(u["window"]) > 128 else "#ffffff"
    for key in ("text_dim", "warning", "error"):
        c = u[key]
        k = 0.0
        while min(_contrast(c, g) for g in grounds) < 4.5 and k < 1:
            k += 0.04
            c = _mix(u[key], target, k)
        u[key] = c
    best = max(("#000000", "#ffffff"), key=lambda c: _contrast(c, u["accent"]))
    if _contrast(u["accent_text"], u["accent"]) < 4.5 and _contrast(best, u["accent"]) > _contrast(u["accent_text"], u["accent"]):
        u["accent_text"] = best


def import_colorset(path: str | Path, name: str | None = None) -> Theme:
    """Converte um colorset (.xml) em tema."""
    root = ET.parse(path).getroot()
    cs = {}
    for c in root.iter("color"):
        val = [int(x) for x in c.get("value", "0,0,0").split(",")]
        cs[c.get("name")] = ColorSpec(tuple(val[:3]), c.get("bold") == "true", c.get("italic") == "true")
    t = Theme(name or Path(path).stem, cs)
    complete_ui(t)
    return t


def load_all() -> dict[str, Theme]:
    """Embutidos (o padrão primeiro) e depois os do usuário."""
    themes: dict[str, Theme] = {}
    for d, builtin in ((BUILTIN_DIR, True), (user_dir(), False)):
        for p in sorted(d.glob("*.json"), key=lambda p: (builtin and p.stem != DEFAULT_FILE, p.name)):
            try:
                t = from_json(json.loads(p.read_text("utf-8")), p, builtin)
                themes[t.name] = t
            except (ValueError, KeyError):
                continue
    return themes


def display_name(t: Theme) -> str:
    """Nome na interface: os embutidos no idioma escolhido, os do usuário como ele escreveu."""
    return tr(t.name) if t.builtin else t.name


def migrate_name(name: str) -> str:
    """Nome salvo antes da tradução (os embutidos tinham nome em português)."""
    return OLD_NAMES.get(name, name)


def write_assets(t: Theme) -> Path:
    """SVGs do tema (marca do checkbox, ponto do rádio, setas) para a folha de estilo."""
    u = t.ui
    d = user_dir().parent / "qss" / _slug(t.name)
    d.mkdir(parents=True, exist_ok=True)
    stroke = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">{}</svg>'
    files = {
        "check.svg": svg.format(f'<path d="M3.5 8.5l3 3 6-7" {stroke} stroke="{u["accent_text"]}" stroke-width="2"/>'),
        "radio.svg": svg.format(f'<circle cx="8" cy="8" r="3" fill="{u["accent_text"]}"/>'),
        "down.svg": svg.format(f'<path d="M4 6l4 4 4-4" {stroke} stroke="{u["text_dim"]}" stroke-width="1.6"/>'),
        "up.svg": svg.format(f'<path d="M4 10l4-4 4 4" {stroke} stroke="{u["text_dim"]}" stroke-width="1.6"/>'),
        "close.svg": svg.format(f'<path d="M5 5l6 6M11 5l-6 6" {stroke} stroke="{u["text_dim"]}" stroke-width="1.6"/>'),
        # setas da árvore: com ::branch estilizado o Qt não desenha mais as dele
        "branch-closed.svg": svg.format(f'<path d="M6.5 4.5l3.5 3.5-3.5 3.5" {stroke} stroke="{u["text_dim"]}" stroke-width="1.6"/>'),
        "menu-check.svg": svg.format(f'<path d="M3.5 8.5l3 3 6-7" {stroke} stroke="{u["accent"]}" stroke-width="1.8"/>'),
        "branch-open.svg": svg.format(f'<path d="M4.5 6.5l3.5 3.5 3.5-3.5" {stroke} stroke="{u["text_dim"]}" stroke-width="1.6"/>'),
    }
    for name, content in files.items():
        f = d / name
        if not f.exists() or f.read_text("utf-8") != content:
            f.write_text(content, "utf-8")
    return d


def build_qss(u: dict[str, str], assets: Path | None = None) -> str:
    """Folha de estilo no espírito do Tailwind: superfícies neutras, cantos arredondados,
    bordas de 1 px sutis, foco na cor de destaque e abas sublinhadas."""
    a = (assets.as_posix() + "/") if assets else ""
    u = {"code_bg": u["window"], **u}
    # contorno de controles desmarcados: visível também nos temas claros (border sozinho some)
    ctl = _mix(u["border"], u["text_dim"], 0.8)
    return f"""
QWidget {{ background-color: {u['window']}; color: {u['text']}; selection-background-color: {u['selection']};
          selection-color: {u['text']}; }}
QMainWindow::separator {{ background: {u['border']}; width: 1px; height: 1px; }}
QSplitter::handle {{ background: {u['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QMenuBar {{ background: {u['window']}; border-bottom: 1px solid {u['border']}; padding: 1px 4px; }}
QMenuBar::item {{ padding: 4px 9px; background: transparent; border-radius: 6px; color: {u['text_dim']}; }}
QMenuBar::item:selected {{ background: {u['panel_alt']}; color: {u['text']}; }}
QMenu {{ background: {u['panel']}; border: 1px solid {u['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 8px; border-radius: 5px; }}
QMenu::item:selected {{ background: {u['accent_soft']}; color: {u['text']}; }}
QMenu::item:disabled {{ color: {u['text_dim']}; }}
QMenu::icon {{ padding-left: 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; padding-left: 8px; }}
QMenu::indicator:checked {{ image: url({a}menu-check.svg); }}
QMenu::right-arrow {{ image: url({a}branch-closed.svg); width: 14px; height: 14px; padding-right: 6px; }}
QMenu::separator {{ height: 1px; background: {u['border']}; margin: 5px 4px; }}

QToolBar {{ background: {u['window']}; border: none; border-bottom: 1px solid {u['border']}; spacing: 2px; padding: 3px 6px; }}
QToolBar::separator {{ background: {u['border']}; width: 1px; margin: 5px 5px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px; color: {u['text_dim']}; }}
QToolButton:hover {{ background: {u['panel_alt']}; color: {u['text']}; }}
QToolButton:pressed {{ background: {u['button_hover']}; }}
QToolButton:checked {{ background: {u['accent_soft']}; color: {u['accent']}; }}
QToolButton[segment="true"] {{ padding: 4px 9px; font-weight: 600; }}
QToolButton[segment="true"]:checked {{ background: {u['accent_soft']}; color: {u['accent']}; border: 1px solid {u['accent_soft']}; }}
QToolButton::menu-indicator {{ image: none; }}

QPushButton {{ background: {u['button']}; border: 1px solid {u['border']}; border-radius: 6px; padding: 4px 12px; font-weight: 500; }}
QPushButton:hover {{ background: {u['button_hover']}; }}
QPushButton:pressed {{ background: {u['panel_alt']}; }}
QPushButton:disabled {{ color: {u['text_dim']}; }}
QPushButton[primary="true"] {{ background: {u['accent']}; color: {u['accent_text']}; border-color: {u['accent']}; }}
QPushButton[primary="true"]:hover {{ border-color: {u['text_dim']}; }}
QPushButton:disabled, QPushButton[primary="true"]:disabled {{ background: {u['panel_alt']}; color: {u['text_dim']};
                                                              border-color: {u['border']}; }}
QToolButton:disabled {{ color: {u['text_dim']}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
    background: {u['input']}; border: 1px solid {u['border']}; border-radius: 5px; padding: 2px 6px; }}
QLineEdit, QAbstractSpinBox, QComboBox {{ min-height: 18px; }}
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {{ color: {u['text_dim']}; background: {u['panel']}; }}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover {{ border-color: {u['button_hover']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{ border: 1px solid {u['accent']}; }}
QLineEdit:read-only {{ background: {u['panel']}; color: {u['text_dim']}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; background: transparent; width: 16px; }}
QAbstractSpinBox::up-arrow {{ image: url({a}up.svg); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url({a}down.svg); width: 10px; height: 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({a}down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {u['panel']}; border: 1px solid {u['border']}; border-radius: 6px; padding: 2px;
                               selection-background-color: {u['accent_soft']}; outline: none; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; border: 1px solid {ctl};
                                                 background: {u['input']}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {u['accent']}; }}
QCheckBox::indicator:checked {{ background: {u['accent']}; border-color: {u['accent']}; image: url({a}check.svg); }}
QRadioButton::indicator:checked {{ background: {u['accent']}; border-color: {u['accent']}; image: url({a}radio.svg); }}

QTabWidget::pane {{ border: none; border-top: 1px solid {u['border']}; background: {u['window']}; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; border: none; border-bottom: 2px solid transparent; padding: 6px 10px;
               margin: 0 2px; color: {u['text_dim']}; }}
QTabBar::tab:hover {{ color: {u['text']}; }}
QTabBar::tab:selected {{ color: {u['text']}; border-bottom-color: {u['accent']}; }}
QTabBar::close-button {{ image: url({a}close.svg); subcontrol-position: right; margin-left: 4px; border-radius: 4px; }}
QTabBar::close-button:hover {{ background: {u['panel_alt']}; }}

/* project tabs: chips on the main bar */
QTabBar#projectTabs::tab {{ border: 1px solid transparent; border-radius: 6px; padding: 3px 0 3px 10px;
                           margin: 2px 4px 2px 0; color: {u['text_dim']}; font-weight: 500; }}
QTabBar#projectTabs::tab:hover {{ background: {u['panel']}; color: {u['text']}; }}
QTabBar#projectTabs::tab:selected {{ background: {u['panel_alt']}; border-color: {u['border']}; color: {u['text']}; }}

/* document tabs: the active one merges with the editor below */
QWidget#docStrip, QTabWidget#codeTabs > QTabBar {{ background: {u['panel']}; }}
QWidget#docStrip {{ border-bottom: 1px solid {u['border']}; }}
QTabWidget#codeTabs::pane {{ border: none; border-top: 1px solid {u['border']}; top: -1px; }}
QTabWidget#codeTabs::tab-bar {{ left: 8px; }}
QTabBar#docTabs::tab {{ background: transparent; color: {u['text_dim']}; border: 1px solid transparent;
                       border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px;
                       padding: 5px 12px 5px 10px; margin: 4px 2px 0 0; }}
QTabBar#docTabs::tab:hover {{ background: {u['panel_alt']}; color: {u['text']}; }}
QTabBar#docTabs::tab:selected {{ background: {u['window']}; color: {u['text']}; border-color: {u['border']};
                                border-top: 2px solid {u['accent']}; margin-bottom: -1px; }}
QTabWidget#codeTabs QTabBar#docTabs::tab:selected {{ background: {u['code_bg']}; }}

/* bottom panel: tabs with icon, counter and collapse button */
QTabWidget#panelTabs::pane {{ border: none; border-top: 1px solid {u['border']}; background: {u['panel']}; }}
QTabWidget#panelTabs > QTabBar {{ background: {u['window']}; }}
QTabBar#panelTabs::tab {{ padding: 6px 10px 5px 8px; margin: 0 4px 0 0; border-bottom: 2px solid transparent;
                         color: {u['text_dim']}; font-weight: 500; }}
QTabBar#panelTabs::tab:hover {{ color: {u['text']}; }}
QTabBar#panelTabs::tab:selected {{ color: {u['text']}; border-bottom-color: {u['accent']}; }}
QLabel#badge, QLabel#badgeError, QLabel#badgeWarning {{ border-radius: 8px; padding: 0 5px; margin-left: 2px;
                                                       font-size: 7.5pt; font-weight: 700; }}
QLabel#badge {{ background: {u['panel_alt']}; color: {u['text_dim']}; }}
QLabel#badgeError {{ background: {_mix(u['window'], u['error'], 0.22)}; color: {u['error']}; }}
QLabel#badgeWarning {{ background: {_mix(u['window'], u['warning'], 0.22)}; color: {u['warning']}; }}
QWidget#emptyState {{ background: {u['panel']}; }}
QLabel#emptyTitle {{ color: {u['text']}; font-weight: 600; }}

/* segmented selector (Editor | Project explorer) */
QWidget#segmentedFoot {{ background: {u['panel']}; border-top: 1px solid {u['border']}; }}
QFrame#segmented {{ background: {u['window']}; border: 1px solid {u['border']}; border-radius: 7px; }}
QToolButton#segment {{ border: 1px solid transparent; border-radius: 5px; padding: 3px 8px; color: {u['text_dim']};
                       font-weight: 500; }}
QToolButton#segment:hover {{ color: {u['text']}; background: transparent; }}
QToolButton#segment:checked {{ background: {u['panel_alt']}; border-color: {u['border']}; color: {u['text']}; }}

/* command palette */
QFrame#palette {{ background: {u['panel']}; border: 1px solid {u['border']}; border-radius: 12px; }}
QFrame#palette QLineEdit {{ border: none; border-bottom: 1px solid {u['border']}; border-radius: 0; background: transparent;
                           padding: 12px 14px; font-size: 11pt; }}
QFrame#palette QListWidget {{ border: none; background: transparent; padding: 6px; }}
QFrame#palette QListWidget::item {{ padding: 7px 10px; border-radius: 7px; }}
QFrame#palette QListWidget::item:selected {{ background: {u['accent_soft']}; }}
QFrame#palette QListWidget QWidget {{ background: transparent; }}
QLabel#kbd {{ color: {u['text_dim']}; background: {u['panel_alt']}; border: 1px solid {u['border']}; border-radius: 5px;
              padding: 1px 6px; font-size: 8pt; }}

QTreeView, QListView, QTableView, QListWidget, QTreeWidget, QTableWidget {{
    background: {u['panel']}; border: 1px solid {u['border']}; border-radius: 6px; padding: 2px; outline: none; }}
QTreeWidget#flatTree {{ border: none; border-radius: 0; padding: 6px; }}
QListView::item {{ padding: 2px 4px; border-radius: 4px; }}
/* trees and tables: the whole row (arrow + columns) is a single band, no corners per cell */
QTreeView {{ show-decoration-selected: 1; }}
QTreeView::item {{ padding: 2px 4px; border: none; border-radius: 0; }}
QTreeView::item:hover, QTreeView::branch:hover, QListView::item:hover, QTableView::item:hover {{ background: {u['panel_alt']}; }}
QTreeView::item:selected, QTreeView::branch:selected, QListView::item:selected, QTableView::item:selected {{
    background: {u['accent_soft']}; color: {u['text']}; }}
QTreeView::branch {{ background: transparent; border: none; }}
QTreeView::branch:has-children:closed {{ image: url({a}branch-closed.svg); }}
QTreeView::branch:has-children:open {{ image: url({a}branch-open.svg); }}
QTableView {{ gridline-color: transparent; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {u['panel']}; color: {u['text_dim']}; border: none; border-bottom: 1px solid {u['border']};
                       padding: 4px 4px; font-weight: 600; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {u['button_hover']}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {u['text_dim']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}

QGroupBox {{ border: 1px solid {u['border']}; border-radius: 6px; margin-top: 16px; padding: 12px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {u['text_dim']}; font-weight: 600; }}

QSlider {{ background: transparent; }}
QSlider:horizontal {{ min-height: 18px; }}
QSlider:vertical {{ min-width: 18px; }}
QSlider::groove:horizontal {{ height: 4px; background: {u['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {u['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -6px 0; border-radius: 8px;
                             background: {u['window']}; border: 2px solid {u['accent']}; }}
QSlider::groove:vertical {{ width: 4px; background: {u['border']}; border-radius: 2px; }}
QSlider::handle:vertical {{ width: 12px; height: 12px; margin: 0 -6px; border-radius: 8px;
                           background: {u['window']}; border: 2px solid {u['accent']}; }}
QSlider::handle:hover {{ background: {u['accent']}; }}
QSlider::sub-page:horizontal:disabled {{ background: {u['border']}; }}
QSlider::handle:horizontal:disabled {{ border-color: {u['border']}; background: {u['panel']}; }}

QProgressBar {{ background: {u['border']}; border: none; border-radius: 3px; max-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {u['accent']}; border-radius: 3px; }}

QStatusBar {{ background: {u['window']}; border-top: 1px solid {u['border']}; color: {u['text_dim']}; }}
QStatusBar QLabel {{ color: {u['text_dim']}; padding: 0 10px; }}
QStatusBar QLabel#stField {{ border-right: 1px solid {u['border']}; }}
QStatusBar QLabel#stAccent {{ color: {u['accent']}; font-weight: 600; }}
QStatusBar QToolButton#stButton {{ padding: 1px 8px; border-radius: 4px; color: {u['text_dim']}; }}
QStatusBar QToolButton#stButton:hover {{ color: {u['text']}; }}
QStatusBar::item {{ border: none; }}

QToolTip {{ background: {u['text']}; color: {u['window']}; border: none; border-radius: 6px; padding: 5px 8px; }}
QScrollArea {{ border: none; }}
QLabel {{ background: transparent; }}
QLabel#dim, QLabel#fieldLabel, QLabel#counter {{ color: {u['text_dim']}; }}
QLabel#hint {{ color: {u['text_dim']}; font-size: 8pt; }}
QLabel#infoChip {{ color: {u['text_dim']}; background: {u['panel_alt']}; border-radius: 6px; padding: 3px 8px; }}
QWidget#plain, QWidget#section, QWidget#sectionBody, QWidget#toolStrip, QWidget#tabCorner {{ background: transparent; }}
QFrame#toolSep, QFrame#sectionRule {{ background: {u['border']}; border: none; }}
QToolButton#sectionHeader {{ border: none; border-radius: 6px; padding: 3px 2px; color: {u['text']};
                             font-weight: 600; text-align: left; background: transparent; }}
QToolButton#sectionHeader:hover {{ color: {u['accent']}; background: transparent; }}
QToolButton#sectionHeader:checked {{ background: transparent; color: {u['text']}; }}
QToolButton#nudge {{ padding: 0; border-radius: 5px; }}
QToolButton#nudge:hover {{ background: {u['panel_alt']}; }}
QLineEdit#filter {{ background: {u['window']}; border-radius: 5px; padding-left: 6px; }}
QWidget#stripTools QToolBar {{ border: none; padding: 0; background: transparent; }}
QFrame#emptyHint {{ background: transparent; }}
QLabel#emptyHintTitle {{ color: {u['text']}; font-size: 11pt; font-weight: 600; }}
QLabel#emptyHintText {{ color: {u['text_dim']}; }}
QLabel#error {{ color: {u['error']}; }}
QLabel#panelHeader {{ color: {u['text_dim']}; font-weight: 700; letter-spacing: 1px; padding: 0 12px; min-height: 30px; max-height: 30px;
                      background: {u['panel']}; border-bottom: 1px solid {u['border']}; }}
QLabel#hero {{ font-size: 22pt; font-weight: 700; }}
QLabel#sectionTitle {{ font-size: 12pt; font-weight: 600; }}
QWidget#sidePanel, QWidget#panelBody {{ background: {u['panel']}; }}
QFrame#card {{ background: {u['panel']}; border: 1px solid {u['border']}; border-radius: 10px; }}
QPushButton#recent {{ text-align: left; padding: 10px 14px; border-radius: 10px; background: {u['panel']}; }}
QPushButton#recent:hover {{ background: {u['accent_soft']}; border-color: {u['accent_soft']}; }}
QFrame#tile {{ background: {u['panel']}; border: 1px solid {u['border']}; border-radius: 8px; }}
QFrame#tile:hover {{ background: {u['panel_alt']}; border-color: {u['button_hover']}; }}
QFrame#tile[primary="true"] {{ background: {u['accent_soft']}; border-color: {u['accent']}; }}
QFrame#tile QLabel#tileTitle {{ font-weight: 600; color: {u['text']}; }}
QFrame#tile QLabel#tileText {{ color: {u['text_dim']}; }}
QFrame#tile QLabel#kbd, QFrame#card QLabel#kbd {{ background: {u['window']}; }}
QPushButton#link {{ background: transparent; border: none; color: {u['accent']}; padding: 2px 4px; }}
QPushButton#link:hover {{ text-decoration: underline; background: transparent; }}

/* code pages: context bar, file tree, controller card */
QWidget#crumbBar {{ background: {u['code_bg']}; border-bottom: 1px solid {u['border']}; }}
QLabel#crumb {{ color: {u['text']}; padding: 2px 0; }}
QToolButton#chipButton {{ border: 1px solid {u['border']}; border-radius: 5px; padding: 2px 8px; color: {u['text']};
                          background: {u['panel']}; text-align: left; }}
QToolButton#chipButton:hover {{ background: {u['panel_alt']}; border-color: {u['button_hover']}; }}
QPushButton#compactPrimary {{ padding: 2px 10px; }}
QTreeWidget#outline {{ border: none; border-radius: 0; background: transparent; padding: 2px 0; }}
QTreeWidget#outline::item {{ padding: 2px 2px; }}
QToolButton#tabClose {{ padding: 0; border: none; border-radius: 5px; }}
QToolButton#tabClose:hover {{ background: {u['button_hover']}; }}
QPushButton#danger {{ color: {u['error']}; }}
QPushButton#danger:hover {{ border-color: {u['error']}; }}
QPushButton#danger:disabled {{ color: {u['text_dim']}; }}
QLabel#previewFrame {{ background: {u['code_bg']}; border: 1px solid {u['border']}; border-radius: 6px; }}
QFrame#ctrlCard {{ background: {u['window']}; border: 1px solid {u['border']}; border-radius: 6px; }}
QFrame#ctrlCard QLabel#cardTitle {{ font-weight: 700; color: {u['text']}; }}
"""
