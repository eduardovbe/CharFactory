"""Editor de animações: painel 'Animações' | canvas + timeline | 'Explorador de sprites'."""
from __future__ import annotations

import copy
import re

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QSplitter, QVBoxLayout,
                               QWidget)

from ...formats import air
from ...tools import clsnkit, edit, ops, render
from ..canvas import SpriteCanvas
from ..context import AppContext
from ..widgets import (MORE, AxisEditor, DocStrip, GroupIndex, Navigator, Section, SidePanel, Timeline, compact,
                       field_label, form_row, hint_label, spin, tool_bar, tool_button)
from .sprites import thumb_icon
from ...i18n import tr

FLIPS = [(tr("None"), ""), ("H", "H"), ("V", "V"), ("HV", "HV")]
TRANS = [(tr("Normal"), ""), (tr("A (additive)"), "A"), (tr("A1 (additive 50%)"), "A1"), (tr("S (subtractive)"), "S"),
         (tr("AS…D… (alpha)"), "AS")]


class AnimationEditor(QWidget):
    title = tr("Animations")

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._quiet = False
        self.tick = 0

        # ------------------------------------------------ painel esquerdo
        self.tools = tool_bar([
            ("play", tr("Play (Ctrl+P)"), self.on_play, True),
            ("stop", tr("Stop (Ctrl+T)"), self.stop),
            None,
            ("new", tr("Add (Ins)"), self.new_action),
            ("delete", tr("Delete (Ctrl+Del)"), self.delete_action),
            ("duplicate", tr("Duplicate (Ctrl+D)"), self.duplicate_action),
            MORE,
            ("code", tr("Edit animation code (Ctrl+Shift+E)"), self.edit_code),
            ("export", tr("Save GIF…"), self.export_gif),
            ("optimize", tr("Optimize (merge repeated frames)"), self.optimize),
        ])
        self.play_action = self.tools.actions_by_tip[tr("Play (Ctrl+P)")]
        self.nav = Navigator()
        self.nav.indexChanged.connect(lambda _: self.show_action())
        self.number = spin(-1, 99999999)
        self.number.valueChanged.connect(self.on_number)
        self.name = QLineEdit()
        self.name.setToolTip(tr("Animation name: the comment above [Begin Action] (Enter saves)"))
        self.name.editingFinished.connect(self.on_name)
        self.frame_nav = Navigator()
        self.frame_nav.indexChanged.connect(lambda _: self.show_frame())
        self.gi = GroupIndex()
        self.gi.changed.connect(lambda g, i: self.set_field(group=g, item=i))
        self.offset = AxisEditor(tr("Offset X"), tr("Offset Y"))
        self.offset.changed.connect(lambda x, y: self.set_field(x=x, y=y))
        self.time = spin(-1, 9999)
        self.time.valueChanged.connect(lambda v: self.set_field(time=v))
        self.flip = QComboBox()
        for label, v in FLIPS:
            self.flip.addItem(label, v)
        self.flip.currentIndexChanged.connect(lambda _: self.set_field(flip=self.flip.currentData()))
        self.trans = QComboBox()
        for label, v in TRANS:
            self.trans.addItem(label, v)
        self.src, self.dst = spin(0, 256), spin(0, 256)
        self.src.setValue(256)
        self.dst.setValue(256)
        for w in (self.trans, self.src, self.dst):
            (w.currentIndexChanged if w is self.trans else w.valueChanged).connect(
                lambda *_, by_value=w is not self.trans: self.set_trans(by_value))
        self.loop = QCheckBox(tr("Start loop on this frame"))
        self.loop.toggled.connect(self.on_loop)
        self.xs, self.ys, self.ang = (QDoubleSpinBox() for _ in range(3))
        for w, lo, hi, v in ((self.xs, -20, 20, 1), (self.ys, -20, 20, 1), (self.ang, -360, 360, 0)):
            w.setRange(lo, hi)
            w.setSingleStep(0.1)
            w.setValue(v)
            w.valueChanged.connect(lambda *_: self.set_extra())
        compact(self.xs, self.ys, self.ang, self.flip, self.trans)
        self.interp = {k: QCheckBox(label) for k, label in
                       [("blend", tr("Blend")), ("offset", tr("Offset")), ("scale", tr("Scale")), ("angle", tr("Angle"))]}
        for cb in self.interp.values():
            cb.toggled.connect(lambda *_: self.set_interp())
        self.box_tools = tool_bar([
            ("box_attack", tr("Draw attack box (Alt+1): turn it on and drag on the canvas"), lambda on: self.box_mode(1, on), True),
            ("box_collision", tr("Draw collision box (Alt+2): turn it on and drag on the canvas"), lambda on: self.box_mode(2, on), True),
            ("box_delete", tr("Delete selected boxes"), self.delete_selected_box),
            ("box_clear", tr("Delete all boxes of the frame (Alt+Del)"), self.delete_frame_boxes),
            ("copy_prev", tr("Copy boxes from the previous frame (Ctrl+Alt+P)"), self.copy_prev_boxes),
            MORE,
            ("box_switch", tr("Switch the type of the selected box (Ctrl+Alt+T)"), self.switch_box_type),
            None,
            ("box_simple", tr("Simple Clsn2 (sprite area)"), self.auto_box),
            ("box_anatomic", tr("Anatomical Clsn2 (head, torso, legs)"), self.auto_box_anatomical),
            ("box_auto_attack", tr("Automatic attack Clsn1 (extended limb)"), self.auto_box_attack),
            ("box_interp", tr("Interpolate boxes between this frame and another…"), self.interpolate_clsn_dialog),
            None,
            ("box_all_frames", tr("This frame's boxes on all frames of the animation (default)"), self.boxes_to_all_frames),
            ("box_instances", tr("This frame's boxes on every occurrence of this sprite (all animations)"), self.boxes_to_instances),
            ("box_clear_anim", tr("Delete all boxes of the animation"), self.delete_anim_boxes),
            None,
            ("wizard", tr("Create a move in the Wizard from this animation · Ctrl+Shift+M"), self.open_move_wizard),
        ], 18)
        self.onion = QCheckBox(tr("Show onion skin"))
        self.onion.setChecked(bool(ctx.settings.get("onion", True)))
        self.onion.toggled.connect(lambda _: self.show_frame())
        self.onion_mode = QComboBox()
        self.onion_mode.addItems([tr("previous frame"), tr("other animation")])
        self.onion_anim = spin(-1, 99999999)
        self.onion_anim.setToolTip(tr("Onion skin animation (e.g. 0 = stand, to align)"))
        self.onion_frame = spin(0, 999)
        self.onion_frame.setToolTip(tr("Frame of that animation"))
        for w in (self.onion_anim, self.onion_frame):
            w.setEnabled(False)
            w.valueChanged.connect(lambda _: self.show_frame())
        self.onion_mode.currentIndexChanged.connect(
            lambda i: ([w.setEnabled(i == 1) for w in (self.onion_anim, self.onion_frame)], self.show_frame()))
        self.speed = QComboBox()
        for label, v in ((tr("0.25×"), 0.25), (tr("0.5×"), 0.5), (tr("1× (60 ticks/s)"), 1.0), ("2×", 2.0), ("4×", 4.0)):
            self.speed.addItem(label, v)
        self.speed.setCurrentIndex(2)
        self.speed.setToolTip(tr("Playback speed"))
        self.speed.currentIndexChanged.connect(
            lambda _: self.timer.setInterval(max(4, round(1000 / (60 * self.speed.currentData())))))
        compact(self.speed, self.onion_mode)
        compact(self.src, self.dst, self.onion_anim, self.onion_frame, min_width=48)

        def apply_btn(tip, what):
            return tool_button("apply_next", tip, lambda: self.apply_next(what), size=16)

        act_sec = Section(tr("Animation"), "anims.action")
        act_sec.add(form_row(tr("Number"), self.number), form_row(tr("Name"), self.name), form_row(tr("Speed"), self.speed))

        frame_sec = Section(tr("Frame"), "anims.frame")
        for b in (tool_button("add_frame", tr("Add frame (Ctrl+Shift+A)"), self.add_frame, size=16),
                  tool_button("duplicate", tr("Duplicate frame"), self.dup_frame, size=16),
                  tool_button("delete", tr("Delete frame (Ctrl+Shift+Del)"), self.del_frame, size=16)):
            frame_sec.add_header_widget(b)
        self.offset.trailing.addWidget(apply_btn(tr("Apply offset to the next frames (Alt+Shift+O)"), "offset"),
                                       0, Qt.AlignmentFlag.AlignVCenter)
        frame_sec.add(self.frame_nav, self.gi, self.offset,
                      form_row(tr("Time"), self.time, apply_btn(tr("Apply time to the next frames (Alt+Shift+T)"), "time")),
                      form_row(tr("Flip"), self.flip, apply_btn(tr("Apply flip to the next frames (Alt+Shift+F)"), "flip")),
                      self.loop)

        blend_sec = Section(tr("Transparency and scale"), "anims.blend", collapsed=True)
        ip = QGridLayout()
        ip.setHorizontalSpacing(12)
        ip.setVerticalSpacing(4)
        for n, cb in enumerate(self.interp.values()):
            ip.addWidget(cb, n // 2, n % 2)
        blend_sec.add(form_row(tr("Mode"), self.trans,
                               apply_btn(tr("Apply transparency to the next frames (Alt+B)"), "trans")),
                      form_row(tr("Source"), self.src, 26), form_row(tr("Destination"), self.dst, 26),
                      hint_label(tr("Source and destination work in AS…D… mode (changing them turns the mode on).")),
                      form_row(tr("Scale X"), self.xs, apply_btn(tr("Apply scale and angle to the next frames"), "scale")),
                      form_row(tr("Scale Y"), self.ys, 26), form_row(tr("Angle"), self.ang, 26),
                      field_label(tr("Interpolate to the next frame (MUGEN 1.1)")), ip)

        box_sec = Section(tr("Collision boxes"), "anims.boxes")
        box_sec.add(self.box_tools,
                    hint_label(tr("Turn on the red box (attack) or the blue one (collision) and drag on the canvas to draw; Shift+drag draws without turning it on. Dragging a box moves it, the edges resize it and Del deletes it.")))

        onion_sec = Section(tr("Onion skin"), "anims.onion", collapsed=True)
        onion_sec.add(self.onion, form_row(tr("Show"), self.onion_mode),
                      form_row(tr("Anim"), self.onion_anim), form_row(tr("Frame"), self.onion_frame))

        content = QWidget()
        lv = QVBoxLayout(content)
        lv.addWidget(self.tools)
        lv.addWidget(self.nav)
        for sec in (act_sec, frame_sec, box_sec, blend_sec, onion_sec):
            lv.addWidget(sec)
        lv.addWidget(hint_label(tr("Ctrl+drag the sprite changes the offset; arrows move 1 px (Shift: 10).")))
        lv.addStretch()
        self.left = SidePanel(tr("Animations"), content, header=False)

        # ------------------------------------------------ centro
        self.file_tab = DocStrip()
        self.canvas = SpriteCanvas()
        self.canvas.edit_boxes = True
        self.canvas.zoom = 3
        self.canvas.boxesChanged.connect(self.on_boxes)
        self.canvas.drawModeExit.connect(lambda: [a.setChecked(False) for a in self.box_tools.actions_by_tip.values()
                                                  if a.isCheckable()])
        self.canvas.drag_image = True
        self.canvas.imageDragged.connect(self.on_image_drag)
        self.canvas.nudged.connect(lambda dx, dy: self.frame() and self.set_field(x=self.frame().x + dx, y=self.frame().y + dy))
        self._drag_offset = None
        self._clip_frames: list = []
        self.timeline = Timeline()
        self.timeline.tickClicked.connect(self.goto_tick)
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self.file_tab)
        cv.addWidget(self.canvas, 1)
        cv.addWidget(self.timeline)

        # ------------------------------------------------ direito: explorador de sprites
        self.group_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(self.fill_explorer)
        self.explorer = QListWidget()
        self.explorer.setViewMode(QListWidget.ViewMode.IconMode)
        self.explorer.setIconSize(QSize(48, 48))
        self.explorer.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.explorer.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.explorer.itemDoubleClicked.connect(lambda *_: self.add_from_explorer(selected=True))
        ex_tools = QHBoxLayout()
        ex_tools.setSpacing(4)
        ex_tools.addWidget(self.group_combo, 1)
        ex_tools.addWidget(tool_button("add_frame", tr("Add selected to the current animation"), lambda: self.add_from_explorer(True)))
        ex_tools.addWidget(tool_button("add_all", tr("Add all to the current animation"), lambda: self.add_from_explorer(False)))
        ex_tools.addWidget(tool_button("anim_new", tr("New animation with the whole group"), self.new_from_group))
        rc = QWidget()
        rv = QVBoxLayout(rc)
        rv.addLayout(ex_tools)
        rv.addWidget(self.explorer, 1)
        rv.addWidget(hint_label(tr("Double-click adds the sprite as a new frame.")))
        self.right = SidePanel(tr("Sprite explorer"), rc, 240)

        split = QSplitter()
        split.addWidget(center)
        split.addWidget(self.right)
        split.setSizes([900, 280])
        split.setChildrenCollapsible(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

        self.timer = QTimer(self)
        self.timer.setInterval(round(1000 / 60))
        self.timer.timeout.connect(self.on_tick)
        ctx.characterChanged.connect(self.load)
        ctx.themeChanged.connect(self.on_theme)
        ctx.modelChanged.connect(lambda part: part in ("anims", "sprites") and QTimer.singleShot(0, lambda: self.load(keep=True)))
        self.on_theme()

    def on_theme(self) -> None:
        self.canvas.set_theme(self.ctx.theme)
        self.timeline.theme = self.ctx.theme
        self.timeline.update()

    # ------------------------------------------------------------ dados

    @property
    def char(self):
        return self.ctx.char

    def numbers(self) -> list[int]:
        return self.char.anims.numbers() if self.char else []

    def action(self) -> air.Action | None:
        nums = self.numbers()
        i = self.nav.index()
        return self.char.anims.get(nums[i]) if 0 <= i < len(nums) else None

    def frame(self) -> air.Frame | None:
        a = self.action()
        i = self.frame_nav.index()
        return a.frames[i] if a and 0 <= i < len(a.frames) else None

    def load(self, keep: bool = False) -> None:
        if not self.char:
            return
        cur = self.action().number if (keep and self.action()) else None
        fi = self.frame_nav.index() if keep else 0
        self.file_tab.set_file(self.char.path_of("anim").name)
        self.nav.set_count(len(self.numbers()))
        if cur is not None and cur in self.numbers():
            self.nav.set_index(self.numbers().index(cur))
        elif not keep:
            self.nav.set_index(0)
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        for gnum in sorted(self.char.sprites.groups()):
            self.group_combo.addItem(tr("Group {0}").format(gnum), gnum)
        self.group_combo.blockSignals(False)
        self.show_action(fi)
        self.fill_explorer()

    def select_action(self, n: int) -> None:
        if n in self.numbers():
            self.nav.set_index(self.numbers().index(n))
            self.show_action()

    def _action_name(self, number: int) -> str:
        """Comentário imediatamente acima de [Begin Action N]."""
        return self.char.anims.name_of(number)

    def on_name(self) -> None:
        a = self.action()
        if self._quiet or not a or self.name.text().strip() == self._action_name(a.number):
            return
        self.char.anims.set_name(a.number, self.name.text())
        self._changed()
        self.ctx.status.emit(tr("Animation {0} name: {1}").format(a.number, self.name.text().strip() or tr('(no name)')))

    def show_action(self, frame_index: int = 0) -> None:
        a = self.action()
        self._quiet = True
        if a:
            self.number.setValue(a.number)
            self.name.setText(self._action_name(a.number))
            self.frame_nav.set_count(len(a.frames))
            self.frame_nav.set_index(min(frame_index, max(0, len(a.frames) - 1)))
            tt = a.total_time
            self.frame_nav.set_suffix(f" - {tt} ticks" if tt >= 0 else tr(" - infinite"))
            self.timeline.set_frames([f.time for f in a.frames], a.loopstart, f"{a.number}\n{self.name.text()}")
        self._quiet = False
        self.show_frame()

    def show_frame(self) -> None:
        a, f = self.action(), self.frame()
        if not f:
            self.canvas.set_image(None)
            self.canvas.set_boxes([], [])
            return
        self._quiet = True
        self.gi.set(f.group, f.item)
        self.offset.set(f.x, f.y)
        self.time.setValue(f.time)
        self.flip.setCurrentIndex(max(0, [v for _, v in FLIPS].index(f.flip.upper()) if f.flip.upper() in [v for _, v in FLIPS] else 0))
        t = f.trans.upper()
        m = re.match(r"AS(\d+)D(\d+)", t)
        self.trans.setCurrentIndex(4 if m else max(0, [v for _, v in TRANS].index(t) if t in [v for _, v in TRANS] else 0))
        if m:
            self.src.setValue(int(m[1]))
            self.dst.setValue(int(m[2]))
        self.loop.setChecked(self.frame_nav.index() == a.loopstart)
        ex = (f.extra + ["", "", ""])[:3]
        self.xs.setValue(float(ex[0] or 1))
        self.ys.setValue(float(ex[1] or 1))
        self.ang.setValue(float(ex[2] or 0))
        for k, cb in self.interp.items():
            cb.setChecked(k in f.interpolate)
        self._quiet = False
        self.timeline.current_tick = self.timeline.frame_start(self.frame_nav.index())
        self.timeline.set_current_frame(self.frame_nav.index())
        self.timeline.update()
        self._draw_layer(f, onion=False)
        self._draw_onion(a)
        self.canvas.set_boxes(f.clsn1, f.clsn2)

    def _draw_onion(self, a) -> None:
        """Frame anterior, ou o frame de outra animação (ex.: o parado, para alinhar)."""
        i = self.frame_nav.index()
        other = None
        if self.onion.isChecked() and not self.timer.isActive():
            if self.onion_mode.currentIndex() == 1:
                oa = self.char.anims.get(self.onion_anim.value())
                if oa and oa.frames:
                    other = oa.frames[min(self.onion_frame.value(), len(oa.frames) - 1)]
            elif i > 0:
                other = a.frames[i - 1]
        if other is not None:
            self._draw_layer(other, onion=True)
        else:
            self.canvas.set_onion(None)

    def _draw_layer(self, f: air.Frame, onion: bool) -> None:
        """Desenha o frame com flip, escala, ângulo e alfa AS/D (MUGEN 1.1) aplicados."""
        target = self.canvas.set_onion if onion else self.canvas.set_image
        # o canvas mistura o sprite principal (soma, subtração, AS/D); o onion só fica translúcido
        lay = render.frame_layer(self.char, f, apply_trans=onion)
        if not onion:
            self.canvas.set_blend(f.trans)
        if lay is None:
            target(None)
            return
        img, x, y = lay
        target(img, 0, 0, (x, y))

    # ------------------------------------------------------------ edição do frame

    def _changed(self) -> None:
        self.ctx.record("anims")                 # marca para gravar e entra no desfazer
        a = self.action()
        if a:
            self.frame_nav.set_suffix(f" - {a.total_time} ticks" if a.total_time >= 0 else tr(" - infinite"))
            self.timeline.set_frames([f.time for f in a.frames], a.loopstart, self.timeline.title)
        self.ctx.modelChanged.emit("anims_inline")

    def set_field(self, **kw) -> None:
        f = self.frame()
        if self._quiet or not f:
            return
        for k, v in kw.items():
            setattr(f, k, v)
        self._changed()
        self.show_frame()

    def set_trans(self, by_value: bool = False) -> None:
        if self._quiet:
            return
        v = self.trans.currentData()
        if by_value and v != "AS":
            self._quiet = True              # mexer em origem/destino liga o modo AS…D…
            self.trans.setCurrentIndex(4)
            self._quiet = False
            v = "AS"
        if v == "AS":
            v = f"AS{self.src.value()}D{self.dst.value()}"
        self.set_field(trans=v)

    def set_extra(self) -> None:
        f = self.frame()
        if self._quiet or not f:
            return
        xs, ys, an = self.xs.value(), self.ys.value(), self.ang.value()
        f.extra = [] if (xs, ys, an) == (1, 1, 0) else [f"{xs:g}", f"{ys:g}", f"{an:g}"]
        self._changed()
        self._draw_layer(f, onion=False)

    def set_interp(self) -> None:
        f = self.frame()
        if self._quiet or not f:
            return
        f.interpolate = [k for k, cb in self.interp.items() if cb.isChecked()]
        self._changed()

    def on_loop(self, on: bool) -> None:
        a = self.action()
        if self._quiet or not a:
            return
        a.loopstart = self.frame_nav.index() if on else 0
        self._changed()

    def on_number(self, n: int) -> None:
        a = self.action()
        if self._quiet or not a or n == a.number:
            return
        if n in self.numbers():
            self.ctx.status.emit(tr("Action {0} already exists").format(n))
            return
        self.char.anims.renumber(a.number, n)    # no lugar: posição e comentários ficam
        self.ctx.changed("anims")
        QTimer.singleShot(20, lambda: self.select_action(n))

    def apply_next(self, what: str) -> None:
        a, f = self.action(), self.frame()
        if not f:
            return
        for g in a.frames[self.frame_nav.index() + 1:]:
            if what == "offset":
                g.x, g.y = f.x, f.y
            elif what == "time":
                g.time = f.time
            elif what == "flip":
                g.flip = f.flip
            elif what == "trans":
                g.trans = f.trans
            elif what == "scale":
                g.extra = list(f.extra)
        self._changed()
        self.ctx.status.emit(tr("Applied to the next frames"))

    def add_frame(self) -> None:
        a, f = self.action(), self.frame()
        if not a:
            return
        base = f or air.Frame(0, 0)
        new = air.Frame(base.group, base.item, base.x, base.y, base.time if base.time > 0 else 4)
        new.clsn2 = list(base.clsn2)
        i = self.frame_nav.index() + 1
        a.frames.insert(i, new)
        self._changed()
        self.show_action(i)

    def dup_frame(self) -> None:
        a, f = self.action(), self.frame()
        if f:
            i = self.frame_nav.index() + 1
            a.frames.insert(i, copy.deepcopy(f))
            self._changed()
            self.show_action(i)

    def del_frame(self) -> None:
        a = self.action()
        i = self.frame_nav.index()
        if a and len(a.frames) > 1:
            del a.frames[i]
            a.loopstart = min(a.loopstart, len(a.frames) - 1)
            self._changed()
            self.show_action(max(0, i - 1))

    # ------------------------------------------------------------ caixas

    def box_mode(self, kind: int, on: bool) -> None:
        """Liga o desenho de caixas: com o botão ligado, arrastar no canvas cria a caixa (antes só
        escolhia o tipo e ainda exigia Shift, então arrastar normalmente não fazia nada)."""
        tips = {1: tr("Draw attack box (Alt+1): turn it on and drag on the canvas"), 2: tr("Draw collision box (Alt+2): turn it on and drag on the canvas")}
        if on:
            self.canvas.new_box_kind = kind
            self.canvas.draw_box_kind = kind
            other = self.box_tools.actions_by_tip[tips[2 if kind == 1 else 1]]
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
            self.ctx.status.emit(tr("Drag on the canvas to draw the ")
                                 + (tr("attack box (Clsn1)") if kind == 1 else tr("collision box (Clsn2)"))
                                 + tr("; Esc or the same button turns it off"))
        elif self.canvas.draw_box_kind == kind:
            self.canvas.draw_box_kind = None
        self.canvas.update_cursor()

    def on_boxes(self) -> None:
        f = self.frame()
        if f:
            f.clsn1, f.clsn2 = self.canvas.get_boxes()
            self._changed()

    def delete_selected_box(self) -> None:
        if self.canvas.selected is not None:
            del self.canvas.boxes[self.canvas.selected]
            self.canvas.selected = None
            self.canvas.update()
            self.on_boxes()

    def delete_frame_boxes(self) -> None:
        f = self.frame()
        if f:
            f.clsn1, f.clsn2 = [], []
            self._changed()
            self.show_frame()

    def copy_prev_boxes(self) -> None:
        a, f = self.action(), self.frame()
        i = self.frame_nav.index()
        if f and i == 0:
            self.ctx.status.emit(tr("The first frame has no previous frame to copy the boxes from"))
        elif f:
            p = a.frames[i - 1]
            f.clsn1, f.clsn2 = list(p.clsn1), list(p.clsn2)
            self._changed()
            self.show_frame()

    def switch_box_type(self) -> None:
        if self.canvas.selected is not None:
            b = self.canvas.boxes[self.canvas.selected]
            b[0] = 1 if b[0] == 2 else 2
            self.canvas.update()
            self.on_boxes()

    def boxes_to_all_frames(self) -> None:
        a, f = self.action(), self.frame()
        if not f:
            return
        for g in a.frames:
            g.clsn1, g.clsn2 = list(f.clsn1), list(f.clsn2)
        a.clsn1_default, a.clsn2_default = list(f.clsn1), list(f.clsn2)
        self._changed()
        self.show_frame()
        self.ctx.status.emit(tr("Boxes copied to the {0} frames (they become the default boxes)").format(len(a.frames)))

    def boxes_to_instances(self) -> None:
        f = self.frame()
        if f:
            r = edit.apply_clsn_to_sprite(self.char, f.group, f.item, list(f.clsn1), list(f.clsn2))
            self.ctx.changed("anims")
            self.ctx.status.emit(tr("Boxes applied to sprite {0},{1} in {2} frame(s)").format(f.group, f.item, r['frames_updated']))

    def delete_anim_boxes(self) -> None:
        a = self.action()
        if a and QMessageBox.question(self, tr("Delete boxes"), tr("Delete all boxes of animation {0}?").format(a.number)) \
                == QMessageBox.StandardButton.Yes:
            for g in a.frames:
                g.clsn1, g.clsn2 = [], []
            a.clsn1_default, a.clsn2_default = [], []
            self._changed()
            self.show_frame()

    def on_image_drag(self, dx: int, dy: int, done: bool) -> None:
        """Ctrl+arrastar o sprite: muda o offset do frame (a 'Alignment tool' do FFS)."""
        f = self.frame()
        if not f:
            return
        if self._drag_offset is None:
            self._drag_offset = (f.x, f.y)
        x0, y0 = self._drag_offset
        self.set_field(x=x0 + dx, y=y0 + dy)
        if done:
            self._drag_offset = None

    # área de transferência do menu Editar: frames
    def copy(self) -> None:
        f = self.frame()
        if f:
            self._clip_frames = [copy.deepcopy(f)]
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(f.line())
            self.ctx.status.emit(tr("Frame copied"))

    def cut(self) -> None:
        a = self.action()
        if self.frame() and a and len(a.frames) > 1:
            self.copy()
            self.del_frame()

    def paste(self) -> None:
        a = self.action()
        if not a or not self._clip_frames:
            return
        i = self.frame_nav.index() + 1
        for k, fr in enumerate(self._clip_frames):
            a.frames.insert(i + k, copy.deepcopy(fr))
        self._changed()
        self.show_action(i)

    def auto_box(self) -> None:
        f = self.frame()
        if f:
            f.clsn2 = ops.auto_clsn(self.char, f.group, f.item)
            self._changed()
            self.show_frame()

    def auto_box_anatomical(self) -> None:
        f = self.frame()
        if f and self.char:
            boxes = clsnkit.anatomical_clsn2(self.char, f.group, f.item)
            if boxes:
                f.clsn2 = boxes
                self._changed()
                self.show_frame()

    def auto_box_attack(self) -> None:
        f = self.frame()
        if f and self.char:
            boxes = clsnkit.attack_clsn1(self.char, f.group, f.item)
            if boxes:
                f.clsn1 = boxes
                self._changed()
                self.show_frame()
            else:
                QMessageBox.information(self, tr("Attack Clsn1"),
                                        tr("No extended limb or front weapon detected in this frame."))

    def interpolate_clsn_dialog(self) -> None:
        a = self.action()
        if not a or len(a.frames) < 3:
            QMessageBox.information(self, tr("Interpolate Boxes"), tr("The animation needs at least 3 frames."))
            return
        curr = self.frame_nav.index()
        next_cand = -1
        for k in range(curr + 1, len(a.frames)):
            if a.frames[k].clsn1 or a.frames[k].clsn2:
                next_cand = k
                break
        if next_cand == -1:
            next_cand = len(a.frames) - 1
        target, ok = QInputDialog.getInt(self, tr("Interpolate Boxes"),
                                         tr("Interpolate boxes from the current frame ({0}) to frame:").format(curr + 1),
                                         next_cand + 1, curr + 2, len(a.frames))
        if not ok:
            return
        target_idx = target - 1
        n2 = clsnkit.interpolate_action_clsn(a, curr, target_idx, box_type=2) if (a.frames[curr].clsn2 and a.frames[target_idx].clsn2) else 0
        n1 = clsnkit.interpolate_action_clsn(a, curr, target_idx, box_type=1) if (a.frames[curr].clsn1 and a.frames[target_idx].clsn1) else 0
        self._changed()
        self.show_frame()

    def open_move_wizard(self) -> None:
        a = self.action()
        anim_num = a.number if a else None
        p = self.window()
        if hasattr(p, "move_wizard"):
            p.move_wizard(initial_anim=anim_num)
        else:
            from ..movewizard import MoveWizardDialog
            MoveWizardDialog(self.ctx, self, initial_anim=anim_num).exec()

    # ------------------------------------------------------------ ações

    def new_action(self) -> None:
        n, ok = QInputDialog.getInt(self, tr("New animation"), tr("Number:"), (max(self.numbers()) + 1) if self.numbers() else 0, -1, 99999999)
        if not ok:
            return
        if n in self.numbers():
            QMessageBox.warning(self, tr("New animation"), tr("Action {0} already exists.").format(n))
            return
        f = self.frame()
        ops.create_action(self.char, n, [{"group": f.group if f else 0, "item": f.item if f else 0, "time": 5}])
        self.ctx.changed("anims")
        QTimer.singleShot(20, lambda: self.select_action(n))

    def duplicate_action(self) -> None:
        a = self.action()
        if not a:
            return
        n, ok = QInputDialog.getInt(self, tr("Duplicate animation"), tr("New number:"), a.number + 1, -1, 99999999)
        if ok:
            edit.duplicate_action(self.char, a.number, n)
            self.ctx.changed("anims")
            QTimer.singleShot(20, lambda: self.select_action(n))

    def delete_action(self) -> None:
        a = self.action()
        if a and QMessageBox.question(self, tr("Delete"), tr("Delete animation {0}?").format(a.number)) == QMessageBox.StandardButton.Yes:
            ops.remove_action(self.char, a.number)
            self.ctx.changed("anims")

    def optimize(self) -> None:
        a = self.action()
        if a:
            r = edit.optimize_action(self.char, a.number)
            self.ctx.status.emit(tr("{0} repeated frame(s) merged").format(r['removed_frames']))
            self.ctx.changed("anims")

    def edit_code(self) -> None:
        a = self.action()
        if a:
            self.ctx.request_code.emit(self.char.path_of("anim").name, f"[Begin Action {a.number}]")

    def export_gif(self) -> None:
        a = self.action()
        if not a:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Save GIF"), f"acao_{a.number}.gif", "GIF (*.gif)")
        if path:
            with open(path, "wb") as fh:
                fh.write(render.render_gif(self.char, a.number))

    # ------------------------------------------------------------ explorador de sprites

    def fill_explorer(self, *_):
        self.explorer.clear()
        if not self.char:
            return
        g = self.group_combo.currentData()
        for spr in self.char.sprites.sprites:
            if spr.group != g:
                continue
            it = QListWidgetItem(str(spr.item))
            it.setData(Qt.ItemDataRole.UserRole, (spr.group, spr.item))
            try:
                it.setIcon(thumb_icon(self.char.sprites.image(spr)))
            except Exception:  # noqa: BLE001
                pass
            self.explorer.addItem(it)

    def add_from_explorer(self, selected: bool) -> None:
        a = self.action()
        if not a:
            return
        items = self.explorer.selectedItems() if selected else [self.explorer.item(i) for i in range(self.explorer.count())]
        for it in items:
            g, i = it.data(Qt.ItemDataRole.UserRole)
            fr = air.Frame(g, i, 0, 0, 4)
            fr.clsn2 = ops.auto_clsn(self.char, g, i)
            a.frames.append(fr)
        if items:
            self._changed()
            self.show_action(len(a.frames) - 1)

    def new_from_group(self) -> None:
        g = self.group_combo.currentData()
        if g is None:
            return
        n, ok = QInputDialog.getInt(self, tr("New animation"), tr("Animation number with group {0}:").format(g), g, -1, 99999999)
        if ok:
            items = sorted(s.item for s in self.char.sprites.sprites if s.group == g)
            ops.create_action(self.char, n, [{"group": g, "item": i, "time": 4} for i in items])
            self.ctx.changed("anims")
            QTimer.singleShot(20, lambda: self.select_action(n))

    # ------------------------------------------------------------ reprodução

    def on_play(self, on: bool) -> None:
        self.tick = 0
        (self.timer.start if on else self.timer.stop)()
        self.show_frame()

    def stop(self) -> None:
        self.play_action.setChecked(False)
        self.frame_nav.set_index(0)

    def goto_tick(self, t: int) -> None:
        a = self.action()
        if not a:
            return
        acc = 0
        for i, f in enumerate(a.frames):
            d = f.time if f.time > 0 else 60
            if t < acc + d:
                self.frame_nav.set_index(i)
                return
            acc += d

    def on_tick(self) -> None:
        a, f = self.action(), self.frame()
        if not a or not f:
            return
        self.tick += 1
        self.timeline.current_tick = self.timeline.frame_start(self.frame_nav.index()) + self.tick
        self.timeline.update()
        if render.has_interpolation(a) and 0 < self.tick < f.time:
            self._draw_layer(render.interpolated_frame(a, self.frame_nav.index(), self.tick), onion=False)
        if f.time >= 0 and self.tick >= max(f.time, 1):
            self.tick = 0
            i = self.frame_nav.index() + 1
            self.frame_nav.set_index(i if i < len(a.frames) else a.loopstart)
