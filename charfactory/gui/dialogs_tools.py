"""Diálogos das ferramentas além do básico: comparar com backup, importar de outro projeto,
transplantar golpe e testador de comandos."""
from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import project
from ..project import ProjectError
from .context import AppContext
from .widgets import field_label
from ..i18n import tr

TEXT_SUFFIXES = {".def", ".air", ".cmd", ".cns", ".st", ".zss", ".txt"}


def _pick_def(parent, start: str = "") -> str | None:
    path, _ = QFileDialog.getOpenFileName(parent, tr("Other character"), start, tr("MUGEN character (*.def)"))
    return path or None


def _src_row(parent, on_pick) -> tuple[QWidget, QLineEdit]:
    edit = QLineEdit()
    edit.setPlaceholderText(tr("the other character's .def file"))
    b = QPushButton("…")

    def pick():
        p = _pick_def(parent, str(Path(edit.text()).parent) if edit.text() else "")
        if p:
            edit.setText(p)
            on_pick(p)
    b.clicked.connect(pick)
    edit.returnPressed.connect(lambda: edit.text() and on_pick(edit.text()))
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    h.addWidget(b)
    return w, edit


def _spin(lo=-99999, hi=99999, v=0) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(v)
    return s


# ======================================================================= comparar com backup

class BackupDiffDialog(QDialog):
    """Arquivo a arquivo: igual, alterado, só no backup ou só no projeto; diferença de texto
    linha a linha e resumo dos binários (sprites, sons). Restaura um arquivo escolhido."""

    def __init__(self, char, backup: Path, restore, parent=None):
        super().__init__(parent)
        self.char, self.backup, self.restore = char, backup, restore
        self.setWindowTitle(tr("Compare with backup {0}").format(backup.name))
        self.resize(1100, 680)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("File"), tr("Status")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(lambda it, _: self.show_diff(it))
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        rest = QPushButton(tr("Restore this file from the backup"))
        rest.clicked.connect(self.do_restore)
        split = QSplitter()
        split.addWidget(self.tree)
        split.addWidget(self.view)
        split.setSizes([320, 780])
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addWidget(rest)
        self.fill()

    @staticmethod
    def _hash(p: Path) -> str:
        return hashlib.sha1(p.read_bytes()).hexdigest()

    def fill(self) -> None:
        cur = {p.relative_to(self.char.dir).as_posix(): p for p in self.char.dir.rglob("*")
               if p.is_file() and "_backups" not in p.relative_to(self.char.dir).parts and p.suffix.lower() != ".bak"}
        bak = {p.relative_to(self.backup).as_posix(): p for p in self.backup.rglob("*") if p.is_file()}
        for rel in sorted(set(cur) | set(bak), key=str.lower):
            a, b = cur.get(rel), bak.get(rel)
            if a and b:
                st = tr("same") if self._hash(a) == self._hash(b) else tr("changed")
            elif b:
                st = tr("only in the backup")
            else:
                st = tr("only in the project")
            if st == tr("only in the project") and rel not in {p.relative_to(self.char.dir).as_posix()
                                                     for p in self.char.project_files() if p.is_relative_to(self.char.dir)}:
                continue                          # arquivos soltos que o backup nunca teve
            it = QTreeWidgetItem([rel, st])
            it.setData(0, Qt.ItemDataRole.UserRole, (rel, a, b))
            if st == tr("changed"):
                it.setForeground(1, Qt.GlobalColor.yellow)
            self.tree.addTopLevelItem(it)

    def show_diff(self, it) -> None:
        if not it:
            return
        rel, a, b = it.data(0, Qt.ItemDataRole.UserRole)
        if not (a and b):
            self.view.setPlainText(tr("The file exists on one side only."))
            return
        if Path(rel).suffix.lower() in TEXT_SUFFIXES:
            ta = a.read_text("latin-1").splitlines()
            tb = b.read_text("latin-1").splitlines()
            d = list(difflib.unified_diff(tb, ta, f"backup/{rel}", f"{tr('current')}/{rel}", lineterm="", n=2))
            self.view.setPlainText("\n".join(d) if d else tr("No differences."))
            return
        lines = [tr("current: {0:,} bytes · backup: {1:,} bytes").format(a.stat().st_size, b.stat().st_size)]
        try:
            if a.suffix.lower() == ".sff":
                from ..formats import sff
                sa, sb = sff.load(a), sff.load(b)
                ka = {(s.group, s.item): (s.width, s.height, s.axis_x, s.axis_y, s._data) for s in sa.sprites}
                kb = {(s.group, s.item): (s.width, s.height, s.axis_x, s.axis_y, s._data) for s in sb.sprites}
                lines.append(tr("sprites: {0} current · {1} in the backup").format(len(ka), len(kb)))
                for name, keys in ((tr("new"), sorted(set(ka) - set(kb))), (tr("deleted"), sorted(set(kb) - set(ka))),
                                   (tr("modified"), sorted(k for k in set(ka) & set(kb) if ka[k] != kb[k]))):
                    if keys:
                        lines.append(f"{name} ({len(keys)}): " + ", ".join(f"{g},{i}" for g, i in keys[:60]))
                pa, pb = [p.colors for p in sa.palettes], [p.colors for p in sb.palettes]
                lines.append(tr("same palettes") if pa == pb else tr("palettes changed ({0} current · {1} in the backup)").format(len(pa), len(pb)))
            elif a.suffix.lower() == ".snd":
                from ..formats import snd
                na, nb = snd.load(a), snd.load(b)
                ka = {(s.group, s.number): s.wav for s in na.sounds}
                kb = {(s.group, s.number): s.wav for s in nb.sounds}
                for name, keys in ((tr("new"), sorted(set(ka) - set(kb))), (tr("deleted"), sorted(set(kb) - set(ka))),
                                   (tr("modified"), sorted(k for k in set(ka) & set(kb) if ka[k] != kb[k]))):
                    if keys:
                        lines.append(tr("sounds {0} ({1}): ").format(name, len(keys)) + ", ".join(f"{g},{i}" for g, i in keys[:60]))
        except Exception as e:  # noqa: BLE001
            lines.append(tr("(could not read: {0})").format(e))
        self.view.setPlainText("\n".join(lines))

    def do_restore(self) -> None:
        it = self.tree.currentItem()
        if not it:
            return
        rel, _a, b = it.data(0, Qt.ItemDataRole.UserRole)
        if b and QMessageBox.question(self, tr("Restore"), tr("Replace {0} with the backup version?").format(rel)) \
                == QMessageBox.StandardButton.Yes:
            self.restore(b, rel)
            self.accept()


