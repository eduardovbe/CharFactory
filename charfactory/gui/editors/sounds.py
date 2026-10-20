"""Editor de sons: painel 'Sons' | forma de onda com régua de ticks.

Na onda: arrastar seleciona, clique põe o cursor (onde Colar insere), roda do mouse dá zoom
em volta do ponteiro e Shift+roda rola. As edições agem sobre a seleção (ou o som inteiro).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
from PySide6.QtCore import QLine, QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QInputDialog, QLabel, QMessageBox, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from ...formats import snd as sndfmt
from ...tools import edit, ops
from ..context import AppContext
from ..widgets import MORE, DocStrip, GroupIndex, Navigator, Section, SidePanel, compact, form_row, hint_label, tool_bar
from .sprites import ask_pair
from ...i18n import tr

AUDIO_FILTER = tr("Audio (*.wav *.ogg *.mp3 *.flac *.opus *.m4a);;WAV (*.wav)")


class Waveform(QWidget):
    RULER = 28
    selectionChanged = Signal(int, int)
    cursorMoved = Signal(int)

    def __init__(self):
        super().__init__()
        self.samples = np.zeros(0)
        self.rate = 1
        self.theme = None
        self.sel = (0, 0)
        self.cursor = 0
        self.view = (0, 0)          # amostras visíveis [início, fim)
        self._press = None
        self.setMinimumHeight(160)
        self.setMouseTracking(True)

    def set_wav(self, wav: bytes | None, keep_view: bool = False) -> None:
        old = len(self.samples)
        self.samples = np.zeros(0)
        if wav:
            try:
                params, data = edit._wav_arrays(wav)
                full = {1: 128, 2: 32768, 4: 2 ** 31}[params.sampwidth]
                self.samples = data.mean(axis=1) / full
                self.rate = params.framerate
            except Exception:  # noqa: BLE001 — WAV não-PCM
                pass
        n = len(self.samples)
        if not keep_view or old != n or self.view[1] <= self.view[0]:
            self.view = (0, n)
        self.view = (min(self.view[0], n), min(self.view[1], n))
        self.cursor = min(self.cursor, n)
        self.update()

    # ------------------------------------------------------------ coordenadas

    def x_of(self, i: float) -> float:
        a, b = self.view
        return (i - a) / max(1, b - a) * self.width()

    def sample_at(self, x: float) -> int:
        a, b = self.view
        return int(max(0, min(len(self.samples), a + x / max(1, self.width()) * (b - a))))

    def zoom(self, factor: float, around: int | None = None) -> None:
        n = len(self.samples)
        if not n:
            return
        a, b = self.view
        c = around if around is not None else (a + b) // 2
        w = max(32, min(n, int((b - a) / factor)))
        a = max(0, min(n - w, int(c - (c - a) / max(1, b - a) * w)))
        self.view = (a, a + w)
        self.update()

    def zoom_fit(self) -> None:
        self.view = (0, len(self.samples))
        self.update()

    def zoom_selection(self) -> None:
        a, b = sorted(self.sel)
        if b - a >= 32:
            self.view = (a, b)
            self.update()

    def wheelEvent(self, e):  # noqa: N802
        d = e.angleDelta().y()
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            a, b = self.view
            step = int((b - a) * 0.15) * (-1 if d > 0 else 1)
            n = len(self.samples)
            a = max(0, min(n - (b - a), a + step))
            self.view = (a, a + (b - a))
            self.update()
        else:
            self.zoom(1.25 if d > 0 else 0.8, self.sample_at(e.position().x()))

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and len(self.samples):
            i = self.sample_at(e.position().x())
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.sel = (self.sel[0] if self.sel != (0, 0) else self.cursor, i)
                self.selectionChanged.emit(*sorted(self.sel))
            else:
                self._press = i
                self.cursor = i
                self.sel = (i, i)
                self.cursorMoved.emit(i)
            self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._press is not None:
            i = self.sample_at(e.position().x())
            self.sel = (self._press, i)
            self.selectionChanged.emit(*sorted(self.sel))
            self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._press is not None:
            a, b = sorted(self.sel)
            if b - a < 2:
                self.sel = (0, 0)
            self.selectionChanged.emit(*sorted(self.sel))
            self._press = None
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        t = self.theme
        ui = (lambda k, d: QColor(t.ui[k])) if t else (lambda k, d: QColor(d))
        p.fillRect(self.rect(), QColor(t.c("background").hex if t else "#222"))
        dim, border = ui("text_dim", "#999"), ui("border", "#444")
        p.fillRect(0, 0, self.width(), self.RULER, ui("panel", "#2b2d33"))
        p.setPen(border)
        p.drawLine(0, self.RULER, self.width(), self.RULER)
        n = len(self.samples)
        top = self.RULER + 12
        h = max(40, self.height() - top - 12)
        mid = top + h / 2
        if not n:
            p.setPen(dim)
            p.drawText(self.rect().adjusted(0, self.RULER, 0, 0), Qt.AlignmentFlag.AlignCenter,
                       tr("No sound to show (Insert adds a WAV)") if self.isEnabled() else "")
            p.end()
            return
        a, b = self.view
        ticks_per_sample = 60 / self.rate
        first, last = int(a * ticks_per_sample), int(b * ticks_per_sample) + 1
        # rótulo a cada 'every' ticks, com espaço para o número; risquinhos menores entre eles
        px_per_tick = self.width() / max(1e-9, (b - a) * ticks_per_sample)
        every = next((e for e in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) if e * px_per_tick >= 34), 1000)
        small = p.font()
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1))
        p.setFont(small)
        p.setPen(dim)
        for k in range(first - first % every, last + 1, every):
            x = self.x_of(k / ticks_per_sample)
            if x < -1:
                continue
            p.drawLine(int(x), self.RULER - 7, int(x), self.RULER)
            p.drawText(int(x) + 4, 6, 60, self.RULER - 14, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{k}t" if k == first - first % every else str(k))
            if every >= 2 and every * px_per_tick >= 12:
                for j in range(1, 5):
                    xm = self.x_of((k + every * j / 5) / ticks_per_sample)
                    p.drawLine(int(xm), self.RULER - 3, int(xm), self.RULER)
        sa, sb = sorted(self.sel)
        if sb > sa:
            sel = ui("selection", "#405080")
            x0, x1 = self.x_of(sa), self.x_of(sb)
            p.fillRect(int(x0), self.RULER + 1, max(1, int(x1 - x0)), self.height() - self.RULER, sel)
        p.setPen(QPen(border, 1, Qt.PenStyle.DashLine))
        p.drawLine(0, int(mid), self.width(), int(mid))
        col = QColor(t.c("sound_wave").hex if t else "#d0b040")
        p.setPen(QPen(col, 1))
        amp = h / 2 * 0.95
        seg = self.samples[a:b]
        if len(seg) <= self.width():  # poucas amostras: linha contínua
            pts = [QPointF(self.x_of(a + i), mid - v * amp) for i, v in enumerate(seg)]
            if pts:
                p.drawPolyline(QPolygonF(pts))
        else:  # muitas: mínimo/máximo por coluna de pixel (as mesmas fatias do np.array_split)
            w = self.width()
            each, extra = divmod(len(seg), w)
            starts = np.concatenate(([0], np.cumsum([each + 1] * extra + [each] * (w - extra))[:-1]))
            ys_top = (mid - np.maximum.reduceat(seg, starts) * amp).astype(int)
            ys_bot = (mid - np.minimum.reduceat(seg, starts) * amp).astype(int)
            p.drawLines([QLine(i, y0, i, y1) for i, (y0, y1) in enumerate(zip(ys_top.tolist(), ys_bot.tolist()))])
        cx = self.x_of(self.cursor)
        p.setPen(QPen(ui("accent", "#6366f1"), 1.5))
        p.drawLine(int(cx), self.RULER, int(cx), self.height())
        p.end()


class SoundEditor(QWidget):
    title = tr("Sounds")

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._quiet = False
        self._clip: bytes | None = None
        self.tools = tool_bar([
            ("play", tr("Play (Ctrl+P)"), self.play),
            ("stop", tr("Stop (Ctrl+T)"), self.stop),
            None,
            ("new", tr("Add (Ins)"), self.import_wav),
            ("delete", tr("Delete (Ctrl+Del)"), self.delete),
            ("sound_swap", tr("Replace sound (Ctrl+Shift+C)"), self.change_sound),
            MORE,
            ("loop", tr("Play in a loop (Ctrl+Shift+L)"), self.play_loop),
            ("zoom_sel", tr("Zoom to selection"), lambda: self.wave.zoom_selection()),
            ("fit", tr("View the whole sound"), lambda: self.wave.zoom_fit()),
            None,
            ("duplicate", tr("Duplicate"), self.duplicate),
            ("export", tr("Export WAV…"), self.export_wav),
            None,
            ("renumber", tr("Renumber group"), self.rename_group),
            ("delete_group", tr("Delete group"), self.delete_group),
        ])
        self.nav = Navigator()
        self.nav.indexChanged.connect(lambda _: self.refresh())
        self.gi = GroupIndex()
        self.gi.changed.connect(self.on_renumber)
        self.gi.prevGroup.connect(lambda: self.jump_group(-1))
        self.gi.nextGroup.connect(lambda: self.jump_group(1))
        self.info = QLabel()
        self.info.setObjectName("infoChip")
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.sel_a, self.sel_b, self.cur = QSpinBox(), QSpinBox(), QSpinBox()
        for s in (self.sel_a, self.sel_b, self.cur):
            s.setRange(0, 10 ** 8)
            s.setKeyboardTracking(False)
        compact(self.sel_a, self.sel_b, self.cur)
        for s in (self.sel_a, self.sel_b):
            s.valueChanged.connect(self.on_sel)
        self.cur.valueChanged.connect(self.on_cursor_spin)
        self.sel_info = hint_label("")
        self.sel_tools = tool_bar([
            ("select_all", tr("Select all (Ctrl+A)"), self.select_all),
            ("crop", tr("Keep only the selection"), lambda: self.range_op("keep")),
            ("remove_sel", tr("Remove the selection"), lambda: self.range_op("remove")),
            MORE,
            ("select_none", tr("Clear selection"), self.select_none),
            None,
            ("silence", tr("Fill the selection with silence"), lambda: self.range_op("silence")),
            ("fade_in", tr("Fade in on the selection"), lambda: self.range_op("fade_in")),
            ("fade_out", tr("Fade out on the selection"), lambda: self.range_op("fade_out")),
            ("reverse", tr("Reverse the selection (back to front)"), lambda: self.range_op("reverse")),
        ])
        sel_sec = Section(tr("Selection"), "sounds.selection")
        sel_sec.add(hint_label(tr("Drag on the wave to select; a click sets the cursor (where Paste inserts). The mouse wheel zooms.")),
                    form_row(tr("Start"), self.sel_a), form_row(tr("End"), self.sel_b), form_row(tr("Cursor"), self.cur),
                    self.sel_info, self.sel_tools)
        self.volume = QDoubleSpinBox(); self.volume.setRange(0, 4); self.volume.setSingleStep(0.1); self.volume.setValue(1)
        self.volume.setPrefix("× ")
        self.fade_in = QSpinBox(); self.fade_in.setRange(0, 10000); self.fade_in.setSuffix(" ms")
        self.fade_out = QSpinBox(); self.fade_out.setRange(0, 10000); self.fade_out.setSuffix(" ms")
        self.rate = QSpinBox(); self.rate.setRange(0, 96000); self.rate.setSpecialValueText(tr("keep")); self.rate.setSuffix(" Hz")
        compact(self.volume, self.fade_in, self.fade_out, self.rate)
        apply = QPushButton(tr("Apply edit"))
        apply.clicked.connect(self.apply_edit)
        ed_sec = Section(tr("Volume, fade and rate"), "sounds.edit")
        ed_sec.add(hint_label(tr("They act on the whole sound (volume acts only on the selection, if any).")),
                   form_row(tr("Volume"), self.volume), form_row(tr("Fade in"), self.fade_in),
                   form_row(tr("Fade out"), self.fade_out), form_row(tr("Resample"), self.rate), apply)
        content = QWidget()
        lv = QVBoxLayout(content)
        for w in (self.tools, self.nav, self.gi, self.info, sel_sec, ed_sec):
            lv.addWidget(w)
        lv.addStretch()
        self.left = SidePanel(tr("Sounds"), content, header=False)

        self.file_tab = DocStrip()
        self.wave = Waveform()
        self.wave.selectionChanged.connect(self.on_wave_sel)
        self.wave.cursorMoved.connect(lambda i: (self.cur.blockSignals(True), self.cur.setValue(i),
                                                 self.cur.blockSignals(False)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.file_tab)
        lay.addWidget(self.wave, 1)
        self._tmp = os.path.join(tempfile.gettempdir(), "charfactory_preview.wav")
        ctx.characterChanged.connect(self.load)
        ctx.themeChanged.connect(self.on_theme)
        ctx.modelChanged.connect(lambda part: part == "sounds" and QTimer.singleShot(0, lambda: self.load(keep=True)))
        self.on_theme()

    def on_theme(self) -> None:
        self.wave.theme = self.ctx.theme
        self.wave.update()

    @property
    def char(self):
        return self.ctx.char

    def sounds(self):
        return self.char.sounds.sounds if self.char else []

    def sound(self):
        s = self.sounds()
        i = self.nav.index()
        return s[i] if 0 <= i < len(s) else None

    def load(self, keep: bool = False) -> None:
        if not self.char:
            return
        i = self.nav.index() if keep else 0
        p = self.char.path_of("sound")
        self.file_tab.set_file(p.name if p else "—")
        self.nav.set_count(len(self.sounds()))
        self.nav.set_index(min(i, max(0, len(self.sounds()) - 1)))
        self.refresh(keep_view=keep)

    def refresh(self, keep_view: bool = False) -> None:
        s = self.sound()
        self.wave.set_wav(s.wav if s else None, keep_view)
        if not s:
            self.info.setText(tr("No sounds") if self.char else "")
            return
        self._quiet = True
        self.gi.set(s.group, s.number)
        n = len(self.wave.samples)
        for sb in (self.sel_a, self.sel_b, self.cur):
            sb.setMaximum(n)
        if not keep_view:
            self.sel_a.setValue(0)
            self.sel_b.setValue(0)
            self.wave.sel = (0, 0)
        self._quiet = False
        i = s.info()
        if "rate" in i:
            ch = {1: tr("1 (mono)"), 2: tr("2 (stereo)")}.get(i["channels"], str(i["channels"]))
            rows = [(tr("Channels"), ch), (tr("Rate"), f"{i['rate']} Hz"), (tr("Bits"), i["bits"]),
                    (tr("Duration"), f"{i['seconds']} s"), (tr("Ticks"), tr("{0} at 60 Hz").format(round(i['seconds'] * 60)))]
            self.info.setText("<table cellspacing='0' cellpadding='1'>" + "".join(
                f"<tr><td>{k}</td><td style='padding-left:14px'>{v}</td></tr>" for k, v in rows) + "</table>")
        else:
            self.info.setText(tr("Non-PCM format"))
        self._sel_text()

    def _sel_text(self) -> None:
        a, b = sorted(self.wave.sel)
        rate = max(1, self.wave.rate)
        self.sel_info.setText(tr("{0} samples · {1:.0f} ms · {2:.1f} ticks").format(b - a, (b - a) / rate * 1000, (b - a) / rate * 60)
                              if b > a else tr("No selection: edits act on the whole sound"))

    def on_sel(self, *_):
        if not self._quiet:
            self.wave.sel = (self.sel_a.value(), self.sel_b.value())
            self.wave.update()
            self._sel_text()

    def on_wave_sel(self, a: int, b: int) -> None:
        self._quiet = True
        self.sel_a.setValue(a)
        self.sel_b.setValue(b)
        self._quiet = False
        self._sel_text()

    def on_cursor_spin(self, v: int) -> None:
        self.wave.cursor = v
        self.wave.update()

    def selection(self) -> tuple[int, int]:
        a, b = sorted(self.wave.sel)
        return (a, b) if b > a else (0, len(self.wave.samples))

    def select_all(self) -> None:
        self.wave.sel = (0, len(self.wave.samples))
        self.on_wave_sel(*self.wave.sel)
        self.wave.update()

    def select_none(self) -> None:
        self.wave.sel = (0, 0)
        self.on_wave_sel(0, 0)
        self.wave.update()

    def on_renumber(self, g: int, n: int) -> None:
        s = self.sound()
        if self._quiet or not s or (s.group, s.number) == (g, n):
            return
        if self.char.sounds.find(g, n):
            self.ctx.status.emit(tr("Sound {0},{1} already exists").format(g, n))
            self.gi.set(s.group, s.number)
            return
        s.group, s.number = g, n
        self.ctx.record("sounds")
        self.window().update_title()

    def select(self, group: int, number: int) -> bool:
        for i, x in enumerate(self.sounds()):
            if (x.group, x.number) == (group, number):
                self.nav.set_index(i)
                return True
        return False

    def jump_group(self, d: int) -> None:
        s = self.sound()
        if not s:
            return
        groups = sorted({x.group for x in self.sounds()})
        k = groups.index(s.group) + d
        if 0 <= k < len(groups):
            for i, x in enumerate(self.sounds()):
                if x.group == groups[k]:
                    self.nav.set_index(i)
                    return

    # ------------------------------------------------------------ áudio

    def _write_tmp(self, wav: bytes | None = None) -> bool:
        s = self.sound()
        if not s:
            return False
        with open(self._tmp, "wb") as fh:
            fh.write(wav or s.wav)
        return True

    def play(self, loop: bool = False) -> None:
        try:
            import winsound
            s = self.sound()
            a, b = sorted(self.wave.sel)
            wav = edit.samples_wav(self.char, s.group, s.number, a, b) if s and b > a else None
            if self._write_tmp(wav):
                flags = winsound.SND_FILENAME | winsound.SND_ASYNC | (winsound.SND_LOOP if loop else 0)
                winsound.PlaySound(self._tmp, flags)
        except (ImportError, RuntimeError, ValueError) as e:
            self.ctx.status.emit(tr("Could not play: {0}").format(e))

    def play_loop(self) -> None:
        self.play(loop=True)

    def stop(self) -> None:
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except ImportError:
            pass

    # ------------------------------------------------------------ edição

    def range_op(self, op: str) -> None:
        s = self.sound()
        if not s:
            return
        a, b = self.selection()
        try:
            edit.edit_sound_range(self.char, s.group, s.number, a, b, op)
        except ValueError as e:
            self.ctx.status.emit(str(e))
            return
        if op in ("remove", "keep"):
            self.wave.sel = (0, 0)
        self.ctx.changed("sounds")

    def apply_edit(self) -> None:
        s = self.sound()
        if not s:
            return
        a, b = sorted(self.wave.sel)
        if b > a and self.volume.value() != 1:
            edit.edit_sound_range(self.char, s.group, s.number, a, b, "volume", self.volume.value())
            vol = 1.0
        else:
            vol = self.volume.value()
        edit.edit_sound(self.char, s.group, s.number, vol, self.fade_in.value(), self.fade_out.value(),
                        self.rate.value() or None)
        self.ctx.changed("sounds")

    def crop_selection(self) -> None:
        self.range_op("keep")

    def _read_audio(self, path: str) -> bytes:
        if Path(path).suffix.lower() == ".wav":
            return Path(path).read_bytes()
        ff = self.ctx.settings.get("ffmpeg") or None
        return edit.audio_to_wav(path, ff)

    def _put(self, path: str, g: int, n: int) -> bool:
        try:
            sndfmt.set_sound(self.char.sounds, g, n, self._read_audio(path))
        except (OSError, ValueError, FileNotFoundError) as e:
            QMessageBox.warning(self, tr("Import sound"), str(e))
            return False
        self.char.mark("sounds")
        return True

    def import_wav(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add sound(s)"), "", AUDIO_FILTER)
        if not paths:
            return
        s = self.sound()
        if len(paths) == 1:
            r = ask_pair(self, tr("Add"), tr("Group,index:"), f"{s.group},{s.number + 1}" if s else "0,0")
            if r and self._put(paths[0], *r):
                self.ctx.changed("sounds")
                QTimer.singleShot(10, lambda: self.select(*r))
            return
        g, ok = QInputDialog.getInt(self, tr("Add sounds"), tr("Group for the {0} sounds:").format(len(paths)), s.group if s else 0, 0, 99999)
        if not ok:
            return
        used = {x.number for x in self.sounds() if x.group == g}
        n = 0
        for p in sorted(paths):
            while n in used:
                n += 1
            if self._put(p, g, n):
                used.add(n)
        self.ctx.changed("sounds")

    def change_sound(self) -> None:
        s = self.sound()
        path = QFileDialog.getOpenFileName(self, tr("Replace sound"), "", AUDIO_FILTER)[0] if s else None
        if path and self._put(path, s.group, s.number):
            self.ctx.changed("sounds")

    def export_wav(self) -> None:
        s = self.sound()
        if s:
            path, _ = QFileDialog.getSaveFileName(self, tr("Export"), f"{s.group}-{s.number}.wav", "WAV (*.wav)")
            if path:
                ops.export_sound(self.char, s.group, s.number, path)

    def duplicate(self) -> None:
        s = self.sound()
        if not s:
            return
        r = ask_pair(self, tr("Duplicate sound"), tr("New group,index:"), f"{s.group},{s.number + 1}")
        if r:
            edit.duplicate_sound(self.char, s.group, s.number, *r)
            self.ctx.changed("sounds")

    def delete(self) -> None:
        s = self.sound()
        if s and QMessageBox.question(self, tr("Delete"), tr("Delete sound {0},{1}?").format(s.group, s.number)) == QMessageBox.StandardButton.Yes:
            edit.remove_sound(self.char, s.group, s.number)
            self.ctx.changed("sounds")

    def rename_group(self) -> None:
        s = self.sound()
        if not s:
            return
        g, ok = QInputDialog.getInt(self, tr("Renumber group"), tr("New number for group {0}:").format(s.group), s.group, 0, 99999)
        if ok and g != s.group:
            try:
                edit.renumber_sound_group(self.char, s.group, g)
            except ValueError as e:
                QMessageBox.warning(self, tr("Renumber group"), str(e))
                return
            self.ctx.changed("sounds")

    def delete_group(self) -> None:
        s = self.sound()
        if s and QMessageBox.question(self, tr("Delete group"), tr("Delete all sounds in group {0}?").format(s.group)) == QMessageBox.StandardButton.Yes:
            edit.delete_sound_group(self.char, s.group)
            self.ctx.changed("sounds")

    # ------------------------------------------------------------ área de transferência (menu Editar)

    def copy(self) -> None:
        s = self.sound()
        if s:
            a, b = self.selection()
            self._clip = edit.samples_wav(self.char, s.group, s.number, a, b)
            self.ctx.status.emit(tr("{0} samples copied").format(b - a))

    def cut(self) -> None:
        if self.sound():
            self.copy()
            self.range_op("remove")

    def paste(self) -> None:
        s = self.sound()
        if s and self._clip:
            edit.insert_samples(self.char, s.group, s.number, self.wave.cursor, self._clip)
            self.ctx.changed("sounds")
