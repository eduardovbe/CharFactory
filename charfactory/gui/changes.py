"""Live file versions, recovery snapshots and the pending changes panel."""
from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QDialogButtonBox,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ..formats import air, cmd, cns, mtext, sff, snd, zssread
from ..i18n import tr
from ..project import ProjectError
from .context import FILE_PARTS
from .fonts import code_font


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="mugen8")


def text_encoding(data):
    try:
        data.decode("utf-8")
        return "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


@dataclass
class FileChange:
    path: Path
    part: str
    data: bytes
    buffer: bool = False
    model_data: bytes | None = None

    def different(self, disk):
        if self.path.suffix.lower() in (".sff", ".snd"):
            return self.data != disk
        def normalized(data):
            return decode(data).replace("\r\n", "\n").replace("\r", "\n")
        return normalized(self.data) != normalized(disk) or (
            not self.buffer and text_encoding(self.data) != text_encoding(disk))


def versions(char, buffers=()):
    """Serialize changed parts without applying buffers or writing project files.

    External common states are included. Temp files use the original serializers,
    so binary versions and original text encodings follow the normal Save path.
    """
    out = {}
    with tempfile.TemporaryDirectory(prefix="charfactory-changes-") as folder:
        def add(path, part, writer):
            if path is None:
                return
            path = Path(path).resolve()
            temp = Path(folder) / (str(len(out)) + path.suffix)
            writer(temp)
            out[path] = FileChange(path, part, temp.read_bytes())

        for part in sorted(char.dirty, key=lambda p: p != "def"):
            if part == "def":
                add(char.def_path, part, char.definition.save)
            elif part == "sprites":
                add(char.path_of("sprite"), part, char.save_sprites)
            elif part == "anims":
                add(char.path_of("anim"), part, char.anims.save)
            elif part == "sounds":
                add(char.path_of("sound"), part, lambda p: snd.save(char.sounds, p))
            elif part == "commands":
                add(char.commands.path, part, char.commands.save)
            elif part == "states":
                for st in char.states:
                    add(st.path, part, st.save)
            elif part == "common_states" and char.common_states:
                add(char.common_states.path, part, char.common_states.save)
        for path, part, text in buffers:
            path = Path(path).resolve()
            model = out.get(path)
            out[path] = FileChange(path, part, text.encode("utf-8"), True, model.data if model else None)
    return list(out.values())


def pending(char, buffers=()):
    out = []
    for change in versions(char, buffers):
        disk = change.path.read_bytes() if change.path.exists() else b""
        if change.different(disk):
            out.append(change)
    return out


def editor_buffers(win):
    mapping = {"air": "anims", "cmd": "commands"}
    return [(f["path"], mapping.get(f["kind"], f["kind"]), f["edit"].toPlainText())
            for page in win.code_pages for f in page.files if f["edit"].document().isModified()]


def diff_text(change, disk=None):
    disk = change.path.read_bytes() if disk is None and change.path.exists() else (disk or b"")
    if change.path.suffix.lower() in (".sff", ".snd"):
        return tr("Binary file: {0:,} saved bytes → {1:,} pending bytes").format(len(disk), len(change.data)) + \
            f"\n\nSHA-256 ({tr('saved')}): {digest(disk)}\nSHA-256 ({tr('pending')}): {digest(change.data)}"
    lines = difflib.unified_diff(decode(disk).splitlines(), decode(change.data).splitlines(),
                                 tr("saved") + "/" + change.path.name,
                                 tr("pending") + "/" + change.path.name, lineterm="")
    return "\n".join(lines) or (tr("Text is unchanged; encoding or line endings differ.")
                                 if change.data != disk else tr("No differences."))