# ======================================================================= importar de outro projeto

class ImportFromProjectDialog(QDialog):
    """Sprites (grupos), animações ou sons (grupos) de outro personagem, com deslocamento."""

    # frases inteiras por tipo: juntar pedaços quebra o gênero e a ordem nas outras línguas
    TITLES = {"sprites": tr("Import sprite groups from another project"),
              "anims": tr("Import animations from another project"),
              "sounds": tr("Import sound groups from another project")}
    PICK = {"sprites": tr("Pick the sprite groups:"), "anims": tr("Pick the animations:"),
            "sounds": tr("Pick the sound groups:")}

    def __init__(self, ctx: AppContext, kind: str, parent=None):
        super().__init__(parent)
        self.ctx, self.kind, self.src = ctx, kind, None
        self.setWindowTitle(self.TITLES[kind])
        self.resize(560, 600)
        row, self.path = _src_row(self, self.load_src)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.offset = _spin(-99999, 99999, 0)
        self.offset.setToolTip(tr("Added to each item's number (e.g. 1000 brings group 200 as 1200)"))
        self.with_sprites = QCheckBox(tr("Also bring the sprites used by the animations"))
        self.with_sprites.setChecked(True)
        self.sprite_offset = _spin(-99999, 99999, 0)
        f = QFormLayout()
        f.addRow(tr("Character"), row)
        f.addRow(self.PICK[kind], self.list)
        f.addRow(tr("Number offset"), self.offset)
        if kind == "anims":
            f.addRow(self.with_sprites)
            f.addRow(tr("Sprite group offset"), self.sprite_offset)
        self.note = QLabel()
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        f.addRow(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Import"))
        bb.accepted.connect(self.run)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f, 1)
        lay.addWidget(bb)
        others = [c for c in ctx.projects if c is not ctx.char]
        if others:                                   # outro projeto aberto em aba: sugere ele
            self.path.setText(str(others[-1].def_path))
            self.load_src(str(others[-1].def_path))

    def load_src(self, path: str) -> None:
        self.list.clear()
        try:
            self.src = project.load(path, self.ctx.settings.get("mugen_dir") or None)
            if self.kind == "sprites":
                for g, items in sorted(self.src.sprites.groups().items()):
                    self._item(tr("group {0}  ({1} sprites)").format(g, len(items)), g)
            elif self.kind == "anims":
                for n in self.src.anims.numbers():
                    name = self.src.anims.name_of(n)
                    self._item(f"{n}" + (f"  · {name}" if name else "") + f"  ({len(self.src.anims.get(n).frames)} frames)", n)
            else:
                groups: dict[int, int] = {}
                for s in self.src.sounds.sounds:
                    groups[s.group] = groups.get(s.group, 0) + 1
                for g, k in sorted(groups.items()):
                    self._item(tr("group {0}  ({1} sounds)").format(g, k), g)
        except (ProjectError, OSError, ValueError) as e:
            self.note.setText(tr("Could not open: {0}").format(e))

    def _item(self, text: str, data) -> None:
        it = QListWidgetItem(text)
        it.setData(Qt.ItemDataRole.UserRole, data)
        self.list.addItem(it)

    def run(self) -> None:
        if not self.src:
            return
        picked = [it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()]
        if not picked:
            self.note.setText(tr("Select at least one item."))
            return
        from ..tools import charkit, transplant
        c, off = self.ctx.char, self.offset.value()
        try:
            if self.kind == "sprites":
                clash = [g + off for g in picked if g + off in c.sprites.groups()]
                if clash and not self._confirm(tr("Groups {0} already exist; sprites with the same number will be replaced.").format(clash[:10])):
                    return
                charkit.import_groups(c, self.src.def_path, picked, off, make_anims=False)
                self.ctx.changed("sprites")
                msg = tr("{0} sprite group(s) imported").format(len(picked))
            elif self.kind == "anims":
                clash = [n + off for n in picked if c.anims.get(n + off) is not None]
                if clash and not self._confirm(tr("Animations {0} already exist and will be replaced.").format(clash[:10])):
                    return
                r = transplant.import_anims(c, self.src, picked, off,
                                            self.sprite_offset.value() if self.with_sprites.isChecked() else None,
                                            self.src.def_path)
                if r["sprite_groups"]:
                    self.ctx.changed("sprites")
                self.ctx.changed("anims")
                msg = tr("{0} animation(s)").format(len(r['anims'])) + (tr(" and {0} sprite group(s)").format(len(r['sprite_groups'])) if r["sprite_groups"] else "")
            else:
                r = transplant.import_sound_groups(c, self.src, picked, off)
                self.ctx.changed("sounds")
                msg = tr("{0} sound(s) imported").format(r['sounds'])
        except (ProjectError, ValueError, OSError) as e:
            QMessageBox.warning(self, tr("Import"), str(e))
            return
        self.ctx.status.emit(msg + f" de {self.src.name}")
        self.accept()

    def _confirm(self, text: str) -> bool:
        return QMessageBox.question(self, tr("Import"), text + tr("\nContinue?")) == QMessageBox.StandardButton.Yes


