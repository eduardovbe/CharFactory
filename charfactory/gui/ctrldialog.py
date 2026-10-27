"""Janela de parâmetros de um state controller: todos os parâmetros do controller (os da doc
do MUGEN e, à parte, os que só o IKEMEN GO aceita), cada um com caixa para entrar no código,
campo com o valor padrão e as opções, e a explicação da doc ao lado. Serve para inserir um
controller novo e para editar o [State] onde está o cursor sem perder triggers e comentários.
"""
from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QSplitter,
                               QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from .. import sctrldocs, validate
from ..i18n import tr
from .widgets import filter_box, hint_label

_TRIGGER = re.compile(r"^\s*trigger(all|\d+)\s*=", re.I)
_KEY = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*=\s*(.*)$")
_STATE_HEADER = re.compile(r"^\s*\[\s*state\s+(-?\d+)\s*(?:,\s*([^\]]*))?\]", re.I)
_ANIM_PARAMS = {"anim", "projanim", "projhitanim", "projremanim", "projcancelanim"}
COMMON = ("persistent", "ignorehitpause")
COMMON_DOC = {
    "persistent": tr("How often the controller can run while its triggers stay true: 1 (default) every "
                     "tick, 0 only once until the state changes, N every N ticks."),
    "ignorehitpause": tr("1 lets the controller run during the hit pause (the freeze when a hit lands). "
                         "Default 0."),
}


@lru_cache(maxsize=1)
def ikemen_params() -> dict:
    p = Path(__file__).resolve().parents[1] / "data" / "ikemen_params.json"
    try:
        return json.loads(p.read_text("utf-8")).get("sctrls", {})
    except (OSError, ValueError):
        return {}


def literal_default(spec: dict) -> str:
    """O padrão da doc quando ele é um valor que dá para escrever (não 'no change')."""
    d = (spec.get("default") or "").strip()
    if not d:
        return ""
    if re.fullmatch(r"[-+\d.,\s]+", d) or any(d.lower() == c[0].lower() for c in spec.get("choices", [])):
        return d
    return d if re.fullmatch(r"[A-Za-z][\w]*", d) and d.lower() not in ("none", "no", "unchanged") else ""


def parse_block(lines: list[str], index: int) -> tuple[int, int, dict] | None:
    """O [State] que contém a linha `index`: (primeira linha, última com texto, dados) ou None.
    dados = {type, statedef, label, triggers (linhas), values {param: valor}, extra (linhas)}."""
    start = index
    while start >= 0 and not lines[start].lstrip().startswith("["):
        start -= 1
    if start < 0:
        return None
    head = _STATE_HEADER.match(lines[start])
    if not head:
        return None
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("["):
        end += 1
    last = end - 1
    while last > start and not lines[last].strip():
        last -= 1
    data = {"type": "", "statedef": int(head[1]), "label": (head[2] or "").strip(), "triggers": [],
            "values": {}, "raw": {}, "extra": []}
    for line in lines[start + 1:last + 1]:
        body = line.split(";", 1)[0]
        m = _KEY.match(body)
        if _TRIGGER.match(body):
            data["triggers"].append(line.strip())
        elif m and m[1].lower() == "type":
            data["type"] = m[2].strip()
        elif m:
            data["values"][m[1].lower()] = m[2].strip()
            data["raw"][m[1].lower()] = line.rstrip()     # a linha como está, com o comentário
        else:
            data["extra"].append(line.rstrip())
    return start, last, data


class _RowFocus(QObject):
    """Foco ou mouse sobre o campo de um parâmetro mostra a ajuda dele."""

    def __init__(self, dialog: "ControllerDialog", name: str):
        super().__init__(dialog)
        self.dialog, self.name = dialog, name

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() in (QEvent.Type.FocusIn, QEvent.Type.Enter):
            self.dialog.show_help(self.name)
        return False


