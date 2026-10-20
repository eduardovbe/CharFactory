"""Diálogos: editor de temas, sprite sheet, throw, PalFX, offset viewer, personagem novo, configurações."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..formats import cns
from ..tools import generate, ops, render
from . import theme as themes
from .canvas import pil_to_qimage
from .context import AppContext
from .widgets import field_label
from ..i18n import tr

# ======================================================================= temas


class ThemeEditor(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Theme editor"))
        self.resize(1040, 680)
        self.ctx = ctx
        self.original = ctx.theme
        self.work: themes.Theme | None = None

        self.list = QListWidget()
        self.list.currentTextChanged.connect(self.pick)
        # duplicar e excluir à vista; importar/exportar (uso raro) num menu
        lb = QHBoxLayout()
        lb.setSpacing(6)
        new_btn = QPushButton(tr("Duplicate"))
        new_btn.setToolTip(tr("New theme from the selected one"))
        new_btn.clicked.connect(self.new_from)
        self.del_btn = QPushButton(tr("Remove"))
        self.del_btn.setObjectName("danger")
        self.del_btn.clicked.connect(self.delete)
        more = QPushButton(tr("More"))
        more_menu = QMenu(more)
        more_menu.addAction(tr("Import colorset (.xml)…"), self.import_colorset)
        more_menu.addAction(tr("Import theme (.json)…"), self.import_json)
        more_menu.addAction(tr("Export theme (.json)…"), self.export_json)
        more.setMenu(more_menu)
        for b in (new_btn, self.del_btn, more):
            b.setAutoDefault(False)
            lb.addWidget(b)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel(tr("Themes")))
        lv.addWidget(self.list, 1)
        lv.addLayout(lb)

        self.name = QLineEdit()
        self.buttons: dict[tuple[str, str], QPushButton] = {}
        self.styles: dict[str, tuple[QCheckBox, QCheckBox]] = {}
        ui_box = QGroupBox(tr("Interface"))
        ug = QGridLayout(ui_box)
        ug.setVerticalSpacing(4)
        ug.setColumnStretch(0, 1)
        for r, (k, label) in enumerate(themes.UI_KEYS.items()):
            ug.setRowMinimumHeight(r, 30)
            ug.addWidget(QLabel(label), r, 0)
            ug.addWidget(self._color_button("ui", k), r, 1)
        cs_box = QGroupBox(tr("Canvas and code (colorset)"))
        cg = QGridLayout(cs_box)
        cg.setVerticalSpacing(4)
        cg.setHorizontalSpacing(12)
        cg.setColumnStretch(0, 1)
        for r, (k, label) in enumerate(themes.COLORSET_KEYS.items()):
            cg.setRowMinimumHeight(r, 30)
            cg.addWidget(QLabel(label), r, 0)
            cg.addWidget(self._color_button("cs", k), r, 1)
            if k in ("comment", "block", "number", "string", "keyword", "keyword_value", "block_type",
                     "function", "const", "foreground", "operator", "key_trigger", "label"):
                b, i = QCheckBox(tr("bold")), QCheckBox(tr("italic"))
                b.toggled.connect(lambda v, k=k: self._style(k, "bold", v))
                i.toggled.connect(lambda v, k=k: self._style(k, "italic", v))
                cg.addWidget(b, r, 2)
                cg.addWidget(i, r, 3)
                self.styles[k] = (b, i)
        # linhas com a mesma altura nos dois grupos: a sobra vai para baixo, não entre as linhas
        ug.setRowStretch(len(themes.UI_KEYS), 1)
        cg.setRowStretch(len(themes.COLORSET_KEYS), 1)
        inner = QWidget()
        iv = QHBoxLayout(inner)
        iv.addWidget(ui_box, 1)
        iv.addWidget(cs_box, 2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        hint = QLabel(tr("Changes show up right away in the main window. Built-in themes are saved as a copy."))
        hint.setObjectName("dim")
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        nf = QFormLayout()
        nf.addRow(tr("Name"), self.name)
        rv.addLayout(nf)
        rv.addWidget(scroll, 1)
        rv.addWidget(hint)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        bb.button(QDialogButtonBox.StandardButton.Save).setText(tr("Save and use"))
        bb.button(QDialogButtonBox.StandardButton.Close).setText(tr("Close"))
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        top = QHBoxLayout()
        top.addWidget(left, 1)
        top.addWidget(right, 3)
        lay = QVBoxLayout(self)
        lay.addLayout(top, 1)
        lay.addWidget(bb)
        self.reload_list(ctx.theme.name)

    def _color_button(self, layer: str, key: str) -> QPushButton:
        b = QPushButton()
        b.setFixedSize(64, 22)
        b.clicked.connect(lambda: self._edit_color(layer, key))
        self.buttons[(layer, key)] = b
        return b

    def reload_list(self, select: str | None = None) -> None:
        self.ctx.themes = themes.load_all()
        self.list.blockSignals(True)
        self.list.clear()
        for name, t in self.ctx.themes.items():
            it = QListWidgetItem(themes.display_name(t) + ("" if t.builtin else "  ★"))
            it.setData(Qt.ItemDataRole.UserRole, name)     # o nome de verdade; o texto segue o idioma
            self.list.addItem(it)
        self.list.blockSignals(False)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == select:
                self.list.setCurrentRow(i)
                return
        self.list.setCurrentRow(0)

    def _current_name(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else ""

    def pick(self, _text: str = "") -> None:
        name = self._current_name()
        if name not in self.ctx.themes:
            return
        self.work = copy.deepcopy(self.ctx.themes[name])
        self.del_btn.setEnabled(not self.work.builtin)
        self.del_btn.setToolTip(tr("Built-in themes cannot be removed") if self.work.builtin else "")
        self.name.setText(self.work.name)
        self.refresh_buttons()
        self.ctx.apply_theme_object(self.work)

    def refresh_buttons(self) -> None:
        for (layer, key), b in self.buttons.items():
            h = self.work.ui.get(key, "#000000") if layer == "ui" else self.work.c(key).hex
            b.setStyleSheet(f"background: {h}; border: 1px solid {self.work.ui.get('border', '#888')}; border-radius: 6px;")
            b.setToolTip(h)
        for k, (bb, ii) in self.styles.items():
            for w, v in ((bb, self.work.c(k).bold), (ii, self.work.c(k).italic)):
                w.blockSignals(True)
                w.setChecked(v)
                w.blockSignals(False)

    def _edit_color(self, layer: str, key: str) -> None:
        cur = self.work.ui.get(key, "#000000") if layer == "ui" else self.work.c(key).hex
        c = QColorDialog.getColor(QColor(cur), self, key)
        if not c.isValid():
            return
        if layer == "ui":
            self.work.ui[key] = c.name()
        else:
            spec = self.work.colorset.get(key) or themes.ColorSpec((0, 0, 0))
            self.work.colorset[key] = themes.ColorSpec((c.red(), c.green(), c.blue()), spec.bold, spec.italic)
        self.refresh_buttons()
        self.ctx.apply_theme_object(self.work)

    def _style(self, key: str, attr: str, value: bool) -> None:
        spec = self.work.colorset.get(key) or themes.ColorSpec((128, 128, 128))
        setattr(spec, attr, value)
        self.work.colorset[key] = spec
        self.ctx.apply_theme_object(self.work)

    def new_from(self) -> None:
        if self.work:
            self.work = copy.deepcopy(self.work)
            self.work.name = self.work.name + tr(" (copy)")
            self.work.builtin = False
            self.name.setText(self.work.name)

    def delete(self) -> None:
        name = self._current_name()
        t = self.ctx.themes.get(name)
        if not t or t.builtin:
            QMessageBox.information(self, tr("Remove"), tr("Built-in themes cannot be removed."))
            return
        if t.path and QMessageBox.question(self, tr("Remove"), tr("Remove the theme {0}?").format(name)) == QMessageBox.StandardButton.Yes:
            Path(t.path).unlink(missing_ok=True)
            self.reload_list()

    def import_colorset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Import colorset"), "", tr("Colorset (*.xml)"))
        if path:
            t = themes.import_colorset(path)
            t.save()
            self.reload_list(t.name)

    def import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Import theme"), "", tr("Theme (*.json)"))
        if path:
            t = themes.from_json(json.loads(Path(path).read_text("utf-8")))
            t.save()
            self.reload_list(t.name)

    def export_json(self) -> None:
        if not self.work:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Export theme"), f"{self.work.name}.json", tr("Theme (*.json)"))
        if path:
            Path(path).write_text(json.dumps(self.work.to_json(), indent=1, ensure_ascii=False), "utf-8")

    def save(self) -> None:
        if not self.work:
            return
        name = self.name.text().strip() or tr("My theme")
        builtin = self.ctx.themes.get(name)
        if builtin is not None and builtin.builtin:
            name += tr(" (custom)")
        self.work.name = name
        self.work.builtin = False
        self.work.path = None
        self.work.save()
        self.ctx.set_theme(name)
        self.accept()

    def reject(self) -> None:
        self.ctx.apply_theme_object(self.ctx.themes.get(self.ctx.settings["theme"], self.original))
        super().reject()


# ======================================================================= sprite sheet


class SheetDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Slice sprite sheet"))
        self.resize(1000, 700)
        self.ctx = ctx
        self.result_data = None
        self.path = QLineEdit()
        browse = QPushButton(tr("Choose…"))
        browse.clicked.connect(self.browse)
        self.mode = QComboBox()
        self.mode.addItem(tr("Automatic (connected pixels)"), "auto")
        self.mode.addItem(tr("Fixed grid"), "grid")
        self.group = QSpinBox(); self.group.setRange(0, 99999)
        self.start = QSpinBox(); self.start.setRange(0, 99999)
        self.bg = QLineEdit(); self.bg.setPlaceholderText(tr("auto (#rrggbb)"))
        self.tol = QSpinBox(); self.tol.setRange(0, 255); self.tol.setValue(8)
        self.merge = QSpinBox(); self.merge.setRange(0, 64); self.merge.setValue(4)
        self.min_area = QSpinBox(); self.min_area.setRange(1, 100000); self.min_area.setValue(12)
        self.axis = QComboBox()
        for k, label in [("feet", tr("Feet + row floor")), ("bottom-center", tr("Bottom center")), ("center", tr("Center"))]:
            self.axis.addItem(label, k)
        self.per_row = QCheckBox(tr("One group per row"))
        self.cw = QSpinBox(); self.cw.setRange(0, 4096); self.cw.setSpecialValueText("—")
        self.ch = QSpinBox(); self.ch.setRange(0, 4096); self.ch.setSpecialValueText("—")
        self.cols = QSpinBox(); self.cols.setRange(0, 256); self.cols.setSpecialValueText("—")
        self.rows = QSpinBox(); self.rows.setRange(0, 256); self.rows.setSpecialValueText("—")
        self.palette = QComboBox()
        for k, label in [("character", tr("Convert to the character's palette")), ("main", tr("Use as the main palette (1,1)")),
                         ("own", tr("Keep the sheet's own palette"))]:
            self.palette.addItem(label, k)
        self.action = QSpinBox(); self.action.setRange(-1, 99999999); self.action.setSpecialValueText(tr("don't create"))
        self.action.setValue(-1)
        self.ftime = QSpinBox(); self.ftime.setRange(1, 999); self.ftime.setValue(5)

        f = QFormLayout()
        row = QHBoxLayout(); row.addWidget(self.path, 1); row.addWidget(browse)
        f.addRow(tr("Image"), row)
        f.addRow(tr("Mode"), self.mode)
        f.addRow(tr("Group / 1st number"), self._pair(self.group, self.start))
        f.addRow(tr("Background color"), self.bg)
        f.addRow(tr("Tolerance"), self.tol)
        f.addRow(tr("Merge pieces up to (px)"), self.merge)
        f.addRow(tr("Minimum area (px)"), self.min_area)
        f.addRow(tr("Axis"), self.axis)
        f.addRow("", self.per_row)
        f.addRow(tr("Grid: cell W×H"), self._pair(self.cw, self.ch))
        f.addRow(tr("Grid: columns×rows"), self._pair(self.cols, self.rows))
        f.addRow(tr("Palette on import"), self.palette)
        f.addRow(tr("Create animation no."), self.action)
        f.addRow(tr("Time per frame"), self.ftime)
        preview_btn = QPushButton(tr("Show preview"))
        preview_btn.clicked.connect(self.run_preview)
        import_btn = QPushButton(tr("Slice and import into the character"))
        import_btn.clicked.connect(self.run_import)
        import_btn.setDefault(True)
        self.summary = QLabel()
        self.summary.setObjectName("dim")
        self.summary.setWordWrap(True)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addLayout(f)
        lv.addWidget(preview_btn)
        lv.addWidget(import_btn)
        lv.addWidget(self.summary)
        lv.addStretch()
        self.preview = QLabel(tr("The preview shows up here"))
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sc = QScrollArea()
        sc.setWidget(self.preview)
        sc.setWidgetResizable(True)
        lay = QHBoxLayout(self)
        lay.addWidget(left, 2)
        lay.addWidget(sc, 3)

    @staticmethod
    def _pair(a, b) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(a, 1)
        h.addWidget(b)
        return w

    def browse(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("Sprite sheet"), "", tr("Images (*.png *.gif *.bmp *.pcx *.jpg)"))
        if p:
            self.path.setText(p)

    def _run(self, out_dir: Path) -> dict:
        from ..tools import sheet
        grid = {}
        if self.mode.currentData() == "grid":
            grid = dict(cell_width=self.cw.value() or None, cell_height=self.ch.value() or None,
                        columns=self.cols.value() or None, rows=self.rows.value() or None)
        return sheet.slice_sheet(self.path.text(), out_dir, self.group.value(), self.start.value(),
                                 self.mode.currentData(), self.bg.text().strip() or None, self.tol.value(),
                                 None, self.merge.value(), self.min_area.value(), self.axis.currentData(),
                                 256, self.per_row.isChecked(), **grid)

    def _out_dir(self) -> Path:
        base = Path(self.ctx.char.dir if self.ctx.char else Path.home()) / "_sheets"
        return base / Path(self.path.text()).stem

    def run_preview(self) -> dict | None:
        if not self.path.text():
            return None
        try:
            r = self._run(self._out_dir())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Sprite sheet"), str(e))
            return None
        pix = QPixmap(r["preview"])
        self.preview.setPixmap(pix)
        self.summary.setText(tr("{0} sprites in {1} row(s) · background {2} · {3} colors ({4})").format(r['count'], r['rows'], r['background'], r['palette_colors'], tr("exact") if r['palette_exact'] else tr("reduced"))
                             + ("\n⚠ " + "; ".join(r["warnings"]) if r["warnings"] else ""))
        return r

    def run_import(self) -> None:
        if not self.ctx.char:
            QMessageBox.information(self, tr("Sprite sheet"), tr("Open a character first."))
            return
        r = self.run_preview()
        if not r:
            return
        act = self.action.value() if self.action.value() >= 0 else None
        res = ops.import_sheet(self.ctx.char, r["csv"], self.palette.currentData(), act, self.ftime.value())
        self.result_data = res
        self.ctx.changed("sprites")
        if act is not None:
            self.ctx.changed("anims")
        self.ctx.status.emit(tr("{0} sprites imported").format(res['imported']) + (tr("; animation {0} created").format(act) if act is not None else ""))
        self.accept()


# ======================================================================= throw


class ThrowDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Throw creator"))
        self.resize(560, 480)
        self.ctx = ctx
        self.sd = QSpinBox(); self.sd.setRange(0, 99999999); self.sd.setValue(800)
        self.p1 = QSpinBox(); self.p1.setRange(0, 99999999); self.p1.setValue(810)
        self.p2 = QSpinBox(); self.p2.setRange(0, 99999999); self.p2.setValue(820)
        self.dmg = QSpinBox(); self.dmg.setRange(0, 9999); self.dmg.setValue(80)
        self.vx = QDoubleSpinBox(); self.vx.setRange(-50, 50); self.vx.setValue(-4)
        self.vy = QDoubleSpinBox(); self.vy.setRange(-50, 50); self.vy.setValue(-6)
        self.button = QLineEdit("y")
        self.binds = QTableWidget(3, 4)
        self.binds.setHorizontalHeaderLabels([tr("P1 frame"), "X", "Y", tr("Priority")])
        self.binds.verticalHeader().hide()
        self.binds.horizontalHeader().setStretchLastSection(True)
        self.binds.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for r, row in enumerate([(1, 25, 0, ""), (2, 30, -20, "1"), (3, 20, -40, "")]):
            for c, v in enumerate(row):
                self.binds.setItem(r, c, QTableWidgetItem(str(v)))
        add = QPushButton(tr("Add position"))
        add.setAutoDefault(False)
        add.setToolTip(tr("New row: where P2 stands on one more frame of P1's animation"))
        add.clicked.connect(lambda: self.binds.insertRow(self.binds.rowCount()))
        f = QFormLayout()
        f.setHorizontalSpacing(14)
        f.setVerticalSpacing(8)
        f.addRow(tr("Base statedef"), self.sd)
        f.addRow(tr("P1 animation"), self.p1)
        f.addRow(tr("P2 animation"), self.p2)
        self.p1.setToolTip(tr("Action of P1 throwing"))
        self.p2.setToolTip(tr("Action of P2 being thrown"))
        f.addRow(tr("Damage"), self.dmg)
        f.addRow(tr("Release velocity (X, Y)"), SheetDialog._pair(self.vx, self.vy))
        f.addRow(tr("Button"), self.button)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Generate and apply"))
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addLayout(f)
        head = QHBoxLayout()
        head.addWidget(field_label(tr("P2 position on each P1 frame")), 1)
        head.addWidget(add)
        lay.addSpacing(6)
        lay.addLayout(head)
        lay.addWidget(self.binds)
        lay.addWidget(bb)

    def apply(self) -> None:
        binds = []
        for r in range(self.binds.rowCount()):
            cells = [self.binds.item(r, c).text().strip() if self.binds.item(r, c) else "" for c in range(4)]
            if cells[0]:
                binds.append({"elem": int(cells[0]), "x": int(cells[1] or 0), "y": int(cells[2] or 0),
                              "sprpriority": int(cells[3]) if cells[3] else None})
        g = generate.throw_states(self.sd.value(), self.p1.value(), self.p2.value(), binds,
                                  self.dmg.value(), (self.vx.value(), self.vy.value()),
                                  command=self.button.text().strip() or "y")
        c = self.ctx.char
        for n, sd in cns.loads(g["states"]).statedefs.items():
            ops.set_statedef_text(c, n, sd.text())
        if -1 in c.all_statedefs(include_common=False):
            ops.add_controller(c, -1, g["cmd_trigger"])
        self.ctx.changed("states")
        self.ctx.changed("commands")
        self.ctx.status.emit(tr("Throw created: statedefs {0}").format(g['statedefs']))
        self.accept()


# ======================================================================= PalFX


class PalFXDialog(QDialog):
    def __init__(self, ctx: AppContext, presets: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("PalFX editor"))
        self.resize(760, 520)
        self.ctx = ctx
        self.preset = QComboBox()
        self.preset.addItem(tr("(none)"), None)
        for d in [p for p in [presets] if p and p.exists()]:
            for x in sorted(d.glob("*.xml")):
                self.preset.addItem(x.stem, str(x))
        self.preset.currentIndexChanged.connect(self.load_preset)
        self.spins = {}
        f = QFormLayout()
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        f.addRow(tr("Preset"), self.preset)
        for key, n, lo, hi, default in [("add", 3, -255, 255, 0), ("mul", 3, 0, 512, 256), ("sinadd", 4, -255, 255, 0)]:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            self.spins[key] = []
            for i in range(n):
                s = QSpinBox()
                s.setRange(0 if (key == "sinadd" and i == 3) else lo, 600 if i == 3 else hi)
                s.setValue(default)
                s.valueChanged.connect(self.update_preview)
                h.addWidget(s)
                self.spins[key].append(s)
            f.addRow({"add": tr("Add color (add)"), "mul": tr("Multiply (mul)"), "sinadd": tr("Pulse color (sinadd)")}[key], row)
            row.setToolTip({"add": tr("R, G, B added to each pixel"), "mul": tr("R, G, B multiplied (256 = no change)"),
                            "sinadd": tr("R, G, B added as a wave; the 4th field is the period in ticks")}[key])
        self.color = QSpinBox(); self.color.setRange(0, 256); self.color.setValue(256)
        self.color.valueChanged.connect(self.update_preview)
        self.invert = QCheckBox(tr("Invert colors (invertall)"))
        self.invert.toggled.connect(self.update_preview)
        self.time = QSpinBox(); self.time.setRange(-1, 9999); self.time.setValue(10)
        self.tick = QSpinBox(); self.tick.setRange(0, 600)
        self.tick.valueChanged.connect(self.update_preview)
        f.addRow(tr("Saturation (color)"), self.color)
        f.addRow("", self.invert)
        f.addRow(tr("Duration (time)"), self.time)
        f.addRow(tr("Preview tick"), self.tick)
        from .editors.code import CodeEdit
        self.out = CodeEdit(ctx.theme)
        self.out.setReadOnly(True)
        self.img = QLabel()
        self.img.setObjectName("previewFrame")
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setMinimumSize(280, 260)
        left = QWidget()
        left.setObjectName("plain")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addLayout(f)
        lv.addWidget(field_label(tr("Generated controller")))
        lv.addWidget(self.out, 1)
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(left, 1)
        body.addWidget(self.img, 1)
        copy_btn = QPushButton(tr("Copy"))
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.out.toPlainText()))
        insert_btn = QPushButton(tr("Insert into editor"))
        insert_btn.setProperty("primary", True)
        insert_btn.setToolTip(tr("Inserts the PalFX at the cursor of the States editor"))
        insert_btn.clicked.connect(self.insert)
        insert_btn.setEnabled(hasattr(parent, "insert_code"))
        close_btn = QPushButton(tr("Close"))
        close_btn.clicked.connect(self.reject)
        for b in (copy_btn, insert_btn, close_btn):
            b.setAutoDefault(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(copy_btn)
        buttons.addWidget(close_btn)
        buttons.addWidget(insert_btn)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)
        lay.addLayout(body, 1)
        lay.addLayout(buttons)
        self.update_preview()

    def insert(self) -> None:
        self.parent().insert_code(self.out.toPlainText().rstrip() + "\n")
        self.accept()

    def values(self) -> dict:
        return {"add": [s.value() for s in self.spins["add"]], "mul": [s.value() for s in self.spins["mul"]],
                "sinadd": [s.value() for s in self.spins["sinadd"]], "color": self.color.value(),
                "invertall": self.invert.isChecked()}

    def load_preset(self) -> None:
        path = self.preset.currentData()
        if not path:
            return
        p = generate.load_palfx_preset(path)
        for key in ("add", "mul", "sinadd"):
            for s, v in zip(self.spins[key], p[key]):
                s.setValue(v)
        self.color.setValue(p["color"])
        self.invert.setChecked(p["invertall"])

    def update_preview(self, *_):
        v = self.values()
        self.out.setPlainText(generate.palfx_controller(0, self.time.value(), v["add"], v["mul"], v["sinadd"],
                                                        v["color"], v["invertall"]))
        c = self.ctx.char
        if c and c.sprites.sprites:
            s = c.sprites.find(0, 0) or c.sprites.sprites[0]
            img = render.apply_palfx(render.sprite_image(c, s.group, s.item), v["add"], v["mul"], v["sinadd"],
                                     v["color"], v["invertall"], self.tick.value())
            img = img.resize((img.width * 2, img.height * 2), 0)
            self.img.setPixmap(QPixmap.fromImage(pil_to_qimage(img)))


# ======================================================================= offset viewer


class OffsetPreview(QWidget):
    """Tela da prévia: mostra a imagem ajustada à área; arrastar move a animação 2."""
    dragged = Signal(int, int)  # deslocamento em pixels do personagem

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.image: QImage | None = None
        self._last: QPointF | None = None
        self._rest = QPointF(0, 0)

    def set_image(self, img: QImage) -> None:
        self.image = img
        self.update()

    def _scale(self) -> float:
        if not self.image:
            return 1.0
        return max(0.25, min(self.width() / self.image.width(), self.height() / self.image.height()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(24, 24, 30))
        if self.image:
            k = self._scale()
            w, h = self.image.width() * k, self.image.height() * k
            p.drawImage(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.image)

    def mousePressEvent(self, e):
        self._last = e.position()
        self._rest = QPointF(0, 0)

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        d = (e.position() - self._last) / self._scale() + self._rest
        dx, dy = int(d.x()), int(d.y())
        self._rest = QPointF(d.x() - dx, d.y() - dy)
        self._last = e.position()
        if dx or dy:
            self.dragged.emit(dx, dy)

    def mouseReleaseEvent(self, _):
        self._last = None


class OffsetViewerDialog(QDialog):
    """Offset viewer do FFS: distância entre os eixos de duas animações, em coordenadas locais.

    A animação 1 tem o eixo azul; a 2 tem o eixo verde e pode ser arrastada na prévia (ou movida
    com as setas). O resultado vira `pos = x, y` para Explod, Helper, Projectile, TargetBind…"""

    RESOLUTIONS = [(320, 240), (640, 480), (960, 720), (1280, 720), (1920, 1080)]

    def __init__(self, ctx: AppContext, insert=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Offset viewer"))
        self.resize(1040, 620)
        self.ctx = ctx
        self.insert = insert
        c = ctx.char
        numbers = sorted(n for n, a in c.anims.actions.items() if a.frames)
        self.res = QComboBox()
        local = render.localcoord(c)
        for wh in [local] + [r for r in self.RESOLUTIONS if r != local]:
            self.res.addItem(f"{wh[0]} × {wh[1]}" + ("  (localcoord)" if wh == local else ""), wh)
        self.anim: list[QComboBox] = []
        self.frame: list[QSpinBox] = []
        for _ in range(2):
            cb = QComboBox()
            cb.setEditable(True)
            for n in numbers:
                cb.addItem(str(n), n)
            self.anim.append(cb)
            self.frame.append(QSpinBox())
        if 0 in numbers:
            self.anim[0].setCurrentIndex(numbers.index(0))
        self.x, self.y = QSpinBox(), QSpinBox()
        for sb in (self.x, self.y):
            sb.setRange(-9999, 9999)
        self.x.setValue(40)
        self.y.setValue(-40)
        self.flip_h, self.flip_v = QCheckBox(tr("Flip horizontally")), QCheckBox(tr("Flip vertically"))
        self.pos_label = QLabel()
        self.pos_label.setObjectName("sectionTitle")
        self.preview = OffsetPreview()
        self.preview.dragged.connect(lambda dx, dy: (self.x.setValue(self.x.value() + dx),
                                                     self.y.setValue(self.y.value() + dy)))
        for w in (self.res, *self.anim):
            w.currentIndexChanged.connect(self.update_preview)
        for w in (*self.frame, self.x, self.y):
            w.valueChanged.connect(self.update_preview)
        for w in (self.flip_h, self.flip_v):
            w.toggled.connect(self.update_preview)

        f = QFormLayout()
        f.addRow(tr("Resolution"), self.res)
        f.addRow(tr("Animation 1 (blue axis)"), self._pair(self.anim[0], self.frame[0]))
        f.addRow("X", self.x)
        f.addRow("Y", self.y)
        f.addRow(tr("Animation 2 (green axis)"), self._pair(self.anim[1], self.frame[1]))
        f.addRow("", self.flip_h)
        f.addRow("", self.flip_v)
        copy_btn = QPushButton(tr("Copy position"))
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.pos_text()))
        ins_btn = QPushButton(tr("Insert into editor"))
        ins_btn.setProperty("primary", True)
        ins_btn.setEnabled(insert is not None)
        ins_btn.clicked.connect(self.do_insert)
        close_btn = QPushButton(tr("Close"))
        close_btn.clicked.connect(self.accept)
        hint = QLabel(tr("Drag the preview or use the arrows (Shift = 10 px) to move animation 2. \"frame\" picks the frame shown for each animation."))
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        left = QWidget()
        left.setObjectName("plain")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addLayout(f)
        lv.addWidget(self.pos_label)
        lv.addWidget(hint)
        lv.addStretch()
        # botões numa linha embaixo, à direita, com um primário só (como os outros diálogos)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for b in (copy_btn, close_btn, ins_btn):
            b.setAutoDefault(False)
            buttons.addWidget(b)
        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(left, 0)
        body.addWidget(self.preview, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)
        lay.addLayout(body, 1)
        lay.addLayout(buttons)
        self.update_preview()

    @staticmethod
    def _pair(combo: QComboBox, frame: QSpinBox) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(combo, 1)
        h.addWidget(QLabel(tr("frame")))
        h.addWidget(frame)
        return w

    def anim_number(self, i: int) -> int | None:
        try:
            return int(self.anim[i].currentText())
        except ValueError:
            return None

    def pos_text(self) -> str:
        return f"pos = {self.x.value()}, {self.y.value()}"

    def do_insert(self) -> None:
        self.insert(self.pos_text())
        self.ctx.status.emit(tr("Inserted: {0}").format(self.pos_text()))

    def keyPressEvent(self, e):
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        d = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
             Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}.get(e.key())
        if d and not isinstance(self.focusWidget(), (QSpinBox, QComboBox, QLineEdit)):
            self.x.setValue(self.x.value() + d[0])
            self.y.setValue(self.y.value() + d[1])
            return
        super().keyPressEvent(e)

    def update_preview(self, *_):
        c = self.ctx.char
        n1, n2 = self.anim_number(0), self.anim_number(1)
        if n1 is None or n2 is None or c.anims.get(n1) is None or c.anims.get(n2) is None:
            self.pos_label.setText(tr("Pick two existing animations"))
            return
        for i, n in enumerate((n1, n2)):
            self.frame[i].setMaximum(max(0, len(c.anims.get(n).frames) - 1))
        try:
            img = render.render_offset(c, n1, n2, self.x.value(), self.y.value(), self.frame[0].value(),
                                       self.frame[1].value(), self.flip_h.isChecked(), self.flip_v.isChecked(),
                                       resolution=self.res.currentData(), label=False)
        except ValueError as e:
            self.pos_label.setText(str(e))
            return
        self.preview.set_image(pil_to_qimage(img))
        self.pos_label.setText(self.pos_text())


# ======================================================================= paleta de comandos


def fuzzy_score(query: str, text: str) -> int | None:
    """Pontuação de busca aproximada: as letras da busca aparecem em ordem no texto.
    Maior é melhor; None quando não casa. Início de palavra e sequência contínua valem mais."""
    q, t = query.lower().strip(), text.lower()
    if not q:
        return 0
    if q in t:
        k = t.index(q)
        end = k + len(q)
        whole = end == len(t) or not t[end].isalnum()  # "anim 20" acerta "anim 20 ·", não "anim 200"
        return 1000 + (50 if whole else 0) - k * 2 - len(t) // 8
    score, pos, run = 0, 0, 0
    for ch in q:
        if ch == " ":
            continue
        k = t.find(ch, pos)
        if k < 0:
            return None
        run = run + 1 if k == pos else 0
        score += 10 + run * 6 + (8 if k == 0 or not t[k - 1].isalnum() else 0)
        pos = k + 1
    return score - len(t) // 8


class CommandPalette(QDialog):
    """Paleta de comandos (F1): busca qualquer ação dos menus e salta para animações, estados,
    sprites e sons pelo número ou nome."""

    LIMIT = 80

    def __init__(self, entries: list[tuple[str, str, object]], parent=None):
        """entries: (texto, detalhe à direita — atalho ou tipo, função sem argumentos)."""
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.entries = entries
        from PySide6.QtWidgets import QFrame
        frame = QFrame()
        frame.setObjectName("palette")
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("Type a command, or: anim 200, state 1000, sprite 9000, sound 5"))
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(self.run)
        self.edit.textChanged.connect(self.refilter)
        self.edit.installEventFilter(self)
        hint = QLabel(tr("↑↓ navigate   Enter run   Esc close"))
        hint.setObjectName("dim")
        hint.setContentsMargins(14, 2, 14, 8)
        v = QVBoxLayout(frame)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.edit)
        v.addWidget(self.list, 1)
        v.addWidget(hint)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        self.resize(640, 440)
        if parent is not None:
            g = parent.geometry()
            self.move(g.x() + (g.width() - self.width()) // 2, g.y() + 90)
        self.refilter("")

    def refilter(self, text: str) -> None:
        self.list.clear()
        scored = []
        for i, (label, detail, fn) in enumerate(self.entries):
            sc = fuzzy_score(text, f"{label} {detail}")
            if sc is not None:
                scored.append((-sc, i))
        for _, i in sorted(scored)[:self.LIMIT]:
            label, detail, fn = self.entries[i]
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setToolTip(label)
            self.list.addItem(it)
            # rótulo à esquerda, atalho/tipo esmaecido à direita
            row = QWidget()
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 0, 10, 0)
            h.addWidget(QLabel(label), 1)
            if detail:
                d = QLabel(detail)
                d.setObjectName("kbd" if any(k in detail for k in ("Ctrl", "Alt", "Shift", "F")) and len(detail) < 16
                                else "dim")
                h.addWidget(d)
            it.setSizeHint(QSize(0, 34))
            self.list.setItemWidget(it, row)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, e):  # noqa: N802
        from PySide6.QtCore import QEvent
        if obj is self.edit and e.type() == QEvent.Type.KeyPress:
            k = e.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 8, Qt.Key.Key_PageUp: -8}[k]
                r = max(0, min(self.list.count() - 1, self.list.currentRow() + step))
                self.list.setCurrentRow(r)
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run(self.list.currentItem())
                return True
        return super().eventFilter(obj, e)

    def run(self, item) -> None:
        if item is None:
            return
        fn = self.entries[item.data(Qt.ItemDataRole.UserRole)][2]
        self.accept()
        fn()


# ======================================================================= recursos não usados


class UnusedDialog(QDialog):
    """Sprites, animações e sons que nada referencia, com ir até o item e apagar em lote."""

    def __init__(self, ctx: AppContext, goto, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
        from .icons import icon
        self._item_cls = QTreeWidgetItem
        self.setWindowTitle(tr("Unused resources"))
        self.resize(560, 600)
        self.ctx = ctx
        self.goto = goto
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemActivated.connect(self.on_item)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.dynamic = QLabel()
        self.dynamic.setObjectName("dim")
        self.dynamic.setWordWrap(True)
        delete = QPushButton(icon("delete", "error"), tr("Delete selected"))
        delete.setObjectName("danger")
        delete.setAutoDefault(False)  # ação destrutiva nunca é o botão padrão do Enter
        delete.setEnabled(False)      # só com algo selecionado
        delete.clicked.connect(self.delete_selected)
        self.tree.itemSelectionChanged.connect(lambda: delete.setEnabled(bool(self.tree.selectedItems())))
        close = QPushButton(tr("Close"))
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(delete)
        row.addStretch()
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.summary)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.dynamic)
        lay.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        from ..tools import usage
        from .icons import icon
        self.tree.clear()
        r = usage.unused_resources(self.ctx.char)
        groups = [("sprites", tr("Sprites"), "sprites", [(f"{g},{i}", ("sprite", g, i)) for g, i in r["sprites"]]),
                  ("animations", tr("Animations"), "anims", [(tr("Action {0}").format(n), ("anim", n, 0)) for n in r["animations"]]),
                  ("sounds", tr("Sounds"), "sounds", [(f"{g},{n}", ("sound", g, n)) for g, n in r["sounds"]])]
        for _, title, ic, items in groups:
            top = self._item_cls([f"{title}  ·  {len(items)}"])
            top.setIcon(0, icon(ic, toggles=False))
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for text, data in items:
                child = self._item_cls([text])
                child.setData(0, Qt.ItemDataRole.UserRole, data)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(len(items) <= 40)
        total = len(r["sprites"]) + len(r["animations"]) + len(r["sounds"])
        head = (tr("No unreferenced resources.") if total == 0 else
                tr("1 unreferenced resource.") if total == 1 else tr("{0} unreferenced resources.").format(total))
        self.summary.setText(tr("<b>{0}</b> Double-click opens the item in the editor; select several to delete them at once.").format(head))
        dyn = ""
        if r["dynamic_references"]:
            n = r["dynamic_references"]
            ex = f"<code>{r['dynamic_examples'][0]}</code>"
            dyn = (tr("1 reference is computed at game time (e.g. {0}) and cannot be checked here: "
                      "review before deleting. ").format(ex) if n == 1 else
                   tr("{0} references are computed at game time (e.g. {1}) and cannot be checked here: "
                      "review before deleting. ").format(n, ex))
        self.dynamic.setText(dyn + r["notes"].capitalize() + ".")

    def on_item(self, item, _col=0) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.goto(*data)

    def delete_selected(self) -> None:
        from ..tools import edit, ops
        sel = [it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()]
        sel = [d for d in sel if d]
        if not sel or QMessageBox.question(self, tr("Delete"), tr("Delete {0} resource(s)? You can undo it by reloading from disk before saving.").format(len(sel))) \
                != QMessageBox.StandardButton.Yes:
            return
        c = self.ctx.char
        parts = set()
        for kind, a, b in sel:
            if kind == "sprite":
                ops.remove_sprite(c, a, b)
                parts.add("sprites")
            elif kind == "anim":
                ops.remove_action(c, a)
                parts.add("anims")
            else:
                edit.remove_sound(c, a, b)
                parts.add("sounds")
        for p in parts:
            self.ctx.changed(p)
        self.ctx.status.emit(tr("{0} resource(s) deleted").format(len(sel)))
        self.refresh()


# ======================================================================= diversos


class NewCharacterDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("New character"))
        self.ctx = ctx
        self.created: Path | None = None
        self.name = QLineEdit(tr("New Fighter"))
        self.author = QLineEdit()
        self.folder = QLineEdit(str(Path(ctx.settings.get("mugen_dir") or Path.home()) / "chars" / "novo_lutador"))
        self.template = QComboBox()
        self.template.addItem(tr("Empty (skeleton)"), None)
        if ctx.char:
            self.template.addItem(tr("Copy of {0}").format(ctx.char.name), str(ctx.char.def_path))
        root = Path(ctx.settings.get("mugen_dir") or "")
        for d in sorted((root / "chars").glob("*/*.def")) if ctx.settings.get("mugen_dir") else []:
            self.template.addItem(tr("Copy of {0}/{1}").format(d.parent.name, d.name), str(d))
        self.setMinimumWidth(620)
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(lambda: self.folder.setText(
            QFileDialog.getExistingDirectory(self, tr("Character folder"), self.folder.text()) or self.folder.text()))
        f = QFormLayout(self)
        f.setContentsMargins(20, 18, 20, 16)
        f.setHorizontalSpacing(14)
        f.setVerticalSpacing(10)
        f.addRow(tr("Name"), self.name)
        f.addRow(tr("Author"), self.author)
        f.addRow(tr("Folder"), SheetDialog._pair(self.folder, browse))
        f.addRow(tr("Template"), self.template)
        hint = QLabel(tr("The folder is created with .def, .sff, .air, .cns and .cmd. A template copies another character's files."))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow("", hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Create"))
        bb.accepted.connect(self.create)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def create(self) -> None:
        try:
            self.created = generate.new_character(self.folder.text(), self.name.text(), self.template.currentData(),
                                                  self.author.text())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("New character"), str(e))
            return
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.mugen = QLineEdit(ctx.settings.get("mugen_dir", ""))
        b = QPushButton("…")
        b.clicked.connect(lambda: self.mugen.setText(QFileDialog.getExistingDirectory(self, tr("MUGEN folder")) or self.mugen.text()))
        self.presets = QLineEdit(ctx.settings.get("presets_dir", ""))
        b2 = QPushButton("…")
        b2.clicked.connect(lambda: self.presets.setText(QFileDialog.getExistingDirectory(self, tr("Presets folder")) or self.presets.text()))
        s = ctx.settings
        self.p2 = QLineEdit(s.get("run_p2", ""))
        self.p2.setPlaceholderText(tr("empty = the character itself (e.g. kfm)"))
        self.stage = QLineEdit(s.get("run_stage", ""))
        self.stage.setPlaceholderText(tr("relative to stages/ (empty = one that exists)"))
        self.p2_ai = QComboBox()
        self.p2_ai.addItem(tr("Human (idle)"), 0)
        for lvl in range(1, 9):
            self.p2_ai.addItem(tr("AI level {0}").format(lvl), lvl)
        self.p2_ai.setCurrentIndex(max(0, min(8, int(s.get("run_p2_ai") or 0))))
        self.autosave = QSpinBox()
        self.autosave.setRange(0, 120)
        self.autosave.setSuffix(" min")
        self.autosave.setSpecialValueText(tr("off"))
        self.autosave.setValue(int(s.get("autosave_min", 5)))
        self.watch = QCheckBox(tr("Warn when a file changes outside the app (charfactory CLI, another editor)"))
        self.watch.setChecked(bool(s.get("file_watcher", True)))
        self.ffmpeg = QLineEdit(s.get("ffmpeg", ""))
        self.ffmpeg.setPlaceholderText(tr("empty = search the PATH (to import OGG/MP3/FLAC)"))
        b4 = QPushButton("…")
        b4.clicked.connect(lambda: self.ffmpeg.setText(
            QFileDialog.getOpenFileName(self, "ffmpeg", "", tr("Program (*.exe)"))[0] or self.ffmpeg.text()))
        self.image_editor = QLineEdit(s.get("image_editor", ""))
        self.image_editor.setPlaceholderText(tr("Aseprite.exe… (empty = default program for PNG)"))
        b3 = QPushButton("…")
        b3.clicked.connect(lambda: self.image_editor.setText(
            QFileDialog.getOpenFileName(self, tr("Image editor"), "", tr("Program (*.exe)"))[0] or self.image_editor.text()))
        self.undo_limit = QSpinBox()
        self.undo_limit.setRange(5, 500)
        self.undo_limit.setValue(int(s.get("undo_limit", 100)))
        from .. import i18n
        self.language = QComboBox()
        for code, name in i18n.LANGUAGES.items():
            self.language.addItem(name, code)
        self._lang0 = s.get("language") or i18n.language()
        self.language.setCurrentIndex(max(0, self.language.findData(self._lang0)))
        self.language_changed = False
        self.setWindowTitle(tr("Options"))
        self.setMinimumWidth(680)

        def group(title: str, rows: list) -> QGroupBox:
            # rótulos com a mesma largura em todos os grupos: os campos começam na mesma coluna
            g = QGroupBox(title)
            f = QFormLayout(g)
            f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            f.setHorizontalSpacing(14)
            f.setVerticalSpacing(8)
            f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for r in rows:
                if isinstance(r, str):
                    lbl = QLabel(r)
                    lbl.setObjectName("hint")
                    lbl.setWordWrap(True)
                    spacer = QLabel("")
                    spacer.setFixedWidth(130)
                    f.addRow(spacer, lbl)
                elif isinstance(r, tuple):
                    lbl = QLabel(r[0])
                    lbl.setFixedWidth(130)
                    f.addRow(lbl, r[1])
                else:
                    f.addRow(r)
            return g
        for btn in (b, b2, b3, b4):
            btn.setText(tr("Browse…"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)
        lay.addWidget(group(tr("Interface"), [
            (tr("Language"), self.language), tr("The new language is used when the program opens again.")]))
        lay.addWidget(group(tr("Folders and programs"), [
            (tr("MUGEN"), SheetDialog._pair(self.mugen, b)),
            (tr("Presets"), SheetDialog._pair(self.presets, b2)),
            tr("Optional: PalFX presets (common/engines/mugen/palFX) and colorsets (common/colorsets) come from there."),
            (tr("Image editor"), SheetDialog._pair(self.image_editor, b3)),
            ("ffmpeg", SheetDialog._pair(self.ffmpeg, b4))]))
        lay.addWidget(group(tr("Run in the engine (Ctrl+R)"), [
            (tr("Opponent (P2)"), self.p2), (tr("Stage"), self.stage), (tr("P2 AI"), self.p2_ai)]))
        lay.addWidget(group(tr("Editing"), [
            (tr("Automatic backup"), self.autosave), (tr("Undo limit"), self.undo_limit), self.watch]))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def save(self) -> None:
        s = self.ctx.settings
        s["mugen_dir"] = self.mugen.text().strip()
        s["presets_dir"] = self.presets.text().strip()
        s["run_p2"] = self.p2.text().strip()
        s["run_stage"] = self.stage.text().strip()
        s["run_p2_ai"] = self.p2_ai.currentData()
        s["autosave_min"] = self.autosave.value()
        s["file_watcher"] = self.watch.isChecked()
        s["ffmpeg"] = self.ffmpeg.text().strip()
        s["undo_limit"] = self.undo_limit.value()
        s["image_editor"] = self.image_editor.text().strip()
        s["language"] = self.language.currentData()
        self.language_changed = s["language"] != self._lang0
        s.save()
        self.ctx.settingsChanged.emit()
        self.accept()


class TextDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 520)
        t = QPlainTextEdit(text)
        t.setReadOnly(True)
        copy_btn = QPushButton(tr("Copy"))
        copy_btn.clicked.connect(lambda: (t.selectAll(), t.copy()))
        lay = QVBoxLayout(self)
        lay.addWidget(t)
        lay.addWidget(copy_btn)


# ferramentas novas (arquivo próprio para não inchar este)
from .dialogs_tools import (BackupDiffDialog, CalculatorDialog, CommandTesterDialog,  # noqa: E402,F401
                            EntryOrganizerDialog, ImportFromProjectDialog, TransplantDialog)
