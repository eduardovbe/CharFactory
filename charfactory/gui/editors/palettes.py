"""Editor de paletas: grade 16×16, edição de cores, variantes e ferramentas de paleta."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QSplitter,
                               QVBoxLayout, QWidget)

from ...tools import edit, ops, render
from ..canvas import SpriteCanvas
from ..context import AppContext
from ...i18n import tr


class PaletteGrid(QWidget):
    """16×16 células; clique seleciona, Shift+clique estende, duplo clique edita."""
    selectionChanged = Signal(int, int)
    colorEdited = Signal(int, object)

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
        i = self._index(e.position())
        cur = QColor(*self.colors[i]) if i < len(self.colors) else QColor(0, 0, 0)
        c = QColorDialog.getColor(cur, self, tr("Color {0}").format(i))
        if c.isValid():
            self.colorEdited.emit(i, (c.red(), c.green(), c.blue()))


class PaletteEditor(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.slot = QComboBox()
        self.slot.currentIndexChanged.connect(self.refresh)
        self.grid = PaletteGrid()
        self.grid.colorEdited.connect(self.on_color)
        self.grid.selectionChanged.connect(self.on_sel)
        self.sel_label = QLabel(tr("Selection: 0"))
        self.sel_label.setObjectName("dim")
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(self.slot)
        lv.addWidget(self.grid, 1)
        lv.addWidget(self.sel_label)

        self.preview = SpriteCanvas()
        self.preview.show_axis = False
        self.preview.center_image = True
        self.preview_sprite = QComboBox()
        self.preview_sprite.currentIndexChanged.connect(self.show_preview)
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.addWidget(QLabel(tr("Preview")))
        mv.addWidget(self.preview_sprite)
        mv.addWidget(self.preview, 1)

        # ferramentas
        var = QGroupBox(tr("New variant (hue/saturation/brightness)"))
        self.hue = QSpinBox(); self.hue.setRange(-180, 180); self.hue.setSuffix("°")
        self.sat = QDoubleSpinBox(); self.sat.setRange(0, 3); self.sat.setSingleStep(0.1); self.sat.setValue(1)
        self.bri = QDoubleSpinBox(); self.bri.setRange(0, 3); self.bri.setSingleStep(0.1); self.bri.setValue(1)
        self.target = QSpinBox(); self.target.setRange(1, 12); self.target.setValue(2)
        f = QFormLayout(var)
        f.addRow(tr("Hue"), self.hue)
        f.addRow(tr("Saturation"), self.sat)
        f.addRow(tr("Brightness"), self.bri)
        f.addRow(tr("Save to slot"), self.target)
        keep = QLabel(tr("Colors selected in the grid are kept (e.g. skin)."))
        keep.setObjectName("dim")
        keep.setWordWrap(True)
        f.addRow(keep)
        b = QPushButton(tr("Create variant"))
        b.clicked.connect(self.make_variant)
        f.addRow(b)

        tools = QGroupBox(tr("Tools"))
        tv = QVBoxLayout(tools)
        for text, fn in [(tr("Gradient on the selection…"), self.gradient), (tr("Grayscale (selection)"), lambda: self.effect("grayscale")),
                         (tr("Invert (selection)"), lambda: self.effect("invert")),
                         (tr("Sort by tones"), self.sort), (tr("Clear repeated colors"), self.dedupe),
                         (tr("Move selection ← (with remap)"), lambda: self.shift(-1)),
                         (tr("Move selection → (with remap)"), lambda: self.shift(1)),
                         (tr("RGB adjustment on the selection…"), self.rgb_adjust),
                         (tr("Optimize (clear unused colors)"), self.optimize),
                         (tr("Generate images of all palettes…"), self.gen_images),
                         (tr("Fit an image to this palette…"), self.adapt_image),
                         (tr("Import .act…"), self.import_act), (tr("Export .act…"), self.export_act)]:
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            tv.addWidget(btn)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(var)
        rv.addWidget(tools)
        rv.addStretch()

        split = QSplitter()
        for w in (left, mid, right):
            split.addWidget(w)
        split.setSizes([420, 420, 300])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(split)

        # métodos e não lambdas: assim o Qt desliga os sinais quando o diálogo do editor é destruído
        ctx.characterChanged.connect(self.load)
        ctx.themeChanged.connect(self._theme_changed)
        ctx.modelChanged.connect(self._model_changed)
        self.preview.set_theme(ctx.theme)

    def _theme_changed(self) -> None:
        self.preview.set_theme(self.ctx.theme)

    def _model_changed(self, part: str) -> None:
        if part == "sprites":
            QTimer.singleShot(0, self, lambda: self.load(keep=True))

    @property
    def char(self):
        return self.ctx.char

    def load(self, keep: bool = False) -> None:
        if not self.char:
            return
        cur = self.slot.currentData() if keep else None
        self.slot.blockSignals(True)
        self.slot.clear()
        for p in self.char.sprites.palettes:
            if p.group == 1:
                self.slot.addItem(tr("Palette 1,{0} ({1} colors)").format(p.item, len(p.colors)), p.item)
        self.slot.blockSignals(False)
        if cur is not None:
            i = self.slot.findData(cur)
            self.slot.setCurrentIndex(max(i, 0))
        # a lista de sprites da prévia é refeita sempre: um sprite apagado deixava a prévia
        # apontando para ele e a recarga quebrava ("sprite 9000,1 não existe")
        old = self.preview_sprite.currentData() if keep else None
        self.preview_sprite.blockSignals(True)
        self.preview_sprite.clear()
        for s in self.char.sprites.sprites[:400]:
            if not s.is_truecolor:
                self.preview_sprite.addItem(f"{s.group},{s.item}", (s.group, s.item))
        i = self.preview_sprite.findData(old) if old is not None else -1
        self.preview_sprite.setCurrentIndex(i if i >= 0 else max(self.preview_sprite.findData((0, 0)), 0))
        self.preview_sprite.blockSignals(False)
        self.refresh()

    def current_slot(self):
        return self.slot.currentData()

    def refresh(self, *_):
        if not self.char or self.current_slot() is None:
            return
        k = self.char.sprites.find_palette(1, self.current_slot())
        if k < 0:              # a paleta escolhida foi apagada: [-1] mostraria outra sem avisar
            return
        p = self.char.sprites.palettes[k]
        self.grid.set_colors(p.colors)
        self.show_preview()

    def show_preview(self, *_):
        key = self.preview_sprite.currentData()
        if key and self.current_slot() is not None and self.char.sprites.find(*key) is not None:
            img = render.sprite_image(self.char, *key, self.current_slot())
            self.preview.set_image(img)
            self.preview.fit()

    def on_sel(self, a: int, b: int) -> None:
        self.sel_label.setText(tr("Selection: {0}").format(a) if a == b else tr("Selection: {0}–{1}").format(a, b))

    def selection(self) -> list[int]:
        a, b = sorted(self.grid.sel)
        return list(range(a, b + 1))

    # ------------------------------------------------------------ ações

    def _done(self) -> None:
        self.ctx.changed("sprites")

    def on_color(self, i: int, rgb) -> None:
        edit.set_palette_colors(self.char, self.current_slot(), {str(i): list(rgb)})
        self._done()

    def make_variant(self) -> None:
        keep = self.selection() if len(self.selection()) > 1 else []
        ops.make_palette_variant(self.char, self.target.value(), self.hue.value(), self.sat.value(),
                                 self.bri.value(), base=self.current_slot(), keep_indices=keep)
        self._done()
        self.ctx.status.emit(tr("Palette 1,{0} created").format(self.target.value()))

    def gradient(self) -> None:
        sel = self.selection()
        if len(sel) < 2:
            self.ctx.status.emit(tr("Select a range in the grid (Shift+click)"))
            return
        a = QColorDialog.getColor(QColor(*self.grid.colors[sel[0]]), self, tr("Start color"))
        b = QColorDialog.getColor(QColor(*self.grid.colors[sel[-1]]), self, tr("End color")) if a.isValid() else None
        if a.isValid() and b and b.isValid():
            edit.palette_gradient(self.char, self.current_slot(), sel[0], sel[-1], a.name(), b.name())
            self._done()

    def effect(self, kind: str) -> None:
        sel = self.selection()
        edit.palette_effect(self.char, self.current_slot(), kind, sel if len(sel) > 1 else None)
        self._done()

    def sort(self) -> None:
        r = edit.sort_palette_by_tones(self.char, self.current_slot())
        self.ctx.status.emit(tr("Palette sorted; {0} sprites remapped").format(r['sprites_remapped']))
        self._done()

    def dedupe(self) -> None:
        r = edit.clear_duplicate_colors(self.char, self.current_slot())
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
        p = edit._pal(self.char, self.current_slot())
        cols = list(p.colors)
        p.colors = [cols[k] for k in order]
        inverse = list(range(256))
        for new, old in enumerate(order):
            inverse[old] = new
        edit._remap_sprites(self.char, self.char.sprites.palettes.index(p), inverse)
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
        edit.set_palette_colors(self.char, self.current_slot(), cols)
        self._done()

    def optimize(self) -> None:
        r = edit.clear_unused_colors(self.char, self.current_slot())
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
        p = edit._pal(self.char, self.current_slot())
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
        path, _ = QFileDialog.getOpenFileName(self, tr("Import palette"), "", tr("ACT palette (*.act)"))
        if path:
            ops.import_palette(self.char, self.current_slot(), path)
            self._done()

    def export_act(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Export palette"), f"pal{self.current_slot()}.act", tr("ACT palette (*.act)"))
        if path:
            ops.export_palette(self.char, self.current_slot(), path)
            self.ctx.status.emit(tr("Palette saved: {0}").format(path))
