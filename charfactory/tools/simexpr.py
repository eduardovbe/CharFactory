"""Avaliador das expressões do CNS (gatilhos e parâmetros) para o simulador de golpes.

Cobre o que os chars costumam usar: números, operadores do MUGEN (com divisão inteira
entre inteiros), intervalos (`x = [a, b]`), `ifelse`/`cond`, funções matemáticas, gatilhos com
nome composto (`pos x`, `p2dist x`, `const(velocity.run.fwd.x)`), formas especiais
(`animelem = 3, >= 0`, `timemod = 4, 0`), letras de tipo (`statetype = A`), strings
(`command = "x"`) e redirecionamento (`enemynear, pos x`, `root, stateno`, `helper(3190), time`).

O contexto (quem avalia) é um objeto com `trigger(name, arg)` e `redirect(kind, arg)`;
gatilho desconhecido levanta `Unsupported` (o simulador anota e considera falso).
Precedência e regras conferidas com a documentação do MUGEN e o compilador do Ikemen GO
(src/compiler.go): `**` > `* / %` > `+ -` > comparações > `= !=` > `&` > `^` > `|` > `&&` >
`^^` > `||`.
"""
from __future__ import annotations

import math
import re
from ..i18n import tr


class Unsupported(Exception):
    pass


TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+\.\d*|\.\d+|\d+)
  | (?P<str>"[^"]*")
  | (?P<op>\*\*|:=|!=|<=|>=|&&|\|\||\^\^|[-+*/%<>=!~&|^(),\[\]])
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
""", re.X)

# gatilhos seguidos de um eixo (x/y/z)
AXIS = {"pos", "vel", "p2dist", "p2bodydist", "p1dist", "screenpos", "rootdist", "parentdist",
        "p2pos", "hitvel", "gethitvar", "camerapos"}
REDIRECT = {"enemynear", "enemy", "root", "parent", "helper", "target", "p2", "partner",
            "playerid", "player"}
LETTERS = {"statetype", "movetype", "p2statetype", "p2movetype", "physics"}
# comparados com uma palavra ou um texto: teammode = single, name = "Kung Fu Man"
WORDS = {"teammode", "name", "p1name", "p2name", "p3name", "p4name", "authorname", "displayname",
         "gamemode", "stagevar"}
ASSIGNABLE = {"var", "fvar", "sysvar", "sysfvar", "zsslocal"}
FUNCS = {"ifelse", "cond", "cos", "sin", "tan", "acos", "asin", "atan", "abs", "floor", "ceil",
         "exp", "ln", "log", "min", "max", "sign", "clamp", "sqrt", "var", "fvar", "sysvar",
         "sysfvar", "const", "animelemtime", "animelemno", "animexist", "selfanimexist",
         "numhelper", "numexplod", "numtarget", "numprojid", "projhittime", "projcontacttime",
         "ishelper", "stateno", "gethitvar", "const240p", "const480p", "const720p",
         "animelemlength"}


def tokenize(s: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(s):
        m = TOKEN.match(s, i)
        if not m:
            raise Unsupported(tr("unexpected character in {0!r}: {1!r}").format(s, s[i]))
        i = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        out.append((kind, m.group()))
    return out


def _num(tok: str):
    return float(tok) if "." in tok else int(tok)


def _div(a, b):
    if b == 0:
        return 0                                   # MUGEN: divisão por zero dá 0 (e avisa)
    if isinstance(a, int) and isinstance(b, int):
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q   # trunca para zero
    return a / b


def _mod(a, b):
    if b == 0:
        return 0
    if isinstance(a, int) and isinstance(b, int):
        r = abs(a) % abs(b)
        return r if a >= 0 else -r
    return math.fmod(a, b)


def _b(v) -> int:
    return 1 if v else 0


class Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = tokenize(text)
        self.i = 0

    # ---------------------------------------------------------------- utilitários
    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else (None, None)

    def take(self, val=None):
        t = self.peek()
        if val is not None and t[1] != val:
            raise Unsupported(tr("expected {0!r} in {1!r}").format(val, self.text))
        self.i += 1
        return t

    def parse(self):
        node = self.expr()
        if self.i != len(self.toks):
            raise Unsupported(tr("leftover in expression {0!r}: {1}").format(self.text, self.toks[self.i:]))
        return node

    # ---------------------------------------------------------------- precedência
    LEVELS = [["||"], ["^^"], ["&&"], ["|"], ["^"], ["&"], ["=", "!="], ["<", "<=", ">", ">="],
              ["+", "-"], ["*", "/", "%"], ["**"]]

    def expr(self, level=0):
        if level == len(self.LEVELS):
            return self.unary()
        return self._rest(level, self.expr(level + 1))

    def _rest(self, level, left):
        ops = self.LEVELS[level]
        while self.peek()[1] in ops:
            op = self.take()[1]
            if op in ("=", "!=") and self.peek()[1] in ("[", "("):
                iv = self.try_interval()
                if iv is not None:
                    left = ("interval", op, left, iv)
                    continue
            right = self.expr(level + 1) if op != "**" else self.expr(level)  # ** à direita
            left = ("bin", op, left, right)
        return left

    def try_interval(self):
        """`[a, b]` / `(a, b)` depois de = ou !=; se não for intervalo, volta."""
        save = self.i
        open_ = self.take()[1]
        try:
            a = self.expr()
            if self.peek()[1] != ",":
                raise Unsupported(tr("not an interval"))
            self.take(",")
            b = self.expr()
            close = self.take()[1]
            if close not in ("]", ")"):
                raise Unsupported(tr("not an interval"))
            return (open_ == "[", a, b, close == "]")
        except Unsupported:
            self.i = save
            return None

    def unary(self):
        t = self.peek()
        if t[1] in ("!", "-", "~", "+"):
            self.take()
            return ("un", t[1], self.unary())
        return self.primary()

    def primary(self):
        kind, val = self.peek()
        if kind is None:
            raise Unsupported(tr("incomplete expression {0!r}").format(self.text))
        if kind == "num":
            self.take()
            return ("num", _num(val))
        if kind == "str":
            self.take()
            return ("str", val[1:-1])
        if val == "(":
            self.take()
            e = self.expr()
            self.take(")")
            return e
        if kind != "name":
            raise Unsupported(tr("unexpected token {0!r} in {1!r}").format(val, self.text))
        name = val.lower()
        self.take()
        # redirecionamento: enemynear, pos x | helper(3190), time | root, stateno
        if name in REDIRECT:
            arg = None
            if self.peek()[1] == "(":
                self.take("(")
                arg = self.expr()
                self.take(")")
            if self.peek()[1] == ",":
                self.take(",")
                return ("redirect", name, arg, self.primary_trigger_chain())
            if name in ("numenemy", "numtarget"):
                return ("trig", name, arg)
            raise Unsupported(tr("redirection without a trigger: {0}").format(name))
        return self.after_name(name)

    def primary_trigger_chain(self):
        """O gatilho depois do redirecionamento (pode ter operadores unários, não binários)."""
        return self.unary()

    def old_compare(self):
        """A 2ª comparação das formas antigas (AnimElem = n, TimeMod = n, ProjHit = n), como o
        evaluateComparison do Ikemen: a vírgula é opcional (`AnimElem = 9 = 21` compara o
        AnimElemTime(9) com 21) e sem operador depois da vírgula vale "="."""
        comma = self.peek()[1] == ","
        if comma:
            self.take(",")
        if self.peek()[1] in ("=", "!=", "<", "<=", ">", ">="):
            op = self.take()[1]
        elif comma:
            op = "="
        else:
            return None, None, False
        return op, self.expr(7), comma

    def after_name(self, name):
        # formas especiais
        if name == "animelem" and self.peek()[1] == "=":
            self.take("=")
            n = self.expr(7)                      # acima de = e !=
            op, m, _ = self.old_compare()
            return ("animelem", n, op, m)
        # forma antiga dos projéteis (Ikemen, compiler.go): ProjHit[ID] = 0|1[, [op] t] vira
        # ProjHitTime(ID) = 0 (sem a 2ª parte) ou ProjHitTime(ID) >= 0 && ProjHitTime(ID) op t;
        # "= 0" nega o resultado
        old = re.fullmatch(r"(projhit|projguarded|projcontact)(\d*)", name)
        if old and self.peek()[1] == "=":
            self.take("=")
            n = self.expr(7)
            op, m, comma = self.old_compare()
            return ("projold", old.group(1) + "time", int(old.group(2) or 0), n, op, m, comma)
        if name == "timemod" and self.peek()[1] == "=":
            self.take("=")
            d = self.expr(7)
            op, r, _ = self.old_compare()
            if op is None:
                raise Unsupported(tr("TimeMod without a comparison in {0!r}").format(self.text))
            return ("timemod", d, op, r)
        if name in LETTERS and self.peek()[1] in ("=", "!="):
            op = self.take()[1]
            letter = self.take()[1]
            return ("letter", name, op, letter.upper())
        if name == "command" and self.peek()[1] in ("=", "!="):
            op = self.take()[1]
            s = self.take()[1].strip('"')
            return ("command", op, s)
        if name in WORDS and name != "stagevar" and self.peek()[1] in ("=", "!="):
            op = self.take()[1]
            return ("word", name, op, self.take()[1].strip('"').lower())
        if name == "const" and self.peek()[1] == "(":
            self.take("(")
            parts = []
            while self.peek()[1] != ")":
                parts.append(self.take()[1])
            self.take(")")
            return ("const", "".join(parts).lower())
        if name == "gethitvar" and self.peek()[1] == "(":     # gethitvar(fall.yvel): nome, não gatilho
            self.take("(")
            parts = []
            while self.peek()[1] != ")":
                if self.peek()[0] is None:
                    raise Unsupported(tr("gethitvar without ')' in {0!r}").format(self.text))
                parts.append(self.take()[1])
            self.take(")")
            return ("trig", "gethitvar", ("str", "".join(parts).lower()))
        if name in AXIS:
            axis = None
            if self.peek()[0] == "name" and self.peek()[1].lower() in ("x", "y", "z"):
                axis = self.take()[1].lower()
            return ("trig", f"{name} {axis}" if axis else name, None)
        if self.peek()[1] == "(" and (name in FUNCS or name not in ("time",)):
            self.take("(")
            args = []
            if self.peek()[1] != ")":
                args.append(self.expr())
                while self.peek()[1] == ",":
                    self.take(",")
                    args.append(self.expr())
            self.take(")")
            if self.peek()[1] == ":=" and name in ASSIGNABLE and len(args) == 1:
                # como no Ikemen: `var(n) := v` é um valor; v vai até o nível de = e !=
                self.take()
                return ("assign", name, args[0], self.expr(self.LEVELS.index(["=", "!="])))
            return ("call", name, args)
        return ("trig", name, None)


def parse(text: str):
    return Parser(text).parse()


_CACHE: dict[str, object] = {}


def compile_expr(text: str):
    t = text.strip()
    if t not in _CACHE:
        _CACHE[t] = parse(t)
    return _CACHE[t]


def evaluate(text_or_node, ctx):
    node = compile_expr(text_or_node) if isinstance(text_or_node, str) else text_or_node
    return _ev(node, ctx)


def _ev(n, ctx):
    k = n[0]
    if k == "num":
        return n[1]
    if k == "str":
        return n[1]
    if k == "bin":
        op = n[1]
        if op == "&&":
            return _b(_ev(n[2], ctx) and _ev(n[3], ctx))
        if op == "||":
            return _b(_ev(n[2], ctx) or _ev(n[3], ctx))
        a, b = _ev(n[2], ctx), _ev(n[3], ctx)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return _div(a, b)
        if op == "%":
            return _mod(a, b)
        if op == "**":
            try:
                return a ** b
            except (OverflowError, ZeroDivisionError):
                return 0
        if op == "=":
            return _b(a == b)
        if op == "!=":
            return _b(a != b)
        if op == "<":
            return _b(a < b)
        if op == "<=":
            return _b(a <= b)
        if op == ">":
            return _b(a > b)
        if op == ">=":
            return _b(a >= b)
        if op == "^^":
            return _b(bool(a) != bool(b))
        if op == "&":
            return int(a) & int(b)
        if op == "|":
            return int(a) | int(b)
        if op == "^":
            return int(a) ^ int(b)
        raise Unsupported(op)
    if k == "assign":
        setter = getattr(ctx, "set_var", None)
        if setter is None:
            raise Unsupported(tr("assignment to {0} outside the character").format(n[1]))
        return setter(n[1], _ev(n[2], ctx), _ev(n[3], ctx))
    if k == "un":
        v = _ev(n[2], ctx)
        if n[1] == "!":
            return _b(not v)
        if n[1] == "-":
            return -v
        if n[1] == "~":
            return ~int(v)
        return v
    if k == "interval":
        _, op, left, (lo_incl, a, b, hi_incl) = n
        v, lo, hi = _ev(left, ctx), _ev(a, ctx), _ev(b, ctx)
        inside = (v >= lo if lo_incl else v > lo) and (v <= hi if hi_incl else v < hi)
        return _b(inside if op == "=" else not inside)
    if k == "animelem":
        num = _ev(n[1], ctx)
        if n[2] is None:
            return _b(ctx.animelem_start(int(num)))
        t = ctx.animelem_time(int(num))
        if t is None:
            return 0
        m = _ev(n[3], ctx)
        return _ev(("bin", n[2], ("num", t), ("num", m)), ctx)
    if k == "projold":
        _, trig, pid, want, op, m, comma = n
        t = ctx.trigger(trig, pid)
        if op is None:
            r = t == 0
        else:                                   # só a forma com vírgula exige contato (t >= 0)
            r = (t >= 0 or not comma) and bool(_ev(("bin", op, ("num", t), ("num", _ev(m, ctx))), ctx))
        return _b(r if _ev(want, ctx) else not r)
    if k == "timemod":
        d, op, r = _ev(n[1], ctx), n[2], _ev(n[3], ctx)
        if d == 0:
            return 0
        return _ev(("bin", op, ("num", _mod(ctx.trigger("time", None), d)), ("num", r)), ctx)
    if k == "letter":
        v = ctx.trigger(n[1], None)
        return _b((v == n[3]) == (n[2] == "="))
    if k == "word":
        v = str(ctx.trigger(n[1], None)).lower()
        return _b((v == n[3]) == (n[2] == "="))
    if k == "command":
        v = ctx.command(n[2])
        return _b(v if n[1] == "=" else not v)
    if k == "const":
        return ctx.const(n[1])
    if k == "trig":
        arg = _ev(n[2], ctx) if n[2] is not None else None
        return ctx.trigger(n[1], arg)
    if k == "redirect":
        arg = _ev(n[2], ctx) if n[2] is not None else None
        other = ctx.redirect(n[1], arg)
        if other is None:
            msg = tr("redirection without a target: {0}").format(n[1])
            if getattr(ctx, "lenient_redirect", False):     # Ikemen: vale falso só ali
                ctx.sim.note(msg)
                return 0
            raise Unsupported(msg)
        return _ev(n[3], other)
    if k == "call":
        name, args = n[1], n[2]
        if name == "ifelse" or name == "cond":
            return _ev(args[1], ctx) if _ev(args[0], ctx) else _ev(args[2], ctx)
        vals = [_ev(a, ctx) for a in args]
        f = {"cos": math.cos, "sin": math.sin, "tan": math.tan, "abs": abs,
             "floor": lambda v: int(math.floor(v)), "ceil": lambda v: int(math.ceil(v)),
             "exp": math.exp, "sqrt": lambda v: math.sqrt(max(0, v)),
             "acos": lambda v: math.acos(max(-1, min(1, v))),
             "asin": lambda v: math.asin(max(-1, min(1, v))), "atan": math.atan,
             "ln": lambda v: math.log(v) if v > 0 else 0,
             "sign": lambda v: (v > 0) - (v < 0)}.get(name)
        if f:
            return f(vals[0])
        if name == "log":
            return math.log(vals[1], vals[0]) if vals[0] > 0 and vals[1] > 0 else 0
        if name == "min":
            return min(vals)
        if name == "max":
            return max(vals)
        if name == "clamp":
            return max(vals[1], min(vals[2], vals[0]))
        return ctx.trigger(name, vals[0] if len(vals) == 1 else vals)
    raise Unsupported(str(k))
