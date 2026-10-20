"""Leitor de ZSS (Ikemen GO): o .zss vira o mesmo modelo de estados do CNS.

O texto ZSS continua sendo a fonte (edição e gravação mexem nele). Por baixo, cada statedef
é rebaixado para um CNS equivalente que a simulação e o validador já entendem:
- if/else/switch viram triggerall; quando o bloco tem mais de um controlador a condição é
  guardada num local antes do corpo, como o Ikemen faz (avaliada uma vez, não a cada
  controlador — senão um varSet no corpo mudaria a condição dos seguintes);
- persistent(n)/ignoreHitPause do bloco vão para cada controlador dele;
- `let x = ...`/`$x`, argumentos e retornos de função viram locais `zsslocal(n)`, que o
  simulador zera a cada tick (o Ikemen também);
- `call f(...)` é expandido com o corpo da função;
- `expr := valor;` vira um Null que faz a atribuição no gatilho;
- laços for/while não são simulados (aviso).
Cada controlador rebaixado leva no rótulo a linha do .zss (`@123`).
"""
from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import mtext
from ..i18n import tr

_ID = re.compile(r"[A-Za-z_][\w.]*")
_LOCAL = re.compile(r'"[^"]*"|\$([A-Za-z_]\w*)')
_NUM = re.compile(r"[+-]?\d+(\.\d+)?")


class ZssError(ValueError):
    def __init__(self, msg: str, line: int):
        super().__init__(tr("line {0}: {1}").format(line, msg))
        self.line = line
        self.msg = msg


# ------------------------------------------------------------------ árvore


@dataclass
class Ctrl:
    name: str
    params: list[tuple[str, str, int]]       # (chave, valor, linha)
    line: int
    label: str = ""


@dataclass
class Block:
    body: list
    ihp: bool = False
    pers: int | None = None
    line: int = 0
    label: str = ""


@dataclass
class If:
    cond: str
    body: list
    orelse: list | None = None
    ihp: bool = False
    pers: int | None = None
    line: int = 0
    label: str = ""


@dataclass
class Loop:
    kind: str
    header: str
    body: list
    line: int = 0
    ihp: bool = False
    pers: int | None = None


@dataclass
class Call:
    name: str
    args: list[str]
    rets: list[str]
    line: int = 0


@dataclass
class Let:
    names: list[str]
    exprs: list[str]
    line: int = 0


@dataclass
class Expr:
    text: str
    line: int = 0


@dataclass
class Jump:
    kind: str
    line: int = 0


@dataclass
class Section:
    kind: str                                   # "statedef" | "function"
    number: int | None
    name: str
    params: list[tuple[str, str]]               # cabeçalho do statedef
    args: list[str]
    rets: list[str]
    body: list
    start: int                                  # deslocamentos no texto: [start, end)
    end: int
    line: int


@dataclass
class ZssFile:
    sections: list[Section] = field(default_factory=list)

    @property
    def statedefs(self) -> list[Section]:
        return [s for s in self.sections if s.kind == "statedef"]

    @property
    def functions(self) -> list[Section]:
        return [s for s in self.sections if s.kind == "function"]


# ------------------------------------------------------------------ texto


def split_top(s: str, sep: str) -> list[str]:
    """Separa por `sep` fora de parênteses, colchetes e aspas."""
    out, depth, cur, quoted = [], 0, [], False
    for ch in s:
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == sep and depth == 0:
                out.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    out.append("".join(cur).strip())
    return out


def _simple(expr: str) -> bool:
    depth, quoted, prev = 0, False, ""
    for ch in expr:
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif depth == 0 and (ch == "," or (ch in "&|^" and prev == ch)):
                return False
        prev = ch
    return True


def _wrap(expr: str) -> str:
    return expr if _simple(expr) else f"({expr})"


