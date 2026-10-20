"""Testador de comandos: dada uma sequência de entradas, quais [Command] do .cmd ela aciona.

Entrada em notação de numpad, um passo por espaço (o lado é sempre "para a frente"):
    2 3 6 a          ↓ ↘ → + A   (meia-lua para frente + A)
    6 2 3 x+y        Z para frente + X e Y juntos
    4*40 6 a         segura para trás 40 ticks, frente, A (carga)
    a*30 ~a          segura A 30 ticks e solta
Cada passo dura 1 tick (ou `*n`); `5` é neutro. Também aceita os nomes do MUGEN
(D, DF, F, a…) no lugar dos números.

A comparação segue as regras da documentação da Elecbyte (cmd.html): `/` segura, `~` solta
(com `~30` exigindo 30 ticks segurando antes), `$` aceita a direção contida em diagonais,
`>` não permite outra entrada entre os dois passos e `+` aperta junto. O resultado é uma
aproximação do buffer do MUGEN, útil para achar comando que conflita ou que não sai.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from ..i18n import tr

NUMPAD = {"1": "DB", "2": "D", "3": "DF", "4": "B", "5": "", "6": "F", "7": "UB", "8": "U", "9": "UF"}
DIRS = {"B", "DB", "D", "DF", "F", "UF", "U", "UB"}
BUTTONS = {"a", "b", "c", "x", "y", "z", "s"}


def _dir_parts(d: str) -> set[str]:
    return {c for c in d} if d else set()     # "DF" -> {"D", "F"}


@dataclass
class Tick:
    direction: str            # "", "F", "DF"...
    buttons: frozenset


def parse_input(text: str) -> list[Tick]:
    """Lista de ticks a partir da notação (ver o módulo)."""
    ticks: list[Tick] = []
    held_buttons: set[str] = set()
    for raw in text.replace(",", " ").split():
        m = re.match(r"^(.*?)(?:\*(\d+))?$", raw)
        body, n = m[1], int(m[2] or 1)
        direction = ""
        buttons: set[str] = set()
        release: set[str] = set()
        for part in body.split("+"):
            p = part.strip()
            if not p:
                continue
            if p.startswith("~"):
                release.add(p[1:].lower())
                continue
            if p in NUMPAD:
                direction = NUMPAD[p]
            elif p.upper() in DIRS:
                direction = p.upper()
            elif p.lower() in BUTTONS:
                buttons.add(p.lower())
            else:
                raise ValueError(tr("unknown input '{0}'").format(p))
        if release:
            held_buttons -= release
            # a direção/botões continuam como estavam; o tick só registra a soltura
            last_dir = ticks[-1].direction if ticks else ""
            ticks.extend(Tick(direction or last_dir, frozenset(held_buttons)) for _ in range(n))
            continue
        held = set(buttons) if n > 1 else set()
        ticks.extend(Tick(direction, frozenset(buttons | (held if k else set()))) for k in range(n))
        held_buttons = held
    return ticks


@dataclass
class Step:
    elems: list[tuple[str, str, int]]   # (modo, símbolo, ticks de carga) — modo: press/hold/release/contains
    strict: bool                         # '>' antes do passo


def parse_command(seq: str) -> list[Step]:
    steps = []
    for raw in seq.split(","):
        s = raw.strip()
        strict = s.startswith(">")
        s = s.lstrip(">").strip()
        elems = []
        for part in s.split("+"):
            p = part.strip()
            mode, charge = "press", 0
            m = re.match(r"^~(\d*)(.*)$", p)
            if m:
                mode, charge, p = "release", int(m[1] or 0), m[2].strip()
            if p.startswith("/"):
                mode, p = ("hold" if mode == "press" else mode), p[1:].strip()
            contains = p.startswith("$")
            p = p.lstrip("$").strip()
            if contains and mode == "press":
                mode = "contains"
            elif contains and mode == "hold":
                mode = "hold_contains"
            elif contains and mode == "release":
                mode = "release_contains"
            elems.append((mode, p, charge))
        steps.append(Step(elems, strict))
    return steps


def _active(t: Tick, sym: str, contains: bool) -> bool:
    if sym in BUTTONS:
        return sym in t.buttons
    if contains:
        return _dir_parts(sym) <= _dir_parts(t.direction)
    return t.direction == sym


def _elem_at(ticks: list[Tick], i: int, mode: str, sym: str, charge: int) -> bool:
    cur = ticks[i]
    prev = ticks[i - 1] if i > 0 else Tick("", frozenset())
    if mode in ("hold", "hold_contains"):
        return _active(cur, sym, mode == "hold_contains")
    if mode in ("release", "release_contains"):
        c = mode == "release_contains"
        if not (_active(prev, sym, c) and not _active(cur, sym, c)):
            return False
        if charge:
            k, held = i - 1, 0
            while k >= 0 and _active(ticks[k], sym, c):
                held += 1
                k -= 1
            return held >= charge
        return True
    contains = mode == "contains"
    if sym in BUTTONS:                                  # botão: aperto neste tick
        return sym in cur.buttons and sym not in prev.buttons
    return _active(cur, sym, contains) and not _active(prev, sym, contains)


def _step_at(ticks: list[Tick], i: int, step: Step) -> bool:
    return all(_elem_at(ticks, i, m, s, c) for m, s, c in step.elems)


def _changed(ticks: list[Tick], i: int) -> bool:
    if i == 0:
        return bool(ticks[0].direction or ticks[0].buttons)
    return ticks[i].direction != ticks[i - 1].direction or ticks[i].buttons != ticks[i - 1].buttons


def matches(ticks: list[Tick], seq: str, time: int = 15, buffer_time: int = 1) -> int | None:
    """Tick em que o comando completa (o último que cabe no buffer), ou None."""
    steps = parse_command(seq)
    n = len(ticks)
    best = None
    for end in range(n):
        if not _step_at(ticks, end, steps[-1]):
            continue
        # busca para trás os passos anteriores dentro de `time`
        pos = end
        ok = True
        for k in range(len(steps) - 2, -1, -1):
            found = None
            # soltar e apertar a próxima direção acontecem no mesmo tick (D -> DF solta o D)
            same = any(m.startswith("release") for m, _, _ in steps[k].elems)
            for j in range(pos - (0 if same else 1), max(-1, end - time - 1), -1):
                if _step_at(ticks, j, steps[k]):
                    found = j
                    break
                if steps[k + 1].strict and _changed(ticks, j):
                    break
            if found is None:
                ok = False
                break
            pos = found
        if ok:
            best = end
    return best


def test(char, text: str) -> dict:
    """Comandos do personagem que a entrada aciona no último tick (e os que completam antes)."""
    ticks = parse_input(text)
    if not ticks:
        return {"ticks": 0, "active": [], "earlier": []}
    active, earlier = [], []
    for c in char.commands.commands():
        try:
            t = int((c.section.get("time") or "15").split(";")[0])
        except ValueError:
            t = 15
        try:
            bt = int((c.section.get("buffer.time") or "1").split(";")[0])
        except ValueError:
            bt = 1
        try:
            at = matches(ticks, c.sequence, t, bt)
        except (ValueError, IndexError):
            continue
        any_at = None
        try:
            any_at = matches(ticks, c.sequence, t, len(ticks) + 1)
        except (ValueError, IndexError):
            pass
        row = {"name": c.name, "command": c.sequence, "time": t, "tick": at if at is not None else any_at}
        if at is not None and at >= len(ticks) - bt:
            active.append(row)
        elif any_at is not None:
            earlier.append(row)
    # o estado que cada comando ativo leva, pelo [Statedef -1]
    entries: dict[str, list[int]] = {}
    sd = char.commands.states.statedefs.get(-1)
    for ctl in (sd.controllers if sd else []):
        if ctl.type.lower() != "changestate":
            continue
        v = re.match(r"^\s*(\d+)", ctl.section.get("value") or "")
        for ex in ctl.triggers().values():
            for e in ex:
                for name in re.findall(r'command\s*=\s*"([^"]+)"', e, re.I):
                    if v:
                        entries.setdefault(name.lower(), []).append(int(v[1]))
    for row in active + earlier:
        row["states"] = sorted(set(entries.get(row["name"].lower(), [])))
    return {"ticks": len(ticks), "active": active, "earlier": earlier}
