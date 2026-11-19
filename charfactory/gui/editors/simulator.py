"""Simulador tick a tick, sem abrir o MUGEN.

A simulação é a de `tools.simulate`: roda um estado com helpers, explods, pausas e um P2
parado, defendendo, agachado ou no ar, sem abrir o MUGEN. Cada tick é desenhado como na folha
de revisão; o play toca os sons do golpe no tempo certo e a barra de frame data mostra preparo,
ativo, recuperação e a vantagem.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time
import traceback

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QSlider,
                               QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..canvas import pil_to_qimage
from ..context import AppContext
from ..icons import icon
from ..widgets import MORE, DocStrip, Section, SidePanel, compact, form_row, tool_bar, tool_button
from ...i18n import tr


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    """Roda uma função fora da thread da interface e devolve o resultado por sinal."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.finished = False
        self.setAutoDelete(False)   # a lista do dono decide quando o objeto pode sumir

    def run(self) -> None:
        try:
            try:
                r = self.fn()
            except Exception as e:  # noqa: BLE001
                self.signals.failed.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
            else:
                self.signals.done.emit(r)
        except RuntimeError:
            pass        # a janela fechou com o trabalho no meio: não há mais quem receba
        finally:
            self.finished = True


def start(job: Job, owner: QObject, priority: int = 0) -> None:
    # mantém vivo até terminar (só some da lista depois de rodar). O resultado deve ir para
    # métodos de um QObject: assim chega pela fila da interface; uma lambda solta rodaria na
    # thread do trabalho. Prioridade maior passa na frente na fila (desenhar o tick que a pessoa
    # está vendo vem antes de auditar os efeitos)
    owner._jobs = [j for j in getattr(owner, "_jobs", []) if not j.finished] + [job]
    _pool().start(job, priority)


_POOL: QThreadPool | None = None


def _pool() -> QThreadPool:
    # uma thread só: numpy/PIL seguram o GIL em cada operação grande, e dois trabalhos ao mesmo
    # tempo deixavam a interface parada por até 0,8 s; em fila, cada um anda e a tela responde
    global _POOL
    if _POOL is None:
        _POOL = QThreadPool()
        _POOL.setMaxThreadCount(1)
    return _POOL


# ======================================================================= simulador

# fases do frame data com as cores de costume dos jogos de luta
PHASES = {"preparo": (tr("Startup"), "#3fb950"), "ativo": (tr("Active"), "#f85149"),
          "entre": (tr("Between hits"), "#a5413c"), "recuperacao": (tr("Recovery"), "#388bfd"),
          "pausa": (tr("Pause"), "#8b949e"), "livre": (tr("Free"), None)}

P2_MODES = [(tr("Standing still"), "stand", False), (tr("Standing guard"), "stand", True),
            (tr("Crouching"), "crouch", False), (tr("Crouching guard"), "crouch", True),
            (tr("In the air"), "air", False)]

# camadas da prévia: chave (a de simulate.render_tick), nome e dica
LAYERS = [("sprites", tr("Sprites"), tr("Turn off to see only the boxes")),
          ("p1", "P1", tr("The character")),
          ("p2", "P2", tr("The opponent and its boxes")),
          ("helpers", tr("Helpers"), tr("Helpers and their boxes")),
          ("projectiles", tr("Projectiles"), tr("Projectiles and their boxes")),
          ("explods", "Explods", tr("Effects on the stage")),
          ("screen", tr("Screen effects"), tr("Cut-ins and other effects attached to the screen")),
          ("clsn1", tr("Attack boxes"), tr("Clsn1, in red")),
          ("clsn2", tr("Hurt boxes"), tr("Clsn2, in blue")),
          ("ground", tr("Ground"), tr("Ground line and the mark under P1"))]

_NO_GUIDES = {"clsn1", "clsn2", "ground"}
VIEW_PRESETS = [("all", tr("Everything"), set()),
                ("clean", tr("Characters only"), {"helpers", "projectiles", "explods", "screen"} | _NO_GUIDES),
                ("p1", tr("P1 only"), {"p2", "helpers", "projectiles", "explods", "screen"} | _NO_GUIDES),
                ("effects", tr("Effects only"), {"p1", "p2"} | _NO_GUIDES),
                ("boxes", tr("Boxes only"), {"sprites"})]

BACKGROUNDS = [("default", tr("Default"), None), ("black", tr("Black"), (0, 0, 0)),
               ("gray", tr("Gray"), (96, 96, 96)), ("white", tr("White"), (245, 245, 245)),
               ("green", tr("Green screen"), (0, 177, 64))]

SPEEDS = [("1/8x", 0.125), ("1/4x", 0.25), ("1/2x", 0.5), ("1x", 1.0), ("2x", 2.0)]


# cor de cada tipo de marcador da linha do tempo
MARK_COLORS = {"acerto": "#f85149", "defesa": "#388bfd", "som": "#d29922", "explod": "#a371f7",
               "helper": "#db61a2", "projectile": "#db61a2",
               # faixa P2: tremor, recuo, defesa, no ar, no chão e a volta ao parado
               "p2_shake": "#e3b341", "p2_stun": "#f0883e", "p2_guard": "#388bfd",
               "p2_air": "#bc8cff", "p2_down": "#6e7681", "p2_state": "#db61a2", "p2_free": "#3fb950"}


