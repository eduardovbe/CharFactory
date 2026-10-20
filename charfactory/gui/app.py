"""Janela principal."""
from __future__ import annotations

import datetime
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QFileSystemWatcher, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon, QKeySequence, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QStyleFactory, QTabBar,
                               QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QTreeWidget,
                               QToolButton, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import archive, i18n
from ..project import ProjectError
from ..tools import generate, ops, runner
from . import dialogs, theme as themes
from .context import AppContext
from .editors.animations import AnimationEditor
from .editors.codepages import CodePage
from .editors.sounds import SoundEditor
from .editors.sprites import SpriteEditor
from .icons import icon
from .widgets import ChipTabBar, SegmentedTabs, Section, install_ui_polish, install_wheel_guard
from ..i18n import tr

APP_NAME = "Char Factory"
PROJECT_FILES = (".def", ".sff", ".air", ".cmd", ".cns", ".st", ".snd", ".act")
IMAGE_FILES = (".png", ".pcx", ".bmp", ".gif")
AUDIO_FILES = (".wav", ".ogg", ".mp3", ".flac", ".opus", ".m4a")


# ======================================================================= tela inicial

class Tile(QFrame):
    """Cartão clicável (ícone, título, descrição e atalho) da tela inicial."""
    clicked = Signal()

    def __init__(self, icon_name: str, title: str, text: str = "", shortcut: str = "",
                 primary: bool = False):
        super().__init__()
        self.setObjectName("tile")
        self.setProperty("primary", primary)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(icon(icon_name).pixmap(QSize(22, 22), QIcon.Mode.Normal,
                                            QIcon.State.On if primary else QIcon.State.Off))
        ic.setFixedWidth(24)
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("tileTitle")
        col.addWidget(t)
        if text:
            d = QLabel(text)
            d.setObjectName("tileText")
            col.addWidget(d)
        lay.addLayout(col, 1)
        if shortcut:
            k = QLabel(shortcut)
            k.setObjectName("kbd")
            lay.addWidget(k)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class Welcome(QWidget):
    title = tr("Home")

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        page = QWidget()
        page.setMaximumWidth(1180)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(40, 36, 40, 32)
        outer.setSpacing(22)
        head = QVBoxLayout()
        head.setSpacing(6)
        self.hero = QLabel(APP_NAME)
        self.hero.setObjectName("hero")
        sub = QLabel(tr("M.U.G.E.N character and stage editor"))
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(QSize(52, 52)))
        title_row.addWidget(logo)
        title_row.addWidget(self.hero)
        title_row.addStretch(1)
        head.addLayout(title_row)
        head.addWidget(sub)
        outer.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(20)
        self.actions_card = self._card()
        self.recent_card = self._card()
        row.addWidget(self.actions_card, 5, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.recent_card, 6, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(row)
        drop = QLabel(tr("Drag a .def or a .zip/.rar/.7z onto the window  ·  F1 finds any command"))
        drop.setObjectName("dim")
        drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop.setWordWrap(True)
        outer.addWidget(drop)
        outer.addStretch(1)
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hl.addWidget(page, 100)
        hl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(holder)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
        self.refresh()

    @staticmethod
    def _card() -> QWidget:
        f = QFrame()
        f.setObjectName("card")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(20, 18, 20, 20)
        lay.setSpacing(8)
        return f

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif item.layout():
                Welcome._clear(item.layout())

    @staticmethod
    def _title(text: str, trailing: QWidget | None = None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 6)
        lbl = QLabel(text)
        lbl.setObjectName("sectionTitle")
        row.addWidget(lbl)
        row.addStretch(1)
        if trailing:
            row.addWidget(trailing)
        return row

    def refresh(self) -> None:
        a, r = self.actions_card.layout(), self.recent_card.layout()
        self._clear(a)
        self._clear(r)
        a.addLayout(self._title(tr("Get started")))
        # stage, sheet, compactado e temas continuam no menu Projeto/Exibir; aqui só o começo
        for ic, text, desc, key, fn, primary in [
                ("file_new", tr("New character"), tr("Folder with ready .def, .sff, .air, .cns and .cmd"),
                 "Ctrl+N", self.win.new_character, True),
                ("open", tr("Open…"), tr("Character or stage (.def), or a .zip/.rar/.7z"), "Ctrl+O",
                 self.win.open_dialog, False)]:
            t = Tile(ic, text, desc, key, primary)
            t.clicked.connect(fn)
            a.addWidget(t)
        a.addStretch(1)
        # projeto que sumiu do disco não aparece (a lista guarda; volta se a pasta voltar)
        recent = [p for p in self.win.ctx.settings.get("recent", []) if Path(p).exists()]
        clear = QPushButton(tr("Clear list"))
        clear.setObjectName("link")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clear_recent)
        clear.setVisible(bool(recent))
        r.addLayout(self._title(tr("Recent projects"), clear))
        if not recent:
            empty = QLabel(tr("No recent projects.\nOpen a character and it shows up here."))
            empty.setObjectName("dim")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(140)
            r.addWidget(empty)
        for p in recent[:8]:
            path = Path(p)
            folder = str(path.parent)
            if len(folder) > 64:
                folder = "…" + folder[-62:]
            t = Tile("definitions", path.stem, folder)
            t.setToolTip(p)
            t.clicked.connect(lambda p=p: self.win.open_path(p))
            r.addWidget(t)

    def clear_recent(self) -> None:
        self.win.ctx.settings["recent"] = []
        self.win.ctx.settings.save()
        self.refresh()


# ======================================================================= janela