class _P:
    def __init__(self, text: str):
        self.t = text
        self.n = len(text)
        self.i = 0
        self.nl = [m.start() for m in re.finditer("\n", text)]
        self.comment: str | None = None
        self.last = 0                           # fim do último token útil (para os trechos)

    def line(self, i: int | None = None) -> int:
        return bisect.bisect_left(self.nl, self.i if i is None else i) + 1

    def err(self, msg: str, i: int | None = None):
        raise ZssError(msg, self.line(i))

    def ws(self) -> None:
        t = self.t
        while self.i < self.n:
            c = t[self.i]
            if c in " \t\r\n﻿":
                self.i += 1
            elif c == "#":
                j = t.find("\n", self.i)
                j = self.n if j < 0 else j
                ls = t.rfind("\n", 0, self.i) + 1
                if not t[ls:self.i].strip():        # comentário sozinho na linha: rótulo do próximo
                    txt = t[self.i + 1:j].strip()
                    self.comment = txt if txt.strip("=-#*~ ") else None
                self.i = j
            else:
                break

    def at(self, s: str) -> bool:
        return self.t.startswith(s, self.i)

    def peek_word(self) -> str:
        m = _ID.match(self.t, self.i)
        return m.group(0) if m else ""

    def take_word(self) -> str:
        w = self.peek_word()
        self.i += len(w)
        return w

    def expect(self, s: str) -> None:
        self.ws()
        if not self.at(s):
            self.err(tr("expected '{0}'").format(s))
        self.i += len(s)
        self.last = self.i

    def until(self, stops: str) -> str:
        """Texto cru até um caractere de `stops` fora de () [] e aspas (sem consumi-lo)."""
        t, depth, out, start = self.t, 0, [], self.i
        while self.i < self.n:
            c = t[self.i]
            if c == '"':
                j = t.find('"', self.i + 1)
                if j < 0:
                    self.err(tr("unclosed quotes"))
                out.append(t[self.i:j + 1])
                self.i = j + 1
                continue
            if c == "#":
                j = t.find("\n", self.i)
                self.i = self.n if j < 0 else j
                out.append(" ")
                continue
            if depth == 0 and c in stops:
                return " ".join("".join(out).split())
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            elif c in "{}":
                self.err(tr("unexpected '{0}'").format(c))
            out.append(c)
            self.i += 1
        self.err(tr("missing '{0}'").format(stops[0]), start)

    # ---------------------------------------------------------- seções

    def file(self) -> ZssFile:
        f = ZssFile()
        self.ws()
        while self.i < self.n:
            if not self.at("["):
                self.err(tr("expected [StateDef ...] or [Function ...]"))
            f.sections.append(self.section())
            self.ws()
        return f

    def section(self) -> Section:
        start, line = self.i, self.line()
        self.i += 1
        t, depth, j = self.t, 0, self.i
        while j < self.n:
            c = t[j]
            if c == '"':
                k = t.find('"', j + 1)
                j = self.n if k < 0 else k
            elif c == "#":
                k = t.find("\n", j)
                j = self.n if k < 0 else k
                continue
            elif c in "([":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == "]":
                if depth == 0:
                    break
                depth -= 1
            j += 1
        else:
            self.err(tr("header without ']'"), start)
        raw = re.sub(r"#[^\n]*", " ", t[self.i:j])
        self.i = self.last = j + 1
        head = " ".join(raw.split())
        m = re.match(r"(?i)statedef\s+([^;]*)(.*)$", head)
        if m:
            num = m[1].strip()
            try:
                number = -10 if num == "+1" else int(num)
            except ValueError:
                number = None                       # const(...) do Ikemen: só o motor resolve
            params = []
            for part in split_top(m[2], ";"):
                if not part:
                    continue
                k, colon, v = part.partition(":")
                if not colon:
                    self.err(tr("parameter without ':' in the header: {0!r}").format(part), start)
                params.append((k.strip().lower(), v.strip()))
            body = self.body(True)
            return Section("statedef", number, num, params, [], [], body, start, self.last, line)
        m = re.match(r"(?i)function\s+(\w+)\s*\(([^)]*)\)\s*(.*)$", head)
        if m:
            args = [a.strip().lstrip("$") for a in m[2].split(",") if a.strip()]
            rets = [r.strip().lstrip("$") for r in m[3].split(",") if r.strip()]
            body = self.body(True)
            return Section("function", None, m[1], [], args, rets, body, start, self.last, line)
        self.err(tr("unknown section: [{0}]").format(head), start)

    # ---------------------------------------------------------- comandos

    def body(self, root: bool, stop: tuple[str, ...] = ()) -> list:
        out = []
        while True:
            self.ws()
            if self.i >= self.n:
                if root:
                    return out
                self.err(tr("missing '}'"))
            if self.at("}"):
                if root:
                    self.err(tr("extra '}'"))
                self.comment = None
                return out
            if root and self.at("["):
                self.comment = None
                return out
            if stop and self.peek_word().lower() in stop:
                return out
            if root and out and self.line() == self.line(self.last - 1):
                self.err(tr("two commands on the same line (outside the state, one per line)"))
            out.append(self.stmt())

    def stmt(self):
        label, self.comment = self.comment, None
        line = self.line()
        ihp, pers = False, None
        while True:
            w = self.peek_word().lower()
            if w == "ignorehitpause":
                self.take_word()
                ihp = True
                self.ws()
                continue
            if w == "persistent":
                save = self.i
                self.take_word()
                self.ws()
                if self.at("("):
                    self.i += 1
                    v = self.until(")")
                    self.expect(")")
                    if not _NUM.fullmatch(v):
                        self.err(tr("persistent needs a number: {0!r}").format(v))
                    pers = int(float(v))
                    self.ws()
                    continue
                self.i = save
            break
        w = self.peek_word()
        wl = w.lower()
        if self.at("{"):
            self.i += 1
            b = self.body(False)
            self.expect("}")
            return Block(b, ihp, pers, line, label)
        if wl == "if":
            return self._if(ihp, pers, line, label)
        if wl == "switch":
            return self._switch(ihp, pers, line, label)
        if wl in ("for", "while"):
            self.take_word()
            header = self.until("{")
            self.expect("{")
            b = self.body(False)
            self.expect("}")
            return Loop(wl, header, b, line, ihp, pers)
        if ihp or pers is not None:
            self.err(tr("ignoreHitPause/persistent only work before a block ({, if, switch)"))
        if wl == "call":
            self.take_word()
            return self._call(line, [])
        if wl == "let":
            self.take_word()
            names = [x.strip().lstrip("$") for x in self.until("=").split(",")]
            self.expect("=")
            self.ws()
            if self.peek_word().lower() == "call":
                self.take_word()
                return self._call(line, names)
            text = self.until(";")
            self.expect(";")
            exprs = split_top(text, ",")
            if len(exprs) != len(names):
                exprs = [text] if len(names) == 1 else exprs
            if len(exprs) != len(names):
                self.err(tr("let with {0} names and {1} values").format(len(names), len(exprs)))
            return Let(names, exprs, line)
        if wl in ("break", "continue"):
            self.take_word()
            self.expect(";")
            return Jump(wl, line)
        if w:
            save = self.i
            self.take_word()
            self.ws()
            if self.at("{"):
                self.i += 1
                return self._sctrl(w, line, label)
            self.i = save
        text = self.until(";")
        self.expect(";")
        if not text:
            self.err(tr("empty command"))
        return Expr(text, line)

    def _if(self, ihp, pers, line, label) -> If:
        self.take_word()
        cond = self.until("{")
        if not cond:
            self.err(tr("if without a condition"))
        self.expect("{")
        b = self.body(False)
        self.expect("}")
        save_i, save_c = self.i, self.comment
        self.ws()
        orelse = None
        if self.peek_word().lower() == "else":
            self.take_word()
            self.ws()
            self.comment = None
            orelse = [self.stmt()]
        else:
            self.i, self.comment = save_i, save_c
        return If(cond, b, orelse, ihp, pers, line, label)

    def _switch(self, ihp, pers, line, label) -> If | Block:
        self.take_word()
        head = self.until("{")
        self.expect("{")
        cases: list[tuple[list[str], list, int]] = []
        default = None
        while True:
            self.ws()
            if self.at("}"):
                self.i += 1
                self.last = self.i
                break
            w = self.peek_word().lower()
            cl = self.line()
            if w == "case":
                self.take_word()
                vals = split_top(self.until(":"), ";")
                self.expect(":")
                cases.append((vals, self.body(False, ("case", "default")), cl))
            elif w == "default":
                self.take_word()
                self.ws()
                if self.at(":"):
                    self.i += 1
                default = self.body(False, ("case", "default"))
            else:
                self.err(tr("expected case or default"))
        node = Block(default, line=line) if default is not None else None
        h = _wrap(head)
        for vals, b, cl in reversed(cases):
            cond = " || ".join(f"{h} = {_wrap(v)}" for v in vals if v)
            node = If(cond, b, [node] if node else None, line=cl)
        if node is None:
            return Block([], ihp, pers, line, label)
        node.ihp, node.pers, node.label = ihp, pers, label
        return node

    def _call(self, line, rets) -> Call:
        self.ws()
        name = self.take_word()
        if not name:
            self.err(tr("call without a function name"))
        self.expect("(")
        args = self.until(")")
        self.expect(")")
        self.expect(";")
        return Call(name, [a for a in split_top(args, ",") if a] if args else [], rets, line)

    def _sctrl(self, name, line, label) -> Ctrl:
        params = []
        while True:
            self.ws()
            if self.at("}"):
                self.i += 1
                self.last = self.i
                break
            pl = self.line()
            key = self.until(":;}")
            if not self.at(":"):
                self.err(tr("{0} parameter without ':' ({1!r})").format(name, key))
            self.i += 1
            val = self.until(";}")
            params.append((key.strip().lower(), val, pl))
            if self.at(";"):
                self.i += 1
        self.comment = None
        return Ctrl(name, params, line, label)