class MoveTimeline(QWidget):
    """Linha do tempo do golpe: uma faixa por tipo de evento (quadros, frame data, acertos,
    sons, efeitos, tela) sobre os ticks da simulação.

    Clicar leva ao tick; duplo clique num evento abre o controlador no código; arrastar um
    evento que dispara por `Time = N`, `!Time` ou `AnimElem` pede para mudar o gatilho (sinal
    `moved`). Roda do mouse anda
    na linha, Ctrl+roda dá zoom."""
    seek = Signal(int)
    open_item = Signal(object)
    moved = Signal(object, int)

    LABEL_W, RULER_H, ROW_H = 92, 16, 18

    def __init__(self):
        super().__init__()
        from ...tools.movetimeline import ROWS
        self.all_rows = ROWS
        self.rows_def = ROWS
        self.loop: tuple[int, int] | None = None     # trecho repetido pelo play
        self.data: dict | None = None
        self.current = 0
        self.scale = 8.0              # pixels por tick
        self.offset = 0.0             # primeiro tick visível
        self.can_drag = lambda item: False
        self._drag: dict | None = None
        self._hover = None
        self.setMouseTracking(True)
        self.setFixedHeight(self.RULER_H + self.ROW_H * len(ROWS) + 4)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def set_hidden_rows(self, hidden) -> None:
        """Esconde faixas da linha do tempo (a altura acompanha)."""
        self.rows_def = [r for r in self.all_rows if r[0] not in hidden]
        self.setFixedHeight(self.RULER_H + self.ROW_H * len(self.rows_def) + 4)
        self.update()

    def set_loop(self, loop: tuple[int, int] | None) -> None:
        self.loop = loop
        self.update()

    @property
    def phases(self) -> list[str]:
        return self.data["phases"] if self.data else []

    def set_data(self, data: dict | None) -> None:
        self.data = data
        self._drag = None
        self.current = 0
        self.offset = 0.0
        if data:                      # cabe o trecho que interessa (até o P1 ficar livre)
            self.scale = max(2.0, min(24.0, self._area_w() / max(1, data["end"])))
        self.update()

    def set_current(self, i: int) -> None:
        if i == self.current:
            return
        self.current = i
        x = self._x(i)                # o tick atual sempre à vista
        if x < self.LABEL_W or x > self.width() - 12:
            self.offset = max(0.0, i - self._area_w() / self.scale * 0.2)
        self.update()

    # ---- geometria
    def _area_w(self) -> int:
        return max(40, self.width() - self.LABEL_W - 8)

    def _x(self, i: float) -> float:
        return self.LABEL_W + (i - self.offset) * self.scale

    def _tick_at(self, x: float) -> int:
        n = self.data["ticks"] if self.data else 0
        return max(0, min(n - 1, int((x - self.LABEL_W) / self.scale + self.offset))) if n else 0

    def _row_at(self, y: float) -> int:
        return int((y - self.RULER_H) // self.ROW_H)

    def _item_at(self, pos):
        if not self.data:
            return None
        r = self._row_at(pos.y())
        if not 0 <= r < len(self.rows_def) or pos.x() < self.LABEL_W:
            return None
        key = self.rows_def[r][0]
        best = None
        for it in self.data["rows"].get(key, []):
            x0, x1 = self._x(it["i0"]), max(self._x(it["i1"]), self._x(it["i0"]) + 7)
            if x0 - 2 <= pos.x() <= x1 + 2 and it.get("kind") != "pausa":
                best = it             # o último desenhado fica por cima
        return best

    # ---- mouse
    def mousePressEvent(self, e):  # noqa: N802
        if not self.data or e.button() != Qt.MouseButton.LeftButton:
            return
        it = self._item_at(e.position())
        if it is not None and self.can_drag(it):
            self._drag = {"item": it, "x": e.position().x(), "to": it["i0"]}
            self.setCursor(Qt.CursorShape.ClosedHandCursor)    # pegou o evento: mão fechada
        self.seek.emit(it["i0"] if it is not None else self._tick_at(e.position().x()))

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position()
        if self._drag is not None:
            d = self._drag
            d["to"] = max(0, min(self.data["ticks"] - 1,
                                 d["item"]["i0"] + round((pos.x() - d["x"]) / self.scale)))
            self.update()
            return
        if e.buttons() & Qt.MouseButton.LeftButton and self.data:
            return self.seek.emit(self._tick_at(pos.x()))
        it = self._item_at(pos)
        if it is not self._hover:
            self._hover = it
            from PySide6.QtWidgets import QToolTip
            tip = it["tip"] if it else ""
            if it is not None and self.can_drag(it):
                tip += tr("\nDrag to another tick (rewrites the Time or AnimElem trigger)")
            elif it is not None and it.get("ci") is not None and not it.get("hitsound") and \
                    it.get("kind") not in ("quadro", "pausa", "acerto", "defesa"):
                tip += tr("\nTo drag it, its trigger has to be just Time = N, !Time or AnimElem = N")
            if it is not None and it.get("ci") is not None:
                tip += tr("\nDouble-click opens it in the code")
            if tip:
                QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            else:
                QToolTip.hideText()
            # mão aberta: o evento se move inteiro (as setas de redimensionar sugeriam esticar)
            self.setCursor(Qt.CursorShape.OpenHandCursor if it is not None and self.can_drag(it)
                           else Qt.CursorShape.PointingHandCursor if it is not None else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        d, self._drag = self._drag, None
        if d is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.update()
            if d["to"] != d["item"]["i0"]:
                self.moved.emit(d["item"], d["to"])

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        it = self._item_at(e.position())
        if it is not None:
            self.open_item.emit(it)

    def wheelEvent(self, e):  # noqa: N802
        if not self.data:
            return
        steps = e.angleDelta().y() / 120 or e.angleDelta().x() / 120
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            at = self._tick_at(e.position().x())
            self.scale = max(1.0, min(40.0, self.scale * (1.25 ** steps)))
            self.offset = max(0.0, at - (e.position().x() - self.LABEL_W) / self.scale)
        else:
            self.offset = max(0.0, min(self.data["ticks"] - 4, self.offset - steps * 40 / self.scale))
        self.update()
        e.accept()

    # ---- desenho
    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        pal = self.palette()
        text, dim = pal.text().color(), QColor(pal.text().color())
        dim.setAlpha(140)
        band = QColor(pal.mid().color())
        band.setAlpha(45)
        f = p.font()
        f.setPointSizeF(max(6.5, f.pointSizeF() - 1.5))
        p.setFont(f)
        fm = p.fontMetrics()
        W, L = self.width(), self.LABEL_W
        for r, (_, name) in enumerate(self.rows_def):
            y = self.RULER_H + r * self.ROW_H
            if r % 2 == 0:
                p.fillRect(0, y, W, self.ROW_H, band)
            p.setPen(dim)
            p.drawText(6, y, L - 10, self.ROW_H, Qt.AlignmentFlag.AlignVCenter, name)
        if not self.data:
            return p.end()
        p.setClipRect(L, 0, W - L, self.height())
        n = self.data["ticks"]
        # régua: um traço a cada 10 ticks (ou 50 com pouco zoom)
        step = 10 if self.scale >= 3 else 50
        p.setPen(dim)
        for t in range(int(self.offset // step * step), n + 1, step):
            x = self._x(t)
            if x > W:
                break
            p.drawLine(int(x), self.RULER_H - 5, int(x), self.RULER_H - 1)
            p.drawText(int(x) + 3, 0, 40, self.RULER_H - 2, Qt.AlignmentFlag.AlignVCenter, str(t))
        rows = {k: r for r, (k, _) in enumerate(self.rows_def)}
        if self.loop:                 # trecho que o play repete
            shade = QColor(pal.highlight().color())
            shade.setAlpha(45)
            x0, x1 = self._x(self.loop[0]), self._x(self.loop[1] + 1)
            p.fillRect(int(x0), 0, max(2, int(x1 - x0)), self.height(), shade)
        # frame data
        y = self.RULER_H + rows.get("fases", 0) * self.ROW_H + 3
        gap = 1 if self.scale >= 4 else 0
        cols = {k: QColor(c) for k, (_, c) in PHASES.items() if c}
        for i, ph in enumerate(self.data["phases"] if "fases" in rows else ()):
            c = cols.get(ph)
            if c is not None:
                x0, x1 = self._x(i), self._x(i + 1)
                if x1 >= L and x0 <= W:
                    p.fillRect(int(x0), y, max(1, int(x1) - int(x0) - gap), self.ROW_H - 6, c)
        # quadros, acertos, sons, efeitos, tela
        frame_cols = (QColor(pal.highlight().color()), QColor(pal.highlight().color()))
        frame_cols[0].setAlpha(110)
        frame_cols[1].setAlpha(60)
        for key, items in self.data["rows"].items():
            if key not in rows:
                continue
            y = self.RULER_H + rows[key] * self.ROW_H + 2
            h = self.ROW_H - 4
            for k, it in enumerate(items):
                dragged = self._drag is not None and self._drag["item"] is it
                i0 = self._drag["to"] if dragged else it["i0"]
                x0 = self._x(i0)
                x1 = max(self._x(i0 + it["i1"] - it["i0"]), x0 + 7)
                if x1 < L or x0 > W:
                    continue
                kind = it.get("kind")
                if kind == "quadro":
                    c = frame_cols[k % 2]
                elif kind == "pausa":
                    c = QColor(PHASES["pausa"][1])
                    c.setAlpha(90)
                else:
                    c = QColor(MARK_COLORS.get(kind, "#3fb9a0"))
                p.fillRect(int(x0), y, max(2, int(x1 - x0) - (1 if kind == "quadro" else 0)), h, c)
                label = it.get("text", "")
                w = fm.horizontalAdvance(label) + 4
                # o rótulo só quando cabe até o próximo item da faixa
                room = (self._x(items[k + 1]["i0"]) if k + 1 < len(items) else W) - x0
                if kind == "quadro":
                    room = min(room, x1 - x0)
                if label and kind != "pausa" and w <= room:
                    p.setPen(text)
                    p.drawText(int(x0) + 2, y, w + 2, h, Qt.AlignmentFlag.AlignVCenter, label)
                if dragged:
                    p.setPen(text)
                    p.drawRect(int(x0), y, max(2, int(x1 - x0)), h)
        # tick atual
        x = self._x(self.current + 0.5)
        p.fillRect(int(x) - 1, 0, 2, self.height(), text)
        p.end()


class SoundPlayer:
    """Toca a trilha do golpe (os sons mixados em um WAV) a partir de um tick. O winsound toca
    um arquivo por vez; dois arquivos alternados evitam regravar o que ainda está tocando. Fora
    do Windows quem toca é o QtMultimedia."""

    def __init__(self):
        self._n = 0
        self.playing = False
        self._player = None

    @staticmethod
    def available() -> bool:
        if sys.platform == "win32":
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer  # noqa: F401
        except ImportError:
            return False
        return True

    def _qt_player(self):
        if self._player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self._player = QMediaPlayer()
            self._player.setAudioOutput(QAudioOutput(self._player))
        return self._player

    def play(self, sim, start_tick: int) -> None:
        self.stop()
        try:
            from ...tools import simulate
            wav = simulate.sound_track(sim, start_tick)
            if not wav:
                return
            self._n ^= 1
            path = os.path.join(tempfile.gettempdir(), f"charfactory_sim_{os.getpid()}_{self._n}.wav")
            with open(path, "wb") as fh:
                fh.write(wav)
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            else:
                player = self._qt_player()
                player.setSource(QUrl.fromLocalFile(path))
                player.play()
            self.playing = True
        except (ImportError, OSError, RuntimeError):
            pass

    def stop(self) -> None:
        if not self.playing:
            return
        self.playing = False
        try:
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(None, 0)
            elif self._player is not None:
                self._player.stop()
        except (ImportError, RuntimeError):
            pass


class ElidedRichLabel(QLabel):
    """Uma linha de texto rico que nunca pede largura à página: o que não couber vira "…" e o
    texto inteiro fica na dica. `text()` devolve sempre o texto completo."""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full = text
        self._fit()

    def text(self) -> str:
        return self._full

    def shown(self) -> str:
        return super().text()

    def setText(self, text: str) -> None:  # noqa: N802 — mesmo nome do QLabel
        self._full = text
        self._fit()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def _cut(self, doc: QTextDocument, chars: int) -> QTextDocument:
        out = doc.clone()
        out.setDocumentMargin(0)
        cursor = QTextCursor(out)
        cursor.setPosition(chars)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText("…")
        return out

    def _fit(self) -> None:
        self.ensurePolished()
        room = self.contentsRect().width()
        doc = QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setDocumentMargin(0)
        doc.setHtml(self._full)
        if room <= 0 or doc.idealWidth() <= room:
            self.setToolTip("")
            super().setText(self._full)
            return
        low, high = 0, doc.characterCount() - 1
        while low < high:                   # o maior corte que ainda cabe na largura
            mid = (low + high + 1) // 2
            if self._cut(doc, mid).idealWidth() <= room:
                low = mid
            else:
                high = mid - 1
        self.setToolTip(self._full)
        super().setText(self._cut(doc, low).toHtml())


class TickView(QLabel):
    """Imagem do tick. 100% cabe na área; a roda aproxima no ponto do mouse, arrastar move a
    imagem aproximada e o duplo clique volta a caber."""
    viewChanged = Signal(float, QPointF)

    MAX_ZOOM = 16.0

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._pix: QPixmap | None = None
        self.view_zoom = 1.0
        self.view_pan = QPointF(0, 0)
        self._grab: QPointF | None = None

    def set_image(self, img) -> None:
        self._pix = QPixmap.fromImage(pil_to_qimage(img)) if img is not None else None
        self._fit()

    def set_message(self, title: str, text: str = "") -> None:
        """Sem imagem, com um aviso no meio (desenhando, falhou, sem simulação)."""
        self.placeholder = (title, text)
        self._pix = None
        self._fit()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()
        self.view_pan = self._clamped(self.view_pan)

    placeholder = (tr("No simulation yet"), tr("Pick a state in the panel and click Simulate (F9 opens this page)."))

    # ---- zoom e posição
    def _scale(self) -> float:
        if self._pix is None or self._pix.isNull():
            return 1.0
        return min(self.width() / self._pix.width(), self.height() / self._pix.height()) * self.view_zoom

    def _clamped(self, pan: QPointF) -> QPointF:
        """A imagem aproximada não sai da área: arrastar não deixa borda vazia."""
        if self._pix is None or self._pix.isNull():
            return pan
        s = self._scale()
        mx = max(0.0, (self._pix.width() * s - self.width()) / 2)
        my = max(0.0, (self._pix.height() * s - self.height()) / 2)
        return QPointF(max(-mx, min(mx, pan.x())), max(-my, min(my, pan.y())))

    def set_view(self, zoom: float, pan: QPointF, notify: bool = False) -> None:
        self.view_zoom = max(1.0, min(self.MAX_ZOOM, zoom))
        self.view_pan = self._clamped(pan)
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.view_zoom > 1 else Qt.CursorShape.ArrowCursor)
        self.update()
        if notify:
            self.viewChanged.emit(self.view_zoom, self.view_pan)

    def zoom_by(self, factor: float, at: QPointF | None = None) -> None:
        """Aproxima mantendo parado o ponto `at` (o centro, sem ele)."""
        c = QPointF(self.width() / 2, self.height() / 2)
        at = at if at is not None else c
        new = max(1.0, min(self.MAX_ZOOM, self.view_zoom * factor))
        k = new / self.view_zoom
        self.set_view(new, (at - c) - (at - c - self.view_pan) * k, notify=True)

    def reset_view(self) -> None:
        self.set_view(1.0, QPointF(0, 0), notify=True)

    def wheelEvent(self, e):  # noqa: N802
        steps = e.angleDelta().y() / 120
        if steps and self._pix is not None:
            self.zoom_by(1.25 ** steps, e.position())
        e.accept()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.view_zoom > 1:
            self._grab = e.position() - self.view_pan
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._grab is not None:
            self.set_view(self.view_zoom, e.position() - self._grab, notify=True)

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._grab = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if self.view_zoom > 1 else Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        self.reset_view()

    def paintEvent(self, e):  # noqa: N802 — sem imagem: um aviso no meio em vez do vazio
        p = QPainter(self)
        if self._pix is not None:
            s = self._scale()
            w, h = self._pix.width() * s, self._pix.height() * s
            target = QRectF((self.width() - w) / 2 + self.view_pan.x(),
                            (self.height() - h) / 2 + self.view_pan.y(), w, h)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, s < 1)   # ampliado: pixels nítidos
            p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))
            p.end()
            return
        r = self.rect()
        f = p.font()
        f.setPointSizeF(f.pointSizeF() + 2)
        f.setBold(True)
        p.setFont(f)
        p.setPen(self.palette().text().color())
        p.drawText(r.adjusted(0, -16, 0, -16), Qt.AlignmentFlag.AlignCenter, self.placeholder[0])
        f.setPointSizeF(f.pointSizeF() - 2)
        f.setBold(False)
        p.setFont(f)
        c = self.palette().text().color()
        c.setAlpha(150)
        p.setPen(c)
        p.drawText(r.adjusted(0, 18, 0, 18), Qt.AlignmentFlag.AlignCenter, self.placeholder[1])
        p.end()

    def _fit(self) -> None:
        if self._pix is None:
            self.clear()
            self.update()
            return
        self.setPixmap(self._pix.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.FastTransformation))
        self.update()


