"""Páginas de código: Definições, Comandos e Estados.

Painel esquerdo com a árvore do arquivo (em Estados, agrupada por faixa: sempre ativos,
básicos, normais, especiais, supers…) | abas de arquivo com barra de contexto, snippets e
problemas | (Comandos/Estados) controllers por categoria, com a ficha do selecionado.
"""
from __future__ import annotations

import re
from functools import lru_cache

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
                               QSizePolicy, QSplitter, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from ... import validate as vmod
from ...project import ProjectError
from ...formats import air, cmd, cns, mtext
from ...tools import generate, ops
from ..context import AppContext
from ..animpreview import AnimPreview
from ..icons import badge_icon, dot_icon, icon
from ..widgets import ElidedCombo, Section, SidePanel, file_icon, filter_box, hint_label, tool_button
from .code import CodeEdit
from ...i18n import tr

# trechos prontos: o nome segue o idioma da interface; o código inserido fica em inglês
SNIPPETS = {
    tr("Header (comment)"): ";---------------------------------------------------------------------------\n; {name}\n",
    tr("Generic Statedef"): "[Statedef {n}]\ntype = S\nmovetype = I\nphysics = S\nanim = {n}\nctrl = 0\nvelset = 0,0\n\n"
                            "[State {n}, end]\ntype = ChangeState\ntrigger1 = AnimTime = 0\nvalue = 0\nctrl = 1\n",
    tr("Move (HitDef)"): "[State {n}, hit]\ntype = HitDef\ntrigger1 = AnimElem = 3\nattr = S, NA\ndamage = 20, 0\n"
                         "animtype = Light\nguardflag = MA\nhitflag = MAF\npriority = 3, Hit\npausetime = 8, 8\n"
                         "sparkxy = -10, -70\nhitsound = 5, 0\nguardsound = 6, 0\nground.type = High\n"
                         "ground.slidetime = 8\nground.hittime = 10\nground.velocity = -4\nair.velocity = -2, -3\n",
    tr("Projectile"): "[State {n}, projectile]\ntype = Projectile\ntrigger1 = AnimElem = 2\nprojanim = {n}\n"
                      "offset = 40, -60\nvelocity = 5, 0\nattr = S, NP\ndamage = 40, 5\nanimtype = Medium\n",
    "Explod": "[State {n}, effect]\ntype = Explod\ntrigger1 = Time = 0\nanim = {n}\npos = 0, 0\n"
              "postype = p1\nbindtime = 1\nremovetime = -2\nsprpriority = 3\n",
    tr("Sound (PlaySnd)"): "[State {n}, sound]\ntype = PlaySnd\ntrigger1 = Time = 0\nvalue = 0, 0\n",
    tr("Command"): "[Command]\nname = \"new_move\"\ncommand = ~D, DF, F, x\ntime = 15\n",
    tr("Entry in [Statedef -1]"): "[State -1, New move]\ntype = ChangeState\nvalue = {n}\n"
                                  "triggerall = command = \"new_move\"\ntrigger1 = statetype = S\ntrigger1 = ctrl\n",
}

TYPE_COLORS = {"S": "#f2c200", "C": "#2fb344", "A": "#3d8bfd", "L": "#b05cff", "U": "#9aa0a6"}
TYPE_NAMES = {"S": tr("standing"), "C": tr("crouching"), "A": tr("in the air"), "L": tr("lying down"), "U": tr("no change")}
MOVETYPE_NAMES = {"A": tr("attacking"), "I": tr("idle"), "H": tr("getting hit"), "U": tr("no change")}

# faixas de número de estado (convenção do MUGEN e da comunidade): (chave, título, ícone, dica)
STATE_GROUPS = [
    ("always", tr("Always active"), "grp_always", tr("Statedef -3, -2 and -1: run every tick (commands, buffs, helpers)"),
     lambda n: n < 0),
    ("basic", tr("Basics"), "grp_basic", tr("0–199: stand, walk, jump, guard, intro and win"), lambda n: n < 200),
    ("normal", tr("Normal moves"), "grp_normal", tr("200–999: punches, kicks and throws"), lambda n: n < 1000),
    ("special", tr("Specials"), "grp_special", tr("1000–2999: special moves"), lambda n: n < 3000),
    ("super", tr("Supers"), "grp_super", tr("3000–4999: supers and hypers"), lambda n: n < 5000),
    ("hit", tr("Reactions"), "grp_hit", tr("5000–5999: states of the one getting hit"), lambda n: n < 6000),
    ("other", tr("Others"), "grp_other", tr("6000 or more: helpers, effects and custom states"), lambda n: True),
]

