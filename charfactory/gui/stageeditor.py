"""Editores de projetos que não são personagem.

- Stage: lista de camadas ([BG …]) e de BGCtrlDef, propriedades editáveis, prévia renderizada
  com câmera, tempo e camadas, e o código do .def.
- Os demais .def (storyboard, fight, system, select, fonte): código com árvore de seções.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..formats import mtext
from .canvas import pil_to_qimage
from .context import AppContext
from .editors.code import CodeEdit
from .icons import icon
from .widgets import SidePanel, field_label, form_row, hint_label, tool_bar
from ..i18n import tr

KIND_NAMES = {"stage": tr("Stage"), "storyboard": tr("Storyboard"), "font": tr("Font"), "select": tr("Select screen"),
              "system": tr("System screens"), "fight": tr("Fight HUD and effects"), "text": tr(".def file")}


class _Preview(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 200)
        self._pix = None
        self.drag = None
        self.on_drag = None

    def set_image(self, img) -> None:
        self._pix = QPixmap.fromImage(pil_to_qimage(img))
        self._fit()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        if self._pix is not None:
            self.setPixmap(self._pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.FastTransformation))

    def mousePressEvent(self, e):  # noqa: N802
        self.drag = e.position()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self.drag is not None and self.on_drag and self._pix is not None:
            k = self._pix.width() / max(1, self.pixmap().width()) if self.pixmap() else 1
            d = (e.position() - self.drag) * k
            self.drag = e.position()
            self.on_drag(-d.x(), -d.y())

    def mouseReleaseEvent(self, e):  # noqa: N802
        self.drag = None


class DefWindow(QMainWindow):
    """Base: um .def aberto como código, com salvar e árvore de seções."""

    def __init__(self, ctx: AppContext, path: str, kind: str, parent=None):
        super().__init__(parent)
        self.ctx, self.path, self.kind = ctx, Path(path), kind
        self.resize(1300, 820)
        self.code = CodeEdit(ctx.theme)
        self.code.document().modificationChanged.connect(lambda _: self._title())
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(lambda it, _c: it.data(0, Qt.ItemDataRole.UserRole) and
                                      self.code.goto_line(it.data(0, Qt.ItemDataRole.UserRole)))
        QShortcut(QKeySequence("Ctrl+S"), self, self.save)
        # método e não lambda: a janela é apagada ao fechar e o Qt só desliga sinais de métodos
        ctx.themeChanged.connect(self._theme_changed)

    def _theme_changed(self) -> None:
        self.code.set_theme(self.ctx.theme)

    def _title(self) -> None:
        mod = self.code.document().isModified() or getattr(self, "dirty", False)
        self.setWindowTitle(f"{'● ' if mod else ''}{self.path.name} — {KIND_NAMES.get(self.kind, '.def')} — charfactory")

    def fill_tree(self) -> None:
        self.tree.clear()
        doc = mtext.parse(self.code.toPlainText())
        line = 1 + len(doc.preamble)
        for sec in doc.sections:
            it = QTreeWidgetItem([sec.name])
            it.setData(0, Qt.ItemDataRole.UserRole, line)
            self.tree.addTopLevelItem(it)
            line += len(sec.lines)

    def save(self) -> None:
        raise NotImplementedError


class TextDefWindow(DefWindow):
    """Storyboard, fight.def, system.def, select.def, fontes: edição pelo código."""

    def __init__(self, ctx: AppContext, path: str, kind: str, parent=None):
        super().__init__(ctx, path, kind, parent)
        self.doc = mtext.load(self.path)
        self.code.setPlainText(self.doc.to_text().replace("\r\n", "\n"))
        self.code.document().setModified(False)
        self.code.textChanged.connect(lambda: QTimer.singleShot(400, self.fill_tree))
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(self.code)
        split.setSizes([280, 1000])
        self.setCentralWidget(split)
        self.fill_tree()
        self._title()

    def save(self) -> None:
        new = mtext.parse(self.code.toPlainText(), self.doc.encoding)
        new.newline = self.doc.newline
        bak = self.path.with_suffix(self.path.suffix + ".bak")
        if not bak.exists():
            bak.write_bytes(self.path.read_bytes())
        new.save(self.path)
        self.doc = new
        self.code.document().setModified(False)
        self.statusBar().showMessage(tr("{0} saved").format(self.path.name), 4000)


class StageWindow(DefWindow):
    def __init__(self, ctx: AppContext, path: str, parent=None):
        super().__init__(ctx, path, "stage", parent)
        from ..tools import stage as stagemod
        self.stage = stagemod.Stage(path, ctx.settings.get("mugen_dir") or None)
        self.dirty = False
        self.tick = 0
        self._quiet = False

        # esquerda: camadas e controles
        self.layers = QListWidget()
        self.layers.currentRowChanged.connect(self.on_layer)
        self.layer_tools = tool_bar([
            ("new", tr("New layer"), self.add_layer),
            ("duplicate", tr("Duplicate layer"), self.dup_layer),
            ("delete", tr("Delete layer"), self.del_layer),
            ("up", tr("Move up (draws earlier)"), lambda: self.move_layer(-1)),
            ("down", tr("Move down (draws later)"), lambda: self.move_layer(1)),
        ])
        self.ctrls = QListWidget()
        self.ctrls.itemClicked.connect(self.on_ctrl)
        left_body = QWidget()
        lv = QVBoxLayout(left_body)
        lv.addWidget(self.layer_tools)
        lv.addWidget(hint_label(tr("In drawing order. ▼ behind the fighters (layerno 0), ▲ in front.")))
        lv.addWidget(self.layers, 3)
        lv.addWidget(field_label(tr("Controls ([BGCtrlDef …])")))
        lv.addWidget(self.ctrls, 1)
        left = SidePanel(tr("Layers"), left_body, 260)

        # centro: prévia
        self.preview = _Preview()
        self.preview.on_drag = self.pan
        self.cam_x = QSlider(Qt.Orientation.Horizontal)
        self.cam_y = QSlider(Qt.Orientation.Horizontal)
        self.time = QSlider(Qt.Orientation.Horizontal)
        self.time.setRange(0, 600)
        for s in (self.cam_x, self.cam_y, self.time):
            s.valueChanged.connect(lambda _: self.redraw())
        self.back = QCheckBox(tr("Background (layerno 0)"))
        self.front = QCheckBox(tr("Foreground (layerno 1)"))
        self.players = QCheckBox(tr("P1/P2 and floor"))
        self.solo = QCheckBox(tr("Only the selected layer"))
        for cb in (self.back, self.front, self.players):
            cb.setChecked(True)
        for cb in (self.back, self.front, self.players, self.solo):
            cb.toggled.connect(lambda _: self.redraw())
        self.play = QPushButton(icon("play"), tr(" Play"))
        self.play.setCheckable(True)
        self.play.toggled.connect(self.toggle_play)
        self.timer = QTimer(self)
        self.timer.setInterval(round(1000 / 30))
        self.timer.timeout.connect(lambda: self.time.setValue((self.time.value() + 2) % (self.time.maximum() + 1)))
        grid = QWidget()
        g = QVBoxLayout(grid)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(6)
        self.slider_values = {}
        for label, w in ((tr("Camera X"), self.cam_x), (tr("Camera Y"), self.cam_y), (tr("Tick"), self.time)):
            val = QLabel("0")
            val.setObjectName("counter")
            val.setMinimumWidth(48)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            w.valueChanged.connect(lambda v, lb=val: lb.setText(str(v)))
            self.slider_values[label] = (w, val)
            g.addLayout(form_row(label, w, val, width=76))
        opts = QHBoxLayout()
        opts.setSpacing(14)
        for w in (self.back, self.front, self.players, self.solo, self.play):
            opts.addWidget(w)
        opts.addStretch()
        g.addLayout(opts)
        self.problems = QLabel()
        self.problems.setObjectName("dim")
        self.problems.setWordWrap(True)
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(12, 12, 12, 10)
        cv.setSpacing(10)
        cv.addWidget(self.preview, 1)
        cv.addWidget(grid)
        cv.addWidget(self.problems)

        # direita: propriedades
        self.props = QTableWidget(0, 2)
        self.props.setHorizontalHeaderLabels([tr("Parameter"), tr("Value")])
        self.props.verticalHeader().setVisible(False)
        self.props.setShowGrid(False)
        self.props.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.props.itemChanged.connect(self.on_prop)
        self.prop_tools = tool_bar([
            ("new", tr("New parameter"), self.add_prop),
            ("delete", tr("Delete parameter"), self.del_prop),
        ])
        right_body = QWidget()
        rv = QVBoxLayout(right_body)
        self.sec_label = QLabel()
        self.sec_label.setObjectName("sectionTitle")
        rv.addWidget(self.sec_label)
        rv.addWidget(self.prop_tools)
        rv.addWidget(self.props, 1)
        rv.addWidget(hint_label(tr("start = axis relative to the top center of the screen · delta = how much the camera moves the layer · tile = repeat (1 = infinite) · trans = add, add1, addalpha, sub · layerno = 0 behind, 1 in front of the fighters")))
        right = SidePanel(tr("Properties"), right_body, 280)

        visual = QSplitter()
        visual.addWidget(left)
        visual.addWidget(center)
        visual.addWidget(right)
        visual.setSizes([280, 760, 320])
        visual.setChildrenCollapsible(False)
        code_tab = QSplitter()
        code_tab.addWidget(self.tree)
        code_tab.addWidget(self.code)
        code_tab.setSizes([260, 1000])
        self.tabs = QTabWidget()
        self.tabs.addTab(visual, tr("Stage"))
        self.tabs.addTab(code_tab, tr("Code"))
        self.tabs.currentChanged.connect(self.on_tab)
        self.setCentralWidget(self.tabs)
        self.load_ui()
        l, r, hi, lo = self.stage.camera_bounds()
        self.cam_x.setRange(min(l, 0), max(r, 0))
        self.cam_y.setRange(min(hi, 0), max(lo, 0))
        for w, val in self.slider_values.values():
            val.setText(str(w.value()))
        self.redraw()

    # ------------------------------------------------------------ dados

    def load_ui(self, keep: int | None = None) -> None:
        self.stage.reload()
        self.layers.blockSignals(True)
        self.layers.clear()
        for lay in self.stage.layers:
            what = f"anim {lay.actionno}" if lay.actionno is not None else f"spr {lay.spriteno[0]},{lay.spriteno[1]}"
            it = QListWidgetItem(f"{'▲' if lay.layerno else '▼'} {lay.name}  ·  {lay.type}, {what}")
            it.setToolTip(tr("layerno 1: in front of the fighters") if lay.layerno else tr("layerno 0: behind the fighters"))
            self.layers.addItem(it)
        self.layers.blockSignals(False)
        self.ctrls.clear()
        for sec in self.stage.controls:
            self.ctrls.addItem(sec.name)
        self.problems.setText(" · ".join(self.stage.problems()[:6]))
        if self.layers.count():
            self.layers.setCurrentRow(min(keep or 0, self.layers.count() - 1))
        self._title()

    def current_section(self):
        i = self.layers.currentRow()
        return self.stage.layers[i].section if 0 <= i < len(self.stage.layers) else None

    def on_layer(self, i: int) -> None:
        sec = self.current_section()
        self.show_props(sec)
        self.redraw()

    def on_ctrl(self, item) -> None:
        sec = next((s for s in self.stage.controls if s.name == item.text()), None)
        self.layers.clearSelection()
        self.show_props(sec)

    def show_props(self, sec) -> None:
        self._sec = sec
        self._quiet = True
        self.props.setRowCount(0)
        self.sec_label.setText(f"[{sec.name}]" if sec else "")
        for e in (sec.entries() if sec else []):
            r = self.props.rowCount()
            self.props.insertRow(r)
            k = QTableWidgetItem(e.key)
            k.setFlags(k.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.props.setItem(r, 0, k)
            self.props.setItem(r, 1, QTableWidgetItem(e.value))
        self._quiet = False

    def on_prop(self, item) -> None:
        if self._quiet or item.column() != 1 or not getattr(self, "_sec", None):
            return
        key = self.props.item(item.row(), 0).text()
        self._sec.set(key, item.text())
        self.changed(keep_props=True)

    def add_prop(self) -> None:
        if not getattr(self, "_sec", None):
            return
        key, ok = QInputDialog.getText(self, tr("New parameter"), tr("Name (e.g. velocity, tile, trans, layerno):"))
        if ok and key.strip():
            val, ok = QInputDialog.getText(self, tr("New parameter"), f"{key} =")
            if ok:
                self._sec.set(key.strip(), val)
                self.changed()

    def del_prop(self) -> None:
        r = self.props.currentRow()
        if r >= 0 and getattr(self, "_sec", None):
            self._sec.remove(self.props.item(r, 0).text())
            self.changed()

    def changed(self, keep_props: bool = False) -> None:
        self.dirty = True
        row = self.layers.currentRow()
        sec = getattr(self, "_sec", None)
        self.stage.reload()
        if keep_props:
            self.problems.setText(" · ".join(self.stage.problems()[:6]))
            self.redraw()
            self._title()
            return
        self.load_ui(row)
        if sec is not None and sec not in [l.section for l in self.stage.layers]:
            self.show_props(sec)

    # ------------------------------------------------------------ camadas

    def _layer_index_in_doc(self, sec) -> int:
        return next(i for i, s in enumerate(self.stage.doc.sections) if s is sec)

    def add_layer(self) -> None:
        name, ok = QInputDialog.getText(self, tr("New layer"), tr("Name:"), text=tr("Layer"))
        if not ok:
            return
        sec = self.current_section()
        at = self._layer_index_in_doc(sec) + 1 if sec else None
        self.stage.doc.append_section(f"BG {name}", ["type = normal", "spriteno = 0,0", "start = 0,0",
                                                     "delta = 1,1", "layerno = 0", ""], index=at)
        self.changed()

    def dup_layer(self) -> None:
        import copy
        sec = self.current_section()
        if sec:
            new = copy.deepcopy(sec)
            i = self._layer_index_in_doc(sec)
            self.stage.doc.sections.insert(i + 1, new)
            self.changed()
            self.layers.setCurrentRow(self.layers.currentRow() + 1)

    def del_layer(self) -> None:
        sec = self.current_section()
        if sec and QMessageBox.question(self, tr("Delete layer"), tr("Delete [{0}]?").format(sec.name)) == QMessageBox.StandardButton.Yes:
            self.stage.doc.remove_section(sec)
            self.changed()

    def move_layer(self, d: int) -> None:
        i = self.layers.currentRow()
        lays = self.stage.layers
        j = i + d
        if not (0 <= i < len(lays) and 0 <= j < len(lays)):
            return
        secs = self.stage.doc.sections
        a, b = self._layer_index_in_doc(lays[i].section), self._layer_index_in_doc(lays[j].section)
        secs[a], secs[b] = secs[b], secs[a]
        self.changed()
        self.layers.setCurrentRow(j)

    # ------------------------------------------------------------ prévia

    def pan(self, dx: float, dy: float) -> None:
        self.cam_x.setValue(int(self.cam_x.value() + dx))
        self.cam_y.setValue(int(self.cam_y.value() + dy))

    def toggle_play(self, on: bool) -> None:
        self.play.setText(tr("⏸ Pause") if on else tr("▶ Play"))
        (self.timer.start if on else self.timer.stop)()

    def redraw(self) -> None:
        layers = tuple(n for n, cb in ((0, self.back), (1, self.front)) if cb.isChecked())
        sel = self.layers.currentRow() if self.layers.currentRow() >= 0 else None
        try:
            img = self.stage.render(self.cam_x.value(), self.cam_y.value(), self.time.value(), layers,
                                    self.players.isChecked(), selected=sel,
                                    only=sel if self.solo.isChecked() else None)
        except Exception as e:  # noqa: BLE001
            self.problems.setText(tr("Preview: {0}").format(e))
            return
        self.preview.set_image(img)

    # ------------------------------------------------------------ código e gravação

    def on_tab(self, i: int) -> None:
        if i == 1:              # visual -> código
            self.stage.anims.to_text()
            self.code.setPlainText(self.stage.doc.to_text().replace("\r\n", "\n"))
            self.code.document().setModified(False)
            self.fill_tree()
        elif self.code.document().isModified():     # código -> visual
            self.apply_code()

    def apply_code(self) -> None:
        enc, nl = self.stage.doc.encoding, self.stage.doc.newline
        self.stage.doc = mtext.parse(self.code.toPlainText(), enc)
        self.stage.doc.newline = nl
        self.stage._sff = None
        self.code.document().setModified(False)
        self.changed()

    def save(self) -> None:
        if self.tabs.currentIndex() == 1 and self.code.document().isModified():
            self.apply_code()
        bak = self.path.with_suffix(self.path.suffix + ".bak")
        if not bak.exists():
            bak.write_bytes(self.path.read_bytes())
        self.stage.save()
        self.dirty = False
        self._title()
        self.statusBar().showMessage(tr("{0} saved").format(self.path.name), 4000)

    def closeEvent(self, e):  # noqa: N802
        if self.dirty or self.code.document().isModified():
            r = QMessageBox.question(self, tr("Stage"), tr("Save the changes?"),
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                     QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                e.ignore()
                return
            if r == QMessageBox.StandardButton.Save:
                self.save()
        e.accept()


def open_window(ctx: AppContext, path: str, kind: str, parent=None) -> QMainWindow:
    w = StageWindow(ctx, path, parent) if kind == "stage" else TextDefWindow(ctx, path, kind, parent)
    w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    w.show()
    return w
