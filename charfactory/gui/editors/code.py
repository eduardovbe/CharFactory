"""Componente de edição de código CNS/CMD/AIR/DEF: realce, números de linha, autocompletar
e marcas de validação. As páginas que o usam estão em codepages.py."""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QPainter, QPen, QPolygonF, QSyntaxHighlighter, QTextCharFormat,
                           QTextCursor, QTextFormat)
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit, QToolTip, QWidget

from ... import validate
from ...formats import mtext
from ..fonts import code_font
from ..theme import Theme
from ...i18n import tr


# ======================================================================= referências

_ANIM_KEYS = {"anim", "projanim", "projhitanim", "projremanim", "projcancelanim"}
_STATE_KEYS = {"p1stateno", "p2stateno", "stateno"}
_SOUND_KEYS = {"hitsound", "guardsound"}
_ANIM_VALUE = {"changeanim", "changeanim2"}
_STATE_VALUE = {"changestate", "selfstate", "targetstate"}


def reference_at(lines: list[str], index: int) -> tuple[str, int, int] | None:
    """O que a linha `index` referencia: ('anim', n, 0), ('state', n, 0) ou ('sound', g, n).

    Entende `anim =` de statedef/Explod/Helper/Projectile, `value` de ChangeAnim/ChangeState/
    SelfState/TargetState/PlaySnd, `p1stateno`/`p2stateno`/`stateno` e `hitsound`/`guardsound`
    com prefixo S (sons do próprio personagem). Valores calculados (expressões) não contam."""
    if not 0 <= index < len(lines):
        return None
    line = lines[index].split(";", 1)[0]
    h = re.match(r"\s*\[\s*statedef\s+(-?\d+)", line, re.I)
    if h:
        return "state", int(h[1]), 0
    m = re.match(r"\s*([\w.]+)\s*=\s*(.*)$", line)
    if not m:
        return None
    key, value = m[1].lower(), m[2].strip()
    ctype = ""
    start = index
    while start > 0 and not lines[start].lstrip().startswith("["):
        start -= 1
    for ln in lines[start + 1:]:
        if ln.lstrip().startswith("["):
            break
        t = re.match(r"\s*type\s*=\s*(\w+)", ln, re.I)
        if t:
            ctype = t[1].lower()
            break
    number = re.fullmatch(r"(-?\d+)", value)
    if number and (key in _ANIM_KEYS or (key == "value" and ctype in _ANIM_VALUE)):
        return "anim", int(number[1]), 0
    if number and (key in _STATE_KEYS or (key == "value" and ctype in _STATE_VALUE)):
        return "state", int(number[1]), 0
    snd = None
    if key == "value" and ctype == "playsnd":
        snd = re.fullmatch(r"(\d+)\s*,\s*(\d+)", value)
    elif key in _SOUND_KEYS:
        snd = re.fullmatch(r"[sS]\s*(\d+)\s*,\s*(\d+)", value)
    if snd:
        return "sound", int(snd[1]), int(snd[2])
    return None


def reference_doc(word: str) -> str | None:
    """Documentação curta (da gramática oficial) de um controller, trigger ou parâmetro."""
    g = validate.grammar()
    w = word.lower()
    if w in g["controllers"]:
        c = g["controllers"][w]
        req = ", ".join(c["required"]) or tr("none")
        return tr("<b>{0}</b> (controller)<br>{1}<br><i>Required:</i> {2}").format(c['name'], c['description'], req)
    if w in g["triggers"]:
        t = g["triggers"][w]
        return f"<b>{t['name']}</b> (trigger)<br>{t['description']}"
    return None


def _fmt(theme: Theme, key: str, weight: QFont.Weight | None = None) -> QTextCharFormat:
    c = theme.c(key)
    f = QTextCharFormat()
    f.setForeground(QColor(c.hex))
    if weight is not None:
        f.setFontWeight(weight)
    elif c.bold:
        f.setFontWeight(QFont.Weight.DemiBold)
    f.setFontItalic(c.italic)
    return f


# operadores de expressão do MUGEN (os mais longos primeiro)
_OPS = re.compile(r":=|&&|\|\||\^\^|!=|<=|>=|\*\*|[=<>!+\-*/%&|^~]")
_TOKENS = re.compile(r'"[^"]*"?'                      # texto
                     r'|-?\d+\.\d*|-?\.\d+|-?\d+'     # números
                     r'|[A-Za-z_][A-Za-z0-9_.]*'       # identificadores
                     r'|:=|&&|\|\||\^\^|!=|<=|>=|\*\*|[=<>!+\-*/%&|^~]'  # operadores
                     r'|[()\[\],]')                    # pontuação
