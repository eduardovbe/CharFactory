"""IA na janela: o assistente (painel à direita), o servidor MCP para agentes externos e o
diálogo de configurações dos dois.

As ferramentas (ai.tools) sempre rodam na thread da interface, venham do assistente (que conversa
com o provedor numa QThread) ou do servidor MCP (thread do HTTP): o GuiHost leva a chamada para
lá, aplica antes o texto ainda não aplicado dos editores de código e registra no desfazer cada
parte que a ferramenta mudou.
"""
from __future__ import annotations

import html
import json
import re
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QPixmap, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QStackedWidget, QTabWidget, QTextBrowser, QToolButton,
                               QVBoxLayout, QWidget)

from ..ai import keys, mcp, providers, tools
from ..ai.agent import Agent
from ..i18n import tr
from .icons import icon
from .theme import _mix
from .widgets import hint_label

KEY_PAGES = {"anthropic": "https://console.anthropic.com/settings/keys",
             "openai": "https://platform.openai.com/api-keys",
             "gemini": "https://aistudio.google.com/apikey",
             "compatible": "https://openrouter.ai/keys"}
PART_NAMES = {"states": tr("states"), "commands": tr("commands"), "anims": tr("animations"),
              "sprites": tr("sprites"), "sounds": tr("sounds"), "def": tr("definitions")}


# ======================================================================= ferramentas na janela

class _Invoker(QObject):
    """Roda funções na thread da interface (onde foi criado), chamado de qualquer thread."""
    request = Signal(object)

    def __init__(self):
        super().__init__()
        self.request.connect(lambda job: job(), Qt.ConnectionType.QueuedConnection)


class GuiHost(tools.Host):
    def __init__(self, ctx, win):
        super().__init__()
        self.ctx, self.win = ctx, win
        self._invoker = _Invoker()
        self.turn_parts: set[str] = set()       # partes alteradas na resposta atual do assistente

    def character(self):
        return self.ctx.char

    def open_character(self, def_path: str):
        self.win.open_path(def_path)
        if self.ctx.char is None:
            raise tools.ToolError(f"could not open {def_path}")
        return self.ctx.char

    def save(self) -> list[str]:
        c = self.ctx.char
        parts = sorted(c.dirty) if c else []
        if not self.win.save():
            raise tools.ToolError("saving failed (see the message in the editor)")
        return parts

    def call(self, name: str, args: dict | None) -> tools.Result:
        if threading.current_thread() is threading.main_thread():
            return self._call(name, args)
        box: list = []
        done = threading.Event()

        def job():
            try:
                box.append(self._call(name, args))
            except Exception as e:  # noqa: BLE001  (a thread que espera não pode ficar presa)
                box.append(tools.Result(f"{type(e).__name__}: {e}", is_error=True))
            done.set()
        self._invoker.request.emit(job)
        done.wait()
        return box[0]

    def _call(self, name: str, args: dict | None) -> tools.Result:
        t = tools.REGISTRY.get(name)
        if t and t.writes and self.ctx.settings.get("ai_confirm") and not self._confirm(t, args or {}):
            return tools.Result("the user declined this change", is_error=True)
        for p in self.win.code_pages:         # o agente lê e muda o que está nos editores
            p.apply_all()
        char = self.ctx.char
        touched: set[str] = set()
        if char is not None and t and t.writes:
            mark = char.mark
            char.mark = lambda *what: (touched.update(what), mark(*what))
        try:
            res = tools.run(self, name, args)
        finally:
            if char is not None:
                char.__dict__.pop("mark", None)
        for part in sorted(touched):
            self.ctx.changed(part)
        if touched and not res.is_error:
            self.turn_parts |= touched
            self.ctx.status.emit(tr("AI changed: {0} (Ctrl+Z undoes)").format(
                ", ".join(PART_NAMES.get(p, p) for p in sorted(touched))))
        return res

    def run_engine(self, prof, engine: str | None = None) -> dict:
        """Ferramenta run_in_engine na janela: roda como o Ctrl+R (sem travar a interface)."""
        r = self.win.run_mugen(engine, prof)
        if not r:
            raise tools.ToolError("the engine did not start (the app showed the reason to the user)")
        return r

    def _view(self) -> dict:
        """O que o usuário está olhando agora (página, arquivo e linha, animação, sprite…)."""
        w = self.win
        page = w.current_page()
        try:
            if page in w.code_pages and page.current():
                f = page.current()
                cur = f["edit"].textCursor()
                lines = f["edit"].toPlainText().splitlines()[:cur.blockNumber() + 1]
                head = next((ln.strip() for ln in reversed(lines)
                             if re.match(r"\s*\[(statedef|begin action|command)\b", ln, re.I)), "")
                return {"kind": "code", "file": f["path"].name, "line": cur.blockNumber() + 1,
                        "head": head.split(";")[0].strip(), "sel": cur.selectedText().replace(" ", "\n").strip()}
            if page is w.anims and page.action():
                a = page.action()
                return {"kind": "anim", "number": a.number, "frame": page.frame_nav.index() + 1, "frames": len(a.frames)}
            if page is w.sprites and page.current():
                s = page.current()
                return {"kind": "sprite", "group": s.group, "item": s.item}
            if page is w.simulator and page.current_state() is not None:
                return {"kind": "sim", "state": page.current_state()}
        except Exception:  # noqa: BLE001  (contexto é um extra: nunca impede a mensagem)
            pass
        return {}

    def view_context(self) -> str:
        """O que está aberto e o que o usuário está olhando, para 'este golpe' ou 'esta animação'
        terem a que se referir. Vai no começo de cada mensagem (o sistema fica fixo, no cache)."""
        c, w = self.ctx.char, self.win
        if c is None:
            return "<editor>no character is open</editor>"
        out = [f'character "{c.name}" ({c.def_path})']
        dirty = sorted(c.dirty) + (["text in the code editors"] if any(p.is_modified() for p in w.code_pages) else [])
        if dirty:
            out.append("unsaved changes: " + ", ".join(dirty))
        v = self._view()
        seen = {"code": lambda: f"the code of {v['file']}, line {v['line']}" + (f", inside {v['head']}" if v["head"] else "")
                + (f"; selected text:\n{v['sel'][:1500]}" if v["sel"] else ""),
                "anim": lambda: f"animation {v['number']} in the animation editor (frame {v['frame']} of {v['frames']})",
                "sprite": lambda: f"sprite {v['group']},{v['item']} in the sprite editor",
                "sim": lambda: f"state {v['state']} in the simulator"}.get(v.get("kind"))
        if seen:
            out.append("the user is looking at " + seen())
        return "<editor>\n" + "\n".join(out) + "\n</editor>"

    def view_label(self) -> str:
        """O mesmo contexto, curto e traduzido, para o painel mostrar o que vai junto."""
        if self.ctx.char is None:
            return tr("No character open")
        v = self._view()
        kind = v.get("kind")
        if kind == "code":
            where = v["head"] or tr("line {0}").format(v["line"])
            return tr("Selection in {0}").format(v["file"]) if v["sel"] else f"{v['file']} · {where}"
        if kind == "anim":
            return tr("Animation {0} · frame {1}").format(v["number"], v["frame"])
        if kind == "sprite":
            return tr("Sprite {0},{1}").format(v["group"], v["item"])
        if kind == "sim":
            return tr("State {0} in the simulator").format(v["state"])
        return self.ctx.char.name

    def _confirm(self, t: tools.Tool, args: dict) -> bool:
        detail = "\n".join(f"{k} = {v}" for k, v in args.items())
        box = QMessageBox(QMessageBox.Icon.Question, tr("AI change"),
                          tr("An AI agent wants to run {0}.").format(t.name), parent=self.win)
        box.setInformativeText(t.description)
        if detail:
            box.setDetailedText(detail)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes


# ======================================================================= servidor MCP

class McpService:
    """Liga e desliga o servidor HTTP conforme as configurações."""

    def __init__(self, ctx, host: GuiHost):
        self.ctx, self.host = ctx, host
        self.server: mcp.HttpServer | None = None
        self.error = ""

    def token(self) -> str:
        if not self.ctx.settings.get("ai_mcp_token"):
            self.ctx.settings["ai_mcp_token"] = mcp.new_token()
            self.ctx.settings.save()
        return self.ctx.settings["ai_mcp_token"]

    def port(self) -> int:
        return int(self.ctx.settings.get("ai_mcp_port") or mcp.DEFAULT_PORT)

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port()}/mcp"

    def apply(self) -> None:
        want = bool(self.ctx.settings.get("ai_mcp_enabled"))
        if self.server and (not want or self.server.port != self.port()
                            or self.server.httpd.token != self.token()):
            self.stop()
        if want and not self.server:
            try:
                self.server = mcp.HttpServer(self.host, self.token(), self.port()).start()
                self.error = ""
            except OSError as e:
                self.error = tr("port {0} is in use or blocked ({1})").format(self.port(), e.strerror or e)
                self.ctx.status.emit(tr("MCP server not started: {0}").format(self.error))

    def stop(self) -> None:
        if self.server:
            self.server.stop()
            self.server = None


# ======================================================================= assistente

def provider_from_settings(settings) -> providers.Provider:
    name = settings.get("ai_provider") or "anthropic"
    return providers.make(name, keys.get(name), settings.get(f"ai_model_{name}") or "",
                          settings.get(f"ai_base_{name}") or "")


PROVIDER_SHORT = {"anthropic": "Claude", "openai": "OpenAI", "gemini": "Gemini", "compatible": "Local"}
INTERNAL = -1          # status de um erro do próprio app (não do provedor)


class _Worker(QObject):
    """Conversa numa thread daemon (não um QThread): fechar a janela no meio de uma resposta
    não espera a rede nem derruba o app ao sair. Os sinais chegam na thread da interface.
    Sem texto, continua a conversa de onde parou (Tentar de novo depois de um erro)."""
    event = Signal(str, object)
    finished_ok = Signal()
    failed = Signal(str, object)           # mensagem, status HTTP (None: rede; INTERNAL: do app)

    def __init__(self, agent: Agent, text: str | None):
        super().__init__()
        self.agent, self.text = agent, text
        self.thread = threading.Thread(target=self.run, name="charfactory-assistant", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def isRunning(self) -> bool:  # noqa: N802
        return self.thread.is_alive()

    def run(self):
        emit = lambda kind, data: self.event.emit(kind, data)  # noqa: E731
        try:
            if self.text is None:
                self.agent.resume(emit)
            else:
                self.agent.send(self.text, emit)
            self.finished_ok.emit()
        except providers.ProviderError as e:
            self.failed.emit(str(e), e.status)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}", INTERNAL)