# ======================================================================= transplantar golpe

class TransplantDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx, self.src, self.info = ctx, None, None
        self.setWindowTitle(tr("Transplant a move from another character"))
        self.resize(720, 640)
        row, self.path = _src_row(self, self.load_src)
        self.state = QComboBox()
        self.state.currentIndexChanged.connect(self.analyze)
        self.offs = {k: _spin(-99999, 99999, 0) for k in ("states", "anims", "sprites", "sounds")}
        for s in self.offs.values():
            s.valueChanged.connect(self.show_plan)
        self.with_cmd = QCheckBox(tr("Bring the command from the .cmd and the entry in [Statedef -1]"))
        self.with_cmd.setChecked(True)
        self.plan = QPlainTextEdit()
        self.plan.setReadOnly(True)
        self.plan.setPlaceholderText(tr("Pick the source character and the move: everything that comes along shows up here (states, animations, sprites, sounds and command)."))
        auto = QPushButton(tr("Suggest free offsets"))
        auto.setAutoDefault(False)
        auto.clicked.connect(self.suggest)
        f = QFormLayout()
        f.addRow(tr("Source character"), row)
        f.addRow(tr("Move (state)"), self.state)
        labels = {"states": tr("State offset"), "anims": tr("Animation offset"),
                  "sprites": tr("Sprite group offset"), "sounds": tr("Sound group offset")}
        for k, s in self.offs.items():
            f.addRow(labels[k], s)
        f.addRow(auto)
        f.addRow(self.with_cmd)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setText(tr("Transplant"))
        self.ok_btn.setEnabled(False)       # só com um personagem de origem carregado
        bb.accepted.connect(self.run)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)
        lay.addLayout(f)
        lay.addWidget(field_label(tr("What comes along")))
        lay.addWidget(self.plan, 1)
        lay.addWidget(bb)
        others = [c for c in ctx.projects if c is not ctx.char]
        if others:
            self.path.setText(str(others[-1].def_path))
            self.load_src(str(others[-1].def_path))

    def load_src(self, path: str) -> None:
        self.state.blockSignals(True)
        self.state.clear()
        try:
            self.src = project.load(path, self.ctx.settings.get("mugen_dir") or None)
            from ..tools import simulate
            try:
                moves = set(simulate.move_states(self.src))
            except Exception:  # noqa: BLE001
                moves = set()
            for n in sorted(self.src.all_statedefs(include_common=False)):
                if n >= 0:
                    self.state.addItem(f"{n}" + (tr("  ★ move with a command") if n in moves else ""), n)
            first = next((i for i in range(self.state.count()) if self.state.itemData(i) in moves), 0)
            self.state.setCurrentIndex(first)
        except (ProjectError, OSError, ValueError) as e:
            self.plan.setPlainText(tr("Could not open: {0}").format(e))
            self.src = None
        self.state.blockSignals(False)
        self.ok_btn.setEnabled(self.src is not None and self.state.count() > 0)
        self.analyze()

    def analyze(self, *_):
        from ..tools import transplant
        n = self.state.currentData()
        if self.src is None or n is None:
            return
        try:
            self.info = transplant.analyze(self.src, n)
        except Exception as e:  # noqa: BLE001
            self.plan.setPlainText(tr("Could not analyze: {0}").format(e))
            self.info = None
            return
        self.show_plan()

    def _off(self):
        o = [s.value() for s in self.offs.values()]
        return o[0], o[1], o[2], o[3]

    def show_plan(self, *_):
        if not self.info:
            return
        from ..tools import transplant
        so, ao, go, sno = self._off()
        i = self.info
        clash = transplant.collisions(self.ctx.char, i, so, ao, go, sno)
        lines = [tr("states: {0}").format(', '.join(str(n + so) for n in i['states'])),
                 tr("animations: {0}").format(', '.join(str(a + ao) for a in i['anims']) or '—'),
                 tr("sprite groups: {0}").format(', '.join(str(g + go) for g in i['sprite_groups']) or '—'),
                 tr("sound groups: {0}").format(', '.join(str(g + sno) for g in i['sound_groups']) or '—'),
                 f"comandos: {', '.join(c.name for c in i['commands']) or '—'}", ""]
        bad = {k: v for k, v in clash.items() if v}
        if bad:
            lines.append(tr("ALREADY EXIST in the current character (will be replaced):"))
            lines += [f"  {k}: {v[:20]}" for k, v in bad.items()]
            lines.append(tr("Use the offsets (or 'Suggest') to avoid overwriting."))
        else:
            lines.append(tr("Nothing collides with what already exists."))
        lines.append("")
        lines.append(tr("The numbers in the copied states (ChangeState, anim, PlaySnd, hitsound, stateno/anim triggers) are changed by the offsets. Check the move in the simulator afterwards."))
        self.plan.setPlainText("\n".join(lines))

    def suggest(self) -> None:
        if not self.info:
            return
        c = self.ctx.char
        i = self.info

        def free(used: set[int], items: list[int], step: int = 100) -> int:
            off = 0
            while any(x + off in used for x in items):
                off += step
            return off
        sds = set(c.all_statedefs(include_common=False))
        vals = [free(sds, i["states"]), free(set(c.anims.actions), i["anims"]),
                free(set(c.sprites.groups()), i["sprite_groups"], 1000),
                free({s.group for s in c.sounds.sounds}, i["sound_groups"], 100)]
        for s, v in zip(self.offs.values(), vals):
            s.setValue(v)

    def run(self) -> None:
        if not self.src or self.state.currentData() is None:
            return
        from ..tools import transplant
        so, ao, go, sno = self._off()
        try:
            r = transplant.transplant(self.ctx.char, self.src.def_path, self.state.currentData(), so, ao, go, sno,
                                      self.with_cmd.isChecked())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("Transplant"), f"{type(e).__name__}: {e}")
            return
        for part in ("sprites", "anims", "sounds", "states", "commands"):
            self.ctx.changed(part)
        self.ctx.reset_cache("constants")
        self.ctx.status.emit(tr("Move transplanted: states {0}, {1} animation(s)").format(r['states'], len(r['anims'])))
        self.accept()


