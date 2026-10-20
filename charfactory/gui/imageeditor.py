"""Editor de imagem embutido: retoca e redesenha sprites sem sair do app.

Pensado para pixel art de MUGEN: trabalha direto nos índices da paleta do sprite (o índice 0 é
a transparência). Ferramentas: seleção retangular, varinha mágica, mover, lápis, borracha, balde,
conta-gotas, linha, retângulo e elipse. Com uma seleção ativa, tudo que pinta fica preso a ela,
e ela pode ser movida (arrastar ou setas), copiada, colada, espelhada e apagada. O menu Imagem
traz contorno, troca de cor, limpeza de pixels soltos, recorte e tamanho da tela (o eixo
acompanha). Onion skin mostra os sprites vizinhos do grupo e PgUp/PgDn passam ao sprite
anterior/seguinte sem fechar a janela. Passar o mouse numa cor da paleta destaca os pixels dela.

Botão esquerdo pinta com a cor principal e o direito com a secundária; Alt+clique pega a cor.
A roda dá zoom no ponto do mouse e o botão do meio (ou espaço+arrastar) move a vista. Sprites
truecolor (PNG32) usam cores RGBA no lugar dos índices.
"""
from __future__ import annotations

from collections import deque

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QColor, QGuiApplication, QImage, QKeySequence,
                           QPainter, QPen, QShortcut)
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from .icons import icon
from ..i18n import tr

ZOOMS = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48]
TOOLS = [("select", "select_rect", tr("Rectangular selection (Shift adds, Ctrl removes)"), "M"),
         ("wand", "wand", tr("Magic wand (Shift adds, Ctrl removes)"), "W"),
         ("move", "move_tool", tr("Move the selection (or the whole figure); Ctrl+drag copies"), "V"),
         ("pencil", "edit", tr("Pencil"), "B"), ("eraser", "eraser", tr("Eraser"), "E"),
         ("fill", "bucket", tr("Paint bucket"), "G"), ("picker", "picker", tr("Eyedropper"), "I"),
         ("line", "line_tool", tr("Line"), "L"), ("rect", "rect_tool", tr("Rectangle (Shift = filled)"), "R"),
         ("ellipse", "ellipse_tool", tr("Ellipse (Shift = filled)"), "O")]
SHAPES = ("line", "rect", "ellipse")
ONION_TINTS = {"prev": (255, 70, 70), "next": (70, 140, 255)}


# ---------------------------------------------------------------- geometria e máscaras