def replace_model_file(ctx, change):
    """Replace a single model file in memory; callers manage editor buffers separately."""
    c = ctx.char
    path, part = change.path.resolve(), change.part
    h = ctx.undo_stack()
    if h:
        h.cut(part)
        h.prime(part)
    with tempfile.TemporaryDirectory(prefix="charfactory-restore-") as folder:
        temp = Path(folder) / path.name
        temp.write_bytes(change.data)
        if part == "def":
            doc = mtext.load(temp)
            old_files = c.definition.find("files")
            new_files = doc.find("files")
            if (old_files.text() if old_files else "") != (new_files.text() if new_files else ""):
                for name in (*FILE_PARTS.values(), "constants"):
                    c.__dict__.pop(name, None)
            c.definition = doc
        elif part == "sprites":
            c.__dict__["sprites"] = sff.load(temp)
        elif part == "sounds":
            c.__dict__["sounds"] = snd.load(temp)
        elif part == "anims":
            c.__dict__["anims"] = air.load(temp)
        elif part == "commands":
            c.__dict__["commands"] = cmd.Commands(mtext.load(temp), path)
        elif part in ("states", "common_states"):
            if path.suffix.lower() == ".zss":
                state = zssread.load(temp, c.zss_functions)
                state.path = path
            else:
                state = cns.States(mtext.load(temp), path)
            if part == "common_states":
                c.__dict__["common_states"] = state
            else:
                i = next(i for i, st in enumerate(c.states) if st.path.resolve() == path)
                c.states[i] = state
            c.__dict__.pop("constants", None)
        else:
            raise ValueError(part)
    ctx.changed(part)


class RecoveryStore:
    """A manifest is published only after a complete snapshot has been written."""
    def __init__(self, char):
        self.char = char
        self.root = char.dir / "_backups" / "autosave"
        self.manifest = self.root / "recovery.json"

    def read(self):
        try:
            value = json.loads(self.manifest.read_text("utf-8"))
            return value if isinstance(value, dict) and value.get("project") == str(self.char.def_path) else {}
        except (OSError, ValueError):
            return {}

    def write(self, changes):
        generation = uuid.uuid4().hex
        snapshots = (self.root / "snapshots").resolve()
        if not snapshots.is_relative_to(self.root.resolve()):
            raise OSError("Snapshot folder is outside the automatic backup folder")
        folder = self.root / "snapshots" / generation
        folder.mkdir(parents=True)
        entries = []
        for i, change in enumerate(changes):
            payload = folder / (str(i) + change.path.suffix)
            payload.write_bytes(change.data)
            entries.append({"source": str(change.path), "part": change.part, "buffer": change.buffer,
                            "payload": payload.relative_to(self.root).as_posix(), "hash": digest(change.data)})
            if change.model_data is not None:
                model = folder / (str(i) + ".model" + change.path.suffix)
                model.write_bytes(change.model_data)
                entries[-1].update(model_payload=model.relative_to(self.root).as_posix(), model_hash=digest(change.model_data))
        manifest = {"project": str(self.char.def_path), "id": generation,
                    "created_ns": time.time_ns(), "files": entries}
        self._publish(manifest)
        # Keep the current and previous complete generations. Resolve targets before deleting.
        folders = sorted(snapshots.iterdir(), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for old in folders[2:]:
            target = old.resolve()
            if old.is_dir() and not old.is_symlink() and target.parent == snapshots and target != folder.resolve():
                shutil.rmtree(target)

    def _publish(self, value):
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.root / ("manifest-" + uuid.uuid4().hex + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), "utf-8")
        temp.replace(self.manifest)

    def dismiss(self):
        manifest = self.read()
        if manifest:
            manifest["dismissed"] = True
            self._publish(manifest)
        elif self.root.is_dir():
            self._publish({"project": str(self.char.def_path), "dismissed": True, "files": []})

    def candidates(self):
        manifest = self.read()
        if not manifest:
            return self.legacy_candidates()
        if manifest.get("dismissed"):
            return []
        changes = []
        created = manifest.get("created_ns", 0)
        if not isinstance(created, int):
            return []
        files = manifest.get("files", [])
        if not isinstance(files, list):
            return []
        for entry in files:
            try:
                payload = (self.root / entry["payload"]).resolve()
                if not payload.is_relative_to(self.root.resolve()):
                    continue
                data = payload.read_bytes()
                if digest(data) != entry["hash"]:
                    continue
                path = Path(entry["source"]).resolve()
                if path.exists() and path.stat().st_mtime_ns >= created:
                    continue
                change = FileChange(path, entry["part"], data, bool(entry.get("buffer")))
                if entry.get("model_payload"):
                    model = (self.root / entry["model_payload"]).resolve()
                    if not model.is_relative_to(self.root.resolve()):
                        continue
                    change.model_data = model.read_bytes()
                    if digest(change.model_data) != entry["model_hash"]:
                        continue
                disk = path.read_bytes() if path.exists() else b""
                if change.different(disk):
                    changes.append(change)
            except (OSError, KeyError, TypeError, ValueError):
                continue
        return changes

    def legacy_candidates(self):
        if not self.root.is_dir():
            return []
        c = self.char
        sources = [(c.def_path, "def"), (c.path_of("sprite"), "sprites"), (c.path_of("anim"), "anims"),
                   (c.path_of("sound"), "sounds"), (c.path_of("cmd"), "commands")]
        sources.extend((st.path, "states") for st in c.states)
        if c.common_states:
            sources.append((c.common_states.path, "common_states"))
        changes = []
        for path, part in sources:
            if path is None or not path.resolve().is_relative_to(c.dir.resolve()):
                continue
            backup = self.root / path.resolve().relative_to(c.dir.resolve())
            if backup.is_file() and (not path.exists() or backup.stat().st_mtime_ns > path.stat().st_mtime_ns):
                change = FileChange(path.resolve(), part, backup.read_bytes(), path.suffix.lower() not in (".sff", ".snd"))
                if change.different(path.read_bytes() if path.exists() else b""):
                    changes.append(change)
        return changes