def parse(text: str) -> ZssFile:
    return _P(text).file()


# ------------------------------------------------------------------ ZSS -> CNS


def _has_ihp(stmts) -> bool:
    for s in stmts or ():
        if isinstance(s, (Block, If)) and (s.ihp or _has_ihp(s.body)
                                           or (isinstance(s, If) and _has_ihp(s.orelse))):
            return True
        if isinstance(s, Loop) and _has_ihp(s.body):
            return True
    return False


class _Lower:
    def __init__(self, funcs: dict, warn):
        self.funcs = funcs
        self.warn = warn
        self.out: list[dict] = []
        self.slot = 0
        self.depth = 0
        self.where = ""

    def new_slot(self) -> str:
        self.slot += 1
        return f"zsslocal({self.slot})"

    def slot_of(self, env: dict, name: str) -> str:
        key = name.lower()
        if key not in env:
            env[key] = self.new_slot()
        return env[key]

    def sub(self, text: str, env: dict, line: int) -> str:
        def rep(m):
            if not m.group(1):
                return m.group(0)
            name = m.group(1).lower()
            if name not in env:
                self.warn(tr("{0}, line {1}: ${2} used without let (counts as 0)").format(self.where, line, m.group(1)))
                env[name] = self.new_slot()
            return env[name]
        return _LOCAL.sub(rep, text)

    def emit(self, typ, params, conds, ihp, pers, label, line, prefix=""):
        lab = f"{prefix}{label} @{line}" if label else f"{prefix}@{line}"
        self.out.append({"type": typ, "params": params, "conds": list(conds), "ihp": ihp,
                         "pers": pers, "label": lab})

    def setlocal(self, slot, value, conds, ihp, line, prefix=""):
        self.emit("VarSet", [(slot, value)], conds, ihp, None, "", line, prefix)

    def block(self, stmts, conds, ihp, pers, env, prefix=""):
        for s in stmts or ():
            if isinstance(s, Ctrl):
                params = [(k, " ".join(self.sub(v, env, ln).split())) for k, v, ln in s.params]
                self.emit(s.name, params, conds, ihp, pers, s.label, s.line, prefix)
            elif isinstance(s, Block):
                self.block(s.body, conds, ihp or s.ihp, s.pers if s.pers is not None else pers,
                           env, prefix)
            elif isinstance(s, If):
                self.lower_if(s, conds, ihp, pers, env, prefix)
            elif isinstance(s, Loop):
                self.lower_loop(s, conds, ihp, pers, env, prefix)
            elif isinstance(s, Call):
                self.call(s, conds, ihp, env, prefix)
            elif isinstance(s, Let):
                for name, e in zip(s.names, s.exprs):
                    value = self.sub(e, env, s.line)
                    if name == "_":
                        self.emit("Null", [], [*conds, f"({value}) || 1"], ihp, None, "", s.line,
                                  prefix)
                        continue
                    self.setlocal(self.slot_of(env, name), value, conds, ihp, s.line, prefix)
            elif isinstance(s, Expr):
                if ":=" not in s.text:
                    self.warn(tr("{0}, line {1}: expression without assignment ignored").format(self.where, s.line))
                    continue
                self.emit("Null", [], [*conds, f"({self.sub(s.text, env, s.line)}) || 1"], ihp,
                          None, "", s.line, prefix)
            elif isinstance(s, Jump):
                self.warn(tr("{0}, line {1}: {2} outside a simulated loop").format(self.where, s.line, s.kind))

    MAX_UNROLL = 64

    def lower_loop(self, s: Loop, conds, ihp, pers, env, prefix):
        """for com limites constantes vira o corpo repetido, com $i trocado pelo valor de cada
        volta (como o Ikemen: de a até b inclusive, passo padrão 1). while, break/continue e
        limites que dependem do jogo não são simulados (aviso)."""
        why = None
        if s.kind != "for":
            why = tr("while depends on the game on every turn")
        elif any(isinstance(x, Jump) for x in _walk(s.body)):
            why = "break/continue"
        name, parts = None, split_top(s.header, ";")
        m = re.match(r"\s*\$?([A-Za-z_]\w*)\s*=(?!=)(.*)$", parts[0]) if parts else None
        if m:
            name, parts = m.group(1), [m.group(2)] + parts[1:]
        vals = []
        if not why:
            nums = []
            for e in parts[:3]:
                e = self.sub(e, env, s.line).strip()
                if not re.fullmatch(r"[\d\s+\-*/().]+", e or "x"):
                    why = tr("limit '{0}' depends on the game").format(e)
                    break
                nums.append(int(eval(e, {"__builtins__": {}})))
            if not why:
                a, b = nums[0], nums[1]
                step = nums[2] if len(nums) > 2 else 1
                v = a
                while (v <= b if step > 0 else v >= b) and len(vals) <= self.MAX_UNROLL:
                    vals.append(v)
                    if step == 0:
                        break
                    v += step
                if len(vals) > self.MAX_UNROLL:
                    why = tr("more than {0} turns").format(self.MAX_UNROLL)
        if why:
            self.warn(tr("{0}, line {1}: loop {2} is not simulated ({3})").format(self.where, s.line, s.kind, why))
            return
        ihp = ihp or s.ihp
        pers = s.pers if s.pers is not None else pers
        for v in vals:
            if name:
                env[name.lower()] = str(v)
            self.block(s.body, conds, ihp, pers, env, prefix)

    def lower_if(self, s: If, conds, ihp, pers, env, prefix):
        ihp = ihp or s.ihp
        pers = s.pers if s.pers is not None else pers
        cond = self.sub(s.cond, env, s.line)
        simple = s.orelse is None and len(s.body) == 1 and isinstance(s.body[0], Ctrl)
        if simple:
            self.block(s.body, [*conds, cond], ihp, pers, env, prefix)
            return
        slot = self.new_slot()
        self.setlocal(slot, cond, conds, ihp or _has_ihp(s.body) or _has_ihp(s.orelse), s.line,
                      prefix)
        self.block(s.body, [*conds, slot], ihp, pers, env, prefix)
        if s.orelse:
            self.block(s.orelse, [*conds, f"!{slot}"], ihp, pers, env, prefix)

    def call(self, c: Call, conds, ihp, env, prefix):
        f = self.funcs.get(c.name) or self.funcs.get(c.name.lower())
        if f is None:
            self.warn(tr("{0}, line {1}: function {2} not found").format(self.where, c.line, c.name))
            return
        if self.depth > 8:
            self.warn(tr("{0}, line {1}: too many nested calls ({2})").format(self.where, c.line, c.name))
            return
        if len(c.args) != len(f.args):
            self.warn(tr("{0}, line {1}: {2} takes {3} argument(s), got {4}").format(self.where, c.line, c.name, len(f.args), len(c.args)))
        fenv: dict = {}
        for name, a in zip(f.args, c.args):
            v = self.sub(a, env, c.line)
            if _NUM.fullmatch(v):
                fenv[name.lower()] = v
            else:
                slot = self.new_slot()
                self.setlocal(slot, v, conds, ihp, c.line, prefix)
                fenv[name.lower()] = slot
        for r in f.rets:
            self.slot_of(fenv, r)
        self.depth += 1
        self.block(f.body, conds, ihp, None, fenv, f"{prefix}{f.name}: ")
        self.depth -= 1
        for name, r in zip(c.rets, f.rets):
            if name == "_":
                continue
            self.setlocal(self.slot_of(env, name), fenv[r.lower()], conds, ihp, c.line, prefix)