# ======================================================================= testador de comandos

class CommandTesterDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(tr("Command tester"))
        self.resize(760, 520)
        self.input = QLineEdit()
        self.input.setPlaceholderText(tr("e.g. 2 3 6 a   ·   6 2 3 x+y   ·   4*40 6 a   ·   a*30 ~a"))
        self.input.returnPressed.connect(self.run)
        presets = QComboBox()
        for label, seq in [(tr("Quarter circle forward + A (236A)"), "2 3 6 a"), (tr("Quarter circle back + A (214A)"), "2 1 4 a"),
                           (tr("Dragon punch + X (623X)"), "6 2 3 x"), (tr("Double quarter circle + X"), "2 3 6 2 3 6 x"),
                           (tr("Forward dash"), "6 5 6"), (tr("Charge back, forward + A"), "4*40 6 a"),
                           (tr("Hold A and release"), "a*30 ~a")]:
            presets.addItem(label, seq)
        presets.activated.connect(lambda i: (self.input.setText(presets.itemData(i)), self.run()))
        go = QPushButton(tr("Test"))
        go.setProperty("primary", True)
        go.clicked.connect(self.run)
        top = QHBoxLayout()
        top.addWidget(self.input, 1)
        top.addWidget(presets)
        top.addWidget(go)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Command"), tr("Sequence"), tr("Window (time)"), tr("Leads to state")])
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        empty = QTreeWidgetItem([tr("Type a sequence and press Enter, or pick an example on the side.")])
        empty.setFlags(Qt.ItemFlag.NoItemFlags)
        self.tree.addTopLevelItem(empty)
        self.tree.setFirstColumnSpanned(0, self.tree.rootIndex(), True)
        help_ = QLabel(tr("Numpad: 1↙ 2↓ 3↘ 4← 5 neutral 6→ 7↖ 8↑ 9↗ (always facing forward) · *n holds n ticks · ~a releases · x+y together. The comparison approximates the MUGEN buffer."))
        help_.setObjectName("dim")
        help_.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(help_)
        lay.addWidget(self.tree, 1)

    def run(self) -> None:
        from ..tools import cmdtest
        self.tree.clear()
        try:
            r = cmdtest.test(self.ctx.char, self.input.text())
        except ValueError as e:
            self.tree.addTopLevelItem(QTreeWidgetItem([tr("Invalid input: {0}").format(e)]))
            return
        act = QTreeWidgetItem([tr("Triggered at the end of the input ({0})").format(len(r['active']))])
        early = QTreeWidgetItem([tr("Completed before the end ({0})").format(len(r['earlier']))])
        for parent, rows in ((act, r["active"]), (early, r["earlier"])):
            for row in rows:
                parent.addChild(QTreeWidgetItem([row["name"], row["command"], str(row["time"]),
                                                 ", ".join(map(str, row["states"])) or "—"]))
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(True)
        for i in range(4):
            self.tree.resizeColumnToContents(i)


