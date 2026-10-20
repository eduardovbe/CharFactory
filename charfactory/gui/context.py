"""Estado compartilhado da interface: projetos abertos, desfazer/refazer, tema e configurações."""
from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import project
from ..formats import sff as sffmod
from . import theme as themes
from ..i18n import tr


def config_path() -> Path:
    p = Path(os.environ.get("APPDATA", Path.home())) / "charfactory" / "config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class Settings(dict):
    DEFAULTS = {"theme": "Default", "mugen_dir": "", "recent": [], "geometry": None,
                "onion": True, "zoom": 2, "autosave_min": 5, "file_watcher": True,
                "undo_limit": 100, "snippets": {}}

    @classmethod
    def load(cls) -> "Settings":
        s = cls(copy.deepcopy(cls.DEFAULTS))
        try:
            s.update(json.loads(config_path().read_text("utf-8")))
        except (OSError, ValueError):
            pass
        s["theme"] = themes.migrate_name(s.get("theme") or themes.DEFAULT_THEME)
        return s

    def save(self) -> None:
        config_path().write_text(json.dumps(self, indent=1, ensure_ascii=False), "utf-8")

    def add_recent(self, path: str) -> None:
        r = [p for p in self.get("recent", []) if p.lower() != path.lower()]
        self["recent"] = [path, *r][:10]


# ======================================================================= desfazer/refazer

# parte do modelo -> atributo do Character que a guarda (carregado sob demanda)
PARTS = {"sprites": "sprites", "anims": "anims", "sounds": "sounds",
         "states": "states", "commands": "commands"}


def _snapshot(part: str, obj):
    """Cópia do estado de uma parte. O SFF é copiado raso por sprite: os bytes codificados
    são imutáveis e compartilhados, o que deixa a cópia de 2.500 sprites barata."""
    if part == "sprites":
        new = copy.copy(obj)
        new.sprites = [copy.copy(s) for s in obj.sprites]
        new.palettes = [sffmod.Palette(p.group, p.item, list(p.colors)) for p in obj.palettes]
        return new
    return copy.deepcopy(obj)


class UndoStack:
    """Histórico por projeto. Cada entrada guarda o estado de UMA parte antes da mudança.

    `record(part)` é chamado depois de cada mudança: empilha o estado anterior (guardado em
    `_last`) e passa a lembrar o atual. Mudanças seguidas da mesma parte em menos de 0,7 s
    (girar um spinbox, arrastar uma caixa) viram um passo só."""

    MERGE_SECONDS = 0.7

    def __init__(self, char: project.Character, limit: int = 100):
        self.char = char
        self.limit = limit
        self.undo: list[tuple[str, object, str]] = []
        self.redo: list[tuple[str, object, str]] = []
        self._last: dict[str, object] = {}
        self._when: dict[str, float] = {}

    def _get(self, part: str):
        try:
            return getattr(self.char, PARTS[part])
        except project.ProjectError:
            return None

    def prime(self, part: str) -> None:
        """Lembra o estado atual de uma parte (antes da primeira mudança)."""
        if part in PARTS and part not in self._last:
            obj = self._get(part)
            if obj is not None:
                self._last[part] = _snapshot(part, obj)

    def record(self, part: str, label: str = "") -> None:
        if part not in PARTS:
            return
        obj = self._get(part)
        if obj is None:
            return
        before = self._last.get(part)
        now = time.monotonic()
        merge = (self.undo and self.undo[-1][0] == part and now - self._when.get(part, 0) < self.MERGE_SECONDS)
        if before is not None and not merge:
            self.undo.append((part, before, label))
            del self.undo[:-self.limit]
        self.redo.clear()
        self._when[part] = now
        self._last[part] = _snapshot(part, obj)

    def _swap(self, src: list, dst: list) -> str | None:
        if not src:
            return None
        part, state, label = src.pop()
        cur = self._get(part)
        if cur is not None:
            dst.append((part, _snapshot(part, cur), label))
        restored = _snapshot(part, state)
        self.char.__dict__[PARTS[part]] = restored
        if part == "states":
            self.char.__dict__.pop("constants", None)   # o .cns é um dos arquivos de estados
        self._last[part] = _snapshot(part, restored)
        self._when[part] = 0
        self.char.mark(part)
        return part

    def do_undo(self) -> str | None:
        return self._swap(self.undo, self.redo)

    def do_redo(self) -> str | None:
        return self._swap(self.redo, self.undo)

    def forget(self, *parts: str) -> None:
        """Esquece o histórico de partes recarregadas do disco."""
        self.undo = [e for e in self.undo if e[0] not in parts]
        self.redo = [e for e in self.redo if e[0] not in parts]
        for p in parts:
            self._last.pop(p, None)
            self.prime(p)


# ======================================================================= contexto