def _cns_controller(number: int, c: dict) -> str:
    lines = [f"[State {number}, {c['label']}]", f"type = {c['type']}"]
    lines += [f"triggerall = {x}" for x in c["conds"]]
    lines.append("trigger1 = 1")
    if c["pers"] is not None:
        lines.append(f"persistent = {0 if c['pers'] <= 0 else c['pers']}")
    if c["ihp"]:
        lines.append("ignorehitpause = 1")
    lines += [f"{k} = {v}" for k, v in c["params"]]
    return "\n".join(lines)


def lower_section(sec: Section, funcs: dict, warnings: list[str] | None = None) -> str:
    """Um [StateDef] do ZSS como texto CNS equivalente."""
    warns = warnings if warnings is not None else []
    lo = _Lower(funcs, warns.append)
    lo.where = tr("state {0}").format(sec.number)
    lo.block(sec.body, [], False, None, {})
    head = [f"[Statedef {sec.number}]", *(f"{k} = {v}" for k, v in sec.params)]
    parts = ["\n".join(head)] + [_cns_controller(sec.number, c) for c in lo.out]
    return "\n\n".join(parts) + "\n"


def to_cns(text_or_file, funcs: dict | None = None,
           warnings: list[str] | None = None) -> str:
    """Todos os statedefs de um arquivo ZSS como um CNS equivalente (só para leitura)."""
    f = parse(text_or_file) if isinstance(text_or_file, str) else text_or_file
    reg = dict(funcs or {})
    for fn in f.functions:
        reg.setdefault(fn.name, fn)
        reg.setdefault(fn.name.lower(), fn)
    out = []
    for s in f.statedefs:
        if s.number is None:
            if warnings is not None:
                warnings.append(tr("line {0}: [StateDef {1}] is not simulated (the number comes from engine constants)").format(s.line, s.name))
            continue
        out.append(lower_section(s, reg, warnings))
    return "\n".join(out)


