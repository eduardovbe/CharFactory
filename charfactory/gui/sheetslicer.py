"""Recortar sprite sheet: a sheet ocupa a janela, com zoom e arrasto, e as caixas de cada sprite
se editam na própria prévia (mover, redimensionar pelas alças, desenhar uma nova, apagar e
arrastar o eixo). A detecção roda sozinha a cada ajuste, sem gravar nada até importar."""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..tools import ops
from ..tools import sheet as SH
from . import icons
from .canvas import pil_to_qimage
from .context import AppContext
from .widgets import hint_label
from ..i18n import tr

HANDLE = 4          # metade do quadradinho das alças, em pixels de tela
NEAR = 7            # distância (tela) que conta como "em cima" do eixo
BOX = QColor(0, 230, 120)
SELECTED = QColor(80, 170, 255)
AXIS = QColor(255, 220, 0)
HANDLES = ("tl", "t", "tr", "r", "br", "b", "bl", "l")
CURSORS = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
           "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
           "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
           "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor}


def _key(slices: list) -> list:
    return [(s.left, s.top, s.right, s.bottom, s.axis_x, s.axis_y) for s in slices]


class SheetView(QWidget):
    """A sheet com zoom (roda do mouse) e arrasto (botão do meio ou espaço + arrastar).
    Clicar numa caixa a seleciona; arrastar move, as alças redimensionam e a cruz amarela é o
    eixo. Arrastar num lugar vazio desenha uma caixa nova."""
    edited = Signal(object)      # lista de recortes de antes da edição (para desfazer)
    selection = Signal(int)
    zoomed = Signal(float)
    hovered = Signal(int, int)
    picked = Signal(int, int)    # conta-gotas: pixel clicado

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(480, 360)
        self.image = None        # QImage da sheet
        self.clean = None        # a mesma sem o fundo
        self.show_clean = False
        self.slices: list = []
        self.sel = -1
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self.picking = False
        self._drag = None        # (tipo, índice, alça, ponto inicial na tela, recorte original)
        self._before = None
        self._new = None         # caixa nova sendo desenhada (imagem)
        self._space = False
        self._fit_pending = True

    # ------------------------------------------------------------ coordenadas
    def to_screen(self, x: float, y: float) -> QPointF:
        return QPointF(x * self.zoom + self.pan.x(), y * self.zoom + self.pan.y())

    def to_image(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - self.pan.x()) / self.zoom, (p.y() - self.pan.y()) / self.zoom)

    def _srect(self, s) -> QRectF:
        return QRectF(self.to_screen(s.left, s.top), QSizeF((s.right - s.left) * self.zoom,
                                                            (s.bottom - s.top) * self.zoom))

    def _saxis(self, s) -> QPointF:
        return self.to_screen(s.left + s.axis_x, s.top + s.axis_y)

    @staticmethod
    def _handles(r: QRectF) -> list[tuple[str, QPointF]]:
        cx, cy = r.center().x(), r.center().y()
        pts = {"tl": (r.left(), r.top()), "t": (cx, r.top()), "tr": (r.right(), r.top()),
               "r": (r.right(), cy), "br": (r.right(), r.bottom()), "b": (cx, r.bottom()),
               "bl": (r.left(), r.bottom()), "l": (r.left(), cy)}
        return [(k, QPointF(*pts[k])) for k in HANDLES]

    def _size(self) -> tuple[int, int]:
        return (self.image.width(), self.image.height()) if self.image is not None else (1, 1)

    # ------------------------------------------------------------ dados
    def set_image(self, image, clean) -> None:
        new = self.image is None or image.size() != self.image.size()
        self.image, self.clean = image, clean
        if new:
            self._fit_pending = True
            self.fit()
        self.update()

    def set_slices(self, slices: list, sel: int = -1) -> None:
        self.slices = slices
        self.sel = sel if 0 <= sel < len(slices) else -1
        self.update()

    def select(self, i: int) -> None:
        i = i if 0 <= i < len(self.slices) else -1
        if i != self.sel:
            self.sel = i
            self.selection.emit(i)
            self.update()

    # ------------------------------------------------------------ zoom
    def set_zoom(self, z: float, around: QPointF | None = None) -> None:
        z = max(0.05, min(32.0, z))
        c = around if around is not None else QPointF(self.width() / 2, self.height() / 2)
        img = self.to_image(c)
        self.zoom = z
        self.pan = QPointF(c.x() - img.x() * z, c.y() - img.y() * z)
        self.zoomed.emit(z)
        self.update()

    def fit(self) -> None:
        if self.image is None or self.width() < 10:
            return
        w, h = self._size()
        z = min((self.width() - 24) / w, (self.height() - 24) / h)
        self.zoom = max(0.05, min(8.0, z))
        self.pan = QPointF((self.width() - w * self.zoom) / 2, (self.height() - h * self.zoom) / 2)
        self._fit_pending = False
        self.zoomed.emit(self.zoom)
        self.update()

    def zoom_to_selection(self) -> None:
        if not 0 <= self.sel < len(self.slices):
            return
        s = self.slices[self.sel]
        w, h = s.right - s.left + 16, s.bottom - s.top + 16
        self.zoom = max(0.05, min(16.0, min(self.width() / w, self.height() / h) * 0.8))
        cx, cy = (s.left + s.right) / 2, (s.top + s.bottom) / 2
        self.pan = QPointF(self.width() / 2 - cx * self.zoom, self.height() / 2 - cy * self.zoom)
        self.zoomed.emit(self.zoom)
        self.update()

    def resizeEvent(self, e) -> None:
        if self._fit_pending:
            self.fit()
        super().resizeEvent(e)

    def wheelEvent(self, e) -> None:
        steps = e.angleDelta().y() / 120
        if steps:
            self.set_zoom(self.zoom * (1.25 ** steps), e.position())

    # ------------------------------------------------------------ desenho
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(28, 28, 36))
        if self.image is None:
            p.setPen(QColor(150, 150, 165))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("Choose an image to slice"))
            return
        w, h = self._size()
        area = QRectF(self.to_screen(0, 0), QSizeF(w * self.zoom, h * self.zoom))
        if self.show_clean:
            p.save()
            p.setClipRect(area)
            cell = 8
            x0, y0 = int(area.left()), int(area.top())
            for y in range(max(0, -y0) // cell * cell, int(area.height()) + cell, cell):
                if y0 + y > self.height():
                    break
                for x in range(max(0, -x0) // cell * cell, int(area.width()) + cell, cell):
                    if x0 + x > self.width():
                        break
                    dark = ((x // cell) + (y // cell)) % 2
                    p.fillRect(x0 + x, y0 + y, cell, cell, QColor(70, 70, 80) if dark else QColor(98, 98, 108))
            p.restore()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1)
        p.drawImage(area, self.clean if self.show_clean and self.clean is not None else self.image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        font = QFont(self.font())
        font.setPointSize(8)
        p.setFont(font)
        fm = p.fontMetrics()
        for i, s in enumerate(self.slices):
            r = self._srect(s)
            if not r.intersects(QRectF(self.rect())):
                continue
            sel = i == self.sel
            col = SELECTED if sel else BOX
            if sel:
                fill = QColor(SELECTED)
                fill.setAlpha(40)
                p.fillRect(r, fill)
            p.setPen(QPen(col, 2 if sel else 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(0, 0, -1, -1))
            label = f"{s.group},{s.item}"
            tw, th = fm.horizontalAdvance(label) + 6, fm.height()
            tag = QRectF(r.left(), r.top() - th if r.top() - th > 0 else r.top(), tw, th)
            p.fillRect(tag, col)
            p.setPen(QColor(10, 10, 14))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)
            a = self._saxis(s)
            for c, wd in ((QColor(0, 0, 0), 3), (AXIS, 1)):
                p.setPen(QPen(c, wd))
                p.drawLine(QPointF(a.x() - 6, a.y()), QPointF(a.x() + 6, a.y()))
                p.drawLine(QPointF(a.x(), a.y() - 6), QPointF(a.x(), a.y() + 6))
            if sel:
                p.setPen(QPen(QColor(10, 10, 14), 1))
                p.setBrush(SELECTED)
                for _k, pt in self._handles(r):
                    p.drawRect(QRectF(pt.x() - HANDLE, pt.y() - HANDLE, HANDLE * 2, HANDLE * 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
        if self._new is not None:
            l, t, rr, b = self._new
            p.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(self.to_screen(l, t), QSizeF((rr - l) * self.zoom, (b - t) * self.zoom)))

    # ------------------------------------------------------------ mouse
    def _hit(self, pos: QPointF) -> tuple[str, int, str]:
        if 0 <= self.sel < len(self.slices):
            for name, pt in self._handles(self._srect(self.slices[self.sel])):
                if abs(pos.x() - pt.x()) <= HANDLE + 2 and abs(pos.y() - pt.y()) <= HANDLE + 2:
                    # a cruz do eixo costuma cair na alça de baixo: o eixo vence
                    a = self._saxis(self.slices[self.sel])
                    if abs(pos.x() - a.x()) <= NEAR and abs(pos.y() - a.y()) <= NEAR:
                        return "axis", self.sel, ""
                    return "resize", self.sel, name
        order = ([self.sel] if self.sel >= 0 else []) + [i for i in range(len(self.slices)) if i != self.sel]
        for i in order:
            a = self._saxis(self.slices[i])
            if abs(pos.x() - a.x()) <= NEAR and abs(pos.y() - a.y()) <= NEAR:
                return "axis", i, ""
        inside = [i for i, s in enumerate(self.slices) if self._srect(s).contains(pos)]
        if self.sel in inside:
            return "move", self.sel, ""
        if inside:
            return "move", min(inside, key=lambda i: (self.slices[i].right - self.slices[i].left)
                               * (self.slices[i].bottom - self.slices[i].top)), ""
        return "new", -1, ""

    def _pixel(self, pos: QPointF) -> tuple[int, int]:
        pt = self.to_image(pos)
        w, h = self._size()
        return max(0, min(w, round(pt.x()))), max(0, min(h, round(pt.y())))

    def mousePressEvent(self, e) -> None:
        self.setFocus()
        if self.image is None:
            return
        pos = e.position()
        if e.button() == Qt.MouseButton.MiddleButton or (e.button() == Qt.MouseButton.LeftButton and self._space):
            self._drag = ("pan", -1, "", pos, QPointF(self.pan))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.picking:
            pt = self.to_image(pos)
            w, h = self._size()
            self.picking = False
            self.unsetCursor()
            if 0 <= pt.x() < w and 0 <= pt.y() < h:
                self.picked.emit(int(pt.x()), int(pt.y()))
            return
        kind, i, handle = self._hit(pos)
        self._before = copy.deepcopy(self.slices)
        if kind == "new":
            self.select(-1)
            x, y = self._pixel(pos)
            self._new = (x, y, x, y)
            self._drag = ("new", -1, "", pos, (x, y))
        else:
            self.select(i)
            self._drag = (kind, i, handle, pos, copy.deepcopy(self.slices[i]))
            if kind == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.update()

    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self.image is not None:
            pt = self.to_image(pos)
            self.hovered.emit(int(pt.x()), int(pt.y()))
        if self._drag is None:
            self._hover_cursor(pos)
            return
        kind, i, handle, start, orig = self._drag
        if kind == "pan":
            self.pan = orig + (pos - start)
            self.update()
            return
        w, h = self._size()
        if kind == "new":
            x, y = self._pixel(pos)
            sx, sy = orig
            self._new = (min(sx, x), min(sy, y), max(sx, x), max(sy, y))
            self.update()
            return
        s = self.slices[i]
        if kind == "axis":
            pt = self.to_image(pos)
            s.axis_x, s.axis_y = round(pt.x()) - s.left, round(pt.y()) - s.top
            s.extra["manual_axis"] = True
            self.update()
            return
        dx = round((pos.x() - start.x()) / self.zoom)
        dy = round((pos.y() - start.y()) / self.zoom)
        ax, ay = orig.left + orig.axis_x, orig.top + orig.axis_y   # o eixo fica onde está na sheet
        if kind == "move":
            bw, bh = orig.right - orig.left, orig.bottom - orig.top
            left = max(0, min(w - bw, orig.left + dx))
            top = max(0, min(h - bh, orig.top + dy))
            right, bottom = left + bw, top + bh
        else:
            left, top, right, bottom = orig.left, orig.top, orig.right, orig.bottom
            if "l" in handle:
                left = max(0, min(right - 1, orig.left + dx))
            if "r" in handle:
                right = min(w, max(left + 1, orig.right + dx))
            if "t" in handle:
                top = max(0, min(bottom - 1, orig.top + dy))
            if "b" in handle:
                bottom = min(h, max(top + 1, orig.bottom + dy))
        s.left, s.top, s.right, s.bottom = left, top, right, bottom
        s.axis_x, s.axis_y = ax - left, ay - top
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is None:
            return
        kind = self._drag[0]
        self._drag = None
        self._hover_cursor(e.position())
        if kind == "pan":
            return
        if kind == "new":
            l, t, r, b = self._new
            self._new = None
            if r - l >= 2 and b - t >= 2:
                self.slices.append(SH.Slice(l, t, r, b))
                self.sel = len(self.slices) - 1
                self.selection.emit(self.sel)
            self.update()
        if self._before is not None and (len(self._before) != len(self.slices)
                                         or _key(self._before) != _key(self.slices)):
            self.edited.emit(self._before)
        self._before = None

    def _hover_cursor(self, pos: QPointF) -> None:
        if self.picking:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._space:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        kind, _i, handle = self._hit(pos)
        if kind == "resize":
            self.setCursor(CURSORS[handle])
        elif kind == "axis":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif kind == "move":
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def contextMenuEvent(self, e) -> None:
        kind, i, _h = self._hit(QPointF(e.pos()))
        if i < 0:
            return
        self.select(i)
        m = QMenu(self)
        a_del = m.addAction(icons.icon("delete"), tr("Delete"))
        a_axis = m.addAction(icons.icon("axis"), tr("Automatic axis"))
        a_axis.setEnabled(bool(self.slices[i].extra.get("manual_axis")))
        a_zoom = m.addAction(icons.icon("zoom_sel"), tr("Zoom to selection"))
        got = m.exec(e.globalPos())
        if got is a_del:
            self.remove_selected()
        elif got is a_axis:
            self.auto_axis()
        elif got is a_zoom:
            self.zoom_to_selection()

    # ------------------------------------------------------------ edição
    def remove_selected(self) -> None:
        if not 0 <= self.sel < len(self.slices):
            return
        before = copy.deepcopy(self.slices)
        del self.slices[self.sel]
        self.sel = min(self.sel, len(self.slices) - 1)
        self.selection.emit(self.sel)
        self.edited.emit(before)

    def auto_axis(self) -> None:
        if 0 <= self.sel < len(self.slices):
            before = copy.deepcopy(self.slices)
            self.slices[self.sel].extra.pop("manual_axis", None)
            self.edited.emit(before)

    def nudge(self, dx: int, dy: int) -> None:
        if not 0 <= self.sel < len(self.slices):
            return
        before = copy.deepcopy(self.slices)
        s = self.slices[self.sel]
        w, h = self._size()
        dx = max(-s.left, min(w - s.right, dx))
        dy = max(-s.top, min(h - s.bottom, dy))
        s.left, s.right, s.top, s.bottom = s.left + dx, s.right + dx, s.top + dy, s.bottom + dy
        s.axis_x, s.axis_y = s.axis_x - dx, s.axis_y - dy
        self.edited.emit(before)

    def keyPressEvent(self, e) -> None:
        k = e.key()
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if k == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_selected()
        elif k == Qt.Key.Key_Escape and self.picking:
            self.picking = False
            self.unsetCursor()
        elif k == Qt.Key.Key_Escape and self.sel >= 0:
            self.select(-1)
        elif k == Qt.Key.Key_Left:
            self.nudge(-step, 0)
        elif k == Qt.Key.Key_Right:
            self.nudge(step, 0)
        elif k == Qt.Key.Key_Up:
            self.nudge(0, -step)
        elif k == Qt.Key.Key_Down:
            self.nudge(0, step)
        elif k == Qt.Key.Key_Tab and self.slices:
            self.select((self.sel + 1) % len(self.slices))
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.unsetCursor()
        else:
            super().keyReleaseEvent(e)

    def focusNextPrevChild(self, nxt: bool) -> bool:
        return False if self.slices else super().focusNextPrevChild(nxt)


# ======================================================================= janela


class SheetDialog(QDialog):
    DETECT = ("mode", "bg", "tol", "merge", "min_area", "cw", "ch", "cols", "rows")

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Slice sprite sheet"))
        self.ctx = ctx
        self.result_data = None
        self.det: SH.Detection | None = None
        self._image_path = None
        self.edited = False         # caixas mexidas à mão: a detecção não as refaz sozinha
        self.undo_stack: list = []
        self.redo_stack: list = []
        self._loading = False
        self._spin_dirty = False
        saved = ctx.settings.get("sheet_slicer") or {}

        self.path = QLineEdit()
        browse = QPushButton(tr("Choose…"))
        browse.clicked.connect(self.browse)
        self.mode = QComboBox()
        self.mode.addItem(tr("Automatic (connected pixels)"), "auto")
        self.mode.addItem(tr("Fixed grid"), "grid")
        self.group = QSpinBox(); self.group.setRange(0, 99999)
        self.start = QSpinBox(); self.start.setRange(0, 99999)
        self.bg = QLineEdit(); self.bg.setPlaceholderText(tr("auto (#rrggbb)"))
        self.pick = QToolButton()
        self.pick.setIcon(icons.icon("picker"))
        self.pick.setToolTip(tr("Pick the background color on the sheet"))
        self.pick.clicked.connect(self._start_pick)
        self.tol = QSpinBox(); self.tol.setRange(0, 255); self.tol.setValue(saved.get("tol", 8))
        self.merge = QSpinBox(); self.merge.setRange(0, 64); self.merge.setValue(saved.get("merge", 4))
        self.min_area = QSpinBox(); self.min_area.setRange(1, 100000); self.min_area.setValue(saved.get("min_area", 12))
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
        self.ftime = QSpinBox(); self.ftime.setRange(1, 999); self.ftime.setValue(saved.get("ftime", 5))
        for combo, key in ((self.mode, "mode"), (self.axis, "axis"), (self.palette, "palette")):
            i = combo.findData(saved.get(key))
            if i >= 0:
                combo.setCurrentIndex(i)
        self.per_row.setChecked(bool(saved.get("per_row")))

        # ---- coluna da esquerda: ajustes
        detect_box = QGroupBox(tr("Detection"))
        fd = QFormLayout(detect_box)
        fd.addRow(tr("Mode"), self.mode)
        bg_row = QHBoxLayout(); bg_row.addWidget(self.bg, 1); bg_row.addWidget(self.pick)
        fd.addRow(tr("Background color"), bg_row)
        fd.addRow(tr("Tolerance"), self.tol)
        self.auto_rows = [(tr("Merge pieces up to (px)"), self.merge), (tr("Minimum area (px)"), self.min_area)]
        self.grid_rows = [(tr("Grid: cell W×H"), self._pair(self.cw, self.ch)),
                          (tr("Grid: columns×rows"), self._pair(self.cols, self.rows))]
        for label, w in self.auto_rows + self.grid_rows:
            fd.addRow(label, w)
        self.detect_form = fd
        self.redetect = QPushButton(icons.icon("reload"), tr("Detect again"))
        self.redetect.setToolTip(tr("Find the sprites again with these settings (boxes edited by hand are redone)"))
        self.redetect.clicked.connect(lambda: self.detect_now(force=True))
        fd.addRow(self.redetect)

        num_box = QGroupBox(tr("Numbers"))
        fn = QFormLayout(num_box)
        fn.addRow(tr("Group / 1st number"), self._pair(self.group, self.start))
        fn.addRow("", self.per_row)
        fn.addRow(tr("Axis"), self.axis)

        imp_box = QGroupBox(tr("Import"))
        fi = QFormLayout(imp_box)
        fi.addRow(tr("Palette on import"), self.palette)
        fi.addRow(tr("Create animation no."), self.action)
        fi.addRow(tr("Time per frame"), self.ftime)

        self.summary = QLabel()
        self.summary.setObjectName("dim")
        self.summary.setWordWrap(True)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        row = QHBoxLayout(); row.addWidget(self.path, 1); row.addWidget(browse)
        lv.addWidget(QLabel(tr("Image")))
        lv.addLayout(row)
        lv.addWidget(detect_box)
        lv.addWidget(num_box)
        lv.addWidget(imp_box)
        lv.addWidget(self.summary)
        lv.addStretch()
        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # largura do que está dentro (as legendas mudam de tamanho com a língua), sem cortar campos
        scroll.setFixedWidth(max(360, left.sizeHint().width() + scroll.verticalScrollBar().sizeHint().width() + 4))

        # ---- direita: a sheet
        self.view = SheetView()
        self.view.edited.connect(self._edited)
        self.view.selection.connect(lambda _i: self._show_selected())
        self.view.zoomed.connect(lambda z: self.zoom_label.setText(f"{z * 100:.0f}%"))
        self.view.hovered.connect(lambda x, y: self.pos_label.setText(f"{x}, {y}"))
        self.view.picked.connect(self._picked)

        def tool(name: str, tip: str, slot) -> QToolButton:
            b = QToolButton()
            b.setIcon(icons.icon(name))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            return b
        bar = QHBoxLayout()
        bar.setSpacing(2)
        bar.addWidget(tool("zoom_out", tr("Zoom out (-)"), lambda: self.view.set_zoom(self.view.zoom / 1.25)))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(self.zoom_label)
        bar.addWidget(tool("zoom_in", tr("Zoom in (+)"), lambda: self.view.set_zoom(self.view.zoom * 1.25)))
        bar.addWidget(tool("zoom_1", tr("Actual size (Ctrl+0)"), lambda: self.view.set_zoom(1.0)))
        bar.addWidget(tool("fit", tr("Fit to screen (Ctrl+9)"), self.view.fit))
        bar.addWidget(tool("zoom_sel", tr("Zoom to selection"), self.view.zoom_to_selection))
        bar.addSpacing(8)
        self.hide_bg = QCheckBox(tr("Hide the background"))
        self.hide_bg.setToolTip(tr("Shows what will be cut: the background becomes a checkerboard"))
        self.hide_bg.toggled.connect(self._toggle_bg)
        bar.addWidget(self.hide_bg)
        bar.addSpacing(8)
        self.b_undo = tool("undo", tr("Undo (Ctrl+Z)"), self.undo)
        self.b_redo = tool("redo", tr("Redo (Ctrl+Y)"), self.redo)
        bar.addWidget(self.b_undo)
        bar.addWidget(self.b_redo)
        bar.addStretch()
        self.pos_label = QLabel()
        self.pos_label.setObjectName("dim")
        bar.addWidget(self.pos_label)

        # caixa selecionada
        self.sel_label = QLabel()
        self.sel_label.setMinimumWidth(110)
        self.sx, self.sy, self.sw, self.sh, self.sax, self.say = (QSpinBox() for _ in range(6))
        for sp in (self.sx, self.sy, self.sw, self.sh):
            sp.setRange(0, 99999)
        for sp in (self.sax, self.say):
            sp.setRange(-9999, 99999)
        for sp in (self.sx, self.sy, self.sw, self.sh, self.sax, self.say):
            sp.valueChanged.connect(self._spin_changed)
        self.b_auto_axis = QPushButton(icons.icon("axis"), tr("Automatic axis"))
        self.b_auto_axis.setToolTip(tr("Go back to the axis the Axis option gives"))
        self.b_auto_axis.clicked.connect(self.view.auto_axis)
        self.b_delete = QPushButton(icons.icon("delete"), tr("Delete"))
        self.b_delete.clicked.connect(self.view.remove_selected)
        sel_row = QHBoxLayout()
        sel_row.addWidget(self.sel_label)
        for label, sp in (("X", self.sx), ("Y", self.sy), (tr("W"), self.sw), (tr("H"), self.sh)):
            sel_row.addWidget(QLabel(label))
            sel_row.addWidget(sp)
        sel_row.addSpacing(10)
        sel_row.addWidget(QLabel(tr("Axis")))
        sel_row.addWidget(self.sax)
        sel_row.addWidget(self.say)
        sel_row.addWidget(self.b_auto_axis)
        sel_row.addStretch()
        sel_row.addWidget(self.b_delete)
        self.sel_widget = QWidget()
        self.sel_widget.setLayout(sel_row)
        sel_row.setContentsMargins(0, 0, 0, 0)

        hint = hint_label(tr("Drag on an empty spot to add a box · drag a box to move it and its corners to resize · "
                             "the yellow cross is the axis · Del removes, arrows move 1 px (Shift: 10) · "
                             "the wheel zooms, the middle button (or Space) pans"))

        import_btn = QPushButton(icons.icon("import"), tr("Slice and import into the character"))
        import_btn.clicked.connect(self.run_import)
        import_btn.setDefault(True)
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(hint, 1)
        buttons.addWidget(cancel)
        buttons.addWidget(import_btn)

        right = QVBoxLayout()
        right.addLayout(bar)
        right.addWidget(self.view, 1)
        right.addWidget(self.sel_widget)
        right.addLayout(buttons)
        lay = QHBoxLayout(self)
        lay.addWidget(scroll)
        lay.addLayout(right, 1)

        for k, seq in (("undo", QKeySequence.StandardKey.Undo), ("redo", QKeySequence.StandardKey.Redo)):
            QShortcut(seq, self, activated=getattr(self, k))
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.view.set_zoom(1.0))
        QShortcut(QKeySequence("Ctrl+9"), self, activated=self.view.fit)
        QShortcut(QKeySequence("+"), self, activated=lambda: self.view.set_zoom(self.view.zoom * 1.25))
        QShortcut(QKeySequence("="), self, activated=lambda: self.view.set_zoom(self.view.zoom * 1.25))
        QShortcut(QKeySequence("-"), self, activated=lambda: self.view.set_zoom(self.view.zoom / 1.25))

        # a detecção roda sozinha um instante depois de cada ajuste
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.detect_now)
        self.path.editingFinished.connect(self.timer.start)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.bg.editingFinished.connect(self.timer.start)
        for sp in (self.tol, self.merge, self.min_area, self.cw, self.ch, self.cols, self.rows):
            sp.valueChanged.connect(self.timer.start)
        for sp in (self.group, self.start):
            sp.valueChanged.connect(lambda _v: self._relayout())
        self.per_row.toggled.connect(lambda _v: self._relayout())
        self.axis.currentIndexChanged.connect(lambda _i: self._relayout())

        self._mode_changed(start=False)
        self._show_selected()
        self._update_undo()
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            self.resize(min(1500, int(screen.width() * 0.9)), min(940, int(screen.height() * 0.9)))
        else:
            self.resize(1300, 820)

    @staticmethod
    def _pair(a, b) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(a, 1)
        h.addWidget(b)
        return w

    # ------------------------------------------------------------ ajustes
    def _mode_changed(self, _i=None, start: bool = True) -> None:
        grid = self.mode.currentData() == "grid"
        for label, w in self.auto_rows:
            self.detect_form.setRowVisible(w, not grid)
        for label, w in self.grid_rows:
            self.detect_form.setRowVisible(w, grid)
        if start:
            self.timer.start()

    def browse(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("Sprite sheet"), "", tr("Images (*.png *.gif *.bmp *.pcx *.jpg)"))
        if p:
            self.path.setText(p)
            self.detect_now(force=True)

    def _start_pick(self) -> None:
        if self.view.image is None:
            return
        self.view.picking = True
        self.view.setCursor(Qt.CursorShape.CrossCursor)
        self.view.setFocus()
        self.summary.setText(tr("Click the background on the sheet (Esc cancels)"))

    def _picked(self, x: int, y: int) -> None:
        if self.det is None:
            return
        r, g, b, _a = self.det.rgba.getpixel((x, y))
        self.bg.setText(f"#{r:02x}{g:02x}{b:02x}")
        self.detect_now(force=not self.edited)

    def _toggle_bg(self, on: bool) -> None:
        self.view.show_clean = on
        self.view.update()

    def _grid(self) -> dict:
        if self.mode.currentData() != "grid":
            return {}
        return dict(cell_width=self.cw.value() or None, cell_height=self.ch.value() or None,
                    columns=self.cols.value() or None, rows=self.rows.value() or None)

    # ------------------------------------------------------------ detecção
    def detect_now(self, force: bool = False) -> bool:
        """Acha os sprites de novo. Com caixas editadas à mão, só refaz se pedido."""
        self.timer.stop()
        path = self.path.text().strip()
        if not path or not Path(path).is_file():
            return False
        if self.edited and not force:
            self.summary.setText(tr("The boxes were edited by hand: Detect again applies the new settings "
                                    "(Ctrl+Z brings the edits back)"))
            self.redetect.setDefault(True)
            return False
        try:
            det = SH.detect(path, self.mode.currentData(), self.bg.text().strip() or None, self.tol.value(),
                            None, self.merge.value(), self.min_area.value(), self.axis.currentData(), **self._grid())
        except Exception as e:  # noqa: BLE001
            # nenhum sprite achado: mostra a sheet sem caixas, para ajustar o fundo vendo
            try:
                img = Image.open(path).convert("RGBA")
            except Exception:  # noqa: BLE001
                self.summary.setText("⚠ " + str(e))
                return False
            det = SH.Detection(img, np.zeros((img.height, img.width), bool), [], {})
            self.summary.setText("⚠ " + str(e))
        if self.det is not None and self.edited:
            self._push()
        same_image = self.det is not None and self._image_path == path
        self.det = det
        self._image_path = path
        self.edited = False
        self.redetect.setDefault(False)
        clean = pil_to_qimage(det.clean())
        self.view.set_image(self.view.image if same_image else pil_to_qimage(det.rgba), clean)
        self._relayout(sel=-1)
        if det.slices:
            self._summarize()
        return True

    def run_preview(self) -> bool:
        return self.detect_now(force=True)

    def _relayout(self, sel: int | None = None) -> None:
        """Ordem de leitura, eixos automáticos e números depois de qualquer mudança."""
        if self.det is None:
            return
        cur = self.view.slices[self.view.sel] if sel is None and 0 <= self.view.sel < len(self.view.slices) else None
        slices = SH.reorder(self.det.slices) if self.det.slices else []
        SH.compute_axes(slices, self.det.mask, self.axis.currentData())
        SH.number(slices, self.group.value(), self.start.value(), self.per_row.isChecked())
        self.det.slices = slices
        idx = next((k for k, s in enumerate(slices) if s is cur), -1) if sel is None else sel
        self.view.set_slices(slices, idx)
        self._show_selected()
        if slices:
            self._summarize()

    def _summarize(self) -> None:
        d = self.det
        rows = max(s.row for s in d.slices) + 1
        from ..tools.ops import unique_colors
        colors = len(unique_colors(np.asarray(d.rgba)[d.mask][:, :3])) if d.mask.any() else 0
        big = [s for s in d.slices if (s.right - s.left) * (s.bottom - s.top) > 0.5 * d.mask.size]
        text = tr("{0} sprites in {1} row(s) · background {2} · {3} colors ({4})").format(
            len(d.slices), rows, d.info.get("background", "?"), min(colors, 256),
            tr("exact") if colors <= 255 else tr("reduced"))
        if self.edited:
            text += "\n" + tr("Boxes edited by hand")
        if big:
            text += "\n⚠ " + tr("a cut takes more than half the image: the background may be wrong or there are grid lines (use ignore_colors or mode='grid')")
        self.summary.setText(text)

    # ------------------------------------------------------------ edição e desfazer
    def _push(self, before: list | None = None) -> None:
        self.undo_stack.append((copy.deepcopy(before if before is not None else self.det.slices), self.edited))
        del self.undo_stack[:-100]
        self.redo_stack.clear()
        self._update_undo()

    def _edited(self, before: list) -> None:
        self._push(before)
        self.edited = True
        self._spin_dirty = False
        self._relayout()

    def _restore(self, src: list, dst: list) -> None:
        if not src or self.det is None:
            return
        dst.append((copy.deepcopy(self.det.slices), self.edited))
        slices, self.edited = src.pop()
        self.det.slices = slices
        self._spin_dirty = False
        self._relayout(sel=-1)
        self._update_undo()

    def undo(self) -> None:
        self._restore(self.undo_stack, self.redo_stack)

    def redo(self) -> None:
        self._restore(self.redo_stack, self.undo_stack)

    def _update_undo(self) -> None:
        self.b_undo.setEnabled(bool(self.undo_stack))
        self.b_redo.setEnabled(bool(self.redo_stack))

    def _show_selected(self) -> None:
        i = self.view.sel
        has = 0 <= i < len(self.view.slices)
        self.sel_widget.setEnabled(has)
        self._spin_dirty = False
        self._loading = True
        if has:
            s = self.view.slices[i]
            self.sel_label.setText(tr("Sprite {0},{1}").format(s.group, s.item))
            for sp, v in ((self.sx, s.left), (self.sy, s.top), (self.sw, s.right - s.left),
                          (self.sh, s.bottom - s.top), (self.sax, s.axis_x), (self.say, s.axis_y)):
                sp.setValue(v)
            self.b_auto_axis.setEnabled(bool(s.extra.get("manual_axis")))
        else:
            self.sel_label.setText(tr("No box selected"))
        self._loading = False

    def _spin_changed(self) -> None:
        if self._loading or not 0 <= self.view.sel < len(self.view.slices):
            return
        if not self._spin_dirty:      # uma sequência de cliques nas setas é um passo só
            self._push()
            self._spin_dirty = True
        s = self.view.slices[self.view.sel]
        w, h = self.view._size()
        ax, ay = s.left + s.axis_x, s.top + s.axis_y
        s.left, s.top = min(self.sx.value(), w - 1), min(self.sy.value(), h - 1)
        s.right = min(w, s.left + max(1, self.sw.value()))
        s.bottom = min(h, s.top + max(1, self.sh.value()))
        if self.sender() in (self.sax, self.say):
            s.axis_x, s.axis_y = self.sax.value(), self.say.value()
            s.extra["manual_axis"] = True
        else:
            s.axis_x, s.axis_y = ax - s.left, ay - s.top
        self.edited = True
        dirty = self._spin_dirty
        self._relayout()
        self._spin_dirty = dirty

    # ------------------------------------------------------------ importar
    def _out_dir(self) -> Path:
        base = Path(self.ctx.char.dir if self.ctx.char else Path.home()) / "_sheets"
        return base / Path(self.path.text()).stem

    def _save_settings(self) -> None:
        self.ctx.settings["sheet_slicer"] = {
            "mode": self.mode.currentData(), "tol": self.tol.value(), "merge": self.merge.value(),
            "min_area": self.min_area.value(), "axis": self.axis.currentData(),
            "per_row": self.per_row.isChecked(), "palette": self.palette.currentData(),
            "ftime": self.ftime.value()}

    def run_import(self) -> None:
        if not self.ctx.char:
            QMessageBox.information(self, tr("Sprite sheet"), tr("Open a character first."))
            return
        if self.det is None and not self.detect_now(force=True):
            return
        if self.timer.isActive() and not self.edited:
            self.detect_now()
        if not self.det.slices:
            QMessageBox.warning(self, tr("Sprite sheet"), tr("no sprite found (adjust bg_color/tolerance/min_area)"))
            return
        try:
            r = SH.export(self.det, self._out_dir())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Sprite sheet"), str(e))
            return
        act = self.action.value() if self.action.value() >= 0 else None
        res = ops.import_sheet(self.ctx.char, r["csv"], self.palette.currentData(), act, self.ftime.value())
        self.result_data = res
        self._save_settings()
        self.ctx.changed("sprites")
        if act is not None:
            self.ctx.changed("anims")
        self.ctx.status.emit(tr("{0} sprites imported").format(res['imported']) + (tr("; animation {0} created").format(act) if act is not None else ""))
        self.accept()