class AppContext(QObject):
    """Sinais globais: os editores escutam e se atualizam."""
    characterChanged = Signal()       # outro personagem ativo (aberto ou trocado de aba)
    projectsChanged = Signal()        # lista de projetos abertos mudou
    modelChanged = Signal(str)        # parte alterada: sprites, anims, states, commands, sounds, def
    themeChanged = Signal()
    settingsChanged = Signal()
    status = Signal(str)
    request_code = Signal(str, str)     # arquivo, local ("[Begin Action 200]")
    request_tool = Signal(str)          # "palfx", "throw", "var_list"…
    request_validate = Signal()
    request_definition = Signal(str, int, int)  # 'anim'|'state'|'sound', a, b (ir para a definição)
    cursor_info = Signal(int, int, int, str)  # linha, coluna, total de linhas, encoding

    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.themes = themes.load_all()
        self.theme = self.themes.get(self.settings["theme"]) or next(iter(self.themes.values()))
        self.char: project.Character | None = None
        self.projects: list[project.Character] = []
        self.history: dict[int, UndoStack] = {}
        self.last_issues: list = []

    # ------------------------------------------------------------ personagem

    def open(self, def_path: str) -> project.Character:
        """Abre (ou ativa, se já estiver aberto) um personagem."""
        resolved = Path(def_path).resolve()
        for c in self.projects:
            if c.def_path == resolved:
                self.activate(c)
                return c
        mugen = self.settings.get("mugen_dir") or None
        c = project.load(def_path, mugen)
        self.projects.append(c)
        self.history[id(c)] = UndoStack(c, int(self.settings.get("undo_limit", 100)))
        self.settings.add_recent(str(c.def_path))
        self.settings.save()
        self.char = c
        self.projectsChanged.emit()
        self.characterChanged.emit()
        self.prime_all()
        return c

    def reopen(self) -> project.Character | None:
        """Recarrega do disco o personagem ativo (mesma posição na lista de projetos)."""
        c = self.char
        if not c:
            return None
        i = self.projects.index(c)
        new = project.load(c.def_path, self.settings.get("mugen_dir") or None)
        self.projects[i] = new
        self.history.pop(id(c), None)
        self.history[id(new)] = UndoStack(new, int(self.settings.get("undo_limit", 100)))
        self.char = new
        self.projectsChanged.emit()
        self.characterChanged.emit()
        self.prime_all()
        return new

    def activate(self, c: project.Character) -> None:
        if c is not self.char and c in self.projects:
            self.char = c
            self.characterChanged.emit()

    def close(self, c: project.Character) -> None:
        if c in self.projects:
            self.projects.remove(c)
            self.history.pop(id(c), None)
        if self.char is c:
            self.char = self.projects[-1] if self.projects else None
        self.projectsChanged.emit()
        if self.char:
            self.characterChanged.emit()

    def prime_all(self) -> None:
        h = self.undo_stack()
        if h:
            for part in PARTS:
                h.prime(part)

    def undo_stack(self) -> UndoStack | None:
        return self.history.get(id(self.char)) if self.char else None

    def changed(self, part: str) -> None:
        """Uma parte do modelo mudou: marca para gravar, guarda no histórico e avisa os editores."""
        if self.char:
            self.char.mark(part)
            h = self.undo_stack()
            if h:
                h.record(part)
        self.modelChanged.emit(part)

    def record(self, part: str) -> None:
        """Como changed(), mas sem pedir que os editores recarreguem (edição feita no lugar)."""
        if self.char:
            self.char.mark(part)
            h = self.undo_stack()
            if h:
                h.record(part)

    def undo(self) -> bool:
        return self._history("do_undo")

    def redo(self) -> bool:
        return self._history("do_redo")

    def _history(self, op: str) -> bool:
        h = self.undo_stack()
        part = getattr(h, op)() if h else None
        if part:
            self.modelChanged.emit(part)
            self.status.emit((tr("Undone") if op == "do_undo" else tr("Redone")) + f": {part}")
        return part is not None

    def reset_cache(self, *names: str) -> None:
        """Descarta propriedades carregadas sob demanda do personagem (ex.: após editar texto)."""
        for n in names:
            if self.char is not None:
                self.char.__dict__.pop(n, None)
        h = self.undo_stack()
        if h:
            h.forget(*[p for p, attr in PARTS.items() if attr in names])

    # ------------------------------------------------------------ tema

    def set_theme(self, name: str) -> None:
        self.themes = themes.load_all()
        if name in self.themes:
            self.theme = self.themes[name]
            self.settings["theme"] = name
            self.settings.save()
            self.themeChanged.emit()

    def apply_theme_object(self, t: "themes.Theme") -> None:
        """Aplica um tema sem salvá-lo (prévia do editor de temas)."""
        self.theme = t
        self.themeChanged.emit()