def register(funcs: dict, f: ZssFile, override: bool = False) -> None:
    for fn in f.functions:
        for k in (fn.name, fn.name.lower()):
            if override or k not in funcs:
                funcs[k] = fn


def is_zss(text: str) -> bool:
    """Texto de um statedef em ZSS (e não CNS)?"""
    return not re.search(r"(?im)^\s*\[state\s", text) and bool(
        re.search(r"(?i)\[statedef\s+[-+]?\d+\s*[;\]]", text) and ("{" in text or ";" in text))


def doc_of(text: str, funcs: dict, warnings: list[str]) -> mtext.Document:
    return mtext.parse(to_cns(text, funcs, warnings))


# ------------------------------------------------------------------ conferência


def _walk(stmts):
    for st in stmts or ():
        yield st
        if isinstance(st, (Block, Loop)):
            yield from _walk(st.body)
        elif isinstance(st, If):
            yield from _walk(st.body)
            yield from _walk(st.orelse)


_IDENT_RE = re.compile(r"[A-Za-z_][\w.]*")
_ATTR_PART = re.compile(r"^[NSHA][ATP]$", re.I)            # A = qualquer nível (AA, AP)


def _known_words() -> set[str]:
    from .zss import ikemen_params
    from .. import validate
    words = set(validate.grammar()["triggers"]) | set(validate.grammar()["redirects"])
    p = Path(__file__).resolve().parents[1] / "data" / "ikemen_params.json"
    if p.exists():
        words |= set(json.loads(p.read_text("utf-8")).get("triggers", []))
    return words


