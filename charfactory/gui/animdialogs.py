"""Janelas do editor de animações: caixas automáticas em todos os frames e nova animação a
partir de um grupo de sprites ou vazia (faixa, tempo dos frames e caixas automáticas já na criação)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QLabel, QLineEdit,
                               QVBoxLayout, QWidget)

from ..i18n import tr
from .widgets import form_row, hint_label, spin


class AutoBoxOptions(QWidget):
    """Tipo da caixa (colisão, dano ou as duas) e formato (anatômico ou retângulo)."""

    def __init__(self, allow_none: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("plain")
        self.kind = QComboBox()
        if allow_none:
            self.kind.addItem(tr("No boxes"), None)
        self.kind.addItem(tr("Collision (Clsn2, blue): where the character gets hit"), "collision")
        self.kind.addItem(tr("Damage (Clsn1, red): covers the whole sprite, for projectiles"), "damage")
        self.kind.addItem(tr("Collision and damage"), "both")
        self.shape = QComboBox()
        self.shape.addItem(tr("Anatomical (head, torso, legs)"), "anatomical")
        self.shape.addItem(tr("Rectangle (the sprite area)"), "rect")
        self.kind.currentIndexChanged.connect(lambda _: self.shape.setEnabled(self.kind.currentData() is not None))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(form_row(tr("Boxes"), self.kind))
        lay.addLayout(form_row(tr("Shape"), self.shape))
        self.shape.setEnabled(self.kind.currentData() is not None)

    def values(self) -> tuple[str | None, str]:
        return self.kind.currentData(), self.shape.currentData()


class AutoBoxesDialog(QDialog):
    """Caixas automáticas em todos os frames da animação de uma vez."""

    def __init__(self, n_frames: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Automatic boxes on all frames"))
        self.opts = AutoBoxOptions()
        self.only_empty = QCheckBox(tr("Only frames that have no box of that type yet (keeps the ones made by hand)"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("Generates the boxes of the {0} frames of this animation.").format(n_frames)))
        lay.addWidget(self.opts)
        lay.addWidget(self.only_empty)
        lay.addWidget(hint_label(tr("Ctrl+Z undoes it.")))
        lay.addWidget(bb)


class NewAnimationDialog(QDialog):
    """Nova animação: número, nome, os sprites de um grupo (todos ou uma faixa), o tempo de
    cada frame e caixas automáticas. O grupo é opcional: "Nenhum" (o padrão) cria a animação
    com um frame vazio e desativa a faixa e as caixas, que dependem dos sprites."""

    def __init__(self, char, number: int, group: int | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("New animation"))
        self.char = char
        self.groups = char.sprites.groups()           # {grupo: [itens]}
        self.number = spin(-1, 99999999)
        self.number.setValue(number)
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("optional"))
        self.group = QComboBox()
        self.group.addItem(tr("None"), None)          # padrão: animação vazia, sprites depois
        for g in sorted(self.groups):
            self.group.addItem(tr("Group {0} ({1} sprites)").format(g, len(self.groups[g])), g)
        if group is not None and self.group.findData(group) >= 0:
            self.group.setCurrentIndex(self.group.findData(group))
        self.first, self.last = spin(0, 99999), spin(0, 99999)
        self.time = spin(-1, 9999)
        self.time.setValue(4)
        self.time.setToolTip(tr("Ticks of each frame (60 ticks = 1 second; -1 = infinite)"))
        self.boxes = AutoBoxOptions(allow_none=True)
        self.boxes.kind.setCurrentIndex(1)            # colisão: o que o "Adicionar" fazia antes
        self.summary = QLabel()
        self.summary.setObjectName("dim")
        self.group.currentIndexChanged.connect(self._group_changed)
        for w in (self.first, self.last, self.time, self.number):
            w.valueChanged.connect(self._update)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form_row(tr("Number"), self.number))
        lay.addLayout(form_row(tr("Name"), self.name))
        lay.addLayout(form_row(tr("Sprites"), self.group))
        lay.addLayout(form_row(tr("From index"), self.first, QLabel(tr("to")), self.last))
        lay.addLayout(form_row(tr("Time"), self.time))
        lay.addWidget(self.boxes)
        lay.addWidget(self.summary)
        lay.addWidget(self.bb)
        self._group_changed()

    def _items(self) -> list[int]:
        g = self.group.currentData()
        if g is None:
            return []
        lo, hi = sorted((self.first.value(), self.last.value()))
        return [i for i in sorted(self.groups.get(g, [])) if lo <= i <= hi]

    def _group_changed(self, *_) -> None:
        has_group = self.group.currentData() is not None
        for w in (self.first, self.last, self.boxes):    # sem grupo não há faixa nem caixas
            w.setEnabled(has_group)
        items = sorted(self.groups.get(self.group.currentData(), []))
        for w, v in ((self.first, items[0] if items else 0), (self.last, items[-1] if items else 0)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)
        self._update()

    def _update(self, *_) -> None:
        empty = self.group.currentData() is None
        n = 1 if empty else len(self._items())
        exists = self.number.value() in self.char.anims.numbers()
        t = self.time.value()
        total = tr("infinite") if t < 0 else tr("{0} ticks").format(n * max(t, 1))
        if exists:
            text = tr("Action {0} already exists.").format(self.number.value())
        elif empty:
            text = tr("Empty animation: one blank frame (-1,0) to fill in later.")
        else:
            text = tr("{0} frame(s) · {1}").format(n, total)
        self.summary.setText(text)
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(n > 0 and not exists)

    def result_values(self) -> dict:
        kind, shape = self.boxes.values()
        return {"number": self.number.value(), "name": self.name.text().strip(),
                "group": self.group.currentData(), "items": self._items(), "time": self.time.value(),
                "kind": kind, "shape": shape}