# ======================================================================= organizador do [Statedef -1]

class EntryOrganizerDialog(QDialog):
    """Ordem das entradas do [Statedef -1]: a primeira que dispara ganha, então supers e
    especiais vêm antes dos normais."""

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(tr("Arrange command priority ([Statedef -1])"))
        self.resize(760, 560)
        self.st = ctx.char.commands.states
        sd = self.st.statedefs.get(-1)
        self.ctrls = list(sd.controllers) if sd else []
        self.list = QListWidget()
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        import re
        for k, c in enumerate(self.ctrls):
            cmds = sorted({n for ex in c.triggers().values() for e in ex
                           for n in re.findall(r'command\s*=\s*"([^"]+)"', e, re.I)})
            val = (c.section.get("value") or "").strip()
            it = QListWidgetItem(f"{c.label or tr("(no label)")}  →  {c.type} {val}" + (f"   [{', '.join(cmds)}]" if cmds else ""))
            it.setData(Qt.ItemDataRole.UserRole, k)
            self.list.addItem(it)
        btns = QHBoxLayout()
        for text, fn in [(tr("Move to top"), lambda: self.move(-10 ** 6)), ("−10", lambda: self.move(-10)), (tr("Move up"), lambda: self.move(-1)),
                         (tr("Move down"), lambda: self.move(1)), ("+10", lambda: self.move(10)), (tr("Move to bottom"), lambda: self.move(10 ** 6))]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        note = QLabel(tr("Drag or use the buttons. The top entry is tested first on every tick."))
        note.setObjectName("dim")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self.list, 1)
        lay.addLayout(btns)
        lay.addWidget(bb)

    def move(self, d: int) -> None:
        r = self.list.currentRow()
        if r < 0:
            return
        it = self.list.takeItem(r)
        nr = max(0, min(self.list.count(), r + d))
        self.list.insertItem(nr, it)
        self.list.setCurrentRow(nr)

    def apply(self) -> None:
        order = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        if order != list(range(len(order))):
            self.st.reorder_controllers(-1, order)
            self.ctx.changed("commands")
            self.ctx.status.emit(tr("Command priority rearranged (Ctrl+S saves)"))
        self.accept()