def _ikemen_consts() -> set[str]:
    """Constantes do motor (const(...) do compilador) e as de data/common.const do Ikemen
    configurado, que todo personagem herda (StateStand, AnimCrouchHit_hard...)."""
    p = Path(__file__).resolve().parents[1] / "data" / "ikemen_params.json"
    out = set(json.loads(p.read_text("utf-8")).get("consts", [])) if p.exists() else set()
    from .. import config
    ik = config.load().get("ikemen")
    common = Path(ik) / "data" / "common.const" if ik else None
    if common and common.exists():
        text = common.read_text("utf-8", errors="replace")
        out |= {k.lower() for k in re.findall(r"(?m)^\s*([\w.]+)\s*=", text)}
    return out


def _bad_identifiers(expr: str, words: set[str]) -> list[str]:
    """Nomes numa expressão que não são gatilho, redirecionamento, palavra-chave nem flag."""
    from .. import validate
    body = re.sub(r'"[^"]*"', '""', expr)
    body = re.sub(r"\$\w+", "0", body)
    body = re.sub(r"(?i)\b(const|gethitvar|stagevar|map|sysvar|sysfvar|var|fvar)\s*\([^()]*\)", "0", body)
    body = re.sub(r"\(\s*[A-Za-z_][\w.]*\s*\)", "(0)", body)   # nome como argumento: mugenVersion(major)
    bad = []
    for m in _IDENT_RE.finditer(body):
        tok = m.group(0)
        if tok.lower() in words or validate._known_identifier(tok):
            continue
        prev = body[:m.start()].rstrip()
        # valor depois de =/!=/, em comparações de tipo e atributo (stateType = A, hitDefAttr = SC, NA)
        if prev.endswith((",", "=")) and len(tok) <= 3:
            continue
        bad.append(tok)
    return bad


_EQ_TRIGGER_RE = re.compile(
    r"(?i)(?<![\w.$])(animelem|timemod|command|selfcommand|proj(?:hit|contact|guarded)\d*)\b(?!\s*\()\s*(\S{0,2})")


