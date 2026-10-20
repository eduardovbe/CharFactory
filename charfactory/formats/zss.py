"""Tradução de estados CNS para ZSS (a linguagem de estados do Ikemen GO).

Segue o compilador do Ikemen (src/compiler.go, stateCompileCNS × stateCompileZSS) para que
o .zss se comporte igual ao CNS de origem:

- triggers: (triggerall && ...) && ((trigger1 && ...) || (trigger2 && ...) || ...); os grupos
  param no primeiro número que falta; um grupo com a constante 0 nunca dispara e um grupo
  só de constantes não-zero sempre dispara;
- persistent: > 128 vale 1, <= 0 vira persistent(0) (uma vez); estados negativos ignoram
  (o ZSS dá erro se aparecer neles);
- ignorehitpause vira atributo do bloco; Explod, ModifyExplod e AfterImage também leem como
  parâmetro, então ficam com os dois;
- `var(n) = x` (só CNS) vira `v: n; value: x` (fvar → fv, sysvar → sysv, sysfvar → sysfv);
- parâmetro repetido: vale o primeiro, como no CNS; parâmetro vazio é descartado (o ZSS
  recusa);
- estados negativos não aceitam parâmetros no [StateDef].

Controladores seguidos com a mesma condição de tempo/animação viram um bloco só quando os
anteriores não mexem no que a condição lê (ver _PURE e _SAFE_COND).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import cns
from ..i18n import tr

_CNS_ONLY = {"type", "persistent", "ignorehitpause"}
_IHP_PARAM = {"explod", "modifyexplod", "afterimage"}
_VARSET = {"varset", "varadd", "parentvarset", "parentvaradd", "rootvarset", "rootvaradd"}
_VAR_KEY = re.compile(r"^(sysfvar|sysvar|fvar|var)\s*\((.+)\)$", re.I)
_VAR_PARAM = {"var": "v", "fvar": "fv", "sysvar": "sysv", "sysfvar": "sysfv"}
_INT = re.compile(r"^[+-]?\d+$")
# vazio com significado: "reversal.attr =" desliga a ReversalDef
_EMPTY_OK = {("reversaldef", "reversal.attr"), ("hitoverride", "attr")}

# controladores que não mudam tempo nem animação do próprio char no tick em que rodam
_PURE = {"playsnd", "stopsnd", "sndpan", "explod", "modifyexplod", "removeexplod", "envshake",
         "fallenvshake", "palfx", "bgpalfx", "allpalfx", "hitdef", "reversaldef", "sprpriority",
         "assertspecial", "nothitby", "hitby", "playerpush", "screenbound", "trans",
         "angledraw", "angleset", "angleadd", "anglemul", "afterimage", "afterimagetime",
         "makedust", "gamemakeanim", "superpause", "pause", "width", "velset", "veladd",
         "velmul", "posadd", "posset", "poweradd", "powerset", "turn", "helper", "ctrlset",
         "offset", "forcefeedback", "envcolor", "attackmulset", "defencemulset", "hitoverride",
         "targetbind", "bindtoroot", "bindtoparent", "bindtotarget", "posfreeze", "projectile"}
# condição que só lê tempo/animação/round (nada que um controlador _PURE altere)
_SAFE_COND = re.compile(
    r"^(?:\s|\d|\.|[-+*/%()\[\],!=<>&|]|\btime\b|\banimtime\b|\banimelem\b|\banimelemtime\b"
    r"|\banimelemno\b|\broundstate\b|\bgametime\b)*$", re.I)

_WORDS = sorted("""after image time all pal fx angle add draw mul set append to clipboard assert
special attack dist bg bind parent root target change anim state clear ctrl defence destroy self
display env color shake explod fall force feedback game make gravity helper hit by def damage vel
override life dust modify move reset not null offset var pause player push play snd pos freeze
power projectile remap remove text reversal run screen bound pan spr priority type stop super drop
facing trans turn random range victory quote width zoom analog vector command input camera list
depth dialogue dizzy points get guard break ground level height lifebar action load file map match
restart bgm reflection shadow stage storyboard print console red sprite round save score shader
shift tag in out team clsn""".split(), key=len, reverse=True)


def sctrl_name(cns_type: str) -> str:
    """Nome do controlador no estilo dos .zss do Ikemen: ChangeState → changeState."""
    t = cns_type.strip()
    if not t:
        return t
    if any(ch.isupper() for ch in t):
        return t[0].lower() + t[1:]
    parts, i = [], 0
    while i < len(t):
        w = next((w for w in _WORDS if t.startswith(w, i)), None)
        if w is None:                       # palavra desconhecida: devolve como veio
            return t
        parts.append(w)
        i += len(w)
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _scan_top(expr: str):
    """Itera (índice, caractere) fora de parênteses, colchetes e aspas."""
    depth, quoted = 0, False
    for i, ch in enumerate(expr):
        if ch == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0:
            yield i, ch


def _simple(expr: str) -> bool:
    """Sem &&, ||, ^^ nem vírgula no nível de cima: pode entrar numa conjunção sem parênteses."""
    prev = ""
    for _, ch in _scan_top(expr):
        if ch == "," or (ch in "&|^" and prev == ch):
            return False
        prev = ch
    return True


def _wrap(expr: str) -> str:
    return expr if _simple(expr) else f"({expr})"


def _and(terms: list[str]) -> str:
    if len(terms) == 1:
        return terms[0]
    return " && ".join(_wrap(t) for t in terms)


_ANIMELEM_NE = re.compile(r"(?i)(?<![\w.])animelem\s*!=\s*(\d+)")


def _old_triggers(term: str) -> str:
    """`animelem != 5` passa no MUGEN e não compila no Ikemen; o equivalente exato é
    `animElemTime(5) != 0`."""
    return _ANIMELEM_NE.sub(r"animElemTime(\1) != 0", term)


def condition(trig: dict[str, list[str]]) -> str | None:
    """Condição ZSS equivalente aos triggers CNS; '' = sempre; None = nunca dispara.

    Lança ValueError se faltar trigger1 (o CNS também recusa)."""
    trig = {k: [_old_triggers(t) for t in v] for k, v in trig.items()}
    groups: list[list[str]] = []
    nums = sorted(int(k) for k in trig if k.isdigit())
    if not nums or nums[0] != 1:
        raise ValueError(tr("trigger1 is missing"))
    always = False
    for n in range(1, nums[-1] + 1):
        if str(n) not in trig:
            break                           # o CNS para no primeiro número que falta
        terms = [t.strip() for t in trig[str(n)]]
        if any(_INT.match(t) and int(t) == 0 for t in terms):
            continue                        # grupo que nunca é verdadeiro
        terms = [t for t in terms if not _INT.match(t)]
        if not terms:
            always = True                   # grupo só de constantes não-zero
            break
        groups.append(terms)
    tall = [t.strip() for t in trig.get("triggerall", []) if not (_INT.match(t.strip()) and int(t) != 0)]
    if not always and not groups:
        return None
    if always:
        return _and(tall) if tall else ""
    if len(groups) == 1:
        return _and(tall + groups[0])
    ors = " || ".join(_and(g) if len(g) == 1 else f"({_and(g)})" for g in groups)
    return _and(tall + [f"({ors})"]) if tall else ors


# o CNS aceita só a 1ª letra; o ZSS exige a palavra (compiler.go: paramAnimtype/Hittype/Postype)
_ANIMTYPE = {"l": "Light", "m": "Medium", "h": "Hard", "b": "Back", "u": "Up", "d": "DiagUp"}
_HITTYPE = {"h": "High", "l": "Low", "t": "Trip", "n": "None"}
_POSTYPE = {"p": "p1", "f": "front", "b": "back", "l": "left", "r": "right", "n": "none"}
_POSTYPE_FULL = {"p1", "p2", "front", "back", "left", "right", "none"}
_TRANS = {"default", "none", "add", "add1", "addalpha", "sub", "subadd"}


def _normalize(kind: str, params: dict[str, str], warn) -> None:
    """Ajusta valores que o CNS tolera e o ZSS recusa (edita params no lugar)."""
    for k in list(params):
        v = params[k]
        low = v.strip().lower()
        if k in ("animtype", "air.animtype", "fall.animtype") and low:
            if low not in ("light", "medium", "hard", "heavy", "back", "up", "diagup"):
                if low[0] in _ANIMTYPE:
                    params[k] = _ANIMTYPE[low[0]]
        elif k in ("ground.type", "air.type") and low and kind in ("hitdef", "reversaldef", "modifyhitdef"):
            if low not in ("high", "low", "trip", "none") and low[0] in _HITTYPE:
                params[k] = _HITTYPE[low[0]]
        elif k == "postype" and low and low not in _POSTYPE_FULL:
            params[k] = "p2" if low.startswith("p2") else _POSTYPE.get(low[0], v)
        elif k == "space" and low not in ("stage", "screen", "none"):
            warn(tr("invalid space '{0}': CNS ignores it, removed").format(v))
            del params[k]
    for pre in ("", "afterimage.", "shadow.", "reflection."):
        t, a = pre + "trans", pre + "alpha"
        if t in params and params[t].strip().lower() not in _TRANS:
            warn(tr("invalid {0} '{1}': CNS ignores it, removed (with {2})").format(t, params[t], a))
            del params[t]
            params.pop(a, None)
        elif a in params and t not in params:
            warn(tr("{0} without {1}: CNS ignores it, removed").format(a, t))
            del params[a]


_PARAMS: dict | None = None


def ikemen_params() -> dict:
    """{controlador: {"params": [...], "open": bool}} do Ikemen (tools/build_ikemen_params.py)."""
    global _PARAMS
    if _PARAMS is None:
        p = Path(__file__).resolve().parents[1] / "data" / "ikemen_params.json"
        _PARAMS = json.loads(p.read_text("utf-8"))["sctrls"] if p.exists() else {}
    return _PARAMS


def param_known(info: dict, key: str) -> bool:
    """O controlador aceita a chave: está na lista, casa um formato livre (map.x,
    shader.param3, var(1)) ou o controlador aceita qualquer uma ("open")."""
    k = key.lower()
    return info["open"] or k in info["params"] or \
        any(re.fullmatch(p, k) for p in info.get("dynamic", ()))


def _drop_unknown(kind: str, params: dict[str, str], warn) -> None:
    """O CNS só avisa de parâmetro que o controlador não lê; o ZSS recusa: remove."""
    table = ikemen_params()
    if not table:
        return
    info = table.get(kind)
    if info is None:
        warn(tr("controller '{0}' does not exist in Ikemen").format(kind))
        return
    if info["open"]:
        return
    for k in list(params):
        if not param_known(info, k) and not _VAR_KEY.match(k):
            warn(tr("parameter '{0}' does not exist in {1} in Ikemen (CNS ignores it); removed").format(k, kind))
            del params[k]


@dataclass
class _Ctrl:
    name: str            # nome ZSS
    kind: str            # tipo em minúsculas
    params: list[tuple[str, str]]
    cond: str            # '' = sempre
    persistent: int      # 1 = padrão, 0 = uma vez
    ihp: bool
    label: str


@dataclass
class Result:
    text: str
    warnings: list[str] = field(default_factory=list)


def _ctrl_of(c: cns.Controller, state: int, warn) -> _Ctrl | None:
    where = tr("state {0}, [State {1}]").format(state, c.label)
    kind = c.type.lower().replace(" ", "")
    if not kind:
        warn(tr("{0}: controller without type, ignored").format(where))
        return None
    try:
        cond = condition(c.triggers())
    except ValueError as e:
        warn(tr("{0}: {1}; ignored").format(where, e))
        return None
    if cond is None:
        warn(tr("{0}: triggers never true; ignored (CNS discards it too)").format(where))
        return None
    seen: dict[str, str] = {}
    persistent, ihp = 1, False
    for e in c.section.entries():
        k = re.sub(r"\s+", "", e.key.lower())
        if k.startswith("trigger") and (k[7:].isdigit() or k[7:] == "all"):
            continue
        if k in seen:
            warn(tr("{0}: '{1}' repeated; the first one counts").format(where, k))
            continue
        seen[k] = e.value.strip()
    if "persistent" in seen and state >= 0:
        m = re.match(r"^[+-]?\d+", seen["persistent"])
        p = int(m[0]) if m else 0
        persistent = 1 if p > 128 else (0 if p <= 0 else p)
    if "ignorehitpause" in seen:
        m = re.match(r"^[+-]?\d+", seen["ignorehitpause"])
        ihp = bool(m and int(m[0]) != 0)
    pd = {k: v for k, v in seen.items()
          if (v != "" or (kind, k) in _EMPTY_OK)
          and not (k in _CNS_ONLY and not (k == "ignorehitpause" and kind in _IHP_PARAM))}
    _normalize(kind, pd, lambda m: warn(f"{where}: {m}"))
    _drop_unknown(kind, pd, lambda m: warn(f"{where}: {m}"))
    params: list[tuple[str, str]] = []
    for k, v in pd.items():
        m = _VAR_KEY.match(k)
        if m and kind in _VARSET:
            params += [(_VAR_PARAM[m[1].lower()], m[2].strip()), ("value", v)]
            continue
        if "(" in k:
            warn(tr("{0}: parameter '{1}' only exists in CNS; check it in the .zss").format(where, k))
        bad = [ch for i, ch in _scan_top(v) if ch in "{}#" or (ch == ":" and v[i + 1:i + 2] != "=")]
        if bad:
            warn(tr("{0}: the value of '{1}' has '{2}', which ZSS reads as syntax").format(where, k, bad[0]))
        params.append((k, v))
    label = "" if re.fullmatch(r"\s*\d*\s*", c.label) else c.label   # "[State 200, 1]" não diz nada
    return _Ctrl(sctrl_name(c.type), kind, params, cond, persistent, ihp, label)


def _call(ct: _Ctrl, indent: str) -> list[str]:
    body = [f"{k}: {v}" for k, v in ct.params]
    one = f"{ct.name}{{{'; '.join(body)}}}"
    if len(body) <= 3 and len(indent) + len(one) <= 96:
        return [indent + one]
    return [f"{indent}{ct.name}{{", *(f"{indent}\t{b};" for b in body), f"{indent}}}"]


def _attrs(ct: _Ctrl) -> str:
    a = []
    if ct.ihp:
        a.append("ignoreHitPause")
    if ct.persistent != 1:
        a.append(f"persistent({ct.persistent})")
    return " ".join(a)


def _runs(ctrls: list[_Ctrl]) -> list[list[_Ctrl]]:
    """Agrupa controladores seguidos com a mesma condição quando é seguro."""
    out: list[list[_Ctrl]] = []
    for ct in ctrls:
        if out:
            last = out[-1]
            head = last[0]
            if (ct.cond and ct.cond == head.cond and ct.persistent == head.persistent == 1
                    and ct.ihp == head.ihp and _SAFE_COND.match(ct.cond)
                    and all(x.kind in _PURE for x in last)):
                last.append(ct)
                continue
        out.append([ct])
    return out


def _header(sd: cns.StateDef, warn) -> str:
    if sd.number < 0:
        return f"[StateDef {sd.number}]"
    parts = []
    seen = set()
    for e in sd.section.entries():
        k = re.sub(r"\s+", "", e.key.lower())
        v = e.value.strip()
        if k in seen or v == "":
            continue
        seen.add(k)
        parts.append(f"{k}: {v};")
    if not parts:
        return f"[StateDef {sd.number}]"
    line = f"[StateDef {sd.number}; " + " ".join(parts) + "]"
    if len(line) <= 100:
        return line
    rows, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) > 76:
            rows.append(cur)
            cur = ""
        cur = f"{cur} {p}".strip()
    rows.append(cur)
    return f"[StateDef {sd.number};\n" + "\n".join(rows) + "]"


def statedef_to_zss(sd: cns.StateDef, warnings: list[str] | None = None,
                    labels: bool = True) -> str:
    warns = warnings if warnings is not None else []
    ctrls = [x for c in sd.controllers if (x := _ctrl_of(c, sd.number, warns.append))]
    lines = [_header(sd, warns.append), ""]
    for run in _runs(ctrls):
        head = run[0]
        attrs = _attrs(head)
        if not head.cond and not attrs:
            for ct in run:
                if labels and ct.label:
                    lines.append(f"# {ct.label}")
                lines += _call(ct, "")
            continue
        if len(run) == 1 and labels and head.label:
            lines.append(f"# {head.label}")
        opener = " ".join(x for x in (attrs, f"if {head.cond}" if head.cond else "") if x)
        lines.append(f"{opener} {{")
        for ct in run:
            if len(run) > 1 and labels and ct.label:
                lines.append(f"\t# {ct.label}")
            lines += _call(ct, "\t")
        lines.append("}")
    return "\n".join(lines).rstrip() + "\n"


def states_to_zss(statedefs: Iterable[cns.StateDef], title: str = "",
                  labels: bool = True) -> Result:
    warns: list[str] = []
    out = []
    if title:
        out.append(f"# {title}\n# ZSS: https://github.com/ikemen-engine/Ikemen-GO/wiki/ZSS\n")
    for sd in statedefs:
        out.append("#" + "=" * 79)
        out.append(statedef_to_zss(sd, warns, labels))
    return Result("\n".join(out), warns)


def cns_to_zss(text: str, title: str = "", labels: bool = True) -> Result:
    """Converte o texto de um arquivo CNS/ST inteiro (só os statedefs)."""
    st = cns.loads(text)
    return states_to_zss(st.statedefs.values(), title, labels)