class RecoveryDialog(QDialog):
    def __init__(self, char, changes, parent):
        super().__init__(parent)
        self.setWindowTitle(tr("Recover unsaved changes — {0}").format(char.name))
        self.resize(960, 620)
        self.list = QListWidget()
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(code_font())
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.changes = changes
        for change in changes:
            it = QListWidgetItem(change.path.name)
            it.setToolTip(str(change.path))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(it)
        self.list.currentRowChanged.connect(lambda i: self.preview.setPlainText(diff_text(changes[i])) if i >= 0 else None)
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(self.preview)
        split.setSizes([260, 700])
        info = QLabel(tr("A newer automatic backup is available. Compare the files and recover selected changes into the editor; Save writes them to disk."))
        info.setWordWrap(True)
        buttons = QDialogButtonBox()
        self.restore = buttons.addButton(tr("Recover selected"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.restore.clicked.connect(self.accept)
        later = buttons.addButton(tr("Later"), QDialogButtonBox.ButtonRole.RejectRole)
        later.clicked.connect(self.reject)
        self.discarded = False
        discard = buttons.addButton(tr("Discard this recovery"), QDialogButtonBox.ButtonRole.DestructiveRole)
        discard.clicked.connect(self.discard)
        self.list.itemChanged.connect(lambda _: self.restore.setEnabled(bool(self.selected())))
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(split, 1)
        lay.addWidget(buttons)
        self.list.setCurrentRow(0)

    def selected(self):
        return [c for i, c in enumerate(self.changes) if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def discard(self):
        self.discarded = True
        self.reject()


class ChangesPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.items = []
        self.files = QListWidget()
        self.files.currentRowChanged.connect(self.show_file)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFont(code_font())
        self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.revert = QPushButton(tr("Revert selected file"))
        self.revert.clicked.connect(self.revert_selected)
        refresh = QPushButton(tr("Refresh"))
        refresh.clicked.connect(self.refresh)
        controls = QHBoxLayout()
        controls.addWidget(self.summary, 1)
        controls.addWidget(refresh)
        controls.addWidget(self.revert)
        split = QSplitter()
        split.addWidget(self.files)
        split.addWidget(self.diff)
        split.setSizes([280, 850])
        files = QWidget()
        fl = QVBoxLayout(files)
        fl.addLayout(controls)
        fl.addWidget(split, 1)
        self.tasks = QListWidget()
        self.tasks.currentRowChanged.connect(self.show_task)
        self.task_detail = QPlainTextEdit()
        self.task_detail.setReadOnly(True)
        self.undo_task = QPushButton(tr("Undo task"))
        self.undo_task.clicked.connect(self.undo_selected_task)
        ts = QSplitter()
        ts.addWidget(self.tasks)
        ts.addWidget(self.task_detail)
        ts.setSizes([280, 850])
        tasks = QWidget()
        tl = QVBoxLayout(tasks)
        tl.addWidget(ts, 1)
        tl.addWidget(self.undo_task, alignment=Qt.AlignmentFlag.AlignRight)
        self.tabs = QTabWidget()
        self.tabs.addTab(files, tr("Files"))
        self.tabs.addTab(tasks, tr("AI tasks"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tabs)
        self.timer = QTimer(self, singleShot=True, interval=350)
        self.timer.timeout.connect(self.refresh)
        win.ctx.modelChanged.connect(self.schedule)
        win.ctx.characterChanged.connect(self.schedule)

    def schedule(self, *_):
        if self.isVisible():
            self.timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        self.timer.stop()
        selected = self.items[self.files.currentRow()].path if 0 <= self.files.currentRow() < len(self.items) else None
        self.files.blockSignals(True)
        self.files.clear()
        try:
            self.items = pending(self.win.ctx.char, editor_buffers(self.win)) if self.win.ctx.char else []
            self.summary.setText(tr("{0} file(s) differ from the saved version.").format(len(self.items)))
        except (OSError, ValueError, ProjectError) as e:
            self.items = []
            self.summary.setText(str(e))
        for c in self.items:
            it = QListWidgetItem(c.path.name + (" · " + tr("unapplied text") if c.buffer else ""))
            it.setToolTip(str(c.path))
            self.files.addItem(it)
        self.files.blockSignals(False)
        index = next((i for i, c in enumerate(self.items) if c.path == selected), 0)
        self.files.setCurrentRow(index if self.items else -1)
        self.show_file(self.files.currentRow())
        self.refresh_tasks()

    def show_file(self, row):
        self.revert.setEnabled(0 <= row < len(self.items) and self.items[row].path.is_file())
        self.diff.setPlainText(diff_text(self.items[row]) if 0 <= row < len(self.items) else tr("No pending changes."))

    def revert_selected(self):
        row = self.files.currentRow()
        if not 0 <= row < len(self.items):
            return
        change = self.items[row]
        if QMessageBox.question(self, tr("Revert selected file"),
                                tr("Replace pending changes in {0} with its saved version?").format(change.path.name)) != QMessageBox.StandardButton.Yes:
            return
        self.win.revert_pending_file(change)
        self.refresh()

    def refresh_tasks(self):
        host = getattr(self.win, "ai_host", None)
        tasks = host.tasks if host else []
        selected = self.tasks.currentRow()
        self.tasks.blockSignals(True)
        self.tasks.clear()
        for task in reversed(tasks):
            it = QListWidgetItem(task["title"])
            it.setData(Qt.ItemDataRole.UserRole, task["id"])
            self.tasks.addItem(it)
        self.tasks.blockSignals(False)
        self.tasks.setCurrentRow(min(max(0, selected), len(tasks) - 1))
        self.show_task(self.tasks.currentRow())

    def selected_task(self):
        it = self.tasks.currentItem()
        return next((t for t in self.win.ai_host.tasks if it and t["id"] == it.data(Qt.ItemDataRole.UserRole)), None)

    def show_task(self, _):
        from .aipanel import PART_NAMES
        task = self.selected_task()
        self.undo_task.setEnabled(bool(task and self.win.ai_host.can_undo_task(task)))
        if task:
            lines = [task["title"], "", tr("Changed parts: {0}").format(
                ", ".join(PART_NAMES.get(p, p) for p in sorted({p for parts in task["parts"].values() for p in parts}))), ""]
            lines.extend(task["steps"])
            lines.extend(["", tr("Undo is available while this task is the latest change in every affected project.")])
            self.task_detail.setPlainText("\n".join(lines))
        else:
            self.task_detail.setPlainText(tr("No AI tasks yet."))

    def undo_selected_task(self):
        task = self.selected_task()
        if task:
            self.win.ai_host.undo_task(task)
            self.refresh()
