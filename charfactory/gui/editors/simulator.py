"""Simulador tick a tick, sem abrir o MUGEN.

A simulação é a de `tools.simulate`: roda um estado com helpers, explods, pausas e um P2
parado, defendendo, agachado ou no ar, sem abrir o MUGEN. Cada tick é desenhado como na folha
de revisão; o play toca os sons do golpe no tempo certo e a barra de frame data mostra preparo,
ativo, recuperação e a vantagem.
"""
from __future__ import annotations

import os
import re
import tempfile
import time
import traceback

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)

from ..canvas import pil_to_qimage
from ..context import AppContext
from ..widgets import DocStrip, Section, SidePanel, compact, form_row, hint_label, tool_bar
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


def start(job: Job, owner: QObject) -> None:
    # mantém vivo até terminar (só some da lista depois de rodar). O resultado deve ir para
    # métodos de um QObject: assim chega pela fila da interface; uma lambda solta rodaria na
    # thread do trabalho
    owner._jobs = [j for j in getattr(owner, "_jobs", []) if not j.finished] + [job]
    _pool().start(job)


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


# cor de cada tipo de marcador da linha do tempo
MARK_COLORS = {"acerto": "#f85149", "defesa": "#388bfd", "som": "#d29922", "explod": "#a371f7",
               "helper": "#db61a2", "projectile": "#db61a2"}


