"""Canvas de sprites/animações com zoom, eixo e edição de caixas Clsn."""
from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .theme import Theme
from ..i18n import tr


def pil_to_qimage(img: Image.Image) -> QImage:
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    q = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    return q.copy()


class SpriteCanvas(QWidget):
    """Mostra uma imagem posicionada pelo eixo, com caixas de colisão opcionais.

    Coordenadas "do personagem": origem no eixo (0,0); x para a direita, y para baixo.
    """
    axisPicked = Signal(int, int)      # nova posição do eixo, relativa ao canto do sprite
    boxesChanged = Signal()
    zoomChanged = Signal(int)
    imageDragged = Signal(int, int, bool)  # deslocamento desde o início do Ctrl+arrastar; True = soltou
    nudged = Signal(int, int)               # setas do teclado (Shift = 10 px)
    doubleClicked = Signal()                # duplo clique no sprite (o editor de sprites abre a edição)
    drawModeExit = Signal()                 # Esc com a ferramenta de caixa ligada

    HANDLE = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.theme: Theme | None = None
        self.image: QImage | None = None
        self.blend = ""                 # transparência do AIR do sprite principal (A, A1, S, AS…D…)
        self.onion: QImage | None = None
        self.onion_pos = QPoint(0, 0)
        self.image_pos = QPoint(0, 0)   # canto superior esquerdo, em coords do personagem
        self.zoom = 2
        self.pan = QPointF(0, 0)
        self.boxes: list[list] = []     # [[tipo(1|2), [x1,y1,x2,y2]], ...]
        self.edit_boxes = False
        self.pick_axis = False
        self.new_box_kind = 2
        self.draw_box_kind: int | None = None   # ferramenta de caixa ligada: arrastar desenha uma nova
        self.selected: int | None = None
        self._drag = None
        self._pan_start = None
        self._img_drag = None
        self._frozen_origin: QPointF | None = None   # origem parada durante o Ctrl+arrastar
        self.drag_image = False     # Ctrl+arrastar move o sprite (offset do frame / eixo do sprite)
        self.box_moved = False
        self.show_axis = True
        self.center_image = False  # True: centraliza o sprite (editor de sprites); False: eixo no chão
        self.show_grid = True
        self.show_rulers = True
        self.show_attack = True
        self.show_collision = True

    # ------------------------------------------------------------ dados

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.update()

    def set_image(self, img: Image.Image | None, axis_x: int = 0, axis_y: int = 0,
                  offset: tuple[int, int] = (0, 0)) -> None:
        self.image = pil_to_qimage(img) if img is not None else None
        self.image_pos = QPoint(offset[0] - axis_x, offset[1] - axis_y)
        self.update()

    def set_blend(self, trans: str) -> None:
        if trans != self.blend:
            self.blend = trans or ""
            self.update()

    def set_onion(self, img: Image.Image | None, axis_x: int = 0, axis_y: int = 0,
                  offset: tuple[int, int] = (0, 0)) -> None:
        self.onion = pil_to_qimage(img) if img is not None else None
        self.onion_pos = QPoint(offset[0] - axis_x, offset[1] - axis_y)
        self.update()

    def set_boxes(self, clsn1: list, clsn2: list) -> None:
        self.boxes = [[1, list(b)] for b in clsn1] + [[2, list(b)] for b in clsn2]
        self.selected = None
        self.update()

    def get_boxes(self) -> tuple[list, list]:
        norm = lambda b: (min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3]))
        return ([norm(b) for k, b in self.boxes if k == 1], [norm(b) for k, b in self.boxes if k == 2])

    # ------------------------------------------------------------ coordenadas

    def origin(self) -> QPointF:
        if self._frozen_origin is not None:
            return self._frozen_origin
        if self.center_image and self.image is not None:
            cx = self.image_pos.x() + self.image.width() / 2
            cy = self.image_pos.y() + self.image.height() / 2
            return QPointF(self.width() / 2 - cx * self.zoom, self.height() / 2 - cy * self.zoom) + self.pan
        return QPointF(self.width() / 2, self.height() * 0.78) + self.pan

    def fit(self, max_zoom: int = 4) -> None:
        """Maior zoom (até max_zoom) em que a imagem cabe inteira."""
        if self.image is None:
            return
        z = max_zoom
        while z > 1 and (self.image.width() * z > self.width() * 0.9 or self.image.height() * z > self.height() * 0.9):
            z -= 1
        self.pan = QPointF(0, 0)
        self.set_zoom(z)

    def to_screen(self, x: float, y: float) -> QPointF:
        o = self.origin()
        return QPointF(o.x() + x * self.zoom, o.y() + y * self.zoom)

    def to_char(self, p: QPointF) -> tuple[int, int]:
        o = self.origin()
        return round((p.x() - o.x()) / self.zoom), round((p.y() - o.y()) / self.zoom)

    def set_zoom(self, z: int) -> None:
        self.zoom = max(1, min(16, z))
        self.zoomChanged.emit(self.zoom)
        self.update()

    # ------------------------------------------------------------ desenho

    def paintEvent(self, _):
        from ..tools.render import trans_factors
        if self.image is not None and trans_factors(self.blend) is not None:
            # soma/subtração precisam do fundo já desenhado: pinta tudo numa imagem e mistura
            dpr = self.devicePixelRatioF()
            buf = QImage(int(self.width() * dpr), int(self.height() * dpr), QImage.Format.Format_RGB32)
            buf.setDevicePixelRatio(dpr)
            bp = QPainter(buf)
            self._paint_scene(bp, blended=True)
            bp.end()
            self._blend_sprite(buf, dpr)
            p = QPainter(self)
            p.drawImage(0, 0, buf)
            self._paint_overlay(p)
            p.end()
            return
        p = QPainter(self)
        self._paint_scene(p, blended=False)
        self._paint_overlay(p)
        p.end()

    def _sprite_rect(self) -> QRectF:
        tl = self.to_screen(self.image_pos.x(), self.image_pos.y())
        return QRectF(tl.x(), tl.y(), self.image.width() * self.zoom, self.image.height() * self.zoom)

    def _blend_sprite(self, buf: QImage, dpr: float) -> None:
        """Mistura o sprite principal sobre o fundo de buf com a transparência do AIR."""
        import numpy as np
        from ..tools.render import blend_layer, blend_lut
        r = self._sprite_rect()
        x0, y0 = max(0, int(r.left() * dpr)), max(0, int(r.top() * dpr))
        x1, y1 = min(buf.width(), int(r.right() * dpr) + 1), min(buf.height(), int(r.bottom() * dpr) + 1)
        if x1 <= x0 or y1 <= y0:
            return
        # camada só do tamanho do sprite na tela (em pixels do aparelho), não da tela inteira
        lw, lh = x1 - x0, y1 - y0
        layer = QImage(lw, lh, QImage.Format.Format_RGBA8888)
        layer.fill(Qt.GlobalColor.transparent)
        lp = QPainter(layer)
        lp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        lp.drawImage(QRectF(r.left() * dpr - x0, r.top() * dpr - y0, r.width() * dpr, r.height() * dpr), self.image)
        lp.end()
        w = buf.width()
        dst = np.frombuffer(buf.bits(), np.uint8, buf.bytesPerLine() * buf.height()).reshape(
            buf.height(), buf.bytesPerLine() // 4, 4)[:, :w]
        src = np.frombuffer(layer.constBits(), np.uint8, layer.bytesPerLine() * lh).reshape(
            lh, layer.bytesPerLine() // 4, 4)[:, :lw]
        d = dst[y0:y1, x0:x1]
        alpha = src[..., 3]
        if not np.count_nonzero((alpha != 0) & (alpha != 255)):
            # sprite indexado (alfa só 0 ou 255): tabela pronta por canal, mesmo resultado da
            # conta em float e ~5x mais rápido (efeitos aditivos tocam sem engasgar)
            on = alpha == 255
            lut_on, lut_off = blend_lut(self.blend, 255).ravel(), blend_lut(self.blend, 0)
            keep = (lut_off == np.arange(256, dtype=np.uint8)[None, :]).all()   # alfa 0 = fundo intacto
            for c_dst, c_src in ((2, 0), (1, 1), (0, 2)):     # RGB32 na memória é B, G, R, X
                dc = d[..., c_dst]
                idx = (src[..., c_src].astype(np.uint16) << 8) | dc
                if keep:
                    np.copyto(dc, lut_on.take(idx), where=on)
                else:
                    np.copyto(dc, np.where(on, lut_on.take(idx), lut_off.ravel().take(idx)))
            return
        bgr = d[..., :3].astype(np.float32) / 255
        rgb = bgr[..., ::-1]                         # RGB32 na memória é B, G, R, X
        sr = src.astype(np.float32) / 255
        out = blend_layer(rgb, sr, self.blend)
        d[..., :3] = (out[..., ::-1] * 255 + 0.5).astype(np.uint8)

    def _paint_scene(self, p: QPainter, blended: bool) -> None:
        """Fundo, grade, chão, onion skin e (sem mistura) o sprite."""
        t = self.theme
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(t.c("back_start").hex if t else "#555"))
        grad.setColorAt(1, QColor(t.c("back_end").hex if t else "#333"))
        p.fillRect(self.rect(), grad)
        o = self.origin()
        if self.show_grid:
            self._draw_grid(p, o)
        # linha do chão
        ground = QColor(self.theme.ui["text_dim"] if self.theme else "#888")
        ground.setAlpha(90)
        p.setPen(QPen(ground, 1, Qt.PenStyle.DashLine))
        p.drawLine(0, int(o.y()), self.width(), int(o.y()))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        if self.onion is not None:
            p.setOpacity(getattr(self, "onion_opacity", 0.35))
            tl = self.to_screen(self.onion_pos.x(), self.onion_pos.y())
            p.drawImage(QRectF(tl.x(), tl.y(), self.onion.width() * self.zoom,
                               self.onion.height() * self.zoom), self.onion)
            p.setOpacity(1.0)
        if self.image is not None and not blended:
            p.drawImage(self._sprite_rect(), self.image)

    def _paint_overlay(self, p: QPainter) -> None:
        """Caixas, eixo, réguas e zoom, sempre por cima e sem mistura."""
        t = self.theme
        o = self.origin()
        for i, (kind, b) in enumerate(self.boxes):
            if (kind == 1 and not self.show_attack) or (kind == 2 and not self.show_collision):
                continue
            col = QColor(t.c("attackbox" if kind == 1 else "collisionbox").hex if t else
                         ("#ff3030" if kind == 1 else "#3070ff"))
            r = self._box_rect(b)
            fill = QColor(col)
            fill.setAlpha(70 if i == self.selected else 40)
            p.fillRect(r, fill)
            p.setPen(QPen(col, 2 if i == self.selected else 1))
            p.drawRect(r)
            if i == self.selected and self.edit_boxes:
                for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                    p.fillRect(QRectF(c.x() - 3, c.y() - 3, 6, 6), col)
        if self.show_axis:
            p.setPen(QPen(QColor(self.theme.ui["accent"] if self.theme else "#6366f1"), 1.5))
            p.drawLine(int(o.x()) - 8, int(o.y()), int(o.x()) + 8, int(o.y()))
            p.drawLine(int(o.x()), int(o.y()) - 8, int(o.x()), int(o.y()) + 8)
        if self.show_rulers:
            self._draw_rulers(p, o)
        # rótulo do zoom num chip com a cor do painel: legível no tema claro e no escuro
        label = f"{self.zoom}x" + (tr("   ·   transparency {0}").format(self.blend.upper()) if self.blend and self.image is not None else "")
        fm = p.fontMetrics()
        chip = QRectF(30, self.height() - 10 - fm.height() - 4, fm.horizontalAdvance(label) + 14, fm.height() + 6)
        ui = self.theme.ui if self.theme else {}
        bg = QColor(ui.get("panel", "#2b2d33"))
        bg.setAlpha(220)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(chip, 6, 6)
        p.setPen(QColor(ui.get("text_dim", "#b0b0b0")))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)

    RULER = 22

    def _grid_step(self) -> int:
        for step in (5, 10, 20, 50, 100):
            if step * self.zoom >= 28:
                return step
        return 200

    def _draw_grid(self, p: QPainter, o: QPointF) -> None:
        """Grade de pontos (estilo 'dot grid'); a cada 5 passos um ponto mais forte."""
        base = QColor(self.theme.ui["text_dim"] if self.theme else "#888")
        minor, major = QColor(base), QColor(base)
        minor.setAlpha(55)
        major.setAlpha(120)
        step = self._grid_step() * self.zoom
        ox, oy = o.x() % step, o.y() % step
        ix0 = int((o.x() - ox) / step)
        iy0 = int((o.y() - oy) / step)
        weak, strong_pts = QPolygonF(), QPolygonF()     # um drawPoints por cor, não um por ponto
        y, j = oy, 0
        while y < self.height():
            x, i = ox, 0
            while x < self.width():
                strong = (ix0 - i) % 5 == 0 and (iy0 - j) % 5 == 0
                (strong_pts if strong else weak).append(QPointF(x, y))
                x += step
                i += 1
            y += step
            j += 1
        p.setPen(QPen(minor, 1.6))
        p.drawPoints(weak)
        p.setPen(QPen(major, 2.2))
        p.drawPoints(strong_pts)

    def _draw_rulers(self, p: QPainter, o: QPointF) -> None:
        r = self.RULER
        bg = QColor(self.theme.ui["panel"] if self.theme else "#2b2d33")
        fg = QColor(self.theme.ui["text_dim"] if self.theme else "#999")
        border = QColor(self.theme.ui["border"] if self.theme else "#444")
        p.fillRect(0, 0, self.width(), r, bg)
        p.fillRect(0, 0, r + 8, self.height(), bg)
        p.setPen(QPen(border, 1))
        p.drawLine(r + 8, r, self.width(), r)
        p.drawLine(r + 8, r, r + 8, self.height())
        f = p.font()
        f.setPointSize(7)
        p.setFont(f)
        p.setPen(fg)
        step = self._grid_step()
        minor = max(1, step // 5)
        start = -int(o.x() / self.zoom) - step
        v = start - start % minor
        while (o.x() + v * self.zoom) < self.width():
            x = o.x() + v * self.zoom
            if x > r + 8:
                major = v % step == 0
                p.drawLine(int(x), r - (9 if major else 4), int(x), r)
                if major:
                    p.drawText(int(x) + 2, 10, str(v))
            v += minor
        start = -int(o.y() / self.zoom) - step
        v = start - start % minor
        while (o.y() + v * self.zoom) < self.height():
            y = o.y() + v * self.zoom
            if y > r:
                major = v % step == 0
                p.drawLine(r + 8 - (9 if major else 4), int(y), r + 8, int(y))
                if major:
                    p.save()
                    p.translate(10, int(y) - 2)
                    p.rotate(-90)
                    p.drawText(0, 0, str(-v))
                    p.restore()
            v += minor

    def _box_rect(self, b) -> QRectF:
        a, c = self.to_screen(min(b[0], b[2]), min(b[1], b[3])), self.to_screen(max(b[0], b[2]), max(b[1], b[3]))
        return QRectF(a, c)

    # ------------------------------------------------------------ interação

    def wheelEvent(self, e):
        self.set_zoom(self.zoom + (1 if e.angleDelta().y() > 0 else -1))

    def _hit(self, pos: QPointF):
        """(índice, alça) da caixa sob o cursor; alça: 'move' ou combinação de l/r/t/b."""
        for i in reversed(range(len(self.boxes))):
            r = self._box_rect(self.boxes[i][1]).adjusted(-self.HANDLE, -self.HANDLE, self.HANDLE, self.HANDLE)
            if not r.contains(pos):
                continue
            inner = r.adjusted(2 * self.HANDLE, 2 * self.HANDLE, -2 * self.HANDLE, -2 * self.HANDLE)
            h = ""
            if pos.x() < inner.left():
                h += "l"
            elif pos.x() > inner.right():
                h += "r"
            if pos.y() < inner.top():
                h += "t"
            elif pos.y() > inner.bottom():
                h += "b"
            return i, h or "move"
        return None, None

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if self.image is not None and not self.edit_boxes and self._sprite_rect().contains(e.position()):
            self.doubleClicked.emit()
        else:
            super().mouseDoubleClickEvent(e)

    def mousePressEvent(self, e):
        pos = e.position()
        if e.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._pan_start = (pos, QPointF(self.pan))
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.pick_axis and self.image is not None:
            x, y = self.to_char(pos)
            # novo eixo relativo ao canto do sprite
            self.axisPicked.emit(x - self.image_pos.x(), y - self.image_pos.y())
            return
        if self.drag_image and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # o deslocamento é medido na tela e a origem fica parada: com a figura centralizada, mudar
            # o eixo movia a origem a cada passo e o eixo fugia do mouse
            self._img_drag = pos
            self._frozen_origin = self.origin()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if not self.edit_boxes:
            return
        x, y = self.to_char(pos)
        self.box_moved = False
        i, handle = self._hit(pos)
        # ferramenta ligada (ou Shift): desenha uma caixa nova; com a ferramenta, pegar a borda ou
        # o canto de uma caixa ainda redimensiona em vez de começar outra por cima
        drawing = self.draw_box_kind is not None and handle in (None, "move")
        if drawing or e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            kind = self.draw_box_kind or self.new_box_kind
            self.boxes.append([kind, [x, y, x, y]])
            self.selected = len(self.boxes) - 1
            self._drag = ("rb", (x, y), list(self.boxes[-1][1]))
        else:
            self.selected = i
            if i is not None:
                self._drag = (handle, (x, y), list(self.boxes[i][1]))
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._pan_start:
            start, pan0 = self._pan_start
            self.pan = pan0 + (pos - start)
            self.update()
            return
        if self._img_drag:
            self.imageDragged.emit(*self._img_delta(pos), False)
            return
        if self.edit_boxes and not self._drag:
            _, h = self._hit(pos)
            cursors = {"move": Qt.CursorShape.SizeAllCursor, "l": Qt.CursorShape.SizeHorCursor,
                       "r": Qt.CursorShape.SizeHorCursor, "t": Qt.CursorShape.SizeVerCursor,
                       "b": Qt.CursorShape.SizeVerCursor, "lt": Qt.CursorShape.SizeFDiagCursor,
                       "rb": Qt.CursorShape.SizeFDiagCursor, "rt": Qt.CursorShape.SizeBDiagCursor,
                       "lb": Qt.CursorShape.SizeBDiagCursor}
            if self.draw_box_kind is not None and h in (None, "move"):
                h = None                     # desenhando: a cruz mostra que arrastar cria caixa
            self.setCursor(cursors.get(h, Qt.CursorShape.CrossCursor if self.draw_box_kind is not None
                                       else Qt.CursorShape.ArrowCursor))
        if not self._drag or self.selected is None:
            return
        handle, (sx, sy), orig = self._drag
        x, y = self.to_char(pos)
        dx, dy = x - sx, y - sy
        b = list(orig)
        x1, y1, x2, y2 = min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3])
        if handle == "move":
            x1, x2, y1, y2 = x1 + dx, x2 + dx, y1 + dy, y2 + dy
        else:
            if "l" in handle:
                x1 += dx
            if "r" in handle:
                x2 += dx
            if "t" in handle:
                y1 += dy
            if "b" in handle:
                y2 += dy
        if [x1, y1, x2, y2] != list(self.boxes[self.selected][1]):
            self.box_moved = True
        self.boxes[self.selected][1] = [x1, y1, x2, y2]
        self.update()

    def mouseReleaseEvent(self, e):
        if self._pan_start:
            self._pan_start = None
            return
        if self._img_drag:
            self.imageDragged.emit(*self._img_delta(e.position()), True)
            self._img_drag = None
            frozen, self._frozen_origin = self._frozen_origin, None
            self.pan += frozen - self.origin()     # a vista fica onde estava ao soltar
            self.update()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._drag:
            self._drag = None
            changed = self.box_moved
            if self.selected is not None:
                b = self.boxes[self.selected][1]
                if b[0] == b[2] or b[1] == b[3]:  # caixa de tamanho zero: descarta
                    del self.boxes[self.selected]
                    self.selected = None
                    changed = True
            if changed:                           # só clicar para selecionar não altera nada
                self.boxesChanged.emit()
            self.update()

    def _img_delta(self, pos: QPointF) -> tuple[int, int]:
        d = (pos - self._img_drag) / self.zoom
        return round(d.x()), round(d.y())

    def update_cursor(self) -> None:
        self.setCursor(Qt.CursorShape.CrossCursor if self.draw_box_kind is not None else Qt.CursorShape.ArrowCursor)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.draw_box_kind is not None:
            self.drawModeExit.emit()
            return
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected is not None and self.edit_boxes:
            del self.boxes[self.selected]
            self.selected = None
            self.boxesChanged.emit()
            self.update()
        elif e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx = {Qt.Key.Key_Left: -step, Qt.Key.Key_Right: step}.get(e.key(), 0)
            dy = {Qt.Key.Key_Up: -step, Qt.Key.Key_Down: step}.get(e.key(), 0)
            if self.selected is not None and self.edit_boxes:   # move a caixa selecionada
                b = self.boxes[self.selected][1]
                self.boxes[self.selected][1] = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]
                self.boxesChanged.emit()
                self.update()
            else:
                self.nudged.emit(dx, dy)
        elif e.key() == Qt.Key.Key_Home:
            self.pan = QPointF(0, 0)
            self.update()
        else:
            super().keyPressEvent(e)