_FLAG = re.compile(r"^(?:[SCALIHPNUFBV]|[SCA]{1,3}|N[ATP]|S[ATP]|H[ATP]|A[ATP]|HV|VH|MAF|MA|AF|HL|[HLAFDMP+\-]{2,5})$")
_SECTION = re.compile(r"^(\s*)(\[)(\s*)([^\],]*?)(\s*)((?:,[^\]]*)?)(\])")
_AIR_CLSN = re.compile(r"^\s*(clsn[12](?:default)?)(\s*:\s*)(\d+)", re.I)
_AIR_BOX = re.compile(r"^\s*(clsn[12])(\[\s*\d+\s*\])", re.I)
_AIR_WORD = re.compile(r"^\s*(loopstart|interpolate\s+(?:offset|blend|scale|angle))\b", re.I)


class MugenHighlighter(QSyntaxHighlighter):
    """Realce para CNS/ST/CMD/AIR/DEF.

    Seções: colchetes (operador), nome (bloco), números e rótulo após a vírgula.
    Chaves: triggerN/triggerall (key_trigger) e parâmetros (keyword).
    Valores: tipo de controller, triggers, redirecionamentos/variáveis, flags (S, NA, HV…),
    números, textos, operadores e pontuação.
    """

    def __init__(self, doc, theme: Theme):
        super().__init__(doc)
        g = validate.grammar()
        self.controllers = set(g["controllers"])
        self.triggers = set(g["triggers"])
        self.redirects = set(g["redirects"]) | {"const", "gethitvar", "stagevar", "var", "fvar", "sysvar",
                                                 "sysfvar", "ifelse", "cond", "helper", "target", "enemynear"}
        self.set_theme(theme)

    def set_theme(self, theme: Theme) -> None:
        keys = ("comment", "block", "number", "string", "keyword", "keyword_value", "block_type",
                "function", "const", "operator", "key_trigger", "label", "foreground")
        self.f = {k: _fmt(theme, k) for k in keys}
        self.f["block_strong"] = _fmt(theme, "block", QFont.Weight.Bold)
        self.f["type_value"] = _fmt(theme, "block_type", QFont.Weight.DemiBold)
        self.rehighlight()

    def _value(self, text: str, start: int, end: int, key: str | None) -> None:
        body = text[start:end]
        for t in _TOKENS.finditer(body):
            s, tok = start + t.start(), t.group(0)
            low = tok.lower()
            if tok.startswith('"'):
                fmt = self.f["string"]
            elif tok[0].isdigit() or (tok[0] in "-." and len(tok) > 1 and tok[-1].isdigit()):
                fmt = self.f["number"]
            elif tok in "()[],":
                fmt = self.f["operator"]
            elif _OPS.fullmatch(tok):
                fmt = self.f["operator"]
            elif key == "type" and low in self.controllers:
                fmt = self.f["type_value"]
            elif low in self.triggers:
                fmt = self.f["function"]
            elif low in self.redirects:
                fmt = self.f["const"]
            elif _FLAG.match(tok):
                fmt = self.f["keyword_value"]
            else:
                continue
            self.setFormat(s, len(tok), fmt)

    def highlightBlock(self, text: str) -> None:
        body = mtext.strip_comment(text)
        if len(body) < len(text):
            self.setFormat(len(body), len(text) - len(body), self.f["comment"])
        if not body.strip():
            return
        m = _SECTION.match(body)
        if m:  # [Statedef 200]  [State 200, rótulo]  [Begin Action 0]
            o = self.f["operator"]
            self.setFormat(m.start(2), 1, o)
            self.setFormat(m.start(7), 1, o)
            name_start = m.start(4)
            for w in re.finditer(r"-?\d+|[^\s\d-]+", m[4]):
                fmt = self.f["number"] if w.group(0).lstrip("-").isdigit() else self.f["block_strong"]
                self.setFormat(name_start + w.start(), len(w.group(0)), fmt)
            if m[6]:
                self.setFormat(m.start(6), 1, o)
                self.setFormat(m.start(6) + 1, len(m[6]) - 1, self.f["label"])
            return
        m = _AIR_CLSN.match(body)
        if m:
            self.setFormat(m.start(1), len(m[1]), self.f["keyword"])
            self.setFormat(m.start(2), len(m[2]), self.f["operator"])
            self.setFormat(m.start(3), len(m[3]), self.f["number"])
            return
        m = _AIR_WORD.match(body)
        if m:
            self.setFormat(m.start(1), len(m[1]), self.f["type_value"])
            return
        m = re.match(r"^(\s*)([^=:]+?)(\s*)(:=|=)(\s*)", body)
        if m and not body.lstrip()[:1].isdigit():
            key = m[2].strip().lower().replace(" ", "")
            kfmt = self.f["key_trigger"] if (key == "triggerall" or (key.startswith("trigger") and key[7:].isdigit())) \
                else self.f["keyword"]
            box = _AIR_BOX.match(m[2])
            if box:
                self.setFormat(m.start(2), len(box[1]), self.f["keyword"])
                self.setFormat(m.start(2) + box.start(2), len(box[2]), self.f["operator"])
            elif re.match(r"^(f|sys|sysf)?var\s*\(", key):
                self._value(body, m.start(2), m.end(2), None)
            else:
                self.setFormat(m.start(2), len(m[2]), kfmt)
            self.setFormat(m.start(4), len(m[4]), self.f["operator"])
            self._value(body, m.end(0), len(body), key)
            return
        self._value(body, 0, len(body), None)  # linhas sem '=': frames do AIR, sequências de comando


