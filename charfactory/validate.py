"""Validação de personagem: sintaxe dos estados, referências cruzadas e completude."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from .formats import cmd as cmdfmt
from .formats import cns
from .project import Character, ProjectError
from .i18n import tr

GRAMMAR_PATH = Path(__file__).parent / "data" / "grammar.json"

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_STRING = re.compile(r'"[^"]*"')
_NUMBER_VALUE = re.compile(r"^\s*(-?\d+)\s*$")
_ARG_FUNCS = ("const", "gethitvar", "stagevar")  # argumentos são nomes, não triggers
_KEYWORDS = {"x", "y", "z", "single", "simul", "turns", "tag", "and", "or", "not",
             "intro", "invisible", "roundnotover", "noautoturn", "nojugglecheck", "noko",
             "nobardisplay", "nobg", "nofg", "nostandguard", "nocrouchguard", "noairguard",
             "noshadow", "globalnoshadow", "nomusic", "nowalk", "unguardable", "timerfreeze",
             "nokosnd", "nokoslow", "hitfall", "high", "low", "trip", "none", "light", "medium",
             "hard", "heavy", "back", "up", "diagup", "hit", "miss", "dodge"}


# Aliases antigos que o MUGEN aceita (e o próprio KFM usa), ausentes da documentação 1.0
_LEGACY_PARAMS = {"explod": {"velocity", "angle", "yangle", "xangle", "space"}, "playsnd": {"volume"}, "posfreeze": {"x", "y"},
                  "projectile": {"velocity"}}
_FREEFORM = {"null"}  # controllers que ignoram parâmetros
# obrigatórios que o MUGEN tolera na prática (ex.: HitOverride com time=0 para limpar o slot)
_TOLERATED_MISSING = {("hitoverride", "stateno")}
# obsoletos segundo a documentação da Elecbyte
DEPRECATED_CONTROLLERS = {"gamemakeanim": tr("use Explod"), "hitvelset": tr("use VelSet/HitDef"),
                          "makedust": tr("use Explod")}
DEPRECATED_PARAMS = {"explod": {"ontop": tr("use sprpriority"), "supermove": tr("use supermovetime")}}


@dataclass
class Issue:
    severity: str   # "error" | "warning"
    file: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.file} {self.where}: {self.message}"


@lru_cache(maxsize=1)
def grammar() -> dict:
    return json.loads(GRAMMAR_PATH.read_text("utf-8"))


def _known_identifier(tok: str) -> bool:
    g = grammar()
    t = tok.lower()
    if t in g["triggers"] or t in g["redirects"] or t in _KEYWORDS or t == "zsslocal":
        return True
    if re.match(r"^(projhit|projcontact|projguarded|projcanceltime|projhittime|projcontacttime|projguardedtime)\d*$", t):
        return True
    if re.match(r"^[a-z]{1,3}$", tok, re.I) and tok.isupper():  # flags: S, C, A, SCA, NA, AT…
        return True
    return False


# funções que existem em outras linguagens (e no simulador) mas não no MUGEN 1.0/1.1: o MUGEN
# recusa o CNS inteiro ("Invalid trigger: sqrt") e o char não abre
_NOT_MUGEN = {"sqrt": "(x) ** 0.5", "min": "ifelse(a < b, a, b)", "max": "ifelse(a > b, a, b)",
              "pow": "a ** b", "sign": "ifelse(x < 0, -1, ifelse(x > 0, 1, 0))",
              "clamp": tr("nested ifelse"), "round": "floor(x + 0.5)"}


def check_expression(expr: str) -> tuple[list[str], list[str]]:
    """(erros, avisos) de uma expressão de trigger."""
    errors, warnings = [], []
    if expr.count('"') % 2:
        errors.append(tr("unclosed quotes"))
    body = _STRING.sub('""', expr)
    depth = 0  # intervalos [a,b) (a,b] misturam colchete e parêntese: conta os dois juntos
    for ch in body:
        depth += ch in "(["
        depth -= ch in ")]"
        if depth < 0:
            errors.append(tr("closing without a matching opening"))
            break
    if depth > 0:
        errors.append(tr("opening without a matching closing"))
    # remove argumentos de const(...), gethitvar(...), stagevar(...)
    body = re.sub(r"\b(" + "|".join(_ARG_FUNCS) + r")\s*\([^()]*\)", "0", body, flags=re.I)
    for m in _IDENT.finditer(body):
        tok = m.group(0)
        if tok.lower() in _NOT_MUGEN and body[m.end():].lstrip().startswith("("):
            errors.append(tr("'{0}()' does not exist in MUGEN (the character won't load): use {1}").format(tok, _NOT_MUGEN[tok.lower()]))
            continue
        if re.match(r"^\d", tok) or _known_identifier(tok):
            continue
        prev = body[:m.start()].rstrip()
        if prev.endswith((",", "=")) and re.search(r"(animelem|hitdefattr|teammode|statetype|movetype|p2statetype|p2movetype)\s*[!<>=]*\s*[^,]*,?\s*$", prev, re.I):
            continue
        warnings.append(tr("unknown identifier '{0}'").format(tok))
    return errors, warnings


def _controller_issues(c: cns.Controller, fname: str, sd_num: int,
                       consts: set[str] | None = None) -> list[Issue]:
    g = grammar()
    out: list[Issue] = []
    where = f"[State {c.owner if c.owner is not None else '?'}, {c.label}]"

    def add(sev: str, msg: str) -> None:
        out.append(Issue(sev, fname, where, msg))

    if c.owner is not None and c.owner != sd_num:
        add("warning", tr("[State] number ({0}) differs from Statedef {1}").format(c.owner, sd_num))
    ctype = c.type
    if not ctype:
        add("error", tr("missing 'type'"))
        return out
    spec = g["controllers"].get(ctype.lower())
    if spec is None:
        add("error", tr("unknown controller type '{0}'").format(ctype))
        return out
    keys = {e.key.lower().replace(" ", "") for e in c.section.entries()}
    if ctype.lower() in DEPRECATED_CONTROLLERS:
        add("warning", tr("{0} is deprecated ({1})").format(spec['name'], DEPRECATED_CONTROLLERS[ctype.lower()]))
    for k, why in DEPRECATED_PARAMS.get(ctype.lower(), {}).items():
        if k in keys:
            add("warning", tr("{0}: '{1}' is deprecated ({2})").format(spec['name'], k, why))
    # zsslocal(n): local do ZSS no CNS equivalente que a montagem usa (formats/zssread.py)
    var_assign = any(re.match(r"^((f|sys|sysf)?var|zsslocal)\(", k) for k in keys)
    if spec.get("required_one_of_variants"):
        if not var_assign and not (("v" in keys or "fv" in keys) and ("value" in keys or "first" in keys)) \
                and not ("value" in keys or "fvalue" in keys):
            add("error", tr("{0}: set v/fv + value, or var(n) = value").format(spec['name']))
    else:
        for req in spec["required"]:
            if req not in keys:
                sev = "warning" if (ctype.lower(), req) in _TOLERATED_MISSING else "error"
                add(sev, tr("{0}: missing required parameter '{1}'").format(spec['name'], req))
        for group in spec.get("required_any", []):
            if not keys & set(group):
                add("error", tr("{0}: set one of these parameters: {1}").format(spec['name'], ', '.join(group)))
    allowed = set(spec["params"]) | set(g["state_common_params"]) | _LEGACY_PARAMS.get(ctype.lower(), set())
    for k in sorted(keys if ctype.lower() not in _FREEFORM else []):
        if k.startswith("trigger") or k in allowed or re.match(r"^(f|sys|sysf)?var\(", k):
            continue
        if (k.startswith("size.") and ctype.lower() == "helper") or \
                (any(k.startswith(p.split(".")[0] + ".") for p in allowed if "." in p) and
                 ctype.lower() in ("hitdef", "projectile", "reversaldef")):
            continue
        add("warning", tr("{0}: unknown parameter '{1}'").format(spec['name'], k))

    trig = c.triggers()
    nums = sorted(int(k) for k in trig if k != "triggerall")
    if not nums:
        add("error", tr("no trigger1 (the controller never runs)"))
    elif nums[0] != 1:
        add("error", tr("triggers start at trigger{0}; MUGEN requires trigger1").format(nums[0]))
    else:
        for a, b in zip(nums, nums[1:]):
            if b != a + 1:
                add("warning", tr("trigger{0} ignored: trigger{1} is missing").format(b, a + 1))
                break
    for key, exprs in trig.items():
        for e in exprs:
            errs, warns = check_expression(e)
            for msg in errs:
                add("error", tr("trigger{0}: {1} in '{2}'").format(key if key != 'triggerall' else 'all', msg, e))
            for msg in warns:
                add("warning", f"trigger{key if key != 'triggerall' else 'all'}: {msg}")
    from .formats import zssread
    if consts is None:
        consts = zssread._ikemen_consts()         # as do MUGEN estão todas nesta lista
    for e in c.section.entries():
        if not e.key.lower().startswith("trigger"):
            errs, _ = check_expression(e.value)
            for msg in errs:
                add("error", f"{e.key}: {msg}")
            # AnimElem só existe como gatilho ("animelem = n"); num parâmetro o MUGEN para com
            # "Died parsing ifelse(animelem = 6, ...)" e o char não abre
            if re.search(r"(?i)\banimelem\b", e.value) and not re.match(r"(?i)^\s*animelem\s*$", e.key):
                add("error", tr("{0}: animelem only works in triggers (the character won't load): use animelemno(0) = n or animelemtime(n) >= 0").format(e.key))
        # redirecionamento solto dentro de função: ifelse(root, stateno = 1, ...) — a vírgula do
        # redirecionamento vira separador de argumento; o certo é ifelse((root, stateno) = 1, ...)
        if re.search(r"(?i)\b\w+\(\s*(root|parent|enemynear|enemy|partner|target|helper\(\d+\)|playerid\(\d+\))\s*,\s*[a-z]",
                     _STRING.sub('""', e.value)):
            add("error", tr("{0}: loose redirection inside a function (the comma becomes an argument): wrap it in parentheses, e.g. ifelse((root, stateno) = 1100, a, b)").format(e.key))
        for name in re.findall(r"(?i)\bconst\s*\(\s*([\w.]+)\s*\)", e.value):
            if consts and name.lower() not in consts:
                add("error", tr("{0}: const({1}) does not exist (MUGEN rejects the character)").format(e.key, name))
        if e.key.lower().strip() == "attr" and ctype.lower() in ("hitdef", "reversaldef", "projectile"):
            msg = zssread.attr_problem(e.value)
            if msg:
                add("error", msg + tr(" (MUGEN silently accepts it and the move does not hit as it should)"))
    return out


def validate_zss(states, fname: str, user_consts: set[str] | None = None) -> list[Issue]:
    """Arquivo .zss: o que o Ikemen recusa (pela tabela de parâmetros e gatilhos dele), o que
    ele aceita calado mas está errado (const inexistente) e o que a simulação não reproduz.
    user_consts: chaves do [Constants] do personagem."""
    from .formats import zssread
    out = [Issue(sev, fname, tr("line {0}").format(ln), msg)
           for sev, ln, msg in zssread.check(states.file, user_consts)]
    out += [Issue("warning", fname, tr("simulation"), w) for w in states.warnings]
    return out


def validate_states(states: cns.States, fname: str,
                    user_consts: set[str] | None = None) -> list[Issue]:
    if hasattr(states, "statedef_zss"):
        return validate_zss(states, fname, user_consts)
    out = []
    for c in states.orphans:
        out.append(Issue("error", fname, f"[State {c.owner}, {c.label}]", tr("controller outside a [Statedef]")))
    from .formats import zssread
    consts = zssread._ikemen_consts()             # lido uma vez, não por controller
    for n, sd in states.statedefs.items():
        for c in sd.controllers:
            out += _controller_issues(c, fname, n, consts)
    return out


def _literal(v: str | None) -> int | None:
    m = _NUMBER_VALUE.match(v or "")
    return int(m[1]) if m else None


def required_anims(char: Character) -> set[int]:
    """Animações que os estados comuns usam por número literal."""
    need: set[int] = set()
    if not char.common_states:
        return need
    for sd in char.common_states.statedefs.values():
        a = _literal(sd.section.get("anim"))
        if a is not None:
            need.add(a)
        for c in sd.controllers:
            if any("animexist" in e.lower() for ex in c.triggers().values() for e in ex):
                continue  # o próprio estado comum verifica se a animação existe
            if c.type.lower() == "changeanim":
                a = _literal(c.section.get("value"))
                if a is not None:
                    need.add(a)
    return need


def validate_character(char: Character) -> list[Issue]:
    issues: list[Issue] = []

    def add(sev, file, where, msg):
        issues.append(Issue(sev, file, where, msg))

    for key in ["cmd", "cns", "st", "stcommon", "sprite", "anim", "sound"]:
        entry = char.file_entry(key)
        if key in ("cmd", "cns", "sprite", "anim") and not entry:
            add("error", char.def_path.name, "[Files]", tr("missing entry '{0}'").format(key))
        elif entry and not char.path_of(key).exists():
            if key == "stcommon" and Path(entry).name.lower() in ("common1.cns", "common1.cns.zss", "common1.zss"):
                add("warning", char.def_path.name, "[Files]",
                    tr("{0} = {1}: file not found in the folder (the engine uses the default from data/)").format(key, entry))
            else:
                add("error", char.def_path.name, "[Files]", tr("{0} = {1}: file not found").format(key, entry))
    if any(i.severity == "error" for i in issues):
        return issues

    sff_name = char.path_of("sprite").name
    air_name = char.path_of("anim").name
    sprites = {(s.group, s.item) for s in char.sprites.sprites}
    if len(sprites) < len(char.sprites.sprites):
        seen, dup = set(), []
        for s in char.sprites.sprites:
            if (s.group, s.item) in seen and len(dup) < 8:
                dup.append(f"{s.group},{s.item}")
            seen.add((s.group, s.item))
        add("warning", sff_name, "", tr("{0} sprite(s) with a repeated number (MUGEN uses the first): {1}").format(len(char.sprites.sprites) - len(sprites), ', '.join(dup)))
    anims = char.anims

    # AIR → SFF
    for n, act in anims.actions.items():
        if not act.frames:
            add("error", air_name, f"[Begin Action {n}]", tr("action without frames"))
        for i, f in enumerate(act.frames):
            if f.group >= 0 and (f.group, f.item) not in sprites:
                add("warning", air_name, f"[Begin Action {n}] frame {i}",
                    tr("sprite {0},{1} does not exist in {2}").format(f.group, f.item, sff_name))
    for n in sorted(required_anims(char) - set(anims.actions)):
        add("warning", air_name, "", tr("animation {0} is missing; the common states use it").format(n))

    # estados
    all_sd = char.all_statedefs()
    snd_ids = {(s.group, s.number) for s in char.sounds.sounds}
    command_names = {c.name.lower() for c in char.commands.commands()}
    files = [(st, st.path.name) for st in char.states] + [(char.commands.states, char.commands.path.name)]
    user_consts: set[str] = set()                 # [Constants] do personagem (Ikemen)
    try:
        for sec in char.constants.sections:
            if sec.name.lower() == "constants":
                user_consts |= {k.lower() for k in sec.as_dict()}
    except ProjectError:
        pass
    for st, fname in files:
        issues += validate_states(st, fname, user_consts)
        for n, sd in st.statedefs.items():
            a = _literal(sd.section.get("anim"))
            if a is not None and a not in anims.actions:
                add("warning", fname, f"[Statedef {n}]", tr("anim = {0} does not exist in the AIR").format(a))
            for c in sd.controllers:
                where = f"[State {n}, {c.label}]"
                t = c.type.lower()
                v = c.section.get("value")
                if t in ("changestate", "selfstate"):
                    target = _literal(v)
                    if target is not None and target not in all_sd:
                        add("warning", fname, where, tr("{0} to state {1}, which does not exist").format(c.type, target))
                guarded = any("animexist" in e.lower() for ex in c.triggers().values() for e in ex)
                if t == "changeanim" and not guarded:
                    target = _literal(v)
                    if target is not None and target not in anims.actions and not (5000 <= target < 6000):
                        add("warning", fname, where, tr("ChangeAnim to action {0}, which does not exist").format(target))
                if t == "playsnd" and v:
                    m = re.match(r"^\s*(F?)\s*(\d+)\s*,\s*(\d+)", v, re.I)
                    if m and not m[1] and (int(m[2]), int(m[3])) not in snd_ids:
                        add("warning", fname, where, tr("sound {0},{1} does not exist in the SND").format(m[2], m[3]))
                for exprs in c.triggers().values():
                    for e in exprs:
                        for name in re.findall(r'command\s*!?=\s*"([^"]+)"', e, re.I):
                            if name.lower() not in command_names:
                                add("error", fname, where, tr("command \"{0}\" is not defined in the CMD").format(name))

    # sequências de comando
    for c in char.commands.commands():
        for msg in cmdfmt.check_sequence(c.sequence):
            add("error", char.commands.path.name, f'[Command] "{c.name}"', msg)

    # Análise semântica: detecção de loops infinitos de ChangeState / SelfState no tick 0
    imm_transitions: dict[int, tuple[int, str, str]] = {}
    for st, fname in files:
        for n, sd in st.statedefs.items():
            for c in sd.controllers:
                t = c.type.lower()
                if t in ("changestate", "selfstate"):
                    trigs = c.triggers()
                    is_immediate = False
                    if "1" in trigs and len(trigs["1"]) == 1:
                        t1 = trigs["1"][0].strip()
                        if t1 in ("1", "!time", "time = 0", "time <= 0") and "triggerall" not in trigs:
                            is_immediate = True
                    if is_immediate:
                        val = _literal(c.section.get("value"))
                        if val is not None:
                            where = f"[State {n}, {c.label}]"
                            imm_transitions[n] = (val, fname, where)
                            if val == n:
                                add("error", fname, where,
                                    tr("Fatal infinite loop: unconditional ChangeState to its own state {0} on tick 0 (freezes the MUGEN engine)").format(n))
                            break

    reported_cycles = set()
    for start_state, (target, fname, where) in list(imm_transitions.items()):
        visited = [start_state]
        curr = target
        while curr in imm_transitions:
            if curr in visited:
                idx = visited.index(curr)
                cycle = tuple(visited[idx:])
                if cycle not in reported_cycles:
                    reported_cycles.add(cycle)
                    cycle_str = " -> ".join(str(s) for s in list(cycle) + [curr])
                    add("error", fname, where,
                        tr("Fatal infinite loop: unconditional ChangeState cycle ({0}) on tick 0 (freezes the MUGEN engine)").format(cycle_str))
                break
            visited.append(curr)
            curr = imm_transitions[curr][0]

    return issues


def deprecated_lines(text: str) -> list[int]:
    """Linhas (1-n) com controller ou parâmetro obsoleto num texto CNS/CMD."""
    out, ctype = [], None
    for n, line in enumerate(text.splitlines(), 1):
        code = line.split(";", 1)[0].strip()
        if code.startswith("["):
            ctype = None
            continue
        m = re.match(r"^([A-Za-z_.0-9 ]+?)\s*=\s*(.*)$", code)
        if not m:
            continue
        key, val = m[1].strip().lower(), m[2].strip().lower()
        if key == "type":
            ctype = val
            if val in DEPRECATED_CONTROLLERS:
                out.append(n)
        elif ctype and key in DEPRECATED_PARAMS.get(ctype, {}):
            out.append(n)
    return out


def report(issues: list[Issue]) -> dict:
    return {"errors": sum(i.severity == "error" for i in issues),
            "warnings": sum(i.severity == "warning" for i in issues),
            "issues": [asdict(i) for i in issues]}


def validate_path(def_path: str | Path) -> dict:
    try:
        return report(validate_character(Character(def_path)))
    except ProjectError as e:
        return report([Issue("error", str(def_path), "", str(e))])