class SimulatorPage(QWidget):
    title = tr("Simulator")

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.sim = None
        self.view_box = None      # não "window": esconderia QWidget.window()
        self.cache: dict[tuple, object] = {}
        # tudo que é pesado (simular, auditar, desenhar um tick, salvar) roda fora da interface;
        # _gen descarta resultados de uma simulação que já foi trocada
        self._gen = 0
        self._rendering = False
        self._want: tuple | None = None
        self._audit_item: QListWidgetItem | None = None
        self._failed: dict[tuple, str] = {}     # ticks que não desenharam (não tenta de novo)
        self._stale = False                     # o personagem mudou com a página fechada
        self._rerun = False                     # mudou enquanto uma simulação rodava
        self._marks: list[int] = []             # ticks de acerto, defesa e troca de fase
        self._loop: tuple[int, int] | None = None
        self._hidden_items: set[tuple] = set()  # helpers e explods escondidos um a um
        self._resume_at = 0
        prefs = self._prefs()

        self._auto = QTimer(self, singleShot=True, interval=350, timeout=self._auto_run)
        self._save_timer = QTimer(self, singleShot=True, interval=500, timeout=self._save_prefs)

        self.state = QComboBox()
        self.state.setEditable(True)
        self.state.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.state.setToolTip(tr("State to simulate (the .cmd moves appear in the list)"))
        self.state.activated.connect(lambda _: self.run())
        self.state.lineEdit().returnPressed.connect(self.run)
        self.reload_states = QToolButton()
        self.reload_states.setIcon(icon("reload"))
        self.reload_states.setToolTip(tr("Reload the state list"))
        self.reload_states.setAutoRaise(True)
        self.reload_states.clicked.connect(lambda: self.fill_states())
        self.dist = QDoubleSpinBox()
        self.dist.setRange(0, 400)
        self.dist.setValue(float(prefs.get("dist", 50)))
        self.dist.setSuffix(" px")
        self.dist.valueChanged.connect(lambda _: self._param_changed())
        self.max_ticks = QSpinBox()
        self.max_ticks.setRange(30, 3000)
        self.max_ticks.setValue(int(prefs.get("max_ticks", 900)))
        self.max_ticks.valueChanged.connect(lambda _: self._param_changed())
        self.zoom = QSpinBox()
        self.zoom.setRange(1, 4)
        self.zoom.setSuffix("x")
        self.zoom.setValue(int(prefs.get("quality", 2)))
        self.zoom.setToolTip(tr("Resolution of the drawing and of the GIF. To get closer, use the mouse wheel on the image"))
        self.zoom.valueChanged.connect(lambda _: (self.show_tick(), self._save_soon()))
        self.background = QComboBox()
        for _key, label, color in BACKGROUNDS:
            self.background.addItem(label, color)
        self.background.setCurrentIndex(next((i for i, b in enumerate(BACKGROUNDS) if b[0] == prefs.get("background")), 0))
        self.background.currentIndexChanged.connect(lambda _: (self.show_tick(), self._save_soon()))
        self.chain = QLineEdit()
        self.chain.setPlaceholderText(tr("e.g. 200 210 1000"))
        self.chain.setToolTip(tr("Move chain (combo): each state comes in as soon as it can — cancels if it touched and there is a cancel, links when P1 can act again. Empty = only the state"))
        self.chain.returnPressed.connect(self.run)
        self.compare = QComboBox()
        self.compare.setToolTip(tr("Also simulates another version of the character (the one saved on disk or a backup) and shows both side by side, tick by tick"))
        self.compare.activated.connect(lambda _: self._param_changed())
        self.sim_b = None
        self.p2_mode = QComboBox()
        for label, *_ in P2_MODES:
            self.p2_mode.addItem(label)
        self.p2_mode.setCurrentIndex(max(0, min(len(P2_MODES) - 1, int(prefs.get("p2_mode", 0)))))
        self.p2_mode.setToolTip(tr("How P2 waits for the move: the guard follows the HitDef guardflag"))
        self.p2_mode.activated.connect(lambda _: self._param_changed())
        self.sound = QCheckBox(tr("Sound"))
        self.sound.setChecked(bool(prefs.get("sound", True)))
        self.sound.setToolTip(tr("Plays the move sounds (PlaySnd, hit and guard) during playback, at normal speed"))
        self.sound.toggled.connect(self._sound_toggled)
        self.audio = SoundPlayer()
        if not self.audio.available():
            self.sound.setChecked(False)
            self.sound.setEnabled(False)
            self.sound.setToolTip(tr("Sound is not available on this system"))
        self._clock: tuple[int, float] | None = None   # (tick, hora) em que o play (re)começou
        self._play_pos = -1
        run = QPushButton(tr("Simulate"))
        run.setProperty("primary", True)
        run.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # o espaço é do play, não do botão
        run.clicked.connect(self.run)
        self.run_btn = run
        compact(self.state, self.dist, self.max_ticks, self.zoom, self.p2_mode, self.compare, self.background)
        run.setMinimumHeight(32)
        self.player = tool_bar([
            ("step_back", tr("Previous tick (,)"), lambda: self.step(-1)),
            ("play", tr("Play / pause (space)"), self.toggle_play, True),
            ("step_fwd", tr("Next tick (.)"), lambda: self.step(1)),
            None,
            ("left", tr("Previous hit or phase (Shift+Left)"), lambda: self.jump_mark(-1)),
            ("right", tr("Next hit or phase (Shift+Right)"), lambda: self.jump_mark(1)),
            ("loop", tr("Repeat (L)"), self._set_repeat, True),
            MORE,
            ("fade_in", tr("Repeat from this tick ([)"), lambda: self.mark_loop(True)),
            ("fade_out", tr("Repeat up to this tick (])"), lambda: self.mark_loop(False)),
            ("close", tr("Repeat the whole move"), self.clear_loop),
            None,
            ("export", tr("Save GIF…"), self.save_gif),
            ("sheet_save", tr("Save tick-by-tick sheets…"), self.save_sheets),
        ])
        self.play_action = self.player.actions_by_tip[tr("Play / pause (space)")]
        self.repeat_action = self.player.actions_by_tip[tr("Repeat (L)")]
        self._repeat = bool(prefs.get("repeat", True))
        self.repeat_action.setChecked(self._repeat)
        self._set_player(False)
        self.speed = QComboBox()
        for label, v in SPEEDS:
            self.speed.addItem(label, v)
        self.speed.setCurrentIndex(max(0, self.speed.findData(float(prefs.get("speed", 1.0)))))
        self.speed.setToolTip(tr("Playback speed"))
        self.speed.currentIndexChanged.connect(self._speed_changed)
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.info = QLabel(tr("Pick a state and click Simulate."))
        self.info.setWordWrap(True)
        self.info.setObjectName("dim")
        self.events = QListWidget()
        self.events.itemActivated.connect(self.on_event)
        self.events.itemClicked.connect(self.on_event)
        setup = Section(tr("Simulation"), "sim.setup")
        setup.add(form_row(tr("State"), self.state, self.reload_states), form_row(tr("Chain"), self.chain),
                  form_row("P2", self.p2_mode), form_row(tr("Distance"), self.dist), run)
        advanced = Section(tr("Advanced"), "sim.advanced", collapsed=True)
        advanced.add(form_row(tr("Max ticks"), self.max_ticks), form_row(tr("Compare"), self.compare))

        # camadas: o que aparece na prévia e no GIF
        hidden = set(prefs.get("hidden", ()))
        self.layer_checks: dict[str, QCheckBox] = {}
        grid_box = QWidget()
        grid_box.setObjectName("plain")
        grid = QGridLayout(grid_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        for k, (key, label, tip) in enumerate(LAYERS):
            cb = QCheckBox(label)
            cb.setToolTip(tip)
            cb.setChecked(key not in hidden)
            cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            cb.toggled.connect(lambda _: self._layers_changed())
            self.layer_checks[key] = cb
            grid.addWidget(cb, k // 2, k % 2)
        self.preset = QComboBox()
        for key, label, _hidden in VIEW_PRESETS:
            self.preset.addItem(label, key)
        self.preset.addItem(tr("Custom"), None)
        self.preset.setToolTip(tr("Ready-made sets of layers"))
        self.preset.activated.connect(self._apply_preset)
        compact(self.preset)
        self.items_list = QListWidget()
        self.items_list.setToolTip(tr("Hide one helper or one explod of this move"))
        self.items_list.setMaximumHeight(112)
        self.items_list.itemChanged.connect(self._item_toggled)
        self.items_list.hide()
        layers = Section(tr("Layers"), "sim.layers", collapsed=True)
        layers.add(form_row(tr("Show"), self.preset), grid_box, self.items_list)
        self._sync_preset()

        view = Section(tr("Display"), "sim.view")
        view.add(form_row(tr("Background"), self.background), form_row(tr("Quality"), self.zoom), self.sound)
        # dano do golpe: cada acerto/defesa, total, vida que sobra ao P2 e energia
        self.damage = QLabel()
        self.damage.setObjectName("dim")
        self.damage.setToolTip(tr("Damage × the attack of [Data]; P2 has defence 100 and 1000 of life. Pause P1/P2 "
                                  "and stun (hittime) in ticks."))
        self.damage.setWordWrap(True)
        self.damage.setTextFormat(Qt.TextFormat.RichText)
        self.damage.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        damage = Section(tr("Damage of the move"), "sim.damage")
        damage.add(self.damage)
        damage.hide()                   # só aparece com uma simulação pronta
        self.damage_sec = damage
        events = Section(tr("Events and problems"), "sim.events")
        self.ask_btn = QPushButton(icon("ai"), tr("Ask the AI to fix this move"))
        self.ask_btn.setToolTip(tr("Opens the assistant with the frame data and the problems of this simulation"))
        self.ask_btn.clicked.connect(self.ask_ai)
        self.ask_btn.setEnabled(False)
        self.events.setToolTip(tr("Click an event to go to its tick."))
        events.add(self.events, self.ask_btn)
        self.events.setMinimumHeight(180)
        content = QWidget()
        lv = QVBoxLayout(content)
        lv.addWidget(setup)
        lv.addWidget(self.info)
        lv.addWidget(damage)
        lv.addWidget(layers)
        lv.addWidget(view)
        lv.addWidget(advanced)
        lv.addWidget(events, 1)
        self.left = SidePanel(tr("Simulator"), content, header=False)

        self.file_tab = DocStrip()
        self.view = TickView()
        self.view_b = TickView()
        self.view_b.placeholder = (tr("Version B"), "")
        self.view_b.hide()
        self.view.viewChanged.connect(lambda z, pan: self._view_moved(self.view_b, z, pan))
        self.view_b.viewChanged.connect(lambda z, pan: self._view_moved(self.view, z, pan))
        self.ab_label = ElidedRichLabel()
        self.ab_label.setObjectName("dim")
        self.ab_label.setContentsMargins(12, 4, 12, 0)
        self.ab_label.hide()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(lambda _: self.show_tick())
        self.tick_label = QLabel("—")
        self.tick_label.setObjectName("counter")
        self.tick_label.setMinimumWidth(260)
        self.tick_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.timeline = MoveTimeline()
        self.timeline.seek.connect(self.slider.setValue)
        self.timeline.open_item.connect(self.open_item)
        self.timeline.moved.connect(self.move_item)
        self.timeline.can_drag = self._can_drag
        self._hidden_rows = set(prefs.get("hidden_rows", ()))
        self.timeline.set_hidden_rows(self._hidden_rows)
        self.fd_label = ElidedRichLabel()
        self.fd_label.setObjectName("dim")

        # zoom, faixas da linha do tempo, modo limpo e a lista de atalhos, à direita do play
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("dim")
        self.zoom_label.setMinimumWidth(40)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setToolTip(tr("100% fits the image in the area"))
        self.rows_btn = tool_button("guides", tr("Timeline rows"), lambda: None)
        self.rows_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        rows_menu = QMenu(self.rows_btn)
        for key, name in self.timeline.all_rows:
            a = rows_menu.addAction(name)
            a.setCheckable(True)
            a.setChecked(key not in self._hidden_rows)
            a.toggled.connect(lambda on, k=key: self._row_toggled(k, on))
        self.rows_btn.setMenu(rows_menu)
        self.clean_btn = tool_button("panel_hide", tr("Clean view: only the image and the player (H)"), self._set_clean, True)
        keys_btn = tool_button("help", tr("Keys: space play · , . tick · Shift+arrows next hit or phase · Home End · "
                                          "L repeat · [ ] repeat range · + - 0 zoom · B boxes · H clean view"), lambda: None)
        view_tools = [tool_button("zoom_out", tr("Zoom out (-)"), lambda: self.view.zoom_by(0.8)), self.zoom_label,
                      tool_button("zoom_in", tr("Zoom in (+)"), lambda: self.view.zoom_by(1.25)),
                      tool_button("fit", tr("Fit the image (0)"), self.view.reset_view), self.rows_btn, self.clean_btn, keys_btn]

        bottom_bar = QWidget()
        bottom_bar.setObjectName("docStrip")
        bottom_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rows = QVBoxLayout(bottom_bar)
        rows.setContentsMargins(12, 6, 12, 6)
        rows.setSpacing(4)
        top = QHBoxLayout()          # barra e linha do tempo com a mesma largura, tick por tick
        top.setSpacing(12)
        top.addWidget(self.fd_label, 1)
        top.addWidget(self.tick_label)
        rows.addLayout(top)
        rows.addWidget(self.timeline)
        transport = QHBoxLayout()
        transport.setSpacing(6)
        # numa caixa de tamanho fixo: a faixa quebra linha e não pede largura à barra
        player_box = QWidget()
        player_box.setObjectName("plain")
        player_box.setFixedSize(262, 32)
        self.player.setParent(player_box)
        self.player.setGeometry(0, 0, 262, 32)
        transport.addWidget(player_box)
        transport.addWidget(self.speed)
        transport.addWidget(self.slider, 1)
        for w in view_tools:
            transport.addWidget(w)
        rows.addLayout(transport)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.file_tab)
        views = QHBoxLayout()
        views.setSpacing(2)
        views.addWidget(self.view, 1)
        views.addWidget(self.view_b, 1)
        lay.addLayout(views, 1)
        lay.addWidget(self.ab_label)
        lay.addWidget(bottom_bar)
        self.timer = QTimer(self)
        self.timer.setInterval(round(1000 / 60))
        self.timer.timeout.connect(self._play_tick)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._install_keys()
        self.clean_btn.setChecked(bool(prefs.get("clean", False)))
        ctx.characterChanged.connect(self.load)
        ctx.modelChanged.connect(self._model_changed)

    def _install_keys(self) -> None:
        """Teclas do player na página e no painel. Num campo de texto as letras, as setas e o
        espaço continuam sendo do campo (o Qt dá preferência a ele)."""
        keys = [("Space", lambda: self.play_action.trigger(), False),
                (",", lambda: self.step(-1), True), ("Left", lambda: self.step(-1), True),
                (".", lambda: self.step(1), True), ("Right", lambda: self.step(1), True),
                ("Home", lambda: self.goto_end(False), False), ("End", lambda: self.goto_end(True), False),
                ("L", lambda: self.repeat_action.trigger(), False),
                ("[", lambda: self.mark_loop(True), False), ("]", lambda: self.mark_loop(False), False),
                ("+", lambda: self.view.zoom_by(1.25), True), ("=", lambda: self.view.zoom_by(1.25), True),
                ("-", lambda: self.view.zoom_by(0.8), True), ("0", self.view.reset_view, False),
                ("B", self._toggle_boxes, False), ("H", lambda: self.clean_btn.toggle(), False)]
        for host in (self, self.left):
            for seq, fn, repeat in keys:
                sc = QShortcut(QKeySequence(seq), host, fn)
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                sc.setAutoRepeat(repeat)

    # ------------------------------------------------------------ preferências

    def _prefs(self) -> dict:
        p = self.ctx.settings.get("simulator")
        return p if isinstance(p, dict) else {}

    def _save_soon(self) -> None:
        self._save_timer.start()

    def _save_prefs(self) -> None:
        self._save_timer.stop()
        s = self.ctx.settings
        s["simulator"] = {**self._prefs(), "dist": self.dist.value(), "max_ticks": self.max_ticks.value(),
                          "p2_mode": self.p2_mode.currentIndex(), "quality": self.zoom.value(),
                          "background": BACKGROUNDS[max(0, self.background.currentIndex())][0],
                          "sound": self.sound.isChecked(), "speed": self.speed.currentData(),
                          "repeat": self._repeat, "clean": self.clean_btn.isChecked(),
                          "hidden": sorted(k for k, cb in self.layer_checks.items() if not cb.isChecked()),
                          "hidden_rows": sorted(self._hidden_rows)}
        s.save()

    def _remember_move(self, state: int) -> None:
        """Último golpe e corrente de cada personagem (os 30 mais recentes)."""
        c = self.ctx.char
        if not c:
            return
        chars = dict(self._prefs().get("chars") or {})
        chars.pop(str(c.def_path), None)
        chars[str(c.def_path)] = {"state": state, "chain": self.chain.text()}
        self.ctx.settings["simulator"] = {**self._prefs(), "chars": dict(list(chars.items())[-30:])}
        self._save_soon()

    def _param_changed(self) -> None:
        """Mudou um parâmetro: refaz a simulação que está na tela, sem precisar clicar."""
        self._save_soon()
        if self.sim is not None:
            self._auto.start()

    def _auto_run(self) -> None:
        if self.sim is not None and self.isVisible() and self.run_btn.isEnabled():
            self._resume_at = self.slider.value()
            self.run()

    # ------------------------------------------------------------ camadas

    def _hide(self) -> frozenset:
        return frozenset({k for k, cb in self.layer_checks.items() if not cb.isChecked()} | self._hidden_items)

    def _bg(self):
        c = self.background.currentData()
        return tuple(c) if c is not None else None

    def _layers_changed(self) -> None:
        self._sync_preset()
        self.show_tick()
        self._save_soon()

    def _sync_preset(self) -> None:
        hidden = {k for k, cb in self.layer_checks.items() if not cb.isChecked()}
        i = next((k for k, (_, _, h) in enumerate(VIEW_PRESETS) if h == hidden), self.preset.count() - 1)
        self.preset.setCurrentIndex(i)

    def _apply_preset(self, index: int) -> None:
        if not 0 <= index < len(VIEW_PRESETS):
            self._sync_preset()
            return
        hidden = VIEW_PRESETS[index][2]
        for key, cb in self.layer_checks.items():
            cb.blockSignals(True)
            cb.setChecked(key not in hidden)
            cb.blockSignals(False)
        self._layers_changed()

    def _toggle_boxes(self) -> None:
        on = not (self.layer_checks["clsn1"].isChecked() or self.layer_checks["clsn2"].isChecked())
        for key in ("clsn1", "clsn2"):
            self.layer_checks[key].setChecked(on)

    def _fill_items(self, sim) -> None:
        """Helpers e explods deste golpe, para esconder um a um."""
        from ...tools import simulate
        found = simulate.layer_items(sim)
        entries = [(("helper", n), n) for n in found["helpers"]] + \
                  [(("explod", a), tr("Explod anim {0}").format(a)) for a in found["explods"]]
        self._hidden_items &= {k for k, _ in entries}
        self.items_list.blockSignals(True)
        self.items_list.clear()
        for key, label in entries:
            it = QListWidgetItem(label)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked if key in self._hidden_items else Qt.CheckState.Checked)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.items_list.addItem(it)
        self.items_list.blockSignals(False)
        self.items_list.setVisible(bool(entries))

    def _item_toggled(self, item) -> None:
        key = tuple(item.data(Qt.ItemDataRole.UserRole))
        if item.checkState() == Qt.CheckState.Checked:
            self._hidden_items.discard(key)
        else:
            self._hidden_items.add(key)
        self.show_tick()

    # ------------------------------------------------------------ vista

    def _view_moved(self, other: TickView, zoom: float, pan: QPointF) -> None:
        other.set_view(zoom, pan)               # A e B aproximam juntos
        self.zoom_label.setText(f"{round(zoom * 100)}%")

    def _set_clean(self, on: bool) -> None:
        self.timeline.setVisible(not on)
        self.fd_label.setVisible(not on)
        self._save_soon()

    def _row_toggled(self, key: str, on: bool) -> None:
        (self._hidden_rows.discard if on else self._hidden_rows.add)(key)
        self.timeline.set_hidden_rows(self._hidden_rows)
        self._save_soon()

    # ------------------------------------------------------------ navegação e repetição

    def goto_end(self, last: bool) -> None:
        if self.sim:
            self.slider.setValue(self.slider.maximum() if last else 0)

    def jump_mark(self, d: int) -> None:
        """Vai ao próximo (ou anterior) acerto, defesa ou troca de fase do golpe."""
        cur = self.slider.value()
        found = [m for m in self._marks if (m > cur if d > 0 else m < cur)]
        if found:
            self.slider.setValue(found[0] if d > 0 else found[-1])

    def _index_of(self, t: int) -> int:
        return next((i for i, r in enumerate(self.sim.track) if r["t"] >= t), len(self.sim.track) - 1)

    def _build_marks(self, phases: list) -> None:
        marks = {i for i in range(1, len(phases)) if phases[i] != phases[i - 1]}
        marks |= {self._index_of(e["t"]) for e in self.sim.events if e["kind"] in ("acerto", "defesa")}
        self._marks = sorted(marks)

    def _set_repeat(self, on: bool) -> None:
        self._repeat = on
        self._save_soon()

    def mark_loop(self, start: bool) -> None:
        if not self.sim:
            return
        cur = self.slider.value()
        a, b = self._loop or (0, self.slider.maximum())
        self._loop = (cur, max(b, cur)) if start else (min(a, cur), cur)
        self.timeline.set_loop(self._loop)
        self._clock = None

    def clear_loop(self) -> None:
        self._loop = None
        self.timeline.set_loop(None)
        self._clock = None

    def _speed_changed(self, _=None) -> None:
        self._clock = None
        self.audio.stop()
        self._save_soon()

    def _set_player(self, on: bool) -> None:
        """Tocar, avançar e salvar só fazem sentido com uma simulação pronta."""
        for act in self.player.actions_by_tip.values():
            act.setEnabled(on)

    def _model_changed(self, part) -> None:
        if part in ("states", "commands", "def"):
            self.fill_states()      # um estado ou golpe novo entra na lista na hora
        if not self.run_btn.isEnabled():
            self._rerun = True      # simulando a versão antiga: refaz quando ela terminar
            return
        if self.sim:
            self._resume_at = self.slider.value()
            self.sim = None
            self._gen += 1
            self.play_action.setChecked(False)
            self._set_player(False)
            if self.isVisible():
                # mudou à vista (desfazer, arrastar na linha do tempo): simula de novo no mesmo tick
                QTimer.singleShot(0, self.run)
            else:
                self._stale = True  # simula de novo ao voltar para a página, no mesmo tick
                self.timeline.set_data(None)
                self.fd_label.setText(tr("The character changed: simulate again."))

    def hideEvent(self, e):  # noqa: N802 — trocar de página para o play e o som
        self.play_action.setChecked(False)
        if self._save_timer.isActive():
            self._save_prefs()
        super().hideEvent(e)

    def load(self) -> None:
        self.sim = None
        self._gen += 1
        self._want = None
        self._stale = self._rerun = False
        self._auto.stop()
        self._drop_pending_render()
        self.play_action.setChecked(False)
        self._set_player(False)
        self.ask_btn.setEnabled(False)
        self.run_btn.setEnabled(True)
        self.cache.clear()
        self._failed.clear()
        self._audit_item = None
        self._marks = []
        self._hidden_items.clear()
        self.items_list.clear()
        self.items_list.hide()
        self.clear_loop()
        self.view.reset_view()
        self.events.clear()
        self.damage_sec.hide()
        self.view.set_message(*TickView.placeholder)
        self.timeline.set_data(None)
        self.fd_label.setText("")
        self.sim_b = None
        self.view_b.set_image(None)
        self.view_b.hide()
        self.ab_label.hide()
        self.fill_compare()
        c = self.ctx.char
        if not c:
            return
        self.file_tab.set_file(tr("{0} · simulation").format(c.name), "simulate")
        self.fill_states(keep=False)
        last = (self._prefs().get("chars") or {}).get(str(c.def_path)) or {}
        if self.state.findData(last.get("state")) >= 0:       # o último golpe testado neste personagem
            self.state.setCurrentIndex(self.state.findData(last["state"]))
        self.chain.setText(last.get("chain", ""))
        self.info.setText(tr("Pick a state and click Simulate."))
        # sem simulação a linha do tempo fica desligada (antes parecia pronta para arrastar)
        self.slider.setEnabled(False)
        self.tick_label.setText(tr("No simulation"))

    def fill_states(self, keep: bool = True) -> None:
        """Lista os estados do personagem (os golpes do .cmd marcados). Com `keep`, o estado
        escolhido continua escolhido depois de refazer a lista."""
        c = self.ctx.char
        cur = self.current_state() if keep else None
        self.state.blockSignals(True)
        self.state.clear()
        if c:
            try:
                from ...tools import simulate
                moves = simulate.move_states(c)
            except Exception:  # noqa: BLE001
                moves = []
            try:
                sds = c.all_statedefs(include_common=False)
            except Exception:  # noqa: BLE001 — texto aplicado com erro: fica só com os golpes
                sds = {}
            for n in sorted(set(moves) | {n for n in sds if n >= 0}):
                self.state.addItem(f"{n}" + (tr("  ★ move") if n in moves else ""), n)
            if cur is not None:
                i = self.state.findData(cur)
                if i >= 0:
                    self.state.setCurrentIndex(i)
                else:
                    self.state.setEditText(str(cur))
            elif moves:   # começa no primeiro golpe, não no estado de vitória
                self.state.setCurrentIndex(max(0, self.state.findData(min(moves))))
        self.state.blockSignals(False)

    def fill_compare(self) -> None:
        """Versões para o A/B: a salva no disco e os backups (o mais novo primeiro)."""
        cur = self.compare.currentData()
        self.compare.blockSignals(True)
        self.compare.clear()
        self.compare.addItem(tr("Nothing"), None)
        c = self.ctx.char
        if c:
            self.compare.addItem(tr("Version saved on disk"), str(c.def_path))
            root = c.dir / "_backups"
            for d in sorted(root.iterdir() if root.is_dir() else [], reverse=True):
                if (d / c.def_path.name).exists():
                    self.compare.addItem(f"Backup {d.name}", str(d / c.def_path.name))
        i = self.compare.findData(cur)
        self.compare.setCurrentIndex(max(0, i))
        self.compare.blockSignals(False)

    def showEvent(self, e):  # noqa: N802 — um backup novo aparece na lista
        self.fill_compare()
        super().showEvent(e)
        if self._stale and self.ctx.char:       # editou em outra página: já volta atualizado
            self._stale = False
            QTimer.singleShot(0, self.run)

    # ------------------------------------------------------------ simulação

    def current_state(self) -> int | None:
        d = self.state.currentData()
        if d is not None and self.state.currentText().split()[0] == str(d):
            return d
        try:
            return int(self.state.currentText().split()[0])
        except (ValueError, IndexError):
            return None

    def run_state(self, n: int) -> None:
        i = self.state.findData(n)
        if i >= 0:
            self.state.setCurrentIndex(i)
        else:
            self.state.setEditText(str(n))
        self.run()

    def run(self) -> None:
        c, n = self.ctx.char, self.current_state()
        chain = tuple(int(x) for x in re.findall(r"-?\d+", self.chain.text()))
        if len(chain) > 1:
            n = chain[0]                # a cadeia começa no 1º estado dela
        else:
            chain = ()
        if not c or n is None:
            return
        self._auto.stop()
        self._stale = False
        self._remember_move(n)
        from ...tools import simulate
        dist, mx = self.dist.value(), self.max_ticks.value()
        _, pose, guard = P2_MODES[max(0, self.p2_mode.currentIndex())]
        other = self.compare.currentData()
        mugen = self.ctx.settings.get("mugen_dir") or None
        self._gen += 1
        gen = self._gen
        self._want = None           # para o pré-desenho da simulação anterior
        self._drop_pending_render()
        self.play_action.setChecked(False)
        self.run_btn.setEnabled(False)
        self.info.setText(tr("Simulating state {0}…").format(n))

        def work():
            # a auditoria dos efeitos vem depois, num trabalho à parte: o primeiro tick aparece
            # sem esperar a medida sprite a sprite das faíscas
            sim = simulate.simulate(c, n, p2_dist=dist, max_ticks=mx, p2_pose=pose, p2_guard=guard,
                                    chain=chain, from_ground=simulate.starts_on_ground(c, n))
            # o mesmo golpe com o P2 no outro modo (defendendo ou não) roda depois, num trabalho à
            # parte (on_done): o primeiro tick não espera e um erro lá não derruba esta simulação
            sim.other_args = None if chain or pose == "air" else (c, n, dist, mx, pose, not guard)
            box = simulate.preview_window(sim) if sim.track else None
            simb = None
            if other:                   # A/B: a outra versão com a mesma simulação e a mesma câmera
                from ... import project
                cb = project.load(other, mugen)
                simb = simulate.simulate(cb, n, p2_dist=dist, max_ticks=mx, p2_pose=pose, p2_guard=guard,
                                         chain=chain, from_ground=simulate.starts_on_ground(cb, n))
                if simb.track and box:
                    bb = simulate.preview_window(simb)
                    box = (min(box[0], bb[0]), max(box[1], bb[1]), min(box[2], bb[2]), max(box[3], bb[3]))
            return gen, sim, box, simb

        job = Job(work)
        job.signals.done.connect(self.on_done)
        job.signals.failed.connect(self._sim_failed)
        start(job, self)

    def _sim_failed(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.info.setText(tr("The simulation failed:\n") + msg)

    def on_done(self, result) -> None:
        gen, sim, box, simb = result
        if gen != self._gen:
            return          # outra simulação foi pedida depois desta
        self.sim_b = simb
        self.run_btn.setEnabled(True)
        self.sim = sim
        self.cache.clear()
        self._failed.clear()
        self._want = None
        self.view_box = box
        self._fill_items(sim)
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(sim.track) - 1, len(self.sim_b.track) - 1 if self.sim_b else 0))
        # depois de arrastar um evento a simulação nova volta ao mesmo tick
        self.slider.setValue(min(getattr(self, "_resume_at", 0), self.slider.maximum()))
        self._resume_at = 0
        self.slider.blockSignals(False)
        self.slider.setEnabled(bool(sim.track))
        self._set_player(bool(sim.track))
        if self._loop and self._loop[1] > self.slider.maximum():
            self.clear_loop()                   # o golpe ficou mais curto que o trecho marcado
        self.ask_btn.setEnabled(True)
        guards = sum(1 for e in sim.events if e["kind"] == "defesa")
        hits = sum(1 for e in sim.events if e["kind"] == "acerto")
        from ...tools import simulate as _sim
        cr = _sim.chain_report(sim)
        self.info.setText(tr("{0} ticks · {1} hit(s)").format(sim.tick, hits)
                          + (tr(" · combo {0}").format(cr["combo"]) if hits > 1 else "")
                          + (tr(" · P2 recovered before the hit at t{0}").format(cr["broke"]["t"])
                             if cr["broke"] and hits > 1 else "")
                          + (tr(" · {0} guard(s)").format(guards) if guards else "")
                          + (tr(" · not simulated: {0}").format(', '.join(sorted(set(map(str, sim.unsupported))))[:120])
                             if sim.unsupported else ""))
        from ...tools import movetimeline, simulate
        tl = movetimeline.build(sim)
        self.timeline.set_data(tl)
        self.timeline.set_current(self.slider.value())
        self._build_marks(tl["phases"])
        self._fd_main = tl["frame_data"]
        self.fd_label.setText(self._chain_text(simulate.chain_report(sim)) if sim.chain
                              else self._fd_text(self._fd_main))
        self.damage.setText(self._damage_text(simulate.damage_report(sim)))
        self.damage_sec.show()
        ab = self.sim_b is not None
        self.view_b.setVisible(ab)
        self.ab_label.setVisible(ab)
        if ab:
            b = self.sim_b
            text = (self._chain_text(simulate.chain_report(b)) if b.chain
                    else self._fd_text(simulate.frame_data(b)))
            hits = sum(1 for e in b.events if e["kind"] == "acerto")
            self.fd_label.setText(tr("<b>A</b> (current) · ") + self.fd_label.text())
            self.ab_label.setText(tr("<b>B</b> ({0}) · {1} · {2} ticks · {3} hit(s)").format(self.compare.currentText(), text, b.tick, hits))
        self.events.clear()
        self._audit_item = QListWidgetItem(tr("Checking the effects…"))
        self._audit_item.setForeground(self.palette().placeholderText())
        self.events.addItem(self._audit_item)
        if sim.ignored:
            note = QListWidgetItem("ⓘ " + tr("Not reproduced in this preview: {0}").format(", ".join(sorted(sim.ignored))))
            note.setToolTip(tr("These controllers ran, but the simulator does not apply them: check them in the engine"))
            self.events.addItem(note)
        for ev in sim.events:
            snd = ev.get("snd")
            if ev["kind"] == "ctrl" and ev.get("value") == "playsnd" and snd:
                txt = tr("sound {0}{1},{2}").format('F' if snd[2] else '', snd[0], snd[1])
            elif ev["kind"] in ("estado", "acerto", "defesa", "pausa", "helper", "explod"):
                txt = {"estado": tr("state {0}").format(ev.get('value')),
                       "acerto": tr("HIT {0}").format(ev.get('label', ''))
                       + (tr(" · combo {0}").format(ev["combo"]) if ev.get("combo") else ""),
                       "defesa": tr("GUARD {0}").format(ev.get('label', '')),
                       "pausa": f"pausa {ev.get('value')}", "helper": f"helper {ev.get('value')}",
                       "explod": f"explod anim {ev.get('value')}"}[ev["kind"]]
            else:
                continue
            it = QListWidgetItem(f"t{ev['t']:>4}  {ev.get('who', '')}: {txt}")
            it.setData(Qt.ItemDataRole.UserRole, ev["t"])
            self.events.addItem(it)
        self.view.set_message(tr("Drawing…"))
        self.show_tick()             # o primeiro tick entra na fila antes da auditoria
        focus = self.window().focusWidget() if self.window() else None
        if not isinstance(focus, QLineEdit) and self.isVisible():
            self.setFocus()          # o teclado já controla o player
        if self._rerun:              # o personagem mudou enquanto esta simulação rodava
            self._rerun = False
            self._resume_at = self.slider.value()
            QTimer.singleShot(0, self.run)
        job = Job(lambda: (gen, simulate.audit(sim)))
        job.signals.done.connect(self._audited)
        job.signals.failed.connect(self._audit_failed)
        start(job, self, priority=-1)
        args = getattr(sim, "other_args", None)
        if args:                    # acerto e defesa lado a lado (issue #4), sem atrasar o 1º tick
            def other(args=args):
                c, n, dist, mx, pose, guard = args
                o = simulate.simulate(c, n, p2_dist=dist, max_ticks=mx, p2_pose=pose, p2_guard=guard,
                                      from_ground=simulate.starts_on_ground(c, n))
                return gen, simulate.frame_data(o)
            job = Job(other)
            job.signals.done.connect(self._other_mode_done)
            start(job, self, priority=-2)

    def _other_mode_done(self, result) -> None:
        gen, fd = result
        if gen != self._gen:
            return
        text = self._fd_text(self._fd_main, fd)
        self.fd_label.setText((tr("<b>A</b> (current) · ") if self.sim_b is not None else "") + text)

    def _audited(self, result) -> None:
        gen, problems = result
        if gen != self._gen or self._audit_item is None:
            return
        row = self.events.row(self._audit_item)
        self.events.takeItem(row)
        self._audit_item = None
        for k, p in enumerate(problems):
            it = QListWidgetItem(f"⚠ {p['message']}")       # a mensagem já vem traduzida; o kind é para scripts
            it.setToolTip(p["kind"])
            it.setData(Qt.ItemDataRole.UserRole, p.get("t"))    # clicar leva ao tick do problema
            self.events.insertItem(row + k, it)

    def _audit_failed(self, msg: str) -> None:
        if self._audit_item is not None:
            self._audit_item.setText(tr("Could not check the effects: ") + msg.splitlines()[0])
            self._audit_item = None

    def ask_ai(self) -> None:
        """Manda ao assistente o golpe simulado: estado, frame data e o que a auditoria achou."""
        if not self.sim:
            return
        from ...tools import simulate
        n = self.sim.chain[0] if self.sim.chain else self.current_state()
        fd = simulate.frame_data(self.sim)
        probs = [self.events.item(k).text().lstrip("⚠ ") for k in range(self.events.count())
                 if self.events.item(k).text().startswith("⚠")]
        where = {"acerto": "hit", "defesa": "guard"}.get(fd["outcome"], "miss")
        text = (f"In the simulator, state {n} (P2 {self.p2_mode.currentText().lower()}, {self.dist.value():g} px): "
                f"startup {fd['startup']}, active {fd['active']}, recovery {fd['recovery']}, {where}"
                + (f" {fd['advantage']:+d}" if fd.get("advantage") is not None else "") + ". "
                + ("Problems found: " + "; ".join(probs) + ". " if probs else "No problems found by the audit. ")
                + "Look at the move (get_move, simulate_state with a picture) and fix what is wrong; "
                  "tell me what you changed.")
        self.ctx.ask_ai.emit(text)

    @staticmethod
    def _damage_text(r: dict) -> str:
        """Dano total, vida que sobra ao P2 e energia, com uma linha por acerto ou defesa."""
        if not r["rows"]:
            return tr("The move did not touch P2: no damage.")
        head = tr("<b>{0}</b> damage ({1}% of the life) · P2 is left with <b>{2}</b>/1000").format(
            r["total"], r["percent"], r["life"])
        power = tr("Power: P1 <b>+{0}</b> · P2 <b>+{1}</b>").format(r["p1power"], r["p2power"])
        cells = "".join(f"<th align='left'>{h}</th>" for h in
                        (tr("tick"), "", tr("damage"), tr("power"), tr("pause"), tr("stun"), "attr"))
        rows = []
        for e in r["rows"]:
            hit = e["kind"] == "acerto"
            kind = tr("hit") if hit else tr("guard") if e["kind"] == "defesa" else "TargetLifeAdd"
            rows.append("<tr>" + "".join(f"<td>{v}</td>" for v in (
                e["t"], kind, e.get("dealt", 0), f"+{e.get('p1power', 0)}", "/".join(map(str, e.get("pause", []))),
                e.get("hittime", ""), e.get("attr", ""))) + "</tr>")
        table = (f"<table cellspacing='4' cellpadding='0' style='margin-top:4px'><tr>{cells}</tr>"
                 + "".join(rows) + "</table>")
        return f"{head}<br>{power}{table}"

    @staticmethod
    def _chain_text(r: dict) -> str:
        """Resumo da cadeia: cada passagem (cancela/liga), o combo e onde quebrou."""
        parts = [str(r["chain"][0])]
        for st in r["steps"]:
            how = {"cancela": tr("cancel"), "liga": tr("link")}.get(st['how'], st['how'])
            parts.append(f"→ <b>{st['to']}</b> <span style='opacity:.7'>({how} t{st['t']})</span>")
        if not r["complete"]:
            parts.append(tr("→ <b>{0}</b> did not come in").format(r['chain'][r['reached']]))
        combo = tr("combo of <b>{0}</b> hit(s) out of {1}").format(r['combo'], r['hits'])
        if r["broke"]:
            combo += tr(" · broke at t{0} (state {1}): P2 recovered").format(r['broke']['t'], r['broke']['state'])
        return " ".join(parts) + " · " + combo

    @staticmethod
    def _fd_text(fd: dict, other: dict | None = None) -> str:
        """Resumo do frame data com a legenda das cores da barra. `other` é a mesma simulação
        com o P2 no outro modo (defendendo ou não): o resumo mostra o acerto e a defesa."""
        def chip(key, value, extra=""):
            name, col = PHASES[key]
            return f"<span style='color:{col}'>■</span> {name} <b>{value}</b>{extra}"
        if fd["startup"] is None:
            return tr("No active Clsn1: nothing to measure")
        pause = tr(" + {0} in the pause").format(fd['startup_pause']) if fd.get("startup_pause") else ""
        parts = [chip("preparo", fd["startup"], pause), chip("ativo", fd["active"]),
                 chip("recuperacao", fd["recovery"]), tr("Total <b>{0}</b>").format(fd['total'])]
        seen = set()
        for f in (fd, other):
            if not f or f["outcome"] in seen or f["startup"] is None:
                continue
            seen.add(f["outcome"])
            parts += SimulatorPage._advantage_text(f)
        if not seen & {"acerto", "defesa"}:
            parts.append(tr("missed P2"))
        return " · ".join(parts)

    @staticmethod
    def _advantage_text(fd: dict) -> list[str]:
        """Vantagem no acerto ou na defesa (o bloqueio à parte quando o guard.ctrltime devolve o
        controle antes) e a janela de cancelamento."""
        where = {"acerto": tr("on hit"), "defesa": tr("on guard")}.get(fd["outcome"])
        if where is None:
            return []
        adv, out = fd["advantage"], []
        if fd["knockdown"]:
            out.append(tr("{0}: knocks down").format(where))
        elif adv is not None:
            stun = fd.get("advantage_stun")
            out.append(f"{where} <b>{adv:+d}</b>" + (tr(" (blockstun {0:+d})").format(stun) if stun is not None else ""))
        cx = fd.get("cancel")
        if cx:
            # o cancelamento que pede Var/Power/NumHelper ganha * (não vale sempre)
            names = [str(n) for n in cx["into"]] + [f"{n}*" for n in cx.get("conditional", [])]
            into = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
            txt = tr("cancel {0} → {1}: from frame {2}, {3} frames").format(where, into, cx["from"], cx["window"])
            if cx["advantage"] is not None:
                txt += tr(", P2 stuck {0}").format(f"<b>{cx['advantage']:+d}</b>")
            if cx.get("conditional"):
                txt += tr(" (* only under a condition: Var, Power, helper…)")
            out.append(txt)
        return out

    # ------------------------------------------------------------ linha do tempo → código

    def _controller(self, item):
        from ...tools import movetimeline
        if not self.ctx.char or item.get("ci") is None or item.get("state") is None:
            return None, None
        return movetimeline.controller(self.ctx.char, item["state"], item["ci"])

    def _can_drag(self, item) -> bool:
        """Som, efeito ou tela cujo momento vem de um gatilho só (`Time = N`, `!Time`,
        `AnimElem = N`): o acerto depende das caixas, e o som do acerto vem do HitDef."""
        from ...tools import movetimeline
        if item.get("kind") in ("quadro", "pausa", "acerto", "defesa") or item.get("hitsound"):
            return False
        ctrl, _ = self._controller(item)
        return ctrl is not None and movetimeline.timing_trigger(ctrl) is not None

    def open_item(self, item) -> None:
        from ...tools import movetimeline
        ctrl, states = self._controller(item)
        if ctrl is None or states is None or states.path is None:
            return
        line = movetimeline.code_line(states, ctrl)
        self.ctx.request_code.emit(states.path.name, f"#{line}" if line else f"[{ctrl.section.name}]")

    def move_item(self, item, to: int) -> None:
        from ...tools import movetimeline
        if not self.sim or not self.ctx.char:
            return
        try:
            r = movetimeline.move_event(self.ctx.char, self.sim, item, to)
        except ValueError as e:
            return self.ctx.status.emit(str(e))
        # grava, entra no desfazer e avisa as páginas de código; a mudança simula de novo
        self.ctx.changed(r["part"])
        self.ctx.status.emit(f"[{r['controller']}]: {r['old']} → {r['new']}" + (f" · {r['note']}" if r["note"] else ""))

    def on_event(self, item) -> None:
        t = item.data(Qt.ItemDataRole.UserRole)
        if t is not None and self.sim:
            self.slider.setValue(self._index_of(t))

    # ------------------------------------------------------------ desenho dos ticks

    def _key(self, i: int) -> tuple:
        return (i, self.zoom.value(), self._hide(), self._bg())

    def show_tick(self) -> None:
        if not self.sim or not self.sim.track or self.view_box is None:
            return
        i = self.slider.value()
        rec = self.sim.track[min(i, len(self.sim.track) - 1)]
        self.timeline.set_current(i)
        p1 = rec["ents"][0] if rec["ents"] else None
        where = tr(" · state {0} anim {1} frame {2}").format(p1['state'], p1['anim'], p1['elem']) if p1 else ""
        from ...tools import simulate
        life = simulate.life_at(self.sim, rec["t"])
        where += tr(" · P2 life {0}").format(life) if life < 1000 else ""
        self.tick_label.setText(f"tick {rec['t']} / {self.sim.track[-1]['t']}{where}")
        img = self.cache.get(self._key(i))
        if img is not None:
            self._show(img)
            self._prefetch()
        elif self._key(i) in self._failed:
            self.view.set_message(tr("Could not draw this tick"), self._failed[self._key(i)])
        else:
            # um tick com várias faíscas giradas leva ~0,5 s: desenha fora da interface e mostra o
            # último pedido (arrastar a linha do tempo pula os do meio em vez de enfileirar)
            self._want = self._key(i)
            self._kick()

    def _kick(self) -> None:
        if self._rendering or self._want is None or not self.sim:
            return
        key, self._want = self._want, None
        if key in self.cache or key in self._failed:
            return self.show_tick() if key == self._key(self.slider.value()) else self._prefetch()
        from ...tools import simulate
        sim, simb, box, gen = self.sim, self.sim_b, self.view_box, self._gen
        i, zoom, hide, bg = key
        self._rendering = True

        def draw(s):                    # a versão mais curta fica no último tick dela
            if not s or not s.track:
                return None
            rec = s.track[min(i, len(s.track) - 1)]
            # A/B uses the same camera position as well as the same viewport dimensions.
            camera_rec = sim.track[min(i, len(sim.track) - 1)]
            window = simulate.preview_tick_window(camera_rec, box)
            return simulate.render_tick(s, rec, window, zoom=zoom, hide=hide, background=bg, hurtboxes=True)
        def work():
            try:
                return gen, key, (draw(sim), draw(simb))
            except Exception as e:  # noqa: BLE001 — o tick fica marcado e a tela mostra o erro
                return gen, key, f"{type(e).__name__}: {e}"
        job = Job(work)
        job.signals.done.connect(self._rendered)
        job.signals.failed.connect(self._render_failed)
        self._render_job = job
        start(job, self, priority=1)

    def _drop_pending_render(self) -> None:
        # um desenho ainda na fila não serve mais: sai da frente da simulação nova
        job = getattr(self, "_render_job", None)
        if job is not None and not job.finished and _pool().tryTake(job):
            job.finished = True
            self._rendering = False

    def _rendered(self, result) -> None:
        gen, key, img = result
        self._rendering = False
        if gen != self._gen:
            return self._kick()
        if isinstance(img, str):
            self._failed[key] = img
            self.ctx.status.emit(tr("Could not draw the tick: ") + img)
            if key == self._key(self.slider.value()):
                self.view.set_message(tr("Could not draw this tick"), img)
            return self._kick() if self._want is not None else self._prefetch()
        while len(self.cache) >= 400:           # tira o mais antigo, não o cache todo
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = img
        if key == self._key(self.slider.value()):
            self._show(img)
        if self._want is not None:
            self._kick()
        else:
            self._prefetch()

    def _show(self, img: tuple) -> None:
        self.view.set_image(img[0])
        if self.sim_b is not None:
            self.view_b.set_image(img[1])

    def _render_failed(self, msg: str) -> None:
        self._rendering = False
        self.ctx.status.emit(tr("Could not draw the tick: ") + msg.splitlines()[0])

    def _prefetch(self) -> None:
        # adianta os próximos ticks enquanto a interface está parada, para o play ficar fluido
        if self._rendering or self._want is not None or not self.sim:
            return
        i = self.slider.value()
        for j in range(i + 1, min(self.slider.maximum() + 1, i + 31)):
            if self._key(j) not in self.cache and self._key(j) not in self._failed:
                self._want = self._key(j)
                return self._kick()

    def step(self, d: int, wrap: bool = False) -> None:
        if not self.sim:
            return
        cur = self._key(self.slider.value())
        if wrap and cur not in self.cache and cur not in self._failed:
            return          # tocando: espera o tick atual aparecer antes de avançar
        v = self.slider.value() + d
        if v > self.slider.maximum():
            v = 0 if wrap else self.slider.maximum()
        self.slider.setValue(max(0, v))

    def toggle_play(self, on: bool) -> None:
        self._clock = None
        self.audio.stop()
        if on and self.sim and not self._repeat:     # no fim do golpe, o play recomeça do início
            first, last = self._loop or (0, self.slider.maximum())
            if self.slider.value() >= last:
                self.slider.setValue(first)
        (self.timer.start if on and self.sim else self.timer.stop)()

    def _sound_toggled(self, on: bool) -> None:
        if not on:
            self.audio.stop()
        self._clock = None          # o play recomeça do tick atual, já com ou sem som

    def _play_tick(self) -> None:
        """Play na velocidade escolhida (1x = 60 ticks por segundo), com o som junto em 1x. Se o
        desenho de um tick atrasa, a imagem e o som esperam e recomeçam juntos dali."""
        if not self.sim:
            return
        cur = self.slider.value()
        first, last = self._loop or (0, self.slider.maximum())
        speed = self.speed.currentData() or 1.0
        if cur != self._play_pos:           # a linha do tempo foi mexida à mão
            self._clock = None
            self.audio.stop()
        now = time.monotonic()
        if self._clock is None:
            if not first <= cur <= last:    # fora do trecho repetido: entra nele
                cur = first
                self.slider.setValue(cur)
            self._play_pos = cur
            missing = [j for j in range(cur, min(last + 1, cur + 6))
                       if self._key(j) not in self.cache and self._key(j) not in self._failed]
            if missing:                     # espera alguns ticks prontos para não engasgar
                if self._want is None:
                    self._want = self._key(missing[0])
                return self._kick()
            self._clock = (cur, now)
            if self.sound.isChecked() and speed == 1.0:
                self.audio.play(self.sim, self.sim.track[min(cur, len(self.sim.track) - 1)]["t"])
            return
        i0, t0 = self._clock
        target = i0 + int((now - t0) * 60 * speed)
        if target > last:                   # fim: repete ou para
            self._clock = None
            self.audio.stop()
            if self._repeat:
                self._play_pos = first
                self.slider.setValue(first)
            else:
                self.slider.setValue(last)      # com atraso o relógio passa do fim: mostra o último tick
                self.play_action.setChecked(False)
            return
        if target == cur:
            return
        if self._key(target) not in self.cache and self._key(target) not in self._failed:
            # o desenho ficou para trás: segura e recomeça do próximo tick quando ele chegar
            self._clock = None
            self.audio.stop()
            target = min(cur + 1, last)
        self._play_pos = target
        self.slider.setValue(target)

    # ------------------------------------------------------------ salvar

    def _save_in_background(self, what: str, fn) -> None:
        self.ctx.status.emit(tr("Saving {0}…").format(what))
        job = Job(fn)
        job.signals.done.connect(self._saved)
        job.signals.failed.connect(self._save_failed)
        start(job, self)

    def _saved(self, msg: str) -> None:
        self.ctx.status.emit(msg)

    def _save_failed(self, msg: str) -> None:
        self.ctx.status.emit(tr("Could not save: ") + msg.splitlines()[0])

    def save_gif(self) -> None:
        if not self.sim:
            return
        from ...tools import simulate
        path, _ = QFileDialog.getSaveFileName(self, tr("Save simulation GIF"),
                                              f"state_{self.current_state()}.gif", "GIF (*.gif)")
        if path:
            sim, zoom, hide, bg = self.sim, self.zoom.value(), self._hide(), self._bg()

            def work():                 # o GIF sai com as camadas e o fundo da prévia
                simulate.animation(sim, path, zoom=zoom, boxes=True, hide=hide, background=bg, hurtboxes=True)
                return tr("GIF saved: {0}").format(path)
            self._save_in_background(tr("the GIF"), work)

    def save_sheets(self) -> None:
        if not self.sim:
            return
        from ...tools import simulate
        d = QFileDialog.getExistingDirectory(self, tr("Folder for the tick-by-tick sheets"))
        if d:
            sim, name, zoom, boxes = self.sim, f"state_{self.current_state()}", self.zoom.value(), \
                self.layer_checks["clsn1"].isChecked() or self.layer_checks["clsn2"].isChecked()

            def work():
                paths = simulate.tick_pages(sim, d, name, zoom=zoom, boxes=boxes)
                return tr("{0} sheet(s) in {1}").format(len(paths), d)
            self._save_in_background(tr("the sheets"), work)
