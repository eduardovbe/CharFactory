"""Comandos (CMD): [Remap], [Defaults], [Command]… e o [Statedef -1] (via cns.States)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import cns, mtext
from ..i18n import tr

# Símbolos válidos numa sequência de comando
DIRECTIONS = {"B", "DB", "D", "DF", "F", "UF", "U", "UB"}
BUTTONS = {"a", "b", "c", "x", "y", "z", "s"}
_TOKEN = re.compile(r"^(~\d*)?([$/>]*)?(.+)$")


@dataclass
class Command:
    section: mtext.Section

    @property
    def name(self) -> str:
        return (self.section.get("name") or "").strip().strip('"')

    @property
    def sequence(self) -> str:
        return (self.section.get("command") or "").strip()

    def info(self) -> dict:
        return {"name": self.name, "command": self.sequence,
                "time": self.section.get("time"), "buffer.time": self.section.get("buffer.time")}


def check_sequence(seq: str) -> list[str]:
    """Erros de sintaxe numa sequência de comando (ex.: '~D, DF, F, x')."""
    errors = []
    if not seq:
        return [tr("empty command")]
    for raw in seq.split(","):
        step = raw.strip()
        if not step:
            errors.append(tr("empty element between commas"))
            continue
        for part in step.split("+"):
            part = part.strip()
            m = _TOKEN.match(part)
            sym = (m[3] if m else part).strip()
            if sym not in DIRECTIONS and sym not in BUTTONS:
                errors.append(tr("unknown symbol '{0}'").format(part))
    return errors


class Commands:
    def __init__(self, doc: mtext.Document, path: Path | None = None):
        self.doc = doc
        self.path = path
        self.states = cns.States(doc, path)

    def commands(self) -> list[Command]:
        return [Command(s) for s in self.doc.of_kind("command")]

    def find(self, name: str) -> list[Command]:
        return [c for c in self.commands() if c.name.lower() == name.lower()]

    def add_command(self, name: str, sequence: str, time: int | None = None,
                    buffer_time: int | None = None) -> Command:
        errs = check_sequence(sequence)
        if errs:
            raise ValueError("; ".join(errs))
        body = [f'name = "{name}"', f"command = {sequence}"]
        if time is not None:
            body.append(f"time = {time}")
        if buffer_time is not None:
            body.append(f"buffer.time = {buffer_time}")
        cmds = self.doc.of_kind("command")
        # novos comandos entram depois do último [Command], antes do [Statedef -1]
        at = (next(i for i, x in enumerate(self.doc.sections) if x is cmds[-1]) + 1) if cmds else 0
        if cmds and cmds[-1].lines[-1].strip():
            cmds[-1].lines.append("")
        sec = self.doc.append_section("Command", body, index=at)
        self.states._index()
        return Command(sec)

    def save(self, path: str | Path | None = None) -> None:
        self.doc.save(path or self.path)


def load(path: str | Path) -> Commands:
    return Commands(mtext.load(path), Path(path))