def eq_problems(expr: str) -> list[str]:
    """Gatilhos do formato antigo que o Ikemen só compila com `=` logo depois (`animElem = 3`,
    `timeMod = 4, 2`, `command = "x"`; o command aceita `!=`). `animElem <= 4` não compila."""
    out = []
    for m in _EQ_TRIGGER_RE.finditer(re.sub(r'"[^"]*"', '""', expr)):
        name, op = m.group(1), m.group(2)
        if op.startswith("=") and not op.startswith("=="):
            continue
        if op.startswith("!=") and name.lower() in ("command", "selfcommand"):
            continue
        hint = tr(" (to compare the frame: animElemNo(0) <= 4 or animElemTime(n) >= 0)") \
            if name.lower() == "animelem" else ""
        out.append(tr("'{0}' requires '=' right after it, not '{1}'{2}").format(name, op or tr('(nothing)'), hint))
    return out


def check(f: ZssFile, user_consts: set[str] | None = None) -> list[tuple[str, int, str]]:
    """Problemas que o Ikemen recusa ao compilar (controlador ou parâmetro desconhecido,
    parâmetro vazio ou repetido, persistent em estado negativo, gatilho escrito errado, attr
    inválido) e o que ele aceita calado mas está errado (const com nome que não existe vale 0).
    [(severidade, linha, msg)]"""
    from .zss import _EMPTY_OK, _normalize, ikemen_params, param_known
    table = ikemen_params()
    words = _known_words()
    consts = _ikemen_consts() | {c.lower() for c in (user_consts or ())}
    out = []

    def exprs(st):
        if isinstance(st, If):
            yield st.line, st.cond
        elif isinstance(st, Let):
            for e in st.exprs:
                yield st.line, e
        elif isinstance(st, Expr):
            yield st.line, st.text
        elif isinstance(st, Loop) and st.kind == "while":
            yield st.line, st.header
        elif isinstance(st, Call):
            for a in st.args:
                yield st.line, a

    for sec in f.sections:
        for st in _walk(sec.body):
            if isinstance(st, (Block, If)) and st.pers is not None and sec.kind == "statedef" \
                    and sec.number is not None and sec.number < 0:
                out.append(("error", st.line, tr("persistent does not work in a negative state")))
            for ln, e in exprs(st):
                for tok in dict.fromkeys(_bad_identifiers(e, words)):
                    out.append(("error", ln, tr("unknown trigger or name '{0}'").format(tok)))
            texts = list(exprs(st)) + ([(ln, v) for _, v, ln in st.params] if isinstance(st, Ctrl) else [])
            for ln, e in texts:
                for msg in eq_problems(e):
                    out.append(("error", ln, msg))
                for name in re.findall(r"(?i)\bconst\s*\(\s*([\w.]+)\s*\)", e):
                    if consts and name.lower() not in consts:
                        out.append(("error", ln, tr("const({0}) does not exist (nor in the character's [Constants]): Ikemen accepts it as 0").format(name)))
            if not isinstance(st, Ctrl) or not table:
                continue
            kind = st.name.lower()
            info = table.get(kind)
            if info is None:
                out.append(("error", st.line, tr("controller '{0}' does not exist in Ikemen").format(st.name)))
                continue
            fixed = {k: v for k, v, _ in st.params}
            _normalize(kind, fixed, lambda m: None)   # o que o tradutor do CNS teria de corrigir
            for k, v, ln in st.params:
                if k not in fixed:
                    out.append(("error", ln, tr("{0}: '{1}: {2}' is invalid in ZSS").format(st.name, k, v)))
                elif fixed[k] != v:
                    out.append(("error", ln, tr("{0}: '{1}: {2}' — ZSS requires the whole word: '{3}'").format(st.name, k, v, fixed[k])))
                if k == "attr" and kind in ("hitdef", "reversaldef", "projectile"):
                    msg = attr_problem(v)
                    if msg:
                        out.append(("error", ln, f"{st.name}: {msg}"))
            seen = set()
            for k, v, ln in st.params:
                if k in seen:
                    out.append(("error", ln, tr("{0}: parameter '{1}' repeated").format(st.name, k)))
                seen.add(k)
                if v == "" and (kind, k) not in _EMPTY_OK:
                    out.append(("error", ln, tr("{0}: '{1}' without a value").format(st.name, k)))
                if not param_known(info, k):
                    out.append(("error", ln, tr("{0}: parameter '{1}' does not exist in Ikemen").format(st.name, k)))
    return out