class LineNumbers(QWidget):
    def __init__(self, editor: "CodeEdit"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, e):
        self.editor.paint_gutter(e)

    def mousePressEvent(self, e):
        """Clique na seta da margem dobra ou desdobra a seção."""
        block = self.editor.cursorForPosition(QPoint(0, int(e.position().y()))).block()
        if self.editor.is_header(block):
            self.editor.toggle_fold(block)


_HEADER = re.compile(r"^\s*\[[^\]]+\]")


class CodeEdit(QPlainTextEdit):
    # ('anim'|'state'|'sound', a, b): F12 ou Ctrl+clique sobre uma referência
    definitionRequested = Signal(str, int, int)
    auto_indent = True       # Enter mantém o recuo da linha (Editar › Recuo automático)

    def __init__(self, theme: Theme):
        super().__init__()
        font = code_font(10.5)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setCursorWidth(2)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.gutter = LineNumbers(self)
        gfont = code_font(9.5)
        self.gutter.setFont(gfont)
        self.blockCountChanged.connect(lambda _: self.setViewportMargins(self.gutter_width(), 0, 0, 0))
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self.refresh_selections)
        self.cursorPositionChanged.connect(lambda: self.gutter.update())
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self.highlighter = MugenHighlighter(self.document(), theme)
        self.issue_lines: dict[int, str] = {}  # linha -> severidade
        self.theme = theme
        g = validate.grammar()
        words = sorted({c["name"] for c in g["controllers"].values()} | {t["name"] for t in g["triggers"].values()} |
                       {p for c in g["controllers"].values() for p in c["params"]} |
                       set(g["statedef_params"]) | {"triggerall", "trigger1", "trigger2", "persistent",
                                                    "ignorehitpause", "Statedef", "State"})
        self.completer = QCompleter(QStringListModel(words), self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setWidget(self)
        self.completer.activated.connect(self._insert_completion)
        self.set_theme(theme)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.setStyleSheet(f"QPlainTextEdit {{ background: {theme.c('background').hex}; "
                           f"color: {theme.c('foreground').hex}; border: none; border-radius: 0; padding-left: 6px; "
                           f"selection-background-color: {theme.ui['selection']}; selection-color: {theme.c('foreground').hex}; }}")
        self.highlighter.set_theme(theme)
        self.refresh_selections()
        self.gutter.update()

    # ------------------------------------------------------------ margem

    def gutter_width(self) -> int:
        # ponto de problema | número da linha | seta de dobrar (a seta não encosta mais no número)
        return 32 + self.gutter.fontMetrics().horizontalAdvance("9") * len(str(max(1, self.blockCount())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def paint_gutter(self, e):
        p = QPainter(self.gutter)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(e.rect(), QColor(self.theme.c("background").hex))
        dim = QColor(self.theme.c("comment").hex)
        dim.setAlpha(150)
        bright = QColor(self.theme.c("foreground").hex)
        current = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        w = self.gutter.width()
        while block.isValid():
            geo = self.blockBoundingGeometry(block).translated(self.contentOffset())
            top, h = round(geo.top()), round(geo.height())
            if top > e.rect().bottom():
                break
            if not block.isVisible():
                block = block.next()
                continue
            n = block.blockNumber()
            sev = self.issue_lines.get(n + 1)
            if sev:
                c = QColor(self.theme.ui["error" if sev == "error" else "warning"])
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawRoundedRect(3, top + h / 2 - 3.5, 7, 7, 3.5, 3.5)
            if self.is_header(block):
                # seta de linha (o mesmo desenho das árvores), à direita do número
                folded = block.next().isValid() and not block.next().isVisible()
                pen = QPen(dim, 1.5)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                cx, cy = w - 9, top + h / 2
                pts = [QPointF(cx - 1.5, cy - 3.5), QPointF(cx + 2, cy), QPointF(cx - 1.5, cy + 3.5)] if folded else \
                      [QPointF(cx - 3.5, cy - 1.5), QPointF(cx, cy + 2), QPointF(cx + 3.5, cy - 1.5)]
                p.drawPolyline(QPolygonF(pts))
            p.setPen(bright if n == current else dim)
            p.drawText(0, top, w - 17, h, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(n + 1))
            block = block.next()
        p.end()

    # ------------------------------------------------------------ dobrar blocos

    @staticmethod
    def is_header(block) -> bool:
        return block.isValid() and bool(_HEADER.match(block.text())) and not block.text().lstrip().startswith(";")

    def _fold_range(self, header):
        """Blocos de uma seção: do seguinte ao cabeçalho até antes do próximo cabeçalho."""
        out, b = [], header.next()
        while b.isValid() and not self.is_header(b):
            out.append(b)
            b = b.next()
        while out and not out[-1].text().strip():   # a linha vazia entre seções fica à mostra
            out.pop()
        return out

    def _set_folded(self, header, folded: bool) -> None:
        blocks = self._fold_range(header)
        for b in blocks:
            b.setVisible(not folded)
        if blocks:
            start, end = blocks[0].position(), blocks[-1].position() + blocks[-1].length()
            self.document().markContentsDirty(start, end - start)

    def _header_of(self, block):
        while block.isValid() and not self.is_header(block):
            block = block.previous()
        return block

    def _refold(self) -> None:
        c = self.textCursor()
        if not c.block().isVisible():
            c = QTextCursor(self._header_of(c.block()))
            self.setTextCursor(c)
        self.viewport().update()
        self.gutter.update()
        self.ensureCursorVisible()

    def toggle_fold(self, block=None) -> None:
        header = self._header_of(block if block is not None else self.textCursor().block())
        if not header.isValid():
            return
        nxt = header.next()
        self._set_folded(header, nxt.isValid() and nxt.isVisible())
        self._refold()

    def fold_all(self) -> None:
        b = self.document().firstBlock()
        while b.isValid():
            if self.is_header(b):
                self._set_folded(b, True)
            b = b.next()
        self._refold()

    def unfold_all(self) -> None:
        b = self.document().firstBlock()
        while b.isValid():
            b.setVisible(True)
            b = b.next()
        self.document().markContentsDirty(0, self.document().characterCount())
        self._refold()

    def goto_line(self, line: int) -> None:
        block = self.document().findBlockByNumber(max(0, line - 1))
        if block.isValid() and not block.isVisible():
            self._set_folded(self._header_of(block), False)
            self._refold()
        c = QTextCursor(block)
        self.setTextCursor(c)
        self.centerCursor()
        self.setFocus()

    # ------------------------------------------------------------ realces

    def refresh_selections(self) -> None:
        sels = []
        cur = QTextEdit.ExtraSelection()
        cur.format.setBackground(QColor(self.theme.c("line").hex))
        cur.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cur.cursor = self.textCursor()
        cur.cursor.clearSelection()
        sels.append(cur)
        sels += self._bracket_selections()
        for line, sev in self.issue_lines.items():
            block = self.document().findBlockByNumber(line - 1)
            if not block.isValid():
                continue
            s = QTextEdit.ExtraSelection()
            s.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            s.format.setUnderlineColor(QColor(self.theme.ui["error" if sev == "error" else "warning"]))
            c = QTextCursor(block)
            c.select(QTextCursor.SelectionType.LineUnderCursor)
            s.cursor = c
            sels.append(s)
        self.setExtraSelections(sels)

    def _bracket_selections(self) -> list:
        """Destaca o par de parênteses/colchetes junto ao cursor (expressões ficam numa linha)."""
        c = self.textCursor()
        block = c.block()
        text, col = block.text(), c.positionInBlock()
        pairs = {"(": ")]", "[": ")]", ")": "([", "]": "(["}  # intervalos (a,b] são válidos
        for pos in (col - 1, col):
            if 0 <= pos < len(text) and text[pos] in "()[]":
                ch = text[pos]
                step = 1 if ch in "([" else -1
                depth, i = 0, pos
                while 0 <= i < len(text):
                    t = text[i]
                    if t in "([":
                        depth += step
                    elif t in ")]":
                        depth -= step
                    if depth == 0:
                        break
                    i += step
                if not (0 <= i < len(text)) or text[i] not in pairs[ch]:
                    i = None
                out = []
                for k in (pos, i):
                    if k is None:
                        continue
                    sel = QTextEdit.ExtraSelection()
                    color = QColor(self.theme.c("paren").hex if i is not None else self.theme.ui["error"])
                    bg = QColor(color)
                    bg.setAlpha(55)
                    sel.format.setBackground(bg)
                    sel.format.setForeground(color)
                    sel.format.setFontWeight(QFont.Weight.Bold)
                    cur = QTextCursor(block)
                    cur.setPosition(block.position() + k)
                    cur.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
                    sel.cursor = cur
                    out.append(sel)
                return out
        return []

    def set_issues(self, issues: dict[int, str]) -> None:
        self.issue_lines = issues
        self.refresh_selections()
        self.gutter.update()

    # ------------------------------------------------------------ autocompletar

    def _word_prefix(self) -> str:
        c = self.textCursor()
        c.select(QTextCursor.SelectionType.WordUnderCursor)
        return c.selectedText()

    def _insert_completion(self, word: str) -> None:
        c = self.textCursor()
        c.select(QTextCursor.SelectionType.WordUnderCursor)
        c.insertText(word)
        self.setTextCursor(c)

    # ------------------------------------------------------------ navegação e documentação

    def reference_under_cursor(self, cursor: QTextCursor | None = None):
        c = cursor or self.textCursor()
        return reference_at(self.toPlainText().split("\n"), c.blockNumber())

    def go_to_definition(self, cursor: QTextCursor | None = None) -> bool:
        ref = self.reference_under_cursor(cursor)
        if ref:
            self.definitionRequested.emit(*ref)
        return ref is not None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            c = self.cursorForPosition(e.position().toPoint())
            if self.go_to_definition(c):
                return
        super().mousePressEvent(e)

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            c = self.cursorForPosition(self.viewport().mapFromGlobal(e.globalPos()))
            c.select(QTextCursor.SelectionType.WordUnderCursor)
            doc = reference_doc(c.selectedText())
            ref = self.reference_under_cursor(c)
            if ref and not doc:
                kind = {"anim": tr("Animation"), "state": tr("State"), "sound": tr("Sound")}[ref[0]]
                doc = tr("{0} {1}{2}<br><i>F12 or Ctrl+click to open</i>").format(kind, ref[1], f',{ref[2]}' if ref[0] == 'sound' else '')
            if doc:
                QToolTip.showText(e.globalPos(), f"<div style='max-width:380px'>{doc}</div>", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_F12:
            self.go_to_definition()
            return
        popup = self.completer.popup()
        if popup.isVisible() and e.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab, Qt.Key.Key_Escape):
            e.ignore()
            return
        if e.key() == Qt.Key.Key_Space and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._show_completer(force=True)
            return
        if self.auto_indent and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not e.modifiers():
            line = self.textCursor().block().text()
            indent = line[:len(line) - len(line.lstrip(" \t"))]
            self.textCursor().insertText("\n" + indent)
            self.ensureCursorVisible()
            return
        super().keyPressEvent(e)
        if e.text() and (e.text().isalnum() or e.text() == "."):
            self._show_completer()
        else:
            popup.hide()

    def _show_completer(self, force: bool = False) -> None:
        prefix = self._word_prefix()
        if len(prefix) < 3 and not force:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(prefix)
        rect = self.cursorRect()
        rect.setWidth(260)
        self.completer.complete(rect)