# categorias dos state controllers (cor do ponto na lista e na árvore)
CTRL_CATEGORIES = [
    (tr("State and animation"), "#a78bfa", "ChangeState ChangeAnim ChangeAnim2 SelfState StateTypeSet CtrlSet Turn "
                                     "AssertSpecial Null"),
    (tr("Movement and position"), "#38bdf8", "VelSet VelAdd VelMul PosSet PosAdd PosFreeze Gravity Offset ScreenBound "
                                       "PlayerPush Width BindToParent BindToRoot BindToTarget HitVelSet"),
    (tr("Attack and defense"), "#f87171", "HitDef ReversalDef Projectile HitBy NotHitBy HitOverride HitAdd MoveHitReset "
                                   "AttackDist AttackMulSet DefenceMulSet HitFallDamage HitFallSet HitFallVel"),
    (tr("Target (opponent)"), "#fb923c", "TargetBind TargetDrop TargetFacing TargetLifeAdd TargetPowerAdd TargetState "
                                   "TargetVelAdd TargetVelSet"),
    (tr("Life and power"), "#4ade80", "LifeAdd LifeSet PowerAdd PowerSet"),
    (tr("Visual effects"), "#f472b6", "Explod ModifyExplod RemoveExplod ExplodBindTime AfterImage AfterImageTime "
                                   "PalFX AllPalFX RemapPal Trans AngleAdd AngleDraw AngleMul AngleSet "
                                   "SprPriority MakeDust GameMakeAnim"),
    (tr("Screen and pause"), "#facc15", "Pause SuperPause EnvShake FallEnvShake EnvColor BGPalFX"),
    (tr("Sound"), "#2dd4bf", "PlaySnd StopSnd SndPan"),
    (tr("Variables"), "#818cf8", "VarSet VarAdd VarRandom VarRangeSet ParentVarAdd ParentVarSet"),
    (tr("Helpers"), "#c084fc", "Helper DestroySelf"),
    (tr("Debug and others"), "#9aa0a6", "AppendToClipboard ClearClipboard DisplayToClipboard ForceFeedback "
                                      "VictoryQuote"),
]
CTRL_COLOR = {n.lower(): color for _, color, names in CTRL_CATEGORIES for n in names.split()}

_HEADER = re.compile(r"\s*\[([^\]]+)\]")
_SPACES = re.compile(r"\s+")
ROLE_LINE = Qt.ItemDataRole.UserRole
ROLE_KEY = Qt.ItemDataRole.UserRole + 1
ROLE_OPEN = Qt.ItemDataRole.UserRole + 2
ROLE_ANIM = Qt.ItemDataRole.UserRole + 3


_dot = lru_cache(maxsize=None)(lambda color, radius=5.0: dot_icon(color, radius))
_badge = lru_cache(maxsize=None)(badge_icon)