class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.restart_requested = False    # trocar o idioma reabre o app ao fechar
        Section.store = ctx.settings      # seções recolhidas dos painéis são lembradas
        install_wheel_guard(QApplication.instance())
        install_ui_polish(QApplication.instance())
        if getattr(QApplication.instance(), "_charfactory_translator", None) is None:
            install_translator(QApplication.instance())
        from PySide6.QtGui import QFontDatabase
        family = "Segoe UI Variable Text" if "Segoe UI Variable Text" in QFontDatabase.families() else "Segoe UI"
        QApplication.instance().setFont(QFont(family, 9))
        self.resize(1440, 900)

        # páginas, na ordem dos botões de editor
        self.defs = CodePage(ctx, "def", tr("Definitions"))
        self.sprites = SpriteEditor(ctx)
        self.anims = AnimationEditor(ctx)
        self.cmds = CodePage(ctx, "cmd", tr("Commands"))
        self.states = CodePage(ctx, "states", tr("States"))
        self.sounds = SoundEditor(ctx)
        self.air_code = CodePage(ctx, "air", tr("Animation code"))
        from .editors.simulator import SimulatorPage
        self.simulator = SimulatorPage(ctx)
        self.welcome = Welcome(self)
        self.pages = [self.defs, self.sprites, self.anims, self.cmds, self.states, self.sounds, self.air_code,
                      self.simulator]
        self.code_pages = [self.defs, self.cmds, self.states, self.air_code]

        self.center = QStackedWidget()
        self.left_stack = QStackedWidget()
        self.center.addWidget(self.welcome)
        self.left_stack.addWidget(QWidget())
        for p in self.pages:
            self.center.addWidget(p)
            self.left_stack.addWidget(p.left)
        self.explorer = QTreeWidget()
        self.explorer.setHeaderHidden(True)
        self.explorer.itemActivated.connect(self.on_explorer)
        self.explorer.itemClicked.connect(self.on_explorer)
        self.left_tabs = SegmentedTabs()
        self.left_tabs.addTab(self.left_stack, tr("Editor"), "edit")
        self.explorer.setObjectName("flatTree")
        explorer_box = QWidget()
        explorer_box.setObjectName("sidePanel")
        eb = QVBoxLayout(explorer_box)
        eb.setContentsMargins(0, 0, 0, 0)
        eb.setSpacing(0)
        eb.addWidget(self.explorer, 1)
        self.left_tabs.addTab(explorer_box, tr("Project"), "project")
        self.left_tabs.setMinimumWidth(300)

        # área inferior: Problemas | Busca avançada | Saída do motor
        self.msgs = QTableWidget(0, 4)
        self.msgs.setHorizontalHeaderLabels(["", tr("File"), tr("Location"), tr("Message")])
        hh = self.msgs.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.resizeSection(0, 30)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.msgs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.msgs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.msgs.verticalHeader().setVisible(False)
        self.msgs.setShowGrid(False)
        self.msgs.setWordWrap(False)
        self.msgs.cellDoubleClicked.connect(self.on_msg)
        self.msgs_empty = self._empty_state("ok", tr("No errors found"),  # ícone verde
                                            tr("Validation runs on open and on save (F7 runs it again)."))
        self.msgs_stack = QStackedWidget()
        self.msgs_stack.addWidget(self.msgs_empty)
        self.msgs_stack.addWidget(self.msgs)
        # avisos (estilo, parâmetro desconhecido…) ficam fora da lista até o usuário pedir: com
        # centenas deles os erros de verdade sumiam no meio
        self.show_warnings = QCheckBox(tr("Show warnings"))
        self.show_warnings.toggled.connect(lambda _: self.fill_messages())
        msgs_head = QWidget()
        mh = QHBoxLayout(msgs_head)
        mh.setContentsMargins(10, 4, 10, 0)
        mh.addStretch(1)
        mh.addWidget(self.show_warnings)
        msgs_box = QWidget()
        mb = QVBoxLayout(msgs_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(0)
        mb.addWidget(msgs_head)
        mb.addWidget(self.msgs_stack, 1)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Search all text files of the project (Enter)"))
        self.search_box.returnPressed.connect(self.advanced_search)
        self.search_results = QListWidget()
        self.search_results.itemDoubleClicked.connect(self.on_search_result)
        sw = QWidget()
        sv = QVBoxLayout(sw)
        sv.setContentsMargins(10, 8, 10, 8)
        sv.setSpacing(8)
        sv.addWidget(self.search_box)
        sv.addWidget(self.search_results)
        self.engine_out = QPlainTextEdit()
        self.engine_out.setReadOnly(True)
        self.bottom = QTabWidget()
        self.bottom.setObjectName("panelTabs")
        self.bottom.tabBar().setObjectName("panelTabs")
        self.bottom.tabBar().setDrawBase(False)
        self.bottom.setIconSize(QSize(15, 15))
        self.bottom.addTab(msgs_box, icon("warning"), tr("Problems"))
        self.bottom.addTab(sw, icon("find"), tr("Advanced search"))
        self.bottom.addTab(self.engine_out, icon("terminal"), tr("Engine output"))
        # contadores das abas: só entram na aba quando há contagem (escondidos, ainda reservavam
        # ~25 px em cada aba) e vêm numa caixa com respiro para não passar do sublinhado
        self.badges = []
        for i in range(self.bottom.count()):
            box = QWidget()
            box.setObjectName("plain")
            bl = QHBoxLayout(box)
            bl.setContentsMargins(2, 0, 4, 0)
            b = QLabel()
            b.setObjectName("badge")
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b.setFixedHeight(17)
            b.setMinimumWidth(17)
            bl.addWidget(b)
            box.hide()
            self.badges.append((box, b))
        self.collapse_btn = QToolButton()
        self.collapse_btn.setAutoRaise(True)
        self.collapse_btn.setIconSize(QSize(16, 16))
        self.collapse_btn.clicked.connect(self.toggle_bottom)
        self.collapse_btn.setIcon(icon("panel_hide"))
        self.collapse_btn.setToolTip(tr("Close panel (Ctrl+J)"))
        self.bottom.setCornerWidget(self.collapse_btn, Qt.Corner.TopRightCorner)
        # começa fechado: abre com Ctrl+J, pelo contador de erros da barra de status ou sozinho
        # quando aparece um erro novo
        self.bottom_open = False
        self._shown_errors: set = set()

        self.project_tabs = ChipTabBar()
        self.project_tabs.setObjectName("projectTabs")
        self.project_tabs.setExpanding(False)
        self.project_tabs.setDrawBase(False)
        self.project_tabs.setIconSize(QSize(15, 15))
        self.project_tabs.tabCloseRequested.connect(self.close_project_at)
        self.project_tabs.currentChanged.connect(self.on_project_tab)
        # a barra 'Extra' (muda com o editor) entra na barra principal, à direita
        self.extra = QToolBar(tr("Extra"))
        self.extra.setObjectName("tb_extra")
        self.extra.setIconSize(QSize(18, 18))
        self.extra.setMovable(False)
        self.extra.setStyleSheet("QToolBar { border: none; background: transparent; padding: 0; spacing: 1px; }")
        self._extra_keys: list[QAction] = []       # atalhos da barra Extra sem botão (zoom ±)
        main_split = QSplitter(Qt.Orientation.Vertical)
        self.main_split = main_split
        work = QSplitter()
        work.addWidget(self.left_tabs)
        work.addWidget(self.center)
        work.setSizes([320, 1120])
        work.setChildrenCollapsible(False)
        main_split.addWidget(work)
        main_split.addWidget(self.bottom)
        main_split.setSizes([700, 190])
        main_split.setStretchFactor(0, 1)
        self.bottom.hide()
        self.setAcceptDrops(True)
        self.setCentralWidget(main_split)

        # barra de status: posição do cursor e contador de erros (clique abre o painel)
        self.st_cursor = QLabel()
        self.st_cursor.setObjectName("stField")
        self.st_errors = QToolButton()
        self.st_errors.setObjectName("stButton")
        self.st_errors.setAutoRaise(True)
        self.st_errors.setIconSize(QSize(14, 14))
        self.st_errors.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.st_errors.setCursor(Qt.CursorShape.PointingHandCursor)
        self.st_errors.setToolTip(tr("Open the Problems panel (Ctrl+J)"))
        self.st_errors.clicked.connect(lambda: self.show_bottom(0, toggle=True))
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(140)
        self.progress.setMaximumHeight(10)
        self.progress.setVisible(False)
        self.statusBar().addWidget(self.st_errors)
        self.statusBar().addWidget(self.st_cursor)
        self.st_msg = QLabel()
        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(lambda: self.st_msg.setText(""))
        self.statusBar().addPermanentWidget(self.st_msg)
        self.statusBar().addPermanentWidget(self.progress)

        self.build_menus()
        self.build_toolbars()
        ctx.status.connect(self.flash)
        ctx.characterChanged.connect(self.on_character)
        ctx.projectsChanged.connect(self.fill_project_tabs)
        ctx.settingsChanged.connect(self.apply_settings)
        ctx.modelChanged.connect(lambda _: QTimer.singleShot(0, self.update_title))
        ctx.themeChanged.connect(self.apply_theme)
        ctx.request_code.connect(self.goto_code)
        ctx.request_tool.connect(self.run_tool)
        ctx.request_validate.connect(self.validate)
        ctx.request_definition.connect(self.open_definition)
        ctx.cursor_info.connect(self.on_cursor)
        # vigia de arquivos (o CLI charfactory e outros editores mexem nos mesmos arquivos)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self.on_file_changed)
        self._own_writes: dict[str, float] = {}
        self._pending_external: set[str] = set()
        self._external_timer = QTimer(self)
        self._external_timer.setSingleShot(True)
        self._external_timer.setInterval(400)
        self._external_timer.timeout.connect(self.handle_external_changes)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.autosave)
        self.apply_settings()
        self.apply_theme()
        self.show_page(None)
        geo = ctx.settings.get("geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))

    def flash(self, message: str, ms: int = 6000) -> None:
        self.st_msg.setText(message)
        self._msg_timer.start(ms)

    def act(self, menu, text, fn, shortcut=None, icon_name=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        (a.toggled if checkable else a.triggered).connect(fn)
        if menu is not None:
            menu.addAction(a)
        return a

    # ------------------------------------------------------------ menus e barras

    def build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu(tr("&Project"))
        self.act(m, tr("New…"), self.new_character, "Ctrl+N", "file_new")
        self.act(m, tr("Open…"), self.open_dialog, "Ctrl+O", "open")
        self.act(m, tr("Open from archive…"), self.open_archive_dialog, "Ctrl+Shift+O", "archive")
        self.act(m, tr("Open stage…"), lambda: self.open_kind("stages"))
        self.act(m, tr("Open system screen (system/fight/select/storyboard)…"), lambda: self.open_kind("data"))
        self.recent_menu = m.addMenu(tr("Recent"))
        self.recent_menu.aboutToShow.connect(self.fill_recent)
        self.act(m, tr("Close"), self.close_project, "Ctrl+W", "close")
        self.act(m, tr("Close all"), self.close_all_projects)
        self.act(m, tr("Close others"), self.close_other_projects)
        m.addSeparator()
        self.act(m, tr("Save"), self.save, "Ctrl+S", "save")
        self.act(m, tr("Save as (copy in another folder)…"), self.save_as, "Ctrl+Shift+S")
        self.act(m, tr("Save all projects"), self.save_all, "Ctrl+Alt+S")
        self.act(m, tr("Reload from disk"), self.reload)
        m.addSeparator()
        self.act(m, tr("Load backup…"), self.load_backup, None, "backup_load")
        self.act(m, tr("Create backup"), self.create_backup, "Ctrl+B", "backup_new")
        self.act(m, tr("Compare with a backup…"), self.compare_backup)
        self.act(m, tr("Pack as .zip…"), self.pack_zip, None, "pack")
        m.addSeparator()
        self.act(m, tr("Exit"), self.close, "Ctrl+Q")

        e = mb.addMenu(tr("&Edit"))
        self.act(e, tr("Undo"), lambda: self._edit("undo"), "Ctrl+Z", "undo")
        self.act(e, tr("Redo"), lambda: self._edit("redo"), "Ctrl+Y", "redo")
        e.addSeparator()
        self.act(e, tr("Cut"), lambda: self._edit("cut"), "Ctrl+X", "cut")
        self.act(e, tr("Copy"), lambda: self._edit("copy"), "Ctrl+C", "copy")
        self.act(e, tr("Paste"), lambda: self._edit("paste"), "Ctrl+V", "paste")
        e.addSeparator()
        self.act(e, tr("Find…"), self.find_text, "Ctrl+F", "find")
        self.act(e, tr("Replace…"), lambda: self.find_text(replace=True), "Ctrl+H", "replace")
        self.act(e, tr("Find and replace in all files…"), self.find_in_files, "Ctrl+Shift+F", "find_files")
        self.act(e, tr("Go to line…"), self.goto_line, "Ctrl+G", "goto_line")
        e.addSeparator()
        self.act(e, tr("Toggle comment on selection"), self.toggle_comment, "Ctrl+;", "comment")
        self.act(e, tr("Auto-format file"), self.auto_format, "Ctrl+Alt+L")
        self.act(e, tr("Save this file as UTF-8"), self.to_utf8)
        self.auto_indent_action = self.act(e, tr("Auto indent"), self.set_auto_indent, None, None, True)
        self.auto_indent_action.setChecked(bool(self.ctx.settings.get("auto_indent", True)))
        self.act(e, tr("Fold all blocks"), lambda: self._code("fold_all"), "Ctrl+Shift+[")
        self.act(e, tr("Unfold all blocks"), lambda: self._code("unfold_all"), "Ctrl+Shift+]")
        self.act(e, tr("Fold/unfold current block"), lambda: self._code("toggle_fold"), "Ctrl+.")
        e.addSeparator()
        self.act(e, tr("Next problem"), lambda: self.jump_issue(1), "Ctrl+F8", "next_error")
        self.act(e, tr("Previous problem"), lambda: self.jump_issue(-1), "Ctrl+Shift+F8", "prev_error")
        self.act(e, tr("Next deprecated parameter"), lambda: self.jump_deprecated(1))
        self.act(e, tr("Previous deprecated parameter"), lambda: self.jump_deprecated(-1))
        e.addSeparator()
        self.act(e, tr("Code snippets…"), self.edit_snippets, None, "snippet")

        v = mb.addMenu(tr("&View"))
        self.act(v, tr("Command palette…"), self.command_palette, "F1", "command")
        self.act(v, tr("Show/hide bottom panel"), self.toggle_bottom, "Ctrl+J", "panel_hide")
        self.act(v, tr("Show/hide left panel"), lambda: self.left_tabs.setVisible(not self.left_tabs.isVisible()), "Ctrl+Alt+B")
        v.addSeparator()
        sv = v.addMenu(icon("states"), tr("State tree"))
        view_group = QActionGroup(self)
        view_group.setExclusive(True)
        for mode, text, ic in [("grouped", tr("Grouped (basics, normals, specials, supers…)"), "grp_special"),
                               ("flat", tr("Plain list (file order)"), "goto_line")]:
            a = self.act(sv, text, lambda on, m=mode: on and self.states.set_view_mode(m), None, ic, checkable=True)
            a.setChecked(self.states.view_mode() == mode)
            view_group.addAction(a)
        v.addSeparator()
        self.theme_menu = v.addMenu(tr("Theme"))
        self.theme_menu.aboutToShow.connect(self.fill_themes)
        self.act(v, tr("Theme editor…"), self.theme_editor, "Ctrl+Shift+T", "theme")
        self.act(v, tr("Import colorset (.xml)…"), self.import_colorset)
        # idioma: cada um no próprio idioma, para quem não lê o atual achar o seu
        lang_menu = v.addMenu(tr("Language"))
        lang_group = QActionGroup(self)
        for code, name in i18n.LANGUAGES.items():
            a = lang_menu.addAction(name)
            a.setCheckable(True)
            a.setChecked(code == i18n.language())
            a.triggered.connect(lambda _=False, c=code: self.choose_language(c))
            lang_group.addAction(a)
        v.addSeparator()
        self.act(v, tr("Calculator…"), lambda: dialogs.CalculatorDialog(self).exec(), "Ctrl+Alt+C")

        # uma QAction por ferramenta, posta em todos os menus onde ela aparece: o atalho fica
        # visível (e igual) em todos, sem duas ações disputando a mesma tecla
        tool = {k: self.act(None, *v) for k, v in {
            "wizard": (tr("Visual move wizard…"), self.move_wizard, "Ctrl+Shift+M", "wizard"),
            "sheet": (tr("Slice sprite sheet…"), self.sheet, "Ctrl+Shift+I", "sheet"),
            "palfx": (tr("PalFX editor…"), self.palfx, "Alt+P", "palfx"),
            "offset": (tr("Offset viewer…"), self.offset_viewer, "Alt+O", "offset"),
            "throw": (tr("Throw creator…"), self.throw, "Alt+T", "throw"),
            "cmd_test": (tr("Command tester…"), self.command_tester, "Ctrl+Shift+K", "cmd_test"),
            "movelist": (tr("Export move list (HTML)…"), self.movelist, None, "movelist"),
        }.items()}
        self.tool_actions = tool

        d = mb.addMenu(tr("&Debug"))
        self.act(d, tr("Validate character"), self.validate, "F7", "validate")
        self.act(d, tr("Run in the engine"), self.run_mugen, "Ctrl+R", "run")
        d.addSeparator()
        d.addAction(tool["cmd_test"])

        # trocar de editor fica em Exibir (e na barra); o menu do editor é um só, o do editor
        # ativo, como a barra 'Extra': entra antes de Ferramentas e muda com a página
        self.editor_actions: list[QAction] = []
        self.editor_menus: dict = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for page, name, ic, key in [(self.defs, tr("Definitions"), "definitions", "F2"),
                                    (self.sprites, tr("Sprites"), "sprites", "F3"),
                                    (self.anims, tr("Animations"), "anims", "F4"),
                                    (self.cmds, tr("Commands"), "commands", "F5"),
                                    (self.states, tr("States"), "states", "F6"),
                                    (self.sounds, tr("Sounds"), "sounds", "F8"),
                                    (self.simulator, tr("Simulator"), "simulate", "F9")]:
            a = self.act(None, name, lambda on, p=page: on and self.show_page(p), key, ic, True)
            a.setToolTip(f"{name} ({key})")
            a.setIconText(name)
            group.addAction(a)
            a.page = page
            self.editor_actions.append(a)
            menu = QMenu(name, self)
            if page is self.sprites:
                # o atalho é do botão da página (Ctrl+E); aqui vai só escrito
                self.act(menu, tr("Edit image…\tCtrl+E"), self.sprites.edit_image, None, "paint")
                menu.addAction(tool["sheet"])
                self.act(menu, tr("Advanced palette editor…"), self.sprites.open_advanced, "Ctrl+Shift+P", "palette")
                self.act(menu, tr("12 palettes by color bands…"), self.sprites.open_palettes12, None, "palettes12")
                self.act(menu, tr("Import sprites from another project…"), lambda: self.import_from_project("sprites"))
            elif page is self.anims:
                self.act(menu, tr("Save animation GIF…"), self.anims.export_gif)
                self.act(menu, tr("Import animations from another project…"), lambda: self.import_from_project("anims"))
            elif page is self.states:
                menu.addActions([tool["wizard"], tool["palfx"], tool["offset"], tool["throw"]])
                self.act(menu, tr("Insert variable usage log"), self.insert_var_log, None, "var_log")
                self.act(menu, tr("Variable usage list…"), self.var_list, None, "var_list")
            elif page is self.cmds:
                menu.addActions([tool["movelist"], tool["cmd_test"]])
                self.act(menu, tr("Arrange priority ([Statedef -1])…"), self.organize_entries)
            elif page is self.sounds:
                self.act(menu, tr("Import sounds from another project…"), lambda: self.import_from_project("sounds"))
            if menu.actions():
                self.editor_menus[page] = menu
                # fora da barra de menus o atalho só vale com a ação presa a um widget visível
                self.addActions([x for x in menu.actions() if not x.isSeparator()])
        first = v.actions()[0]
        v.insertActions(first, self.editor_actions)
        v.insertSeparator(first)

        t = mb.addMenu(tr("&Tools"))
        self._tools_menu_action = t.menuAction()
        self._editor_menu = None
        t.addActions([tool["wizard"], tool["sheet"], tool["throw"], tool["palfx"], tool["offset"]])
        t.addSeparator()
        self.act(t, tr("Unused resources…"), self.unused_resources, None, "sweep")
        self.act(t, tr("Transplant a move from another character…"), self.transplant_move, None, "transplant")
        t.addActions([tool["cmd_test"], tool["movelist"]])
        t.addSeparator()
        self.act(t, tr("Options…"), self.settings_dialog, "Ctrl+K", "options")

        h = mb.addMenu(tr("&Help"))
        self.act(h, tr("Command palette…"), self.command_palette, None, "command")
        self.act(h, tr("Reference documentation (MUGEN)"), self.open_docs, "Ctrl+F1", "help")
        self.act(h, tr("About"), lambda: QMessageBox.about(
            self, APP_NAME, tr("<b>{0}</b><br>Open source M.U.G.E.N character editor (MIT license).").format(APP_NAME)))

    def build_toolbars(self) -> None:
        """Uma faixa só: abrir/salvar, projetos abertos, editores e, à direita, as ferramentas
        do editor ativo (barra 'Extra'), rodar e validar. Novo, fechar, backup e
        documentação ficam no menu Projeto/Ajuda e nos atalhos."""
        main = QToolBar(tr("Main"))
        main.setObjectName("tb_main")
        main.setIconSize(QSize(18, 18))
        main.setMovable(False)
        main.addAction(icon("open"), tr("Open (Ctrl+O)"), self.open_dialog)
        # só com projeto aberto (na tela inicial ficavam botões que não faziam nada)
        self._char_actions = [main.addAction(icon("save"), tr("Save (Ctrl+S)"), self.save)]
        self._char_actions.append(main.addSeparator())
        self._char_actions.append(main.addWidget(self.project_tabs))
        self._char_actions.append(main.addSeparator())
        for a in self.editor_actions:
            main.addAction(a)
            btn = main.widgetForAction(a)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setProperty("segment", True)
        spacer = QWidget()
        spacer.setObjectName("plain")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        main.addWidget(spacer)
        self._extra_action = main.addWidget(self.extra)
        self._extra_sep = main.addSeparator()
        self._char_actions += [main.addAction(icon("run"), tr("Run in the engine (Ctrl+R)"), self.run_mugen),
                               main.addAction(icon("validate"), tr("Validate (F7)"), self.validate)]
        main.addAction(icon("command"), tr("Command palette (F1)"), self.command_palette)
        main.addAction(icon("options"), tr("Options (Ctrl+K)"), self.settings_dialog)
        self.addToolBar(main)

    def fill_extra(self, page) -> None:
        """Barra 'Extra' contextual (escondida quando não há ações)."""
        self._fill_extra(page)
        # a barra fica num layout: sem isso ela guarda a largura do editor anterior e manda
        # os últimos botões para o '»' de extensão
        self.extra.setMinimumWidth(self.extra.sizeHint().width())

    def _fill_extra(self, page) -> None:
        # clear() só tira as ações da barra; sem apagar, cada troca de página deixava ~18 QActions
        # órfãs (com atalhos) penduradas na barra
        from shiboken6 import isValid
        old = self.extra.actions()
        self.extra.clear()
        for a in old:       # separadores o clear() já apaga
            if isValid(a) and a.parent() is self.extra:
                a.deleteLater()
        for a in self._extra_keys:
            self.removeAction(a)
            a.deleteLater()
        self._extra_keys = []
        on = page in self.code_pages or page in (self.sprites, self.anims)
        if hasattr(self, "_extra_action"):
            self._extra_action.setVisible(on)
            self._extra_sep.setVisible(on)
        # desfazer/recortar/copiar/colar ficam no menu Editar e nos atalhos de sempre
        if page in self.code_pages:
            items = [("find", tr("Find (Ctrl+F)"), self.find_text),
                     ("goto_line", tr("Go to line (Ctrl+G)"), self.goto_line), None,
                     ("prev_error", tr("Previous problem (Ctrl+Shift+F8)"), lambda: self.jump_issue(-1)),
                     ("next_error", tr("Next problem (Ctrl+F8)"), lambda: self.jump_issue(1)),
                     ("comment", tr("Toggle comment on selection (Ctrl+;)"), self.toggle_comment)]
            for it in items:
                if it is None:
                    self.extra.addSeparator()
                else:
                    self.extra.addAction(icon(it[0]), it[1], it[2])
        elif page in (self.sprites, self.anims):
            cv = page.canvas

            def center():
                cv.pan = QPointF(0, 0)
                cv.update()
            for name, tip, fn, key in [("fit", tr("Fit to screen (Ctrl+9)"), lambda: cv.fit(8), "Ctrl+9"),
                                       ("zoom_1", tr("Actual size (Ctrl+0)"), lambda: cv.set_zoom(1), "Ctrl+0"),
                                       ("center", tr("Center origin (Home)"), center, None)]:
                act = self.extra.addAction(icon(name), tip, fn)
                if key:
                    act.setShortcut(QKeySequence(key))
            # zoom ± sem botão (a roda do mouse já dá zoom): só o atalho
            for keys, fn in [(["Ctrl+-"], lambda: cv.set_zoom(cv.zoom - 1)),
                             (["Ctrl++", "Ctrl+="], lambda: cv.set_zoom(cv.zoom + 1))]:
                act = QAction(self)
                act.setShortcuts([QKeySequence(k) for k in keys])
                act.triggered.connect(fn)
                self.addAction(act)
                self._extra_keys.append(act)
            self.extra.addSeparator()
            toggles = [("guides", tr("Guides"), "show_grid"), ("ruler", tr("Rulers"), "show_rulers"),
                       ("axis", tr("Axis"), "show_axis")]
            if page is self.anims:      # o editor de sprites não desenha caixas: os botões ficavam mortos
                toggles += [("show_attack", tr("Attack box"), "show_attack"),
                            ("show_collision", tr("Collision box"), "show_collision")]
            for name, tip, attr in toggles:
                a = self.extra.addAction(icon(name), tip)
                a.setCheckable(True)
                a.setChecked(getattr(cv, attr))
                a.toggled.connect(lambda on, at=attr, c=cv: (setattr(c, at, on), c.update()))
            if page is self.sprites:
                self.extra.addSeparator()
                a = self.extra.addAction(icon("thumbs"), tr("Organizer (thumbnails) (Ctrl+Alt+O)"))
                a.setShortcut(QKeySequence("Ctrl+Alt+O"))
                a.setCheckable(True)
                a.setChecked(self.sprites.organizer.isVisible())
                a.toggled.connect(self.sprites.toggle_organizer)

    # ------------------------------------------------------------ páginas

    def show_page(self, page) -> None:
        if page is not None and page not in self.code_pages and self.ctx.char is not None:
            self.commit_code()          # o editor visual não pode trabalhar sobre texto velho
        has_char = page is not None and self.ctx.char is not None
        # tela inicial: sem painel lateral, sem painel inferior e sem a barra de status do projeto
        self.left_tabs.setVisible(has_char)
        self.bottom.setVisible(has_char and self.bottom_open)
        for a in self._char_actions:
            a.setVisible(has_char)
        for a in self.editor_actions:
            # a QAction é a mesma do menu Exibir: escondida, F2–F9 não trocam para uma página vazia
            a.setVisible(has_char)
        for w in (self.st_cursor, self.st_errors):
            w.setVisible(has_char)
        self.set_editor_menu(page if has_char else None)
        if not has_char:
            self.center.setCurrentWidget(self.welcome)
            self.left_stack.setCurrentIndex(0)
            self.fill_extra(None)
            return
        if page in self.code_pages and not page.files:
            page.load()
            page.set_issues(self.ctx.last_issues)
        self.center.setCurrentWidget(page)
        self.left_stack.setCurrentWidget(page.left)
        self.left_tabs.setCurrentIndex(0)
        # o código das animações é uma vista do editor de Animações: o botão Animações fica marcado
        nav = self.anims if page is self.air_code else page
        for a in self.editor_actions:
            a.blockSignals(True)
            a.setChecked(getattr(a, "page", None) is nav)
            a.blockSignals(False)
        self.fill_extra(page)
        if page in self.code_pages:
            page.emit_cursor()
        else:
            self.st_cursor.setText("")

    def set_editor_menu(self, page) -> None:
        """Põe na barra de menus (antes de Ferramentas) o menu do editor ativo."""
        menu = self.editor_menus.get(self.anims if page is self.air_code else page)
        if menu is self._editor_menu:
            return
        mb = self.menuBar()
        if self._editor_menu is not None:
            mb.removeAction(self._editor_menu.menuAction())
        if menu is not None:
            mb.insertMenu(self._tools_menu_action, menu)
        self._editor_menu = menu

    def current_page(self):
        w = self.center.currentWidget()
        return w if w in self.pages else None

    def current_code_edit(self):
        p = self.current_page()
        return p.current_edit() if p in self.code_pages else None

    # ------------------------------------------------------------ edição de texto

    def _edit(self, op: str) -> None:
        """Editar › desfazer/refazer/recortar/copiar/colar no editor atual. Nos editores de
        código vale o histórico do texto; nos visuais, o do modelo (sprites, animações, sons)."""
        page = self.current_page()
        if page in self.code_pages:
            ed = page.current_edit()
            if ed:
                getattr(ed, op)()
            return
        if op in ("undo", "redo"):
            if not getattr(self.ctx, op)():
                self.ctx.status.emit(tr("Nothing to undo") if op == "undo" else tr("Nothing to redo"))
            self.update_title()
            return
        fn = getattr(page, op, None) if page else None
        if fn:
            fn()

    def find_text(self, replace: bool = False) -> None:
        from .findreplace import FindReplaceDialog
        if not self.current_code_edit():
            self.show_page(self.states if self.ctx.char else None)
        ed = self.current_code_edit()
        if not ed:
            return
        dlg = FindReplaceDialog.instance(self)
        dlg.open_for(ed.textCursor().selectedText(), replace=replace, all_files=False)

    def goto_line(self) -> None:
        ed = self.current_code_edit()
        if ed:
            n, ok = QInputDialog.getInt(self, tr("Go to line"), tr("Line:"), ed.textCursor().blockNumber() + 1, 1, ed.blockCount())
            if ok:
                ed.goto_line(n)

    def toggle_comment(self) -> None:
        ed = self.current_code_edit()
        if not ed:
            return
        c = ed.textCursor()
        start, end = sorted((c.selectionStart(), c.selectionEnd()))
        doc = ed.document()
        first, last = doc.findBlock(start).blockNumber(), doc.findBlock(end).blockNumber()
        blocks = [doc.findBlockByNumber(n) for n in range(first, last + 1)]
        uncomment = all(b.text().lstrip().startswith(";") for b in blocks if b.text().strip())
        c.beginEditBlock()
        for b in blocks:
            cur = QTextCursor(b)
            if uncomment:
                idx = b.text().find(";")
                if idx >= 0:
                    cur.setPosition(b.position() + idx)
                    cur.deleteChar()
            else:
                cur.insertText(";")
        c.endEditBlock()

    def jump_issue(self, d: int) -> None:
        ed = self.current_code_edit()
        if not ed or not ed.issue_lines:
            return
        cur = ed.textCursor().blockNumber() + 1
        lines = sorted(ed.issue_lines)
        nxt = [n for n in lines if (n > cur if d > 0 else n < cur)]
        ed.goto_line((nxt[0] if d > 0 else nxt[-1]) if nxt else (lines[0] if d > 0 else lines[-1]))

    def on_cursor(self, line: int, col: int, count: int, enc: str) -> None:
        self.st_cursor.setText(tr("Line {0}, column {1}").format(line, col))
        self.st_cursor.setToolTip(tr("{0} lines · {1}").format(count, enc.upper()) if enc else tr("{0} lines").format(count))

    # ------------------------------------------------------------ tema

    def apply_theme(self) -> None:
        from . import icons
        u = self.ctx.theme.ui
        icons.set_colors(u["icon"], u["accent"], u["text_dim"], u["error"], u["warning"])
        app = QApplication.instance()
        if not getattr(app, "_charfactory_fusion", False):   # repolir tudo de novo custava ~0,2 s por troca
            app.setStyle(QStyleFactory.create("Fusion"))
            app._charfactory_fusion = True
        app.setStyleSheet(self.ctx.theme.stylesheet())
        for w in app.allWidgets():  # repinta ícones e canvases com as novas cores
            w.update()
        self.welcome.refresh()      # os ícones dos cartões são imagens: refaz com as cores novas
        self.collapse_btn.setIcon(icon("panel_hide"))
        if self.ctx.char:
            self.update_error_count()

    def fill_themes(self) -> None:
        self.theme_menu.clear()
        self.ctx.themes = themes.load_all()
        group = QActionGroup(self)
        for name, t in self.ctx.themes.items():
            a = self.theme_menu.addAction(themes.display_name(t) + ("" if t.builtin else "  ★"))
            a.setCheckable(True)
            a.setChecked(name == self.ctx.settings["theme"])
            a.triggered.connect(lambda _=False, n=name: self.ctx.set_theme(n))
            group.addAction(a)

    def theme_editor(self) -> None:
        dialogs.ThemeEditor(self.ctx, self).exec()

    def import_colorset(self) -> None:
        start = str(Path(self.ctx.settings.get("presets_dir") or "") / "common" / "colorsets")
        path, _ = QFileDialog.getOpenFileName(self, tr("Import colorset"), start, tr("Colorset (*.xml)"))
        if path:
            t = themes.import_colorset(path)
            t.save()
            self.ctx.set_theme(t.name)

    # ------------------------------------------------------------ projeto

    def fill_recent(self) -> None:
        self.recent_menu.clear()
        for p in self.ctx.settings.get("recent", []):
            self.recent_menu.addAction(p).triggered.connect(lambda _=False, p=p: self.open_path(p))

    def open_dialog(self) -> None:
        start = str(Path(self.ctx.settings.get("mugen_dir") or Path.home()) / "chars")
        path, _ = QFileDialog.getOpenFileName(self, tr("Open project"), start,
                                              tr("MUGEN project or archive (*.def *.zip *.rar *.7z);;MUGEN project: character, stage, storyboard, fight, system, select, font (*.def);;Archived character (*.zip *.rar *.7z)"))
        if path:
            self.open_path(path)

    def open_archive_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Open from archive"), str(Path.home() / "Downloads"),
                                              tr("Archived character (*.zip *.rar *.7z)"))
        if path:
            self.open_path(path)

    def open_archive(self, path: str) -> None:
        """Extrai o compactado em chars/<nome> e abre o .def."""
        mugen = self.ctx.settings.get("mugen_dir") or None
        dest = archive.default_destination(Path(path), Path(mugen) if mugen else None)
        overwrite = False
        if dest.exists() and any(dest.iterdir()):
            box = QMessageBox(QMessageBox.Icon.Question, tr("Open from archive"),
                              tr("The folder {0} already exists.").format(dest), parent=self)
            use = box.addButton(tr("Open the existing one"), QMessageBox.ButtonRole.AcceptRole)
            repl = box.addButton(tr("Replace files"), QMessageBox.ButtonRole.DestructiveRole)
            other = box.addButton(tr("Another folder…"), QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is use:
                d = self._pick_def(archive.character_defs(dest))
                if d:
                    self.open_path(str(d))
                return
            if box.clickedButton() is other:
                d = QFileDialog.getExistingDirectory(self, tr("Extract to"), str(dest.parent))
                if not d:
                    return
                dest = Path(d) / Path(path).stem
            elif box.clickedButton() is repl:
                overwrite = True
            else:
                return
        try:
            r = archive.extract(path, dest, mugen, overwrite)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, tr("Open from archive"), str(e))
            return
        d = self._pick_def([Path(x) for x in r["defs"]])
        if d:
            self.ctx.status.emit(tr("Extracted to {0}").format(r['folder']))
            self.open_path(str(d))

    def _pick_def(self, defs: list[Path]) -> Path | None:
        if len(defs) <= 1:
            return defs[0] if defs else None
        names = [str(d) for d in defs]
        name, ok = QInputDialog.getItem(self, tr("Open"), tr("More than one character in the archive:"), names, 0, False)
        return Path(name) if ok else None

    def open_path(self, path: str) -> None:
        if Path(path).suffix.lower() in archive.ARCHIVE_SUFFIXES:
            self.open_archive(path)
            return
        if Path(path).suffix.lower() == ".def":
            from ..tools.stage import detect_kind
            kind = detect_kind(path)
            if kind != "character":       # stage, storyboard, fight, system, select, fonte
                from .stageeditor import open_window
                try:
                    w = open_window(self.ctx, path, kind, self)
                except Exception as e:  # noqa: BLE001
                    QMessageBox.warning(self, tr("Open"), f"{Path(path).name}: {e}")
                    return
                self._def_windows = [x for x in getattr(self, "_def_windows", []) if x.isVisible()] + [w]
                self.ctx.settings.add_recent(str(Path(path).resolve()))
                self.ctx.settings.save()
                return
        self.commit_code()          # o projeto atual continua aberto em outra aba
        try:
            self.ctx.open(path)
        except (ProjectError, OSError) as e:
            QMessageBox.warning(self, tr("Open"), str(e))

    def is_dirty(self) -> bool:
        c = self.ctx.char
        return bool(c and c.dirty) or any(p.is_modified() for p in self.code_pages)

    def confirm_discard(self) -> bool:
        if not self.is_dirty():
            return True
        r = QMessageBox.question(self, tr("Unsaved changes"), tr("Save the changes first?"),
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                 QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return r == QMessageBox.StandardButton.Discard

    def close_project(self) -> bool:
        c = self.ctx.char
        if not c or not self.confirm_discard():
            return False
        for p in self.code_pages:
            p.tabs.clear()
            p.files = []
        self.ctx.close(c)
        if not self.ctx.char:
            self.explorer.clear()
            self.msgs.setRowCount(0)
            self.welcome.refresh()
            self.show_page(None)
        self.watch_files()
        self.update_title()
        return True

    def new_character(self) -> None:
        d = dialogs.NewCharacterDialog(self.ctx, self)
        if d.exec() and d.created:
            self.open_path(str(d.created))

    def save(self) -> bool:
        c = self.ctx.char
        if not c:
            return False
        for p in self.code_pages:
            p.apply_all()
        self.mark_own_writes(c)
        try:
            written = c.save()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, tr("Save"), str(e))
            return False
        self.mark_own_writes(c)
        self.ctx.status.emit(tr("{0} file(s) saved").format(len(written)) if written else tr("Nothing to save"))
        self.update_title()
        self.validate()
        return True

    def reload(self) -> None:
        if self.ctx.char and self.confirm_discard():
            for p in self.code_pages:
                p.tabs.clear()
                p.files = []
            self.ctx.reopen()

    def _backup_root(self) -> Path:
        return self.ctx.char.dir / "_backups"

    def create_backup(self) -> None:
        c = self.ctx.char
        if not c:
            return
        dest = self._backup_root() / datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dest.mkdir(parents=True)
        n = 0
        for f in c.dir.iterdir():
            if f.is_file() and f.suffix.lower() in PROJECT_FILES:
                shutil.copy2(f, dest / f.name)
                n += 1
        self.ctx.status.emit(tr("Backup created with {0} file(s): {1}").format(n, dest))

    def pack_zip(self) -> None:
        if not self.need_char():
            return
        c = self.ctx.char
        if self.is_dirty() and QMessageBox.question(self, tr("Pack"), tr("Save before packing?")) == QMessageBox.StandardButton.Yes:
            self.save()
        path, _ = QFileDialog.getSaveFileName(self, tr("Pack as .zip"), str(c.dir.parent / f"{c.dir.name}.zip"), "ZIP (*.zip)")
        if path:
            r = archive.pack(c.dir, path)
            self.ctx.status.emit(tr("{0} file(s) in {1}").format(r['files'], r['zip']))

    def load_backup(self) -> None:
        c = self.ctx.char
        if not c:
            return
        d = QFileDialog.getExistingDirectory(self, tr("Choose the backup folder"), str(self._backup_root()))
        if d and QMessageBox.question(self, tr("Load backup"), tr("Replace the current files with the backup ones?")) == QMessageBox.StandardButton.Yes:
            self.mark_own_writes(c)
            for f in Path(d).rglob("*"):
                if f.is_file():
                    out = c.dir / f.relative_to(d)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, out)
            c.dirty.clear()
            for p in self.code_pages:
                for f in p.files:
                    f["edit"].document().setModified(False)
                p.tabs.clear()
                p.files = []
            self.ctx.reopen()

    def on_character(self) -> None:
        c = self.ctx.char
        if not c:
            return
        self.fill_project_tabs()
        self.fill_explorer()
        self.welcome.refresh()
        self._shown_errors = set()      # outro personagem: os erros dele contam como novos
        self.validate()
        self.show_page(self.current_page() or self.defs)
        self.update_title()
        self.watch_files()
        self.ctx.status.emit(tr("{0} opened").format(c.name))

    def update_title(self) -> None:
        c = self.ctx.char
        dirty = self.is_dirty()
        self.setWindowTitle(f"{'● ' if dirty else ''}{c.name + ' — ' if c else ''}{APP_NAME}")
        for i, p in enumerate(self.ctx.projects):
            if i < self.project_tabs.count():
                d = bool(p.dirty) or (p is c and dirty)
                self.project_tabs.setTabText(i, ("● " if d else "") + p.name)

    # ------------------------------------------------------------ explorador do projeto

    def fill_explorer(self) -> None:
        self.explorer.clear()
        c = self.ctx.char
        if not c:
            return
        root = QTreeWidgetItem([c.def_path.name])
        root.setIcon(0, icon("definitions", toggles=False))
        root.setData(0, Qt.ItemDataRole.UserRole, ("page", self.defs))
        self.explorer.addTopLevelItem(root)
        seen = set()
        for key, page, ic in [("cmd", self.cmds, "commands"), ("cns", self.states, "states"), ("st", self.states, "states"),
                              ("sprite", self.sprites, "sprites"), ("anim", self.anims, "anims"),
                              ("sound", self.sounds, "sounds"), ("stcommon", self.states, "states")]:
            p = c.path_of(key)
            if p and p.name not in seen:
                seen.add(p.name)
                it = QTreeWidgetItem([p.name + ("" if p.exists() else tr("  (not found)"))])
                it.setIcon(0, icon(ic, toggles=False))
                it.setToolTip(0, str(p))
                if not p.exists():
                    from PySide6.QtGui import QColor
                    it.setForeground(0, QColor(self.ctx.theme.ui["text_dim"]))
                it.setData(0, Qt.ItemDataRole.UserRole, ("page", page))
                root.addChild(it)
        for n, path in c.palette_files().items():
            it = QTreeWidgetItem([f"pal{n}: {path.name}"])
            it.setIcon(0, icon("palette", toggles=False))
            root.addChild(it)
        root.setExpanded(True)

    def on_explorer(self, item, _col=0) -> None:
        d = item.data(0, Qt.ItemDataRole.UserRole)
        if d and d[0] == "page":
            self.show_page(d[1])

    # ------------------------------------------------------------ validação e busca

    def validate(self) -> None:
        if not self.ctx.char:
            return
        from .. import validate as vmod
        issues = vmod.validate_character(self.ctx.char)
        self.ctx.last_issues = issues
        for p in self.code_pages:
            if p.files:
                p.set_issues(issues)
        self.fill_messages()
        self.update_error_count()
        # erro novo abre o painel sozinho; os mesmos erros de antes (ou só avisos) não
        errors = {(i.file, i.where, i.message) for i in issues if i.severity == "error"}
        if errors - self._shown_errors and self.ctx.char:
            self.show_bottom(0)
        self._shown_errors = errors

    def fill_messages(self) -> None:
        """Lista de Problemas: os erros e, com 'Mostrar avisos', também os avisos."""
        issues = self.ctx.last_issues if self.ctx.char else []
        e = sum(i.severity == "error" for i in issues)
        warnings = len(issues) - e
        shown = issues if self.show_warnings.isChecked() else [i for i in issues if i.severity == "error"]
        self.show_warnings.setText(tr("Show warnings ({0})").format(warnings))
        self.show_warnings.setEnabled(bool(warnings))
        self.msgs.setRowCount(0)
        for i in shown:
            r = self.msgs.rowCount()
            self.msgs.insertRow(r)
            sev = QTableWidgetItem()
            sev.setIcon(icon("error" if i.severity == "error" else "warning",
                             "error" if i.severity == "error" else "warning"))
            sev.setToolTip(tr("Error") if i.severity == "error" else tr("Warning"))
            self.msgs.setItem(r, 0, sev)
            for col, v in enumerate([i.file, i.where, i.message], 1):
                it = QTableWidgetItem(v)
                it.setToolTip(v)
                self.msgs.setItem(r, col, it)
        self.msgs_stack.setCurrentWidget(self.msgs if shown else self.msgs_empty)
        self.set_badge(0, e, "badgeError")

    def update_error_count(self) -> None:
        issues = self.ctx.last_issues if self.ctx.char else []
        e = sum(i.severity == "error" for i in issues)
        self.st_errors.setIcon(icon("error", "error") if e else icon("ok", "green"))
        self.st_errors.setText((tr("1 error") if e == 1 else tr("{0} errors").format(e)) if e else tr("No errors"))

    def on_msg(self, row: int, _col: int) -> None:
        self.goto_code(self.msgs.item(row, 1).text(), self.msgs.item(row, 2).text())

    def goto_code(self, filename: str, where: str) -> None:
        for p in self.code_pages:
            if not p.files and self.ctx.char:
                p.load()
                p.set_issues(self.ctx.last_issues)
            if p.has_file(filename):
                self.show_page(p)
                p.goto(filename, where)
                return

    def advanced_search(self) -> None:
        self.search_results.clear()
        text = self.search_box.text()
        if not text or not self.ctx.char:
            return
        rx = re.compile(re.escape(text), re.I)
        for p in self.code_pages:
            if not p.files:
                p.load()
            for f in p.files:
                for n, line in enumerate(f["edit"].toPlainText().split("\n"), 1):
                    if rx.search(line):
                        it = QListWidgetItem(f"{f['path'].name}:{n}:  {line.strip()[:140]}")
                        it.setData(Qt.ItemDataRole.UserRole, (self.code_pages.index(p), f["path"].name, n))
                        self.search_results.addItem(it)
        self.set_badge(1, self.search_results.count(), "badge")

    def on_search_result(self, item) -> None:
        pi, name, line = item.data(Qt.ItemDataRole.UserRole)
        page = self.code_pages[pi]
        self.show_page(page)
        for i, f in enumerate(page.files):
            if f["path"].name == name:
                page.tabs.setCurrentIndex(i)
                f["edit"].goto_line(line)

    # ------------------------------------------------------------ painel inferior

    def _empty_state(self, icon_name: str, title: str, text: str) -> QWidget:
        w = QWidget()
        w.setObjectName("emptyState")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ic = QLabel()
        ic.setPixmap(icon(icon_name, "green" if icon_name == "ok" else None).pixmap(QSize(28, 28)))
        t = QLabel(title)
        t.setObjectName("emptyTitle")
        d = QLabel(text)
        d.setObjectName("dim")
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(t)
        col.addWidget(d)
        row = QHBoxLayout(w)
        row.setContentsMargins(24, 16, 24, 16)
        row.setSpacing(14)
        row.addStretch(1)
        row.addWidget(ic)
        row.addLayout(col)
        row.addStretch(1)
        return w

    def set_badge(self, index: int, count: int, kind: str = "badge") -> None:
        box, b = self.badges[index]
        b.setText(str(count) if count < 1000 else "999+")
        b.setObjectName(kind)
        b.style().unpolish(b)
        b.style().polish(b)
        b.adjustSize()
        bar = self.bottom.tabBar()
        side = QTabBar.ButtonPosition.RightSide
        if count > 0 and bar.tabButton(index, side) is not box:
            bar.setTabButton(index, side, box)
            box.show()
        elif count <= 0 and bar.tabButton(index, side) is box:
            bar.setTabButton(index, side, None)
            box.setParent(None)
            box.hide()
        bar.update()

    def toggle_bottom(self) -> None:
        """Abre ou fecha o painel inferior (Ctrl+J), como nos IDEs."""
        self.set_bottom_open(not self.bottom_open)

    def set_bottom_open(self, on: bool) -> None:
        self.bottom_open = on
        self.bottom.setVisible(on and self.ctx.char is not None and self.current_page() is not None)

    def show_bottom(self, tab, toggle: bool = False) -> None:
        """Abre o painel inferior na aba dada (índice ou widget). Com toggle, clicar de novo
        na mesma aba aberta fecha o painel."""
        idx = tab if isinstance(tab, int) else self.bottom.indexOf(tab)
        if toggle and self.bottom_open and self.bottom.currentIndex() == idx:
            self.set_bottom_open(False)
            return
        self.bottom.setCurrentIndex(idx)
        self.set_bottom_open(True)

    # ------------------------------------------------------------ navegação

    def open_definition(self, kind: str, a: int, b: int = 0) -> None:
        """Abre no editor certo o que uma referência aponta (F12/Ctrl+clique, paleta, relatórios)."""
        c = self.ctx.char
        if not c:
            return
        if kind == "anim":
            if c.anims.get(a) is None:
                self.ctx.status.emit(tr("Animation {0} does not exist").format(a))
                return
            self.show_page(self.anims)
            self.anims.select_action(a)
        elif kind == "state":
            hit = c.all_statedefs().get(a)
            if not hit:
                self.ctx.status.emit(tr("State {0} does not exist").format(a))
                return
            self.goto_code(hit[1].path.name, f"[Statedef {a}]")
        elif kind == "sound":
            self.show_page(self.sounds)
            if not self.sounds.select(a, b):
                self.ctx.status.emit(tr("Sound {0},{1} does not exist").format(a, b))
        elif kind == "sprite":
            self.show_page(self.sprites)
            self.sprites.select(a, b)

    def palette_entries(self) -> list:
        """Itens da paleta de comandos: todas as ações dos menus e, com projeto aberto, os
        recursos do personagem."""
        out = []

        def walk(menu, path):
            for a in menu.actions():
                if a.menu():
                    walk(a.menu(), path + [a.text().replace("&", "")])
                elif a.text() and not a.isSeparator() and a.isEnabled() and a.text() != tr("Command palette…"):
                    label = " › ".join(path + [a.text().replace("&", "").rstrip("…")])
                    out.append((label, a.shortcut().toString(QKeySequence.SequenceFormat.NativeText), a.trigger))
        for top in self.menuBar().actions():
            if top.menu() and top.text().replace("&", "") != tr("Recent") and top.menu() not in self.editor_menus.values():
                walk(top.menu(), [top.text().replace("&", "")])
        for menu in self.editor_menus.values():     # os menus dos outros editores também
            walk(menu, [menu.title()])
        for p in self.ctx.settings.get("recent", [])[:10]:
            out.append((tr("Open recent › {0}/{1}").format(Path(p).parent.name, Path(p).name), tr("project"),
                        lambda p=p: self.open_path(p)))
        c = self.ctx.char
        if not c:
            return out
        try:
            for n in sorted(c.anims.actions):
                name = self.anims._action_name(n)
                out.append((f"anim {n}" + (f" · {name}" if name else ""), tr("animation"),
                            lambda n=n: self.open_definition("anim", n)))
        except ProjectError:
            pass
        for n, (sd, st) in sorted(c.all_statedefs(include_common=False).items()):
            out.append((tr("state {0}").format(n), st.path.name, lambda n=n: self.open_definition("state", n)))
        try:
            for s in c.sprites.sprites:
                out.append((f"sprite {s.group},{s.item}", f"{s.width}×{s.height}",
                            lambda g=s.group, i=s.item: self.open_definition("sprite", g, i)))
        except ProjectError:
            pass
        for s in c.sounds.sounds:
            out.append((tr("sound {0},{1}").format(s.group, s.number), tr("sound"),
                        lambda g=s.group, n=s.number: self.open_definition("sound", g, n)))
        return out

    def command_palette(self) -> None:
        dialogs.CommandPalette(self.palette_entries(), self).exec()

    # ------------------------------------------------------------ arrastar e soltar

    def _dropped(self, e) -> list[Path]:
        return [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()] if e.mimeData().hasUrls() else []

    def dragEnterEvent(self, e):  # noqa: N802
        ok = (".def", *archive.ARCHIVE_SUFFIXES) + ((*IMAGE_FILES, *AUDIO_FILES) if self.ctx.char else ())
        if any(p.suffix.lower() in ok for p in self._dropped(e)):
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        files = self._dropped(e)
        for p in files:
            if p.suffix.lower() in (".def", *archive.ARCHIVE_SUFFIXES):
                self.open_path(str(p))
                return
        images = [p for p in files if p.suffix.lower() in IMAGE_FILES]
        sounds = [p for p in files if p.suffix.lower() in AUDIO_FILES]
        if images:
            self.import_dropped_images(images)
        if sounds:
            self.import_dropped_sounds(sounds)

    def import_dropped_images(self, paths: list[Path]) -> None:
        """Imagens soltas na janela viram sprites novos no grupo atual (próximos índices livres)."""
        c = self.ctx.char
        cur = self.sprites.current()
        group = cur.group if cur else 0
        used = {s.item for s in c.sprites.sprites if s.group == group}
        item, done = 0, []
        for p in sorted(paths):
            while item in used:
                item += 1
            try:
                ops.import_sprite(c, p, group, item)
            except Exception as ex:  # noqa: BLE001
                QMessageBox.warning(self, tr("Import sprite"), f"{p.name}: {ex}")
                continue
            used.add(item)
            done.append((group, item))
        if done:
            self.ctx.changed("sprites")
            self.show_page(self.sprites)
            self.sprites.load(keep=True)
            self.sprites.select(*done[0])
            self.ctx.status.emit(tr("{0} sprite(s) imported into group {1}").format(len(done), group))

    def import_dropped_sounds(self, paths: list[Path]) -> None:
        c = self.ctx.char
        cur = self.sounds.sound()
        group = cur.group if cur else 0
        used = {s.number for s in c.sounds.sounds if s.group == group}
        n, done = 0, []
        for p in sorted(paths):
            while n in used:
                n += 1
            if self.sounds._put(str(p), group, n):          # WAV direto; OGG/MP3/FLAC pelo ffmpeg
                used.add(n)
                done.append((group, n))
        if not done:
            return
        self.ctx.changed("sounds")
        self.show_page(self.sounds)
        self.sounds.load(keep=True)
        self.sounds.select(*done[0])
        self.ctx.status.emit(tr("{0} sound(s) imported into group {1}").format(len(done), group))

    # ------------------------------------------------------------ ferramentas

    def unused_resources(self) -> None:
        if self.need_char():
            dialogs.UnusedDialog(self.ctx, self.open_definition, self).exec()

    def need_char(self) -> bool:
        if not self.ctx.char:
            QMessageBox.information(self, APP_NAME, tr("Open a project first."))
            return False
        return True

    def run_tool(self, name: str) -> None:
        {"palfx": self.palfx, "throw": self.throw, "var_list": self.var_list,
         "offset": self.offset_viewer, "wizard": self.move_wizard}.get(name, lambda: None)()

    def run_mugen(self) -> None:
        if not self.need_char():
            return
        c = self.ctx.char
        if self.is_dirty() and QMessageBox.question(self, "MUGEN", tr("Save before running?")) == QMessageBox.StandardButton.Yes:
            self.save()
        from ..tools import charkit
        root = Path(self.ctx.settings.get("mugen_dir") or (c.mugen_root or ""))
        try:
            exe = charkit.find_mugen_exe(root)
        except (FileNotFoundError, OSError):
            QMessageBox.information(self, "MUGEN", tr("Set the MUGEN folder in Tools › Options."))
            return
        try:
            arg = runner.char_argument(c, root)
        except ValueError:
            # fora de chars/: o MUGEN não carrega; instala uma cópia em chars/<pasta>
            if QMessageBox.question(self, "MUGEN", tr("The character is outside {0}.\nInstall a copy there and run it?").format(root / 'chars')) != QMessageBox.StandardButton.Yes:
                return
            r = charkit.install(c.dir, root)
            arg = f"{Path(r['installed']).name}/{c.def_path.name}"
        s = self.ctx.settings
        p2 = s.get("run_p2") or arg
        args = [str(exe), arg, p2]
        stage = s.get("run_stage") or charkit.default_stage(root)
        if stage:
            args += ["-s", stage]
        if s.get("run_p2_ai"):
            args += ["-p2.ai", str(s["run_p2_ai"])]
        proc = subprocess.Popen(args, cwd=root)
        self.engine_out.appendPlainText(f"[{datetime.datetime.now():%H:%M:%S}] {exe.name} {' '.join(args[1:])}  (pid {proc.pid})")
        self.show_bottom(2)

        def check():
            code = proc.poll()
            if code is None:
                QTimer.singleShot(1000, check)
                return
            self.engine_out.appendPlainText(tr("[{0:%H:%M:%S}] MUGEN closed (code {1})").format(datetime.datetime.now(), code))
            log = root / "mugen.log"
            if log.exists() and log.stat().st_size:
                self.engine_out.appendPlainText(log.read_text("cp1252", errors="replace")[-4000:])
        QTimer.singleShot(1000, check)

    def sheet(self) -> None:
        dialogs.SheetDialog(self.ctx, self).exec()

    def move_wizard(self, initial_anim: int | None = None) -> None:
        if isinstance(initial_anim, bool):   # vindo do menu, o Qt passa o 'checked' da ação
            initial_anim = None
        if initial_anim is None and self.current_page() is self.anims and self.anims.action():
            initial_anim = self.anims.action().number   # no editor de animações, parte da atual
        if self.need_char():
            from .movewizard import MoveWizardDialog
            MoveWizardDialog(self.ctx, self, initial_anim=initial_anim).exec()

    def throw(self) -> None:
        if self.need_char():
            dialogs.ThrowDialog(self.ctx, self).exec()

    def palfx(self) -> None:
        base = self.ctx.settings.get("presets_dir")
        presets = Path(base) / "common" / "engines" / "mugen" / "palFX" if base else None
        dialogs.PalFXDialog(self.ctx, presets, self).exec()

    def insert_code(self, text: str) -> None:
        """Insere texto no cursor do editor de código atual (ou no de Estados)."""
        page = self.current_page() if self.current_page() in self.code_pages else self.states
        if not page.files:
            page.load()
        ed = page.current_edit()
        if ed:
            ed.textCursor().insertText(text)
            ed.setFocus()

    def offset_viewer(self) -> None:
        if not self.need_char():
            return
        try:
            self.ctx.char.anims
        except ProjectError as e:
            QMessageBox.warning(self, tr("Offset viewer"), str(e))
            return
        dialogs.OffsetViewerDialog(self.ctx, self.insert_code, self).exec()

    def insert_var_log(self) -> None:
        if self.need_char():
            self.show_page(self.states)
            self.states.insert_var_log()

    def var_list(self) -> None:
        if self.need_char():
            dialogs.TextDialog(tr("Variable usage list"), ops.var_list_comment(self.ctx.char, only_used=False), self).exec()

    def movelist(self) -> None:
        if not self.need_char():
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Move list"), f"{self.ctx.char.def_path.stem}_golpes.html", "HTML (*.html)")
        if path:
            generate.movelist_html(self.ctx.char, path)

    def settings_dialog(self) -> None:
        dlg = dialogs.SettingsDialog(self.ctx, self)
        if dlg.exec() and dlg.language_changed:
            self.ask_restart()

    def choose_language(self, code: str) -> None:
        if code == (self.ctx.settings.get("language") or i18n.language()):
            return
        self.ctx.settings["language"] = code
        self.ctx.settings.save()
        self.ask_restart()

    def ask_restart(self) -> None:
        """O idioma vale ao reabrir (os rótulos são montados ao abrir): oferece reabrir agora."""
        box = QMessageBox(self)
        box.setWindowTitle(tr("Language"))
        box.setText(tr("The new language is used when the program opens again. Restart now?"))
        now = box.addButton(tr("Restart now"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("Later"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is now:
            self.restart_requested = True
            if not self.close():                  # cancelou por causa de alterações não salvas
                self.restart_requested = False

    def open_docs(self) -> None:
        import webbrowser
        root = Path(self.ctx.settings.get("mugen_dir") or (self.ctx.char.mugen_root if self.ctx.char else "") or "")
        doc = root / "docs" / "sctrls.html"
        webbrowser.open(doc.as_uri() if doc.exists() else "https://www.elecbyte.com/mugendocs/sctrls.html")

    # ------------------------------------------------------------ ferramentas extras

    def _code(self, op: str) -> None:
        ed = self.current_code_edit()
        if ed:
            getattr(ed, op)()

    def find_in_files(self) -> None:
        from .findreplace import FindReplaceDialog
        if not self.ctx.char:
            return
        ed = self.current_code_edit()
        FindReplaceDialog.instance(self).open_for(ed.textCursor().selectedText() if ed else "",
                                                   replace=True, all_files=True)

    def set_auto_indent(self, on: bool) -> None:
        from .editors.code import CodeEdit
        CodeEdit.auto_indent = on
        self.ctx.settings["auto_indent"] = on
        self.ctx.settings.save()

    def auto_format(self) -> None:
        from ..tools import edit as editmod
        ed = self.current_code_edit()
        if not ed:
            return
        text = ed.toPlainText()
        new = editmod.format_cns(text)
        if new != text:
            c = ed.textCursor()
            line = c.blockNumber() + 1
            c.beginEditBlock()
            c.select(QTextCursor.SelectionType.Document)
            c.insertText(new)
            c.endEditBlock()
            ed.goto_line(min(line, ed.blockCount()))
            self.ctx.status.emit(tr("File auto-formatted (Ctrl+Z undoes)"))

    def jump_deprecated(self, d: int) -> None:
        from .. import validate as vmod
        ed = self.current_code_edit()
        if not ed:
            return
        lines = vmod.deprecated_lines(ed.toPlainText())
        if not lines:
            self.ctx.status.emit(tr("No deprecated parameter in this file"))
            return
        cur = ed.textCursor().blockNumber() + 1
        nxt = [n for n in lines if (n > cur if d > 0 else n < cur)]
        ed.goto_line((nxt[0] if d > 0 else nxt[-1]) if nxt else (lines[0] if d > 0 else lines[-1]))

    def edit_snippets(self) -> None:
        from .findreplace import SnippetsDialog
        SnippetsDialog(self.ctx, self).exec()
        for p in self.code_pages:
            p.fill_snippets()

    # ------------------------------------------------------------ vários projetos

    def commit_code(self) -> None:
        """Aplica ao modelo o texto editado e ainda não aplicado nas páginas de código, para
        que o editor visual e o salvar não trabalhem sobre uma versão velha."""
        if self.ctx.char:
            for p in self.code_pages:
                if p.files and p.is_modified():
                    p.apply_all()

    def fill_project_tabs(self) -> None:
        self.project_tabs.blockSignals(True)
        while self.project_tabs.count():
            self.project_tabs.removeTab(0)
        for c in self.ctx.projects:
            dirty = bool(c.dirty) or (c is self.ctx.char and any(p.is_modified() for p in self.code_pages))
            i = self.project_tabs.addTab(icon("character", toggles=False), ("● " if dirty else "") + c.name)
            self.project_tabs.setTabToolTip(i, str(c.def_path))
        if self.ctx.char in self.ctx.projects:
            self.project_tabs.setCurrentIndex(self.ctx.projects.index(self.ctx.char))
        self.project_tabs.blockSignals(False)

    def on_project_tab(self, i: int) -> None:
        if 0 <= i < len(self.ctx.projects) and self.ctx.projects[i] is not self.ctx.char:
            self.commit_code()
            self.ctx.activate(self.ctx.projects[i])

    def close_project_at(self, i: int) -> None:
        if 0 <= i < len(self.ctx.projects):
            self.on_project_tab(i)
            self.close_project()

    def close_all_projects(self) -> bool:
        while self.ctx.char:
            if not self.close_project():
                return False
        return True

    def close_other_projects(self) -> None:
        self.commit_code()
        keep = self.ctx.char
        for c in [c for c in self.ctx.projects if c is not keep]:
            self.ctx.activate(c)
            if not self.close_project():
                return
        if keep:
            self.ctx.activate(keep)

    def save_all(self) -> None:
        self.commit_code()
        n = 0
        for c in self.ctx.projects:
            self.mark_own_writes(c)
            try:
                n += len(c.save())
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, tr("Save"), f"{c.name}: {e}")
            self.mark_own_writes(c)
        self.ctx.status.emit(tr("{0} file(s) saved in {1} project(s)").format(n, len(self.ctx.projects)))
        self.update_title()

    def save_as(self) -> None:
        """Grava uma cópia do projeto (com as alterações) em outra pasta e passa a editar a cópia."""
        c = self.ctx.char
        if not self.need_char():
            return
        parent = QFileDialog.getExistingDirectory(self, tr("Save as: folder where to create the copy"), str(c.dir.parent))
        if not parent:
            return
        name, ok = QInputDialog.getText(self, tr("Save as"), tr("New folder name:"), text=f"{c.dir.name}_copia")
        if not ok or not name.strip():
            return
        dest = Path(parent) / name.strip()
        if dest.exists() and any(dest.iterdir()):
            QMessageBox.warning(self, tr("Save as"), tr("{0} already exists and is not empty.").format(dest))
            return
        self.commit_code()
        shutil.copytree(c.dir, dest, ignore=shutil.ignore_patterns("_backups", "*.bak"), dirs_exist_ok=True)
        c.save_to(dest)
        new_def = dest / c.def_path.name
        c.dirty.clear()
        self.ctx.close(c)
        self.ctx.open(str(new_def))
        self.ctx.status.emit(tr("Copy saved to {0}").format(dest))

    def compare_backup(self) -> None:
        if not self.need_char():
            return
        d = QFileDialog.getExistingDirectory(self, tr("Compare with the backup"), str(self._backup_root()))
        if d:
            dialogs.BackupDiffDialog(self.ctx.char, Path(d), self.restore_backup_file, self).exec()

    def restore_backup_file(self, src: Path, rel: str) -> None:
        c = self.ctx.char
        self.mark_own_writes(c)
        shutil.copy2(src, c.dir / rel)
        for p in self.code_pages:
            p.tabs.clear()
            p.files = []
        self.ctx.reopen()
        self.ctx.status.emit(tr("{0} restored from the backup").format(rel))

    # ------------------------------------------------------------ vigia de arquivos e backup automático

    def apply_settings(self) -> None:
        from .editors.code import CodeEdit
        CodeEdit.auto_indent = bool(self.ctx.settings.get("auto_indent", True))
        m = int(self.ctx.settings.get("autosave_min") or 0)
        if m > 0:
            self.autosave_timer.start(m * 60_000)
        else:
            self.autosave_timer.stop()
        for h in self.ctx.history.values():
            h.limit = int(self.ctx.settings.get("undo_limit", 100))
        self.watch_files()

    def watch_files(self) -> None:
        if self.watcher.files():
            self.watcher.removePaths(self.watcher.files())
        c = self.ctx.char
        if c and self.ctx.settings.get("file_watcher", True):
            self.watcher.addPaths([str(p) for p in c.project_files()])

    @staticmethod

    def _key(path) -> str:
        import os
        return os.path.normcase(str(Path(path).resolve()))

    def mark_own_writes(self, c) -> None:
        """As próximas mudanças nestes arquivos (nos próximos 3 s) são do próprio app."""
        import time
        until = time.time() + 3
        for p in c.project_files():
            self._own_writes[self._key(p)] = until

    def on_file_changed(self, path: str) -> None:
        import time
        # o Windows tira o arquivo da vigia quando ele é substituído; volta a vigiar
        QTimer.singleShot(300, lambda: Path(path).exists() and path not in self.watcher.files()
                          and self.watcher.addPath(path))
        if self._own_writes.get(self._key(path), 0) > time.time():
            return
        self._pending_external.add(path)
        self._external_timer.start()

    def handle_external_changes(self) -> None:
        files, self._pending_external = sorted(self._pending_external), set()
        c = self.ctx.char
        if not c or not files:
            return
        names = ", ".join(Path(f).name for f in files)
        if self.is_dirty():
            r = QMessageBox.question(self, tr("File changed outside the app"),
                                     tr("{0} changed on disk (charfactory CLI or another editor).\n\nReload? Unsaved changes here will be lost.\n(No = keep yours; when you save, they replace what is on disk.)").format(names))
            if r != QMessageBox.StandardButton.Yes:
                return
            c.dirty.clear()
        for p in self.code_pages:
            for f in p.files:
                f["edit"].document().setModified(False)
            p.tabs.clear()
            p.files = []
        self.ctx.reopen()
        self.ctx.status.emit(tr("Reloaded from disk: {0}").format(names))

    def autosave(self) -> None:
        """Grava as partes alteradas (e o texto não aplicado) em _backups/autosave, sem mexer
        nos arquivos do projeto."""
        n = 0
        for c in self.ctx.projects:
            texts = []
            if c is self.ctx.char:
                texts = [(f["path"], f["edit"].toPlainText()) for p in self.code_pages for f in p.files
                         if f["edit"].document().isModified()]
            if not c.dirty and not texts:
                continue
            dest = c.dir / "_backups" / "autosave"
            try:
                c.save_to(dest)
                for path, text in texts:
                    try:
                        rel = Path(path).resolve().relative_to(c.dir)
                    except ValueError:
                        continue
                    (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                    (dest / rel).write_text(text, "utf-8")
                n += 1
            except Exception as e:  # noqa: BLE001
                self.ctx.status.emit(tr("Automatic backup failed ({0}): {1}").format(c.name, e))
        if n:
            self.ctx.status.emit(tr("Automatic backup of {0} project(s) in _backups/autosave").format(n))

    # ------------------------------------------------------------ ferramentas novas

    def import_from_project(self, kind: str) -> None:
        if self.need_char():
            dialogs.ImportFromProjectDialog(self.ctx, kind, self).exec()

    def transplant_move(self) -> None:
        if self.need_char():
            dialogs.TransplantDialog(self.ctx, self).exec()

    def command_tester(self) -> None:
        if self.need_char():
            dialogs.CommandTesterDialog(self.ctx, self).exec()

    def simulate_state(self, n: int) -> None:
        if self.need_char():
            self.show_page(self.simulator)
            self.simulator.run_state(n)

    def open_kind(self, folder: str) -> None:
        root = Path(self.ctx.settings.get("mugen_dir") or Path.home())
        path, _ = QFileDialog.getOpenFileName(self, tr("Open"), str(root / folder), tr("MUGEN file (*.def)"))
        if path:
            self.open_path(path)

    def organize_entries(self) -> None:
        if self.need_char():
            self.commit_code()
            dialogs.EntryOrganizerDialog(self.ctx, self).exec()

    def to_utf8(self) -> None:
        """Passa o arquivo de código atual para UTF-8; vale ao salvar."""
        page = self.current_page()
        f = page.current() if page in self.code_pages else None
        c = self.ctx.char
        if not f or not c:
            return
        page.apply(f)
        kind = f["kind"]
        if kind == "def":
            doc, part = c.definition, "def"
        elif kind == "cmd":
            doc, part = c.commands.doc, "commands"
        elif kind == "states":
            doc, part = c.states[f["index"]].doc, "states"
        else:
            doc, part = c.anims.doc, "anims"
        doc.encoding = "utf-8"
        c.mark(part)
        self.update_title()
        self.ctx.status.emit(tr("{0} will be saved as UTF-8 (Ctrl+S)").format(f['path'].name))

    # @@append

    def closeEvent(self, e) -> None:
        self.commit_code()
        for c in list(self.ctx.projects):
            self.ctx.activate(c)
            if not self.confirm_discard():
                e.ignore()
                return
        self.ctx.settings["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.ctx.settings.save()
        e.accept()

def app_icon():
    """Ícone do programa (gerado por tools/build_app_icon.py)."""
    assets = Path(__file__).parent / "assets"
    ico = assets / ("charfactory.ico" if sys.platform == "win32" else "charfactory.png")
    return QIcon(str(ico if ico.exists() else assets / "charfactory.png"))


def install_translator(app) -> None:
    """Botões e diálogos padrão do Qt (OK/Cancelar, abrir arquivo, menus de texto) no idioma do app."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    name = i18n.qt_locale_name()
    if not name:
        return                                # inglês: o Qt já é assim
    qt_tr = QTranslator(app)
    if qt_tr.load(QLocale(name), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(qt_tr)
        app._charfactory_translator = qt_tr


def run(app, argv: list[str]) -> int:
    """Abre a janela principal num QApplication já criado (o idioma já foi escolhido)."""
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    install_translator(app)
    ctx = AppContext()
    win = MainWindow(ctx)
    win.show()
    if len(argv) > 1 and argv[1].lower().endswith((".def", *archive.ARCHIVE_SUFFIXES)):
        win.open_path(argv[1])
    code = app.exec()
    if win.restart_requested:
        from PySide6.QtCore import QProcess
        from .launch import restart_command
        prog, args = restart_command()
        QProcess.startDetached(prog, args)
    return code


def main(argv: list[str] | None = None) -> int:
    from .launch import main as launch
    return launch(argv)


if __name__ == "__main__":
    sys.exit(main())