class MoveTimeline(QWidget):
    """Linha do tempo do golpe: uma faixa por tipo de evento (quadros, frame data, acertos,
    sons, efeitos, tela) sobre os ticks da simulação.

    Clicar leva ao tick; duplo clique num evento abre o controlador no código; arrastar um
    evento que dispara por `Time = N` pede para mudar o N (sinal `moved`). Roda do mouse anda
    na linha, Ctrl+roda dá zoom."""
    seek = Signal(int)
    open_item = Signal(object)
    moved = Signal(object, int)

    LABEL_W, RULER_H, ROW_H = 92, 16, 18

    def __init__(self):
        super().__init__()
        from ...tools.movetimeline import ROWS
        self.rows_def = ROWS
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
                tip += tr("\nDrag to change the tick (rewrites the Time = N)")
            if it is not None and it.get("ci") is not None:
                tip += tr("\nDouble-click opens it in the code")
            if tip:
                QToolTip.showText(e.globalPosition().toPoint(), tip, self)
            else:
                QToolTip.hideText()
            self.setCursor(Qt.CursorShape.SizeHorCursor if it is not None and self.can_drag(it)
                           else Qt.CursorShape.PointingHandCursor if it is not None else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):  # noqa: N802
        d, self._drag = self._drag, None
        if d is not None:
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
        # frame data
        y = self.RULER_H + rows["fases"] * self.ROW_H + 3
        gap = 1 if self.scale >= 4 else 0
        cols = {k: QColor(c) for k, (_, c) in PHASES.items() if c}
        for i, ph in enumerate(self.data["phases"]):
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
    um arquivo por vez; dois arquivos alternados evitam regravar o que ainda está tocando."""

    def __init__(self):
        self._n = 0
        self.playing = False

    def play(self, sim, start_tick: int) -> None:
        self.stop()
        try:
            import winsound
            from ...tools import simulate
            wav = simulate.sound_track(sim, start_tick)
            if not wav:
                return
            self._n ^= 1
            path = os.path.join(tempfile.gettempdir(), f"charfactory_sim_{os.getpid()}_{self._n}.wav")
            with open(path, "wb") as fh:
                fh.write(wav)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.playing = True
        except (ImportError, OSError, RuntimeError):
            pass

    def stop(self) -> None:
        if not self.playing:
            return
        self.playing = False
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except ImportError:
            pass


class TickView(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 240)
        self._pix: QPixmap | None = None

    def set_image(self, img) -> None:
        self._pix = QPixmap.fromImage(pil_to_qimage(img)) if img is not None else None
        self._fit()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    placeholder = (tr("No simulation yet"), tr("Pick a state in the panel and click Simulate (F9 opens this page)."))

    def paintEvent(self, e):  # noqa: N802 — sem imagem: um aviso no meio em vez do vazio
        if self._pix is not None:
            return super().paintEvent(e)
        from PySide6.QtGui import QPainter
        p = QPainter(self)
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

        self.state = QComboBox()
        self.state.setEditable(True)
        self.state.setToolTip(tr("State to simulate (the .cmd moves appear in the list)"))
        self.dist = QDoubleSpinBox()
        self.dist.setRange(0, 400)
        self.dist.setValue(50)
        self.dist.setSuffix(" px")
        self.max_ticks = QSpinBox()
        self.max_ticks.setRange(30, 3000)
        self.max_ticks.setValue(900)
        self.zoom = QSpinBox()
        self.zoom.setRange(1, 4)
        self.zoom.setSuffix("x")
        self.zoom.setValue(2)
        self.zoom.valueChanged.connect(lambda _: self.show_tick())
        self.boxes = QCheckBox(tr("Clsn boxes"))
        self.boxes.setChecked(True)
        self.p2 = QCheckBox(tr("Show P2"))
        self.p2.setChecked(True)
        for cb in (self.boxes, self.p2):
            cb.toggled.connect(lambda _: self.show_tick())
        self.chain = QLineEdit()
        self.chain.setPlaceholderText(tr("e.g. 200 210 1000"))
        self.chain.setToolTip(tr("Move chain (combo): each state comes in as soon as it can — cancels if it touched and there is a cancel, links when P1 can act again. Empty = only the state"))
        self.chain.returnPressed.connect(self.run)
        self.compare = QComboBox()
        self.compare.setToolTip(tr("Also simulates another version of the character (the one saved on disk or a backup) and shows both side by side, tick by tick"))
        self.sim_b = None
        self.p2_mode = QComboBox()
        for label, *_ in P2_MODES:
            self.p2_mode.addItem(label)
        self.p2_mode.setToolTip(tr("How P2 waits for the move: the guard follows the HitDef guardflag"))
        self.sound = QCheckBox(tr("Sound"))
        self.sound.setChecked(True)
        self.sound.setToolTip(tr("Plays the move sounds (PlaySnd, hit and guard) during playback"))
        self.sound.toggled.connect(self._sound_toggled)
        self.audio = SoundPlayer()
        self._clock: tuple[int, float] | None = None   # (tick, hora) em que o play (re)começou
        self._play_pos = -1
        run = QPushButton(tr("Simulate"))
        run.setProperty("primary", True)
        run.clicked.connect(self.run)
        self.run_btn = run
        compact(self.state, self.dist, self.max_ticks, self.zoom, self.p2_mode, self.compare)
        run.setMinimumHeight(32)
        self.player = tool_bar([
            ("step_back", tr("Previous tick (,)"), lambda: self.step(-1)),
            ("play", tr("Play / pause (space)"), self.toggle_play, True),
            ("step_fwd", tr("Next tick (.)"), lambda: self.step(1)),
            None,
            ("export", tr("Save GIF…"), self.save_gif),
            ("sheet_save", tr("Save tick-by-tick sheets…"), self.save_sheets),
        ])
        self.play_action = self.player.actions_by_tip[tr("Play / pause (space)")]
        self._set_player(False)
        self.info = QLabel(tr("Pick a state and click Simulate."))
        self.info.setWordWrap(True)
        self.info.setObjectName("dim")
        self.events = QListWidget()
        self.events.itemActivated.connect(self.on_event)
        self.events.itemClicked.connect(self.on_event)
        setup = Section(tr("Simulation"), "sim.setup")
        setup.add(form_row(tr("State"), self.state), form_row(tr("Chain"), self.chain), form_row("P2", self.p2_mode),
                  form_row(tr("Distance"), self.dist), form_row(tr("Max ticks"), self.max_ticks),
                  form_row(tr("Compare"), self.compare), run)
        view = Section(tr("Display"), "sim.view")
        view.add(form_row(tr("Zoom"), self.zoom), self.boxes, self.p2, self.sound, self.player)
        events = Section(tr("Events and problems"), "sim.events")
        events.add(hint_label(tr("Click an event to go to its tick.")), self.events)
        self.events.setMinimumHeight(180)
        content = QWidget()
        lv = QVBoxLayout(content)
        lv.addWidget(setup)
        lv.addWidget(self.info)
        lv.addWidget(view)
        lv.addWidget(events, 1)
        self.left = SidePanel(tr("Simulator"), content, header=False)

        self.file_tab = DocStrip()
        self.view = TickView()
        self.view_b = TickView()
        self.view_b.placeholder = (tr("Version B"), "")
        self.view_b.hide()
        self.ab_label = QLabel()
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
        self.fd_label = QLabel("")
        self.fd_label.setObjectName("dim")
        self.fd_label.setTextFormat(Qt.TextFormat.RichText)
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
        rows.addWidget(self.slider)
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
        ctx.characterChanged.connect(self.load)
        ctx.modelChanged.connect(self._model_changed)

    def _set_player(self, on: bool) -> None:
        """Tocar, avançar e salvar só fazem sentido com uma simulação pronta."""
        for act in self.player.actions_by_tip.values():
            act.setEnabled(on)

    def _model_changed(self, _part) -> None:
        if self.sim:
            self.sim = None
            self._gen += 1
            self.play_action.setChecked(False)
            self._set_player(False)
            if self.isVisible() and self.run_btn.isEnabled():
                # mudou à vista (desfazer, arrastar na linha do tempo): simula de novo no mesmo tick
                self._resume_at = self.slider.value()
                QTimer.singleShot(0, self.run)
            else:
                self.timeline.set_data(None)
                self.fd_label.setText(tr("The character changed: simulate again."))

    def hideEvent(self, e):  # noqa: N802 — trocar de página para o play e o som
        self.play_action.setChecked(False)
        super().hideEvent(e)

    def load(self) -> None:
        self.sim = None
        self._gen += 1
        self._want = None
        self._drop_pending_render()
        self.play_action.setChecked(False)
        self._set_player(False)
        self.run_btn.setEnabled(True)
        self.cache.clear()
        self.events.clear()
        self.view.set_image(None)
        self.timeline.set_data(None)
        self.fd_label.setText("")
        self.sim_b = None
        self.view_b.set_image(None)
        self.fill_compare()
        c = self.ctx.char
        if not c:
            return
        self.file_tab.set_file(tr("{0} · simulation").format(c.name), "simulate")
        self.state.clear()
        try:
            from ...tools import simulate
            moves = simulate.move_states(c)
        except Exception:  # noqa: BLE001
            moves = []
        sds = c.all_statedefs(include_common=False)
        for n in sorted(set(moves) | {n for n in sds if n >= 0}):
            self.state.addItem(f"{n}" + (tr("  ★ move") if n in moves else ""), n)
        if moves:   # começa no primeiro golpe, não no estado de vitória
            self.state.setCurrentIndex(max(0, self.state.findData(min(moves))))
        self.info.setText(tr("Pick a state and click Simulate."))
        # sem simulação a linha do tempo fica desligada (antes parecia pronta para arrastar)
        self.slider.setEnabled(False)
        self.tick_label.setText(tr("No simulation"))

    def fill_compare(self) -> None:
        """Versões para o A/B: a salva no disco e os backups (o mais novo primeiro)."""
        cur = self.compare.currentData()
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

    def showEvent(self, e):  # noqa: N802 — um backup novo aparece na lista
        self.fill_compare()
        super().showEvent(e)

    def keyPressEvent(self, e):  # noqa: N802
        k = e.key()
        if k == Qt.Key.Key_Space:
            self.play_action.toggle()
        elif k in (Qt.Key.Key_Comma, Qt.Key.Key_Left):
            self.step(-1)
        elif k in (Qt.Key.Key_Period, Qt.Key.Key_Right):
            self.step(1)
        else:
            super().keyPressEvent(e)

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
            # a auditoria (summary) mede efeitos sprite a sprite: segundos num golpe cheio de
            # faíscas, por isso vai junto da simulação e não na thread da interface
            sim = simulate.simulate(c, n, p2_dist=dist, max_ticks=mx, p2_pose=pose, p2_guard=guard,
                                    chain=chain)
            box = simulate._window(sim) if sim.track else None
            simb = None
            if other:                   # A/B: a outra versão com a mesma simulação e a mesma câmera
                from ... import project
                simb = simulate.simulate(project.load(other, mugen), n, p2_dist=dist, max_ticks=mx,
                                         p2_pose=pose, p2_guard=guard, chain=chain)
                if simb.track and box:
                    bb = simulate._window(simb)
                    box = (min(box[0], bb[0]), max(box[1], bb[1]), min(box[2], bb[2]), max(box[3], bb[3]))
            return gen, sim, simulate.summary(sim), box, simb

        job = Job(work)
        job.signals.done.connect(self.on_done)
        job.signals.failed.connect(self._sim_failed)
        start(job, self)

    def _sim_failed(self, msg: str) -> None:
        self.run_btn.setEnabled(True)
        self.info.setText(tr("The simulation failed:\n") + msg)

    def on_done(self, result) -> None:
        gen, sim, summ, box, *rest = result
        if gen != self._gen:
            return          # outra simulação foi pedida depois desta
        self.sim_b = rest[0] if rest else None
        self.run_btn.setEnabled(True)
        self.sim = sim
        self.cache.clear()
        self._want = None
        self.view_box = box
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, len(sim.track) - 1, len(self.sim_b.track) - 1 if self.sim_b else 0))
        # depois de arrastar um evento a simulação nova volta ao mesmo tick
        self.slider.setValue(min(getattr(self, "_resume_at", 0), self.slider.maximum()))
        self._resume_at = 0
        self.slider.blockSignals(False)
        self.slider.setEnabled(bool(sim.track))
        self._set_player(bool(sim.track))
        guards = sum(1 for e in sim.events if e["kind"] == "defesa")
        self.info.setText(tr("{0} ticks · {1} hit(s)").format(summ['ticks'], summ['hits'])
                          + (tr(" · {0} guard(s)").format(guards) if guards else "")
                          + (tr(" · not simulated: {0}").format(', '.join(sorted(set(map(str, summ['unsupported']))))[:120])
                             if summ["unsupported"] else ""))
        from ...tools import movetimeline, simulate
        tl = movetimeline.build(sim)
        self.timeline.set_data(tl)
        self.fd_label.setText(self._chain_text(simulate.chain_report(sim)) if sim.chain
                              else self._fd_text(tl["frame_data"]))
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
        for p in summ["problems"]:
            it = QListWidgetItem(f"⚠ {p['kind']}: {p['message']}")
            self.events.addItem(it)
        for ev in sim.events:
            snd = ev.get("snd")
            if ev["kind"] == "ctrl" and ev.get("value") == "playsnd" and snd:
                txt = tr("sound {0}{1},{2}").format('F' if snd[2] else '', snd[0], snd[1])
            elif ev["kind"] in ("estado", "acerto", "defesa", "pausa", "helper", "explod"):
                txt = {"estado": tr("state {0}").format(ev.get('value')), "acerto": tr("HIT {0}").format(ev.get('label', '')),
                       "defesa": tr("GUARD {0}").format(ev.get('label', '')),
                       "pausa": f"pausa {ev.get('value')}", "helper": f"helper {ev.get('value')}",
                       "explod": f"explod anim {ev.get('value')}"}[ev["kind"]]
            else:
                continue
            it = QListWidgetItem(f"t{ev['t']:>4}  {ev.get('who', '')}: {txt}")
            it.setData(Qt.ItemDataRole.UserRole, ev["t"])
            self.events.addItem(it)
        self.show_tick()

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
    def _fd_text(fd: dict) -> str:
        """Resumo do frame data com a legenda das cores da barra."""
        def chip(key, value, extra=""):
            name, col = PHASES[key]
            return f"<span style='color:{col}'>■</span> {name} <b>{value}</b>{extra}"
        if fd["startup"] is None:
            return tr("No active Clsn1: nothing to measure")
        pause = tr(" + {0} in the pause").format(fd['startup_pause']) if fd.get("startup_pause") else ""
        parts = [chip("preparo", fd["startup"], pause), chip("ativo", fd["active"]),
                 chip("recuperacao", fd["recovery"]), tr("Total <b>{0}</b>").format(fd['total'])]
        adv = fd["advantage"]
        where = {"acerto": tr("on hit"), "defesa": tr("on guard")}.get(fd["outcome"])
        if where is None:
            parts.append(tr("missed P2"))
        elif fd["knockdown"]:
            parts.append(tr("{0}: knocks down").format(where))
        elif adv is not None:
            parts.append(f"{where} <b>{adv:+d}</b>")
        return " · ".join(parts)

    # ------------------------------------------------------------ linha do tempo → código

    def _controller(self, item):
        from ...tools import movetimeline
        if not self.ctx.char or item.get("ci") is None or item.get("state") is None:
            return None, None
        return movetimeline.controller(self.ctx.char, item["state"], item["ci"])

    def _can_drag(self, item) -> bool:
        """Só o que dispara por um simples `Time = N` (som, efeito, tela): o acerto depende das
        caixas, e o som do acerto vem do HitDef."""
        from ...tools import movetimeline
        if item.get("kind") in ("quadro", "pausa", "acerto", "defesa") or item.get("hitsound"):
            return False
        ctrl, _ = self._controller(item)
        return ctrl is not None and movetimeline.time_trigger(ctrl) is not None

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
        delta = movetimeline.state_ticks(self.sim, item["i0"], to, item.get("who") or "P1")
        if not delta:
            return self.ctx.status.emit(tr("The event fell in a pause: the state Time does not change there"))
        try:
            r = movetimeline.shift_time(self.ctx.char, item["state"], item["ci"], delta)
        except ValueError as e:
            return self.ctx.status.emit(str(e))
        # grava, entra no desfazer e avisa as páginas de código; a mudança simula de novo
        self.ctx.changed(r["part"])
        self.ctx.status.emit(f"[{r['controller']}]: Time = {r['old']} → {r['new']}")

    def on_event(self, item) -> None:
        t = item.data(Qt.ItemDataRole.UserRole)
        if t is not None and self.sim:
            idx = next((i for i, r in enumerate(self.sim.track) if r["t"] >= t), len(self.sim.track) - 1)
            self.slider.setValue(idx)

    # ------------------------------------------------------------ desenho dos ticks

    def _key(self, i: int) -> tuple:
        return (i, self.zoom.value(), self.boxes.isChecked(), self.p2.isChecked())

    def show_tick(self) -> None:
        if not self.sim or not self.sim.track or self.view_box is None:
            return
        i = self.slider.value()
        rec = self.sim.track[min(i, len(self.sim.track) - 1)]
        self.timeline.set_current(i)
        p1 = rec["ents"][0] if rec["ents"] else None
        where = tr(" · state {0} anim {1} frame {2}").format(p1['state'], p1['anim'], p1['elem']) if p1 else ""
        self.tick_label.setText(f"tick {rec['t']} / {self.sim.track[-1]['t']}{where}")
        img = self.cache.get(self._key(i))
        if img is not None:
            self._show(img)
            self._prefetch()
        else:
            # um tick com várias faíscas giradas leva ~0,5 s: desenha fora da interface e mostra o
            # último pedido (arrastar a linha do tempo pula os do meio em vez de enfileirar)
            self._want = self._key(i)
            self._kick()

    def _kick(self) -> None:
        if self._rendering or self._want is None or not self.sim:
            return
        key, self._want = self._want, None
        if key in self.cache:
            return self.show_tick() if key == self._key(self.slider.value()) else self._prefetch()
        from ...tools import simulate
        sim, simb, box, gen = self.sim, self.sim_b, self.view_box, self._gen
        i, zoom, boxes, p2 = key
        self._rendering = True

        def draw(s):                    # a versão mais curta fica no último tick dela
            return simulate.render_tick(s, s.track[min(i, len(s.track) - 1)], box, zoom=zoom,
                                        boxes=boxes, show_p2=p2) if s and s.track else None
        job = Job(lambda: (gen, key, (draw(sim), draw(simb))))
        job.signals.done.connect(self._rendered)
        job.signals.failed.connect(self._render_failed)
        self._render_job = job
        start(job, self)

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
        while len(self.cache) >= 300:           # tira o mais antigo, não o cache todo
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
        for j in range(i + 1, min(self.slider.maximum() + 1, i + 13)):
            if self._key(j) not in self.cache:
                self._want = self._key(j)
                return self._kick()

    def step(self, d: int, wrap: bool = False) -> None:
        if not self.sim:
            return
        if wrap and self._key(self.slider.value()) not in self.cache:
            return          # tocando: espera o tick atual aparecer antes de avançar
        v = self.slider.value() + d
        if v > self.slider.maximum():
            v = 0 if wrap else self.slider.maximum()
        self.slider.setValue(max(0, v))

    def toggle_play(self, on: bool) -> None:
        self._clock = None
        self.audio.stop()
        (self.timer.start if on and self.sim else self.timer.stop)()

    def _sound_toggled(self, on: bool) -> None:
        if not on:
            self.audio.stop()
        self._clock = None          # o play recomeça do tick atual, já com ou sem som

    def _play_tick(self) -> None:
        """Play em tempo real (60 ticks por segundo) com o som junto. Se o desenho de um tick
        atrasa, a imagem e o som esperam e recomeçam juntos dali."""
        if not self.sim:
            return
        cur, last = self.slider.value(), self.slider.maximum()
        if cur != self._play_pos:           # a linha do tempo foi mexida à mão
            self._clock = None
            self.audio.stop()
        now = time.monotonic()
        if self._clock is None:
            self._play_pos = cur
            missing = [j for j in range(cur, min(last + 1, cur + 6)) if self._key(j) not in self.cache]
            if missing:                     # espera alguns ticks prontos para não engasgar
                if self._want is None:
                    self._want = self._key(missing[0])
                return self._kick()
            self._clock = (cur, now)
            if self.sound.isChecked():
                self.audio.play(self.sim, self.sim.track[min(cur, len(self.sim.track) - 1)]["t"])
            return
        i0, t0 = self._clock
        target = i0 + int((now - t0) * 60)
        if target > last:                   # fim: volta ao começo
            self._clock = None
            self.audio.stop()
            self._play_pos = 0
            self.slider.setValue(0)
            return
        if target == cur:
            return
        if self._key(target) not in self.cache:
            # o desenho ficou para trás: segura e recomeça do próximo tick quando ele chegar
            self._clock = None
            self.audio.stop()
            target = cur + 1
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
            sim, zoom, boxes = self.sim, self.zoom.value(), self.boxes.isChecked()

            def work():
                simulate.animation(sim, path, zoom=zoom, boxes=boxes)
                return tr("GIF saved: {0}").format(path)
            self._save_in_background(tr("the GIF"), work)

    def save_sheets(self) -> None:
        if not self.sim:
            return
        from ...tools import simulate
        d = QFileDialog.getExistingDirectory(self, tr("Folder for the tick-by-tick sheets"))
        if d:
            sim, name, zoom, boxes = self.sim, f"state_{self.current_state()}", self.zoom.value(), \
                self.boxes.isChecked()

            def work():
                paths = simulate.tick_pages(sim, d, name, zoom=zoom, boxes=boxes)
                return tr("{0} sheet(s) in {1}").format(len(paths), d)
            self._save_in_background(tr("the sheets"), work)
