"""Ícones de linha (estilo Lucide/Heroicons), desenhados em tempo de execução.

Grade de 24×24, traço 1,8 com pontas e junções arredondadas. As cores vêm do tema atual
(set_colors), e o QIconEngine pinta na hora: ao trocar o tema, os ícones acompanham sem
precisar ser recriados. Ícone marcado/ativo usa a cor de destaque.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap

_COLORS = {"normal": "#52525b", "active": "#6366f1", "disabled": "#a1a1aa",
           "red": "#ef4444", "blue": "#3b82f6", "green": "#22c55e",
           "error": "#f87171", "warning": "#fbbf24"}


def set_colors(normal: str, active: str, disabled: str, error: str | None = None,
               warning: str | None = None) -> None:
    _COLORS.update(normal=normal, active=active, disabled=disabled)
    if error:
        _COLORS["error"] = error
    if warning:
        _COLORS["warning"] = warning


# ---------------------------------------------------------------- primitivas

class _Pen:
    def __init__(self, p: QPainter, color: QColor, width: float = 1.8):
        self.p = p
        self.color = color
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def line(self, *pts):
        for a, b in zip(pts, pts[1:]):
            self.p.drawLine(QPointF(*a), QPointF(*b))

    def poly(self, *pts, close=False, fill=False):
        path = QPainterPath(QPointF(*pts[0]))
        for pt in pts[1:]:
            path.lineTo(QPointF(*pt))
        if close:
            path.closeSubpath()
        if fill:
            self.p.fillPath(path, self.color)
        self.p.drawPath(path)

    def rect(self, x, y, w, h, r=2.0, fill=None):
        if fill is not None:
            self.p.setBrush(fill)
        self.p.drawRoundedRect(QRectF(x, y, w, h), r, r)
        self.p.setBrush(Qt.BrushStyle.NoBrush)

    def circle(self, cx, cy, r, fill=False):
        if fill:
            self.p.setBrush(self.color)
        self.p.drawEllipse(QPointF(cx, cy), r, r)
        self.p.setBrush(Qt.BrushStyle.NoBrush)

    def arc(self, cx, cy, r, start, span):
        self.p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), int(start * 16), int(span * 16))

    def text(self, s, size=7.5, bold=True):
        f = QFont("Segoe UI", 1)
        f.setPointSizeF(size)
        f.setBold(bold)
        self.p.setFont(f)
        self.p.drawText(QRectF(0, 0, 24, 24), Qt.AlignmentFlag.AlignCenter, s)


def _arrow_head(d: _Pen, x, y, angle_deg, size=3.5):
    a = math.radians(angle_deg)
    for s in (-1, 1):
        b = a + math.pi + s * math.radians(40)
        d.line((x, y), (x + size * math.cos(b), y + size * math.sin(b)))


# ---------------------------------------------------------------- desenhos

def _draw(name: str, d: _Pen) -> None:
    L, R, P, C = d.line, d.rect, d.poly, d.circle
    if name == "new":                       # adicionar um item (camada, imagem, parâmetro…)
        L((12, 4.5), (12, 19.5))
        L((4.5, 12), (19.5, 12))
    elif name == "file_new":                # novo personagem: folha com +
        P((6, 3), (14, 3), (19, 8), (19, 21), (6, 21), close=True)
        L((14, 3), (14, 8), (19, 8))
        L((12.5, 11), (12.5, 18))
        L((9, 14.5), (16, 14.5))
    elif name == "open":
        P((3, 7), (3, 19), (21, 19), (21, 9), (11, 9), (9, 6), (3, 6), close=True)
    elif name == "save":
        P((5, 3), (16, 3), (21, 8), (21, 21), (3, 21), (3, 5), (5, 3))
        R(7, 13, 10, 8, 1)
        L((8, 3), (8, 8), (15, 8))
    elif name == "close":
        L((6, 6), (18, 18))
        L((18, 6), (6, 18))
    elif name == "delete":
        L((4, 7), (20, 7))
        P((6, 7), (7, 20), (17, 20), (18, 7))
        P((9, 7), (9, 4), (15, 4), (15, 7))
        L((10, 11), (10, 16))
        L((14, 11), (14, 16))
    elif name in ("duplicate", "copy"):
        R(8, 8, 12, 12, 2.5)
        P((16, 8), (16, 5), (14, 4), (6, 4), (4, 6), (4, 14), (5, 16), (8, 16))
        if name == "duplicate":
            L((14, 11), (14, 17))
            L((11, 14), (17, 14))
    elif name == "edit":
        P((15, 4), (20, 9), (9, 20), (4, 20), (4, 15), close=True)
        L((13, 6), (18, 11))
    elif name in ("undo", "redo"):
        m = (lambda x: 24 - x) if name == "redo" else (lambda x: x)  # redo = undo espelhado
        path = QPainterPath(QPointF(m(20), 19))
        path.cubicTo(QPointF(m(20), 12), QPointF(m(15), 8.5), QPointF(m(9), 8.5))
        path.lineTo(QPointF(m(4), 8.5))
        d.p.drawPath(path)
        P((m(8), 4.5), (m(4), 8.5), (m(8), 12.5))
    elif name == "cut":
        C(6.5, 17, 2.8)
        C(17.5, 17, 2.8)
        L((8.6, 15.2), (18, 4))
        L((15.4, 15.2), (6, 4))
    elif name == "paste":
        R(5, 5, 14, 16, 2.5)
        R(9, 3, 6, 4, 1.2)
    elif name in ("find", "zoom_in", "zoom_out"):
        C(10.5, 10.5, 6.5)
        L((15.5, 15.5), (21, 21))
        if name != "find":
            L((7.5, 10.5), (13.5, 10.5))
        if name == "zoom_in":
            L((10.5, 7.5), (10.5, 13.5))
    elif name in ("left", "right", "up", "down"):
        pts = {"left": [(15, 5), (8, 12), (15, 19)], "right": [(9, 5), (16, 12), (9, 19)],
               "up": [(5, 15), (12, 8), (19, 15)], "down": [(5, 9), (12, 16), (19, 9)]}[name]
        P(*pts)
    elif name in ("prev_error", "next_error"):
        C(12, 12, 9)
        if name == "prev_error":
            P((14, 8), (10, 12), (14, 16))
        else:
            P((10, 8), (14, 12), (10, 16))
    elif name == "apply_next":
        R(2.5, 7, 7, 10, 1.5)
        L((11, 12), (15, 12))
        _arrow_head(d, 15.5, 12, 0, 2.6)
        R(17, 5, 4.5, 6, 1)
        R(17, 13, 4.5, 6, 1)
    elif name == "goto":
        L((12, 4), (12, 15))
        P((7, 11), (12, 16), (17, 11))
        L((5, 20), (19, 20))
    elif name == "insert":
        P((20, 4), (20, 11), (18, 14), (6, 14))
        P((10, 10), (6, 14), (10, 18))
    elif name == "comment":
        C(6, 8.5, 1.3, fill=True)
        L((6, 14.5), (4.8, 18))
        C(6, 14.5, 1.1, fill=True)
        L((11, 8.5), (20, 8.5))
        L((11, 14.5), (17, 14.5))
    elif name == "zoom_1":
        R(3, 5, 18, 14, 3)
        d.text("1:1", 6.5)
    elif name == "fit":
        P((4, 9), (4, 4), (9, 4))
        P((15, 4), (20, 4), (20, 9))
        P((20, 15), (20, 20), (15, 20))
        P((9, 20), (4, 20), (4, 15))
    elif name == "center":
        C(12, 12, 7)
        for a, b in (((12, 2), (12, 7)), ((12, 17), (12, 22)), ((2, 12), (7, 12)), ((17, 12), (22, 12))):
            L(a, b)
    elif name == "guides":
        R(3, 3, 18, 18, 2.5)
        L((9, 3), (9, 21))
        L((15, 3), (15, 21))
        L((3, 9), (21, 9))
        L((3, 15), (21, 15))
    elif name == "sheet":                   # recortar sprite sheet: folha com linhas de corte
        R(3, 3, 18, 18, 2)
        _dashed(d)
        L((12, 3), (12, 21))
        L((3, 12), (21, 12))
        _dashed(d, False)
    elif name == "image":
        R(3, 4, 18, 16, 2.5)
        C(9, 9.5, 1.8)
        P((3, 17), (9, 12), (13, 15.5), (16, 13), (21, 17))
    elif name == "sprites":
        P((7, 3.5), (19, 3.5), (21, 5.5), (21, 15))
        R(3, 7, 15, 14, 2.5)
        C(8, 11.5, 1.6)
        P((3, 18.5), (8, 14.5), (11.5, 17), (14, 15), (18, 18.5))
    elif name == "anims":
        R(3, 5, 18, 14, 2.5)
        L((3, 9), (21, 9))
        L((8, 5), (6.5, 9))
        L((13, 5), (11.5, 9))
        L((18, 5), (16.5, 9))
        P((10, 12), (15, 14.5), (10, 17), close=True)
    elif name == "sounds":
        P((4, 9), (8, 9), (13, 5), (13, 19), (8, 15), (4, 15), close=True)
        d.arc(13, 12, 4, -50, 100)
        d.arc(13, 12, 7.5, -50, 100)
    elif name == "commands":
        R(2, 7, 20, 11, 5)
        L((6, 12.5), (10, 12.5))
        L((8, 10.5), (8, 14.5))
        C(15.5, 11.5, 0.6, fill=True)
        C(18, 13.5, 0.6, fill=True)
    elif name == "states":
        P((8, 4), (5, 4), (5, 10), (3, 12), (5, 14), (5, 20), (8, 20))
        P((16, 4), (19, 4), (19, 10), (21, 12), (19, 14), (19, 20), (16, 20))
        L((10, 12), (14, 12))
    elif name == "definitions":
        P((6, 3), (14, 3), (19, 8), (19, 21), (6, 21), close=True)
        L((14, 3), (14, 8), (19, 8))
        L((9, 12), (16, 12))
        L((9, 16), (14, 16))
    elif name == "options":
        pts = []
        for k in range(8):  # engrenagem de 8 dentes: alterna raio externo/interno
            base = k * 45
            for da, r in ((-12, 9.3), (12, 9.3), (17, 7.0), (28, 7.0)):
                a = math.radians(base + da)
                pts.append((12 + r * math.cos(a), 12 + r * math.sin(a)))
        P(*pts, close=True)
        C(12, 12, 3)
    elif name == "help":
        C(12, 12, 9)
        P((9.5, 9.5), (10, 8), (12, 7.3), (14, 8), (14.5, 9.8), (13.5, 11.2), (12, 12), (12, 13.6))
        C(12, 16.8, 0.5, fill=True)
    elif name in ("play", "run"):
        if name == "run":
            C(12, 12, 9)
            P((10, 8.5), (16, 12), (10, 15.5), close=True, fill=True)
        else:
            P((7, 5), (19, 12), (7, 19), close=True, fill=True)
    elif name == "stop":
        R(6, 6, 12, 12, 2, fill=d.color)
    elif name == "pause":
        R(6, 5, 4, 14, 1.2, fill=d.color)
        R(14, 5, 4, 14, 1.2, fill=d.color)
    elif name == "loop":
        P((17, 3), (20, 6), (17, 9))
        P((4, 11), (4, 9), (6, 6), (20, 6))
        P((7, 21), (4, 18), (7, 15))
        P((20, 13), (20, 15), (18, 18), (4, 18))
    elif name == "switch":
        P((16, 3), (20, 7), (16, 11))
        L((20, 7), (4, 7))
        P((8, 13), (4, 17), (8, 21))
        L((4, 17), (20, 17))
    elif name in ("box_attack", "box_collision"):
        col = QColor(_COLORS["red" if name == "box_attack" else "blue"])
        fill = QColor(col)
        fill.setAlpha(60)
        pen = d.p.pen()
        pen.setColor(col)
        d.p.setPen(pen)
        R(4, 5, 16, 14, 2.5, fill=fill)
    elif name == "default":
        P((4, 9), (4, 4), (9, 4))
        P((15, 4), (20, 4), (20, 9))
        P((20, 15), (20, 20), (15, 20))
        P((9, 20), (4, 20), (4, 15))
        R(9, 8, 6, 8, 1.5)
    elif name == "align":
        L((4, 20), (20, 20))
        R(6, 10, 4, 8, 1.2)
        R(14, 5, 4, 13, 1.2)
    elif name == "export":
        L((12, 15), (12, 3))
        P((7, 8), (12, 3), (17, 8))
        P((4, 15), (4, 20), (20, 20), (20, 15))
    elif name == "import":
        L((12, 3), (12, 15))
        P((7, 10), (12, 15), (17, 10))
        P((4, 15), (4, 20), (20, 20), (20, 15))
    elif name == "backup":
        d.arc(12, 12, 8.5, 110, 300)
        P((3.5, 6), (3.5, 10.5), (8, 10.5))
        P((12, 7.5), (12, 12), (15, 14))
    elif name == "backup_new":
        R(3, 4, 18, 5, 1.5)
        P((5, 9), (5, 20), (19, 20), (19, 9))
        L((12, 11.5), (12, 17.5))
        L((9, 14.5), (15, 14.5))
    elif name == "validate":
        C(12, 12, 9)
        P((8, 12.5), (11, 15.5), (16.5, 9))
    elif name == "palette":
        P((12, 3), (7, 4.5), (3.5, 9), (3.5, 14), (6.5, 19), (12, 21), (14, 19.5), (13.5, 17), (15, 15.5),
          (18, 15.5), (20.5, 13), (20.5, 9), (17, 4.5), close=True)
        for x, y in ((8, 10), (11.5, 7), (16, 8.5)):
            C(x, y, 1.1, fill=True)
    elif name == "palfx":
        P((12, 3), (13.5, 9), (19, 10.5), (13.5, 12), (12, 18), (10.5, 12), (5, 10.5), (10.5, 9), close=True)
        L((19, 16), (19, 21))
        L((16.5, 18.5), (21.5, 18.5))
    elif name == "throw":
        P((5, 19), (5, 13), (8, 9), (13, 7), (18, 7))
        P((15, 4), (18, 7), (15, 10))
        C(5, 19, 1, fill=True)
    elif name in ("var_list", "var_log"):
        for y in (6, 12, 18):
            L((9, y), (20, y))
            C(5, y, 0.9, fill=True)
        if name == "var_log":
            L((17, 13), (17, 21))
            L((13, 17), (21, 17))
    elif name == "picker":                  # conta-gotas
        L((10.5, 7.5), (16.5, 13.5))
        P((12.5, 9.5), (4.5, 17.5), (3.5, 20.5), (6.5, 19.5), (14.5, 11.5))
        L((14.5, 9.5), (17, 7))
        C(18.5, 5.5, 2.4, fill=True)
    elif name == "offset":
        L((12, 3), (12, 21))
        L((3, 12), (21, 12))
        _arrow_head(d, 12, 3, -90)
        _arrow_head(d, 12, 21, 90)
        _arrow_head(d, 3, 12, 180)
        _arrow_head(d, 21, 12, 0)
    elif name == "warning":
        P((12, 3.5), (21.5, 20), (2.5, 20), close=True)
        L((12, 9.5), (12, 14))
        C(12, 17, 0.9, fill=True)
    elif name == "error":
        C(12, 12, 9)
        L((9, 9), (15, 15))
        L((15, 9), (9, 15))
    elif name == "ok":
        C(12, 12, 9)
        P((8, 12.5), (11, 15.5), (16.5, 9.5))
    elif name == "terminal":
        R(3, 4, 18, 16, 2.5)
        P((7, 9), (10, 12), (7, 15))
        L((12, 15), (17, 15))
    elif name == "command":
        R(3, 5, 18, 14, 3)
        L((7, 10), (9, 10))
        L((11, 10), (13, 10))
        L((15, 10), (17, 10))
        L((8, 14.5), (16, 14.5))
    elif name == "sweep":
        L((17, 3), (11, 12))
        P((7, 12), (15, 12), (17, 21), (5, 21), close=True)
        L((9.5, 16), (9, 21))
        L((12.5, 16), (13, 21))
    elif name in ("panel_hide", "panel_show"):
        R(3, 4, 18, 16, 2.5)
        L((3, 14), (21, 14))
        if name == "panel_hide":
            P((9, 8), (12, 11), (15, 8))
        else:
            P((9, 11), (12, 8), (15, 11))
    elif name == "archive":                 # arquivo compactado: folha com zíper
        P((6, 3), (14, 3), (19, 8), (19, 21), (6, 21), close=True)
        L((14, 3), (14, 8), (19, 8))
        for y in (5, 9):
            L((10, y), (12, y))
            L((11, y + 2), (13, y + 2))
        R(9.5, 13, 4, 5, 1)
    elif name == "rotate":
        d.arc(12, 12, 7, 60, 280)
        _arrow_head(d, 15.5, 6, 20)
    elif name == "resize":
        R(3, 9, 12, 12, 1.5)
        L((11, 13), (20, 4))
        L((14, 4), (20, 4), (20, 10))
    elif name == "crop":
        L((7, 3), (7, 17), (21, 17))
        L((3, 7), (17, 7), (17, 21))
    elif name in ("select_all", "select_none"):
        for x, y in ((4, 4), (13, 4), (4, 13), (13, 13)):
            R(x, y, 7, 7, 1.5)
        if name == "select_all":
            L((5.5, 7.5), (7, 9), (9.5, 5.5))
            L((14.5, 16.5), (16, 18), (18.5, 14.5))
    elif name == "link":
        R(3, 9, 9, 6, 3)
        R(12, 9, 9, 6, 3)
        L((9, 12), (15, 12))
    elif name == "simulate":
        R(3, 4, 18, 13, 2)
        P((10, 7.5), (15, 10.5), (10, 13.5), close=True)
        L((7, 20.5), (17, 20.5))
    elif name == "chevron_down":
        P((6, 9), (12, 15), (18, 9))
    elif name == "chevron_right":
        P((9, 6), (15, 12), (9, 18))
    elif name == "search":
        C(10.5, 10.5, 6)
        L((15, 15), (20, 20))
    elif name == "folder_open":
        P((3, 19), (3, 6), (9, 6), (11, 9), (19, 9), (19, 11))
        P((3, 19), (6, 12), (22, 12), (19, 19), close=True)
    elif name == "diff":
        R(3, 3, 8, 18, 1.5)
        R(13, 3, 8, 18, 1.5)
        L((5, 8), (9, 8))
        L((15, 12), (19, 12))
        L((15, 16), (19, 16))
    elif not _draw_more(name, d):
        C(12, 12, 3, fill=True)


def _dashed(d: _Pen, on: bool = True) -> None:
    pen = d.p.pen()
    if on:
        pen.setDashPattern([1.2, 1.4])
    else:
        pen.setStyle(Qt.PenStyle.SolidLine)
    d.p.setPen(pen)


def _colored(d: _Pen, role: str) -> QColor:
    col = QColor(_COLORS[role])
    pen = d.p.pen()
    pen.setColor(col)
    d.p.setPen(pen)
    return col


def _box(d: _Pen, x, y, w, h, role: str = "blue") -> None:
    """Caixa de colisão pequena (vermelha = Clsn1, azul = Clsn2), cor fixa como no canvas."""
    col = _colored(d, role)
    fill = QColor(col)
    fill.setAlpha(60)
    d.rect(x, y, w, h, 1.5, fill=fill)
    pen = d.p.pen()
    pen.setColor(d.color)
    d.p.setPen(pen)


def _draw_more(name: str, d: _Pen) -> bool:
    """Ícones de função (um desenho por ação, para não repetir o mesmo em coisas diferentes)."""
    L, R, P, C = d.line, d.rect, d.poly, d.circle
    if name == "overflow":                  # "⋯": mais ferramentas
        for x in (6, 12, 18):
            C(x, 12, 1.6, fill=True)
    elif name == "copy_prev":               # caixas do frame anterior -> frame atual
        _dashed(d)
        R(2.5, 6, 7, 12, 1.5)
        _dashed(d, False)
        _box(d, 14.5, 6, 7, 12)
        L((10.5, 12), (13, 12))
        _arrow_head(d, 13, 12, 0, 2.6)
    elif name == "backup_load":             # caixa de arquivo com seta saindo (restaurar)
        R(3, 3.5, 18, 5, 1.5)
        P((5, 8.5), (5, 20.5), (19, 20.5), (19, 8.5))
        L((12, 18), (12, 11.5))
        _arrow_head(d, 12, 11.5, -90, 3)
    elif name == "backup_save":             # caixa de arquivo com seta entrando (guardar)
        R(3, 3.5, 18, 5, 1.5)
        P((5, 8.5), (5, 20.5), (19, 20.5), (19, 8.5))
        L((12, 11), (12, 17.5))
        _arrow_head(d, 12, 17.5, 90, 3)
    elif name == "pack":                    # empacotar para distribuir: pacote fechado
        P((12, 2.8), (20.5, 7.3), (20.5, 16.7), (12, 21.2), (3.5, 16.7), (3.5, 7.3), close=True)
        L((3.5, 7.3), (12, 11.8), (20.5, 7.3))
        L((12, 11.8), (12, 21.2))
        L((7.8, 5), (16.2, 9.5))
    elif name == "goto_line":
        L((4, 6), (12, 6))
        L((4, 12), (19, 12))
        _arrow_head(d, 19.5, 12, 0, 3.2)
        L((4, 18), (12, 18))
    elif name == "ruler":
        R(2.5, 7.5, 19, 9, 1.5)
        for k, x in enumerate((6, 9, 12, 15, 18)):
            L((x, 7.5), (x, 10.5 if k % 2 else 12))
    elif name == "axis":
        L((12, 21), (12, 4))
        _arrow_head(d, 12, 3.5, -90, 3)
        L((3, 12), (20, 12))
        _arrow_head(d, 20.5, 12, 0, 3)
        C(12, 12, 1.4, fill=True)
    elif name == "thumbs":
        for x in (3, 10, 17):
            for y in (5, 13):
                R(x, y, 5, 6, 1)
    elif name == "flip_h":                  # um triângulo e o reflexo dele do outro lado do eixo
        _dashed(d)
        L((12, 3), (12, 21))
        _dashed(d, False)
        P((9, 6), (9, 18), (3, 12), close=True)
        P((15, 6), (15, 18), (21, 12), close=True, fill=True)
    elif name == "flip_v":
        _dashed(d)
        L((3, 12), (21, 12))
        _dashed(d, False)
        P((6, 9), (18, 9), (12, 3), close=True)
        P((6, 15), (18, 15), (12, 21), close=True, fill=True)
    elif name == "rotate90":
        R(3.5, 10.5, 10, 10, 1.5)
        path = QPainterPath(QPointF(8, 6.5))
        path.cubicTo(QPointF(13, 3), QPointF(19, 5), QPointF(19.5, 12))
        d.p.drawPath(path)
        _arrow_head(d, 19.5, 12.5, 90, 3)
    elif name == "rotate_any":
        d.arc(12, 12, 8, 70, 290)
        _arrow_head(d, 14.8, 4.5, 15, 3)
        L((8, 16), (16, 16))
        L((8, 16), (14, 9.5))
    elif name == "renumber":                # renumerar: #
        L((10, 4), (8, 20))
        L((16, 4), (14, 20))
        L((4.5, 9), (19.5, 9))
        L((4.5, 15), (19.5, 15))
    elif name == "swap_colors":             # trocar cores: duas amostras e uma seta de ida e volta
        R(2.5, 3.5, 8, 8, 1.5, fill=QColor(d.color))
        R(13.5, 3.5, 8, 8, 1.5)
        L((5, 17.5), (19, 17.5))
        _arrow_head(d, 4.5, 17.5, 180, 3)
        _arrow_head(d, 19.5, 17.5, 0, 3)
    elif name == "invert_sel":
        R(4, 4, 16, 16, 2)
        P((4.5, 19.5), (19.5, 4.5), (19.5, 19.5), close=True, fill=True)
    elif name == "box_switch":
        _box(d, 3, 3, 11, 11, "red")
        _box(d, 10, 10, 11, 11, "blue")
    elif name == "reverse":
        P((11.5, 6), (3.5, 12), (11.5, 18), close=True)
        P((20.5, 6), (12.5, 12), (20.5, 18), close=True)
    elif name in ("fade_in", "fade_out"):
        if name == "fade_out":
            d.p.translate(24, 0)
            d.p.scale(-1, 1)
        P((3, 19), (21, 5), (21, 19), close=True)
        for x in (9, 14):
            L((x, 19), (x, 19 - (x - 3) * 14 / 18 + 1.5))
    elif name == "silence":
        L((3, 12), (21, 12))
        L((3, 7), (3, 17))
        L((21, 7), (21, 17))
        d.p.setOpacity(0.55)
        for x, h in ((7, 4), (11, 2.5), (15, 4), (18, 2)):
            L((x, 12 - h), (x, 12 + h))
        d.p.setOpacity(1)
    elif name == "remove_sel":
        L((6, 4), (3, 4), (3, 20), (6, 20))
        L((18, 4), (21, 4), (21, 20), (18, 20))
        L((8.5, 8.5), (15.5, 15.5))
        L((15.5, 8.5), (8.5, 15.5))
    elif name == "delete_group":
        P((9, 5), (9, 2.5), (21.5, 2.5), (21.5, 14), (19, 14))
        L((3, 8), (17, 8))
        P((7, 8), (7, 6), (13, 6), (13, 8))
        P((4.5, 8), (5.5, 21), (14.5, 21), (15.5, 8))
        L((8.5, 11.5), (8.5, 17.5))
        L((11.5, 11.5), (11.5, 17.5))
    elif name == "box_delete":
        _box(d, 3, 3, 13, 13, "blue")
        L((14, 14), (21, 21))
        L((21, 14), (14, 21))
    elif name == "box_clear":
        _box(d, 3, 4, 9, 9, "red")
        _box(d, 9, 9, 11, 11, "blue")
        L((3, 21), (21, 3))
    elif name == "box_clear_anim":          # apagar as caixas da animação: filme com um X
        R(3, 4, 18, 16, 2)
        L((3, 8), (21, 8))
        L((3, 16), (21, 16))
        L((10, 10), (14, 14))
        L((14, 10), (10, 14))
    elif name == "box_simple":              # Clsn2 da área do sprite: caixa dentro do contorno
        _dashed(d)
        R(3, 3, 18, 18, 2)
        _dashed(d, False)
        _box(d, 7, 7, 10, 10, "blue")
    elif name == "box_anatomic":            # cabeça, tronco e pernas: três caixas empilhadas
        _box(d, 9, 2.5, 6, 5, "blue")
        _box(d, 6, 9.5, 12, 6, "blue")
        _box(d, 7.5, 17.5, 9, 4, "blue")
    elif name == "box_auto_attack":         # Clsn1 automática: caixa de ataque e um brilho
        _box(d, 3, 8, 13, 13, "red")
        P((18, 1.5), (19.2, 4.8), (22.5, 6), (19.2, 7.2), (18, 10.5), (16.8, 7.2), (13.5, 6), (16.8, 4.8),
          close=True, fill=True)
    elif name == "box_interp":
        _box(d, 2.5, 7, 6, 10, "blue")
        _box(d, 15.5, 5, 6, 14, "blue")
        _dashed(d)
        R(9.5, 6.5, 5, 11, 1.2)
        _dashed(d, False)
    elif name == "box_all_frames":          # caixa em todos os frames: caixas em pilha
        for k in (0, 3.5, 7):
            _box(d, 3 + k, 3 + k, 11, 11)
    elif name == "box_instances":
        _box(d, 2.5, 4, 8, 10, "blue")
        R(13, 9, 9, 6, 3)
        L((10.5, 12), (13, 12))
        L((16, 12), (19, 12))
    elif name == "remap":
        C(5, 12, 2.6)
        C(19, 12, 2.6, fill=True)
        L((8, 12), (15.5, 12))
        _arrow_head(d, 16, 12, 0, 3)
    elif name == "palette_apply":           # tira de cores -> imagem
        for y in (4, 10, 16):
            R(2.5, y, 4, 4, 1, fill=d.color)
        L((8, 12), (10.5, 12))
        _arrow_head(d, 11, 12, 0, 2.4)
        R(12.5, 5, 9, 14, 1.5)
    elif name == "check":
        P((4.5, 12.5), (9.5, 17.5), (19.5, 6.5))
    elif name == "wizard":
        L((4, 20), (15, 9))
        L((13, 7), (17, 11))
        for (x1, y1), (x2, y2) in (((18, 2.5), (18, 5.5)), ((16.5, 4), (19.5, 4)),
                                   ((8, 3), (8, 6)), ((6.5, 4.5), (9.5, 4.5)),
                                   ((20, 11), (20, 14)), ((18.5, 12.5), (21.5, 12.5))):
            L((x1, y1), (x2, y2))
    elif name == "cmd_test":
        R(2.5, 6.5, 19, 12, 2)
        for x in (6, 10, 14, 18):
            C(x, 10.5, 0.9, fill=True)
        L((8, 15), (16, 15))
    elif name == "replace":
        C(8.5, 8.5, 4.5)
        L((11.8, 11.8), (14.5, 14.5))
        L((10, 19.5), (20, 19.5))
        _arrow_head(d, 20.5, 19.5, 0, 3)
        L((17, 5), (21, 5))
        L((17, 9), (21, 9))
    elif name == "find_files":
        P((5, 21), (3, 21), (3, 3), (12, 3), (16, 7), (16, 10))
        C(14, 15, 3.8)
        L((16.8, 17.8), (20.5, 21.5))
    elif name == "theme":
        C(12, 12, 8.5)
        path = QPainterPath(QPointF(12, 3.5))
        path.arcTo(QRectF(3.5, 3.5, 17, 17), 90, -180)
        path.closeSubpath()
        d.p.fillPath(path, d.color)
    elif name == "anim_new":
        R(2.5, 5, 13, 14, 2)
        P((7, 9), (11.5, 12), (7, 15), close=True)
        L((19, 8), (19, 16))
        L((15.5, 12), (22.5, 12))
    elif name == "select_group":
        _dashed(d)
        R(2.5, 2.5, 19, 19, 3)
        _dashed(d, False)
        R(6.5, 8, 5, 8, 1)
        R(13, 8, 5, 8, 1)
    elif name == "add_all":
        P((7, 5), (7, 3), (21, 3), (21, 15), (19, 15))
        R(3, 7, 14, 14, 1.5)
        L((10, 10.5), (10, 17.5))
        L((6.5, 14), (13.5, 14))
    elif name == "add_frame":
        R(2.5, 4, 14, 16, 2)
        for x in (5.5, 9.5, 13.5):
            L((x, 4), (x, 6.5))
            L((x, 17.5), (x, 20))
        L((20, 9), (20, 17))
        L((16, 13), (24, 13))
    elif name == "code":
        P((8, 7), (3, 12), (8, 17))
        P((16, 7), (21, 12), (16, 17))
        L((14, 4.5), (10, 19.5))
    elif name == "external":
        P((10, 4), (4, 4), (4, 20), (20, 20), (20, 14))
        L((11, 13), (20, 4))
        P((14, 4), (20, 4), (20, 10))
    elif name in ("step_back", "step_fwd"):
        if name == "step_fwd":
            d.p.translate(24, 0)
            d.p.scale(-1, 1)
        L((6, 5.5), (6, 18.5))
        P((19, 5.5), (9.5, 12), (19, 18.5), close=True)
    elif name == "zoom_sel":
        for (a, b, c) in (((3, 8), (3, 3), (8, 3)), ((16, 3), (21, 3), (21, 8)),
                          ((21, 16), (21, 21), (16, 21)), ((8, 21), (3, 21), (3, 16))):
            P(a, b, c)
        C(11, 11, 3.6)
        L((13.6, 13.6), (16.5, 16.5))
    elif name == "info":
        C(12, 12, 9)
        L((12, 11), (12, 17))
        C(12, 7.6, 0.6, fill=True)
    elif name == "sound_swap":
        P((3, 9.5), (6.5, 9.5), (10.5, 5.5), (10.5, 18.5), (6.5, 14.5), (3, 14.5), close=True)
        L((14, 9), (20.5, 9))
        _arrow_head(d, 21, 9, 0, 2.6)
        L((21, 15), (14.5, 15))
        _arrow_head(d, 14, 15, 180, 2.6)
    elif name == "image_swap":
        R(2.5, 4.5, 11, 10, 1.5)
        P((4.5, 12.5), (7.5, 9), (11.5, 12.5))
        L((14, 17), (20.5, 17))
        _arrow_head(d, 21, 17, 0, 2.6)
        L((21, 21), (14.5, 21))
        _arrow_head(d, 14, 21, 180, 2.6)
        P((16, 4.5), (21.5, 4.5), (21.5, 12))
    elif name == "images_add":
        P((7, 5), (7, 3), (21, 3), (21, 14), (19, 14))
        R(3, 7, 14, 11, 1.5)
        P((5, 16), (8.5, 12), (12.5, 16))
        L((19, 16.5), (19, 22.5))
        L((16, 19.5), (22, 19.5))
    elif name == "trim":
        _dashed(d)
        R(2.5, 2.5, 19, 19, 2)
        _dashed(d, False)
        R(8, 8, 8, 8, 1.5)
        L((4.5, 12), (6.5, 12))
        L((19.5, 12), (17.5, 12))
        L((12, 4.5), (12, 6.5))
        L((12, 19.5), (12, 17.5))
    elif name == "sheet_save":              # folhas (grade) baixando para o disco
        for x, y in ((3, 3), (10, 3), (3, 10), (10, 10)):
            R(x, y, 5, 5, 1)
        L((18, 10), (18, 18))
        _arrow_head(d, 18, 18.5, 90, 3)
        L((14, 21), (22, 21))
    elif name in ("show_attack", "show_collision"):   # mostrar Clsn1/Clsn2: olho na cor da caixa
        _colored(d, "red" if name == "show_attack" else "blue")
        path = QPainterPath(QPointF(2.5, 12))
        path.quadTo(QPointF(12, 2), QPointF(21.5, 12))
        path.quadTo(QPointF(12, 22), QPointF(2.5, 12))
        d.p.drawPath(path)
        C(12, 12, 3, fill=True)
    elif name == "optimize":
        R(2.5, 3, 6, 8, 1.2)
        R(2.5, 13, 6, 8, 1.2)
        P((9.5, 7), (12, 7), (14, 12), (12, 17), (9.5, 17))
        L((14, 12), (16, 12))
        R(16.5, 7, 6, 10, 1.2)
    elif name == "paint":                   # editar imagem no app: pincel
        L((20.5, 3.5), (13.5, 10.5))
        P((11.5, 8.5), (15.5, 12.5), (13.5, 14.5), (9.5, 10.5), close=True)
        path = QPainterPath(QPointF(9.5, 10.5))
        path.cubicTo(QPointF(6, 11), QPointF(5, 14), QPointF(4.5, 17))
        path.cubicTo(QPointF(4.2, 19), QPointF(3.6, 20), QPointF(3, 20.5))
        path.cubicTo(QPointF(7.5, 21), QPointF(12.5, 19.5), QPointF(13.5, 14.5))
        d.p.drawPath(path)
    elif name == "bucket":                  # balde de tinta (preencher área)
        P((18.5, 11), (10.5, 3), (2.8, 10.7), (2.8, 13.3), (8.2, 18.7), (10.8, 18.7), close=True)
        L((5.5, 2), (10, 6.5))
        L((3, 12), (17.5, 12))
        path = QPainterPath(QPointF(20.5, 15))
        path.cubicTo(QPointF(21.5, 17), QPointF(22.5, 18.2), QPointF(22.5, 19.5))
        path.cubicTo(QPointF(22.5, 21.5), QPointF(18.5, 21.5), QPointF(18.5, 19.5))
        path.cubicTo(QPointF(18.5, 18.2), QPointF(19.5, 17), QPointF(20.5, 15))
        d.p.fillPath(path, d.color)
    elif name == "line_tool":               # traçar reta
        L((6, 18), (18, 6))
        C(5, 19, 2)
        C(19, 5, 2)
    elif name == "rect_tool":               # traçar retângulo
        R(4, 6, 16, 12, 1.5)
    elif name == "movelist":                # lista de golpes: uma entrada (seta) por linha
        for y in (6, 12, 18):
            L((3, y), (7, y))
            _arrow_head(d, 7.5, y, 0, 2.2)
            L((11, y), (21, y))
    elif name == "grp_const":               # controles deslizantes (constantes do char)
        for y, k in ((6, 15), (12, 8), (18, 13)):
            L((3.5, y), (k - 2.6, y))
            C(k, y, 2.4)
            L((k + 2.6, y), (20.5, y))
    elif name == "grp_always":              # infinito (roda todo tick)
        path = QPainterPath(QPointF(12, 12))
        path.cubicTo(QPointF(9.5, 7.5), QPointF(3, 7.5), QPointF(3, 12))
        path.cubicTo(QPointF(3, 16.5), QPointF(9.5, 16.5), QPointF(12, 12))
        path.cubicTo(QPointF(14.5, 7.5), QPointF(21, 7.5), QPointF(21, 12))
        path.cubicTo(QPointF(21, 16.5), QPointF(14.5, 16.5), QPointF(12, 12))
        d.p.drawPath(path)
    elif name == "grp_basic":               # boneco parado (postura básica)
        C(12, 5, 2.5)
        L((12, 8), (12, 15))
        L((7.5, 11), (16.5, 11))
        L((12, 15), (8.5, 21))
        L((12, 15), (15.5, 21))
    elif name == "grp_normal":              # golpe normal: duas espadas cruzadas
        for sx in (1, -1):
            d.p.save()
            if sx < 0:
                d.p.translate(24, 0)
                d.p.scale(-1, 1)
            L((4, 4), (15.5, 15.5))             # lâmina
            L((12.5, 18.5), (18.5, 12.5))       # guarda
            L((17, 17), (20.5, 20.5))           # cabo
            d.p.restore()
    elif name == "grp_special":             # chama (técnica especial)
        path = QPainterPath(QPointF(12, 21.5))
        path.cubicTo(QPointF(7, 21.5), QPointF(4.8, 17.5), QPointF(6.5, 13.5))
        path.cubicTo(QPointF(7.4, 11.4), QPointF(9.2, 10), QPointF(9, 7))
        path.cubicTo(QPointF(11.3, 8.4), QPointF(12.5, 10.3), QPointF(12.4, 12.2))
        path.cubicTo(QPointF(13.8, 10.4), QPointF(14.2, 7.2), QPointF(13.4, 2.5))
        path.cubicTo(QPointF(17.2, 5.6), QPointF(19.3, 10), QPointF(19.2, 14.2))
        path.cubicTo(QPointF(19.1, 18.6), QPointF(16, 21.5), QPointF(12, 21.5))
        d.p.drawPath(path)
        inner = QPainterPath(QPointF(12, 21.5))
        inner.cubicTo(QPointF(9.6, 21.5), QPointF(9, 18.8), QPointF(10.6, 16.8))
        inner.cubicTo(QPointF(11.4, 15.8), QPointF(12, 15), QPointF(12.2, 13.8))
        inner.cubicTo(QPointF(14.3, 15.4), QPointF(15, 17.6), QPointF(14.6, 19.2))
        d.p.drawPath(inner)
    elif name == "grp_super":               # raio (super, gasta energia)
        P((13.5, 2.5), (5.5, 13.5), (11.5, 13.5), (10.5, 21.5), (18.5, 10.2), (12.5, 10.2), close=True)
    elif name == "grp_hit":                 # estrela de impacto (reação a golpe)
        pts = []
        for k in range(16):
            ang = math.radians(-90 + k * 22.5)
            r = 9.5 if k % 2 == 0 else (4.6 if k % 4 == 1 else 5.6)
            pts.append((12 + r * math.cos(ang), 12 + r * math.sin(ang)))
        P(*pts, close=True)
    elif name == "grp_other":               # camadas (helpers e estados próprios)
        P((12, 3.5), (21, 8), (12, 12.5), (3, 8), close=True)
        P((3, 12), (12, 16.5), (21, 12))
        P((3, 16), (12, 20.5), (21, 16))
    elif name == "stage":                   # cenário de luta: montanhas atrás e chão em perspectiva
        P((2.5, 12.5), (7.5, 6), (11, 9.5), (15, 4), (21.5, 12.5))
        P((2.5, 20.5), (6.5, 13.5), (17.5, 13.5), (21.5, 20.5), close=True)
        L((12, 13.5), (12, 20.5))
    elif name == "character":               # personagem: busto
        C(12, 8, 4)
        path = QPainterPath(QPointF(4.5, 21))
        path.cubicTo(QPointF(4.5, 15.5), QPointF(8, 13.5), QPointF(12, 13.5))
        path.cubicTo(QPointF(16, 13.5), QPointF(19.5, 15.5), QPointF(19.5, 21))
        d.p.drawPath(path)
    elif name == "project":                 # explorador do projeto: árvore de arquivos
        R(3, 3, 7, 5, 1.2)
        L((6.5, 8), (6.5, 18.5), (12, 18.5))
        L((6.5, 12.5), (12, 12.5))
        R(12, 10, 9, 5, 1.2)
        R(12, 16, 9, 5, 1.2)
    elif name == "tools":                   # ferramentas: chave inglesa
        path = QPainterPath(QPointF(14.5, 3.5))
        path.cubicTo(QPointF(11.5, 3.5), QPointF(9.5, 6.5), QPointF(10.7, 9.6))
        path.lineTo(QPointF(3.8, 16.5))
        path.cubicTo(QPointF(2.8, 17.5), QPointF(2.8, 19.2), QPointF(3.8, 20.2))
        path.cubicTo(QPointF(4.8, 21.2), QPointF(6.5, 21.2), QPointF(7.5, 20.2))
        path.lineTo(QPointF(14.4, 13.3))
        path.cubicTo(QPointF(17.5, 14.5), QPointF(20.5, 12.5), QPointF(20.5, 9.5))
        path.lineTo(QPointF(17.5, 12))
        path.lineTo(QPointF(14.5, 11.5))
        path.lineTo(QPointF(12.5, 9.5))
        path.lineTo(QPointF(12, 6.5))
        path.closeSubpath()
        d.p.drawPath(path)
    elif name == "snippet":                 # trecho pronto: chaves com "+"
        P((8, 4), (6, 4), (5, 5.5), (5, 10.5), (3, 12), (5, 13.5), (5, 18.5), (6, 20), (8, 20))
        P((16, 4), (18, 4), (19, 5.5), (19, 10.5), (21, 12), (19, 13.5), (19, 18.5), (18, 20), (16, 20))
        L((12, 8.5), (12, 15.5))
        L((8.5, 12), (15.5, 12))
    elif name == "transplant":              # transplantar golpe: de um char para outro
        R(2.5, 11, 7, 8, 1.5)
        R(14.5, 11, 7, 8, 1.5, fill=d.color)
        path = QPainterPath(QPointF(6, 9))
        path.cubicTo(QPointF(8, 3), QPointF(16, 3), QPointF(18, 8.5))
        d.p.drawPath(path)
        _arrow_head(d, 18, 9, 75, 3)
    elif name == "palettes12":              # as 12 paletas: 12 amostras
        for k in range(12):
            C(5 + (k % 4) * 4.67, 7 + (k // 4) * 5, 1.5, fill=True)
    elif name == "eraser":                  # limpar cores: borracha
        P((3.5, 15), (12.5, 6), (20, 13.5), (13, 20.5), (8.5, 20.5), close=True)
        L((8, 10.5), (15.5, 18))
        L((13, 20.5), (21, 20.5))
    elif name == "expand_all":              # setas se afastando de uma linha (abrir tudo)
        P((7, 8), (12, 3.5), (17, 8))
        P((7, 16), (12, 20.5), (17, 16))
        L((5, 12), (19, 12))
    elif name == "collapse_all":            # setas se juntando numa linha (recolher tudo)
        P((7, 3.5), (12, 8), (17, 3.5))
        P((7, 20.5), (12, 16), (17, 20.5))
        L((5, 12), (19, 12))
    elif name == "select_rect":             # seleção retangular: retângulo tracejado
        _dashed(d)
        R(4, 5, 16, 14, 1)
        _dashed(d, False)
    elif name == "wand":                    # varinha da seleção: haste encostada numa seleção tracejada
        _dashed(d)
        R(11, 3, 10, 10, 1.5)
        _dashed(d, False)
        L((3.5, 20.5), (11, 13))
    elif name == "move_tool":               # mover pixels: seta de ponteiro
        P((6, 3), (6, 19), (10.2, 15), (13.2, 21.2), (15.8, 20), (12.8, 13.8), (18.5, 13.8), close=True)
    elif name == "ellipse_tool":            # traçar elipse
        d.p.drawEllipse(QRectF(3.5, 6, 17, 12))
    elif name == "outline":                 # contorno: figura cheia com um anel em volta
        C(12, 12, 4.5, fill=True)
        C(12, 12, 8.5)
    elif name == "onion":                   # onion skin: quadro tracejado atrás do atual
        _dashed(d)
        R(3, 3, 12, 14, 2)
        _dashed(d, False)
        R(9, 7, 12, 14, 2)
    else:
        return False
    return True


class LineIconEngine(QIconEngine):
    def __init__(self, name: str, role: str | None = None, toggles: bool = True):
        super().__init__()
        self.name = name
        self.role = role  # cor fixa ("red", "green"…); None = cor do tema
        # toggles=False: o estado 'On' não pinta de destaque (numa árvore, 'On' é só 'aberto')
        self.toggles = toggles

    def paint(self, painter: QPainter, rect, mode, state):
        if self.role:
            color = QColor(_COLORS[self.role])
        elif mode == QIcon.Mode.Disabled:
            color = QColor(_COLORS["disabled"])
        elif (self.toggles and state == QIcon.State.On) or mode in (QIcon.Mode.Active, QIcon.Mode.Selected):
            color = QColor(_COLORS["active"])
        else:
            color = QColor(_COLORS["normal"])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(rect.width(), rect.height())
        painter.translate(rect.x() + (rect.width() - side) / 2, rect.y() + (rect.height() - side) / 2)
        painter.scale(side / 24, side / 24)
        _draw(self.name, _Pen(painter, color, 1.8 if side >= 20 else 2.1))
        painter.restore()

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        pm = QPixmap(size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRectF(0, 0, size.width(), size.height()).toRect(), mode, state)
        p.end()
        return pm

    def scaledPixmap(self, size: QSize, mode, state, scale: float) -> QPixmap:  # noqa: N802
        """Pixmap na resolução real da tela (sem isso, 150% de escala borrava o traço)."""
        pm = QPixmap(QSize(round(size.width() * scale), round(size.height() * scale)))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRectF(0, 0, pm.width(), pm.height()).toRect(), mode, state)
        p.end()
        pm.setDevicePixelRatio(scale)
        return pm

    def clone(self):
        return LineIconEngine(self.name, self.role, self.toggles)


def icon(name: str, role: str | None = None, toggles: bool = True) -> QIcon:
    return QIcon(LineIconEngine(name, role, toggles))


class _VectorEngine(QIconEngine):
    """Base dos ícones pintados na hora: nítidos em qualquer escala de tela (150%, 200%…).
    Um QPixmap fixo de 16 px ficava borrado quando o Windows ampliava."""

    def draw(self, p: QPainter) -> None:     # grade 24×24
        raise NotImplementedError

    def paint(self, painter: QPainter, rect, mode, state):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        side = min(rect.width(), rect.height())
        painter.translate(rect.x() + (rect.width() - side) / 2, rect.y() + (rect.height() - side) / 2)
        painter.scale(side / 24, side / 24)
        self.draw(painter)
        painter.restore()

    def pixmap(self, size: QSize, mode, state) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(self, size: QSize, mode, state, scale: float) -> QPixmap:  # noqa: N802
        pm = QPixmap(QSize(round(size.width() * scale), round(size.height() * scale)))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRectF(0, 0, pm.width(), pm.height()).toRect(), mode, state)
        p.end()
        pm.setDevicePixelRatio(scale)
        return pm


class BadgeIconEngine(_VectorEngine):
    """Letra num quadrado arredondado colorido (S, C, A, L do type do Statedef)."""

    def __init__(self, color: str, letter: str):
        super().__init__()
        self.color, self.letter = color, letter

    def draw(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawRoundedRect(QRectF(2, 2, 20, 20), 5.5, 5.5)
        f = QFont("Segoe UI")
        f.setPixelSize(14)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#15161a"))
        p.drawText(QRectF(2, 2.5, 20, 20), Qt.AlignmentFlag.AlignCenter, self.letter)

    def clone(self):
        return BadgeIconEngine(self.color, self.letter)


class DotIconEngine(_VectorEngine):
    """Ponto colorido (categoria do controller)."""

    def __init__(self, color: str, radius: float = 5.0):
        super().__init__()
        self.color, self.radius = color, radius

    def draw(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(QPointF(12, 12), self.radius, self.radius)

    def clone(self):
        return DotIconEngine(self.color, self.radius)


def badge_icon(color: str, letter: str) -> QIcon:
    return QIcon(BadgeIconEngine(color, letter))


def dot_icon(color: str, radius: float = 5.0) -> QIcon:
    return QIcon(DotIconEngine(color, radius))
