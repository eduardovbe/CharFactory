"""Estados (CNS/ST e a parte [Statedef -1] do CMD) sobre o documento de texto.

Um [Statedef N] é dono dos [State N, rótulo] que vêm depois dele, até o próximo Statedef.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import mtext
from ..i18n import tr

_STATEDEF = re.compile(r"^statedef\s+(-?\d+)", re.I)
_STATE = re.compile(r"^state\s+(-?[^,\]]*?)\s*(?:,\s*(.*))?$", re.I)


@dataclass
class Controller:
    section: mtext.Section
    owner: int | None          # número declarado no cabeçalho [State N, ...]
    label: str

    @property
    def type(self) -> str:
        return (self.section.get("type") or "").strip()

    def triggers(self) -> dict[str, list[str]]:
        """{'triggerall': [...], '1': [...], '2': [...]} na ordem do arquivo."""
        out: dict[str, list[str]] = {}
        for e in self.section.entries():
            k = e.key.lower().replace(" ", "")
            if k == "triggerall":
                out.setdefault("triggerall", []).append(e.value)
            elif k.startswith("trigger") and k[7:].isdigit():
                out.setdefault(k[7:], []).append(e.value)
        return out


@dataclass
class StateDef:
    number: int
    section: mtext.Section
    controllers: list[Controller] = field(default_factory=list)

    def params(self) -> dict[str, str]:
        return self.section.as_dict()

    def text(self) -> str:
        return "\n".join([self.section.text(), *(c.section.text() for c in self.controllers)])

    def info(self) -> dict:
        p = self.params()
        return {"statedef": self.number, "type": p.get("type", "S"), "movetype": p.get("movetype", "I"),
                "physics": p.get("physics", "N"), "anim": p.get("anim"), "ctrl": p.get("ctrl"),
                "controllers": [{"label": c.label, "type": c.type} for c in self.controllers]}


class States:
    """Visão dos statedefs de um documento (pode haver vários arquivos por personagem)."""

    def __init__(self, doc: mtext.Document, path: Path | None = None):
        self.doc = doc
        self.path = path
        self.statedefs: dict[int, StateDef] = {}
        self.orphans: list[Controller] = []  # [State] sem Statedef antes
        self._index()

    def _index(self) -> None:
        self.statedefs.clear()
        self.orphans.clear()
        current: StateDef | None = None
        for sec in self.doc.sections:
            m = _STATEDEF.match(sec.name)
            if m:
                current = StateDef(int(m[1]), sec)
                self.statedefs.setdefault(current.number, current)
                continue
            m = _STATE.match(sec.name)
            if m and sec.kind == "state":
                try:
                    owner = int(m[1])
                except ValueError:
                    owner = None
                c = Controller(sec, owner, (m[2] or "").strip())
                (current.controllers if current else self.orphans).append(c)
            elif sec.kind not in ("state", "statedef"):
                current = None  # outra seção ([Data], [Command]…) encerra o statedef

    # ------------------------------------------------------------ edição

    def set_statedef_text(self, number: int, text: str) -> StateDef:
        """Substitui (ou cria) um statedef inteiro a partir de texto CNS."""
        new = mtext.parse(text)
        if not new.sections or not _STATEDEF.match(new.sections[0].name):
            raise ValueError(tr("the text must start with [Statedef N]"))
        if int(_STATEDEF.match(new.sections[0].name)[1]) != number:
            raise ValueError(tr("the [Statedef] number in the text does not match"))
        for s in new.sections:
            if s.lines and s.lines[-1].strip():
                s.lines.append("")
        old = self.statedefs.get(number)
        if old:
            # por identidade: controladores iguais em outros estados (ex.: o mesmo "fim")
            # são iguais por valor, e list.remove/index apagaria o do estado errado
            ids = {id(old.section), *(id(c.section) for c in old.controllers)}
            at = next(i for i, x in enumerate(self.doc.sections) if x is old.section)
            self.doc.sections[:] = [x for x in self.doc.sections if id(x) not in ids]
        else:
            at = len(self.doc.sections)
            if self.doc.sections and self.doc.sections[-1].lines[-1].strip():
                self.doc.sections[-1].lines.append("")
        self.doc.sections[at:at] = new.sections
        self._index()
        return self.statedefs[number]

    def remove_statedef(self, number: int) -> bool:
        sd = self.statedefs.get(number)
        if not sd:
            return False
        ids = {id(sd.section), *(id(c.section) for c in sd.controllers)}
        self.doc.sections[:] = [x for x in self.doc.sections if id(x) not in ids]
        self._index()
        return True

    def add_controller(self, number: int, text: str) -> Controller:
        """Acrescenta um [State] (texto CNS) ao fim de um statedef existente."""
        sd = self.statedefs[number]
        new = mtext.parse(text)
        if not new.sections or new.sections[0].kind != "state":
            raise ValueError(tr("the text must start with [State N, label]"))
        last = sd.controllers[-1].section if sd.controllers else sd.section
        at = next(i for i, x in enumerate(self.doc.sections) if x is last) + 1   # identidade
        if last.lines and last.lines[-1].strip():
            last.lines.append("")
        for s in new.sections:
            if s.lines and s.lines[-1].strip():
                s.lines.append("")
        self.doc.sections[at:at] = new.sections
        self._index()
        return self.statedefs[number].controllers[-1]

    def reorder_controllers(self, number: int, order: list[int]) -> None:
        """Reordena os [State] de um statedef: `order` lista os índices atuais na nova ordem
        (no [Statedef -1], a ordem é a prioridade dos comandos)."""
        sd = self.statedefs[number]
        if sorted(order) != list(range(len(sd.controllers))):
            raise ValueError(tr("the new order must have each controller once"))
        secs = [c.section for c in sd.controllers]
        for s in secs:
            if s.lines and s.lines[-1].strip():
                s.lines.append("")
        pos = [next(i for i, x in enumerate(self.doc.sections) if x is s) for s in secs]
        for p, k in zip(sorted(pos), order):
            self.doc.sections[p] = secs[k]
        self._index()

    def save(self, path: str | Path | None = None) -> None:
        self.doc.save(path or self.path)


def load(path: str | Path) -> States:
    return States(mtext.load(path), Path(path))


def loads(text: str) -> States:
    return States(mtext.parse(text))
