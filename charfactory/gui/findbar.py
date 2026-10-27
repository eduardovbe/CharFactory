"""Barra de busca embutida nas abas de código (Ctrl+F / Ctrl+H): procura qualquer texto do
arquivo enquanto se digita, marca todas as ocorrências, conta ("3 de 17"), anda com Enter e
Shift+Enter, substitui e manda a lista de todos os arquivos para a Busca avançada."""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout

from ..i18n import tr
from .icons import icon

_ASTRAL = re.compile("[\U00010000-\U0010FFFF]")
HIGHLIGHT_LIMIT = 3000        # mais que isso só conta (marcar tudo deixaria a digitação lenta)


def utf16_offsets(text: str):
    """Posição do Python -> posição do Qt (UTF-16): só difere com caracteres fora do BMP."""
    if not _ASTRAL.search(text):
        return None
    out, extra = [], 0
    for ch in text:
        out.append(extra)
        if ord(ch) > 0xFFFF:
            extra += 1
    out.append(extra)
    return out


class FindBar(QFrame):
    """Uma barra por página de código; segue a aba ativa (set_editor)."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.ed = None
        self.hits: list[tuple[int, int]] = []
        self.current = -1
        self.setObjectName("findBar")
        self._timer = QTimer(self, singleShot=True, interval=150, timeout=lambda: self.search(move=False))

        self.find_edit = QLineEdit()
        self.find_edit.setObjectName("filter")
        self.find_edit.setPlaceholderText(tr("Find in the file (Enter next, Shift+Enter previous)"))
        self.find_edit.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.find_edit.textChanged.connect(lambda _: self.search(move=True))
        self.find_edit.installEventFilter(self)
        self.count = QLabel()
        self.count.setObjectName("dim")
        self.count.setMinimumWidth(96)
        self.toggles = {}
        for key, text, tip in (("case", "Aa", tr("Match case")), ("word", "ab|", tr("Whole word")),
                               ("regex", ".*", tr("Regular expression"))):
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setObjectName("findToggle")
            b.toggled.connect(lambda _on: self.search(move=True))
            self.toggles[key] = b
        self.expand = self._button("chevron_right", tr("Replace (Ctrl+H)"), lambda: self.set_replace(not self.repl_row_visible()))
        prev_b = self._button("up", tr("Previous (Shift+Enter)"), lambda: self.step(-1))
        next_b = self._button("down", tr("Next (Enter)"), lambda: self.step(1))
        all_b = self._button("find_files", tr("List the lines in every file (Advanced search tab)"), self.list_all)
        goto_b = self._button("goto", tr("Go to a statedef, command or animation… (Ctrl+Shift+G)"),
                              lambda: self.page.window().goto_anything())
        close_b = self._button("close", tr("Close (Esc)"), self.close_bar)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        row.addWidget(self.expand)
        row.addWidget(self.find_edit, 1)
        for b in self.toggles.values():
            row.addWidget(b)
        row.addWidget(self.count)
        for b in (prev_b, next_b, all_b, goto_b, close_b):
            row.addWidget(b)

        self.repl_edit = QLineEdit()
        self.repl_edit.setObjectName("filter")
        self.repl_edit.setPlaceholderText(tr("Replace with (Enter replaces and goes to the next)"))
        self.repl_edit.addAction(icon("replace"), QLineEdit.ActionPosition.LeadingPosition)
        self.repl_edit.returnPressed.connect(self.replace_one)
        self.repl_edit.installEventFilter(self)
        one = QToolButton()
        one.setText(tr("Replace"))
        one.clicked.connect(self.replace_one)
        every = QToolButton()
        every.setText(tr("Replace all"))
        every.clicked.connect(self.replace_all)
        self.repl_row = QHBoxLayout()
        self.repl_row.setContentsMargins(26, 0, 0, 0)
        self.repl_row.setSpacing(3)
        self.repl_row.addWidget(self.repl_edit, 1)
        self.repl_row.addWidget(one)
        self.repl_row.addWidget(every)
        self.repl_widgets = (self.repl_edit, one, every)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 5, 8, 5)
        lay.setSpacing(4)
        lay.addLayout(row)
        lay.addLayout(self.repl_row)
        self.set_replace(False)
        self.hide()
        QShortcut(QKeySequence("Escape"), self, self.close_bar,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _button(self, name: str, tip: str, fn) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon(name))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.clicked.connect(fn)
        return b

    # ------------------------------------------------------------ abrir e fechar

    def repl_row_visible(self) -> bool:
        return self.repl_edit.isVisibleTo(self)

    def set_replace(self, on: bool) -> None:
        for w in self.repl_widgets:
            w.setVisible(on)
        self.expand.setIcon(icon("chevron_down" if on else "chevron_right"))

    def open(self, text: str = "", replace: bool = False) -> None:
        self.set_replace(replace or (self.isVisible() and self.repl_row_visible()))
        self.show()
        if text and " " not in text:
            self.find_edit.blockSignals(True)
            self.find_edit.setText(text)
            self.find_edit.blockSignals(False)
        (self.repl_edit if replace and self.find_edit.text() else self.find_edit).setFocus()
        self.find_edit.selectAll()
        self.search(move=True)

    def close_bar(self) -> None:
        self.hide()
        if self.ed is not None:
            self.ed.set_search_hits([], -1)
            self.ed.setFocus()

    def set_editor(self, ed) -> None:
        """A aba ativa mudou: as marcas saem do editor antigo e a busca roda no novo."""
        if ed is self.ed:
            return
        if self.ed is not None:
            try:
                self.ed.set_search_hits([], -1)
                self.ed.document().contentsChanged.disconnect(self._timer.start)
            except (RuntimeError, TypeError):    # editor já apagado ao trocar de personagem
                pass
        self.ed = ed
        if ed is not None:
            ed.document().contentsChanged.connect(self._timer.start)
            if self.isVisible():
                self.search(move=False)

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() == QEvent.Type.KeyPress:
            if e.key() == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if obj is self.find_edit and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.step(-1 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                return True
        return super().eventFilter(obj, e)

    # ------------------------------------------------------------ busca

    def pattern(self) -> re.Pattern | None:
        t = self.find_edit.text()
        if not t:
            return None
        src = t if self.toggles["regex"].isChecked() else re.escape(t)
        if self.toggles["word"].isChecked():
            src = rf"\b{src}\b"
        try:
            return re.compile(src, 0 if self.toggles["case"].isChecked() else re.I)
        except re.error:
            return None

    def search(self, move: bool) -> None:
        """Acha todas as ocorrências; com move, leva o cursor à primeira a partir de onde ele está
        (ao digitar, a busca não pula para longe a cada letra)."""
        if self.ed is None or not self.isVisible():
            return
        rx = self.pattern()
        text = self.ed.toPlainText()
        if rx is None:
            self.hits, self.current = [], -1
            bad = bool(self.find_edit.text()) and self.toggles["regex"].isChecked()
            self.count.setText(tr("invalid expression") if bad else "")
            self.ed.set_search_hits([], -1)
            return
        off = utf16_offsets(text)
        conv = (lambda i: i + off[i]) if off else (lambda i: i)
        self.hits = [(conv(m.start()), conv(m.end()) - conv(m.start())) for m in rx.finditer(text) if m.end() > m.start()]
        if not self.hits:
            self.current = -1
            self.count.setText(tr("no results"))
            self.ed.set_search_hits([], -1)
            return
        c = self.ed.textCursor()
        here = next((k for k, (s, _n) in enumerate(self.hits) if s >= c.selectionStart()), 0)
        if not move:
            # o texto mudou: fica na ocorrência que já estava selecionada, se ainda existir
            sel = (c.selectionStart(), c.selectionEnd() - c.selectionStart())
            here = self.hits.index(sel) if sel in self.hits else here
        self.current = here
        if move:
            self._select(here)
        else:
            self._paint()

    def _paint(self) -> None:
        shown = self.hits[:HIGHLIGHT_LIMIT]
        self.ed.set_search_hits(shown, self.current if self.current < len(shown) else -1)
        self.count.setText(tr("{0} of {1}").format(self.current + 1, len(self.hits)) if self.hits else "")

    def _select(self, k: int) -> None:
        if not self.hits:
            return
        self.current = k % len(self.hits)
        s, n = self.hits[self.current]
        self.ed.reveal(s)
        c = QTextCursor(self.ed.document())
        c.setPosition(s)
        c.setPosition(s + n, QTextCursor.MoveMode.KeepAnchor)
        self.ed.setTextCursor(c)
        self.ed.centerCursor()
        self._paint()

    def step(self, d: int) -> None:
        if not self.hits:
            self.search(move=True)
            return
        c = self.ed.textCursor()
        sel = (c.selectionStart(), c.selectionEnd() - c.selectionStart())
        if sel in self.hits:
            self._select(self.hits.index(sel) + d)
            return
        pos = c.position()     # o cursor andou: a próxima/anterior a partir de onde ele está
        if d > 0:
            k = next((i for i, (s, _n) in enumerate(self.hits) if s >= pos), 0)
        else:
            k = next((i for i in range(len(self.hits) - 1, -1, -1) if self.hits[i][0] < pos), len(self.hits) - 1)
        self._select(k)

    # ------------------------------------------------------------ substituir

    def _replacement(self, matched: str) -> str:
        rx = self.pattern()
        if self.toggles["regex"].isChecked() and rx is not None:
            m = rx.fullmatch(matched)
            if m:
                return m.expand(self.repl_edit.text())
        return self.repl_edit.text()

    def replace_one(self) -> None:
        rx = self.pattern()
        if self.ed is None or rx is None:
            return
        c = self.ed.textCursor()
        if c.hasSelection() and rx.fullmatch(c.selectedText()):
            c.insertText(self._replacement(c.selectedText()))
            self.ed.setTextCursor(c)
        self._timer.stop()
        self.search(move=True)

    def replace_all(self) -> None:
        rx = self.pattern()
        if self.ed is None or rx is None:
            return
        repl = self.repl_edit.text()
        new, n = rx.subn((lambda m: m.expand(repl)) if self.toggles["regex"].isChecked() else (lambda m: repl),
                         self.ed.toPlainText())
        if n:
            line = self.ed.textCursor().blockNumber() + 1
            c = self.ed.textCursor()
            c.beginEditBlock()            # um Ctrl+Z desfaz tudo
            c.select(QTextCursor.SelectionType.Document)
            c.insertText(new)
            c.endEditBlock()
            self.ed.goto_line(min(line, self.ed.blockCount()))
        self._timer.stop()
        self.search(move=False)
        self.count.setText(tr("{0} replaced").format(n))

    def list_all(self) -> None:
        """Mesma busca em todos os arquivos de texto do projeto, na aba Busca avançada."""
        from .findreplace import FindReplaceDialog
        win = self.page.window()
        dlg = FindReplaceDialog.instance(win)
        dlg.find_edit.setText(self.find_edit.text())
        dlg.case.setChecked(self.toggles["case"].isChecked())
        dlg.word.setChecked(self.toggles["word"].isChecked())
        dlg.regex.setChecked(self.toggles["regex"].isChecked())
        dlg.scope.setCurrentIndex(1)
        dlg.list_all()
