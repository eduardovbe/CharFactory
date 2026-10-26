"""Editor de paletas: a tira com as paletas do personagem, grade 16×16, prévia com poses, ferramentas
de cor, variante por matiz/saturação/brilho e as 12 paletas por faixas de cor, numa janela só."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter,
                               QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ...formats import sff
from ...tools import edit, ops, render
from ...tools import palettes12 as P
from ..canvas import SpriteCanvas, pil_to_qimage
from ..context import AppContext, _snapshot
from ..icons import icon
from ..widgets import form_row, hint_label
from ...i18n import tr

HIGHLIGHT = (255, 0, 255)     # cor que pisca na prévia nos índices selecionados


def _thumb(img, size: QSize) -> QPixmap:
    pm = QPixmap.fromImage(pil_to_qimage(img))
    return pm.scaled(size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)


def edit_colors_live(parent, palette, indices: list[int], title: str, redraw) -> tuple | None:
    """Escolhe uma cor para os índices mostrando cada tentativa na hora: a paleta muda enquanto o
    diálogo está aberto e redraw() repinta. No fim as cores antigas voltam (quem chamou grava a
    escolhida pelo caminho normal, com histórico); devolve (r, g, b) ou None se cancelou."""
    before = list(palette.colors)
    cur = before[indices[0]] if indices[0] < len(before) else (0, 0, 0)
    dlg = QColorDialog(QColor(*cur[:3]), parent)
    dlg.setWindowTitle(title)
    dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)   # o nativo não avisa a cada cor

    def show(c: QColor) -> None:
        if not c.isValid():
            return
        cols = list(before) + [(0, 0, 0)] * max(0, max(indices) + 1 - len(before))
        for i in indices:
            cols[i] = (c.red(), c.green(), c.blue())
        palette.colors = cols
        redraw()

    dlg.currentColorChanged.connect(show)
    ok = dlg.exec()
    c = dlg.selectedColor() if ok else None
    dlg.deleteLater()
    palette.colors = before
    redraw()
    return (c.red(), c.green(), c.blue()) if c is not None and c.isValid() else None


class PaletteGrid(QWidget):
    """16×16 células; clique seleciona, Shift+clique estende, duplo clique pede a edição."""
    selectionChanged = Signal(int, int)
    editRequested = Signal(int)

    def __init__(self):
        super().__init__()
        self.colors: list[tuple] = []
        self.sel = (0, 0)
        self.setMinimumSize(192, 192)
        self.setMouseTracking(True)
        sp = self.sizePolicy()
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 — a grade é sempre quadrada
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return w

    def mouseMoveEvent(self, e):  # noqa: N802 — índice e cor sob o ponteiro
        i = self._index(e.position())
        if i < len(self.colors):
            r, g, b = self.colors[i][:3]
            tip = tr("Index {0}  ·  #{1:02x}{2:02x}{3:02x}  ({4}, {5}, {6})").format(i, r, g, b, r, g, b) + (tr("  ·  transparent") if i == 0 else "")
        else:
            tip = tr("Index {0}  ·  empty").format(i)
        if tip != self.toolTip():
            self.setToolTip(tip)

    def set_colors(self, colors) -> None:
        colors = list(colors)
        if colors != self.colors:       # sprites da mesma paleta não repintam a grade
            self.colors = colors
            self.update()

    def _cell(self) -> float:
        return min(self.width(), self.height()) / 16

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        u = self.theme.ui if getattr(self, "theme", None) else {}
        empty = QColor(u.get("border", "#3f3f46"))
        ring = QColor(u.get("accent", "#6366f1"))
        s = self._cell()
        a, b = sorted(self.sel)
        for i in range(256):
            r = QRectF((i % 16) * s, (i // 16) * s, s, s).adjusted(1.5, 1.5, -1.5, -1.5)
            if i < len(self.colors):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(*self.colors[i]))
            else:
                p.setPen(QPen(empty, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, 3, 3)
            if i == 0:  # transparente
                p.setPen(QPen(QColor(u.get("text_dim", "#a1a1aa")), 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 2, 2)
            if a <= i <= b:
                p.setPen(QPen(ring, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-0.5, -0.5, 0.5, 0.5), 3.5, 3.5)
        p.end()

    def _index(self, pos) -> int:
        s = self._cell()
        return max(0, min(255, int(pos.y() // s) * 16 + int(pos.x() // s)))

    def mousePressEvent(self, e):
        i = self._index(e.position())
        self.sel = (self.sel[0], i) if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else (i, i)
        self.selectionChanged.emit(*sorted(self.sel))
        self.update()

    def mouseDoubleClickEvent(self, e):
        self.editRequested.emit(self._index(e.position()))

    def select(self, a: int, b: int | None = None) -> None:
        self.sel = (a, a if b is None else b)
        self.selectionChanged.emit(*sorted(self.sel))
        self.update()


def _empty_icon(w: int, h: int, color: str) -> QIcon:
    """Lugar de paleta vazio na tira: contorno tracejado com um +."""
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.DashLine))
    p.drawRoundedRect(QRectF(3, 3, w - 6, h - 6), 6, 6)
    p.drawLine(w // 2 - 8, h // 2, w // 2 + 8, h // 2)
    p.drawLine(w // 2, h // 2 - 8, w // 2, h // 2 + 8)
    p.end()
    return QIcon(pm)


def _swatch(colors: list, n: int = 8, w: int = 72, h: int = 14) -> QIcon:
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    take = colors[:: max(1, len(colors) // n)][:n] or colors[:1]
    for k, c in enumerate(take):
        p.fillRect(round(k * w / len(take)), 0, round(w / len(take)) + 1, h, QColor(*c[:3]))
    p.end()
    return QIcon(pm)


class PaletteEditor(QWidget):
    """Tudo das paletas numa janela só: a tira com as paletas do personagem (as cores que o
    jogador escolhe na seleção), a grade da escolhida, a prévia com as poses e três abas —
    Cores (ferramentas), Nova paleta (variante por matiz/saturação/brilho) e 12 paletas (gerar
    por faixas de cor e ajustar uma faixa numa paleta). Tudo muda na hora e entra no Desfazer."""
    TAB_COLORS, TAB_NEW, TAB_SET = 0, 1, 2
    THUMB = QSize(64, 80)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._slot: int | None = None
        self._want_slot = None          # paleta a mostrar na próxima recarga (a recém-criada)
        self._blink = False
        self._shown_key = None
        self._start = None              # paletas e sprites como estavam ao abrir o editor
        self._touched = False
        self._light = False             # a mudança veio do ajuste de faixa: só repinta, sem recarregar
        self._pending = False           # recarga já agendada (várias mudanças viram uma recarga)
        self.rngs: list[dict] = []      # faixas de cor da paleta 1
        self.adj: dict[int, dict] = {}  # paleta -> faixa -> (matiz, sat, brilho), desta sessão
        self.adj_origin: dict[int, list] = {}   # paleta -> cores antes do ajuste por faixa
        self._labels: dict[int, str] = {}         # paleta -> tema/harmonia com que foi gerada
        self.poses: list[tuple] = []

        # ------------------------------------------------ topo: desfazer e a tira de paletas
        # desfazer aqui dentro: o editor abre numa janela modal, aonde o Ctrl+Z da janela principal
        # não chega, e cada ferramenta (tons de cinza, inverter…) muda a paleta na hora
        self.undo_btn = QPushButton(icon("undo"), tr("Undo"))
        self.undo_btn.setToolTip(tr("Undo the last palette change (Ctrl+Z)"))
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = QPushButton(icon("redo"), tr("Redo"))
        self.redo_btn.setToolTip(tr("Redo (Ctrl+Y)"))
        self.redo_btn.clicked.connect(self.redo)
        self.revert_btn = QPushButton(tr("Back to how it was"))
        self.revert_btn.setToolTip(tr("Puts back all palettes as they were when this editor opened "
                                      "(this can be undone too)"))
        self.revert_btn.clicked.connect(self.revert)
        self.delete_btn = QPushButton(icon("delete", "error"), tr("Delete palette"))
        self.delete_btn.setToolTip(tr("Deletes the chosen palette (palette 1 is the character's own and stays)"))
        self.delete_btn.clicked.connect(self.delete_palette)
        for seq, fn in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo),
                        (QKeySequence("Ctrl+Y"), self.redo)):
            sc = QShortcut(seq, self, fn)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("<b>Character palettes</b> · the colors the player picks on the select screen")))
        top.addStretch()
        for w in (self.undo_btn, self.redo_btn, self.revert_btn, self.delete_btn):
            top.addWidget(w)
        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.ViewMode.IconMode)
        self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(self.THUMB)
        # cada paleta numa célula fixa: o nome do tema (2ª linha) não invade a vizinha
        self.strip.setGridSize(QSize(self.THUMB.width() + 26, self.THUMB.height() + 40))
        self.strip.setWordWrap(True)
        self.strip.setFixedHeight(self.THUMB.height() + 62)
        self.strip.setMovement(QListWidget.Movement.Static)
        self.strip.setSpacing(2)
        self.strip.itemClicked.connect(self._strip_clicked)

        # ------------------------------------------------ grade
        self.grid = PaletteGrid()
        self.grid.editRequested.connect(self.edit_color)
        self.grid.selectionChanged.connect(self.on_sel)
        self.grid_title = QLabel()
        self.sel_label = QLabel(tr("Selection: 0"))
        self.sel_label.setObjectName("dim")
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(self.grid_title)
        lv.addWidget(self.grid, 1)
        lv.addWidget(self.sel_label)
        lv.addWidget(hint_label(tr("Click picks a color, Shift+click marks a range and double-click edits it "
                                   "(the preview follows while you choose).")))

        # ------------------------------------------------ prévia
        self.preview = SpriteCanvas()
        self.preview.show_axis = False
        self.preview.center_image = True
        self.preview.pick_pixel = True
        self.preview.pixelPicked.connect(self.pick_from_preview)
        self.preview.setToolTip(tr("Click a part of the sprite to select its color in the grid"))
        self.preview_sprite = QComboBox()
        self.preview_sprite.currentIndexChanged.connect(self.show_preview)
        self.poses_row = QHBoxLayout()
        self.poses_row.setSpacing(4)
        self.pose_buttons: list[QToolButton] = []
        self.highlight = QCheckBox(tr("Blink the selected colors"))
        self.highlight.setToolTip(tr("Shows where the colors selected in the grid (or the band picked in "
                                     "12 palettes) appear on the sprite"))
        self.highlight.toggled.connect(self._highlight_toggled)
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(450)
        self._blink_timer.timeout.connect(self._blink_step)
        self.preview_note = QLabel()
        self.preview_note.setObjectName("infoChip")
        self.preview_note.setWordWrap(True)
        self.preview_note.hide()
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.addLayout(form_row(tr("Preview"), self.preview_sprite))
        mv.addWidget(self.preview, 1)
        mv.addLayout(self.poses_row)
        mv.addWidget(self.preview_note)
        mv.addWidget(self.highlight)
        mv.addWidget(hint_label(tr("Click the sprite to find its color in the grid.")))

        # ------------------------------------------------ abas
        self.tabs = QTabWidget()
        self.tabs.addTab(self._colors_tab(), tr("Colors"))
        self.tabs.addTab(self._new_tab(), tr("New palette"))
        self.tabs.addTab(self._set_tab(), tr("12 palettes"))
        self.tabs.currentChanged.connect(lambda _: (self._load_adjust(), self.show_preview()))

        split = QSplitter()
        for w in (left, mid, self.tabs):
            split.addWidget(w)
        split.setSizes([380, 440, 360])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        # aviso quando o .def não deixa escolher alguma paleta no jogo (botões, pal.defaults)
        self.map_note = QLabel()
        self.map_note.setObjectName("infoChip")
        self.map_note.setWordWrap(True)
        self.map_fix = QPushButton(tr("Fix in the .def"))
        self.map_fix.setToolTip(tr("Buttons x, y, z, a, b, c on palettes 1 to 6 (Start + button = 7 to 12) "
                                   "and pal.defaults with the palettes the character has. Ctrl+Z undoes."))
        self.map_fix.clicked.connect(lambda: self.fix_def())
        self.map_row = QWidget()
        mr = QHBoxLayout(self.map_row)
        mr.setContentsMargins(0, 0, 0, 0)
        mr.addWidget(self.map_note, 1)
        mr.addWidget(self.map_fix)
        self.map_row.hide()
        lay.addLayout(top)
        lay.addWidget(self.strip)
        lay.addWidget(self.map_row)
        lay.addWidget(split, 1)

        # métodos e não lambdas: assim o Qt desliga os sinais quando o diálogo do editor é destruído
        ctx.characterChanged.connect(self.load)
        ctx.themeChanged.connect(self._theme_changed)
        ctx.modelChanged.connect(self._model_changed)
        self.preview.set_theme(ctx.theme)

    # ------------------------------------------------------------ abas

    @staticmethod
    def _scroll(content: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        # só rola na vertical: o conteúdo acompanha a largura da aba e os textos quebram linha
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(content)
        return sa

    def _colors_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        groups = [
            (tr("Selected colors"), [
                (tr("Edit the selected color…"), lambda: self.edit_color(self.selection()[0])),
                (tr("Gradient on the selection…"), self.gradient),
                (tr("RGB adjustment on the selection…"), self.rgb_adjust)]),
            (tr("Organize"), [
                (tr("Sort by tones"), self.sort), (tr("Clear repeated colors"), self.dedupe),
                (tr("Move selection ← (with remap)"), lambda: self.shift(-1)),
                (tr("Move selection → (with remap)"), lambda: self.shift(1)),
                (tr("Optimize (clear unused colors)"), self.optimize)]),
            (tr("Files"), [
                (tr("Import palette (.act or image)…"), self.import_act), (tr("Export .act…"), self.export_act),
                (tr("Generate images of all palettes…"), self.gen_images),
                (tr("Fit an image to this palette…"), self.adapt_image)]),
        ]
        for title, items in groups:
            box = QGroupBox(title)
            bv = QVBoxLayout(box)
            for text, fn in items:
                b = QPushButton(text)
                b.clicked.connect(fn)
                bv.addWidget(b)
            v.addWidget(box)
            if title == tr("Selected colors"):
                v.addWidget(self._effects_box())    # os efeitos logo depois das cores, sem rolar
        v.addWidget(hint_label(tr("Tools act on the colors selected in the grid; with one color selected, "
                                  "on the whole palette. Ctrl+Z undoes each one.")))
        v.addStretch()
        return self._scroll(w)

    def _effects_box(self) -> QGroupBox:
        """Efeitos de cor, em duas linhas: uma cor vira outra (só os
        tons dela mudam) e um efeito da lista. Cada aplicação é um passo do Ctrl+Z."""
        names = {"red": tr("Red"), "orange": tr("Orange"), "yellow": tr("Yellow"), "green": tr("Green"),
                 "cyan": tr("Cyan"), "blue": tr("Blue"), "purple": tr("Purple"), "pink": tr("Pink")}
        box = QGroupBox(tr("Color effects"))
        g = QGridLayout(box)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        self.fx_src, self.fx_dst = QComboBox(), QComboBox()
        for key, label in names.items():
            self.fx_src.addItem(label, key)
            self.fx_dst.addItem(label, key)
        self.fx_src.setCurrentIndex(self.fx_src.findData("blue"))
        self.fx_dst.setCurrentIndex(self.fx_dst.findData("red"))
        self.fx_src.setToolTip(tr("Only the tones of this color change; skin, grays and other colors stay"))
        go = QPushButton(tr("Apply"))
        go.clicked.connect(lambda: self.recolor(self.fx_src.currentData(), self.fx_dst.currentData()))
        g.addWidget(QLabel(tr("Turn")), 0, 0)
        g.addWidget(self.fx_src, 0, 1)
        g.addWidget(QLabel("→"), 0, 2)
        g.addWidget(self.fx_dst, 0, 3)
        g.addWidget(go, 0, 4)
        self.fx_kind = QComboBox()
        for text, kind in [(tr("Grayscale"), "grayscale"), (tr("Invert"), "invert"), (tr("Sepia"), "sepia"),
                           (tr("Lighter"), "lighter"), (tr("Darker"), "darker"),
                           (tr("More saturated"), "saturate"), (tr("Less saturated"), "desaturate"),
                           (tr("Swap red ⇄ green"), "swap_rg"), (tr("Swap red ⇄ blue"), "swap_rb"),
                           (tr("Swap green ⇄ blue"), "swap_gb"), (tr("Rotate red → green → blue"), "rotate_rgb"),
                           (tr("Rotate red → blue → green"), "rotate_rbg")]:
            self.fx_kind.addItem(text, kind)
        go2 = QPushButton(tr("Apply"))
        go2.clicked.connect(lambda: self.effect(self.fx_kind.currentData()))
        g.addWidget(QLabel(tr("Effect")), 1, 0)
        g.addWidget(self.fx_kind, 1, 1, 1, 3)
        g.addWidget(go2, 1, 4)
        return box

    def _new_tab(self) -> QWidget:
        """Variante da paleta escolhida; a prévia mostra o resultado antes de gravar."""
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(hint_label(tr("Makes a new palette from the chosen one by turning the hue and changing "
                               "saturation and brightness. The preview shows it before saving.")))
        self.hue, hue_row = self._slider(-180, 180, 0, "°", self.show_preview)
        self.sat, sat_row = self._slider(0, 300, 100, "%", self.show_preview)
        self.bri, bri_row = self._slider(0, 300, 100, "%", self.show_preview)
        self.target = QSpinBox()
        self.target.setRange(1, 12)
        self.target.valueChanged.connect(self._target_note)
        self.target_note = QLabel()
        self.target_note.setObjectName("dim")
        f.addRow(tr("Hue"), hue_row)
        f.addRow(tr("Saturation"), sat_row)
        f.addRow(tr("Brightness"), bri_row)
        f.addRow(tr("Save to slot"), self.target)
        f.addRow(self.target_note)
        keep = QLabel(tr("Colors selected in the grid are kept (e.g. skin)."))
        keep.setObjectName("dim")
        keep.setWordWrap(True)
        f.addRow(keep)
        reset = QPushButton(tr("Reset"))
        reset.clicked.connect(self.reset_variant)
        b = QPushButton(tr("Create variant"))
        b.clicked.connect(self.make_variant)
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addWidget(b, 1)
        f.addRow(row)
        return self._scroll(w)

    def _set_tab(self) -> QWidget:
        """As 12 paletas de uma vez: faixas de cor da paleta 1, esquema e ajuste por faixa."""
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(hint_label(tr("Palette 1 split in color bands (skin, hair, clothes…). Lock what stays "
                                  "the same in all palettes and generate palettes 2 to 12 with a scheme.")))
        self.bands = QTreeWidget()
        self.bands.setHeaderLabels([tr("Band"), tr("Colors"), tr("Locked")])
        self.bands.setRootIsDecorated(False)
        self.bands.setIconSize(QSize(72, 14))
        self.bands.itemChanged.connect(self._lock_changed)
        self.bands.currentItemChanged.connect(lambda *_: (self._load_adjust(), self.show_preview()))
        v.addWidget(self.bands, 1)
        self.scheme = QComboBox()
        self.scheme.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.scheme.setMinimumContentsLength(12)     # o texto longo do esquema não alarga a aba
        for key, text in P.SCHEMES.items():
            self.scheme.addItem(text, key)
            self.scheme.setItemData(self.scheme.count() - 1, text, Qt.ItemDataRole.ToolTipRole)
        v.addLayout(form_row(tr("Scheme"), self.scheme))
        # onde gerar: uma faixa de paletas, e só nos lugares vazios se quiser guardar as feitas
        self.gen_from = QSpinBox()
        self.gen_from.setRange(2, 12)
        self.gen_to = QSpinBox()
        self.gen_to.setRange(2, 12)
        self.gen_to.setValue(12)
        self.gen_from.valueChanged.connect(lambda v: self.gen_to.setValue(max(v, self.gen_to.value())))
        self.gen_to.valueChanged.connect(lambda v: self.gen_from.setValue(min(v, self.gen_from.value())))
        rng = QHBoxLayout()
        rng.addWidget(self.gen_from)
        rng.addWidget(QLabel(tr("to")))
        rng.addWidget(self.gen_to)
        rng.addStretch()
        v.addLayout(form_row(tr("Palettes"), rng))
        self.gen_empty = QCheckBox(tr("Only empty slots (keep the palettes already made)"))
        v.addWidget(self.gen_empty)
        self.gen_def = QCheckBox(tr("Also fix the .def so they can be picked in the game"))
        self.gen_def.setToolTip(tr("[Palette Keymap] (buttons x to c) and pal.defaults, only if something "
                                   "was missing"))
        self.gen_def.setChecked(True)
        v.addWidget(self.gen_def)
        gen = QPushButton(tr("Generate palettes"))
        gen.setToolTip(tr("Makes the chosen palettes from palette 1 with the scheme (locked bands stay "
                          "the same). Ctrl+Z undoes."))
        gen.clicked.connect(self.generate)
        v.addWidget(gen)
        self.adjust_box = QGroupBox(tr("Adjust one band in one palette"))
        f = QFormLayout(self.adjust_box)
        self.adjust_title = QLabel()
        self.adjust_title.setWordWrap(True)
        f.addRow(self.adjust_title)
        self.dh, dh_row = self._slider(-180, 180, 0, "°", self._adjust_changed)
        self.sm, sm_row = self._slider(0, 300, 100, "%", self._adjust_changed)
        self.bm, bm_row = self._slider(0, 300, 100, "%", self._adjust_changed)
        f.addRow(tr("Hue"), dh_row)
        f.addRow(tr("Saturation"), sm_row)
        f.addRow(tr("Brightness"), bm_row)
        f.addRow(hint_label(tr("Pick a band above and a palette on the strip; the change shows at once.")))
        v.addWidget(self.adjust_box)
        return self._scroll(w)

    def _slider(self, lo: int, hi: int, value: int, suffix: str, on_change):
        """Slider com campo numérico ao lado; mexer em qualquer um chama on_change na hora."""
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(value)
        sb = QSpinBox()
        sb.setRange(lo, hi)
        sb.setSuffix(suffix)
        sb.setValue(value)
        sl.valueChanged.connect(sb.setValue)
        sb.valueChanged.connect(sl.setValue)
        sb.valueChanged.connect(on_change)
        row = QHBoxLayout()
        row.addWidget(sl, 1)
        row.addWidget(sb)
        return sb, row

    # ------------------------------------------------------------ modelo e histórico

    def _theme_changed(self) -> None:
        self.preview.set_theme(self.ctx.theme)
        self._refresh_strip()

    def _model_changed(self, part: str) -> None:
        if part == "def":
            self._map_note()
        if part == "sprites":
            if self._light:              # ajuste de faixa: as paletas são as mesmas, só as cores mudaram
                self._light = False
                self.refresh()
                self._refresh_strip(only=self._slot)
            elif not self._pending:
                self._pending = True
                QTimer.singleShot(0, self, lambda: self.load(keep=True))
        self._history_buttons()

    def _history_buttons(self) -> None:
        """Desfazer/refazer só valem para as paletas/sprites: um passo de outra parte (animação
        editada antes de abrir) não é desfeito daqui sem a pessoa ver."""
        h = self.ctx.undo_stack()
        self.undo_btn.setEnabled(bool(h and h.undo and self._ours(h.undo[-1])))
        self.redo_btn.setEnabled(bool(h and h.redo and self._ours(h.redo[-1])))
        self.revert_btn.setEnabled(self._touched and self._start is not None)
        self.delete_btn.setEnabled(self._slot is not None and not self.is_own() and self._slot != 1)

    def _forget_adjust(self) -> None:
        """Depois de desfazer, o próximo ajuste de faixa parte das cores como estão agora (e os
        nomes dos temas na tira já não valem)."""
        self.adj.clear()
        self.adj_origin.clear()
        self._labels.clear()

    LABEL = "palettes"                   # rótulo dos passos do editor que mexem no .def junto

    @classmethod
    def _ours(cls, entry) -> bool:
        """Passo deste editor: paletas/sprites, ou o .def acertado por ele."""
        part, _state, label = entry
        return part == "sprites" or (part == "def" and str(label).startswith(cls.LABEL))

    def _step(self, op: str) -> bool:
        """Desfaz/refaz um passo; os que têm o mesmo rótulo (gerar + acertar o .def) vão juntos."""
        h = self.ctx.undo_stack()
        stack = h.undo if op == "undo" else h.redo
        label = stack[-1][2] if stack else ""
        if not getattr(self.ctx, op)():
            return False
        while label and stack and stack[-1][2] == label and getattr(self.ctx, op)():
            pass
        return True

    def undo(self) -> None:
        if self.undo_btn.isEnabled() and self._step("undo"):
            self._touched = True
            self._forget_adjust()
            self.ctx.status.emit(tr("Palette change undone"))
        self._history_buttons()

    def redo(self) -> None:
        if self.redo_btn.isEnabled() and self._step("redo"):
            self._touched = True
            self._forget_adjust()
            self.ctx.status.emit(tr("Palette change redone"))
        self._history_buttons()

    def revert(self) -> None:
        """Volta tudo como estava ao abrir; entra no histórico, então Ctrl+Z refaz o que havia."""
        if self._start is None or not self.char:
            return
        self._cut()
        label = self._new_label()
        self.char.__dict__["sprites"] = _snapshot("sprites", self._start)
        self.ctx.changed("sprites", label)
        start_def = getattr(self, "_start_def", None)
        if start_def is not None and start_def.to_text() != self.char.definition.to_text():
            self.char.definition = _snapshot("def", start_def)       # o .def que "Gerar" acertou também volta
            self.ctx.changed("def", label)
        self._touched = False
        self._forget_adjust()
        self._history_buttons()
        self.ctx.status.emit(tr("Palettes back to how they were when the editor opened (Ctrl+Z undoes)"))

    @property
    def char(self):
        return self.ctx.char

    def char_palettes(self) -> list:
        return [p for p in self.char.sprites.palettes if p.group == 1] if self.char else []

    def load(self, keep: bool = False) -> None:
        self._pending = False
        if not self.char:
            return
        if not keep:
            self._start = _snapshot("sprites", self.char.sprites)
            self._start_def = _snapshot("def", self.char.definition)
            self._touched = False
            self._slot = None
            try:
                self.poses = P.pose_sprites(self.char)
            except Exception:  # noqa: BLE001 — personagem sem AIR: a prévia ainda funciona
                self.poses = []
            self._load_bands()
        existing = sorted(p.item for p in self.char_palettes())
        if self._want_slot is not None:
            self._slot, self._want_slot = self._want_slot, None
        if self.current_palette() is None:       # apagada (ou nunca escolhida): a primeira 1,x
            self._slot = existing[0] if existing else None
        self._fill_preview(self.preview_sprite.currentData() if keep else None)
        if not keep:
            self._build_poses()
        self._own_mode()
        if not (keep and self.variant_active()):
            free = [n for n in range(2, 13) if n not in existing]
            self.target.setValue(free[0] if free else 12)
        self._refresh_strip()
        self._target_note()
        self._history_buttons()
        self._load_adjust()
        self._map_note()
        self.refresh()

    def _map_note(self) -> None:
        """Mostra o que no .def impede escolher as paletas no jogo, com o botão de corrigir."""
        try:
            problems = P.def_mapping(self.char)["problems"] if self.char else []
        except Exception:  # noqa: BLE001 — .def sem [Info] ou com erro: sem aviso
            problems = []
        self.map_row.setVisible(bool(problems))
        if problems:
            self.map_note.setText(tr("<b>In the game:</b> ") + "; ".join(problems) + ".")

    def fix_def(self, label: str = "") -> list[str]:
        """Acerta botões e pal.defaults no .def; um passo do Desfazer (junto do `label`)."""
        changes = P.fix_mapping(self.char)
        if changes:
            self._touched = True
            self.ctx.changed("def", label or self._new_label())
            self.ctx.status.emit(tr(".def: {0} · Ctrl+Z undoes").format("; ".join(changes)))
        self._map_note()
        self._history_buttons()
        return changes

    def _new_label(self) -> str:
        self._label_n = getattr(self, "_label_n", 0) + 1
        return f"{self.LABEL}-{id(self)}-{self._label_n}"

    def current_slot(self):
        return self._slot

    def is_own(self) -> bool:
        """Editando a paleta própria de um sprite (3,0 de um efeito…), não uma 1,x do personagem."""
        return isinstance(self._slot, tuple)

    def _key(self) -> tuple[int, int]:
        return self._slot if self.is_own() else (1, self._slot)

    def current_palette(self):
        if not self.char or self._slot is None:
            return None
        k = self.char.sprites.find_palette(*self._key())
        return self.char.sprites.palettes[k] if k >= 0 else None

    def _fill_preview(self, keep_key=None) -> None:
        """Sprites da prévia: os pintados com a paleta que está na grade. Com uma 1,x, todos os da
        paleta do personagem (um efeito com paleta própria não muda quando a 1,N muda, e a prévia
        parecia não responder); com uma paleta própria, os que usam ela. Refeita sempre: um
        sprite apagado deixava a prévia apontando para ele ("sprite 9000,1 não existe")."""
        s = self.char.sprites
        if self.is_own():
            k = s.find_palette(*self._slot)
            use = {k}
        else:
            use = {i for i, p in enumerate(s.palettes) if p.group == 1}
        self.preview_sprite.blockSignals(True)
        self.preview_sprite.clear()
        for spr in [x for x in s.sprites if not x.is_truecolor and x.palette_index in use][:600]:
            self.preview_sprite.addItem(f"{spr.group},{spr.item}", (spr.group, spr.item))
        first = (self.poses[0][0], self.poses[0][1]) if self.poses and not self.is_own() else None
        i = self._find_sprite(keep_key) if keep_key is not None else -1
        if i < 0 and first is not None:
            i = self._find_sprite(first)
        self.preview_sprite.setCurrentIndex(max(i, 0))
        self.preview_sprite.blockSignals(False)

    def _find_sprite(self, key) -> int:
        """Linha do sprite (grupo, item) na lista da prévia. O findData do Qt não acha tuplas do
        Python (dava -1 sempre e a prévia caía no primeiro sprite)."""
        key = tuple(key) if key else None
        return next((k for k in range(self.preview_sprite.count())
                     if tuple(self.preview_sprite.itemData(k)) == key), -1)

    def _own_mode(self) -> None:
        """Paleta própria: Nova paleta e 12 paletas são das paletas do personagem (ficam
        desligadas), as poses do char não usam essa paleta (somem) e nada fica marcado na tira."""
        own = self.is_own()
        for t in (self.TAB_NEW, self.TAB_SET):
            self.tabs.setTabEnabled(t, not own)
        if own and self.tabs.currentIndex() != self.TAB_COLORS:
            self.tabs.setCurrentIndex(self.TAB_COLORS)
        for b in self.pose_buttons:
            b.setVisible(not own)

    def open_on(self, sprite=None, slot=None) -> None:
        """Abre no sprite que estava na tela e na paleta dele: uma 1,N (número) ou a própria do
        sprite ((grupo, item)). Sem isso o editor abria numa pose e numa paleta qualquer."""
        if self.char is None:
            return
        if slot is not None and self.char.sprites.find_palette(*(slot if isinstance(slot, tuple) else (1, slot))) >= 0:
            self._slot = slot
        self._fill_preview(tuple(sprite) if sprite else None)
        self._own_mode()
        self._mark_strip()
        self._history_buttons()
        self._load_adjust()
        self.refresh()

    def select_slot(self, slot: int) -> None:
        """Escolhe a paleta da tira; um lugar vazio leva à aba Nova paleta, com ele como destino."""
        if self.char is None:
            return
        if self.char.sprites.find_palette(1, slot) < 0:
            if self.is_own():                    # a aba Nova paleta é das paletas do personagem
                self.select_slot(min(p.item for p in self.char_palettes()))
            self.tabs.setCurrentIndex(self.TAB_NEW)
            self.target.setValue(slot)
            self.ctx.status.emit(tr("Palette {0} is empty: make it here from the chosen one, or with "
                                    "“12 palettes”").format(slot))
            self._mark_strip()
            return
        was_own, self._slot = self.is_own(), slot
        if was_own:                              # volta às paletas do personagem
            self._fill_preview()
            self._own_mode()
        self._mark_strip()
        self._history_buttons()
        self._load_adjust()
        self.refresh()

    def _strip_clicked(self, item) -> None:
        self.select_slot(item.data(Qt.ItemDataRole.UserRole))

    def _strip_key(self):
        """Sprite das miniaturas da tira: a primeira pose (em pé), senão o da prévia."""
        if self.poses:
            return self.poses[0][:2]
        return self.preview_sprite.currentData()

    def _refresh_strip(self, only: int | None = None) -> None:
        if not self.char:
            return
        existing = {p.item: p for p in self.char_palettes()}
        last = max([12, *existing])
        if self.strip.count() != last:
            self.strip.clear()
            for slot in range(1, last + 1):
                it = QListWidgetItem(str(slot))
                it.setData(Qt.ItemDataRole.UserRole, slot)
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.strip.addItem(it)
        key = self._strip_key()
        spr = self.char.sprites.find(*key) if key else None
        dim = (self.ctx.theme.ui.get("text_dim", "#a1a1aa") if self.ctx.theme else "#a1a1aa")
        for row in range(self.strip.count()):
            it = self.strip.item(row)
            slot = it.data(Qt.ItemDataRole.UserRole)
            if only is not None and slot != only:
                continue
            p = existing.get(slot)
            if p is None:
                it.setIcon(_empty_icon(self.THUMB.width(), self.THUMB.height(), dim))
                it.setText(tr("{0} · empty").format(slot))
                it.setToolTip(tr("Palette {0} does not exist yet: click to make it").format(slot))
            else:
                img = self.char.sprites.image(spr, p).convert("RGBA") if spr is not None else None
                it.setIcon(QIcon(_thumb(img, self.THUMB)) if img is not None else QIcon())
                label = self._labels.get(slot)
                it.setText(f"{slot}\n{label}" if label else str(slot))
                it.setToolTip(tr("Palette {0} · {1} colors").format(slot, len(p.colors)))
        self._mark_strip()

    def _mark_strip(self) -> None:
        self.strip.blockSignals(True)
        self.strip.clearSelection()
        self.strip.setCurrentRow(-1)
        for row in range(self.strip.count()):
            if self.strip.item(row).data(Qt.ItemDataRole.UserRole) == self._slot:
                self.strip.setCurrentRow(row)
        self.strip.blockSignals(False)

    def _build_poses(self) -> None:
        for b in self.pose_buttons:
            self.poses_row.removeWidget(b)
            b.deleteLater()
        self.pose_buttons = []
        for g, i, label in self.poses:
            b = QToolButton()
            b.setIconSize(QSize(44, 52))
            b.setToolTip(tr("{0} ({1},{2}) · click to show it in the preview").format(label, g, i))
            b.clicked.connect(lambda _=False, k=(g, i): self._show_pose(k))
            self.poses_row.addWidget(b)
            self.pose_buttons.append(b)
        self.poses_row.addStretch()

    def _show_pose(self, key) -> None:
        i = self._find_sprite(key)
        if i >= 0:
            self.preview_sprite.setCurrentIndex(i)

    def refresh(self, *_):
        p = self.current_palette()
        if p is None:          # a paleta escolhida foi apagada: [-1] mostraria outra sem avisar
            return
        if self.is_own():
            self.grid_title.setText(tr("<b>Own palette {0},{1}</b> of the sprite · {2} colors · edits change only "
                                       "the sprites that use it").format(*self._slot, len(p.colors)))
        else:
            self.grid_title.setText(tr("<b>Palette {0}</b> · {1} colors").format(self._slot, len(p.colors)))
        self.grid_title.setWordWrap(True)
        self.grid.set_colors(p.colors)
        self.show_preview()

    # ------------------------------------------------------------ prévia

    def variant_active(self) -> bool:
        """A prévia mostra a variante só com a aba Nova paleta aberta e os controles fora do neutro."""
        return self.tabs.currentIndex() == self.TAB_NEW and \
            (self.hue.value(), self.sat.value(), self.bri.value()) != (0, 100, 100)

    def variant(self) -> list:
        keep = self.selection() if len(self.selection()) > 1 else []
        return ops.variant_colors(self.current_palette().colors, self.hue.value(), self.sat.value() / 100,
                                  self.bri.value() / 100, keep)

    def _highlighted(self) -> list[int]:
        """Índices que piscam: a faixa escolhida na aba 12 paletas, senão a seleção da grade."""
        j = self._current_band()
        if self.tabs.currentIndex() == self.TAB_SET and j is not None:
            return self.rngs[j]["indices"]
        return self.selection()

    def _colors_for_preview(self) -> list:
        p = self.current_palette()
        colors = self.variant() if self.variant_active() else list(p.colors)
        if self.highlight.isChecked() and self._blink:
            colors = list(colors) + [(0, 0, 0)] * max(0, 256 - len(colors))
            for i in self._highlighted():
                if i > 0:
                    colors[i] = HIGHLIGHT
        return colors

    def show_preview(self, *_):
        """Sprite da prévia com a paleta como está, ou como a variante vai ficar se os controles
        de matiz/saturação/brilho saíram do neutro (nada é gravado até 'Criar variante')."""
        key = self.preview_sprite.currentData()
        p = self.current_palette()
        spr = self.char.sprites.find(*key) if key and p is not None else None
        if spr is None:
            return
        active = self.variant_active()
        if active:
            self.preview_note.setText(tr("Previewing the variant, not saved yet: “Create variant” saves it "
                                         "to palette 1,{0}.").format(self.target.value()))
        self.preview_note.setVisible(active)
        pal = sff.Palette(*self._key(), self._colors_for_preview())
        self.preview.set_image(self.char.sprites.image(spr, pal).convert("RGBA"))
        if key != self._shown_key:       # só reenquadra ao trocar de sprite: mexer na cor não pula o zoom
            self.preview.fit()
            self._shown_key = key
        for b, (g, i, _) in zip(self.pose_buttons, self.poses):
            ps = self.char.sprites.find(g, i)
            if ps is not None:
                b.setIcon(QIcon(_thumb(self.char.sprites.image(ps, pal).convert("RGBA"), b.iconSize())))

    def _highlight_toggled(self, on: bool) -> None:
        self._blink = on
        (self._blink_timer.start if on else self._blink_timer.stop)()
        self.show_preview()

    def _blink_step(self) -> None:
        self._blink = not self._blink
        self.show_preview()

    def _target_note(self, *_) -> None:
        n = self.target.value()
        exists = self.char is not None and self.char.sprites.find_palette(1, n) >= 0
        self.target_note.setText(tr("Replaces palette 1,{0}").format(n) if exists else tr("New palette 1,{0}").format(n))
        if self.variant_active():
            self.show_preview()

    def pick_from_preview(self, x: int, y: int) -> None:
        """Conta-gotas: o índice do pixel clicado vira a seleção da grade."""
        key = self.preview_sprite.currentData()
        spr = self.char.sprites.find(*key) if key else None
        if spr is None or spr.is_truecolor:
            return
        i = self.char.sprites.pixels(spr)[y * spr.width + x]
        self.grid.select(i)
        self.ctx.status.emit(tr("Index {0} selected").format(i) if i else tr("Index 0 is the transparent color"))

    def on_sel(self, a: int, b: int) -> None:
        self.sel_label.setText(tr("Selection: {0}").format(a) if a == b else tr("Selection: {0}–{1}").format(a, b))
        if self.variant_active() or self.highlight.isChecked():
            self.show_preview()          # a seleção muda o que a variante mantém e o que pisca

    def selection(self) -> list[int]:
        a, b = sorted(self.grid.sel)
        return list(range(a, b + 1))

    # ------------------------------------------------------------ 12 paletas por faixas

    def _base(self) -> list:
        k = self.char.sprites.find_palette(1, 1)
        return [tuple(c) for c in self.char.sprites.palettes[k].colors] if k >= 0 else []

    def _load_bands(self) -> None:
        """Faixas de cor da paleta 1; as travas escolhidas antes ficam (pelo nome da faixa)."""
        locked = {r["name"]: r["locked"] for r in self.rngs}
        base = self._base()
        self.rngs = P.ranges(base, P.used_indices(self.char)) if base else []
        for r in self.rngs:
            r["locked"] = locked.get(r["name"], r["locked"])
        self._forget_adjust()
        self.bands.blockSignals(True)
        self.bands.clear()
        for j, r in enumerate(self.rngs):
            it = QTreeWidgetItem([r["name"], str(len(r["indices"])), ""])
            it.setIcon(0, _swatch([base[i] for i in r["indices"]]))
            it.setCheckState(2, Qt.CheckState.Checked if r["locked"] else Qt.CheckState.Unchecked)
            it.setData(0, Qt.ItemDataRole.UserRole, j)
            it.setToolTip(0, tr("indices ") + ", ".join(map(str, r["indices"][:40])))
            self.bands.addTopLevelItem(it)
        self.bands.resizeColumnToContents(0)
        self.bands.blockSignals(False)

    def _lock_changed(self, it, col) -> None:
        if col == 2:
            self.rngs[it.data(0, Qt.ItemDataRole.UserRole)]["locked"] = it.checkState(2) == Qt.CheckState.Checked

    def _current_band(self) -> int | None:
        it = self.bands.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def generate(self) -> None:
        if not self.rngs:
            self.ctx.status.emit(tr("The character does not have palette 1,1"))
            return
        has = lambda n: self.char.sprites.find_palette(1, n) >= 0
        slots = [n for n in range(self.gen_from.value(), self.gen_to.value() + 1)
                 if not (self.gen_empty.isChecked() and has(n))]
        if not slots:
            self.ctx.status.emit(tr("No empty palette between {0} and {1}").format(
                self.gen_from.value(), self.gen_to.value()))
            return
        exists = [n for n in slots if has(n)]
        if exists and QMessageBox.question(
                self, tr("Generate palettes"),
                tr("This replaces the palettes the character already has ({0}). Ctrl+Z undoes it. Continue?")
                .format(", ".join(map(str, exists)))) != QMessageBox.StandardButton.Yes:
            return
        base = self._base()
        labels: dict[int, str] = {}
        # "Surpresa" sorteia de novo a cada clique; os outros esquemas dão sempre o mesmo. As
        # variações do esquema (2 a 12) vão, em ordem, para as paletas escolhidas
        made = P.scheme(self.scheme.currentData(), self.rngs, labels=labels)
        variants = [made[k] for k in sorted(made)]
        names = [labels.get(k) for k in sorted(made)]
        self.adj = {n: variants[i] for i, n in enumerate(slots)}
        self.adj_origin = {n: list(base) for n in self.adj}
        self._labels.update({n: names[i] for i, n in enumerate(slots) if names[i]})
        P.write(self.char, {n: P.adjust(base, self.rngs, a) for n, a in self.adj.items()})
        self._want_slot = slots[0]
        label = self._new_label()
        self._done(label)
        fixed = self.fix_def(label) if self.gen_def.isChecked() else []
        span = f"{slots[0]}" if len(slots) == 1 else f"{slots[0]}–{slots[-1]}"
        self.ctx.status.emit(tr("Palettes {0} generated · Ctrl+Z undoes").format(span)
                             + (tr(" · .def fixed") if fixed else "")
                             + (tr(" · click again for other colors") if self.scheme.currentData() == "sortear" else ""))

    def _load_adjust(self) -> None:
        """Mostra o ajuste da faixa escolhida na paleta escolhida (cada um começa um passo novo
        do Desfazer)."""
        j = self._current_band()
        ok = j is not None and isinstance(self._slot, int) and self._slot != 1
        self.adjust_box.setEnabled(ok)
        if j is None:
            self.adjust_title.setText(tr("Pick a band above."))
            return
        if not ok:
            self.adjust_title.setText(tr("Palette 1 is the original: pick another one on the strip."))
            return
        dh, sm, bm, *_ = self.adj.get(self._slot, {}).get(j, (0, 1.0, 1.0))
        for w, val in ((self.dh, int(dh) if dh <= 180 else int(dh) - 360), (self.sm, round(sm * 100)),
                       (self.bm, round(bm * 100))):
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)
        self.adjust_title.setText(tr("<b>{0}</b> in palette {1} (relative to palette 1)").format(
            self.rngs[j]["name"], self._slot))
        self._cut()

    def _adjust_changed(self, *_) -> None:
        """Ajuste de uma faixa numa paleta: grava na hora; os passos de um arraste viram um só."""
        j, slot = self._current_band(), self._slot
        p = self.current_palette()
        if j is None or not isinstance(slot, int) or slot == 1 or p is None:
            return
        if slot not in self.adj_origin:          # parte das cores que a paleta tem agora
            self.adj_origin[slot] = list(p.colors)
            self.adj[slot] = {}
        prev = self.adj[slot].get(j, ())
        tint = prev[3] if len(prev) > 3 else 0.0     # a tinta do tema nos cinzas fica
        self.adj[slot][j] = (self.dh.value(), self.sm.value() / 100, self.bm.value() / 100, tint)
        new = P.adjust(self._base(), self.rngs, self.adj[slot])
        cols = list(self.adj_origin[slot]) + [(0, 0, 0)] * max(0, len(new) - len(self.adj_origin[slot]))
        for jj in self.adj[slot]:
            for i in self.rngs[jj]["indices"]:
                cols[i] = new[i]
        P.write(self.char, {slot: cols})
        self._touched = True
        self._light = True
        self.ctx.changed("sprites")

    # ------------------------------------------------------------ ações

    def _cut(self) -> None:
        h = self.ctx.undo_stack()
        if h:
            h.cut("sprites")

    def _done(self, label: str = "") -> None:
        self._touched = True
        self._cut()                      # cada ferramenta é um passo do Desfazer
        self.ctx.changed("sprites", label)

    def _redraw_live(self) -> None:
        p = self.current_palette()
        if p is not None:
            self.grid.set_colors(p.colors)
            self.show_preview()

    def edit_color(self, i: int) -> None:
        """Duplo clique numa cor: com uma faixa selecionada que a contém, pinta a faixa toda."""
        p = self.current_palette()
        if p is None:
            return
        sel = self.selection()
        idx = sel if i in sel and len(sel) > 1 else [i]
        title = tr("Color {0}").format(i) if len(idx) == 1 else tr("Colors {0}–{1}").format(idx[0], idx[-1])
        rgb = edit_colors_live(self, p, idx, title, self._redraw_live)
        if rgb is not None:
            self.on_color(idx, rgb)

    def on_color(self, indices, rgb) -> None:
        edit.set_palette_colors(self.char, self._slot, {str(i): list(rgb) for i in indices})
        self._done()

    def delete_palette(self) -> None:
        slot = self._slot
        if slot in (None, 1) or self.is_own():
            return
        edit.delete_palette(self.char, slot)
        self._done()
        self.ctx.status.emit(tr("Palette 1,{0} deleted · Ctrl+Z undoes").format(slot))

    def reset_variant(self) -> None:
        for w, v in ((self.hue, 0), (self.sat, 100), (self.bri, 100)):
            w.setValue(v)

    def make_variant(self) -> None:
        keep = self.selection() if len(self.selection()) > 1 else []
        target = self.target.value()
        ops.make_palette_variant(self.char, target, self.hue.value(), self.sat.value() / 100,
                                 self.bri.value() / 100, base=self._slot, keep_indices=keep)
        self.reset_variant()
        self._want_slot = target         # a recarga mostra a paleta nova, não a base
        self._done()
        self.ctx.status.emit(tr("Palette 1,{0} created").format(target))

    def gradient(self) -> None:
        sel = self.selection()
        if len(sel) < 2:
            self.ctx.status.emit(tr("Select a range in the grid (Shift+click)"))
            return
        a = QColorDialog.getColor(QColor(*self.grid.colors[sel[0]]), self, tr("Start color"))
        b = QColorDialog.getColor(QColor(*self.grid.colors[sel[-1]]), self, tr("End color")) if a.isValid() else None
        if a.isValid() and b and b.isValid():
            edit.palette_gradient(self.char, self._slot, sel[0], sel[-1], a.name(), b.name())
            self._done()

    def effect(self, kind: str) -> None:
        sel = self.selection()
        edit.palette_effect(self.char, self._slot, kind, sel if len(sel) > 1 else None)
        self._done()

    def recolor(self, source: str, target: str) -> None:
        if source == target:
            return
        sel = self.selection()
        r = edit.recolor_family(self.char, self._slot, source, target, sel if len(sel) > 1 else None)
        if not r["changed"]:
            self.ctx.status.emit(tr("No color of that tone in the palette"))
            return
        self.ctx.status.emit(tr("{0} color(s) changed").format(r["changed"]))
        self._done()

    def sort(self) -> None:
        r = edit.sort_palette_by_tones(self.char, self._slot)
        self.ctx.status.emit(tr("Palette sorted; {0} sprites remapped").format(r['sprites_remapped']))
        self._done()

    def dedupe(self) -> None:
        r = edit.clear_duplicate_colors(self.char, self._slot)
        self.ctx.status.emit(tr("{0} repeated color(s) cleared").format(r['cleared']))
        self._done()

    def shift(self, d: int) -> None:
        """Move as cores selecionadas uma posição (as imagens não mudam: os pixels são remapeados)."""
        sel = self.selection()
        a, b = sel[0], sel[-1]
        if (d < 0 and a <= 1) or (d > 0 and b >= len(self.grid.colors) - 1):
            return
        order = list(range(len(self.grid.colors)))
        if d < 0:
            order[a - 1:b + 1] = order[a:b + 1] + [order[a - 1]]
        else:
            order[a:b + 2] = [order[b + 1]] + order[a:b + 1]
        edit.reorder_palette(self.char, self._slot, order)
        self.grid.sel = (a + d, b + d)
        self.grid.update()
        self._done()

    def rgb_adjust(self) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        sel = self.selection()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("RGB adjustment (add to the channels)"))
        f = QFormLayout(dlg)
        boxes = []
        for name in (tr("Red"), tr("Green"), tr("Blue")):
            sb = QSpinBox()
            sb.setRange(-255, 255)
            f.addRow(name, sb)
            boxes.append(sb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if not dlg.exec():
            return
        dr, dg, db = (b.value() for b in boxes)
        idx = [i for i in (sel if len(sel) > 1 else range(1, len(self.grid.colors))) if i > 0]
        cols = {str(i): [max(0, min(255, c + d)) for c, d in zip(self.grid.colors[i], (dr, dg, db))]
                for i in idx if i < len(self.grid.colors)}
        edit.set_palette_colors(self.char, self._slot, cols)
        self._done()

    def optimize(self) -> None:
        r = edit.clear_unused_colors(self.char, self._slot)
        self.ctx.status.emit(tr("{0} unused color(s) cleared").format(r['cleared']))
        self._done()

    def gen_images(self) -> None:
        """Uma imagem do sprite da prévia em cada paleta 1,n."""
        key = self.preview_sprite.currentData()
        d = QFileDialog.getExistingDirectory(self, tr("Folder for the palette images")) if key else None
        if not d:
            return
        from pathlib import Path
        n = 0
        for p in self.char.sprites.palettes:
            if p.group == 1:
                render.sprite_image(self.char, *key, p.item).save(Path(d) / f"pal{p.item}_{key[0]}-{key[1]}.png")
                n += 1
        self.ctx.status.emit(tr("{0} image(s) in {1}").format(n, d))

    def adapt_image(self) -> None:
        """Converte uma imagem qualquer para esta paleta (cor mais próxima) e salva como PNG indexado."""
        from pathlib import Path
        from PIL import Image
        src, _ = QFileDialog.getOpenFileName(self, tr("Image to fit"), "", tr("Images (*.png *.bmp *.gif *.pcx *.jpg)"))
        if not src:
            return
        dst, _ = QFileDialog.getSaveFileName(self, tr("Save fitted image"), str(Path(src).with_name(Path(src).stem + "_pal.png")),
                                             "PNG (*.png)")
        if not dst:
            return
        import numpy as np
        p = edit._pal(self.char, self._slot)
        img = Image.open(src)
        rgb = np.asarray(img.convert("RGB"))
        idx = ops._nearest_indices(rgb, list(p.colors))
        idx[~ops._opaque_mask(img)] = 0
        out = Image.fromarray(idx.reshape(rgb.shape[:2]), "P")
        flat = [c for col in p.colors for c in col][:768]
        out.putpalette(flat + [0] * (768 - len(flat)))
        out.save(dst, transparency=0)
        self.ctx.status.emit(tr("Fitted image saved to {0}").format(dst))

    def import_act(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Import palette"), "",
            tr("Palette or image with a palette (*.act *.png *.pcx *.gif *.bmp);;ACT palette (*.act)"))
        if path and not path.lower().endswith(".act"):
            # a paleta embutida da imagem (PNG/PCX/GIF indexado), índice por índice
            from ...tools import spriteimport as SI
            try:
                cols = SI.palette_from_image(path)
            except (OSError, ValueError) as e:
                return self.ctx.status.emit(tr("Could not read the image: {0}").format(e))
            if self.is_own():
                cols = cols[:len(self.current_palette().colors)]
                edit.set_palette_colors(self.char, self._slot, {str(i): list(c) for i, c in enumerate(cols)})
            else:                                # o mesmo caminho do .act (cria a 1,N se faltar)
                import tempfile
                from ...formats import act
                tmp = Path(tempfile.gettempdir()) / f"charfactory_pal_{os.getpid()}.act"
                act.save((cols + [(0, 0, 0)] * 256)[:256], tmp)
                ops.import_palette(self.char, self._slot, tmp)
                tmp.unlink(missing_ok=True)
            self.ctx.status.emit(tr("{0} colors taken from the image palette").format(len(cols)))
            return self._done()
        if path:
            if self.is_own():                    # paleta própria: as cores do .act entram nela
                from ...formats import act
                cols = act.load(path)[:len(self.current_palette().colors)]
                edit.set_palette_colors(self.char, self._slot, {str(i): list(c) for i, c in enumerate(cols)})
            else:
                ops.import_palette(self.char, self._slot, path)
            self._done()

    def export_act(self) -> None:
        name = "pal{0}-{1}".format(*self._slot) if self.is_own() else f"pal{self._slot}"
        path, _ = QFileDialog.getSaveFileName(self, tr("Export palette"), f"{name}.act", tr("ACT palette (*.act)"))
        if path:
            if self.is_own():
                from ...formats import act
                act.save(self.current_palette().colors, path)
            else:
                ops.export_palette(self.char, self._slot, path)
            self.ctx.status.emit(tr("Palette saved: {0}").format(path))
