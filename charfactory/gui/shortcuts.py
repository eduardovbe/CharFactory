"""Persistent, locale-independent bindings for menu and contextual Qt shortcuts."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QDialog, QDialogButtonBox,
                               QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import i18n
from ..i18n import tr

PORTABLE = QKeySequence.SequenceFormat.PortableText


@dataclass
class Binding:
    key: str
    label: str
    scope: str
    defaults: list
    nodes: list = field(default_factory=list)


class Shortcuts:
    def __init__(self, win):
        self.win = win
        self.reverse = {v: k for k, v in i18n.load_catalog(i18n.language()).items()}
        self.applying = False

    def canonical(self, text):
        return self.reverse.get(text, text).replace("&", "").split("\t", 1)[0]

    def register_action(self, action, text):
        action.setProperty("cf_binding", "main/" + self.canonical(text))

    def scope(self, node):
        cur = node.parent()
        while cur is not None:
            for page in self.win.pages:
                if cur is page or cur is page.left:
                    return getattr(page, "kind", type(page).__name__)
            cur = cur.parent()
        return "main"

    def collect(self):
        entries = {}
        for node in self.win.findChildren(QWidget) + self.win.findChildren(QAction) + self.win.findChildren(QShortcut):
            if not isinstance(node, (QAction, QShortcut, QAbstractButton)) or node.property("cf_shortcut_ignore"):
                continue
            parent = node if isinstance(node, QWidget) else node.parent()
            while parent is not None and not isinstance(parent, QWidget):
                parent = parent.parent()
            if parent is not None and parent.window() is not self.win:
                continue
            if isinstance(node, QAbstractButton) and getattr(node, "defaultAction", lambda: None)():
                continue
            if isinstance(node, QAction):
                keys = [k.toString(PORTABLE) for k in node.shortcuts()]
                label = node.text().replace("&", "").split("\t", 1)[0]
                label = label or node.toolTip() or ", ".join(keys)
            elif isinstance(node, QShortcut):
                keys = [node.key().toString(PORTABLE)] if not node.key().isEmpty() else []
                label = {"Ctrl+Return": tr("Apply"), "Ctrl+Shift+Space": tr("Controller parameters"),
                         "Alt+G": tr("Go to referenced resource")}.get(keys[0] if keys else "", ", ".join(keys))
                label = node.property("cf_binding_label") or label
            else:
                keys = [node.shortcut().toString(PORTABLE)] if not node.shortcut().isEmpty() else []
                label = node.toolTip() or node.text()
            defaults = node.property("cf_default_keys")
            explicit = node.property("cf_binding")
            if defaults is None:
                if not keys and not explicit:
                    continue
                defaults = keys
                node.setProperty("cf_default_keys", defaults)
                node.setProperty("cf_binding_label", label)
                if hasattr(node, "toolTip"):
                    node.setProperty("cf_original_tip", node.toolTip())
            label = node.property("cf_binding_label") or label
            scope = self.scope(node)
            key = explicit or scope + "/" + self.canonical(label)
            node.setProperty("cf_binding", key)
            binding = entries.setdefault(key, Binding(key, label, scope, list(defaults)))
            binding.nodes.append(node)
        return entries

    def apply(self):
        if self.applying:
            return
        self.applying = True
        try:
            overrides = self.win.ctx.settings.get("shortcuts", {})
            if not isinstance(overrides, dict):
                overrides = {}
            for key, binding in self.collect().items():
                keys = overrides.get(key, binding.defaults)
                if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                    keys = binding.defaults
                sequences = [QKeySequence(k, PORTABLE) for k in keys if k]
                for node in binding.nodes:
                    if isinstance(node, QAction):
                        node.setShortcuts(sequences)
                    elif isinstance(node, QShortcut):
                        node.setKey(sequences[0] if sequences else QKeySequence())
                    else:
                        node.setShortcut(sequences[0] if sequences else QKeySequence())
                    tip = node.property("cf_original_tip")
                    if tip and hasattr(node, "setToolTip"):
                        replacement = ", ".join(keys)
                        for original in binding.defaults:
                            if original:
                                tip = tip.replace("(" + original + ")", "(" + replacement + ")" if replacement else "")
                        node.setToolTip(tip)
        finally:
            self.applying = False

    @staticmethod
    def overlaps(a, b):
        return a == "main" or b == "main" or a == b

    @staticmethod
    def scope_label(scope):
        return {"main": tr("Global"), "def": tr("Definitions"), "cmd": tr("Commands"),
                "states": tr("States"), "air": tr("Animation code"),
                "SpriteEditor": tr("Sprites"), "AnimationEditor": tr("Animations"),
                "SoundEditor": tr("Sounds"), "SimulatorPage": tr("Simulator")}.get(scope, scope)

    def conflicts(self, entries, overrides):
        found = []
        values = list(entries.values())
        for i, a in enumerate(values):
            for b in values[i + 1:]:
                if not self.overlaps(a.scope, b.scope):
                    continue
                # Incumbent aliases can share a key; edits must not introduce ambiguity.
                if a.key not in overrides and b.key not in overrides:
                    continue
                for ka in overrides.get(a.key, a.defaults):
                    for kb in overrides.get(b.key, b.defaults):
                        if ka and kb and (QKeySequence(ka).matches(QKeySequence(kb)) != QKeySequence.SequenceMatch.NoMatch or
                                         QKeySequence(kb).matches(QKeySequence(ka)) != QKeySequence.SequenceMatch.NoMatch):
                            found.append((a, b, ka))
        return found


class ShortcutsDialog(QDialog):
    def __init__(self, manager, parent):
        super().__init__(parent)
        self.manager = manager
        self.entries = manager.collect()
        self.draft = dict(manager.win.ctx.settings.get("shortcuts", {}))
        self.rows = sorted(self.entries.values(), key=lambda b: (b.scope, b.label))
        self.setWindowTitle(tr("Keyboard shortcuts"))
        self.resize(950, 650)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(tr("Search actions or shortcuts…"))
        self.filter.textChanged.connect(self.filter_rows)
        self.table = QTableWidget(len(self.rows), 4)
        self.table.setHorizontalHeaderLabels([tr("Action"), tr("Context"), tr("Shortcut"), tr("Default")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for i, b in enumerate(self.rows):
            for j, value in enumerate([b.label, manager.scope_label(b.scope), ", ".join(self.draft.get(b.key, b.defaults)), ", ".join(b.defaults)]):
                self.table.setItem(i, j, QTableWidgetItem(value))
        self.table.currentCellChanged.connect(self.select)
        self.key = QKeySequenceEdit()
        self.key.editingFinished.connect(self.assign)
        clear = QPushButton(tr("Clear shortcut"))
        clear.clicked.connect(lambda: self.set_binding([]))
        reset = QPushButton(tr("Restore selected default"))
        reset.clicked.connect(self.reset_selected)
        reset_all = QPushButton(tr("Restore all defaults"))
        reset_all.clicked.connect(self.reset_all)
        edit = QHBoxLayout()
        edit.addWidget(self.key, 1)
        edit.addWidget(clear)
        edit.addWidget(reset)
        edit.addWidget(reset_all)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.table, 1)
        lay.addLayout(edit)
        lay.addWidget(self.warning)
        lay.addWidget(buttons)
        self.validate()
        self.table.setCurrentCell(0, 0)

    def select(self, row, *_):
        if 0 <= row < len(self.rows):
            b = self.rows[row]
            values = self.draft.get(b.key, b.defaults)
            self.key.setKeySequence(QKeySequence(values[0]) if values else QKeySequence())

    def assign(self):
        value = self.key.keySequence().toString(PORTABLE)
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            b = self.rows[row]
            current = self.draft.get(b.key, b.defaults)
            if value == (current[0] if current else ""):
                return
        self.set_binding([value] if value else [])

    def set_binding(self, values):
        row = self.table.currentRow()
        if not 0 <= row < len(self.rows):
            return
        b = self.rows[row]
        if values == b.defaults:
            self.draft.pop(b.key, None)
        else:
            self.draft[b.key] = values
        self.table.item(row, 2).setText(", ".join(values))
        self.select(row)
        self.validate()

    def reset_selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            self.set_binding(self.rows[row].defaults)

    def reset_all(self):
        self.draft.clear()
        for row, b in enumerate(self.rows):
            self.table.item(row, 2).setText(", ".join(b.defaults))
        self.select(self.table.currentRow())
        self.validate()

    def validate(self):
        conflicts = self.manager.conflicts(self.entries, self.draft)
        self.save_button.setEnabled(not conflicts)
        self.warning.setText("\n".join(tr("{0}: conflict between {1} and {2}.").format(key, a.label, b.label)
                                         for a, b, key in conflicts[:3]))

    def filter_rows(self, query):
        for row, binding in enumerate(self.rows):
            text = " ".join(self.table.item(row, col).text() for col in range(4))
            self.table.setRowHidden(row, query.casefold() not in text.casefold())
        if self.table.currentRow() < 0 or self.table.isRowHidden(self.table.currentRow()):
            visible = next((row for row in range(len(self.rows)) if not self.table.isRowHidden(row)), -1)
            self.table.setCurrentCell(visible, 0 if visible >= 0 else -1)

    def save(self):
        self.assign()
        if not self.save_button.isEnabled():
            return
        self.manager.win.ctx.settings["shortcuts"] = self.draft
        self.manager.win.ctx.settings.save()
        self.manager.apply()
        self.accept()
