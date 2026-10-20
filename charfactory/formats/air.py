"""AIR: animações do MUGEN.

    [Begin Action 200]
    Clsn2Default: 1
     Clsn2[0] = -10, 0, 10, -80
    200,0, 0,0, 3            ; grupo,nº, x,y, tempo[, flip[, trans[, xscale, yscale, angle]]]
    Loopstart
    Clsn1: 1
     Clsn1[0] = 15,-70, 40,-60
    200,1, 0,0, 4, H, A
    Interpolate Offset       ; MUGEN 1.1: interpola até o próximo frame

As ações não modificadas são regravadas com o texto original (comentários preservados).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import mtext
from ..i18n import tr

Box = tuple[int, int, int, int]

_ACTION = re.compile(r"^\s*begin\s+action\s+(-?\d+)", re.I)
_CLSN_HDR = re.compile(r"^\s*clsn([12])(default)?\s*:\s*(\d+)", re.I)
_CLSN_BOX = re.compile(r"^\s*clsn([12])\s*\[\s*\d+\s*\]\s*=\s*(.+)$", re.I)
_INTERP = re.compile(r"^\s*interpolate\s+(offset|blend|scale|angle)\s*$", re.I)


@dataclass
class Frame:
    group: int
    item: int
    x: int = 0
    y: int = 0
    time: int = 1
    flip: str = ""            # "", "H", "V", "HV"
    trans: str = ""           # "", "A", "A1", "S", "AS<n>D<m>", "Add", "Sub"…
    extra: list[str] = field(default_factory=list)  # 1.1: xscale, yscale, angle
    clsn1: list[Box] = field(default_factory=list)  # caixas de ataque efetivas
    clsn2: list[Box] = field(default_factory=list)  # caixas de colisão efetivas
    interpolate: list[str] = field(default_factory=list)  # tipos que terminam neste frame

    def line(self) -> str:
        parts = [f"{self.group},{self.item}", f" {self.x},{self.y}", f" {self.time}"]
        tail = [self.flip, self.trans, *self.extra]
        while tail and not tail[-1]:
            tail.pop()
        parts += [f" {t}" for t in tail]
        return ",".join(parts)

    def info(self) -> dict:
        return {"sprite": [self.group, self.item], "offset": [self.x, self.y],
                "time": self.time, "flip": self.flip, "trans": self.trans,
                "extra": self.extra, "clsn1": [list(b) for b in self.clsn1],
                "clsn2": [list(b) for b in self.clsn2], "interpolate": self.interpolate}


@dataclass
class Action:
    number: int
    frames: list[Frame] = field(default_factory=list)
    loopstart: int = 0
    clsn1_default: list[Box] = field(default_factory=list)
    clsn2_default: list[Box] = field(default_factory=list)
    _raw: list[str] | None = field(default=None, repr=False)
    _snapshot: list[str] | None = field(default=None, repr=False)
    _comments: list[str] | None = field(default=None, repr=False)  # texto original, para os comentários

    @property
    def total_time(self) -> int:
        """Duração em ticks; -1 se algum frame tem tempo infinito."""
        if any(f.time == -1 for f in self.frames):
            return -1
        return sum(f.time for f in self.frames)

    def sprites(self) -> list[tuple[int, int]]:
        return [(f.group, f.item) for f in self.frames]

    def to_lines(self, raw: list[str] | None = None) -> list[str]:
        """Texto canônico da ação (sem o cabeçalho). Com `raw` (o texto original), os
        comentários voltam no mesmo lugar: antes do frame em que estavam, no fim da linha do
        frame e no bloco final (que costuma ser o nome da próxima ação)."""
        out: list[str] = []
        before, inline, tail, indent = _comment_layout(raw) if raw else ({}, {}, [], " ")

        def boxes(n: int, items: list[Box], default: bool) -> None:
            out.append(f"Clsn{n}{'Default' if default else ''}: {len(items)}")
            out.extend(f"{indent}Clsn{n}[{i}] = {b[0]}, {b[1]}, {b[2]}, {b[3]}" for i, b in enumerate(items))

        out.extend(before.get(-1, []))
        if self.clsn2_default:
            boxes(2, self.clsn2_default, True)
        if self.clsn1_default:
            boxes(1, self.clsn1_default, True)
        for i, f in enumerate(self.frames):
            out.extend(before.get(i, []))
            if i == self.loopstart and i > 0:
                out.append("Loopstart")
            for kind in f.interpolate:
                out.append(f"Interpolate {kind.capitalize()}")
            if f.clsn2 != self.clsn2_default:
                boxes(2, f.clsn2, False)
            if f.clsn1 != self.clsn1_default:
                boxes(1, f.clsn1, False)
            out.append(f.line() + (f" {inline[i]}" if i in inline else ""))
        for k in sorted(k for k in before if k >= len(self.frames)):
            out.extend(before[k])
        out.extend(tail)
        return out

    def info(self, frames: bool = True) -> dict:
        d = {"action": self.number, "frame_count": len(self.frames),
             "total_time": self.total_time, "loopstart": self.loopstart}
        if frames:
            d["frames"] = [f.info() for f in self.frames]
        return d


def _ints(text: str) -> list[int]:
    out = []
    for t in text.split(","):
        t = t.strip()
        try:
            out.append(int(float(t)))
        except ValueError:
            out.append(0)
    return out


def parse_action(number: int, body: list[str]) -> Action:
    act = Action(number)
    default = {1: [], 2: []}
    pending: dict[int, list[Box] | None] = {1: None, 2: None}
    reading: tuple[int, bool, int] | None = None  # (tipo, é_default, restantes)
    buf: list[Box] = []
    interp: list[str] = []
    for raw in body:
        line = mtext.strip_comment(raw).strip()
        if not line:
            continue
        m = _CLSN_HDR.match(line)
        if m:
            n, is_def, count = int(m[1]), bool(m[2]), int(m[3])
            buf = []
            reading = (n, is_def, count)
            if count == 0:
                (default.__setitem__(n, []) if is_def else pending.__setitem__(n, []))
                reading = None
            continue
        m = _CLSN_BOX.match(line)
        if m and reading:
            v = (_ints(m[2]) + [0, 0, 0, 0])[:4]
            buf.append((v[0], v[1], v[2], v[3]))
            n, is_def, count = reading
            if len(buf) >= count:
                if is_def:
                    default[n] = buf
                else:
                    pending[n] = buf
                reading = None
            continue
        if line.lower().startswith("loopstart"):
            act.loopstart = len(act.frames)
            continue
        m = _INTERP.match(line)
        if m:
            interp.append(m[1].lower())
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and re.match(r"^-?\d+$", parts[0]) and re.match(r"^-?\d+$", parts[1]):
            nums = _ints(",".join(parts[:5]))
            f = Frame(nums[0], nums[1], nums[2], nums[3], nums[4])
            rest = parts[5:]
            if rest:
                f.flip = rest[0].upper()
            if len(rest) > 1:
                f.trans = rest[1]
            if len(rest) > 2:
                f.extra = rest[2:]
                while f.extra and not f.extra[-1]:      # "…, A," (vírgula sobrando) = sem escala
                    f.extra.pop()
            f.clsn1 = pending[1] if pending[1] is not None else list(default[1])
            f.clsn2 = pending[2] if pending[2] is not None else list(default[2])
            f.interpolate, interp = interp, []
            pending = {1: None, 2: None}
            act.frames.append(f)
    # o default "da ação" é o que vale no 1º frame, para regravar de forma compacta
    if act.frames:
        act.clsn1_default = list(default[1]) if _defaults_hold(act, 1, default[1]) else []
        act.clsn2_default = list(default[2]) if _defaults_hold(act, 2, default[2]) else []
    return act


def _is_frame_line(line: str) -> bool:
    parts = [p.strip() for p in line.split(",")]
    return len(parts) >= 5 and bool(re.match(r"^-?\d+$", parts[0])) and bool(re.match(r"^-?\d+$", parts[1]))


def _comment_layout(raw: list[str]):
    """Onde ficam os comentários do texto original de uma ação.

    Devolve (antes, na_linha, final, recuo): `antes[k]` são as linhas de comentário que vêm
    antes do frame k (-1 = antes das caixas default), `na_linha[k]` o comentário no fim da
    linha do frame k, `final` o bloco de comentários/linhas vazias depois do último conteúdo
    e `recuo` o recuo usado nas linhas Clsn[n]."""
    last = max((i for i, l in enumerate(raw) if mtext.strip_comment(l).strip()), default=-1)
    tail = list(raw[last + 1:])
    before: dict[int, list[str]] = {}
    inline: dict[int, str] = {}
    indent = " "
    frame = 0
    seen_default = False
    pending: list[str] = []
    for line in raw[:last + 1]:
        code = mtext.strip_comment(line).strip()
        if not code:
            if line.strip():
                pending.append(line)
            continue
        if _CLSN_BOX.match(code):
            indent = line[:len(line) - len(line.lstrip())] or indent
        if _CLSN_HDR.match(code) and _CLSN_HDR.match(code)[2] and frame == 0 and not seen_default:
            seen_default = True
            if pending:
                before.setdefault(-1, []).extend(pending)
                pending = []
            continue
        if pending:
            before.setdefault(frame, []).extend(pending)
            pending = []
        if _is_frame_line(code):
            rest = line.strip()[len(code):].strip()
            if rest.startswith(";"):
                inline[frame] = rest
            frame += 1
    if pending:
        before.setdefault(frame, []).extend(pending)
    return before, inline, tail, indent


def _defaults_hold(act: Action, n: int, boxes: list[Box]) -> bool:
    return bool(boxes) and any((f.clsn1 if n == 1 else f.clsn2) == boxes for f in act.frames)


class AIR:
    def __init__(self, doc: mtext.Document | None = None):
        self.doc = doc or mtext.Document()
        self.actions: dict[int, Action] = {}
        self._sections: dict[int, mtext.Section] = {}
        for sec in self.doc.sections:
            m = _ACTION.match(sec.name)
            if m:
                n = int(m[1])
                if n in self.actions:
                    # número repetido: o MUGEN usa a primeira (zeno.air tem a 1411 com o rastro
                    # de garra e depois uma 1411 vazia; ficar com a última escondia o efeito)
                    continue
                act = parse_action(n, sec.lines[1:])
                act._raw = act._comments = sec.lines[1:]
                act._snapshot = act.to_lines()
                self.actions[n] = act
                self._sections[n] = sec

    # ---------------------------------------------------------------- API

    def get(self, number: int) -> Action | None:
        return self.actions.get(number)

    def numbers(self) -> list[int]:
        return sorted(self.actions)

    def set_action(self, action: Action) -> None:
        """Insere ou substitui uma ação (a seção é regenerada ao salvar)."""
        old = self.actions.get(action.number)
        action._raw = action._snapshot = None
        # substituir uma ação mantém os comentários dela; uma ação nova não herda os de outra
        action._comments = old._comments if old is not None else None
        self.actions[action.number] = action
        if action.number not in self._sections:
            sec = self.doc.append_section(f"Begin Action {action.number}")
            self._sections[action.number] = sec

    def _sync_section(self, sec) -> None:
        """Depois de mexer nas linhas da seção, a ação dela passa a usar esse texto como original."""
        for n, s in self._sections.items():
            if s is sec:
                act = self.actions[n]
                act._comments = sec.lines[1:]
                if act._raw is not None:
                    act._raw = sec.lines[1:]
                    act._snapshot = act.to_lines()

    def name_of(self, number: int) -> str:
        """Nome da ação: o comentário logo acima de [Begin Action N]."""
        sec = self._sections.get(number)
        if sec is None:
            return ""
        k = next(i for i, s in enumerate(self.doc.sections) if s is sec)
        if k:   # o comentário mora no fim da seção anterior: só ela precisa estar em dia
            above = self.doc.sections[k - 1]
            owner = next((n for n, s in self._sections.items() if s is above), None)
            if owner is not None:
                self._sync_lines(owner)
        prev = self.doc.sections[k - 1].lines[1:] if k else self.doc.preamble
        for line in reversed(prev):
            t = line.strip()
            if t.startswith(";"):
                return t.lstrip(";-= ").strip()
            if t:
                break
        return ""

    def set_name(self, number: int, name: str) -> None:
        """Grava o nome como comentário logo acima do cabeçalho da ação (vazio = remove)."""
        sec = self._sections[number]
        self.to_text()                                  # seções em dia com as ações
        k = next(i for i, s in enumerate(self.doc.sections) if s is sec)
        prev = self.doc.sections[k - 1].lines if k else self.doc.preamble
        first = 1 if k else 0                           # a linha 0 de uma seção é o cabeçalho
        j = len(prev) - 1
        while j >= first and not prev[j].strip():
            j -= 1
        line = f"; {name.strip()}"
        if j >= first and prev[j].strip().startswith(";"):
            if name.strip():
                prev[j] = line
            else:
                del prev[j]
        elif name.strip():
            prev.insert(j + 1, line)
            if j >= first:
                prev.insert(j + 1, "")
        if k:
            self._sync_section(self.doc.sections[k - 1])

    def renumber(self, old: int, new: int) -> None:
        """Muda o número da ação no próprio cabeçalho (posição e comentários ficam)."""
        if new in self.actions:
            raise ValueError(tr("action {0} already exists").format(new))
        sec = self._sections.pop(old)
        act = self.actions.pop(old)
        sec.lines[0] = re.sub(r"(?i)(begin\s+action\s+)-?\d+", lambda m: f"{m[1]}{new}", sec.lines[0], count=1)
        sec.name = re.sub(r"(?i)(begin\s+action\s+)-?\d+", lambda m: f"{m[1]}{new}", sec.name, count=1)
        act.number = new
        self.actions[new] = act
        self._sections[new] = sec

    def remove_action(self, number: int) -> bool:
        if number not in self.actions:
            return False
        self.doc.remove_section(self._sections.pop(number))
        del self.actions[number]
        return True

    def _sync_lines(self, n: int) -> None:
        """Linhas da seção da ação n refeitas a partir da ação (texto original se não mudou)."""
        act, sec = self.actions[n], self._sections[n]
        if act._raw is not None and act.to_lines() == act._snapshot:
            sec.lines = [sec.lines[0], *act._raw]
        else:
            body = act.to_lines(act._comments)
            sec.lines = [sec.lines[0], *body, *([""] if not act._comments else [])]

    def to_text(self) -> str:
        for n in self.actions:
            self._sync_lines(n)
        return self.doc.to_text()

    def save(self, path: str | Path) -> None:
        self.to_text()
        self.doc.save(path)


def load(path: str | Path) -> AIR:
    return AIR(mtext.load(path))


def loads(text: str) -> AIR:
    return AIR(mtext.parse(text))