class CodePage(QWidget):
    """Uma página de código para um tipo de arquivo ('def', 'cmd' ou 'states')."""

    def __init__(self, ctx: AppContext, kind: str, title: str):
        super().__init__()
        self.ctx = ctx
        self.kind = kind
        self.title = title
        self.files: list[dict] = []
        self.issues: list = []
        self._applying = False
        self._expanded: dict[str, bool] = {}     # grupos/estados abertos, mantidos entre recargas da árvore
        self._line_items: list[tuple[int, QTreeWidgetItem]] = []
        self._sync_timer = QTimer(self, singleShot=True, interval=120, timeout=self.sync_to_cursor)
        self._tree_timer = QTimer(self, singleShot=True, interval=700, timeout=self.fill_tree)

        # ------------------------------------------------ esquerdo: árvore do arquivo
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemClicked.connect(self.on_tree)
        self.tree.itemExpanded.connect(lambda it: self._remember(it, True))
        self.tree.itemCollapsed.connect(lambda it: self._remember(it, False))
        self.tree.setIndentation(14)
        self.tree.setObjectName("outline")
        self.tree.setUniformRowHeights(True)
        self.tree_filter = filter_box(tr("Filter sections and states…") if kind == "states" else tr("Filter sections…"),
                                      self.filter_tree)
        left_content = QWidget()
        lv = QVBoxLayout(left_content)
        head = QHBoxLayout()
        head.setSpacing(4)
        head.addWidget(self.tree_filter, 1)
        head.addWidget(tool_button("expand_all", tr("Expand all"), lambda: self._expand_all(True)))
        head.addWidget(tool_button("collapse_all", tr("Collapse all"), lambda: self._expand_all(False)))
        lv.addLayout(head)
        if kind == "states":
            lv.addWidget(self._tools_button())
        lv.addWidget(self.tree, 1)
        if kind == "states":
            legend = " ".join(f"<span style='color:{c}'>■</span>&nbsp;{t}&nbsp;{TYPE_NAMES[t]}&nbsp;"
                              for t, c in TYPE_COLORS.items() if t != "U")
            self.legend = hint_label(legend)
            self.legend.setToolTip(tr("State letter = type (posture) of the [Statedef]. The number on the right counts the state controllers."))
            lv.addWidget(self.legend)
        self.left = SidePanel(title, left_content, flush=True, header=False)

        # ------------------------------------------------ centro
        self.tabs = QTabWidget()
        self.tabs.setObjectName("codeTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setObjectName("docTabs")
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setIconSize(QSize(15, 15))
        # com um arquivo só (o caso comum) a linha de abas não diz nada: some
        self.tabs.setTabBarAutoHide(True)
        self.tabs.currentChanged.connect(lambda _: (self.fill_tree(), self.fill_issue_combo(), self.emit_cursor()))

        # erros do arquivo: só aparece quando há erro (avisos ficam marcados na margem do
        # código e no painel Problemas)
        self.issue_combo = ElidedCombo()
        self.issue_combo.activated.connect(self.goto_issue)
        self.issue_combo.setToolTip(tr("Errors in this file (pick one to go to the line; Ctrl+F8 / Ctrl+Shift+F8 move between problems)"))
        self.issue_combo.setFixedWidth(320)
        self.issue_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.issue_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.issue_combo.hide()
        self.apply_btn = QPushButton(tr("Apply"))
        self.apply_btn.setProperty("primary", True)
        self.apply_btn.setObjectName("compactPrimary")
        self.apply_btn.setToolTip(tr("Apply the text to the project · Ctrl+Enter (Ctrl+S saves to disk)"))
        self.apply_btn.clicked.connect(self.apply_current)

        # barra de contexto: onde está o cursor, erros, snippets e Aplicar
        self.crumb = QLabel()
        self.crumb.setObjectName("crumb")
        self.crumb.setTextFormat(Qt.TextFormat.RichText)
        self.crumb.setMinimumWidth(80)
        self.snippet_menu = QMenu(self)
        self.snippet_btn = QToolButton()
        self.snippet_btn.setObjectName("chipButton")
        self.snippet_btn.setText(tr("Snippets"))
        self.snippet_btn.setIcon(icon("snippet"))
        self.snippet_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.snippet_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.snippet_btn.setMenu(self.snippet_menu)
        self.snippet_btn.setToolTip(tr("Insert a ready-made code snippet below the current line"))
        self.fill_snippets()
        crumb_bar = QWidget()
        crumb_bar.setObjectName("crumbBar")
        cb = QHBoxLayout(crumb_bar)
        cb.setContentsMargins(12, 3, 8, 3)
        cb.setSpacing(6)
        cb.addWidget(self.crumb, 1)
        cb.addWidget(self.issue_combo)
        cb.addWidget(self.snippet_btn)
        cb.addWidget(self.apply_btn)
        self.crumb_bar = crumb_bar
        crumb_bar.hide()
        # a barra fica entre a aba e o editor: muda para dentro da aba ativa (_place_crumb)
        self.tabs.currentChanged.connect(lambda _: (self._place_crumb(), self.sync_to_cursor()))

        split = QSplitter()
        split.addWidget(self.tabs)
        if kind in ("states", "cmd"):
            split.addWidget(self._controllers_panel())
            split.setSizes([1000, 250])
            split.setChildrenCollapsible(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

        QShortcut(QKeySequence("Ctrl+Return"), self, self.apply_current)
        ctx.characterChanged.connect(self.on_character)
        ctx.themeChanged.connect(self.on_theme)
        ctx.modelChanged.connect(self.on_model)

    def _preview_toggled(self, on: bool) -> None:
        if on:
            self.preview.show_anim(getattr(self, "_preview_anim", None))
        else:
            self.preview.show_anim(None)

    def _place_crumb(self) -> None:
        """Uma barra de contexto só, posta no topo da aba ativa (o QTabWidget não deixa pôr nada
        entre a barra de abas e o painel)."""
        f = self.current()
        if f:
            f["box"].layout().insertWidget(0, self.crumb_bar)
            self.crumb_bar.show()
        else:
            self.crumb_bar.hide()

    # ------------------------------------------------------------ painéis

    def _tools_button(self) -> QToolButton:
        """Ferramentas de Estados num menu com nome e atalho (antes eram 5 ícones sem texto)."""
        b = QToolButton()
        b.setObjectName("chipButton")
        b.setText(tr("State tools"))
        b.setIcon(icon("tools"))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        m = QMenu(b)
        # o atalho vai depois do \t só como texto: quem responde ao atalho é o menu Estados
        for entry in [
            ("wizard", tr("Visual move wizard…\tCtrl+Shift+M"), lambda: self.ctx.request_tool.emit("wizard")),
            None,
            ("palfx", tr("PalFX editor…\tAlt+P"), lambda: self.ctx.request_tool.emit("palfx")),
            ("offset", tr("Offset viewer…\tAlt+O"), lambda: self.ctx.request_tool.emit("offset")),
            ("throw", tr("Throw creator…\tAlt+T"), lambda: self.ctx.request_tool.emit("throw")),
            None,
            ("var_log", tr("Insert variable usage log"), self.insert_var_log),
            ("var_list", tr("Variable usage list…"), lambda: self.ctx.request_tool.emit("var_list")),
        ]:
            if entry is None:
                m.addSeparator()
                continue
            name, text, fn = entry
            a = QAction(icon(name), text, m)
            a.triggered.connect(fn)
            m.addAction(a)
        b.setMenu(m)
        return b

    def _controllers_panel(self) -> SidePanel:
        self.ctrls = QTreeWidget()
        self.ctrls.setObjectName("outline")
        self.ctrls.setHeaderHidden(True)
        self.ctrls.setColumnCount(2)
        self.ctrls.header().setStretchLastSection(False)
        self.ctrls.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.ctrls.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.ctrls.setIndentation(12)
        self.ctrls.setUniformRowHeights(True)
        self.ctrls.setToolTip(tr("Click shows what the controller does; double-click inserts it into the state"))
        g = vmod.grammar()
        by_cat: dict[str, list] = {}
        known = {n.lower() for _, _, names in CTRL_CATEGORIES for n in names.split()}
        for c in g["controllers"].values():
            cat = next((t for t, _, names in CTRL_CATEGORIES if c["name"].lower() in names.lower().split()), None)
            by_cat.setdefault(cat if c["name"].lower() in known else tr("Debug and others"), []).append(c)
        opened = self.ctx.settings.get("ctrl_groups") or {}
        bold = QFont()
        bold.setBold(True)
        for title, color, _ in CTRL_CATEGORIES:
            items = sorted(by_cat.get(title, []), key=lambda c: c["name"].lower())
            if not items:
                continue
            grp = QTreeWidgetItem([title, str(len(items))])
            grp.setIcon(0, _dot(color, 4.5))
            grp.setFont(0, bold)
            grp.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grp.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.ctrls.addTopLevelItem(grp)
            for c in items:
                it = QTreeWidgetItem([c["name"] + (tr(" (deprecated)") if c.get("deprecated") else "")])
                it.setData(0, ROLE_KEY, c["name"])
                it.setToolTip(0, c.get("description") or c["name"])
                if c.get("deprecated"):
                    f = it.font(0)
                    f.setItalic(True)
                    it.setFont(0, f)
                grp.addChild(it)
            grp.setExpanded(bool(opened.get(title, title in (tr("State and animation"), tr("Attack and defense")))))
        self.ctrls.itemExpanded.connect(lambda it: self._remember_cat(it, True))
        self.ctrls.itemCollapsed.connect(lambda it: self._remember_cat(it, False))
        self.ctrls.itemDoubleClicked.connect(lambda it, _c: it.data(0, ROLE_KEY) and self.insert_controller(it.data(0, ROLE_KEY)))
        self.ctrls.currentItemChanged.connect(lambda it, _p: self.show_ctrl_card(it))
        self.ctrl_filter = filter_box(tr("Filter controllers…"), self.filter_ctrls)

        # ficha do controller selecionado
        card = QFrame()
        card.setObjectName("ctrlCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 10)
        cl.setSpacing(4)
        self.card_title = QLabel()
        self.card_title.setObjectName("cardTitle")
        self.card_text = hint_label("")
        self.card_req = hint_label("")
        self.card_req.setTextFormat(Qt.TextFormat.RichText)
        self.card_insert = QPushButton(tr("Insert into state"))
        self.card_insert.setProperty("primary", True)
        self.card_insert.setObjectName("compactPrimary")
        self.card_insert.setToolTip(tr("Inserts below the current line, with the required parameters"))
        self.card_insert.clicked.connect(lambda: self._selected_ctrl() and self.insert_controller(self._selected_ctrl()))
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self.card_insert, 1)
        row.addWidget(tool_button("info", tr("Full reference (all parameters)"), self.show_reference))
        cl.addWidget(self.card_title)
        cl.addWidget(self.card_text)
        cl.addWidget(self.card_req)
        cl.addLayout(row)
        self.card = card
        self.show_ctrl_card(None)

        rc = QWidget()
        rv = QVBoxLayout(rc)
        if self.kind == "states":
            # prévia da animação do estado no topo do painel direito; começa fechada e, fechada,
            # não desenha nada (só guarda qual ação mostrar ao abrir)
            self.preview = AnimPreview(self.ctx)
            self.preview.openRequested.connect(lambda n: self.ctx.request_definition.emit("anim", n, 0))
            self.preview_section = Section(tr("Animation preview"), key="states_preview_right",
                                           collapsed=True).add(self.preview)
            self.preview_section.toggled.connect(self._preview_toggled)
            rv.addWidget(self.preview_section)
        rv.addWidget(self.ctrl_filter)
        rv.addWidget(self.ctrls, 1)
        rv.addWidget(card)
        return SidePanel(tr("Controllers"), rc, 230, flush=True)

    def _selected_ctrl(self) -> str | None:
        it = self.ctrls.currentItem()
        return it.data(0, ROLE_KEY) if it else None

    def show_ctrl_card(self, it) -> None:
        name = it.data(0, ROLE_KEY) if it else None
        self.card_insert.setEnabled(bool(name))
        # sem controller escolhido a ficha some (a dica de cada um está no tooltip da lista)
        self.card.setVisible(bool(name))
        if not name:
            self.card_req.hide()
            return
        spec = generate.describe_controller(name)
        self.card_title.setText(spec["name"])
        desc = spec["description"] or ""
        self.card_text.setText(desc if len(desc) <= 220 else desc[:217].rsplit(" ", 1)[0] + "…")
        req = spec["required"]
        self.card_req.setText(tr("<b>Required:</b> ") + (", ".join(req) if req else tr("none besides the trigger")))
        self.card_req.show()

    def _remember_cat(self, it, on: bool) -> None:
        if it.parent() is None:
            data = dict(self.ctx.settings.get("ctrl_groups") or {})
            data[it.text(0)] = on
            self.ctx.settings["ctrl_groups"] = data

    @property
    def char(self):
        return self.ctx.char

    # ------------------------------------------------------------ arquivos

    def _sources(self) -> list[dict]:
        c = self.char
        if self.kind == "def":
            return [{"kind": "def", "path": c.def_path, "text": lambda: c.definition.to_text()}]
        if self.kind == "cmd":
            try:
                return [{"kind": "cmd", "path": c.commands.path, "text": lambda: c.commands.doc.to_text()}]
            except Exception:  # noqa: BLE001
                return []
        if self.kind == "states":
            return [{"kind": "states", "index": i, "path": st.path, "text": (lambda st=st: st.doc.to_text())}
                    for i, st in enumerate(c.states)]
        if self.kind == "air":
            return [{"kind": "air", "path": c.path_of("anim"), "text": lambda: c.anims.to_text()}]
        return []

    def on_character(self) -> None:
        """Outro personagem: a página à vista recarrega já; as escondidas só esvaziam e carregam
        quando forem abertas (show_page chama load). Abrir um char não colore quatro editores."""
        if self.isVisible():
            self.load()
        else:
            self.unload()

    def unload(self) -> None:
        self.crumb_bar.setParent(None)        # sai da aba antiga antes de ela ser descartada
        self._expanded = {}                   # personagem novo: a árvore começa toda fechada
        old = [self.tabs.widget(i) for i in range(self.tabs.count())]
        self.tabs.clear()
        for w in old:
            w.deleteLater()
        self.files = []
        self.crumb.setText("")

    def load(self) -> None:
        self.unload()
        if not self.char:
            return
        for src in self._sources():
            ed = CodeEdit(self.ctx.theme)
            ed.setPlainText(src["text"]().replace("\r\n", "\n"))
            ed.document().setModified(False)
            ed.document().modificationChanged.connect(self._titles)
            ed.cursorPositionChanged.connect(self.emit_cursor)
            ed.cursorPositionChanged.connect(self._sync_timer.start)
            ed.document().contentsChanged.connect(self._tree_timer.start)
            ed.definitionRequested.connect(self.ctx.request_definition)
            box = QWidget()
            box.setObjectName("plain")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(0)
            bl.addWidget(ed, 1)
            box.setFocusProxy(ed)
            src["edit"] = ed
            src["box"] = box
            self.files.append(src)
            self.tabs.addTab(box, file_icon(src["path"].name), src["path"].name)
        self._tree_timer.stop()
        self._place_crumb()
        self.fill_tree()

    def _titles(self, *_):
        for i, f in enumerate(self.files):
            self.tabs.setTabText(i, ("● " if f["edit"].document().isModified() else "") + f["path"].name)

    def current(self):
        i = self.tabs.currentIndex()
        return self.files[i] if 0 <= i < len(self.files) else None

    def current_edit(self) -> CodeEdit | None:
        f = self.current()
        return f["edit"] if f else None

    def is_modified(self) -> bool:
        return any(f["edit"].document().isModified() for f in self.files)

    def on_theme(self) -> None:
        for f in self.files:
            f["edit"].set_theme(self.ctx.theme)
        self.fill_tree()

    def on_model(self, part: str) -> None:
        if self._applying or not self.files:
            return
        mapping = {"def": "def", "commands": "cmd", "states": "states", "anims": "air", "anims_inline": "air"}
        if mapping.get(part) == self.kind or (part == "states" and self.kind == "cmd"):
            QTimer.singleShot(0, self.reload_unmodified)

    def reload_unmodified(self) -> None:
        for f in self.files:
            if not f["edit"].document().isModified():
                ed = f["edit"]
                pos = ed.verticalScrollBar().value()
                ed.setPlainText(f["text"]().replace("\r\n", "\n"))
                ed.document().setModified(False)
                ed.verticalScrollBar().setValue(pos)
        self._tree_timer.stop()
        self.fill_tree()

    def emit_cursor(self) -> None:
        ed = self.current_edit()
        f = self.current()
        if ed and self.isVisible():
            c = ed.textCursor()
            enc = (self.char.definition.encoding if f["kind"] == "def" else "")
            self.ctx.cursor_info.emit(c.blockNumber() + 1, c.positionInBlock() + 1, ed.blockCount(), enc or "")

    # ------------------------------------------------------------ árvore

    def _dim(self) -> QColor:
        return QColor(self.ctx.theme.ui.get("text_dim", "#888") if self.ctx.theme else "#888")

    def fill_tree(self) -> None:
        self._tree_timer.stop()
        bar = self.tree.verticalScrollBar().value()
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        self._line_items = []
        f = self.current()
        try:
            if f:
                self._build_tree(f)
                self._restore_expanded()
        finally:
            self.tree.setUpdatesEnabled(True)
        if self.tree_filter.text():
            self.filter_tree(self.tree_filter.text())
        self.tree.verticalScrollBar().setValue(bar)
        self._line_items.sort(key=lambda x: x[0])
        self.sync_to_cursor()

    def _build_tree(self, f) -> None:
        doc = mtext.parse(f["edit"].toPlainText())
        line = 1 + len(doc.preamble)
        positions = []
        for sec in doc.sections:
            positions.append((sec, line))
            line += len(sec.lines)
        line_of = {id(s): ln for s, ln in positions}
        if self.kind == "states":
            st = cns.States(doc)
            consts = [(s, ln) for s, ln in positions if s.kind not in ("state", "statedef")]
            if consts:
                grp = self._group("const", tr("Constants"), "grp_const", tr("Character data sections ([Data], [Size], [Velocity]…)"), len(consts), default_open=False)
                for sec, ln in consts:
                    self._item(grp, sec.name, ln)
            if self.view_mode() == "flat":
                # lista tradicional: tudo na ordem do arquivo
                for n, sd in st.statedefs.items():
                    self._statedef_item(self.tree, doc, n, sd, line_of)
                return
            members: dict[str, list] = {}
            for n, sd in st.statedefs.items():
                key = next(k for k, *_rest, test in STATE_GROUPS if test(n))
                members.setdefault(key, []).append((n, sd))
            for key, title, ic, tip, _test in STATE_GROUPS:
                if key not in members:
                    continue
                grp = self._group(key, title, ic, tip, len(members[key]), default_open=False)
                for n, sd in members[key]:
                    self._statedef_item(grp, doc, n, sd, line_of)
        elif self.kind == "cmd":
            cmds_secs = [(s, ln) for s, ln in positions if s.kind == "command"]
            if cmds_secs:
                cmds = self._group("cmds", tr("Commands"), "commands", tr("[Command] sections"), len(cmds_secs))
                for sec, ln in cmds_secs:
                    it = self._item(cmds, (sec.get("name") or "?").strip('"'), ln)
                    it.setText(1, (sec.get("command") or "").strip())
                    it.setForeground(1, self._dim())
            for sec, ln in positions:
                if sec.kind not in ("command", "state", "statedef"):
                    self._item(self.tree, sec.name, ln)
            st = cns.States(doc)
            for n, sd in st.statedefs.items():
                it = self._item(self.tree, f"Statedef {n}", line_of[id(sd.section)], key=f"sd:{n}",
                                count=len(sd.controllers), default_open=n == -1)
                it.setIcon(0, icon("states", toggles=False))
                for ctl in sd.controllers:
                    c = self._item(it, ctl.label or ctl.type, line_of[id(ctl.section)])
                    c.setIcon(0, _dot(CTRL_COLOR.get((ctl.type or "").lower(), "#9aa0a6"), 4))
        else:
            for sec, ln in positions:
                entries = list(sec.entries())
                it = self._item(self.tree, sec.name, ln, key=f"sec:{sec.name}", count=len(entries) or None)
                it.setIcon(0, icon("definitions", toggles=False))
                for e in entries:
                    c = self._item(it, e.key, ln + e.line)
                    c.setText(1, str(e.value)[:40])
                    c.setForeground(1, self._dim())
                    c.setToolTip(0, f"{e.key} = {e.value}")

    def view_mode(self) -> str:
        """'grouped' (por faixa: básicos, especiais, supers…) ou 'flat' (ordem do arquivo)."""
        return self.ctx.settings.get("states_view") or "grouped"

    def set_view_mode(self, mode: str) -> None:
        self.ctx.settings["states_view"] = mode
        self.fill_tree()

    @staticmethod
    def _int(text) -> int | None:
        try:
            return int(str(text).split(";")[0].strip())
        except (TypeError, ValueError):
            return None

    def _statedef_item(self, parent, doc, n: int, sd, line_of: dict):
        sec = sd.section
        typ = (sec.get("type") or "S").strip().upper()[:1]
        mt = (sec.get("movetype") or "I").strip().upper()[:1]
        anim = self._int(sec.get("anim"))
        comment = self._comment_above(doc, sec) or (f"anim {anim}" if anim is not None else "")
        it = self._item(parent, f"{n}  {comment}".rstrip(), line_of[id(sec)], key=f"sd:{n}",
                        count=len(sd.controllers), default_open=False)
        it.setIcon(0, _badge(TYPE_COLORS.get(typ, "#9aa0a6"), typ))
        it.setData(0, ROLE_ANIM, anim)
        it.setToolTip(0, f"[Statedef {n}] {comment}\n"
                         f"type {typ} ({TYPE_NAMES.get(typ, '?')}) · movetype {mt} "
                         f"({MOVETYPE_NAMES.get(mt, '?')}) · physics {(sec.get('physics') or '?').strip()}\n"
                         f"anim {anim if anim is not None else '—'} · {len(sd.controllers)} controllers")
        for ctl in sd.controllers:
            label = (ctl.label or "").strip()
            show = ctl.type or "?"
            if label and not label.isdigit() and label.lower() != show.lower():
                show += f"  ·  {label}"
            c = self._item(it, show, line_of[id(ctl.section)])
            c.setIcon(0, _dot(CTRL_COLOR.get((ctl.type or "").lower(), "#9aa0a6"), 4))
            # ChangeAnim com número fixo mostra a própria ação; os outros, a do Statedef
            own = self._int(ctl.section.get("value")) if (ctl.type or "").lower() in ("changeanim", "changeanim2") else None
            c.setData(0, ROLE_ANIM, own if own is not None else anim)
        return it

    def _group(self, key: str, title: str, ic: str, tip: str, count: int, default_open: bool = True):
        it = QTreeWidgetItem([title, str(count)])
        it.setIcon(0, icon(ic, toggles=False))
        f = it.font(0)
        f.setBold(True)
        it.setFont(0, f)
        it.setToolTip(0, tip)
        it.setForeground(1, self._dim())
        it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        it.setData(0, ROLE_KEY, "g:" + key)
        it.setData(0, ROLE_OPEN, default_open)
        self.tree.addTopLevelItem(it)
        return it

    def _item(self, parent, text: str, line, color: str | None = None, badge: str | None = None,
              key: str | None = None, count: int | None = None, default_open: bool = False):
        it = QTreeWidgetItem([text])
        it.setData(0, ROLE_LINE, line)
        if count is not None:
            it.setText(1, str(count))
            it.setForeground(1, self._dim())
            it.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if color:
            it.setIcon(0, _badge(color, badge or ""))
        if isinstance(parent, QTreeWidget):
            parent.addTopLevelItem(it)
        else:
            parent.addChild(it)
        if key:
            it.setData(0, ROLE_KEY, key)
            it.setData(0, ROLE_OPEN, default_open)
        if line:
            self._line_items.append((line, it))
        return it

    def _restore_expanded(self) -> None:
        """Grupos e estados voltam abertos ou fechados como o usuário deixou antes da recarga."""
        def walk(it):
            key = it.data(0, ROLE_KEY)
            if key and it.childCount():
                it.setExpanded(self._expanded.get(key, bool(it.data(0, ROLE_OPEN))))
            for k in range(it.childCount()):
                walk(it.child(k))
        for k in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(k))

    def _remember(self, it, on: bool) -> None:
        key = it.data(0, ROLE_KEY)
        if key and self.tree.updatesEnabled():
            self._expanded[key] = on

    def _expand_all(self, on: bool) -> None:
        def walk(it):
            if it.childCount():
                it.setExpanded(on)
                for k in range(it.childCount()):
                    walk(it.child(k))
        for k in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(k))

    @staticmethod
    def _comment_above(doc, sec) -> str:
        i = next(k for k, x in enumerate(doc.sections) if x is sec)   # identidade, não igualdade
        prev = doc.sections[i - 1].lines if i else doc.preamble
        block = []      # o bloco de comentários colado ao [Statedef], de cima para baixo
        for line in reversed(prev):
            t = line.strip()
            if not t.startswith(";"):
                if t or block:
                    break
                continue
            block.insert(0, t.lstrip(";").strip())
        # o título é a primeira linha com texto (pula as de traços ou '====')
        for t in block:
            t = t.strip("-=*~# ").strip()
            if t and not re.match(r"(?i)^(cns difficulty|difficulty)", t):
                return t[:60]
        return ""

    def sync_to_cursor(self) -> None:
        """Marca na árvore o item onde está o cursor e mostra o caminho na barra de contexto."""
        ed = self.current_edit()
        f = self.current()
        if not ed or not f:
            self.crumb.setText("")
            return
        line = ed.textCursor().blockNumber() + 1
        found = None
        for ln, it in self._line_items:      # ordenado: o último cabeçalho acima do cursor
            if ln > line:
                break
            found = it
        dim = self._dim().name()
        parts = [f"<span style='color:{dim}'>{f['path'].name}</span>"]
        if found is not None:
            chain = []
            it = found
            while it is not None:
                chain.insert(0, it.text(0).replace("&", "&amp;").replace("<", "&lt;"))
                it = it.parent()
            parts += [f"<span style='color:{dim}'>{c}</span>" for c in chain[:-1]]
            parts.append(f"<b>{chain[-1]}</b>")
            vis = found
            while vis.parent() is not None and not vis.parent().isExpanded():
                vis = vis.parent()
            if self.kind == "states":
                self._preview_anim = found.data(0, ROLE_ANIM)
                if self.preview_section.is_open():
                    self.preview.show_anim(self._preview_anim)
            if self.tree.currentItem() is not vis:
                self.tree.blockSignals(True)
                self.tree.setCurrentItem(vis)
                self.tree.blockSignals(False)
                self.tree.scrollToItem(vis)
        self.crumb.setText(f"&nbsp;<span style='color:{dim}'>›</span>&nbsp;".join(parts))

    def filter_tree(self, text: str) -> None:
        """Mostra só os itens (e os pais deles) que contêm o texto."""
        t = text.strip().lower()

        def walk(it) -> bool:
            child_hit = False
            for k in range(it.childCount()):
                child_hit = walk(it.child(k)) or child_hit
            hit = not t or t in it.text(0).lower() or child_hit
            it.setHidden(not hit)
            if t and child_hit:
                it.setExpanded(True)
            return hit
        self.tree.setUpdatesEnabled(False)       # não grava como escolha do usuário
        for k in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(k))
        self.tree.setUpdatesEnabled(True)

    def filter_ctrls(self, text: str) -> None:
        t = text.strip().lower()
        for g in range(self.ctrls.topLevelItemCount()):
            grp = self.ctrls.topLevelItem(g)
            shown = 0
            for k in range(grp.childCount()):
                it = grp.child(k)
                hide = bool(t) and t not in it.text(0).lower()
                it.setHidden(hide)
                shown += not hide
            grp.setHidden(shown == 0)
            if t and shown:
                self.ctrls.blockSignals(True)
                grp.setExpanded(True)
                self.ctrls.blockSignals(False)

    def on_tree(self, item, _col=0) -> None:
        line = item.data(0, ROLE_LINE)
        ed = self.current_edit()
        if ed and line:
            ed.goto_line(line)
        elif item.childCount():
            item.setExpanded(not item.isExpanded())

    # ------------------------------------------------------------ inserções

    def _insert(self, text: str) -> None:
        ed = self.current_edit()
        if not ed:
            return
        c = ed.textCursor()
        c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        c.insertText("\n\n" + text.rstrip() + "\n")
        ed.setTextCursor(c)
        ed.setFocus()

    def _current_statedef(self) -> int:
        ed = self.current_edit()
        if not ed:
            return 0
        lines = ed.toPlainText().split("\n")[: ed.textCursor().blockNumber() + 1]
        for line in reversed(lines):
            m = re.match(r"\s*\[\s*statedef\s+(-?\d+)", line, re.I)
            if m:
                return int(m[1])
        return 0

    def all_snippets(self) -> dict:
        return {**SNIPPETS, **(self.ctx.settings.get("snippets") or {})}

    def fill_snippets(self) -> None:
        m = self.snippet_menu
        m.clear()
        for name in self.all_snippets():
            a = m.addAction(icon("code"), name)
            a.triggered.connect(lambda _=False, n=name: self.insert_snippet(n))
        m.addSeparator()
        m.addAction(icon("new"), tr("New snippet from the selection…"), self.new_snippet)
        m.addAction(icon("edit"), tr("Edit snippets…"), lambda: self.window().edit_snippets())

    def insert_snippet(self, name: str) -> None:
        n = self._current_statedef() or 1000
        text = self.all_snippets().get(name, "")
        name = self.char.name if self.char else ""
        try:                                    # {nome}: trechos salvos antes da tradução
            text = text.format(n=n, name=name, nome=name)
        except (KeyError, IndexError, ValueError):
            text = text.replace("{n}", str(n)).replace("{name}", name).replace("{nome}", name)
        self._insert(text)

    def new_snippet(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        ed = self.current_edit()
        text = ed.textCursor().selectedText().replace(chr(0x2029), chr(10)) if ed else ""
        if not text.strip():
            self.ctx.status.emit(tr("Select the code that becomes a snippet"))
            return
        name, ok = QInputDialog.getText(self, tr("New snippet"), tr("Snippet name:"))
        if ok and name.strip():
            data = dict(self.ctx.settings.get("snippets") or {})
            data[name.strip()] = text
            self.ctx.settings["snippets"] = data
            self.ctx.settings.save()
            self.fill_snippets()

    def insert_controller(self, name: str) -> None:
        self._insert(generate.controller_template(name, self._current_statedef()))

    def show_reference(self) -> None:
        name = self._selected_ctrl()
        if name:
            spec = generate.describe_controller(name)
            from ..dialogs import TextDialog
            body = spec["description"] + tr("\n\nParameters:\n") + "\n".join(
                ("* " if p in spec["required"] else "  ") + v["syntax"] for p, v in spec["params"].items())
            TextDialog(spec["name"], body + tr("\n\n* required"), self).exec()

    def insert_var_log(self) -> None:
        if self.char:
            self._insert(ops.var_list_comment(self.char))

    # ------------------------------------------------------------ aplicar

    def apply_current(self) -> None:
        f = self.current()
        if f:
            self.apply(f)

    def apply_all(self) -> None:
        for f in self.files:
            if f["edit"].document().isModified():
                self.apply(f)

    def apply(self, f) -> None:
        """Aplica o texto da aba ao modelo. Se o arquivo deixou de ser achado (o .def editado
        não aponta mais para ele), avisa e mantém a aba como modificada em vez de quebrar."""
        try:
            self._apply(f)
        except (ProjectError, IndexError) as e:   # IndexError: o .def perdeu a entrada desse .cns
            self._applying = False
            self.ctx.status.emit(tr("{0} was not applied: {1}").format(f['path'].name, e))
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, tr("Apply to project"),
                                tr("{0} was not applied to the project:\n{1}\n\nCheck the [Files] section of the .def; the text stays in the tab.").format(f['path'].name, e))

    def _apply(self, f) -> None:
        c = self.char
        text = f["edit"].toPlainText()
        self._applying = True
        try:
            def parse(old):
                doc = mtext.parse(text, old.encoding)
                doc.newline = old.newline
                return doc
            if f["kind"] == "def":
                c.definition = parse(c.definition)
                self.ctx.reset_cache("sprites", "anims", "sounds", "commands", "states", "common_states", "constants")
                self.ctx.changed("def")
            elif f["kind"] == "cmd":
                c.__dict__["commands"] = cmd.Commands(parse(c.commands.doc), f["path"])
                self.ctx.changed("commands")
            elif f["kind"] == "states":
                if f["index"] >= len(c.states):
                    raise ProjectError(tr("the .def no longer lists {0} in [Files]").format(f['path'].name))
                c.states[f["index"]] = cns.States(parse(c.states[f["index"]].doc), f["path"])
                self.ctx.reset_cache("constants")
                self.ctx.changed("states")
            elif f["kind"] == "air":
                c.__dict__["anims"] = air.AIR(parse(c.anims.doc))
                self.ctx.changed("anims")
        finally:
            self._applying = False
        f["edit"].document().setModified(False)
        self.fill_tree()
        self.ctx.status.emit(tr("{0} applied to the project (Ctrl+S saves to disk)").format(f['path'].name))
        self.ctx.request_validate.emit()

    # ------------------------------------------------------------ problemas

    @staticmethod
    def header_index(lines: list[str]) -> dict[str, int]:
        """Cabeçalho normalizado -> primeira linha dele. "[Statedef 200]" também acha
        "[Statedef 200, Soco fraco]": a parte antes da vírgula entra como chave própria."""
        index: dict[str, int] = {}
        for n, line in enumerate(lines, 1):
            if "[" not in line:
                continue
            h = _HEADER.match(line)
            if h:
                head = _SPACES.sub(" ", h[1].strip().lower())
                index.setdefault(head, n)
                index.setdefault(head.split(",")[0].strip(), n)
        return index

    @staticmethod
    def line_of(lines: list[str] | dict[str, int], where: str) -> int | None:
        m = re.match(r"\[([^\]]+)\]", where or "")
        if not m:
            return None
        index = lines if isinstance(lines, dict) else CodePage.header_index(lines)
        return index.get(_SPACES.sub(" ", m[1].strip().lower()))

    def set_issues(self, issues: list) -> None:
        self.issues = issues
        by_file: dict[str, list] = {}
        for i in issues:
            by_file.setdefault(i.file, []).append(i)
        for f in self.files:
            mine = by_file.get(f["path"].name, [])
            index = self.header_index(f["edit"].toPlainText().split("\n")) if mine else {}
            marks: dict[int, str] = {}
            for i in mine:
                ln = self.line_of(index, i.where)
                if ln and marks.get(ln) != "error":
                    marks[ln] = i.severity
            f["edit"].set_issues(marks)
        self.fill_issue_combo()

    def fill_issue_combo(self) -> None:
        self.issue_combo.clear()
        f = self.current()
        if not f:
            return
        mine = [i for i in self.issues if i.file == f["path"].name and i.severity == "error"]
        self.issue_combo.setVisible(bool(mine))
        n = len(mine)
        for k, i in enumerate(mine, 1):
            self.issue_combo.addItem(icon("error", "error"), f"{k}/{n}  {i.where} {i.message}", i.where)

    def goto_issue(self, idx: int) -> None:
        where = self.issue_combo.itemData(idx)
        f = self.current()
        if where and f:
            ln = self.line_of(f["edit"].toPlainText().split("\n"), where)
            if ln:
                f["edit"].goto_line(ln)

    def has_file(self, name: str) -> bool:
        return any(f["path"].name == name for f in self.files)

    def goto(self, filename: str, where: str) -> bool:
        for i, f in enumerate(self.files):
            if f["path"].name == filename:
                self.tabs.setCurrentIndex(i)
                # "#N" é a linha exata (dois controladores podem ter o mesmo cabeçalho)
                ln = int(where[1:]) if re.fullmatch(r"#\d+", where or "") else \
                    self.line_of(f["edit"].toPlainText().split("\n"), where)
                if ln:
                    QTimer.singleShot(0, lambda: f["edit"].goto_line(ln))
                return True
        return False