def _step_label(name: str, args: dict) -> str:
    """O que uma ferramenta fez, em palavras; o nome e os argumentos crus ficam nos detalhes."""
    a = args or {}
    n = a.get("number", a.get("state", ""))
    sprite = f"{a.get('group')},{a.get('item')}"
    labels = {
        "open_character": lambda: tr("Opened {0}").format(Path(str(a.get("def_path", ""))).name),
        "character_info": lambda: tr("Read the character summary"),
        "validate_character": lambda: (tr("Checked state {0} for problems").format(n) if "number" in a
                                       else tr("Checked the character for problems")),
        "save_project": lambda: tr("Saved the project"),
        "unused_resources": lambda: tr("Looked for unused resources"),
        "list_states": lambda: tr("Listed the states"),
        "get_state": lambda: tr("Read state {0}").format(n),
        "get_move": lambda: tr("Read move {0}").format(n),
        "set_state": lambda: tr("Wrote state {0}").format(n),
        "add_controller": lambda: tr("Added a controller to state {0}").format(n),
        "edit_state": lambda: tr("Changed state {0}").format(n),
        "remove_state": lambda: tr("Deleted state {0}").format(n),
        "remove_controller": lambda: tr("Deleted a controller of state {0}").format(n),
        "get_constants": lambda: tr("Read the constants"),
        "set_constant": lambda: tr("Set {0} to {1}").format(f"{a.get('section')}.{a.get('key')}", a.get("value")),
        "list_commands": lambda: tr("Listed the commands"),
        "add_command": lambda: tr("Added command {0}").format(a.get("name", "")),
        "test_command": lambda: tr("Tested the input {0}").format(a.get("input") or a.get("command") or ""),
        "create_move": lambda: tr("Created move {0}").format(n),
        "build_move": lambda: tr("Built a move in {0} steps").format(len(a.get("steps") or [])),
        "list_animations": lambda: tr("Listed the animations"),
        "view_animations": lambda: tr("Looked at animations {0}").format(", ".join(map(str, a.get("numbers", [])[:8]))),
        "get_animation": lambda: tr("Read animation {0}").format(n),
        "set_animation": lambda: tr("Wrote animation {0}").format(
            ", ".join(re.findall(r"begin action\s+(-?\d+)", str(a.get("text", "")), re.I)[:6])),
        "build_animation": lambda: tr("Built animation {0}").format(n),
        "edit_animation": lambda: tr("Changed animation {0}").format(n),
        "remove_animation": lambda: tr("Deleted animation {0}").format(n),
        "view_animation": lambda: tr("Looked at animation {0}").format(n),
        "suggest_clsn": lambda: tr("Fitted boxes to sprite {0}").format(sprite),
        "list_sprites": lambda: tr("Listed the sprites"),
        "view_sprite": lambda: tr("Looked at sprite {0}").format(sprite),
        "list_palettes": lambda: tr("Listed the palettes"),
        "list_sounds": lambda: tr("Listed the sounds"),
        "mugen_guide": lambda: tr("Read the guide: {0}").format(a.get("topic", "")),
        "mugen_reference": lambda: tr("Looked up {0} in the reference").format(a.get("name", "")),
        "simulate_state": lambda: tr("Simulated state {0}").format(n),
        "inspect_images": lambda: tr("Looked at {0} image(s)").format(len(a.get("paths") or [])),
        "import_sprites": lambda: tr("Imported {0} image(s) as sprites {1}").format(len(a.get("paths") or []), sprite),
        "import_palette_from_image": lambda: tr("Took palette {0} from {1}").format(
            a.get("slot", 1), Path(str(a.get("path", ""))).name),
        "list_run_profiles": lambda: tr("Read the run profiles"),
        "run_in_engine": lambda: tr("Ran the character in the engine ({0})").format(a.get("profile") or tr("active profile")),
    }
    try:
        return labels[name]()
    except (KeyError, TypeError, ValueError, AttributeError):
        return name


def _friendly_error(msg: str, status) -> str:
    """O que o erro quer dizer e o que fazer; a mensagem crua do provedor vai embaixo, menor."""
    if status == 429:
        return tr("The provider's usage limit was reached (requests per minute or the daily quota). "
                  "Wait a little and try again, or check the plan of your API key.")
    if status in (401, 403):
        return tr("The provider refused the API key. Check it in AI settings.")
    if status == 404:
        return tr("The provider does not know this model. Choose another one in AI settings.")
    if isinstance(status, int) and status >= 500:
        return tr("The provider is overloaded or failing right now. Try again in a moment.")
    if status == INTERNAL:
        return tr("Something went wrong in Char Factory while running the assistant.")
    if msg.startswith("stopped after"):
        return tr("The assistant used every step it has for one answer. Ask it to continue, or split the request.")
    if status is None and "empty response" not in msg and "no answer from the model" not in msg:
        return tr("Could not reach the provider. Check the internet connection, or that the local "
                  "server (Ollama, LM Studio) is running.")
    return tr("The provider could not answer this request.")


def _markdown(text: str, u: dict) -> str:
    """O pouco de Markdown que os modelos usam: blocos de código, `código`, **negrito**, *itálico*,
    títulos, listas e links."""
    out, parts = [], re.split(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", text, flags=re.S)
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f"<table width='100%' cellspacing='0' cellpadding='8' bgcolor='{u['code_bg']}' "
                       f"style='margin-top:4px; margin-bottom:4px'><tr><td><pre style='margin:0'>"
                       f"{html.escape(part.rstrip())}</pre></td></tr></table>")
            continue
        s = html.escape(part)
        s = re.sub(r"`([^`\n]+)`", rf"<code style='background-color:{u['code_bg']}'>&nbsp;\1&nbsp;</code>", s)
        s = re.sub(r"(?m)^(\s*)[-*] ", r"\1• ", s)
        s = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"<i>\1</i>", s)
        s = re.sub(r"(?m)^#{1,6} (.+)$", r"<b>\1</b>", s)
        s = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", rf"<a href='\2' style='color:{u['accent']}'>\1</a>", s)
        out.append(s.strip("\n").replace("\n", "<br>"))
    return "".join(out)