def line_points(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Pixels de uma reta (Bresenham), sem buracos."""
    pts = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        pts.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return pts
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def rect_points(x0: int, y0: int, x1: int, y1: int, filled: bool) -> list[tuple[int, int]]:
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    if filled:
        return [(x, y) for y in range(ya, yb + 1) for x in range(xa, xb + 1)]
    pts = [(x, y) for x in range(xa, xb + 1) for y in (ya, yb)]
    pts += [(x, y) for y in range(ya + 1, yb) for x in (xa, xb)]
    return pts


def ellipse_points(x0: int, y0: int, x1: int, y1: int, filled: bool) -> list[tuple[int, int]]:
    """Elipse inscrita no retângulo (x0, y0)-(x1, y1); o contorno não tem buracos."""
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    w, h = xb - xa + 1, yb - ya + 1
    if w <= 2 or h <= 2:
        m = np.ones((h, w), bool)
    else:
        ys, xs = np.mgrid[0:h, 0:w]
        rx, ry = w / 2, h / 2
        m = ((xs + 0.5 - rx) / rx) ** 2 + ((ys + 0.5 - ry) / ry) ** 2 <= 1.0
    if not filled:
        p = np.pad(m, 1)
        m = m & ~(p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:])
    yy, xx = np.nonzero(m)
    return [(int(x) + xa, int(y) + ya) for y, x in zip(yy, xx)]


def same_color(arr: np.ndarray, v) -> np.ndarray:
    v = np.asarray(v, dtype=arr.dtype)
    return (arr == v) if arr.ndim == 2 else np.all(arr == v, axis=2)


def region(arr: np.ndarray, x: int, y: int, contiguous: bool = True) -> np.ndarray:
    """Máscara dos pixels da cor de (x, y): a região contígua (4 vizinhos) ou a imagem toda."""
    same = same_color(arr, arr[y, x])
    if not contiguous:
        return same
    h, w = same.shape
    done = np.zeros((h, w), bool)
    todo = deque([(x, y)])
    while todo:                       # varre por linhas: um ponto de partida por trecho vizinho
        cx, cy = todo.pop()
        if done[cy, cx] or not same[cy, cx]:
            continue
        a = cx
        while a > 0 and same[cy, a - 1] and not done[cy, a - 1]:
            a -= 1
        b = cx
        while b < w - 1 and same[cy, b + 1] and not done[cy, b + 1]:
            b += 1
        done[cy, a:b + 1] = True
        for ny in (cy - 1, cy + 1):
            if 0 <= ny < h:
                row = same[ny, a:b + 1] & ~done[ny, a:b + 1]
                starts = np.flatnonzero(row & ~np.concatenate(([False], row[:-1])))
                todo.extend((a + int(s), ny) for s in starts)
    return done


def flood_fill(arr: np.ndarray, x: int, y: int, value, contiguous: bool = True,
               limit: np.ndarray | None = None) -> bool:
    """Balde: troca a região da cor de (x, y) (contígua ou toda) pela cor; `limit` restringe."""
    value = np.asarray(value, dtype=arr.dtype)
    if np.array_equal(arr[y, x], value):
        return False
    m = region(arr, x, y, contiguous)
    if limit is not None:
        m &= limit
    if not m.any():
        return False
    arr[m] = value
    return True


def opaque(arr: np.ndarray) -> np.ndarray:
    return arr != 0 if arr.ndim == 2 else arr[..., 3] > 0


def touching(mask: np.ndarray, diagonal: bool = False, border: bool = False) -> np.ndarray:
    """Pixels com algum vizinho em `mask` (4 ou 8 vizinhos); `border` conta fora da imagem."""
    p = np.pad(mask, 1, constant_values=border)
    n = p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    if diagonal:
        n |= p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:]
    return n


def outline_mask(arr: np.ndarray, diagonal: bool = False, inside: bool = False,
                 within: np.ndarray | None = None) -> np.ndarray:
    """Contorno da figura: por fora (pixels vazios encostados nela) ou por dentro (pixels da
    borda dela). `within` limita a figura considerada (a seleção)."""
    op = opaque(arr)
    fig = op if within is None else op & within
    if inside:
        return fig & touching(~op, diagonal, border=True)
    return ~op & touching(fig, diagonal)


def isolated_mask(arr: np.ndarray) -> np.ndarray:
    """Pixels soltos: opacos sem nenhum vizinho opaco (nem na diagonal)."""
    op = opaque(arr)
    return op & ~touching(op, diagonal=True)


def shifted(a: np.ndarray, dx: int, dy: int, fill=0) -> np.ndarray:
    """Cópia de `a` deslocada (dx, dy); o que sai da imagem some e o que entra vem com `fill`."""
    out = np.zeros_like(a) if fill == 0 else np.full_like(a, fill)
    h, w = a.shape[:2]
    xs0, xs1, ys0, ys1 = max(0, -dx), min(w, w - dx), max(0, -dy), min(h, h - dy)
    if xs0 < xs1 and ys0 < ys1:
        out[ys0 + dy:ys1 + dy, xs0 + dx:xs1 + dx] = a[ys0:ys1, xs0:xs1]
    return out


def bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1) inclusivos da parte marcada, ou None."""
    ys, xs = np.nonzero(mask)
    if not xs.size:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def resize_canvas(arr: np.ndarray, left: int, top: int, right: int, bottom: int) -> np.ndarray:
    """Margens a mais (positivas) ou a menos (negativas) em cada lado; a figura não muda."""
    h, w = arr.shape[:2]
    nw, nh = w + left + right, h + top + bottom
    if nw < 1 or nh < 1:
        raise ValueError(tr("the image would be empty"))
    out = np.zeros((nh, nw) + arr.shape[2:], arr.dtype)
    sx0, sy0 = max(0, -left), max(0, -top)
    sx1, sy1 = min(w, nw - left), min(h, nh - top)
    if sx0 < sx1 and sy0 < sy1:
        out[sy0 + top:sy1 + top, sx0 + left:sx1 + left] = arr[sy0:sy1, sx0:sx1]
    return out


def nearest_indices(rgba: np.ndarray, colors: list[tuple[int, int, int]]) -> np.ndarray:
    """Imagem RGBA nos índices da paleta mais próximos (o 0 fica para o transparente)."""
    h, w = rgba.shape[:2]
    pal = np.array([c[:3] for c in colors[1:256]], np.int32)
    px = rgba[..., :3].reshape(-1, 3).astype(np.int32)
    uniq, inv = np.unique(px, axis=0, return_inverse=True)
    d = ((uniq[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
    out = (d.argmin(axis=1) + 1)[inv.reshape(-1)].reshape(h, w).astype(np.uint8)
    out[rgba[..., 3] < 128] = 0
    return out


def tinted(img: Image.Image, tint: tuple[int, int, int]) -> QImage:
    """Imagem RGBA puxada para uma cor (onion skin: anterior em vermelho, seguinte em azul)."""
    a = np.array(img.convert("RGBA"), np.float32)
    a[..., :3] = a[..., :3] * 0.35 + np.array(tint, np.float32) * 0.65
    a = a.astype(np.uint8)
    h, w = a.shape[:2]
    return QImage(a.tobytes(), w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


# ---------------------------------------------------------------- tela de pintura

class PixelCanvas(QWidget):
    hovered = Signal(int, int)          # pixel sob o mouse (-1, -1 fora da imagem)
    picked = Signal(object, bool)        # cor pega e se foi com o botão direito
    aboutToChange = Signal()             # antes de cada mudança (para desfazer)
    changed = Signal()
    discard = Signal()                   # a mudança anunciada não aconteceu (tira o desfazer)
    selectionChanged = Signal()
    zoomChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(320, 240)
        self.arr: np.ndarray | None = None
        self.indexed = True
        self.colors: list[tuple[int, int, int]] = []
        self.zoom = 8
        self.pan = QPointF(0, 0)
        self.tool = "pencil"
        self.size = 1
        self.primary = 1
        self.secondary = 0
        self.contiguous = True
        self.show_grid = True
        self.show_axis = True
        self.axis = (0, 0)
        self.selection: np.ndarray | None = None
        self.onion: list[tuple[QImage, int, int]] = []    # (imagem tingida, eixo x, eixo y dela)
        self.show_onion = False
        self.highlight = None             # cor da paleta sob o mouse: os pixels dela acendem
        self.bg = QColor("#2b2b2b")
        self.grid_color = QColor(255, 255, 255, 40)
        self.accent = QColor("#a6e22e")
        self._qimg: QImage | None = None
        self._hl_img: QImage | None = None
        self._edges: list | None = None
        self._hover: tuple[int, int] | None = None
        self._stroke = None              # (valor, último ponto) durante um traço
        self._shape = None               # (início, fim, botão) da linha/retângulo/elipse
        self._sel_drag = None            # (início, fim, modo) da seleção retangular em andamento
        self._move = None                # pixels levantados durante um arrasto de mover
        self._pan_from = None
        self._space = False
        self._last_end: tuple[int, int] | None = None   # fim do último traço (Shift+clique = reta)

    # ------------------------------------------------------------ dados

    def set_image(self, arr: np.ndarray, indexed: bool, colors: list[tuple[int, int, int]]) -> None:
        self.arr, self.indexed, self.colors = arr, indexed, colors
        if self.selection is not None and self.selection.shape != arr.shape[:2]:
            self.selection = None
        self._edges = None
        self.refresh()

    def refresh(self) -> None:
        a = self.arr
        if a is None:
            self._qimg = None
        elif self.indexed:
            h, w = a.shape
            q = QImage(np.ascontiguousarray(a).tobytes(), w, h, w, QImage.Format.Format_Indexed8)
            table = [QColor(*c).rgb() for c in self.colors[:256]]
            table += [0xFF000000] * (256 - len(table))
            table[0] = 0                  # índice 0 = transparente
            q.setColorTable(table)
            self._qimg = q.copy()
        else:
            h, w = a.shape[:2]
            self._qimg = QImage(np.ascontiguousarray(a).tobytes(), w, h, w * 4,
                                QImage.Format.Format_RGBA8888).copy()
        self._hl_img = None
        self.update()

    def value_for(self, right: bool):
        return self.secondary if right else self.primary

    def eraser_value(self):
        return 0 if self.indexed else (0, 0, 0, 0)

    def limit(self) -> np.ndarray | None:
        """A seleção, quando há uma: pintar, apagar e preencher ficam presos a ela."""
        return self.selection if self.selection is not None and self.selection.any() else None

    def set_selection(self, mask: np.ndarray | None) -> None:
        if mask is not None and not mask.any():
            mask = None
        self.selection = mask
        self._edges = None
        self.selectionChanged.emit()
        self.update()

    def set_highlight(self, v) -> None:
        if v != self.highlight:
            self.highlight = v
            self._hl_img = None
            self.update()

    # ------------------------------------------------------------ vista

    def origin(self) -> QPointF:
        if self.arr is None:
            return QPointF(0, 0)
        h, w = self.arr.shape[:2]
        return QPointF((self.width() - w * self.zoom) / 2 + self.pan.x(),
                       (self.height() - h * self.zoom) / 2 + self.pan.y())

    def to_pixel(self, pos: QPointF) -> tuple[int, int]:
        o = self.origin()
        return int((pos.x() - o.x()) // self.zoom), int((pos.y() - o.y()) // self.zoom)

    def inside(self, x: int, y: int) -> bool:
        return self.arr is not None and 0 <= x < self.arr.shape[1] and 0 <= y < self.arr.shape[0]

    def clamp(self, x: int, y: int) -> tuple[int, int]:
        h, w = self.arr.shape[:2]
        return max(0, min(w - 1, x)), max(0, min(h - 1, y))

    def set_zoom(self, z: int, anchor: QPointF | None = None) -> None:
        z = max(ZOOMS[0], min(ZOOMS[-1], z))
        if z == self.zoom:
            return
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        o = self.origin()
        ix, iy = (anchor.x() - o.x()) / self.zoom, (anchor.y() - o.y()) / self.zoom
        self.zoom = z
        o2 = self.origin()
        # o pixel sob o mouse continua sob o mouse
        self.pan += QPointF(anchor.x() - (o2.x() + ix * z), anchor.y() - (o2.y() + iy * z))
        self.zoomChanged.emit(z)
        self.update()

    def step_zoom(self, d: int, anchor: QPointF | None = None) -> None:
        i = min(range(len(ZOOMS)), key=lambda k: abs(ZOOMS[k] - self.zoom))
        self.set_zoom(ZOOMS[max(0, min(len(ZOOMS) - 1, i + d))], anchor)

    def fit(self) -> None:
        if self.arr is None:
            return
        h, w = self.arr.shape[:2]
        room = min((self.width() - 40) / max(1, w), (self.height() - 40) / max(1, h))
        self.zoom = max(z for z in ZOOMS if z <= max(1, room)) if room >= 1 else 1
        self.pan = QPointF(0, 0)
        self.zoomChanged.emit(self.zoom)
        self.update()

    # ------------------------------------------------------------ pintura

    def _plot(self, pts, value) -> None:
        a = self.arr
        sel = self.limit()
        h, w = a.shape[:2]
        lo, hi = -((self.size - 1) // 2), self.size // 2
        n = self.size if self.tool in ("pencil", "eraser") else 1
        if n == 1:
            lo = hi = 0
        for x, y in pts:
            x0, x1 = max(0, x + lo), min(w, x + hi + 1)
            y0, y1 = max(0, y + lo), min(h, y + hi + 1)
            if x0 < x1 and y0 < y1:
                if sel is None:
                    a[y0:y1, x0:x1] = value
                else:
                    a[y0:y1, x0:x1][sel[y0:y1, x0:x1]] = value

    def _pick(self, x: int, y: int, right: bool) -> None:
        if self.inside(x, y):
            v = int(self.arr[y, x]) if self.indexed else tuple(int(c) for c in self.arr[y, x])
            self.picked.emit(v, right)

    def _mode(self, mods) -> str:
        if mods & Qt.KeyboardModifier.ShiftModifier:
            return "add"
        if mods & Qt.KeyboardModifier.ControlModifier:
            return "sub"
        return "new"

    def _combine(self, mask: np.ndarray, mode: str) -> None:
        cur = self.selection
        if mode == "add" and cur is not None:
            mask = cur | mask
        elif mode == "sub":
            mask = (cur & ~mask) if cur is not None else None
        self.set_selection(mask)

    # ---- mover: levanta os pixels, arrasta e carimba no lugar novo

    def start_move(self, x: int, y: int, copy: bool = False) -> bool:
        sel = self.limit()
        mask = sel if sel is not None else opaque(self.arr)
        if not mask.any():
            return False
        self.aboutToChange.emit()
        base = self.arr.copy()
        if not copy:
            base[mask] = self.eraser_value()
        self._move = {"base": base, "vals": self.arr.copy(), "mask": mask.copy(), "start": (x, y),
                      "off": (0, 0), "sel": sel is not None}
        return True

    def drag_move(self, dx: int, dy: int) -> None:
        m = self._move
        if (dx, dy) == m["off"]:
            return
        m["off"] = (dx, dy)
        mask = shifted(m["mask"], dx, dy, False)
        vals = shifted(m["vals"], dx, dy)
        a = m["base"].copy()
        a[mask] = vals[mask]
        self.arr[...] = a
        if m["sel"]:
            self.selection = mask
            self._edges = None
        self.refresh()

    def end_move(self) -> None:
        moved = self._move["off"] != (0, 0) or not np.array_equal(self._move["base"], self._move["vals"])
        self._move = None
        if moved:
            self.changed.emit()
            self.selectionChanged.emit()
        else:
            self.discard.emit()

    def nudge(self, dx: int, dy: int) -> None:
        """Setas: move a seleção (ou a figura toda, com a ferramenta mover) de uma vez."""
        if self.start_move(0, 0):
            self.drag_move(dx, dy)
            self.end_move()

    # ---- mouse

    def mousePressEvent(self, e):  # noqa: N802
        if self.arr is None:
            return
        pos = e.position()
        if e.button() == Qt.MouseButton.MiddleButton or (self._space and e.button() == Qt.MouseButton.LeftButton):
            self._pan_from = (pos, QPointF(self.pan))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if e.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return
        right = e.button() == Qt.MouseButton.RightButton
        x, y = self.to_pixel(pos)
        mods = e.modifiers()
        if self.tool == "picker" or mods & Qt.KeyboardModifier.AltModifier:
            self._pick(x, y, right)
            return
        sel = self.limit()
        if self.tool == "move" or (self.tool == "select" and self._mode(mods) == "new" and sel is not None
                                   and self.inside(x, y) and sel[y, x]):
            self.start_move(x, y, copy=bool(mods & Qt.KeyboardModifier.ControlModifier) and self.tool == "move")
            return
        if self.tool == "select":
            cx, cy = self.clamp(x, y)
            self._sel_drag = ((cx, cy), (cx, cy), self._mode(mods), self.inside(x, y))
            self.update()
            return
        if self.tool == "wand":
            if self.inside(x, y):
                self._combine(region(self.arr, x, y, self.contiguous), self._mode(mods))
            elif self._mode(mods) == "new":
                self.set_selection(None)
            return
        if self.tool == "fill":
            if self.inside(x, y):
                self.aboutToChange.emit()
                if flood_fill(self.arr, x, y, self.value_for(right), self.contiguous, self.limit()):
                    self.refresh()
                    self.changed.emit()
                else:
                    self.discard.emit()
            return
        if self.tool in SHAPES:
            self._shape = ((x, y), (x, y), right)
            self.update()
            return
        value = self.eraser_value() if self.tool == "eraser" else self.value_for(right)
        self.aboutToChange.emit()
        start = (x, y)
        if mods & Qt.KeyboardModifier.ShiftModifier and self._last_end:
            self._plot(line_points(*self._last_end, x, y), value)   # Shift+clique: reta desde o último ponto
        else:
            self._plot([start], value)
        self._stroke = (value, start)
        self.refresh()

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position()
        if self._pan_from:
            p0, pan0 = self._pan_from
            self.pan = pan0 + (pos - p0)
            self.update()
            return
        x, y = self.to_pixel(pos)
        hov = (x, y) if self.inside(x, y) else None
        if hov != self._hover:
            self._hover = hov
            self.hovered.emit(*(hov or (-1, -1)))
            self.update()
        if self._move:
            sx, sy = self._move["start"]
            self.drag_move(x - sx, y - sy)
        elif self._sel_drag:
            a, _b, mode, began = self._sel_drag
            self._sel_drag = (a, self.clamp(x, y), mode, began)
            self.update()
        elif self._stroke:
            value, last = self._stroke
            if (x, y) != last:
                self._plot(line_points(*last, x, y), value)
                self._stroke = (value, (x, y))
                self.refresh()
        elif self._shape:
            a, _b, right = self._shape
            self._shape = (a, (x, y), right)
            self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._pan_from:
            self._pan_from = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._space else Qt.CursorShape.CrossCursor)
            return
        if self._move:
            self.end_move()
        elif self._sel_drag:
            (x0, y0), (x1, y1), mode, began = self._sel_drag
            self._sel_drag = None
            if (x0, y0) == (x1, y1) and mode == "new":
                self.set_selection(None)          # clique simples tira a seleção
                return
            if not began and (x0, y0) == (x1, y1):
                self.update()
                return
            m = np.zeros(self.arr.shape[:2], bool)
            m[min(y0, y1):max(y0, y1) + 1, min(x0, x1):max(x0, x1) + 1] = True
            self._combine(m, mode)
        elif self._stroke:
            self._last_end = self._stroke[1]
            self._stroke = None
            self.changed.emit()
        elif self._shape:
            pts = self._shape_points(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            right = self._shape[2]
            self._last_end = self._shape[1]
            self._shape = None
            self.aboutToChange.emit()
            self._plot(pts, self.value_for(right))
            self.refresh()
            self.changed.emit()

    def _shape_points(self, filled=False):
        (x0, y0), (x1, y1), _ = self._shape
        if self.tool == "line":
            return line_points(x0, y0, x1, y1)
        if self.tool == "ellipse":
            return ellipse_points(x0, y0, x1, y1, bool(filled))
        return rect_points(x0, y0, x1, y1, bool(filled))

    def leaveEvent(self, _e):  # noqa: N802
        self._hover = None
        self.hovered.emit(-1, -1)
        self.update()

    def wheelEvent(self, e):  # noqa: N802
        self.step_zoom(1 if e.angleDelta().y() > 0 else -1, e.position())

    ARROWS = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif e.key() in self.ARROWS and (self.limit() is not None or self.tool == "move") and self.arr is not None:
            dx, dy = self.ARROWS[e.key()]
            k = 8 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self.nudge(dx * k, dy * k)
        elif e.key() == Qt.Key.Key_Escape and self.selection is not None:
            self.set_selection(None)
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            super().keyReleaseEvent(e)

    # ------------------------------------------------------------ desenho

    def _color_of(self, v) -> QColor:
        if self.indexed:
            if v == 0:
                return QColor(0, 0, 0, 0)
            r, g, b = self.colors[v] if v < len(self.colors) else (0, 0, 0)
            return QColor(r, g, b)
        return QColor(*v)

    def _selection_edges(self):
        """Segmentos da borda da seleção (em pixels da imagem), calculados uma vez por mudança."""
        if self._edges is None:
            s = self.selection
            segs = []
            if s is not None:
                p = np.pad(s, 1)
                hor = p[1:, 1:-1] != p[:-1, 1:-1]      # borda acima da linha y (y = 0..h)
                ver = p[1:-1, 1:] != p[1:-1, :-1]      # borda à esquerda da coluna x (x = 0..w)
                ys, xs = np.nonzero(hor)
                segs += [(x, y, x + 1, y) for y, x in zip(ys.tolist(), xs.tolist())]
                ys, xs = np.nonzero(ver)
                segs += [(x, y, x, y + 1) for y, x in zip(ys.tolist(), xs.tolist())]
            self._edges = segs
        return self._edges

    def _highlight_image(self) -> QImage | None:
        if self.highlight is None or self.arr is None:
            return None
        if self._hl_img is None:
            m = same_color(self.arr, self.highlight)
            h, w = m.shape
            buf = np.zeros((h, w, 4), np.uint8)
            buf[...] = (0, 0, 0, 150)
            buf[m] = (self.accent.red(), self.accent.green(), self.accent.blue(), 255)
            self._hl_img = QImage(buf.tobytes(), w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        return self._hl_img

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), self.bg)
        if self.arr is None or self._qimg is None:
            p.end()
            return
        h, w = self.arr.shape[:2]
        z = self.zoom
        o = self.origin()
        r = QRectF(o.x(), o.y(), w * z, h * z)
        # xadrez atrás da transparência, em quadros fixos de 8 px de tela
        p.save()
        p.setClipRect(r)
        c1, c2 = QColor(self.bg).lighter(135), QColor(self.bg).lighter(160)
        s = 8
        y = r.top()
        row = 0
        while y < r.bottom():
            x = r.left() + (s if row % 2 else 0)
            p.fillRect(QRectF(r.left(), y, r.width(), s), c1)
            while x < r.right():
                p.fillRect(QRectF(x, y, s, s), c2)
                x += 2 * s
            y += s
            row += 1
        p.restore()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(r, self._qimg)
        # onion skin: os vizinhos tingidos, alinhados pelo eixo, por cima e transparentes
        if self.show_onion and self.onion:
            p.setOpacity(0.3)
            ax, ay = self.axis
            for q, oax, oay in self.onion:          # eixos coincidem: o vizinho anda com o eixo
                p.drawImage(QRectF(o.x() + (ax - oax) * z, o.y() + (ay - oay) * z,
                                   q.width() * z, q.height() * z), q)
            p.setOpacity(1.0)
        hl = self._highlight_image()
        if hl is not None:
            p.drawImage(r, hl)
        # prévia da linha/retângulo/elipse
        if self._shape:
            col = self._color_of(self.value_for(self._shape[2]))
            if col.alpha() == 0:
                col = QColor(255, 255, 255, 90)
            for x, y in self._shape_points():
                if 0 <= x < w and 0 <= y < h:
                    p.fillRect(QRectF(o.x() + x * z, o.y() + y * z, z, z), col)
        if self.show_grid and z >= 6:
            p.setPen(QPen(self.grid_color, 1))
            x0 = max(0, int(-o.x() // z))
            x1 = min(w, int((self.width() - o.x()) // z) + 1)
            y0 = max(0, int(-o.y() // z))
            y1 = min(h, int((self.height() - o.y()) // z) + 1)
            for x in range(x0, x1 + 1):
                p.drawLine(QPointF(o.x() + x * z, max(r.top(), 0)), QPointF(o.x() + x * z, min(r.bottom(), self.height())))
            for y in range(y0, y1 + 1):
                p.drawLine(QPointF(max(r.left(), 0), o.y() + y * z), QPointF(min(r.right(), self.width()), o.y() + y * z))
        p.setPen(QPen(QColor(self.grid_color.red(), self.grid_color.green(), self.grid_color.blue(), 110), 1))
        p.drawRect(r.adjusted(-0.5, -0.5, 0.5, 0.5))
        if self.show_axis:
            ax, ay = self.axis
            cx, cy = o.x() + ax * z, o.y() + ay * z
            pen = QPen(self.accent, 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(cx - 10, cy), QPointF(cx + 10, cy))
            p.drawLine(QPointF(cx, cy - 10), QPointF(cx, cy + 10))
        # borda da seleção: preta por baixo, branca tracejada por cima
        segs = self._selection_edges()
        if segs:
            lines = [(QPointF(o.x() + a * z, o.y() + b * z), QPointF(o.x() + c * z, o.y() + d * z))
                     for a, b, c, d in segs]
            for pen in (QPen(QColor(0, 0, 0), 1), QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine)):
                p.setPen(pen)
                for a, b in lines:
                    p.drawLine(a, b)
        if self._sel_drag:
            (x0, y0), (x1, y1), _m, _b = self._sel_drag
            rr = QRectF(o.x() + min(x0, x1) * z, o.y() + min(y0, y1) * z,
                        (abs(x1 - x0) + 1) * z, (abs(y1 - y0) + 1) * z)
            p.setPen(QPen(QColor(0, 0, 0), 1))
            p.drawRect(rr)
            p.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
            p.drawRect(rr)
        # pixel(s) sob o pincel
        if self._hover and not self._pan_from and self.tool in ("pencil", "eraser", *SHAPES):
            x, y = self._hover
            lo = -((self.size - 1) // 2)
            n = self.size if self.tool in ("pencil", "eraser") else 1
            if n == 1:
                lo = 0
            p.setPen(QPen(QColor(255, 255, 255, 200), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(o.x() + (x + lo) * z, o.y() + (y + lo) * z, n * z, n * z).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()


# ---------------------------------------------------------------- paleta

class Swatches(QWidget):
    """Cores para escolher: os 256 índices da paleta (ou as cores da imagem truecolor).
    Esquerdo = cor principal, direito = secundária. Um ponto marca as cores que a imagem usa e
    passar o mouse numa cor avisa (`hovered`) para a tela destacar os pixels dela."""
    chosen = Signal(object, bool)
    hovered = Signal(object)

    CELL = 15

    def __init__(self):
        super().__init__()
        self.values: list = []
        self.colors: list[QColor] = []
        self.used: set = set()
        self.primary = None
        self.secondary = None
        self._hover = None
        self.setMouseTracking(True)
        self.setFixedWidth(16 * self.CELL + 2)

    def set_values(self, values: list, colors: list[QColor]) -> None:
        self.values, self.colors = values, colors
        rows = (len(values) + 15) // 16
        self.setFixedHeight(max(1, rows) * self.CELL + 2)
        self.update()

    def _at(self, pos) -> int | None:
        k = int(pos.y() // self.CELL) * 16 + int(pos.x() // self.CELL)
        return k if 0 <= pos.x() < 16 * self.CELL and 0 <= k < len(self.values) else None

    def mousePressEvent(self, e):  # noqa: N802
        k = self._at(e.position())
        if k is not None:
            self.chosen.emit(self.values[k], e.button() == Qt.MouseButton.RightButton)

    def mouseMoveEvent(self, e):  # noqa: N802
        k = self._at(e.position())
        v = None if k is None else self.values[k]
        if v != self._hover:
            self._hover = v
            self.hovered.emit(v)
        if k is None:
            self.setToolTip("")
            return
        c = self.colors[k]
        name = tr("transparent") if c.alpha() == 0 else c.name()
        use = tr(" · used in the image") if v in self.used else ""
        self.setToolTip((tr("index {0} · {1}").format(v, name) if isinstance(v, int) else name) + use)

    def leaveEvent(self, _e):  # noqa: N802
        if self._hover is not None:
            self._hover = None
            self.hovered.emit(None)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        s = self.CELL
        for k, c in enumerate(self.colors):
            r = QRectF(1 + (k % 16) * s, 1 + (k // 16) * s, s - 1, s - 1)
            if c.alpha() == 0:
                p.fillRect(r, QColor("#9a9a9a"))
                p.fillRect(QRectF(r.x(), r.y(), r.width() / 2, r.height() / 2), QColor("#d0d0d0"))
                p.fillRect(QRectF(r.center().x(), r.center().y(), r.width() / 2, r.height() / 2), QColor("#d0d0d0"))
            else:
                p.fillRect(r, c)
            if self.values[k] in self.used and c.alpha():
                dot = QColor(0, 0, 0) if c.lightness() > 140 else QColor(255, 255, 255)
                p.fillRect(QRectF(r.right() - 4, r.bottom() - 4, 3, 3), dot)
        for v, col, dash in ((self.secondary, QColor("#ffffff"), True), (self.primary, QColor("#ffffff"), False)):
            if v in self.values:
                k = self.values.index(v)
                r = QRectF(1 + (k % 16) * s, 1 + (k // 16) * s, s - 1, s - 1).adjusted(-1, -1, 1, 1)
                p.setPen(QPen(QColor(0, 0, 0), 3))
                p.drawRect(r)
                p.setPen(QPen(col, 1.5, Qt.PenStyle.DashLine if dash else Qt.PenStyle.SolidLine))
                p.drawRect(r)
        p.end()


class ColorPair(QWidget):
    """As duas cores em uso (principal na frente, secundária atrás); clique troca."""
    swapRequested = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(46, 40)
        self.front = QColor(0, 0, 0)
        self.back = QColor(0, 0, 0, 0)
        self.setToolTip(tr("Primary (left) and secondary (right) color · click or X swaps"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_colors(self, front: QColor, back: QColor) -> None:
        self.front, self.back = front, back
        self.update()

    def mousePressEvent(self, _e):  # noqa: N802
        self.swapRequested.emit()

    def _swatch(self, p, r, c):
        if c.alpha() == 0:
            p.fillRect(r, QColor("#9a9a9a"))
            p.fillRect(QRectF(r.x(), r.y(), r.width() / 2, r.height() / 2), QColor("#d0d0d0"))
            p.fillRect(QRectF(r.center().x(), r.center().y(), r.width() / 2, r.height() / 2), QColor("#d0d0d0"))
        else:
            p.fillRect(r, c)
        p.setPen(QPen(self.palette().text().color(), 1))
        p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        self._swatch(p, QRectF(18, 14, 26, 24), self.back)
        self._swatch(p, QRectF(2, 2, 26, 24), self.front)
        p.end()


class CanvasSizeDialog(QDialog):
    """Margens a mais ou a menos em cada lado da imagem; o eixo acompanha a figura."""

    def __init__(self, parent, w: int, h: int):
        super().__init__(parent)
        self.setWindowTitle(tr("Canvas size"))
        self.w0, self.h0 = w, h
        form = QFormLayout(self)
        self.sides = {}
        for key, label in (("left", tr("Left")), ("top", tr("Top")), ("right", tr("Right")), ("bottom", tr("Bottom"))):
            sb = QSpinBox()
            sb.setRange(-(w if key in ("left", "right") else h) + 1, 1024)
            sb.setSuffix(" px")
            sb.valueChanged.connect(self._update)
            self.sides[key] = sb
            form.addRow(label, sb)
        self.info = QLabel()
        self.info.setObjectName("dim")
        form.addRow(self.info)
        hint = QLabel(tr("Positive enlarges the canvas on that side (room to draw); negative crops."))
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        form.addRow(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self._update()

    def values(self) -> tuple[int, int, int, int]:
        s = self.sides
        return s["left"].value(), s["top"].value(), s["right"].value(), s["bottom"].value()

    def _update(self) -> None:
        l, t, r, b = self.values()
        self.info.setText(f"{self.w0}×{self.h0} → {self.w0 + l + r}×{self.h0 + t + b} px")


# ---------------------------------------------------------------- janela

class ImageEditor(QDialog):
    """Edita um sprite; Aplicar devolve a imagem (mesmos índices e paleta) e o eixo.

    `onion`: [(imagem RGBA, (eixo x, eixo y), "prev"|"next")] dos vizinhos para o onion skin.
    `navigate(delta, imagem_ou_None, eixo)`: chamado em PgUp/PgDn; recebe a imagem editada
    (None se nada mudou) para gravar e devolve o próximo sprite como dict com image, colors,
    axis, title, key e onion (ou None se não há para onde ir)."""

    def __init__(self, parent, image: Image.Image, colors: list[tuple[int, int, int]],
                 axis: tuple[int, int], title: str, theme=None, onion=None, navigate=None):
        super().__init__(parent)
        self.resize(1120, 740)
        self.setModal(True)
        self.navigate = navigate
        self.result_image: Image.Image | None = None
        self.result_axis: tuple[int, int] | None = None
        self.saved_any = False            # navegar grava o sprite que fica para trás
        self._histories: dict = {}
        self._clip = None                 # (valores, máscara, x, y) copiados nesta janela
        self._colors_set = False
        self.key = title

        self.canvas = PixelCanvas()
        if theme is not None:
            u = theme.ui
            self.canvas.bg = QColor(u.get("window", "#2b2b2b")).darker(115)
            self.canvas.accent = QColor(u.get("accent", "#a6e22e"))
        self.canvas.aboutToChange.connect(self._snapshot)
        self.canvas.changed.connect(self._on_changed)
        self.canvas.discard.connect(self._drop_snapshot)
        self.canvas.picked.connect(self.set_color)
        self.canvas.hovered.connect(self._on_hover)
        self.canvas.selectionChanged.connect(lambda: self._on_hover(-1, -1))
        self.canvas.zoomChanged.connect(lambda z: self.zoom_label.setText(f"{z * 100}%"))

        # ------------------------------------------------ barra de ferramentas (topo)
        bar = QWidget()
        bar.setObjectName("docStrip")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(10, 6, 10, 6)
        tl.setSpacing(2)
        self.tool_group = QActionGroup(self)
        self.tool_actions: dict[str, QAction] = {}
        for key, ic, name, short in TOOLS:
            a = QAction(icon(ic), f"{name} ({short})", self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(short))
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            a.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_group.addAction(a)
            self.addAction(a)
            tl.addWidget(self._button(a))
            self.tool_actions[key] = a
            if key in ("move", "picker"):
                tl.addSpacing(6)
        tl.addSpacing(10)
        self.size_label = QLabel(tr("Size"))
        self.size_label.setObjectName("dim")
        tl.addWidget(self.size_label)
        self.size = QSpinBox()
        self.size.setRange(1, 16)
        self.size.setSuffix(" px")
        self.size.setToolTip(tr("Pencil and eraser size · [ and ] change it"))
        self.size.valueChanged.connect(lambda v: setattr(self.canvas, "size", v))
        tl.addWidget(self.size)
        self.contiguous = QCheckBox(tr("Contiguous"))
        self.contiguous.setChecked(True)
        self.contiguous.setToolTip(tr("Bucket and wand take only the touching region; off, the color in the whole image"))
        self.contiguous.toggled.connect(lambda on: setattr(self.canvas, "contiguous", on))
        tl.addWidget(self.contiguous)
        tl.addSpacing(10)
        self.undo_action = self._action("undo", tr("Undo (Ctrl+Z)"), self.undo, QKeySequence.StandardKey.Undo)
        self.redo_action = self._action("redo", tr("Redo (Ctrl+Y)"), self.redo, QKeySequence("Ctrl+Y"))
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.redo)
        tl.addWidget(self._button(self.undo_action))
        tl.addWidget(self._button(self.redo_action))
        tl.addSpacing(6)
        self.image_menu = self._build_image_menu()
        menu_btn = QToolButton()
        menu_btn.setText(tr("Image"))
        menu_btn.setIcon(icon("image"))
        menu_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_btn.setMenu(self.image_menu)
        menu_btn.setAutoRaise(True)
        menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu_btn.setToolTip(tr("Selection, outline, color swap, crop and canvas size"))
        self.image_menu_button = menu_btn
        tl.addWidget(menu_btn)
        tl.addStretch(1)
        self.onion_action = self._action("onion", tr("Onion skin: neighbor sprites in the group (F3)"),
                                         self._toggle_onion, QKeySequence("F3"), checkable=True)
        tl.addWidget(self._button(self.onion_action))
        self.prev_action = self._action("left", tr("Previous sprite, saving this one (PgUp)"),
                                        lambda: self.go(-1), QKeySequence("PgUp"))
        self.next_action = self._action("right", tr("Next sprite, saving this one (PgDown)"),
                                        lambda: self.go(1), QKeySequence("PgDown"))
        tl.addWidget(self._button(self.prev_action))
        tl.addWidget(self._button(self.next_action))
        self.prev_action.setVisible(navigate is not None)
        self.next_action.setVisible(navigate is not None)
        tl.addSpacing(8)
        grid = self._action("guides", tr("Pixel grid (Ctrl+G)"), lambda on: self._toggle("show_grid", on),
                            QKeySequence("Ctrl+G"), checkable=True)
        grid.setChecked(True)
        axis_a = self._action("axis", tr("Show the axis"), lambda on: self._toggle("show_axis", on), checkable=True)
        axis_a.setChecked(True)
        tl.addWidget(self._button(grid))
        tl.addWidget(self._button(axis_a))
        tl.addSpacing(8)
        tl.addWidget(self._button(self._action("zoom_out", tr("Zoom out (-)"), lambda: self.canvas.step_zoom(-1),
                                               QKeySequence("-"))))
        self.zoom_label = QLabel()
        self.zoom_label.setObjectName("counter")
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tl.addWidget(self.zoom_label)
        tl.addWidget(self._button(self._action("zoom_in", tr("Zoom in (+)"), lambda: self.canvas.step_zoom(1),
                                              QKeySequence("+"))))
        tl.addWidget(self._button(self._action("fit", tr("Fit to window (Ctrl+0)"), self.canvas.fit,
                                              QKeySequence("Ctrl+0"))))

        # ------------------------------------------------ cores (direita)
        side = QFrame()
        side.setObjectName("ctrlCard")
        side.setFixedWidth(16 * Swatches.CELL + 2 + 24)     # a largura da paleta; o texto quebra
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 12, 12, 12)
        sl.setSpacing(8)
        head = QLabel(tr("Colors"))
        head.setObjectName("cardTitle")
        sl.addWidget(head)
        pair_row = QHBoxLayout()
        pair_row.setSpacing(10)
        self.pair = ColorPair()
        self.pair.swapRequested.connect(self.swap_colors)
        self.pair_info = QLabel()
        self.pair_info.setObjectName("dim")
        self.pair_info.setWordWrap(True)
        pair_row.addWidget(self.pair)
        pair_row.addWidget(self.pair_info, 1)
        sl.addLayout(pair_row)
        self.swatches = Swatches()
        self.swatches.chosen.connect(self.set_color)
        self.swatches.hovered.connect(self.canvas.set_highlight)
        sl.addWidget(self.swatches)
        self.more_color = QPushButton(tr("Other color…"))
        self.more_color.clicked.connect(self.choose_rgba)
        sl.addWidget(self.more_color)
        self.hint = QLabel()
        self.hint.setObjectName("dim")
        self.hint.setWordWrap(True)
        sl.addWidget(self.hint)
        sl.addStretch(1)
        QShortcut(QKeySequence("X"), self, self.swap_colors)
        QShortcut(QKeySequence("["), self, lambda: self.size.setValue(self.size.value() - 1))
        QShortcut(QKeySequence("]"), self, lambda: self.size.setValue(self.size.value() + 1))

        # ------------------------------------------------ rodapé
        foot = QWidget()
        foot.setObjectName("docStrip")
        foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(12, 8, 12, 8)
        fl.setSpacing(8)
        self.status = QLabel()
        self.status.setObjectName("dim")
        fl.addWidget(self.status, 1)
        cancel = QPushButton(tr("Cancel"))
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        self.apply_btn = QPushButton(tr("Apply to sprite"))
        self.apply_btn.setProperty("primary", True)
        self.apply_btn.setToolTip(tr("Saves to the sprite (Ctrl+Enter); you can undo it in the app with Ctrl+Z"))
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self.accept)
        fl.addWidget(cancel)
        fl.addWidget(self.apply_btn)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.accept)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self.canvas, 1)
        mid.addWidget(side)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        lay.addLayout(mid, 1)
        lay.addWidget(foot)

        self.load(image, colors, axis, title, onion, key=title)
        self.set_tool("pencil")
        self.zoom_label.setText(f"{self.canvas.zoom * 100}%")
        self.canvas.setFocus()

    # ------------------------------------------------------------ montagem

    def _button(self, a: QAction) -> QToolButton:
        b = QToolButton()
        b.setDefaultAction(a)
        b.setIconSize(QSize(18, 18))
        b.setAutoRaise(True)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    def _action(self, ic: str | None, tip: str, fn, seq=None, checkable: bool = False) -> QAction:
        a = QAction(icon(ic), tip, self) if ic else QAction(tip, self)
        a.setToolTip(tip)
        if seq is not None:
            a.setShortcut(seq)
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        a.setCheckable(checkable)
        (a.toggled if checkable else a.triggered).connect(fn)
        self.addAction(a)
        return a

    def _build_image_menu(self) -> QMenu:
        m = QMenu(self)
        k = QKeySequence
        self.menu_actions = {}

        def add(key, text, fn, seq=None, ic=None):
            a = self._action(ic, text, fn, k(seq) if isinstance(seq, str) else seq)
            m.addAction(a)
            self.menu_actions[key] = a
        add("select_all", tr("Select all"), self.select_all, "Ctrl+A", "select_all")
        add("deselect", tr("Deselect"), lambda: self.canvas.set_selection(None), "Ctrl+D", "select_none")
        add("invert", tr("Invert selection"), self.invert_selection, "Ctrl+Shift+I", "invert_sel")
        add("select_color", tr("Select the primary color in the whole image"), self.select_primary, None, "wand")
        m.addSeparator()
        add("copy", tr("Copy"), self.copy, k.StandardKey.Copy, "copy")
        add("cut", tr("Cut"), self.cut, k.StandardKey.Cut, "cut")
        add("paste", tr("Paste"), self.paste, k.StandardKey.Paste, "paste")
        add("paste_system", tr("Paste image from another program (nearest colors)"), self.paste_system,
            "Ctrl+Shift+V")
        add("clear", tr("Delete the selection"), self.clear_selection, k.StandardKey.Delete, "delete")
        m.addSeparator()
        add("flip_h", tr("Flip horizontally"), lambda: self.flip(True), "Shift+H", "flip_h")
        add("flip_v", tr("Flip vertically"), lambda: self.flip(False), "Shift+V", "flip_v")
        m.addSeparator()
        add("outline", tr("Outer outline with the primary color"), lambda: self.outline(False, False), "Ctrl+Shift+O",
            "outline")
        add("outline_diag", tr("Outer outline with diagonals"), lambda: self.outline(True, False))
        add("outline_in", tr("Inner outline (edge of the figure)"), lambda: self.outline(False, True))
        add("replace", tr("Replace the primary color with the secondary"), self.replace_color, "Ctrl+R", "swap_colors")
        add("despeckle", tr("Remove stray pixels"), self.despeckle, None, "sweep")
        m.addSeparator()
        add("crop_sel", tr("Crop to selection"), self.crop_to_selection, None, "crop")
        add("trim", tr("Trim transparent borders"), self.trim, None, "trim")
        add("canvas", tr("Canvas size…"), self.canvas_size, None, "resize")
        return m

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            QTimer.singleShot(0, self.canvas.fit)      # depois do layout: a tela já tem o tamanho final

    def _toggle(self, attr: str, on: bool) -> None:
        setattr(self.canvas, attr, on)
        self.canvas.update()

    def _toggle_onion(self, on: bool) -> None:
        self.canvas.show_onion = on
        self.canvas.update()
        if on and not self.canvas.onion:
            self.status.setText(tr("No neighbor sprites in the group for the onion skin"))

    # ------------------------------------------------------------ sprite em edição

    def load(self, image: Image.Image, colors, axis, title: str, onion=None, key=None) -> None:
        """Põe um sprite na tela (na abertura e ao navegar para outro)."""
        self.setWindowTitle(title)
        self.key = key if key is not None else title
        self.indexed = image.mode == "P"
        if self.indexed:
            arr = np.array(image, dtype=np.uint8)
            self.colors = list(colors[:256]) + [(0, 0, 0)] * max(0, 256 - len(colors))
        else:
            arr = np.array(image.convert("RGBA"), dtype=np.uint8)
            self.colors = []
        self.canvas.axis = tuple(axis)
        self.canvas.selection = None
        self.canvas.set_image(arr, self.indexed, self.colors)
        self._undo, self._redo = self._histories.get(self.key, ([], []))
        self.modified = False
        self.apply_btn.setEnabled(False)
        # cores iniciais: na abertura (ou ao trocar entre indexado e truecolor) a principal é a
        # primeira cor que o sprite usa; navegando entre sprites parecidos, as escolhidas ficam
        kind = int if self.indexed else tuple
        if not self._colors_set or not isinstance(self.canvas.primary, kind):
            if self.indexed:
                used = np.unique(arr)
                self.canvas.primary = int(used[used > 0][0]) if (used > 0).any() else 1
                self.canvas.secondary = 0
            else:
                self.canvas.primary, self.canvas.secondary = (0, 0, 0, 255), (0, 0, 0, 0)
            self._colors_set = True
        self.canvas.onion = [(tinted(img, ONION_TINTS.get(side, ONION_TINTS["prev"])), oax, oay)
                             for img, (oax, oay), side in onion or []]
        self.onion_action.setEnabled(bool(onion))
        self.more_color.setVisible(not self.indexed)
        self.hint.setText(tr("Left paints with the primary, right with the secondary. ")
                          + (tr("Index 0 is transparency. ") if self.indexed else "")
                          + tr("Alt+click picks the color; Shift+click draws a line. With a selection, the brush stays inside it and the arrows move it. Hover a color to see where it is; the dot marks the colors the image uses."))
        self._fill_swatches()
        self._update_pair()
        self._update_history()
        self._on_hover(-1, -1)
        if getattr(self, "_fitted", False):
            self.canvas.fit()

    def go(self, delta: int) -> None:
        """PgUp/PgDn: grava este sprite (se mudou) e abre o vizinho na mesma janela."""
        if self.navigate is None:
            return
        self._histories[self.key] = (self._undo, self._redo)
        img = self.image() if self.modified else None
        nxt = self.navigate(delta, img, self.canvas.axis)
        if img is not None:
            self.saved_any = True
            self.modified = False
        if nxt:
            self.load(nxt["image"], nxt["colors"], nxt["axis"], nxt["title"], nxt.get("onion"), nxt.get("key"))
        else:
            self.status.setText(tr("No more sprites that way"))

    # ------------------------------------------------------------ cores

    def _qcolor(self, v) -> QColor:
        return self.canvas._color_of(v)

    def _fill_swatches(self) -> None:
        arr = self.canvas.arr
        if self.indexed:
            # até a última cor que existe (não preta) ou que a imagem usa, em linhas de 16: o
            # resto da paleta de 256 costuma ser preto vazio
            last = max([k for k, c in enumerate(self.colors) if any(c)] + [int(arr.max()), 15])
            vals = list(range(min(len(self.colors), (last // 16 + 1) * 16)))
            self.swatches.used = {int(v) for v in np.unique(arr)}
        else:
            a = arr.reshape(-1, 4)
            uniq, counts = np.unique(a[a[:, 3] > 0], axis=0, return_counts=True)
            order = np.argsort(-counts)[:126]
            vals = [(0, 0, 0, 0)] + [tuple(int(c) for c in uniq[i]) for i in order]
            for v in (self.canvas.primary, self.canvas.secondary):
                if v not in vals:
                    vals.append(v)
            self.swatches.used = set(vals[1:1 + len(order)])
        self.swatches.set_values(vals, [self._qcolor(v) for v in vals])

    def set_color(self, v, right: bool = False) -> None:
        if right:
            self.canvas.secondary = v
        else:
            self.canvas.primary = v
        if not self.indexed and v not in self.swatches.values:
            self._fill_swatches()
        self._update_pair()

    def swap_colors(self) -> None:
        c = self.canvas
        c.primary, c.secondary = c.secondary, c.primary
        self._update_pair()

    def choose_rgba(self) -> None:
        c = QColorDialog.getColor(self._qcolor(self.canvas.primary), self, tr("Primary color"),
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.set_color((c.red(), c.green(), c.blue(), c.alpha()))

    def _describe(self, v) -> str:
        c = self._qcolor(v)
        if self.indexed:
            return tr("{0} · transparent").format(v) if v == 0 else f"{v} · {c.name()}"
        return tr("transparent") if c.alpha() == 0 else c.name()

    def _update_pair(self) -> None:
        c = self.canvas
        self.pair.set_colors(self._qcolor(c.primary), self._qcolor(c.secondary))
        self.swatches.primary, self.swatches.secondary = c.primary, c.secondary
        self.swatches.update()
        self.pair_info.setText(tr("Primary: {0}\nSecondary: {1}").format(self._describe(c.primary), self._describe(c.secondary)))

    # ------------------------------------------------------------ ferramentas e histórico

    def set_tool(self, key: str) -> None:
        self.canvas.tool = key
        self.tool_actions[key].setChecked(True)
        self.size.setEnabled(key in ("pencil", "eraser"))
        self.contiguous.setEnabled(key in ("fill", "wand"))

    def _snapshot(self) -> None:
        self._undo.append((self.canvas.arr.copy(), self.canvas.axis))
        del self._undo[:-200]
        self._redo.clear()
        self._update_history()

    def _drop_snapshot(self) -> None:
        if self._undo:
            self._undo.pop()
        self._update_history()

    def _on_changed(self) -> None:
        self.modified = True
        self.apply_btn.setEnabled(True)
        self._update_history()
        self._fill_swatches()

    def _restore(self, src: list, dst: list) -> None:
        if not src:
            return
        dst.append((self.canvas.arr.copy(), self.canvas.axis))
        arr, axis = src.pop()
        self.canvas.axis = axis
        self.canvas.selection = None
        self.canvas.set_image(arr, self.indexed, self.colors)
        self.modified = True
        self.apply_btn.setEnabled(True)
        self._update_history()
        self._fill_swatches()

    def undo(self) -> None:
        self._restore(self._undo, self._redo)

    def redo(self) -> None:
        self._restore(self._redo, self._undo)

    def _update_history(self) -> None:
        self.undo_action.setEnabled(bool(self._undo))
        self.redo_action.setEnabled(bool(self._redo))

    def _edit(self, fn) -> None:
        """Roda uma operação da imagem com desfazer; `fn` devolve False se nada mudou."""
        self._snapshot()
        if fn() is False:
            self._drop_snapshot()
            return
        self.canvas.refresh()
        self._on_changed()

    def _replace_array(self, arr: np.ndarray, dx: int, dy: int) -> None:
        """Troca a imagem por outra de outro tamanho; o eixo anda junto com a figura."""
        ax, ay = self.canvas.axis
        self.canvas.axis = (ax + dx, ay + dy)
        self.canvas.selection = None
        self.canvas.set_image(arr, self.indexed, self.colors)
        self.canvas.selectionChanged.emit()

    # ------------------------------------------------------------ seleção e área de transferência

    def _region(self) -> np.ndarray:
        sel = self.canvas.limit()
        return sel if sel is not None else np.ones(self.canvas.arr.shape[:2], bool)

    def select_all(self) -> None:
        self.canvas.set_selection(np.ones(self.canvas.arr.shape[:2], bool))

    def invert_selection(self) -> None:
        sel = self.canvas.selection
        self.canvas.set_selection(np.ones(self.canvas.arr.shape[:2], bool) if sel is None else ~sel)

    def select_primary(self) -> None:
        self.canvas.set_selection(same_color(self.canvas.arr, self.canvas.primary))

    def copy(self) -> bool:
        a = self.canvas.arr
        sel = self.canvas.limit()
        mask = (sel if sel is not None else opaque(a)).copy()
        box = bbox(mask)
        if box is None:
            self.status.setText(tr("Nothing to copy"))
            return False
        x0, y0, x1, y1 = box
        self._clip = (a[y0:y1 + 1, x0:x1 + 1].copy(), mask[y0:y1 + 1, x0:x1 + 1].copy(), x0, y0)
        part = self.image().convert("RGBA").crop((x0, y0, x1 + 1, y1 + 1))
        m = np.array(part)
        keep = mask[y0:y1 + 1, x0:x1 + 1] & opaque(a[y0:y1 + 1, x0:x1 + 1])
        m[..., 3] = np.where(keep, m[..., 3], 0)
        h, w = m.shape[:2]
        QGuiApplication.clipboard().setImage(QImage(m.tobytes(), w, h, w * 4, QImage.Format.Format_RGBA8888).copy())
        self.status.setText(tr("Copied: {0}×{1} px").format(w, h))
        return True

    def cut(self) -> None:
        if self.copy():
            self.clear_selection()

    def clear_selection(self) -> None:
        reg = self.canvas.limit()
        if reg is None:
            self.status.setText(tr("Select something to delete (Ctrl+A selects all)"))
            return

        def run():
            if not opaque(self.canvas.arr)[reg].any():
                return False
            self.canvas.arr[reg] = self.canvas.eraser_value()
        self._edit(run)

    def _stamp(self, vals: np.ndarray, mask: np.ndarray, x: int, y: int) -> None:
        a = self.canvas.arr
        h, w = a.shape[:2]
        full_v = np.zeros_like(a)
        full_m = np.zeros((h, w), bool)
        ph, pw = mask.shape
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + pw), min(h, y + ph)
        if x0 >= x1 or y0 >= y1:
            return
        full_v[y0:y1, x0:x1] = vals[y0 - y:y1 - y, x0 - x:x1 - x]
        full_m[y0:y1, x0:x1] = mask[y0 - y:y1 - y, x0 - x:x1 - x]

        def run():
            a[full_m] = full_v[full_m]
        self._edit(run)
        self.canvas.set_selection(full_m)
        self.status.setText(tr("Pasted: drag it or use the arrows to place it"))

    def paste(self) -> None:
        """Cola o que foi copiado nesta janela no mesmo lugar, já selecionado para mover; sem
        cópia daqui, usa a imagem da área de transferência do sistema."""
        if self._clip is None or (not self.indexed and self._clip[0].ndim == 2) or \
                (self.indexed and self._clip[0].ndim == 3):
            self.paste_system()
            return
        vals, mask, x, y = self._clip
        self._stamp(vals, mask, x, y)

    def paste_system(self) -> None:
        q = QGuiApplication.clipboard().image()
        if q.isNull():
            self.status.setText(tr("The clipboard has no image"))
            return
        q = q.convertToFormat(QImage.Format.Format_RGBA8888)
        rgba = np.frombuffer(bytes(q.constBits()), np.uint8).reshape(q.height(), q.bytesPerLine())
        rgba = rgba[:, :q.width() * 4].reshape(q.height(), q.width(), 4)
        mask = rgba[..., 3] >= 128
        vals = nearest_indices(rgba, self.colors) if self.indexed else rgba.copy()
        h, w = self.canvas.arr.shape[:2]
        self._stamp(vals, mask, max(0, (w - q.width()) // 2), max(0, (h - q.height()) // 2))

    # ------------------------------------------------------------ operações da imagem

    def flip(self, horizontal: bool) -> None:
        """Espelha a seleção no próprio lugar (ou a imagem toda, sem seleção)."""
        a = self.canvas.arr
        sel = self.canvas.limit()

        def run():
            if sel is None:
                a[...] = a[:, ::-1] if horizontal else a[::-1]
                return None
            x0, y0, x1, y1 = bbox(sel)
            block, m = a[y0:y1 + 1, x0:x1 + 1].copy(), sel[y0:y1 + 1, x0:x1 + 1]
            fb, fm = (block[:, ::-1], m[:, ::-1]) if horizontal else (block[::-1], m[::-1])
            sub = a[y0:y1 + 1, x0:x1 + 1]
            sub[m] = self.canvas.eraser_value()
            sub[fm] = fb[fm]
            new = sel.copy()
            new[y0:y1 + 1, x0:x1 + 1] = fm
            self.canvas.selection = new
            self.canvas._edges = None
            return None
        self._edit(run)

    def outline(self, diagonal: bool, inside: bool) -> None:
        a = self.canvas.arr
        sel = self.canvas.limit()

        def run():
            m = outline_mask(a, diagonal, inside, within=sel)
            if not m.any():
                return False
            a[m] = self.canvas.primary
        self._edit(run)

    def replace_color(self) -> None:
        a = self.canvas.arr
        src, dst = self.canvas.primary, self.canvas.secondary

        def run():
            m = same_color(a, src) & self._region()
            if not m.any() or src == dst:
                return False
            a[m] = dst
        self._edit(run)
        self.status.setText(f"{self._describe(src)} → {self._describe(dst)}")

    def despeckle(self) -> None:
        a = self.canvas.arr

        def run():
            m = isolated_mask(a) & self._region()
            if not m.any():
                return False
            a[m] = self.canvas.eraser_value()
            self.status.setText(tr("{0} stray pixel(s) removed").format(int(m.sum())))
        self._edit(run)

    def crop_to_selection(self) -> None:
        sel = self.canvas.limit()
        if sel is None:
            self.status.setText(tr("Make a selection to crop"))
            return
        x0, y0, x1, y1 = bbox(sel)
        a = self.canvas.arr
        self._snapshot()
        self._replace_array(a[y0:y1 + 1, x0:x1 + 1].copy(), -x0, -y0)
        self._on_changed()

    def trim(self) -> None:
        a = self.canvas.arr
        box = bbox(opaque(a))
        if box is None or box == (0, 0, a.shape[1] - 1, a.shape[0] - 1):
            self.status.setText(tr("There is no transparent border to trim"))
            return
        x0, y0, x1, y1 = box
        self._snapshot()
        self._replace_array(a[y0:y1 + 1, x0:x1 + 1].copy(), -x0, -y0)
        self._on_changed()

    def canvas_size(self) -> None:
        h, w = self.canvas.arr.shape[:2]
        dlg = CanvasSizeDialog(self, w, h)
        if not dlg.exec():
            return
        self.set_canvas_size(*dlg.values())

    def set_canvas_size(self, left: int, top: int, right: int, bottom: int) -> None:
        if not any((left, top, right, bottom)):
            return
        try:
            arr = resize_canvas(self.canvas.arr, left, top, right, bottom)
        except ValueError as e:
            self.status.setText(str(e))
            return
        self._snapshot()
        self._replace_array(arr, left, top)
        self._on_changed()

    # ------------------------------------------------------------ status

    def _on_hover(self, x: int, y: int) -> None:
        if self.canvas.arr is None:
            return
        h, w = self.canvas.arr.shape[:2]
        parts = [f"{w}×{h} px"]
        sel = self.canvas.selection
        box = bbox(sel) if sel is not None else None
        if box:
            parts.append(tr("selection {0}×{1}").format(box[2] - box[0] + 1, box[3] - box[1] + 1))
        if x < 0:
            parts.append(tr("mouse wheel = zoom · middle button or space+drag moves the view"))
        else:
            ax, ay = self.canvas.axis
            v = self.canvas.arr[y, x]
            v = int(v) if self.indexed else tuple(int(c) for c in v)
            parts.append(tr("pixel {0}, {1} · from axis {2}, {3} · color {4}").format(x, y, x - ax, y - ay, self._describe(v)))
        self.status.setText(" · ".join(parts))

    # ------------------------------------------------------------ fechar

    def image(self) -> Image.Image:
        if self.indexed:
            img = Image.fromarray(self.canvas.arr, "P")
            flat = [c for col in self.colors for c in col]
            img.putpalette(flat[:768] + [0] * (768 - len(flat[:768])))
            return img
        return Image.fromarray(self.canvas.arr, "RGBA")

    def accept(self) -> None:
        if not self.modified:
            return super().accept() if self.saved_any else super().reject()
        self.result_image = self.image()
        self.result_axis = self.canvas.axis
        super().accept()

    def reject(self) -> None:
        if self.modified:
            r = QMessageBox.question(self, tr("Discard changes"),
                                     tr("Close without applying? The changes to this image will be lost."),
                                     QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                     QMessageBox.StandardButton.Cancel)
            if r != QMessageBox.StandardButton.Discard:
                return
        super().reject()
