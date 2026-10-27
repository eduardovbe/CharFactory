"""Editor de sprites: painel 'Sprites' | canvas com réguas | painel 'Paletas'."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QFileSystemWatcher, QItemSelectionModel, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QSlider, QSpinBox, QSplitter, QToolButton, QVBoxLayout, QWidget)

from ...tools import edit, ops, render
from ..canvas import SpriteCanvas, pil_to_qimage
from ..context import AppContext
from ..widgets import (MORE, AxisEditor, DocStrip, GroupIndex, Navigator, Section, SidePanel, compact, form_row,
                       hint_label, tool_bar)
from ...i18n import tr

ANCHORS = [("topleft", "↖"), ("top", "↑"), ("topright", "↗"),
           ("left", "←"), ("center", "•"), ("right", "→"),
           ("bottomleft", "↙"), ("bottom", "↓"), ("bottomright", "↘")]
ANCHOR_NAMES = {"topleft": tr("top left"), "top": tr("top center"), "topright": tr("top right"),
                "left": tr("center left"), "center": "centro", "right": tr("center right"),
                "bottomleft": tr("bottom left"), "bottom": tr("bottom center (feet)"),
                "bottomright": tr("bottom right")}


def thumb_icon(img: Image.Image, size: int = 48) -> QIcon:
    im = img.convert("RGBA")
    im.thumbnail((size, size), Image.Resampling.NEAREST)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
    return QIcon(QPixmap.fromImage(pil_to_qimage(canvas)))


def ask_pair(parent, title: str, label: str, default: str) -> tuple[int, int] | None:
    """Pergunta 'grupo,índice'; avisa (em vez de quebrar) quando o texto não é válido."""
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    if not ok:
        return None
    try:
        a, b = (int(v.strip()) for v in text.split(","))
        return a, b
    except ValueError:
        QMessageBox.warning(parent, title, tr("'{0}' is not group,index (e.g. 200,3).").format(text))
        return None


def _form_dialog(parent, title: str, rows: list) -> bool:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    f = QFormLayout(dlg)
    for r in rows:
        f.addRow(*r) if isinstance(r, tuple) else f.addRow(r)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    f.addRow(bb)
    return bool(dlg.exec())


class SpriteEditor(QWidget):
    title = tr("Sprites")

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._quiet = False
        self._picked_pal: int | None = None    # paleta escolhida na lista "Todas as paletas do SFF"
        self._pal_jump = False                 # indo ao sprite de uma paleta: a lista não segue o sprite
        self._pal_uses: dict[int, list] | None = None
        self._ext: dict[str, tuple[int, int, int, int]] = {}   # arquivo externo -> sprite e eixo
        self.ext_watch = QFileSystemWatcher(self)
        self.ext_watch.fileChanged.connect(self.on_external_image)

        # ------------------------------------------------ painel esquerdo
        self.tools = tool_bar([
            ("new", tr("Add image (Ins)"), self.import_image),
            ("delete", tr("Delete (Ctrl+Del)"), self.delete),
            ("duplicate", tr("Duplicate (Ctrl+D)"), self.duplicate),
            ("paint", tr("Edit image (Ctrl+E)"), self.edit_image),
            ("image_swap", tr("Replace image (Ctrl+Shift+C)"), self.change_image),
            MORE,
            ("images_add", tr("Add several images… (Ctrl+I)"), self.import_many),
            ("sheet", tr("Slice sprite sheet…"), self.open_sheet),
            None,
            ("external", tr("Edit in an external image editor (Ctrl+Shift+E)"), self.edit_external),
            ("export", tr("Export PNG… (Ctrl+Shift+X)"), self.export_png),
            None,
            ("renumber", tr("Renumber group"), self.renumber_group),
            ("delete_group", tr("Delete group"), self.delete_group),
        ])
        self.nav = Navigator()
        self.nav.indexChanged.connect(self.show_index)
        self.gi = GroupIndex()
        self.gi.changed.connect(self.on_renumber)
        self.gi.prevGroup.connect(lambda: self.jump_group(-1))
        self.gi.nextGroup.connect(lambda: self.jump_group(1))
        self.axis = AxisEditor()
        self.axis.changed.connect(self.on_axis)
        self.pick = QCheckBox(tr("Clicking the canvas sets the axis"))
        self.pick.toggled.connect(lambda on: setattr(self.canvas, "pick_axis", on))
        self.show_trans = QCheckBox(tr("Show transparency color"))
        self.show_trans.toggled.connect(lambda _: self.refresh())
        self.info = QLabel()
        self.info.setWordWrap(True)
        # onde o sprite aparece: cada animação é um link que abre no editor de animações
        self.usage = QLabel()
        self.usage.setWordWrap(True)
        self.usage.setObjectName("dim")
        self.usage.linkActivated.connect(lambda n: self.ctx.request_definition.emit("anim", int(n), 0))

        # onion skin: o sprite anterior ou um escolhido, com opacidade
        self.onion = QCheckBox(tr("Show"))
        self.onion.toggled.connect(lambda _: self.refresh())
        self.onion_mode = QComboBox()
        self.onion_mode.addItems([tr("previous sprite"), tr("chosen sprite")])
        self.onion_gi = GroupIndex(group_buttons=False)
        self.onion_gi.set(0, 0)
        self.onion_gi.setEnabled(False)
        self.onion_gi.changed.connect(lambda *_: self.refresh())
        self.onion_mode.currentIndexChanged.connect(
            lambda i: (self.onion_gi.setEnabled(i == 1), self.onion_gi.setVisible(i == 1), self.refresh()))
        self.onion_opacity = QSlider(Qt.Orientation.Horizontal)
        self.onion_opacity.setRange(10, 90)
        self.onion_opacity.setValue(35)
        self.onion_opacity.setToolTip(tr("Onion skin opacity"))
        self.onion_opacity.valueChanged.connect(lambda _: self.refresh())

        # alvo das ações em lote
        self.scope = QComboBox()
        self.scope.addItems([tr("current sprite"), tr("selected in the organizer"), tr("whole group")])
        self.scope.setToolTip(tr("Which sprites align, transform, delete, export and apply palette act on"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        for k, (anchor, arrow) in enumerate(ANCHORS):
            b = QToolButton()
            b.setText(arrow)
            b.setObjectName("nudge")
            b.setFixedSize(30, 26)
            b.setToolTip(tr("Align: axis at the {0} point").format(ANCHOR_NAMES[anchor]))
            b.clicked.connect(lambda _=False, a=anchor: self.align(a))
            grid.addWidget(b, k // 3, k % 3)
        align_box = QWidget()
        align_box.setLayout(grid)
        self.transform_bar = tool_bar([
            ("flip_h", tr("Flip horizontally"), lambda: self.transform(flip_h=True)),
            ("flip_v", tr("Flip vertically"), lambda: self.transform(flip_v=True)),
            ("rotate90", tr("Rotate 90°"), lambda: self.transform(rotate=90)),
            ("trim", tr("Trim transparent borders"), lambda: self.transform(trim=True)),
            MORE,
            ("rotate_any", tr("Rotate by any angle…"), self.rotate_any),
            ("resize", tr("Resize…"), self.resize_dialog),
            ("crop", tr("Crop rectangle…"), self.crop_dialog),
            ("export", tr("Export aligned group + axis.txt…"), self.export_aligned),
        ])
        self.info.setObjectName("infoChip")
        sprite_sec = Section(tr("Sprite"), "sprites.sprite")
        sprite_sec.add(self.gi, self.axis, self.info, self.usage, self.pick, self.show_trans,
                       hint_label(tr("Drag the sprite to align it (Shift locks the direction; arrows: 1 px, "
                                     "Shift: 10).")))
        onion_sec = Section(tr("Onion skin"), "sprites.onion", collapsed=True)
        onion_row = QHBoxLayout()
        onion_row.addWidget(self.onion)
        onion_row.addWidget(self.onion_mode, 1)
        self.onion_gi.hide()
        onion_sec.add(onion_row, self.onion_gi, form_row(tr("Opacity"), self.onion_opacity))
        batch_sec = Section(tr("Batch and transform"), "sprites.batch")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(align_box)
        hint = hint_label(tr("Axis at an anchor (bottom center = feet)."))
        row.addWidget(hint, 1)
        batch_sec.add(form_row(tr("Act on"), self.scope), row, self.transform_bar)
        left_content = QWidget()
        lv = QVBoxLayout(left_content)
        lv.addWidget(self.tools)
        lv.addWidget(self.nav)
        for sec in (sprite_sec, batch_sec, onion_sec):
            lv.addWidget(sec)
        lv.addStretch()
        self.left = SidePanel(tr("Sprites"), left_content, header=False)

        # ------------------------------------------------ centro
        self.file_tab = DocStrip()
        self.canvas = SpriteCanvas()
        self.canvas.center_image = True
        self.canvas.axisPicked.connect(self.on_axis_picked)
        self.canvas.drag_image = True          # arrastar a figura (ou Ctrl+arrastar) a move em relação ao eixo
        self.canvas.drag_image_plain = True    # como no FFS: sem Ctrl; arrastar o vazio move a vista
        self.canvas.imageDragged.connect(self.on_image_drag)
        self.canvas.doubleClicked.connect(self.edit_image)
        self.canvas.nudged.connect(lambda dx, dy: self.current() and self.on_axis(
            self.current().axis_x - dx, self.current().axis_y - dy))
        self._drag_axis = None
        self.organizer = QListWidget()
        self.organizer.setViewMode(QListWidget.ViewMode.IconMode)
        self.organizer.setIconSize(QSize(48, 48))
        self.organizer.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.organizer.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.organizer.currentRowChanged.connect(lambda r: r >= 0 and not self._quiet and self.nav.set_index(r))
        self.org_bar = tool_bar([
            ("select_all", tr("Select all"), lambda: self.organizer.selectAll()),
            ("select_none", tr("None"), lambda: self.organizer.clearSelection()),
            ("invert_sel", tr("Invert selection"), self.invert_selection),
            ("select_group", tr("Select the current group"), self.select_group),
        ])
        self.org_box = QWidget()
        ob = QVBoxLayout(self.org_box)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.addWidget(self.org_bar)
        ob.addWidget(self.organizer)
        self.org_box.setMaximumHeight(210)
        self.org_box.setVisible(False)
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self.file_tab)
        cv.addWidget(self.canvas, 1)
        cv.addWidget(self.org_box)

        # ------------------------------------------------ painel direito (paletas)
        from .palettes import PaletteGrid
        self.pal_tools = tool_bar([
            ("palette", tr("Palette editor: colors, new palettes and the 12 palettes · Ctrl+Shift+P"),
             self.open_advanced),
            ("new", tr("New palette (variant of the current one)…"), self.new_palette),
            ("delete", tr("Delete the palette"), self.delete_palette),
            ("picker", tr("Set the color of the selected indices"), self.set_color),
            MORE,
            ("palettes12", tr("12 palettes by color bands (skin, hair, clothes)…"), self.open_palettes12),
            ("swap_colors", tr("Swap the position of the selected colors"), self.swap_colors),
            ("remap", tr("Remap: pixels of the clicked index move to the Shift+clicked one"), self.remap),
            ("palette_apply", tr("Apply the palette to the sprites in 'Act on'"), self.apply_palette),
            ("eraser", tr("Clear colors no sprite uses"), self.clear_unused),
        ])
        self.pal_scope = QComboBox()
        self.pal_scope.addItem(tr("Character palettes (1,x)"), "char")
        self.pal_scope.addItem(tr("All palettes in the SFF"), "all")
        self.pal_scope.setToolTip(tr("The character palettes are the ones the player picks in the game; the SFF "
                                     "also keeps the own palettes of effects, portraits and other sprites"))
        if ctx.settings.get("sprite_pal_scope") == "all":
            self.pal_scope.setCurrentIndex(1)
        self.pal_scope.currentIndexChanged.connect(self.on_pal_scope)
        self.pal_nav = Navigator()
        self.pal_nav.indexChanged.connect(lambda _: self.on_palette_picked())
        self.use_sprite_pal = QCheckBox(tr("Use the sprite's palette"))
        self.use_sprite_pal.setToolTip(tr("On: each sprite shows with the palette it has and the panel follows it. "
                                          "Picking another palette turns it off to show the sprite with that one."))
        self.use_sprite_pal.setChecked(True)
        self.use_sprite_pal.toggled.connect(lambda _: self.on_palette())
        self.pal_label = QLabel()
        self.color_count = QComboBox()
        for n in (16, 32, 64, 128, 256):
            self.color_count.addItem(tr("{0} colors").format(n), n)
        self.color_count.activated.connect(self.on_color_count)
        compact(self.color_count, self.scope, self.onion_mode)
        self.pal_grid = PaletteGrid()
        self.pal_grid.setMinimumSize(192, 192)
        self.pal_grid.editRequested.connect(self.on_color_edit)
        self.pal_label.setObjectName("infoChip")
        self.pal_label.setWordWrap(True)
        self.pal_label.linkActivated.connect(self._go_to_sprite)
        right_content = QWidget()
        rv = QVBoxLayout(right_content)
        for w in (self.pal_tools, self.pal_scope, self.pal_nav, self.use_sprite_pal, self.pal_label):
            rv.addWidget(w)
        rv.addLayout(form_row(tr("Colors"), self.color_count))
        rv.addWidget(self.pal_grid)
        rv.addWidget(hint_label(tr("Click picks the color, Shift+click marks a range and double-click edits the color.")))
        rv.addStretch()
        self.right = SidePanel(tr("Palettes"), right_content, 270)

        split = QSplitter()
        split.addWidget(center)
        split.addWidget(self.right)
        split.setSizes([900, 290])
        split.setChildrenCollapsible(False)
        self.center = split
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

        ctx.characterChanged.connect(self.load)
        ctx.themeChanged.connect(self.on_theme)
        ctx.modelChanged.connect(lambda part: part == "sprites" and QTimer.singleShot(0, lambda: self.load(keep=True)))
        self.on_theme()
        self._show_pal_scope()

    def set_backdrop(self, bg: dict | None) -> None:
        """Com fundo, o eixo fica no chão do stage; sem, o sprite aparece centralizado."""
        centered = bg is None
        self.canvas.set_backdrop(bg)
        if self.canvas.center_image != centered:
            self.canvas.center_image = centered
            self.canvas.fit()

    def on_theme(self) -> None:
        self.canvas.set_theme(self.ctx.theme)
        self.pal_grid.theme = self.ctx.theme
        self.pal_grid.update()
        spr = self.current() if self.char else None
        if spr is not None:
            self.usage.setText(self._usage_text(spr))     # os links usam a cor do tema

    # ------------------------------------------------------------ dados

    @property
    def char(self):
        return self.ctx.char

    def sprites(self):
        try:
            return self.char.sprites.sprites if self.char else []
        except Exception:  # noqa: BLE001 — personagem sem SFF
            return []

    def current(self):
        s = self.sprites()
        i = self.nav.index()
        return s[i] if 0 <= i < len(s) else None

    def char_palettes(self):
        try:
            return [p for p in self.char.sprites.palettes if p.group == 1] if self.char else []
        except Exception:  # noqa: BLE001
            return []

    def targets(self) -> list[tuple[int, int]]:
        """Sprites sobre os quais as ações em lote agem (conforme 'Agir sobre')."""
        spr = self.current()
        mode = self.scope.currentIndex()
        if mode == 1:
            rows = sorted({self.organizer.row(it) for it in self.organizer.selectedItems()})
            all_s = self.sprites()
            picked = [(all_s[r].group, all_s[r].item) for r in rows if r < len(all_s)]
            if picked:
                return picked
        if mode == 2 and spr:
            return [(s.group, s.item) for s in self.sprites() if s.group == spr.group]
        return [(spr.group, spr.item)] if spr else []

    def load(self, keep: bool = False) -> None:
        if not self.char:
            return
        idx = self.nav.index() if keep else 0
        p = self.char.path_of("sprite")
        self.file_tab.set_file(p.name if p else "—")
        self.nav.set_count(len(self.sprites()))
        self._pal_uses = None
        self._pal_scope_count()
        self.pal_nav.set_count(self._pal_nav_count())
        if self.org_box.isVisible():
            self.fill_organizer()
        self.nav.set_index(min(idx, max(0, len(self.sprites()) - 1)))
        self.refresh()
        if not keep:
            self.canvas.fit()

    def fill_organizer(self) -> None:
        sel = {self.organizer.row(it) for it in self.organizer.selectedItems()}
        self.organizer.clear()
        s = self.char.sprites
        for spr in s.sprites:
            it = QListWidgetItem(f"{spr.group},{spr.item}")
            try:
                it.setIcon(thumb_icon(s.image(spr)))
            except Exception:  # noqa: BLE001
                pass
            self.organizer.addItem(it)
        for r in sel:
            if r < self.organizer.count():
                self.organizer.item(r).setSelected(True)

    def toggle_organizer(self, on: bool) -> None:
        self.org_box.setVisible(on)
        if on and self.char:
            self.fill_organizer()
            self.scope.setCurrentIndex(1)
        elif self.scope.currentIndex() == 1:
            self.scope.setCurrentIndex(0)

    def invert_selection(self) -> None:
        for i in range(self.organizer.count()):
            it = self.organizer.item(i)
            it.setSelected(not it.isSelected())

    def select_group(self) -> None:
        spr = self.current()
        if not spr:
            return
        self.organizer.clearSelection()
        for i, s in enumerate(self.sprites()):
            if s.group == spr.group and i < self.organizer.count():
                self.organizer.item(i).setSelected(True)

    def show_index(self, _=None) -> None:
        self.refresh()
        if self.canvas.center_image:
            self.canvas.fit()
        # com fundo de stage o eixo fica parado no chão (como no FFS): trocar de sprite não mexe
        # na vista, e dá para comparar a posição de um sprite com a do outro
        if self.org_box.isVisible() and self.nav.index() < self.organizer.count():
            self._quiet = True
            self.organizer.setCurrentRow(self.nav.index(), QItemSelectionModel.SelectionFlag.NoUpdate)
            self._quiet = False

    def select(self, group: int, item: int) -> None:
        for i, s in enumerate(self.sprites()):
            if (s.group, s.item) == (group, item):
                self.nav.set_index(i)
                self.refresh()
                return

    def selected_palette_slot(self):
        pals = self.char_palettes()
        i = self.pal_nav.index()
        return pals[i].item if 0 <= i < len(pals) else None

    def own_palette(self):
        """Paleta própria (fora das 1,x) do sprite atual, como a 3,0 de um efeito; senão None."""
        spr = self.current()
        if spr is None or spr.is_truecolor or spr.palette_index is None:
            return None
        pals = self.char.sprites.palettes
        p = pals[spr.palette_index] if 0 <= spr.palette_index < len(pals) else None
        return p if p is not None and p.group != 1 else None

    def palette_key(self):
        """A paleta que a grade mostra e as ferramentas editam: a própria do sprite, quando ele
        usa uma e o painel segue o sprite; senão a 1,N escolhida. Na lista de todas as paletas,
        a escolhida nela, pela posição (um SFF pode repetir o mesmo número)."""
        if self._all_pals() and self.char:
            pals = self.char.sprites.palettes
            k = self._picked_pal
            return (pals[k].group, pals[k].item, k) if k is not None and 0 <= k < len(pals) else None
        own = self.own_palette() if self.use_sprite_pal.isChecked() else None
        return (own.group, own.item) if own is not None else self.selected_palette_slot()

    def shown_palette(self):
        key = self.palette_key()
        if key is None:
            return None
        if isinstance(key, tuple) and len(key) == 3:
            return self.char.sprites.palettes[key[2]]
        g, i = key if isinstance(key, tuple) else (1, key)
        k = self.char.sprites.find_palette(g, i)
        return self.char.sprites.palettes[k] if k >= 0 else None

    def refresh(self) -> None:
        spr = self.current()
        if spr is None:
            self.canvas.set_image(None)
            self.canvas.set_onion(None)
            self.info.setText(tr("No sprites") if self.char else "")
            self.usage.setText("")
            return
        slot = None if self.use_sprite_pal.isChecked() or self._all_pals() else self.selected_palette_slot()
        img = render.sprite_image(self.char, spr.group, spr.item, slot)
        if self.show_trans.isChecked() and not spr.is_truecolor:
            pal = self.char.sprites.palettes[spr.palette_index] if spr.palette_index is not None else None
            if pal and pal.colors:
                bg = Image.new("RGBA", img.size, (*pal.colors[0], 255))
                bg.alpha_composite(img)
                img = bg
        self.canvas.set_image(img, spr.axis_x, spr.axis_y)
        self._draw_onion()
        self.gi.set(spr.group, spr.item)
        self.axis.set(spr.axis_x, spr.axis_y)
        i = self.nav.index()
        same = sum(1 for s in self.sprites() if s.linked_to == i or (spr.linked_to is not None and s is not spr and s.linked_to == spr.linked_to))
        self.info.setText(f"{spr.width} × {spr.height} px ({spr.color_depth} bits) · {spr.format_name}"
                          + (tr(" · linked") if spr.linked_to is not None else tr(" · unique")) + (tr(" · {0} link(s)").format(same) if same else ""))
        self.usage.setText(self._usage_text(spr))
        if self._all_pals():
            if not self._pal_jump and spr.palette_index is not None and not spr.is_truecolor:
                self._picked_pal = spr.palette_index      # o slider segue a paleta do sprite
                self._quiet = True
                self.pal_nav.set_index(spr.palette_index)
                self._quiet = False
        elif self.use_sprite_pal.isChecked() and spr.palette_index is not None and not spr.is_truecolor:
            p = self.char.sprites.palettes[spr.palette_index]
            self._quiet = True
            pals = self.char_palettes()
            if p in pals:
                self.pal_nav.set_index(pals.index(p))
            self._quiet = False
        self.show_palette()

    def anims_using(self, group: int, item: int) -> list[int]:
        """Animações com algum frame neste sprite."""
        try:
            anims = self.char.anims
        except Exception:  # noqa: BLE001 — personagem sem AIR
            return []
        out = []
        for n in anims.numbers():
            a = anims.get(n)
            if a and any(f.group == group and f.item == item for f in a.frames):
                out.append(n)
        return out

    def _usage_text(self, spr) -> str:
        uses = self.anims_using(spr.group, spr.item)
        if not uses:
            return tr("Does not appear in any animation.")
        color = self.ctx.theme.ui.get("accent", "#6366f1") if self.ctx.theme else "#6366f1"
        links = ", ".join(f'<a href="{n}" style="color:{color}">{n}</a>' for n in uses[:12])
        more = tr(" and {0} more").format(len(uses) - 12) if len(uses) > 12 else ""
        if len(uses) == 1:
            return tr("Used in animation {0}").format(links)
        return tr("Used in {0} animations: {1}{2}").format(len(uses), links, more)

    def _draw_onion(self) -> None:
        if not self.onion.isChecked():
            self.canvas.set_onion(None)
            return
        if self.onion_mode.currentIndex() == 1:
            other = self.char.sprites.find(self.onion_gi.g.value(), self.onion_gi.i.value())
        else:
            i = self.nav.index()
            other = self.sprites()[i - 1] if i > 0 else None
        if other is None:
            self.canvas.set_onion(None)
            return
        self.canvas.onion_opacity = self.onion_opacity.value() / 100
        self.canvas.set_onion(render.sprite_image(self.char, other.group, other.item), other.axis_x, other.axis_y)

    def show_palette(self) -> None:
        p = self.shown_palette() if self.char else None
        if p is None:
            return
        own = self.own_palette()
        text = tr("Group {0}, index {1} · {2} colors").format(p.group, p.item, len(p.colors))
        if self._all_pals():
            text = self._palette_uses_text(p)
        elif p.group != 1:
            text = tr("Own palette {0},{1} of this sprite · {2} colors. Edits here change only the sprites "
                      "that use it.").format(p.group, p.item, len(p.colors))
        elif own is not None:
            text += tr(" · This sprite has its own palette ({0},{1}): the character palettes do not change it. "
                       "Turn on “Use the sprite's palette” to edit it.").format(own.group, own.item)
        self.pal_label.setText(text)
        self.pal_grid.set_colors(p.colors)
        k = self.color_count.findData(len(p.colors))
        self.color_count.setCurrentIndex(k if k >= 0 else self.color_count.count() - 1)

    def on_palette(self) -> None:
        if not self._quiet:
            self.refresh()

    # ------------------------------------------------------------ todas as paletas do SFF

    def _all_pals(self) -> bool:
        return self.pal_scope.currentData() == "all"

    def _show_pal_scope(self) -> None:
        self.use_sprite_pal.setVisible(not self._all_pals())

    def _pal_nav_count(self) -> int:
        """O slider passa pelas paletas do personagem ou, como no FFS, por todas as do SFF."""
        if not self.char:
            return 0
        try:
            return len(self.char.sprites.palettes) if self._all_pals() else len(self.char_palettes())
        except Exception:  # noqa: BLE001 — personagem sem SFF
            return 0

    def _pal_scope_count(self) -> None:
        try:
            n = len(self.char.sprites.palettes) if self.char else 0
        except Exception:  # noqa: BLE001 — personagem sem SFF
            n = 0
        self.pal_scope.setItemText(1, tr("All palettes in the SFF ({0})").format(n) if n else tr("All palettes in the SFF"))

    def on_pal_scope(self, _=None) -> None:
        self.ctx.settings["sprite_pal_scope"] = self.pal_scope.currentData()
        self._show_pal_scope()
        self._quiet = True
        self.pal_nav.set_count(self._pal_nav_count())
        self._quiet = False
        if self._all_pals():
            spr = self.current()
            self._picked_pal = spr.palette_index if spr is not None and not spr.is_truecolor else None
        elif not self.use_sprite_pal.isChecked():
            self.use_sprite_pal.setChecked(True)      # volta a mostrar cada sprite com a paleta dele
        if self.char:
            self.refresh()

    def palette_uses(self) -> dict[int, list]:
        """Posição da paleta -> sprites (indexados) que a usam."""
        if self._pal_uses is None:
            uses: dict[int, list] = {}
            for spr in self.sprites():
                if spr.palette_index is not None and not spr.is_truecolor:
                    uses.setdefault(spr.palette_index, []).append(spr)
            self._pal_uses = uses
        return self._pal_uses

    def pick_any_palette(self, k: int) -> None:
        """Slider em todas as paletas: a paleta k vai para a grade e, se o sprite na tela não a
        usa, passa a mostrar o primeiro que usa."""
        if not self.char or not 0 <= k < len(self.char.sprites.palettes):
            return
        self._picked_pal = k
        spr = self.current()
        users = self.palette_uses().get(k, [])
        if users and (spr is None or spr.palette_index != k):
            self._pal_jump = True
            try:
                self.select(users[0].group, users[0].item)
            finally:
                self._pal_jump = False
        else:
            self.show_palette()

    def _palette_uses_text(self, p) -> str:
        k = self._picked_pal
        users = self.palette_uses().get(k, []) if k is not None else []
        text = tr("Palette {0},{1} · {2} colors").format(p.group, p.item, len(p.colors))
        if not users:
            return text + tr(" · no sprite uses it")
        color = self.ctx.theme.ui.get("accent", "#6366f1") if self.ctx.theme else "#6366f1"
        links = ", ".join(f'<a href="{s.group},{s.item}" style="color:{color}">{s.group},{s.item}</a>'
                          for s in users[:12])
        more = tr(" and {0} more").format(len(users) - 12) if len(users) > 12 else ""
        return text + " · " + tr("used by {0} sprite(s): {1}{2}").format(len(users), links, more)

    def _go_to_sprite(self, ref: str) -> None:
        g, i = (int(v) for v in ref.split(","))
        self._pal_jump = True
        try:
            self.select(g, i)
        finally:
            self._pal_jump = False

    def on_palette_picked(self) -> None:
        """Escolher uma paleta no navegador mostra o sprite com ela. Antes, com 'Usar a paleta do
        sprite' ligado, a escolha voltava sozinha para a paleta do sprite e a edição sumia."""
        if self._quiet:
            return
        if self._all_pals():
            self.pick_any_palette(self.pal_nav.index())
            return
        if self.use_sprite_pal.isChecked():
            self.use_sprite_pal.blockSignals(True)
            self.use_sprite_pal.setChecked(False)
            self.use_sprite_pal.blockSignals(False)
        self.refresh()

    # ------------------------------------------------------------ edição de sprite

    def _changed(self) -> None:
        self.ctx.changed("sprites")

    def _palette_changed(self) -> None:
        """Ferramenta de paleta (um clique): vira um passo próprio do Ctrl+Z."""
        h = self.ctx.undo_stack()
        if h:
            h.cut("sprites")
        self._changed()

    def on_axis(self, x: int, y: int) -> None:
        spr = self.current()
        if spr and (spr.axis_x, spr.axis_y) != (x, y):
            ops.set_sprite_axis(self.char, spr.group, spr.item, x, y)
            self.ctx.record("sprites")
            self.refresh()
            self.window().update_title()

    def on_axis_picked(self, x: int, y: int) -> None:
        self.on_axis(x, y)

    def on_image_drag(self, dx: int, dy: int, done: bool) -> None:
        """Ctrl+arrastar: a figura anda e o eixo fica; equivale a mudar o eixo ao contrário."""
        spr = self.current()
        if not spr:
            return
        if self._drag_axis is None:
            self._drag_axis = (spr.axis_x, spr.axis_y)
        ax, ay = self._drag_axis
        self.on_axis(ax - dx, ay - dy)
        if done:
            self._drag_axis = None

    def on_renumber(self, g: int, i: int) -> None:
        spr = self.current()
        if not spr or (spr.group, spr.item) == (g, i):
            return
        try:
            r = edit.renumber_sprite(self.char, spr.group, spr.item, g, i)
        except ValueError as e:
            self.ctx.status.emit(str(e))
            self.gi.set(spr.group, spr.item)
            return
        if r["frames_updated"]:
            self.ctx.changed("anims")
        self.ctx.record("sprites")
        self.window().update_title()
        self.ctx.status.emit(tr("Sprite renumbered to {0},{1}").format(g, i) +
                             (tr("; {0} animation frame(s) updated").format(r['frames_updated']) if r["frames_updated"] else ""))

    def jump_group(self, d: int) -> None:
        spr = self.current()
        if not spr:
            return
        groups = sorted(self.char.sprites.groups())
        gi = groups.index(spr.group) + d
        if 0 <= gi < len(groups):
            g = groups[gi]
            self.select(g, min(self.char.sprites.groups()[g]))

    def _each(self, fn, label: str) -> None:
        done, errors = 0, []
        for g, i in self.targets():
            try:
                fn(g, i)
                done += 1
            except ValueError as e:
                errors.append(f"{g},{i}: {e}")
        if done:
            self._changed()
        self.ctx.status.emit(tr("{0}: {1} sprite(s)").format(label, done) + (tr("; {0} error(s): {1}").format(len(errors), errors[0]) if errors else ""))

    def transform(self, **kw) -> None:
        self._each(lambda g, i: edit.transform_sprite(self.char, g, i, **kw), tr("Transformed"))

    def rotate_any(self) -> None:
        a, ok = QInputDialog.getDouble(self, tr("Rotate"), tr("Angle (degrees, counterclockwise):"), 15, -360, 360, 1)
        if ok and a:
            self._each(lambda g, i: edit.rotate_sprite(self.char, g, i, a), tr("Rotated {0:g}°").format(a))

    def resize_dialog(self) -> None:
        spr = self.current()
        if not spr:
            return
        mode = QComboBox()
        mode.addItems([tr("by percentage (all in 'Act on')"), tr("exact size (current sprite only)")])
        pct = QDoubleSpinBox()
        pct.setRange(1, 1000)
        pct.setValue(100)
        pct.setSuffix(" %")
        w, h = QSpinBox(), QSpinBox()
        for sb, v in ((w, spr.width), (h, spr.height)):
            sb.setRange(1, 8192)
            sb.setValue(v)
        if not _form_dialog(self, tr("Resize"), [(tr("Mode"), mode), (tr("Scale"), pct), (tr("Width"), w), (tr("Height"), h)]):
            return
        if mode.currentIndex() == 0:
            s = pct.value() / 100
            self._each(lambda g, i: edit.transform_sprite(self.char, g, i, scale=s), tr("Scale {0:g}%").format(pct.value()))
        else:
            edit.resize_sprite(self.char, spr.group, spr.item, w.value(), h.value())
            self._changed()

    def crop_dialog(self) -> None:
        spr = self.current()
        if not spr:
            return
        x, y, w, h = (QSpinBox() for _ in range(4))
        x.setRange(0, max(0, spr.width - 1))
        y.setRange(0, max(0, spr.height - 1))
        w.setRange(1, spr.width)
        h.setRange(1, spr.height)
        w.setValue(spr.width)
        h.setValue(spr.height)
        hint = QLabel(tr("The image is {0} × {1} px; the axis stays on the same point of the figure.").format(spr.width, spr.height))
        hint.setObjectName("dim")
        if _form_dialog(self, tr("Crop rectangle"), [hint, ("X", x), ("Y", y), (tr("Width"), w), (tr("Height"), h)]):
            try:
                edit.crop_sprite(self.char, spr.group, spr.item, x.value(), y.value(), w.value(), h.value())
            except ValueError as e:
                QMessageBox.warning(self, tr("Crop"), str(e))
                return
            self._changed()

    def align(self, anchor: str) -> None:
        t = self.targets()
        if t:
            ops.align_sprites(self.char, anchor, sprites=[list(x) for x in t])
            self._changed()
            self.ctx.status.emit(tr("{0} sprite(s) aligned: axis at the {1} point").format(len(t), ANCHOR_NAMES[anchor]))

    def duplicate(self) -> None:
        spr = self.current()
        if not spr:
            return
        r = ask_pair(self, tr("Duplicate sprite"), tr("New group,index:"), f"{spr.group},{self._free_item(spr.group, spr.item)}")
        if r:
            if self.char.sprites.find(*r) and QMessageBox.question(
                    self, tr("Duplicate"), tr("Sprite {0},{1} already exists. Replace it?").format(r[0], r[1])) != QMessageBox.StandardButton.Yes:
                return
            edit.duplicate_sprite(self.char, spr.group, spr.item, *r)
            self._changed()
            QTimer.singleShot(10, lambda: self.select(*r))

    def delete(self) -> None:
        t = self.targets()
        if not t:
            return
        what = tr("sprite {0},{1}").format(t[0][0], t[0][1]) if len(t) == 1 else f"{len(t)} sprites"
        if QMessageBox.question(self, tr("Delete"), tr("Delete {0}?").format(what)) == QMessageBox.StandardButton.Yes:
            for g, i in t:
                ops.remove_sprite(self.char, g, i)
            self._changed()

    def delete_group(self) -> None:
        spr = self.current()
        if spr and QMessageBox.question(self, tr("Delete group"), tr("Delete the whole group {0}?").format(spr.group)) == QMessageBox.StandardButton.Yes:
            edit.delete_group(self.char, spr.group)
            self._changed()

    def renumber_group(self) -> None:
        spr = self.current()
        if not spr:
            return
        g, ok = QInputDialog.getInt(self, tr("Renumber group"), tr("New number for group {0}:").format(spr.group), spr.group, 0, 99999)
        if ok:
            try:
                edit.renumber_group(self.char, spr.group, g)
            except ValueError as e:
                QMessageBox.warning(self, tr("Renumber"), str(e))
                return
            self.ctx.changed("anims")
            self._changed()

    def _ask_image(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Image"), "", tr("Images (*.png *.pcx *.gif *.bmp)"))
        return path

    def _free_item(self, group: int, start: int = 0) -> int:
        used = {s.item for s in self.sprites() if s.group == group}
        while start in used:
            start += 1
        return start

    def import_image(self) -> None:
        self._import(many=False)

    def import_many(self) -> None:
        self._import(many=True)

    def _import(self, many: bool) -> None:
        from ..spriteimport import ask_images
        self.import_paths(ask_images(self, many))

    def import_paths(self, paths: list) -> None:
        """Janela de importar: grupo e número, paleta (do personagem, do SFF, própria ou cores
        reais), fundo transparente e prévia. Antes tudo ia para a paleta 1,1 sem perguntar.
        Vale para Adicionar, arrastar imagens para a janela e colar."""
        from ..spriteimport import ImportSpritesDialog
        paths = [str(p) for p in paths]
        if not paths or not self.char:
            return
        spr = self.current()
        g = spr.group if spr else 0
        dlg = ImportSpritesDialog(self.ctx, paths, g, self._free_item(g, spr.item if spr else 0), self)
        if dlg.exec() and dlg.result:
            made = dlg.result["sprites"]
            self._changed()
            self.load(keep=True)                 # já mostra o primeiro importado
            self.select(*made[0])
            r = dlg.result
            self.ctx.status.emit(tr("{0} image(s) imported").format(len(made))
                                 + (tr(" · palette {0},{1} created").format(*r["palette"]) if r.get("palette") else
                                    tr(" · {0} palettes created").format(len(r["palettes"])) if r.get("palettes") else "")
                                 + (tr(" · {0} linked to identical sprites").format(len(r["linked"]))
                                    if r.get("linked") else ""))
        dlg.deleteLater()

    def change_image(self) -> None:
        spr = self.current()
        path = self._ask_image() if spr else None
        if path:
            ops.import_sprite(self.char, path, spr.group, spr.item, spr.axis_x, spr.axis_y, autocrop=False,
                              keep_indices=True)
            self._changed()

    def export_png(self) -> None:
        t = self.targets()
        if not t:
            return
        if len(t) == 1:
            g, i = t[0]
            path, _ = QFileDialog.getSaveFileName(self, tr("Export PNG"), f"{g}-{i}.png", "PNG (*.png)")
            if path:
                render.sprite_image(self.char, g, i).save(path)
            return
        d = QFileDialog.getExistingDirectory(self, tr("Folder for the {0} PNGs").format(len(t)))
        if d:
            for g, i in t:
                render.sprite_image(self.char, g, i).save(Path(d) / f"{g}-{i}.png")
            self.ctx.status.emit(tr("{0} PNG(s) in {1}").format(len(t), d))

    def export_aligned(self) -> None:
        spr = self.current()
        d = QFileDialog.getExistingDirectory(self, tr("Destination folder")) if spr else None
        if d:
            r = ops.export_sprites_aligned(self.char, Path(d) / f"grupo_{spr.group}", group=spr.group)
            self.ctx.status.emit(tr("{0} sprites exported; axis {1} (axis.txt)").format(r['exported'], r['axis']))

    def open_sheet(self) -> None:
        from ..dialogs import SheetDialog
        SheetDialog(self.ctx, self).exec()

    # ------------------------------------------------------------ área de transferência (menu Editar)

    def copy(self) -> None:
        spr = self.current()
        if spr:
            img = render.sprite_image(self.char, spr.group, spr.item).convert("RGBA")
            QGuiApplication.clipboard().setImage(pil_to_qimage(img))
            self.ctx.status.emit(tr("Sprite {0},{1} copied").format(spr.group, spr.item))

    def cut(self) -> None:
        spr = self.current()
        if spr:
            self.copy()
            ops.remove_sprite(self.char, spr.group, spr.item)
            self._changed()

    def paste(self) -> None:
        q = QGuiApplication.clipboard().image()
        if q.isNull() or not self.char:
            self.ctx.status.emit(tr("The clipboard has no image"))
            return
        q = q.convertToFormat(QImage.Format.Format_RGBA8888)
        img = Image.frombytes("RGBA", (q.width(), q.height()), bytes(q.constBits()), "raw", "RGBA", q.bytesPerLine())
        spr = self.current()
        g = spr.group if spr else 0
        i = self._free_item(g, spr.item if spr else 0)
        tmp = Path(tempfile.gettempdir()) / "charfactory_paste.png"
        img.save(tmp)
        self.import_paths([tmp])

    # ------------------------------------------------------------ editor de imagem embutido

    def _editor_payload(self, spr) -> dict:
        """O que o editor de imagem precisa de um sprite: pixels nos índices, paleta, eixo e os
        vizinhos do grupo para o onion skin."""
        sff = self.char.sprites
        if spr.is_truecolor:
            img, colors = sff.image(spr), []
        else:
            img = Image.frombytes("P", (spr.width, spr.height), sff.pixels(spr))
            pal = sff.palettes[spr.palette_index] if spr.palette_index is not None else None
            colors = [tuple(c[:3]) for c in pal.colors] if pal else []
            colors += [(0, 0, 0)] * (256 - len(colors))
        same = [s for s in self.sprites() if s.group == spr.group]
        k = next((n for n, s in enumerate(same) if s.item == spr.item), 0)
        onion = []
        for side, n in (("prev", k - 1), ("next", k + 1)):
            if 0 <= n < len(same):
                o = same[n]
                onion.append((render.sprite_image(self.char, o.group, o.item), (o.axis_x, o.axis_y), side))
        return {"image": img, "colors": colors, "axis": (spr.axis_x, spr.axis_y), "onion": onion,
                "title": tr("Edit image · sprite {0},{1}").format(spr.group, spr.item), "key": (spr.group, spr.item)}

    def _apply_edit(self, g: int, i: int, image, axis) -> None:
        ops.set_sprite_image(self.char, g, i, image)
        spr = self.char.sprites.find(g, i)
        if axis is not None and (spr.axis_x, spr.axis_y) != tuple(axis):
            ops.set_sprite_axis(self.char, g, i, *axis)
        self._changed()

    def edit_image(self) -> None:
        """Abre o sprite atual no editor de imagem do app (pixels nos índices da paleta). PgUp e
        PgDn lá dentro gravam o sprite e passam ao anterior/seguinte sem fechar a janela."""
        spr = self.current()
        if not spr:
            return
        from ..imageeditor import ImageEditor
        state = {"key": (spr.group, spr.item)}

        def navigate(delta, image, axis):
            g0, i0 = state["key"]
            if image is not None:
                self._apply_edit(g0, i0, image, axis)
            order = [(s.group, s.item) for s in self.sprites()]
            j = order.index((g0, i0)) + delta if (g0, i0) in order else -1
            if not 0 <= j < len(order):
                return None
            state["key"] = order[j]
            return self._editor_payload(self.char.sprites.find(*order[j]))

        p = self._editor_payload(spr)
        dlg = ImageEditor(self, p["image"], p["colors"], p["axis"], p["title"], self.ctx.theme,
                          onion=p["onion"], navigate=navigate)
        ok = dlg.exec()
        g, i = state["key"]
        if ok and dlg.result_image is not None:
            self._apply_edit(g, i, dlg.result_image, dlg.result_axis)
            self.ctx.status.emit(tr("Sprite {0},{1} edited").format(g, i))
        elif dlg.saved_any:
            self.ctx.status.emit(tr("Sprites edited in the image editor"))
        if (ok and dlg.result_image is not None) or dlg.saved_any:
            QTimer.singleShot(0, lambda: self.select(g, i))

    # ------------------------------------------------------------ editor de imagens externo

    def edit_external(self) -> None:
        """Abre o sprite num editor externo (Aseprite, Photoshop…; Ferramentas › Opções) e
        reimporta cada vez que o arquivo é salvo lá, com os mesmos índices de cor e eixo."""
        spr = self.current()
        if not spr:
            return
        d = Path(tempfile.gettempdir()) / "charfactory_edit"
        d.mkdir(exist_ok=True)
        path = d / f"{self.char.def_path.stem}_{spr.group}-{spr.item}.png"
        if spr.is_truecolor:
            self.char.sprites.image(spr).save(path)
        else:
            img = Image.frombytes("P", (spr.width, spr.height), self.char.sprites.pixels(spr))
            pal = self.char.sprites.palettes[spr.palette_index].colors if spr.palette_index is not None else []
            flat = [c for col in pal for c in col][:768]
            img.putpalette(flat + [0] * (768 - len(flat)))
            img.save(path, transparency=0)
        key = str(path)
        self._ext[key] = (spr.group, spr.item, spr.axis_x, spr.axis_y)
        if key not in self.ext_watch.files():
            self.ext_watch.addPath(key)
        exe = self.ctx.settings.get("image_editor")
        try:
            if exe:
                subprocess.Popen([exe, key])
            else:
                os.startfile(key)  # noqa: S606 — programa padrão do Windows para PNG
        except (OSError, AttributeError) as e:
            QMessageBox.warning(self, tr("External editor"), tr("Could not open {0}: {1}").format(key, e))
            return
        self.ctx.status.emit(tr("Editing {0} outside the app; when you save it there, the sprite is updated").format(path.name))

    def on_external_image(self, path: str) -> None:
        QTimer.singleShot(300, lambda: Path(path).exists() and path not in self.ext_watch.files()
                          and self.ext_watch.addPath(path))
        info = self._ext.get(path)
        if not info or not self.char or not Path(path).exists():
            return
        g, i, ax, ay = info

        def reimport():
            try:
                ops.import_sprite(self.char, path, g, i, ax, ay, autocrop=False, keep_indices=True)
            except Exception as e:  # noqa: BLE001 — arquivo ainda sendo gravado
                self.ctx.status.emit(tr("Reimport {0}: {1}").format(Path(path).name, e))
                return
            self._changed()
            self.ctx.status.emit(tr("Sprite {0},{1} updated by the external editor").format(g, i))
        QTimer.singleShot(250, reimport)

    # ------------------------------------------------------------ paletas (painel direito)

    def new_palette(self) -> None:
        """Nova paleta: o editor de paletas na aba Nova paleta, com a prévia antes de gravar."""
        from .palettes import PaletteEditor
        self.open_palette_editor(PaletteEditor.TAB_NEW)

    def delete_palette(self) -> None:
        slot = self.selected_palette_slot()
        if slot is None:
            return
        if QMessageBox.question(self, tr("Delete palette"), tr("Delete palette 1,{0}?").format(slot)) == QMessageBox.StandardButton.Yes:
            try:
                edit.delete_palette(self.char, slot)
            except ValueError as e:
                QMessageBox.warning(self, tr("Delete palette"), str(e))
                return
            self._palette_changed()

    def _selection(self):
        a, b = sorted(self.pal_grid.sel)
        return a, b

    def swap_colors(self) -> None:
        a, b = self._selection()
        slot = self.palette_key()
        if slot is not None and a != b:
            edit.swap_palette_colors(self.char, slot, a, b)
            self._palette_changed()

    def remap(self) -> None:
        a, b = self.pal_grid.sel
        slot = self.palette_key()
        if slot is None or a == b:
            self.ctx.status.emit(tr("Click the source index and Shift+click the destination"))
            return
        r = edit.remap_palette_index(self.char, slot, a, b)
        self._palette_changed()
        self.ctx.status.emit(tr("Index {0} → {1} in {2} sprite(s)").format(a, b, r['sprites_remapped']))

    def apply_palette(self) -> None:
        slot = self.selected_palette_slot()
        t = self.targets()
        if slot is not None and t:
            r = edit.set_sprites_palette(self.char, [list(x) for x in t], slot)
            self._palette_changed()
            self.ctx.status.emit(tr("Palette 1,{0} applied to {1} sprite(s)").format(slot, r['sprites']))

    def clear_unused(self) -> None:
        slot = self.palette_key()
        if slot is not None:
            r = edit.clear_unused_colors(self.char, slot)
            self._palette_changed()
            self.ctx.status.emit(tr("{0} unused color(s) cleared").format(r['cleared']))

    def on_color_count(self, _=None) -> None:
        slot = self.palette_key()
        n = self.color_count.currentData()
        if slot is None or not n:
            return
        used = edit.used_palette_indices(self.char, slot)
        if used and max(used) >= n and QMessageBox.question(
                self, tr("Number of colors"), tr("Some sprites use index {0}; with {1} colors they get the wrong color. Continue?").format(max(used), n)) != QMessageBox.StandardButton.Yes:
            self.show_palette()
            return
        edit.set_palette_size(self.char, slot, n)
        self._palette_changed()

    def set_color(self) -> None:
        a, b = self._selection()
        self._edit_colors(list(range(a, b + 1)))

    def on_color_edit(self, i: int) -> None:
        """Duplo clique numa cor; com uma faixa selecionada que a contém, pinta a faixa toda."""
        a, b = self._selection()
        self._edit_colors(list(range(a, b + 1)) if a <= i <= b and a != b else [i])

    def _edit_colors(self, indices: list[int]) -> None:
        """Escolha de cor com o sprite atualizando a cada cor; Cancelar volta como estava."""
        from .palettes import edit_colors_live
        slot, p = self.palette_key(), self.shown_palette()
        if slot is None or p is None:
            return
        title = tr("Color {0}").format(indices[0]) if len(indices) == 1 else \
            tr("Colors {0}–{1}").format(indices[0], indices[-1])
        rgb = edit_colors_live(self, p, indices, title, self._redraw_palette)
        if rgb is not None:
            edit.set_palette_colors(self.char, slot, {str(i): list(rgb) for i in indices})
            self._palette_changed()

    def _redraw_palette(self) -> None:
        self._quiet = True               # repinta sem mexer no navegador de paletas
        self.refresh()
        self._quiet = False

    def open_palettes12(self) -> None:
        from .palettes import PaletteEditor
        self.open_palette_editor(PaletteEditor.TAB_SET)

    def open_advanced(self) -> None:
        self.open_palette_editor()

    def default_palette_slot(self, spr):
        """A paleta padrão do sprite: a 1,N que ele usa (normalmente a 1,1), ou a própria dele
        ((grupo, item), como a 3,0 de um efeito). Sprite sem paleta: a primeira do personagem."""
        pals = self.char.sprites.palettes
        if spr is not None and not spr.is_truecolor and spr.palette_index is not None                 and 0 <= spr.palette_index < len(pals):
            p = pals[spr.palette_index]
            return p.item if p.group == 1 else (p.group, p.item)
        char = self.char_palettes()
        return min(p.item for p in char) if char else None

    def open_palette_editor(self, tab: int | None = None, sprite=None) -> None:
        """Tudo das paletas numa janela (cores, nova paleta e as 12 paletas), sempre no sprite
        atual (ou no `sprite` dado, como o frame da animação na tela) e na paleta padrão dele;
        se pedido, numa aba."""
        from .palettes import PaletteEditor
        if not self.char:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Palette editor"))
        dlg.resize(1280, 800)
        lay = QVBoxLayout(dlg)
        ed = PaletteEditor(self.ctx)
        ed.load()
        spr = self.char.sprites.find(*sprite) if sprite else self.current()
        if spr is None:
            spr = self.current()
        slot = self.default_palette_slot(spr)
        if tab is not None and isinstance(slot, tuple):
            slot = self.default_palette_slot(None)   # Nova paleta e 12 paletas são das 1,x
        ed.open_on((spr.group, spr.item) if spr is not None else None, slot)
        if tab is not None:
            ed.tabs.setCurrentIndex(tab)
        lay.addWidget(ed)
        dlg.exec()
        dlg.deleteLater()      # fechado, o editor não deve seguir recarregando a cada mudança
