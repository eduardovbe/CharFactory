"""Janela "12 paletas": separa a paleta 1 em faixas de cor, trava as que não mudam (pele,
contorno), gera as paletas 2 a 12 por um esquema e deixa ajustar cada faixa em cada paleta,
com a prévia em várias poses e a tira das 12 cores lado a lado."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..tools import palettes12 as P
from .canvas import pil_to_qimage
from .context import AppContext
from .widgets import form_row, hint_label
from ..i18n import tr

ROLE = Qt.ItemDataRole.UserRole


def _swatch(colors: list, n: int = 8, w: int = 72, h: int = 14) -> QIcon:
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    take = colors[:: max(1, len(colors) // n)][:n] or colors[:1]
    for k, c in enumerate(take):
        p.fillRect(round(k * w / len(take)), 0, round(w / len(take)) + 1, h, QColor(*c))
    p.end()
    return QIcon(pm)


def _pix(img, size: int) -> QPixmap:
    if img is None:
        return QPixmap()
    pm = QPixmap.fromImage(pil_to_qimage(img))
    return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)


class PaletteSetDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        c = ctx.char
        self.setWindowTitle(tr("12 palettes by color bands"))
        self.resize(1180, 720)
        k = c.sprites.find_palette(1, 1)
        if k < 0:
            raise ValueError(tr("the character does not have palette 1,1"))
        self.base = [tuple(x) for x in c.sprites.palettes[k].colors]
        self.rngs = P.ranges(self.base, P.used_indices(c))
        self.poses = P.pose_sprites(c)
        self.adj: dict[int, dict[int, tuple]] = {}       # paleta -> faixa -> (matiz, sat, brilho)
        self.touched: set[int] = set()
        self.slot = 2

        # faixas
        self.faixas = QTreeWidget()
        self.faixas.setHeaderLabels([tr("Band"), tr("Colors"), tr("Locked")])
        self.faixas.setRootIsDecorated(False)
        self.faixas.setIconSize(QSize(72, 14))
        for j, r in enumerate(self.rngs):
            it = QTreeWidgetItem([r["name"], str(len(r["indices"])), ""])
            it.setIcon(0, _swatch([self.base[i] for i in r["indices"]]))
            it.setCheckState(2, Qt.CheckState.Checked if r["locked"] else Qt.CheckState.Unchecked)
            it.setData(0, ROLE, j)
            it.setToolTip(0, tr("indices ") + ", ".join(map(str, r["indices"][:40])))
            self.faixas.addTopLevelItem(it)
        self.faixas.itemChanged.connect(self._lock_changed)
        self.faixas.currentItemChanged.connect(lambda *_: self._load_adjust())
        self.faixas.resizeColumnToContents(0)

        self.scheme = QComboBox()
        for key, text in P.SCHEMES.items():
            self.scheme.addItem(text, key)
        gen = QPushButton(tr("Generate the 11 palettes"))
        gen.setToolTip(tr("Redoes palettes 2 to 12 from the scheme (locked bands stay the same)"))
        gen.clicked.connect(self.generate)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel(tr("<b>Color bands</b> of palette 1")))
        lv.addWidget(self.faixas, 1)
        lv.addWidget(hint_label(tr("Lock what stays the same in all of them (skin, outline, eyes).")))
        lv.addLayout(form_row(tr("Scheme"), self.scheme))
        lv.addWidget(gen)

        # as 12 paletas e as poses da escolhida
        self.strip = QListWidget()
        self.strip.setViewMode(QListWidget.ViewMode.IconMode)
        self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(72, 96))
        self.strip.setFixedHeight(140)
        self.strip.setMovement(QListWidget.Movement.Static)
        for slot in range(1, 13):
            it = QListWidgetItem(str(slot))
            it.setData(ROLE, slot)
            self.strip.addItem(it)
        self.strip.currentItemChanged.connect(self._slot_changed)
        self.poses_row = QHBoxLayout()
        self.pose_labels = []
        for g, i, label in self.poses:
            box = QVBoxLayout()
            pic = QLabel()
            pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pic.setMinimumSize(150, 170)
            cap = QLabel(label)
            cap.setObjectName("dim")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(pic, 1)
            box.addWidget(cap)
            self.poses_row.addLayout(box)
            self.pose_labels.append(pic)
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.addWidget(self.strip)
        self.slot_title = QLabel()
        mv.addWidget(self.slot_title)
        mv.addLayout(self.poses_row, 1)

        # ajuste fino: a faixa escolhida na paleta escolhida
        self.dh = QSpinBox()
        self.dh.setRange(-180, 180)
        self.dh.setSuffix("°")
        self.sm = QDoubleSpinBox()
        self.sm.setRange(0, 3)
        self.sm.setSingleStep(0.1)
        self.bm = QDoubleSpinBox()
        self.bm.setRange(0, 3)
        self.bm.setSingleStep(0.1)
        for w in (self.dh, self.sm, self.bm):
            w.valueChanged.connect(self._adjust_changed)
        self.adjust_box = QWidget()
        av = QVBoxLayout(self.adjust_box)
        av.setContentsMargins(0, 0, 0, 0)
        self.adjust_title = QLabel()
        self.adjust_title.setWordWrap(True)
        av.addWidget(self.adjust_title)
        av.addLayout(form_row(tr("Hue"), self.dh))
        av.addLayout(form_row(tr("Saturation"), self.sm))
        av.addLayout(form_row(tr("Brightness"), self.bm))
        av.addWidget(hint_label(tr("Pick a band on the left and a palette on the strip to adjust only that one.")))
        av.addStretch(1)

        body = QHBoxLayout()
        body.addWidget(left, 3)
        body.addWidget(mid, 7)
        body.addWidget(self.adjust_box, 3)
        buttons = QDialogButtonBox()
        self.write_btn = buttons.addButton(tr("Save the palettes"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(body, 1)
        lay.addWidget(buttons)
        self.refresh_strip()
        self.strip.setCurrentRow(1)
        if self.faixas.topLevelItemCount():
            self.faixas.setCurrentItem(self.faixas.topLevelItem(0))

    # ------------------------------------------------------------ cores

    def colors(self, slot: int) -> list:
        """A paleta `slot` como vai ficar: gerada/ajustada aqui, ou a que o char já tem."""
        if slot == 1:
            return self.base
        if slot in self.touched:
            return P.adjust(self.base, self.rngs, self.adj.get(slot, {}))
        s = self.ctx.char.sprites
        k = s.find_palette(1, slot)
        return [tuple(c) for c in s.palettes[k].colors] if k >= 0 else self.base

    def refresh_strip(self) -> None:
        g, i, _ = self.poses[0] if self.poses else (0, 0, "")
        for row in range(self.strip.count()):
            it = self.strip.item(row)
            slot = it.data(ROLE)
            it.setIcon(QIcon(_pix(P.preview_image(self.ctx.char, g, i, self.colors(slot)), 96)))
            it.setText(f"{slot}" + (" •" if slot in self.touched else ""))

    def refresh_poses(self) -> None:
        cols = self.colors(self.slot)
        for pic, (g, i, _) in zip(self.pose_labels, self.poses):
            pic.setPixmap(_pix(P.preview_image(self.ctx.char, g, i, cols), 170))
        new = tr("generated here, not saved yet") if self.slot in self.touched else \
            ("original" if self.slot == 1 else tr("the one the character already has"))
        self.slot_title.setText(tr("<b>Palette {0}</b> · {1}").format(self.slot, new))

    def _refresh_slot(self) -> None:
        row = self.slot - 1
        g, i, _ = self.poses[0] if self.poses else (0, 0, "")
        it = self.strip.item(row)
        it.setIcon(QIcon(_pix(P.preview_image(self.ctx.char, g, i, self.colors(self.slot)), 96)))
        it.setText(f"{self.slot}" + (" •" if self.slot in self.touched else ""))
        self.refresh_poses()

    # ------------------------------------------------------------ ações

    def generate(self) -> None:
        self.adj = P.scheme(self.scheme.currentData(), self.rngs)
        self.touched = set(self.adj)
        self.refresh_strip()
        self.refresh_poses()
        self._load_adjust()

    def _lock_changed(self, it, col) -> None:
        if col == 2:
            self.rngs[it.data(0, ROLE)]["locked"] = it.checkState(2) == Qt.CheckState.Checked

    def _slot_changed(self, it, _prev=None) -> None:
        if it is None:
            return
        self.slot = it.data(ROLE)
        self.refresh_poses()
        self._load_adjust()

    def _current_range(self) -> int | None:
        it = self.faixas.currentItem()
        return it.data(0, ROLE) if it else None

    def _load_adjust(self) -> None:
        j = self._current_range()
        ok = j is not None and self.slot != 1
        self.adjust_box.setEnabled(ok)
        if j is None:
            return
        dh, sm, bm = self.adj.get(self.slot, {}).get(j, (0, 1.0, 1.0))
        for w, v in ((self.dh, int(dh) if dh <= 180 else int(dh) - 360), (self.sm, sm), (self.bm, bm)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self.adjust_title.setText(tr("<b>{0}</b> in palette {1}").format(self.rngs[j]['name'], self.slot)
                                  if ok else tr("Palette 1 is the original: pick another one on the strip."))

    def _adjust_changed(self, *_) -> None:
        j = self._current_range()
        if j is None or self.slot == 1:
            return
        if self.slot not in self.touched:         # começa da que existe: ajuste em cima do zero
            self.adj[self.slot] = {}
            self.touched.add(self.slot)
        self.adj[self.slot][j] = (self.dh.value(), self.sm.value(), self.bm.value())
        self._refresh_slot()

    def accept(self) -> None:
        if not self.touched:
            return super().reject()
        s = self.ctx.char.sprites
        exists = sorted(n for n in self.touched if s.find_palette(1, n) >= 0)
        if exists and QMessageBox.question(
                self, tr("Save the palettes"),
                tr("Replace the palettes the character already has ({0})?").format(', '.join(map(str, exists)))) \
                != QMessageBox.StandardButton.Yes:
            return
        P.write(self.ctx.char, {n: self.colors(n) for n in sorted(self.touched)})
        self.ctx.changed("sprites")
        self.ctx.status.emit(tr("{0} palette(s) saved: {1}").format(len(self.touched), ', '.join(map(str, sorted(self.touched)))))
        super().accept()