class ControllerDialog(QDialog):
    """`existing`: dados de parse_block quando é para editar um [State] que já existe."""

    def __init__(self, ctx, ctype: str, statedef: int, existing: dict | None = None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.statedef = statedef
        self.existing = existing
        self.result_text = ""
        self.rows: dict[str, dict] = {}
        self._help_name: str | None = None
        self.setWindowTitle(tr("Edit controller") if existing else tr("Add controller"))
        self.resize(1080, 720)
        g = validate.grammar()

        # ------------------------------------------------ topo: qual controller e o que faz
        self.ctype = QComboBox()
        self.ctype.setEditable(True)
        self.ctype.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for c in sorted(g["controllers"].values(), key=lambda c: c["name"].lower()):
            self.ctype.addItem(c["name"], c["name"].lower())
        self.ctype.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.ctype.completer().setCompletionMode(self.ctype.completer().CompletionMode.PopupCompletion)
        self.desc = hint_label("")
        self.desc.setTextFormat(Qt.TextFormat.PlainText)
        self.label = QLineEdit()
        self.label.setPlaceholderText(tr("Name shown in [State {0}, name]").format(statedef))
        self.triggers = QPlainTextEdit()
        self.triggers.setTabChangesFocus(True)
        self.triggers.setFixedHeight(64)
        self.triggers.setToolTip(tr("One trigger per line: triggerall = …, trigger1 = …, trigger2 = …"))
        top = QGridLayout()
        top.setHorizontalSpacing(12)
        top.setVerticalSpacing(8)
        top.addWidget(QLabel(tr("Controller")), 0, 0)
        top.addWidget(self.ctype, 0, 1)
        top.addWidget(QLabel(tr("Name")), 0, 2)
        top.addWidget(self.label, 0, 3)
        top.addWidget(self.desc, 1, 1, 1, 3)
        top.addWidget(QLabel(tr("Triggers")), 2, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self.triggers, 2, 1, 1, 3)
        top.setColumnStretch(1, 2)
        top.setColumnStretch(3, 1)

        # ------------------------------------------------ esquerda: parâmetros
        self.filter = filter_box(tr("Filter parameters…"), self.apply_filter)
        self.only_used = QCheckBox(tr("Only the ones in use"))
        self.only_used.toggled.connect(lambda _: self.apply_filter(self.filter.text()))
        bar = QHBoxLayout()
        bar.setSpacing(14)
        bar.addWidget(self.filter, 1)
        bar.addWidget(self.only_used)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("plain")
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.grid_host)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 10, 0)
        lv.setSpacing(10)
        lv.addLayout(bar)
        lv.addWidget(self.scroll, 1)

        # ------------------------------------------------ direita: ajuda e código gerado
        self.help = QTextBrowser()
        self.help.setOpenLinks(False)
        self.help.anchorClicked.connect(self.on_help_link)
        self.help.document().setDocumentMargin(12)
        from .editors.code import CodeEdit
        self.preview = CodeEdit(ctx.theme)
        self.preview.setReadOnly(True)
        right = QSplitter(Qt.Orientation.Vertical)
        right.setHandleWidth(10)
        for title, w in ((tr("What it does"), self.help), (tr("Code that will be inserted"), self.preview)):
            box = QWidget()
            bv = QVBoxLayout(box)
            bv.setContentsMargins(10, 0, 0, 0)
            bv.setSpacing(6)
            head = QLabel(title)
            head.setObjectName("sectionTitle")
            bv.addWidget(head)
            bv.addWidget(w, 1)
            right.addWidget(box)
        right.setSizes([380, 260])
        split = QSplitter()
        split.setHandleWidth(10)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([620, 460])
        split.setChildrenCollapsible(False)

        # ------------------------------------------------ rodapé
        self.comment_rest = QCheckBox(tr("Also list the unused parameters as comments"))
        self.comment_rest.setToolTip(tr("They go in as ';param = default', ready to uncomment"))
        self.comment_rest.setChecked(bool(ctx.settings.get("ctrl_comment_rest", not existing)))
        self.comment_rest.toggled.connect(self.update_preview)
        self.missing = QLabel()
        self.missing.setObjectName("dim")
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(tr("Update") if existing else tr("Insert"))
        self.ok.setProperty("primary", True)
        bb.accepted.connect(self.accept_text)
        bb.rejected.connect(self.reject)
        foot = QHBoxLayout()
        foot.addWidget(self.comment_rest)
        foot.addWidget(self.missing, 1)
        foot.addWidget(bb)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(16)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addLayout(foot)

        self.label.textChanged.connect(self.update_preview)
        self.triggers.textChanged.connect(self.update_preview)
        if existing:
            self.label.setText(existing["label"])
            self.triggers.setPlainText("\n".join(existing["triggers"]))
        else:
            self.triggers.setPlainText("trigger1 = Time = 0")
        self.ctype.setCurrentIndex(max(0, self.ctype.findData(ctype.lower())))
        self.ctype.currentIndexChanged.connect(lambda _: self.build_rows())
        self.build_rows()
        QTimer.singleShot(0, self._focus_first)

    # ------------------------------------------------------------ parâmetros

    def spec(self) -> dict:
        return validate.grammar()["controllers"].get(self.ctype.currentData() or "", {})

    def _anim_items(self) -> list[tuple[str, int]]:
        c = self.ctx.char
        try:
            anims = c.anims if c else None
            return [(f"{n}  {anims.name_of(n) or ''}".rstrip(), n) for n in anims.numbers()] if anims else []
        except Exception:  # noqa: BLE001 — personagem sem AIR
            return []

    def _editor(self, name: str, spec: dict, value: str):
        choices = [c[0] for c in spec.get("choices", [])]
        typ = (spec.get("type") or "").lower()
        if choices or typ in ("boolean", "bool") or name in _ANIM_PARAMS:
            ed = QComboBox()
            ed.setEditable(True)
            ed.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            # nomes longos de animação não podem alargar a coluna (cortava o "?" da linha)
            ed.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            ed.setMinimumContentsLength(8)
            if name in _ANIM_PARAMS:
                for text, n in self._anim_items():
                    ed.addItem(text, str(n))
                # escolher "200  soco" escreve só o número
                ed.activated.connect(lambda i, e=ed: e.setEditText(e.itemData(i) or e.itemText(i)))
            else:
                for c in choices or ["0", "1"]:
                    ed.addItem(c, c)
            ed.setEditText(value)
            ed.lineEdit().setPlaceholderText(literal_default(spec))
            ed.editTextChanged.connect(lambda _t, n=name: self.on_value(n))
            return ed, ed.lineEdit()
        ed = QLineEdit(value)
        ed.setPlaceholderText(literal_default(spec) or self._hint(spec))
        ed.textChanged.connect(lambda _t, n=name: self.on_value(n))
        return ed, ed

    @staticmethod
    def _hint(spec: dict) -> str:
        s = spec.get("syntax", "")
        rhs = s.split("=", 1)[1] if "=" in s else ""
        return re.sub(r"\s*\([^()]*\)\s*$", "", rhs).strip()

    def build_rows(self) -> None:
        """Monta a grade: obrigatórios, comuns a todo controller, opcionais e os do IKEMEN GO."""
        old = {n: self.value(n) for n, r in self.rows.items() if r["check"].isChecked()}
        values = dict(self.existing["values"]) if self.existing and not self.rows else old
        self.rows = {}
        host = QWidget()
        host.setObjectName("plain")
        self.grid = QGridLayout(host)
        self.grid.setContentsMargins(2, 0, 12, 8)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(2, 1)
        spec = self.spec()
        self.desc.setText(sctrldocs.doc(spec.get("description", "")))
        if not self.existing:
            self.label.setPlaceholderText(spec.get("name", ""))
        params = spec.get("params", {})
        required = list(spec.get("required", [])) + [g[0] for g in spec.get("required_any", [])]
        if spec.get("required_one_of_variants"):     # VarSet e cia.: v ou fv, os dois com value
            required = [p for p in required if p == "value"]
        optional = [p for p in params if p not in required]
        ik = [p for p in ikemen_params().get(self.ctype.currentData() or "", {}).get("params", [])
              if p not in params and p not in COMMON]
        ikdocs = sctrldocs.ikemen_docs().get(self.ctype.currentData() or "", {})
        self._row_n = 0
        groups = [(tr("Required"), required, True), (tr("Optional"), optional, False),
                  (tr("Common to every controller"), list(COMMON), False),
                  (tr("IKEMEN GO only"), ik, False)]
        self.group_heads = []
        for title, names, req in groups:
            if not names:
                continue
            head = QLabel(title)
            head.setObjectName("sectionTitle")
            if self._row_n:
                head.setContentsMargins(0, 12, 0, 2)
            self.grid.addWidget(head, self._row_n, 0, 1, 3)
            self.group_heads.append((head, names))
            self._row_n += 1
            for name in names:
                if name in params:
                    # o que o IKEMEN GO mudou num parâmetro do MUGEN vira uma nota na ajuda
                    pspec = dict(params[name])
                    if ikdocs.get(name, {}).get("doc"):
                        pspec["ikemen_note"] = ikdocs[name]["doc"]
                elif name in COMMON:
                    pspec = {"doc": COMMON_DOC[name], "type": "int", "translated": True}
                else:
                    ik_doc = ikdocs.get(name, {})
                    pspec = {"doc": ik_doc.get("doc", ""), "syntax": ik_doc.get("syntax", ""), "ikemen": True}
                self._add_row(name, pspec, req, values.get(name))
        self.grid.setRowStretch(self._row_n, 1)
        self.scroll.setWidget(host)
        self.grid_host = host
        self.apply_filter(self.filter.text())
        self.show_help(None)
        self.update_preview()

    def _add_row(self, name: str, spec: dict, required: bool, value: str | None) -> None:
        check = QCheckBox()
        check.setChecked(required or value is not None)
        check.toggled.connect(lambda _on: self.update_preview())
        title = QLabel(f"<b>{name}</b>" + (" <span style='color:#f85149'>*</span>" if required else "")
                       + (f" <i style='color:#d29922'>{tr('(deprecated)')}</i>" if spec.get("deprecated") else ""))
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setToolTip(spec.get("syntax") or name)
        ed, line = self._editor(name, spec, value or "")
        more = QToolButton()
        more.setText("?")
        more.setAutoRaise(True)
        more.setToolTip(tr("What this parameter does"))
        more.clicked.connect(lambda _=False, n=name: self.show_help(n))
        r = self._row_n
        self.grid.addWidget(check, r, 0)
        self.grid.addWidget(title, r, 1)
        self.grid.addWidget(ed, r, 2)
        self.grid.addWidget(more, r, 3)
        f = _RowFocus(self, name)
        for w in (ed, line, title, check):
            w.installEventFilter(f)
        title.mousePressEvent = lambda _e, c=check: c.toggle()
        self.rows[name] = {"check": check, "edit": ed, "line": line, "spec": spec, "required": required,
                           "widgets": (check, title, ed, more)}
        self._row_n += 1

    def value(self, name: str) -> str:
        r = self.rows[name]
        ed = r["edit"]
        return (ed.currentText() if isinstance(ed, QComboBox) else ed.text()).strip()

    def on_value(self, name: str) -> None:
        r = self.rows.get(name)
        if r is None:
            return
        if self.value(name) and not r["check"].isChecked():
            r["check"].setChecked(True)     # escrever um valor já põe o parâmetro no código
        else:
            self.update_preview()
        if self._help_name != name:
            self.show_help(name)

    def apply_filter(self, text: str) -> None:
        t = text.strip().lower()
        used = self.only_used.isChecked()
        for name, r in self.rows.items():
            doc = (r["spec"].get("doc") or "").lower()
            show = (not t or t in name or t in doc) and (not used or r["check"].isChecked())
            for w in r["widgets"]:
                w.setVisible(show)
        for head, names in self.group_heads:
            head.setVisible(any(self.rows[n]["widgets"][0].isVisibleTo(self.grid_host) for n in names
                                if n in self.rows))

    def _focus_first(self) -> None:
        for r in self.rows.values():
            if r["required"] and not self.value_of(r):
                r["line"].setFocus()
                return
        self.filter.setFocus()

    @staticmethod
    def value_of(r: dict) -> str:
        ed = r["edit"]
        return (ed.currentText() if isinstance(ed, QComboBox) else ed.text()).strip()

    # ------------------------------------------------------------ ajuda

    def show_help(self, name: str | None) -> None:
        self._help_name = name
        ui = self.ctx.theme.ui if self.ctx.theme else {}
        accent, dim = ui.get("accent", "#6366f1"), ui.get("text_dim", "#888")
        esc = html.escape
        spec = self.spec()
        if name is None or name not in self.rows:
            req = ", ".join(spec.get("required", [])) or tr("none besides the trigger")
            self.help.setHtml(
                f"<h3>{esc(spec.get('name', ''))}</h3><p>{esc(sctrldocs.doc(spec.get('description', '')))}</p>"
                f"<p><b>{tr('Required:')}</b> {esc(req)}</p>"
                f"<p style='color:{dim}'>{tr('Click a parameter (or its ?) to see what it does. Typing a value checks '
                                            'it; the checked ones go into the code.')}</p>")
            return
        p = self.rows[name]["spec"]
        parts = [f"<h3>{esc(name)}</h3>"]
        if p.get("syntax"):
            parts.append(f"<p><code>{esc(p['syntax'])}</code></p>")
        meta = []
        if self.rows[name]["required"]:
            meta.append(f"<b style='color:#f85149'>{tr('required')}</b>")
        if p.get("deprecated"):
            meta.append(f"<b style='color:#d29922'>{tr('deprecated')}</b>")
        if p.get("ikemen"):
            meta.append(f"<b style='color:{accent}'>{tr('IKEMEN GO only')}</b>")
        if p.get("default"):
            meta.append(tr("default: {0}").format(f"<b>{esc(p['default'])}</b>"))
        if meta:
            parts.append(f"<p>{' · '.join(meta)}</p>")
        doc = p.get("doc") or ""
        if not p.get("translated"):
            doc = sctrldocs.doc(doc)
        if not doc and p.get("ikemen"):
            doc = tr("Parameter accepted only by IKEMEN GO (M.U.G.E.N 1.1 does not know it).")
        parts += self._paragraphs(doc)
        if p.get("choices"):
            parts.append(f"<p><b>{tr('Options (click to use):')}</b></p><ul>")
            for word, cdoc in p["choices"]:
                parts.append(f"<li><a href='choice:{esc(word)}' style='color:{accent}'><b>{esc(word)}</b></a>"
                             f" — {esc(sctrldocs.doc(cdoc))}</li>")
            parts.append("</ul>")
        if p.get("ikemen_note"):
            parts.append(f"<p style='margin-top:10px'><b style='color:{accent}'>{tr('In IKEMEN GO:')}</b></p>")
            parts += self._paragraphs(sctrldocs.doc(p["ikemen_note"]))
        self.help.setHtml("".join(parts))

    @staticmethod
    def _paragraphs(text: str) -> list[str]:
        """Parágrafos da doc em HTML; as linhas "• item" viram uma lista."""
        out, items = [], []
        for para in text.split("\n"):
            para = para.strip()
            if para.startswith("• "):
                items.append(f"<li>{html.escape(para[2:])}</li>")
                continue
            if items:
                out.append("<ul>" + "".join(items) + "</ul>")
                items = []
            if para:
                out.append(f"<p>{html.escape(para)}</p>")
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
        return out

    def on_help_link(self, url) -> None:
        s = url.toString()
        if s.startswith("choice:") and self._help_name in self.rows:
            ed = self.rows[self._help_name]["edit"]
            word = s[len("choice:"):]
            ed.setEditText(word) if isinstance(ed, QComboBox) else ed.setText(word)

    # ------------------------------------------------------------ código

    def text(self) -> str:
        spec = self.spec()
        name = spec.get("name", "")
        label = self.label.text().strip() or (self.existing["label"] if self.existing else "") or name
        out = [f"[State {self.statedef}, {label}]", f"type = {name}"]
        trig = [t.rstrip() for t in self.triggers.toPlainText().split("\n") if t.strip()]
        out += trig or ["trigger1 = 1"]
        rest = []
        ex = self.existing or {"values": {}, "raw": {}}
        for n, r in self.rows.items():
            v = self.value_of(r)
            if r["check"].isChecked():
                # valor que não mudou volta como estava (com o comentário e a grafia, ex. "ID")
                out.append(ex["raw"][n] if n in ex["raw"] and ex["values"].get(n) == v else
                           f"{n} = {v or literal_default(r['spec'])}")
            elif self.comment_rest.isChecked() and not r["spec"].get("ikemen"):
                rest.append(f";{n} = {literal_default(r['spec']) or self._hint(r['spec'])}")
        if self.existing:
            known = set(self.rows)
            # parâmetros que a janela não conhece (de outro motor, digitados à mão) ficam
            out += [line for k, line in ex["raw"].items() if k not in known]
            out += [x for x in self.existing["extra"]
                    if not re.match(r"^\s*;\s*([\w.]+)\s*=", x) or re.match(r"^\s*;\s*([\w.]+)", x)[1].lower()
                    not in known]
            while out and not out[-1].strip():
                out.pop()
        return "\n".join(out + rest) + "\n"

    def update_preview(self, *_):
        if not hasattr(self, "preview"):
            return
        missing = [n for n, r in self.rows.items() if r["required"] and r["check"].isChecked()
                   and not self.value_of(r) and not literal_default(r["spec"])]
        for n, r in self.rows.items():
            bad = n in missing
            r["line"].setStyleSheet("border: 1px solid #f85149;" if bad else "")
        self.missing.setText(tr("Missing a value: {0}").format(", ".join(missing)) if missing else "")
        pos = self.preview.verticalScrollBar().value()
        self.preview.setPlainText(self.text())
        self.preview.verticalScrollBar().setValue(pos)
        if self.only_used.isChecked():
            self.apply_filter(self.filter.text())

    def accept_text(self) -> None:
        self.result_text = self.text()
        self.ctx.settings["ctrl_comment_rest"] = self.comment_rest.isChecked()
        self.accept()