# ======================================================================= calculadora

class CalculatorDialog(QDialog):
    """Calcula expressões com as regras do MUGEN (divisão inteira entre inteiros, &&, ||, **,
    intervalos [a,b], ceil/floor/abs…)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Calculator (MUGEN rules)"))
        self.resize(520, 360)
        self.input = QLineEdit()
        self.input.setPlaceholderText(tr("e.g. 7/2 · 7.0/2 · 2**10 · (1 && 0) || 1 · 3 = [1,5] · ceil(10*0.75)"))
        self.input.returnPressed.connect(self.calc)
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.setPlaceholderText(tr("Type an expression and press Enter; the results stay here."))
        note = QLabel(tr("Character triggers (Time, Vel X…) have no value here; use the Simulator."))
        note.setObjectName("dim")
        lay = QVBoxLayout(self)
        lay.addWidget(self.input)
        lay.addWidget(self.history, 1)
        lay.addWidget(note)

    def calc(self) -> None:
        from ..tools import simexpr
        t = self.input.text().strip()
        if not t:
            return
        try:
            v = simexpr.evaluate(t, None)
            out = f"{t}  =  {v}"
        except Exception as e:  # noqa: BLE001
            out = tr("{0}  →  error: {1}").format(t, e)
        self.history.appendPlainText(out)
        self.input.selectAll()


# Re-exporta o Assistente Visual de Golpes
from .movewizard import MoveWizardDialog  # noqa: E402