class AssistantPanel(QWidget):
    """Conversa com o modelo escolhido em Configurações de IA.

    A conversa fica guardada como itens (mensagem, passo, imagem, erro) e é desenhada a partir
    deles: trocar o tema redesenha tudo com as cores novas."""

    @staticmethod
    def suggestions() -> tuple[str, ...]:
        return (tr("Explain what the move I am looking at does"), tr("Find and fix the character's errors"),
                tr("Create a new crouching kick"), tr("Make this animation a little faster"))

    def __init__(self, ctx, host: GuiHost, win):
        super().__init__()
        self.ctx, self.host, self.win = ctx, host, win
        self.agent: Agent | None = None
        self.worker: _Worker | None = None
        self._items: list[tuple] = []
        self._steps: list[tuple] = []           # (Call, Result) de cada passo, para os detalhes
        self._images: dict[str, QImage] = {}
        self._last_text = ""
        self._busy_text, self._dots = "", 0
        self.setObjectName("sidePanel")
        self.setMinimumWidth(320)

        head = QHBoxLayout()
        head.setContentsMargins(12, 8, 6, 4)
        title = QLabel(tr("Assistant"))
        title.setObjectName("panelTitle")
        self.model_btn = QToolButton()
        self.model_btn.setAutoRaise(True)
        self.model_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.model_btn.setToolTip(tr("Provider and model (AI settings)"))
        self.model_btn.clicked.connect(lambda: self.win.ai_settings())
        new = QToolButton()
        new.setAutoRaise(True)
        new.setIcon(icon("file_new"))
        new.setToolTip(tr("New conversation"))
        new.clicked.connect(self.new_chat)
        close = QToolButton()
        close.setAutoRaise(True)
        close.setIcon(icon("close"))
        close.setToolTip(tr("Close (Ctrl+Shift+A)"))
        close.clicked.connect(lambda: self.win.toggle_assistant(False))
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.model_btn)
        head.addWidget(new)
        head.addWidget(close)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.view.document().setDocumentMargin(12)
        self.view.anchorClicked.connect(self._link)
        self.welcome = self._build_welcome()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.view)

        self.busy_label = QLabel()
        self.busy_label.setObjectName("aiBusy")
        self.busy_label.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._tick)

        self.composer = QFrame()
        self.composer.setObjectName("aiComposer")
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(tr("Ask about the character or ask for a change"))
        self.input.setToolTip(tr("Enter sends · Shift+Enter breaks the line · Esc stops · ↑ repeats the last message"))
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._input_changed)
        self.context_label = QLabel()
        self.context_label.setObjectName("aiContext")
        self.context_label.setToolTip(tr("Sent with each message, so the assistant knows what \"this\" means"))
        self.context_label.setMinimumWidth(10)
        self.send_btn = QPushButton(icon("send"), tr("Send"))
        self.send_btn.setProperty("primary", True)
        self.send_btn.clicked.connect(self.send_or_stop)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.context_label, 1)
        row.addWidget(self.send_btn)
        cl = QVBoxLayout(self.composer)
        cl.setContentsMargins(8, 6, 8, 8)
        cl.setSpacing(4)
        cl.addWidget(self.input)
        cl.addLayout(row)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addWidget(self.stack, 1)
        # atalhos do que está na tela (explicar este estado, ajustar estas caixas…)
        self.quick = QWidget()
        self.quick_row = QHBoxLayout(self.quick)
        self.quick_row.setContentsMargins(0, 0, 0, 0)
        self.quick_row.setSpacing(4)
        self.quick.hide()
        box = QVBoxLayout()
        box.setContentsMargins(10, 4, 10, 10)
        box.setSpacing(4)
        box.addWidget(self.quick)
        box.addWidget(self.busy_label)
        box.addWidget(self.composer)
        lay.addLayout(box)

        self._ctx_timer = QTimer(self)
        self._ctx_timer.setInterval(700)
        self._ctx_timer.timeout.connect(self._refresh_context)
        self._ctx_timer.start()
        ctx.themeChanged.connect(self._rerender)
        self.refresh()
        self._input_changed()
        self._refresh_context()

    # ------------------------------------------------------------ tela inicial

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        w.setObjectName("sidePanel")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 18, 16, 12)
        lay.setSpacing(10)
        badge = QLabel()
        badge.setPixmap(icon("ai").pixmap(28, 28))
        title = QLabel(tr("AI assistant"))
        title.setObjectName("aiTitle")
        text = QLabel(tr("It reads and changes the open character, looks at the animations and simulates "
                         "the moves. Every change can be undone with Ctrl+Z, and nothing is written to "
                         "disk until you save."))
        text.setObjectName("dim")
        text.setWordWrap(True)

        self.setup = QFrame()
        self.setup.setObjectName("aiSetup")
        sl = QVBoxLayout(self.setup)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(8)
        self.setup_text = QLabel()
        self.setup_text.setWordWrap(True)
        setup_btn = QPushButton(icon("options"), tr("Set up a provider"))
        setup_btn.setProperty("primary", True)
        setup_btn.clicked.connect(lambda: self.win.ai_settings())
        self.mcp_link = QLabel()
        self.mcp_link.setWordWrap(True)
        self.mcp_link.linkActivated.connect(lambda _: self.win.ai_settings(1))
        sl.addWidget(self.setup_text)
        sl.addWidget(setup_btn, 0, Qt.AlignmentFlag.AlignLeft)
        sl.addWidget(self.mcp_link)

        lay.addWidget(badge)
        lay.addWidget(title)
        lay.addWidget(text)
        lay.addSpacing(4)
        lay.addWidget(self.setup)
        try_label = QLabel(tr("Try"))
        try_label.setObjectName("hint")
        lay.addWidget(try_label)
        for s in self.suggestions():
            chip = QPushButton()
            chip.setObjectName("aiChip")
            chip.setText(s)
            chip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            chip.clicked.connect(lambda _=False, b=chip: self._suggest(b.text()))
            lay.addWidget(chip)
        lay.addStretch(1)
        return w

    def _suggest(self, text: str) -> None:
        self.input.setPlainText(text)
        self.send_or_stop()

    def ask(self, text: str) -> None:
        """Pedido vindo de outra página: manda já, ou deixa no campo se uma resposta está em curso."""
        self.input.setPlainText(text)
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        if not self.busy():
            self.send_or_stop()

    @staticmethod
    def quick_actions(kind: str | None) -> tuple[str, ...]:
        """Atalhos para o que está na tela (mudam com a página)."""
        return {
            "code": (tr("Explain this state"), tr("Check this state for errors")),
            "anim": (tr("Fit the collision boxes of this animation"), tr("Make this animation smoother")),
            "sprite": (tr("Fit collision boxes to this sprite"), tr("Is the axis of this sprite right?")),
            "sim": (tr("Fix the problems the simulator found"), tr("Make this move combo better")),
        }.get(kind or "", ())

    def _fill_quick(self) -> None:
        kind = self.host._view().get("kind") if self.ctx.char is not None else None
        if kind == getattr(self, "_quick_kind", object()):
            return
        self._quick_kind = kind
        while self.quick_row.count():
            w = self.quick_row.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for text in self.quick_actions(kind):
            b = QPushButton(text)
            b.setObjectName("aiChip")
            b.setToolTip(tr("Sends: {0}").format(text))
            b.clicked.connect(lambda _=False, t=text: self._suggest(t))
            self.quick_row.addWidget(b)
        self.quick.setVisible(bool(self.quick_actions(kind)))

    def _colors(self) -> dict:
        # o fundo do código sai do painel (o do editor pode ser igual ao painel em temas claros)
        u = self.ctx.theme.ui
        return {**u, "code_bg": _mix(u["panel"], u["text"], 0.08)}

    def refresh(self) -> None:
        """Provedor ou modelo mudou nas configurações: a próxima mensagem já usa o novo."""
        u = self._colors()
        try:
            p = provider_from_settings(self.ctx.settings)
        except providers.ProviderError:
            p = None
        if p is None:
            self.model_btn.setText(tr("Choose a provider"))
        else:
            short = PROVIDER_SHORT.get(p.name, p.label)
            same = p.model.lower().startswith((short.lower(), "gpt", "o1", "o3", "o4"))
            self.model_btn.setText(p.model if p.model and same and p.name != "compatible" else f"{short} · {p.model or '?'}")
        missing = p is None or (p.needs_key and not p.api_key) or not p.model
        if p is not None and p.needs_key and not p.api_key:
            self.setup_text.setText(tr("No API key for {0}.").format(p.label))
        elif p is not None and not p.model:
            self.setup_text.setText(tr("Choose a model for {0}.").format(p.label))
        self.setup.setVisible(missing)
        self.mcp_link.setText(f"<a href='mcp' style='color:{u['accent']}'>"
                              + html.escape(tr("Or use your Claude, ChatGPT or Gemini subscription through MCP"))
                              + "</a>")

    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def focus_input(self) -> None:
        self.input.setFocus()

    def new_chat(self) -> None:
        if self.busy():
            return
        self.agent = None
        self._items.clear()
        self._steps.clear()
        self._images.clear()
        self.view.clear()
        self.stack.setCurrentWidget(self.welcome)
        self.focus_input()

    # ------------------------------------------------------------ campo de texto

    def eventFilter(self, obj, e):  # noqa: N802
        if obj is self.input:
            t = e.type()
            if t == QEvent.Type.KeyPress:
                key, mods = e.key(), e.modifiers()
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not mods & Qt.KeyboardModifier.ShiftModifier:
                    self.send_or_stop() if not self.busy() else None
                    return True
                if key == Qt.Key.Key_Escape and self.busy():
                    self.send_or_stop()
                    return True
                if key == Qt.Key.Key_Up and not self.input.toPlainText() and self._last_text:
                    self.input.setPlainText(self._last_text)
                    self.input.moveCursor(QTextCursor.MoveOperation.End)
                    return True
            elif t in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self.composer.setProperty("focus", t == QEvent.Type.FocusIn)
                self.composer.style().unpolish(self.composer)
                self.composer.style().polish(self.composer)
        return super().eventFilter(obj, e)

    def _input_changed(self) -> None:
        """O campo cresce com o texto (2 a 8 linhas) e o Enviar só acende com algo escrito."""
        lines = max(2, min(8, int(self.input.document().documentLayout().documentSize().height())))
        self.input.setFixedHeight(lines * self.input.fontMetrics().lineSpacing() + 10)
        self.send_btn.setEnabled(self.busy() or bool(self.input.toPlainText().strip()))

    def _refresh_context(self) -> None:
        if not self.isVisible():
            return
        self._fill_quick()
        text = tr("Sees: {0}").format(self.host.view_label())
        width = max(40, self.context_label.width())
        self.context_label.setText(self.context_label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, width))

    # ------------------------------------------------------------ conversa

    def send_or_stop(self) -> None:
        if self.busy():
            if self.agent:
                self.agent.cancelled = True
            self._busy_text = tr("Stopping after the current step")
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.refresh()
        p = provider_from_settings(self.ctx.settings)
        if (p.needs_key and not p.api_key) or not p.model:
            self.win.ai_settings()
            return
        if self.agent is None or self.agent.provider.name != p.name:
            if self.agent is not None:
                self._add(("note", tr("New conversation with {0}: one provider cannot read another's history.")
                           .format(p.label)))
            self.agent = Agent(p, self.host)
        else:
            self.agent.provider = p               # outra chave ou modelo: a conversa continua
        self._last_text = text
        self.input.clear()
        self._add(("user", text))
        self._start(self.host.view_context() + "\n\n" + text)

    def _start(self, text: str | None) -> None:
        self.host.turn_parts.clear()
        self.worker = _Worker(self.agent, text)
        self.worker.event.connect(self.on_event)
        self.worker.finished_ok.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.show_busy(True)
        self.worker.start()

    def show_busy(self, on: bool, text: str = "") -> None:
        """Enviar vira Parar e aparece o progresso animado (ou o contrário)."""
        self.send_btn.setText(tr("Stop") if on else tr("Send"))
        self.send_btn.setIcon(icon("stop" if on else "send"))
        self.send_btn.setProperty("primary", not on)
        self.send_btn.setProperty("danger", on)
        self._repolish(self.send_btn)
        self.busy_label.setVisible(on)
        if on:
            self.send_btn.setEnabled(True)
            self._busy_text = text or tr("Thinking")
            self._tick()
            self._timer.start()
        else:
            self._timer.stop()
            self._input_changed()

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.busy_label.setText(self._busy_text + "." * self._dots)

    def on_event(self, kind: str, data) -> None:
        if kind == "text":
            self._add(("text", data))
            self._busy_text = tr("Thinking")
        elif kind == "tool":
            self._busy_text = _step_label(data.name, data.args)
        elif kind == "result":
            call, res = data
            self._steps.append((call, res))
            self._add(("step", len(self._steps) - 1))
            for png in res.images:
                img = QImage.fromData(png)
                if img.isNull():
                    continue
                key = f"ai-image-{len(self._images) + 1}"
                self._images[key] = img
                self.view.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(key), img)
                self._add(("image", key))
            self._busy_text = tr("Thinking")
        elif kind == "stopped":
            self._add(("note", tr("Stopped.")))

    def _idle(self) -> None:
        self.show_busy(False)

    def on_done(self) -> None:
        self._idle()
        if self.host.turn_parts:
            names = ", ".join(PART_NAMES.get(p, p) for p in sorted(self.host.turn_parts))
            self._add(("note", tr("AI changed: {0} (Ctrl+Z undoes)").format(names)))

    def on_failed(self, msg: str, status=None) -> None:
        self._idle()
        self._add(("error", msg, status))

    @staticmethod
    def _repolish(w) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    # ------------------------------------------------------------ desenho

    def _add(self, item: tuple) -> None:
        self._items.append(item)
        self.stack.setCurrentWidget(self.view)
        sb = self.view.verticalScrollBar()
        at_end = sb.value() >= sb.maximum() - 8        # quem subiu para ler não é puxado para baixo
        self.view.append(self._render(item, self._colors()))
        if at_end:
            sb.setValue(sb.maximum())

    def _rerender(self) -> None:
        """Tema novo: redesenha a conversa e os links com as cores dele."""
        self.refresh()
        if not self._items:
            return
        self.view.clear()
        for key, img in self._images.items():
            self.view.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(key), img)
        u = self._colors()
        for item in self._items:
            self.view.append(self._render(item, u))

    def _render(self, item: tuple, u: dict) -> str:
        kind = item[0]
        if kind == "user":
            body = html.escape(item[1]).replace("\n", "<br>")
            return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:12px'><tr>"
                    f"<td width='14%'></td><td bgcolor='{u['accent_soft']}' style='padding:8px'>{body}</td>"
                    "</tr></table>")
        if kind == "text":
            return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:10px'><tr><td>"
                    f"{_markdown(item[1], u)}</td></tr></table>")
        if kind == "step":
            call, res = self._steps[item[1]]
            color = u["warning"] if res.is_error else u["text_dim"]
            mark = "✗" if res.is_error else "✓"
            line = (f"<a href='step:{item[1]}' style='color:{color}; text-decoration:none'>{mark}&nbsp;"
                    f"{html.escape(_step_label(call.name, call.args))}</a>")
            if res.is_error:
                first = res.text.strip().splitlines()[0] if res.text.strip() else ""
                line += f"<span style='color:{u['text_dim']}'> — {html.escape(first[:110])}</span>"
            return f"<div style='margin-top:2px; font-size:8pt'>{line}</div>"
        if kind == "image":
            img = self._images[item[1]]
            w = min(img.width(), max(160, self.view.viewport().width() - 34))
            return (f"<div style='margin-top:4px'><a href='img:{item[1]}'>"
                    f"<img src='{item[1]}' width='{w}'></a></div>")
        if kind == "note":
            return f"<div style='margin-top:6px; color:{u['text_dim']}; font-size:8pt'>{html.escape(item[1])}</div>"
        if kind == "error":
            msg, status = item[1], item[2]
            bg = _mix(u["panel"], u["error"], 0.14)
            link = f"style='color:{u['accent']}'"
            return (f"<table width='100%' cellspacing='0' cellpadding='8' bgcolor='{bg}' style='margin-top:8px'>"
                    f"<tr><td><span style='color:{u['error']}'><b>{html.escape(tr('Error'))}</b></span> "
                    f"{html.escape(_friendly_error(msg, status))}<br>"
                    f"<span style='color:{u['text_dim']}; font-size:8pt'>{html.escape(msg[:400])}</span><br>"
                    f"<a href='action:retry' {link}>{html.escape(tr('Try again'))}</a>&nbsp;&nbsp;·&nbsp;&nbsp;"
                    f"<a href='action:settings' {link}>{html.escape(tr('AI settings'))}</a></td></tr></table>")
        return ""

    def _link(self, url: QUrl) -> None:
        s = url.toString()
        if s.startswith("step:"):
            call, res = self._steps[int(s[5:])]
            text = (f"{call.name}\n{json.dumps(call.args, ensure_ascii=False, indent=2)}\n\n"
                    f"── {tr('Result')} ──\n{res.text}")
            self._detail(_step_label(call.name, call.args), text)
        elif s.startswith("img:"):
            self._image(self._images.get(s[4:]))
        elif s == "action:retry":
            if not self.busy() and self.agent is not None:
                self._start(None)
        elif s == "action:settings":
            self.win.ai_settings()
        else:
            QDesktopServices.openUrl(url)

    def _detail(self, title: str, text: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(620, 520)
        ed = QPlainTextEdit(text)
        ed.setReadOnly(True)
        ed.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addWidget(ed, 1)
        lay.addWidget(buttons)
        dlg.exec()

    def _image(self, img: QImage | None) -> None:
        if img is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Image"))
        label = QLabel()
        label.setPixmap(QPixmap.fromImage(img))
        area = QScrollArea()
        area.setWidget(label)
        area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(dlg)
        lay.addWidget(area)
        dlg.resize(min(img.width() + 40, 1400), min(img.height() + 50, 900))
        dlg.exec()


# ======================================================================= configurações

def _client_snippets(url: str, token: str) -> dict[str, tuple[str, str]]:
    """Configuração de cada cliente MCP: (onde colar, texto)."""
    auth = f"Bearer {token}"
    if getattr(sys, "frozen", False):
        command, args = sys.executable, ["--mcp"]
    else:
        command, args = sys.executable, ["-m", "charfactory.cli", "mcp"]
    j = lambda o: json.dumps(o, indent=2, ensure_ascii=False)
    return {
        "Claude Code": (tr("Run in a terminal:"),
                        f'claude mcp add --transport http charfactory {url} --header "Authorization: {auth}"'),
        "Claude Desktop": (tr("Add to claude_desktop_config.json (Settings > Developer > Edit Config) and restart Claude:"),
                           j({"mcpServers": {"charfactory": {"command": command, "args": args}}})),
        "Codex": (tr("Add to ~/.codex/config.toml:"),
                  f'[mcp_servers.charfactory]\nurl = "{url}"\nhttp_headers = {{ "Authorization" = "{auth}" }}'),
        "Gemini CLI": (tr("Add to ~/.gemini/settings.json:"),
                       j({"mcpServers": {"charfactory": {"httpUrl": url, "headers": {"Authorization": auth}}}})),
        "Cursor": (tr("Add to ~/.cursor/mcp.json:"),
                   j({"mcpServers": {"charfactory": {"url": url, "headers": {"Authorization": auth}}}})),
        "VS Code": (tr("Add to .vscode/mcp.json:"),
                    j({"servers": {"charfactory": {"type": "http", "url": url, "headers": {"Authorization": auth}}}})),
        tr("Other"): (tr("Streamable HTTP server; send the token in the Authorization header:"),
                      f"URL: {url}\nAuthorization: {auth}\n\nstdio: {command} {' '.join(args)}"),
    }


class AISettingsDialog(QDialog):
    def __init__(self, ctx, mcp_service: McpService, parent=None, tab: int = 0):
        super().__init__(parent)
        self.ctx, self.mcp = ctx, mcp_service
        self.setWindowTitle(tr("AI settings"))
        self.resize(640, 560)
        s = ctx.settings
        self._keys: dict[str, str] = {}               # chaves digitadas, gravadas no OK
        self._models: dict[str, str] = {}
        self._bases: dict[str, str] = {}

        # --- assistente
        a = QWidget()
        form = QFormLayout(a)
        self.provider = QComboBox()
        for p in providers.PROVIDERS.values():
            self.provider.addItem(p.label, p.name)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_link = QLabel()
        self.key_link.setOpenExternalLinks(True)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.key_link)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # testar a conexão é pedir a lista de modelos: um botão só, ao lado do modelo
        test = QPushButton(icon("reload"), tr("Test connection"))
        test.setToolTip(tr("Checks the key and loads the provider's models into the list"))
        test.clicked.connect(self.load_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(test)
        self.base = QLineEdit()
        self.test_label = QLabel()
        self.test_label.setWordWrap(True)
        self.test_label.hide()
        model_box = QVBoxLayout()
        model_box.setSpacing(4)
        model_box.addLayout(model_row)
        model_box.addWidget(self.test_label)
        form.addRow(tr("Provider"), self.provider)
        form.addRow(tr("API key"), key_row)
        form.addRow(tr("Model"), model_box)
        form.addRow(tr("Base URL"), self.base)
        form.addRow(hint_label(tr(
            "The key is stored in the Windows Credential Manager, never in the settings file. Without a "
            "saved key, the provider's environment variable is used (ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "GEMINI_API_KEY). For local models, choose OpenAI-compatible and point the base URL to Ollama "
            "or LM Studio.\n\nTo use a Claude, ChatGPT or Gemini subscription instead of an API key, "
            "connect that app to Char Factory in the Connect agents tab.")))
        self._current = s.get("ai_provider") or "anthropic"
        self.provider.setCurrentIndex(max(0, self.provider.findData(self._current)))
        self._show_provider(self._current)
        self.provider.currentIndexChanged.connect(self._provider_changed)

        # --- agentes externos (MCP)
        m = QWidget()
        ml = QVBoxLayout(m)
        ml.addWidget(hint_label(tr(
            "Claude Code, Claude Desktop, Codex, Gemini CLI, Cursor and other MCP clients can read and edit "
            "the open character, with your own account and subscription. Changes show up in the editor "
            "and can be undone with Ctrl+Z.")))
        self.enabled = QCheckBox(tr("Run the MCP server while Char Factory is open"))
        self.enabled.setChecked(bool(s.get("ai_mcp_enabled")))
        self.confirm = QCheckBox(tr("Ask before an AI changes the character"))
        self.confirm.setChecked(bool(s.get("ai_confirm")))
        mf = QFormLayout()
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(mcp_service.port())
        self.token = QLineEdit(mcp_service.token())
        self.token.setReadOnly(True)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        show = QToolButton()
        show.setText(tr("Show"))
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.token.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        regen = QPushButton(tr("New token"))
        regen.setToolTip(tr("Clients configured with the old token stop working"))
        regen.clicked.connect(self.new_token)
        tok_row = QHBoxLayout()
        tok_row.addWidget(self.token, 1)
        tok_row.addWidget(show)
        tok_row.addWidget(regen)
        mf.addRow(tr("Port"), self.port)
        mf.addRow(tr("Token"), tok_row)
        self.server_state = QLabel()
        self.client = QComboBox()
        self.where = QLabel()
        self.where.setWordWrap(True)
        self.snippet = QPlainTextEdit()
        self.snippet.setReadOnly(True)
        self.snippet.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        copy = QPushButton(icon("copy"), tr("Copy"))
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(self.snippet.toPlainText()),
                                      copy.setText(tr("Copied"))))
        ml.addWidget(self.enabled)
        ml.addWidget(self.confirm)
        ml.addLayout(mf)
        ml.addWidget(self.server_state)
        ml.addWidget(QLabel(tr("Connect a client")))
        ml.addWidget(self.client)
        ml.addWidget(self.where)
        ml.addWidget(self.snippet, 1)
        ml.addWidget(copy, 0, Qt.AlignmentFlag.AlignRight)
        self.port.valueChanged.connect(self.fill_snippet)
        self.client.currentIndexChanged.connect(self.fill_snippet)
        self.enabled.toggled.connect(self.fill_snippet)
        for name in _client_snippets("", ""):
            self.client.addItem(name)
        self.fill_snippet()

        tabs = QTabWidget()
        tabs.addTab(a, icon("ai"), tr("Assistant"))
        tabs.addTab(m, icon("plug"), tr("Connect agents (MCP)"))
        tabs.setCurrentIndex(tab)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tabs, 1)
        lay.addWidget(buttons)

    # ------------------------------------------------------------ provedor

    def _show_provider(self, name: str) -> None:
        cls = providers.PROVIDERS[name]
        s = self.ctx.settings
        self.key.setText(self._keys.get(name, ""))
        saved = bool(keys.get(name))
        self.key.setPlaceholderText(
            (tr("from the environment variable") if keys.from_env(name) else tr("saved (type to replace)"))
            if saved else (tr("optional") if not cls.needs_key else tr("paste your API key")))
        self.key_link.setText(f"<a href='{KEY_PAGES[name]}' style='color:{self.ctx.theme.ui['accent']}'>"
                              f"{tr('Get a key')}</a>")
        self.model.clear()
        model = self._models.get(name, s.get(f"ai_model_{name}") or cls.default_model)
        if model:
            self.model.addItem(model)
        self.model.setEditText(model)
        self.base.setText(self._bases.get(name, s.get(f"ai_base_{name}") or ""))
        self.base.setPlaceholderText(cls.default_base)
        self.test_label.setText("")
        self.test_label.hide()

    def _remember(self) -> None:
        name = self._current
        if self.key.text().strip():
            self._keys[name] = self.key.text().strip()
        self._models[name] = self.model.currentText().strip()
        self._bases[name] = self.base.text().strip()

    def _provider_changed(self) -> None:
        self._remember()
        self._current = self.provider.currentData()
        self._show_provider(self._current)

    def _provider(self) -> providers.Provider:
        name = self._current
        return providers.make(name, self.key.text().strip() or keys.get(name), self.model.currentText().strip(),
                              self.base.text().strip())

    def load_models(self) -> None:
        """Lista os modelos do provedor (também serve de teste da chave)."""
        self.test_label.setText(tr("Connecting…"))
        self.test_label.show()
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            names = self._provider().list_models()
        except providers.ProviderError as e:
            self.test_label.setText(f"<span style='color:{self.ctx.theme.ui['error']}'>{html.escape(str(e))}</span>")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(names)
        self.model.setEditText(current or (names[0] if names else ""))
        self.test_label.setText(f"<span style='color:{self.ctx.theme.ui['accent']}'>✓ "
                                + html.escape(tr("Connected: {0} model(s) available.").format(len(names))) + "</span>")

    # ------------------------------------------------------------ MCP

    def new_token(self) -> None:
        self.token.setText(mcp.new_token())
        self.fill_snippet()

    def fill_snippet(self) -> None:
        url = f"http://127.0.0.1:{self.port.value()}/mcp"
        snippets = _client_snippets(url, self.token.text())
        where, text = snippets.get(self.client.currentText(), ("", ""))
        self.where.setText(where)
        self.snippet.setPlainText(text)
        srv, u = self.mcp.server, self.ctx.theme.ui
        if not self.enabled.isChecked():
            color, text = u["text_dim"], tr("Server off: turn it on and press OK.")
        elif srv and srv.port == self.port.value():
            color, text = u["accent"], tr("Server running at {0}").format(srv.url)
        elif self.mcp.error:
            color, text = u["error"], tr("Server not running: {0}").format(self.mcp.error)
        else:
            color, text = u["warning"], tr("The server starts when you press OK.")
        self.server_state.setText(f"<span style='color:{color}'>●</span>&nbsp; {html.escape(text)}")

    def accept(self) -> None:
        self._remember()
        s = self.ctx.settings
        try:
            for name, key in self._keys.items():
                keys.set(name, key)
        except OSError as e:
            QMessageBox.warning(self, tr("AI settings"), tr("Could not store the API key: {0}").format(e))
            return
        for name, model in self._models.items():
            s[f"ai_model_{name}"] = model
        for name, base in self._bases.items():
            s[f"ai_base_{name}"] = base
        s["ai_provider"] = self._current
        s["ai_mcp_enabled"] = self.enabled.isChecked()
        s["ai_confirm"] = self.confirm.isChecked()
        s["ai_mcp_port"] = self.port.value()
        s["ai_mcp_token"] = self.token.text()
        s.save()
        super().accept()
