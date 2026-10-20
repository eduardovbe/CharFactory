"""Buscar e substituir (arquivo atual ou todos os arquivos de texto do projeto) e snippets do usuário."""
from __future__ import annotations

import re

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from .context import AppContext
from ..i18n import tr


class FindReplaceDialog(QDialog):
    """Diálogo não modal, um por janela."""

    @classmethod
    def instance(cls, win) -> "FindReplaceDialog":
        dlg = getattr(win, "_find_dialog", None)
        if dlg is None:
            dlg = win._find_dialog = cls(win)
        return dlg

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("Find and replace"))
        self.setModal(False)
        self.find_edit = QLineEdit()
        self.repl_edit = QLineEdit()
        self.case = QCheckBox(tr("Match case"))
        self.word = QCheckBox(tr("Whole word"))
        self.regex = QCheckBox(tr("Regular expression"))
        self.back = QCheckBox(tr("Backwards"))
        self.wrap = QCheckBox(tr("Wrap around"))
        self.wrap.setChecked(True)
        self.scope = QComboBox()
        self.scope.addItems([tr("Current file"), tr("All project files")])
        self.result = QLabel()
        self.result.setObjectName("dim")
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        g.setColumnMinimumWidth(0, 96)
        self.repl_label = QLabel(tr("Replace with"))
        g.addWidget(QLabel(tr("Find")), 0, 0)
        g.addWidget(self.find_edit, 0, 1, 1, 2)
        g.addWidget(self.repl_label, 1, 0)
        g.addWidget(self.repl_edit, 1, 1, 1, 2)
        g.addWidget(QLabel(tr("Where")), 2, 0)
        g.addWidget(self.scope, 2, 1, 1, 2)
        # opções em duas colunas alinhadas
        for k, cb in enumerate((self.case, self.word, self.regex, self.back, self.wrap)):
            g.addWidget(cb, 3 + k // 2, 1 + k % 2)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(2, 1)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.replace_buttons = []
        for text, fn, repl in [(tr("List all"), self.list_all, False),
                               (tr("Replace"), self.replace_one, True), (tr("Replace all"), self.replace_all, True),
                               (tr("Previous"), lambda: self.find(True), False), (tr("Next"), lambda: self.find(False), False)]:
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            if text == tr("List all"):
                row.addWidget(b)
                row.addStretch(1)
            else:
                row.addWidget(b)
            if repl:
                self.replace_buttons.append(b)
            if text == tr("Next"):
                b.setProperty("primary", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(12)
        lay.addLayout(g)
        lay.addWidget(self.result)
        lay.addLayout(row)
        self.find_edit.returnPressed.connect(lambda: self.find(False))
        self.resize(600, 240)

    def open_for(self, text: str, replace: bool, all_files: bool) -> None:
        if text and " " not in text:
            self.find_edit.setText(text)
        self.scope.setCurrentIndex(1 if all_files else 0)
        # Ctrl+F só busca; Ctrl+H mostra também a linha e os botões de substituir
        for w in (self.repl_label, self.repl_edit, *self.replace_buttons):
            w.setVisible(replace)
        self.setWindowTitle(tr("Replace") if replace else tr("Find"))
        self.adjustSize()
        self.show()
        self.raise_()
        self.activateWindow()
        (self.repl_edit if replace and self.find_edit.text() else self.find_edit).setFocus()
        self.find_edit.selectAll()

    # ------------------------------------------------------------ busca

    def pattern(self) -> re.Pattern | None:
        t = self.find_edit.text()
        if not t:
            return None
        src = t if self.regex.isChecked() else re.escape(t)
        if self.word.isChecked():
            src = rf"\b{src}\b"
        try:
            return re.compile(src, 0 if self.case.isChecked() else re.I)
        except re.error as e:
            self.result.setText(tr("Invalid expression: {0}").format(e))
            return None

    def _qregex(self) -> QRegularExpression | None:
        rx = self.pattern()
        if rx is None:
            return None
        opts = QRegularExpression.PatternOption.NoPatternOption
        if not self.case.isChecked():
            opts |= QRegularExpression.PatternOption.CaseInsensitiveOption
        return QRegularExpression(rx.pattern, opts)

    def editor(self):
        return self.win.current_code_edit()

    def find(self, backwards: bool | None = None) -> bool:
        ed = self.editor()
        rx = self._qregex()
        if not ed or rx is None:
            return False
        back = self.back.isChecked() if backwards is None else (backwards != self.back.isChecked())
        flags = QTextDocument.FindFlag.FindBackward if back else QTextDocument.FindFlag(0)
        cur = ed.document().find(rx, ed.textCursor(), flags)
        if cur.isNull() and self.wrap.isChecked():
            start = QTextCursor(ed.document())
            if back:
                start.movePosition(QTextCursor.MoveOperation.End)
            cur = ed.document().find(rx, start, flags)
        if cur.isNull():
            self.result.setText(tr("Not found"))
            return False
        ed.setTextCursor(cur)
        ed.centerCursor()
        self.result.setText(tr("Line {0}").format(cur.blockNumber() + 1))
        return True

    def _replacement(self, matched: str) -> str:
        rx = self.pattern()
        if self.regex.isChecked() and rx is not None:
            m = rx.fullmatch(matched)
            if m:
                return m.expand(self.repl_edit.text())
        return self.repl_edit.text()

    def replace_one(self) -> None:
        ed = self.editor()
        rx = self.pattern()
        if not ed or rx is None:
            return
        c = ed.textCursor()
        if c.hasSelection() and rx.fullmatch(c.selectedText()):
            c.insertText(self._replacement(c.selectedText()))
        self.find(False)

    def _replace_in(self, ed, rx: re.Pattern) -> int:
        text = ed.toPlainText()
        new, n = rx.subn((lambda m: m.expand(self.repl_edit.text())) if self.regex.isChecked()
                         else (lambda m: self.repl_edit.text()), text)
        if n:
            c = ed.textCursor()
            line = c.blockNumber() + 1
            c.beginEditBlock()             # um Ctrl+Z desfaz a substituição inteira
            c.select(QTextCursor.SelectionType.Document)
            c.insertText(new)
            c.endEditBlock()
            ed.goto_line(min(line, ed.blockCount()))
        return n

    def _all_editors(self):
        for p in self.win.code_pages:
            if not p.files and self.win.ctx.char:
                p.load()
            for f in p.files:
                yield p, f

    def replace_all(self) -> None:
        rx = self.pattern()
        if rx is None:
            return
        if self.scope.currentIndex() == 0:
            ed = self.editor()
            n = self._replace_in(ed, rx) if ed else 0
            self.result.setText(tr("{0} replacement(s)").format(n))
            return
        total, files = 0, 0
        for _, f in self._all_editors():
            n = self._replace_in(f["edit"], rx)
            total += n
            files += bool(n)
        self.result.setText(tr("{0} replacement(s) in {1} file(s); Ctrl+S saves").format(total, files))

    def list_all(self) -> None:
        rx = self.pattern()
        if rx is None:
            return
        win = self.win
        win.search_results.clear()
        n = 0
        pages = win.code_pages if self.scope.currentIndex() == 1 else [win.current_page()]
        for _, f in self._all_editors():
            page = next(p for p in win.code_pages if f in p.files)
            if page not in pages or (self.scope.currentIndex() == 0 and f["edit"] is not self.editor()):
                continue
            for ln, line in enumerate(f["edit"].toPlainText().split("\n"), 1):
                if rx.search(line):
                    it = QListWidgetItem(f"{f['path'].name}:{ln}:  {line.strip()[:140]}")
                    it.setData(Qt.ItemDataRole.UserRole, (win.code_pages.index(page), f["path"].name, ln))
                    win.search_results.addItem(it)
                    n += 1
        win.set_badge(1, n, "badge")
        win.show_bottom(1)
        self.result.setText(tr("{0} line(s) in the Advanced search tab").format(n))


class SnippetsDialog(QDialog):
    """Snippets do usuário (além dos embutidos): {nome: texto}. {n} vira o statedef atual e
    {nome} o nome do personagem."""

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(tr("Code snippets"))
        self.resize(760, 480)
        self.data: dict = dict(ctx.settings.get("snippets") or {})
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self.show_item)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(tr("Pick a snippet on the left or click New. The built-in snippets (Statedef, HitDef, Explod…) are already in the Snippets button."))
        self.text.textChanged.connect(self.store)
        hint = QLabel(tr("{n} = number of the statedef where the cursor is · {name} = character name"))
        hint.setObjectName("dim")
        btns = QHBoxLayout()
        for label, fn in [(tr("New"), self.new), (tr("Rename"), self.rename), (tr("Delete"), self.delete)]:
            b = QPushButton(label)
            b.setAutoDefault(False)
            if label == tr("Delete"):
                b.setObjectName("danger")
            b.clicked.connect(fn)
            btns.addWidget(b)
        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        left.addLayout(btns)
        right = QVBoxLayout()
        right.addWidget(self.text, 1)
        right.addWidget(hint)
        row = QHBoxLayout()
        row.addLayout(left, 1)
        row.addLayout(right, 2)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(row, 1)
        lay.addWidget(bb)
        self.list.addItems(sorted(self.data))
        if self.list.count():
            self.list.setCurrentRow(0)

    def show_item(self, name: str) -> None:
        self.text.blockSignals(True)
        self.text.setPlainText(self.data.get(name, ""))
        self.text.blockSignals(False)

    def store(self) -> None:
        it = self.list.currentItem()
        if it:
            self.data[it.text()] = self.text.toPlainText()

    def new(self) -> None:
        name, ok = QInputDialog.getText(self, tr("New snippet"), tr("Name:"))
        if ok and name.strip() and name.strip() not in self.data:
            self.data[name.strip()] = "[State {n}, ]\ntype = \ntrigger1 = \n"
            self.list.addItem(name.strip())
            self.list.setCurrentRow(self.list.count() - 1)

    def rename(self) -> None:
        it = self.list.currentItem()
        if not it:
            return
        name, ok = QInputDialog.getText(self, tr("Rename snippet"), tr("Name:"), text=it.text())
        if ok and name.strip() and name.strip() not in self.data:
            self.data[name.strip()] = self.data.pop(it.text())
            it.setText(name.strip())

    def delete(self) -> None:
        it = self.list.currentItem()
        if it and QMessageBox.question(self, tr("Delete"), tr("Delete the snippet '{0}'?").format(it.text())) == QMessageBox.StandardButton.Yes:
            self.data.pop(it.text(), None)
            self.list.takeItem(self.list.row(it))

    def save(self) -> None:
        self.ctx.settings["snippets"] = self.data
        self.ctx.settings.save()
        self.accept()
