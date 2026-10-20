"""Prévia rápida de uma ação do .AIR, tocando em loop como um GIF (usada ao lado da árvore de
Estados: mostra a anim do Statedef ou do ChangeAnim onde está o cursor)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..tools import render
from .canvas import pil_to_qimage
from .context import AppContext
from .widgets import tool_button
from ..i18n import tr

TICK_MS = 1000 / 60


class _FrameView(QWidget):
    """Desenha o quadro atual ajustado ao espaço, sem suavizar (pixel art), com a linha do chão."""

    def __init__(self, preview: "AnimPreview"):
        super().__init__()
        self.preview = preview
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, e):  # noqa: N802
        pv = self.preview
        p = QPainter(self)
        ui = pv.ctx.theme.ui if pv.ctx.theme else {}
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(ui.get("border", "#444")), 1))
        p.setBrush(QColor(ui.get("window", "#202020")))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        if not pv.frames:
            p.setPen(QColor(ui.get("text_dim", "#888")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, pv.empty_text)
            return
        pm = pv.frames[pv.index][0]
        w, h = pm.width(), pm.height()
        area = self.rect().adjusted(8, 8, -8, -8)
        k = min(area.width() / max(w, 1), area.height() / max(h, 1), 3.0)
        dw, dh = w * k, h * k
        x0 = area.x() + (area.width() - dw) / 2
        y0 = area.y() + (area.height() - dh) / 2
        ax, ay = pv.axis
        p.setPen(QPen(QColor(ui.get("text_dim", "#888")), 1, Qt.PenStyle.DashLine))
        gy = y0 + ay * k
        p.drawLine(QPointF(area.x(), gy), QPointF(area.right(), gy))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawPixmap(QRectF(x0, y0, dw, dh), pm, QRectF(0, 0, w, h))


class AnimPreview(QWidget):
    """Toca a ação pedida em loop (tempo real de cada quadro; -1 segura 1 s e recomeça)."""
    openRequested = Signal(int)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.setObjectName("plain")
        self.ctx = ctx
        self.number: int | None = None
        self.frames: list[tuple[QPixmap, int]] = []
        self.loopstart = 0
        self.index = 0
        self.axis = (0, 0)
        self.empty_text = tr("No animation")
        self.source = None            # outro char; None = o aberto
        self.background = (0, 0, 0, 0)  # efeito aditivo some no transparente: a biblioteca usa preto
        self._cache: dict = {}
        self._pending: int | None = None
        self.timer = QTimer(self, singleShot=True, timeout=self._next)
        self._load_timer = QTimer(self, singleShot=True, interval=150, timeout=self._load)

        self.view = _FrameView(self)
        self.title = QLabel()
        self.title.setObjectName("fieldLabel")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        # texto longo não alarga o painel: é cortado (completo na dica)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.play_btn = tool_button("pause", tr("Pause / play the preview"), self.toggle_play, size=16)
        self.open_btn = tool_button("external", tr("Open this action in the animation editor"),
                                    lambda: self.number is not None and self.openRequested.emit(self.number), size=16)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(2)
        head.addWidget(self.title, 1)
        head.addWidget(self.play_btn)
        head.addWidget(self.open_btn)
        self.info = QLabel()
        self.info.setObjectName("hint")
        self.info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.insertWidget(1, self.info)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(head)
        lay.addWidget(self.view, 1)
        self.playing = True
        ctx.characterChanged.connect(self.reset)
        ctx.modelChanged.connect(self._on_model)
        self._set_header()

    # ------------------------------------------------------------ dados

    def char(self):
        return self.source if self.source is not None else self.ctx.char

    def reset(self) -> None:
        self._cache.clear()
        n, self.number = self.number, None
        if n is not None:
            self.show_anim(n)

    def _on_model(self, part: str) -> None:
        if part in ("anims", "anims_inline", "sprites", "def"):
            self.reset()

    def show_anim(self, number: int | None) -> None:
        """Pede a prévia da ação (com um pequeno atraso: andar com o cursor não renderiza tudo)."""
        if number == self.number and number is not None:
            return
        self._pending = number
        self._load_timer.start()

    def _load(self) -> None:
        n = self._pending
        self.number = n
        self.timer.stop()
        self.frames, self.index = [], 0
        char = self.char()
        if n is None or char is None:
            self.empty_text = tr("No animation in this state")
            self._set_header()
            self.view.update()
            return
        key = (id(char), n)
        if key not in self._cache:
            self._cache[key] = self._render(char, n, self.background)
        self.frames, self.loopstart, self.axis, self.empty_text = self._cache[key]
        self._set_header()
        self.view.update()
        if self.frames and self.playing and self.isVisible():
            self._schedule()

    @staticmethod
    def _render(char, n: int, background=(0, 0, 0, 0)):
        act = char.anims.get(n) if char.anims else None
        if act is None:
            return [], 0, (0, 0), tr("Action {0} does not exist in the .AIR").format(n)
        if not act.frames:
            return [], 0, (0, 0), tr("Action {0} has no frames").format(n)
        try:
            b = render._bounds(char, act, None, False)
            frames = []
            for i, f in enumerate(act.frames):
                img = render.render_frame(char, act, i, None, False, 1, background=background, bounds=b,
                                          axis=False)
                frames.append((QPixmap.fromImage(pil_to_qimage(img)), f.time))
        except Exception as e:  # noqa: BLE001 — sprite faltando ou SFF ilegível: só avisa
            return [], 0, (0, 0), tr("Could not draw action {0}: {1}").format(n, e)
        return frames, min(act.loopstart, len(frames) - 1), (-b[0], -b[1]), ""

    def _set_header(self) -> None:
        if self.number is None:
            self.title.setText(tr("Animation"))
            self.info.setText("")
            self.open_btn.setEnabled(False)
            return
        char = self.char()
        name = ""
        try:
            name = char.anims.name_of(self.number) if char else ""
        except Exception:  # noqa: BLE001
            pass
        self.title.setText(tr("<b>Action {0}</b>").format(self.number) + (f" · {name[:40]}" if name else ""))
        self.title.setToolTip(tr("Action {0}").format(self.number) + (f" · {name}" if name else ""))
        self.open_btn.setEnabled(True)
        if self.frames:
            ticks = [t for _, t in self.frames]
            total = "∞" if any(t < 0 for t in ticks) else f"{sum(ticks)}t"
            self.info.setText(tr("{0} fr · {1}").format(len(self.frames), total))
            self.info.setToolTip(tr("{0} frames · ").format(len(self.frames))
                                 + (tr("infinite time (-1)") if total == "∞" else f"{sum(ticks)} ticks"))
        else:
            self.info.setText("")

    # ------------------------------------------------------------ tocar

    def _schedule(self) -> None:
        t = self.frames[self.index][1]
        self.timer.start(1000 if t < 0 else max(1, round(max(t, 1) * TICK_MS)))

    def _next(self) -> None:
        if not self.frames:
            return
        t = self.frames[self.index][1]
        if t < 0 or self.index + 1 >= len(self.frames):
            self.index = 0 if t < 0 else self.loopstart
        else:
            self.index += 1
        self.view.update()
        if self.playing and self.isVisible():
            self._schedule()

    def toggle_play(self) -> None:
        self.playing = not self.playing
        from .icons import icon
        self.play_btn.setIcon(icon("pause" if self.playing else "play"))
        if self.playing and self.frames:
            self._schedule()
        else:
            self.timer.stop()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if self._pending != self.number:
            self._load()
        elif self.frames and self.playing:
            self._schedule()

    def hideEvent(self, e):  # noqa: N802
        super().hideEvent(e)
        self.timer.stop()