def attr_problem(v: str) -> str | None:
    """attr de HitDef: `S, NA` (tipo S/C/A; depois N/S/H + A/T/P). None se estiver certo."""
    parts = [p.strip() for p in v.split(",")]
    if not parts or not re.fullmatch(r"[SCA]*", parts[0], re.I):   # vazio aparece em chars prontos
        return tr("attr '{0}': the 1st item is S, C, A or a combination (SCA)").format(v)
    for p in parts[1:]:
        if not _ATTR_PART.match(p):
            return tr("attr '{0}': '{1}' is not N/S/H/A + A/T/P (NA, SA, HP, AA...)").format(v, p)
    return None


# ------------------------------------------------------------------ arquivo editável


class ZssStates:
    """Arquivo .zss com a mesma interface de cns.States.

    `statedefs`/`doc` são o CNS rebaixado (para ler, simular e auditar); edições
    (`set_statedef_text`, `add_controller`, `remove_statedef`) mudam o texto ZSS e `save`
    grava o ZSS. Texto CNS recebido nas edições é traduzido para ZSS antes.
    """

    def __init__(self, text: str, path=None, funcs: dict | None = None):
        from pathlib import Path
        self.text = text
        self.path = Path(path) if path else None
        self.funcs = funcs if funcs is not None else {}
        self.warnings: list[str] = []
        self._build()

    # ---- leitura
    def _build(self) -> None:
        from . import cns
        self.file = parse(self.text)
        register(self.funcs, self.file, override=True)
        self.warnings = []
        self.doc = mtext.parse(to_cns(self.file, self.funcs, self.warnings))
        view = cns.States(self.doc, self.path)
        self.statedefs, self.orphans = view.statedefs, view.orphans

    def _index(self) -> None:          # compatibilidade com quem reindexa cns.States
        self._build()

    def section(self, number: int) -> Section | None:
        return next((s for s in self.file.statedefs if s.number == number), None)

    def statedef_zss(self, number: int) -> str:
        s = self.section(number)
        if not s:
            raise KeyError(number)
        return self.text[s.start:s.end].rstrip() + "\n"

    def zss_line(self, number: int) -> int | None:
        s = self.section(number)
        return s.line if s else None

    # ---- edição
    def _set_text(self, text: str) -> None:
        parse(text)                      # erro de sintaxe não estraga o arquivo
        self.text = text
        self._build()

    @staticmethod
    def _as_zss(number: int, text: str) -> str:
        if is_zss(text):
            return text.strip() + "\n"
        from . import cns, zss
        st = cns.loads(text)
        sd = st.statedefs.get(number)
        if not sd:
            raise ValueError(tr("the text must start with [StateDef N] (ZSS) or [Statedef N] (CNS)"))
        warns: list[str] = []
        return zss.statedef_to_zss(sd, warns)

    def set_statedef_text(self, number: int, text: str):
        new = self._as_zss(number, text)
        f = parse(new)
        if len(f.statedefs) != 1 or f.statedefs[0].number != number:
            raise ValueError(tr("the text must have a single [StateDef {0}]").format(number))
        s = self.section(number)
        if s:
            body = self.text[:s.start] + new.rstrip() + self.text[s.end:]
        else:
            body = self.text.rstrip() + "\n\n" + new
        self._set_text(body if body.endswith("\n") else body + "\n")
        return self.statedefs[number]

    def remove_statedef(self, number: int) -> bool:
        s = self.section(number)
        if not s:
            return False
        before = self.text[:s.start].rstrip()
        after = self.text[s.end:].lstrip("\n")
        self._set_text((before + "\n\n" + after).lstrip("\n") if before else after)
        return True

    def add_controller(self, number: int, text: str):
        """Acrescenta controladores (ZSS, ou um [State] em CNS) ao fim do statedef."""
        s = self.section(number)
        if not s:
            raise KeyError(number)
        if re.search(r"(?im)^\s*\[state\s", text):
            from . import cns, zss
            sd = cns.loads(f"[Statedef {number}]\n\n{text}").statedefs[number]
            z = zss.statedef_to_zss(sd, [])
            snippet = z.split("\n", 2)[2].strip()     # sem o cabeçalho
        else:
            snippet = text.strip()
        body = self.text[:s.end].rstrip() + "\n\n" + snippet + self.text[s.end:]
        self._set_text(body)
        return self.statedefs[number].controllers[-1]

    def save(self, path=None) -> None:
        from pathlib import Path
        Path(path or self.path).write_text(self.text, "utf-8")


def load(path, funcs: dict | None = None) -> ZssStates:
    from pathlib import Path
    return ZssStates(Path(path).read_text("utf-8", errors="replace"), path, funcs)
