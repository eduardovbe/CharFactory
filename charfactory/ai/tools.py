"""Ferramentas que um agente de IA usa no personagem: o mesmo registro serve o servidor MCP e
o assistente embutido.

Cada ferramenta recebe o Character aberto e devolve um Result (texto e, se for o caso, imagens
PNG). As de escrita só mexem no modelo em memória, como as de tools.ops: gravar no disco é a
ferramenta save_project. Quem roda a ferramenta é um Host: o do CLI chama direto; o da janela
roda na thread da interface e registra cada mudança no desfazer.

As descrições ficam em inglês: são lidas pelo modelo, não pelo usuário.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import project, validate
from ..tools import combo, ops

MAX_IMAGE_SIDE = 1568          # acima disso os provedores reduzem a imagem de qualquer jeito
MAX_TEXT = 60_000              # resposta de ferramenta maior que isso é cortada (contexto do modelo)

# o caminho para um golpe novo, nas instruções do MCP e do assistente. Curto e direto: modelos
# rápidos seguem cada passo ao pé da letra, e explorar demais (ler golpes, olhar dezenas de
# animações) gasta a cota antes de escrever qualquer coisa
MOVE_RECIPE = (
    "To make a new move, from one hit to a long combo, use build_move: you pick the poses and what "
    "each step does, and it writes the statedefs, [Command] and [Statedef -1] entry and tunes the "
    "physics in the simulator. 1) Map the request onto roles: ground punches and kicks = strike, "
    "'throws the enemy up' = launcher, an aerial attack = air_strike or air_finisher (the jump is "
    "added for you), a knockdown = finisher, closing in = dash. 2) Call list_animations once (with "
    "`search` on a big character) and pick each pose by its name; only when the names do not say "
    "enough, look at the few candidates in ONE view_animations call. 3) Call build_move. 4) Read its "
    "verdict: if a step MISSES, follow its hint and call build_move again with the same state; as "
    "soon as every hit connects, stop and answer. For a new move do not read the character's other "
    "states or animations, and do not run simulate_state or validate_character after build_move "
    "(its answer has both). Only for what build_move cannot do (projectiles, throws, helpers, custom "
    "effects) write states by hand, the way the character's other moves do (mugen_guide topic "
    "'style': helpers instead of the Projectile controller, the character's own sparks and sounds): "
    "create_move gives a starting point, set_state(replace=true) the rest, mugen_guide explains "
    "each part and simulate_state checks it; never tell the user a move "
    "works while the simulation says it misses. To change a move: one made by build_move (its "
    "statedef has a '; build_move:' comment) is changed by calling build_move again with the same "
    "state; for others, get_move shows the statedef, animations, input and verdict in one call, "
    "edit_state fixes parameters and edit_animation frames, timing or boxes. A new move gets new "
    "state and animation numbers: never overwrite another move unless the user asks to change that "
    "one. The game only sees saved files: tell the user to save before testing in MUGEN, and if "
    "they say the game still behaves the old way, check unsaved_changes in character_info first.")


class ToolError(Exception):
    """Erro que vai para o modelo como resultado da ferramenta (ele corrige e tenta de novo)."""


@dataclass
class Result:
    text: str
    images: list[bytes] = field(default_factory=list)     # PNG
    is_error: bool = False


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    fn: Callable
    required: tuple = ()
    writes: bool = False
    needs_char: bool = True

    def schema(self) -> dict:
        return {"type": "object", "properties": self.params, "required": list(self.required)}


REGISTRY: dict[str, Tool] = {}


def tool(description: str, params: dict | None = None, required: tuple = (), writes: bool = False,
         needs_char: bool = True):
    def deco(fn):
        REGISTRY[fn.__name__] = Tool(fn.__name__, description, params or {}, fn, required, writes, needs_char)
        return fn
    return deco


def _int(desc: str) -> dict:
    return {"type": "integer", "description": desc}


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _bool(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


def _range(spec: str | None) -> tuple[int, int] | None:
    """'200-699' ou '1000' -> (menor, maior); vazio = tudo."""
    if spec is None or not str(spec).strip():
        return None
    m = re.fullmatch(r"\s*(-?\d+)\s*(?:(?:-|\.\.|to)\s*(-?\d+))?\s*", str(spec))
    if not m:
        raise ToolError(f"range '{spec}' not understood: use '200-699' or one number")
    lo = int(m[1])
    return lo, int(m[2]) if m[2] else lo


def _png(img) -> bytes:
    if max(img.size) > MAX_IMAGE_SIDE:
        k = MAX_IMAGE_SIDE / max(img.size)
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ======================================================================= host

class Host:
    """Onde as ferramentas rodam. O padrão (CLI e testes) guarda um personagem carregado."""

    def __init__(self, char: project.Character | None = None):
        self.char = char

    def character(self) -> project.Character | None:
        return self.char

    def open_character(self, def_path: str) -> project.Character:
        self.char = project.load(def_path)
        return self.char

    def save(self) -> list[str]:
        return self.char.save()

    def call(self, name: str, args: dict | None) -> Result:
        return run(self, name, args)


def run(host: Host, name: str, args: dict | None) -> Result:
    """Roda uma ferramenta e transforma qualquer erro em resultado de erro para o modelo."""
    t = REGISTRY.get(name)
    if t is None:
        return Result(f"unknown tool: {name}", is_error=True)
    args = dict(args or {})
    missing = [k for k in t.required if k not in args]
    if missing:
        return Result(f"missing argument(s): {', '.join(missing)}", is_error=True)
    unknown = [k for k in args if k not in t.params]
    if unknown:
        return Result(f"unknown argument(s): {', '.join(unknown)}; accepted: {', '.join(t.params) or 'none'}",
                      is_error=True)
    try:
        if t.needs_char:
            char = host.character()
            if char is None:
                raise ToolError("no character is open: call open_character with the path of a .def first")
            out = t.fn(host, char, **args)
        else:
            out = t.fn(host, **args)
    except (ToolError, project.ProjectError, ValueError, KeyError, IndexError, TypeError, OSError) as e:
        return Result(f"{type(e).__name__}: {e}" if not isinstance(e, ToolError) else str(e), is_error=True)
    except Exception as e:  # noqa: BLE001  (falha interna: o servidor MCP e o loop não podem cair)
        return Result(f"internal error in {name}: {type(e).__name__}: {e}", is_error=True)
    if isinstance(out, Result):
        return out
    text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n… (cut: {len(text) - MAX_TEXT} more characters; ask for less, e.g. one group)"
    return Result(text)


def describe() -> list[Tool]:
    return list(REGISTRY.values())


# ======================================================================= personagem

@tool("Open a character from its .def file and make it the active one. Every other tool works on "
      "the active character.", {"def_path": _str("Absolute path of the character's .def file")},
      ("def_path",), needs_char=False)
def open_character(host, def_path: str):
    if not Path(def_path).is_file():
        raise ToolError(f"file not found: {def_path}")
    c = host.open_character(def_path)
    return c.summary()


@tool("Summary of the active character: name, files, sprite/animation/sound counts and the states "
      "defined in its own files. Call this first.")
def character_info(host, char):
    s = char.summary()
    s["unsaved_changes"] = sorted(char.dirty)
    return s


@tool("Check the character for errors and warnings (missing animations, sprites, sounds, bad "
      "triggers and parameters), like the editor's Problems panel, plus [Statedef -1] entries whose "
      "input an earlier entry takes first (the move never comes out). Repeated problems come grouped "
      "with a count and a few places. Pass `number` after editing a move to see only its issues, "
      "warnings included.",
      {"warnings": _bool("Also list warnings (default: errors only, plus the warning count)"),
       "number": _int("Only issues of this statedef, of its animation and of its [Statedef -1] entry")})
def validate_character(host, char, warnings: bool = False, number: int | None = None):
    r = validate.report(validate.validate_character(char))
    issues = r["issues"]
    if number is not None:
        sd = char.all_statedefs().get(number)
        anim = (sd[0].params().get("anim") or "").strip() if sd and hasattr(sd[0], "params") else ""
        marks = (f"[State {number},", f"[Statedef {number}]", f"[Begin Action {anim or number}]")
        issues = [i for i in issues if i["where"].startswith(marks)]
        warnings = True
    out = {"errors": sum(i["severity"] == "error" for i in issues),
           "warnings": sum(i["severity"] == "warning" for i in issues)}
    shown = issues if warnings else [i for i in issues if i["severity"] == "error"]
    out["issues"] = shown[:300] if number is not None else _grouped(shown)
    cmd = _command_problems(char, number)
    if cmd:
        out["command_problems"] = cmd
    return out


def _grouped(issues: list[dict], limit: int = 120) -> list[dict]:
    """Problemas iguais a menos dos números viram um item com a contagem e alguns lugares:
    um char grande tem centenas de 'sprite N,N does not exist' que diriam a mesma coisa."""
    groups: dict[tuple, dict] = {}
    for i in issues:
        key = (i["severity"], i.get("file", ""), re.sub(r"-?\d+", "N", i["message"]))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {k: v for k, v in i.items() if v not in ("", None)}
            g["where"] = [i["where"]] if i.get("where") else []
            g["count"] = 0
        g["count"] += 1
        if g["count"] > 1 and len(g["where"]) < 4 and i.get("where"):
            g["where"].append(i["where"])
    out = sorted(groups.values(), key=lambda g: (g["severity"] != "error", -g["count"]))
    for g in out:
        if g["count"] == 1:
            g.pop("count")
            g["where"] = g["where"][0] if g["where"] else ""
        else:
            g["message"] = "e.g. " + g["message"]
        if not g["where"]:
            g.pop("where")
    return out[:limit]


@tool("Write every change made so far to the character's files on disk (a .bak backup of each file "
      "is kept). Only do this when the user asks to save.", writes=True)
def save_project(host, char):
    written = host.save()
    return {"written": written} if written else "nothing to save"


@tool("Sprites, animations and sounds that nothing references (candidates for deletion).")
def unused_resources(host, char):
    from ..tools import usage
    return usage.unused_resources(char)


# ======================================================================= contexto

# ações com número fixo no MUGEN (o common1.cns e o motor usam estes números)
STANDARD_ACTIONS = {
    0: "stand", 5: "turn", 6: "crouch turn", 10: "stand to crouch", 11: "crouching", 12: "crouch to stand",
    20: "walk forward", 21: "walk back", 40: "jump start", 41: "jump up (neutral)", 42: "jump up (forward)",
    43: "jump up (back)", 44: "jump down (neutral)", 45: "jump down (forward)", 46: "jump down (back)",
    47: "jump landing", 100: "run forward", 105: "hop back", 120: "guard start (stand)",
    121: "guard start (crouch)", 122: "guard start (air)", 130: "guarding (stand)", 131: "guarding (crouch)",
    132: "guarding (air)", 140: "guard end (stand)", 141: "guard end (crouch)", 142: "guard end (air)",
    150: "guard hit (stand)", 151: "guard hit (crouch)", 152: "guard hit (air)", 170: "lose", 175: "time over",
    180: "win", 181: "win", 190: "intro", 195: "taunt", 5000: "get hit (stand, high)", 5010: "get hit (stand, low)",
    5020: "get hit (crouch)", 5030: "get hit (air)", 5040: "get hit (air, recovering)", 5050: "falling",
    5080: "lying down", 5100: "hit the ground", 5110: "lying on the ground", 5120: "getting up",
    5160: "hit the ground (bounce)", 5170: "lying (bounce)", 5210: "air recovery", 5300: "dizzy"}


def _entries_by_state(char) -> dict[int, list[str]]:
    """Estado -> entradas do [Statedef -1] que levam a ele, com o comando: 'Soco forte (y)'."""
    from ..tools import cmdtest
    out: dict[int, list[str]] = {}
    for k, ctl in enumerate(_minus1_entries(char), 1):
        if not hasattr(ctl, "triggers") or ctl.type.lower() != "changestate":
            continue
        n = _entry_state(ctl)
        names = cmdtest.entry_commands(ctl)
        seqs = [c.sequence for nm in names[:1] for c in char.commands.find(nm)[:1]]
        if n is not None:
            out.setdefault(n, []).append(f"{ctl.label or f'entry {k}'} ({seqs[0] if seqs else ', '.join(names) or '-'})")
    return out


def _anim_users(char) -> dict[int, list[int]]:
    """Ação -> statedefs do personagem que a usam (anim = N no cabeçalho ou ChangeAnim)."""
    out: dict[int, list[int]] = {}
    for n, (sd, _) in char.all_statedefs(include_common=False).items():
        for a in _state_anims(sd):
            if n not in out.setdefault(a, []):
                out[a].append(n)
    return out


def _anim_name(char, number: int) -> str:
    try:
        name = char.anims.name_of(number)
    except (StopIteration, KeyError, AttributeError):
        name = ""
    return name or STANDARD_ACTIONS.get(number, "")


def _free_number(char, start: int) -> int:
    """Primeiro número livre (de estado e de animação) a partir de `start`, de 10 em 10."""
    used = set(char.all_statedefs()) | set(char.anims.numbers())
    n = start - start % 10
    while n in used:
        n += 10
    return n


# ======================================================================= estados

@tool("List the statedefs, one line each, grouped by file: number, type/movetype/physics, anim with "
      "its name, and the [Statedef -1] entries that lead to it with their input (what move it is). "
      "Filter with `numbers` ('200-699') or `search` on a big character.",
      {"numbers": _str("Only this range, e.g. '200-699' or '1000'"),
       "search": _str("Only states whose animation name, entry or input contains this text"),
       "include_common": _bool("Also list the states of common1.cns that the character does not override")})
def list_states(host, char, numbers: str | None = None, search: str | None = None, include_common: bool = False):
    rng, entries = _range(numbers), _entries_by_state(char)
    want = (search or "").strip().lower()
    lines, last_file = [], None
    for d in ops.list_statedefs(char, include_common):
        n = d["statedef"]
        if rng and not rng[0] <= n <= rng[1]:
            continue
        anim = (d.get("anim") or "").split(";")[0].strip()
        name = _anim_name(char, int(anim)) if anim.isdigit() else ""
        how = entries.get(n, [])
        row = f"{n} {d.get('type') or 'S'}/{d.get('movetype') or 'I'}/{d.get('physics') or 'N'}"
        row += f" anim {anim[:24]}" if anim else ""
        row += f' "{name}"' if name else ""
        row += f" <- {'; '.join(how[:3])}" if how else ""
        if want and want not in row.lower():
            continue
        if d.get("file") != last_file:
            last_file = d.get("file")
            lines.append(f"# {last_file}")
        lines.append(row)
    if not lines:
        return "no statedef matches"
    return "type/movetype/physics; <- = [Statedef -1] entry (input) that leads to it\n" + "\n".join(lines)


@tool("Full text of a statedef with all its state controllers, as written in the file (CNS or ZSS).",
      {"number": _int("Statedef number, e.g. 200"),
       "as_cns": _bool("For an Ikemen ZSS state, return the equivalent CNS instead")}, ("number",))
def get_state(host, char, number: int, as_cns: bool = False):
    return ops.get_statedef_text(char, number, lowered=as_cns)


def _minus1_entries(char) -> list:
    st = char.states_file_for(-1)
    sd = st.statedefs.get(-1) if st is not None else None
    return sd.controllers if sd else []


def _entry_state(ctl) -> int | None:
    m = re.match(r"^\s*(-?\d+)", ctl.section.get("value") or "")
    return int(m[1]) if m else None


def _command_note(r: dict) -> str | None:
    """Frase para o modelo a partir de cmdtest.check_entry/place_entry (None se está tudo certo)."""
    if "unknown" in r:
        return r["unknown"]
    if "moved_before" in r:
        m = r["moved_before"]
        return (f"moved before [State -1, {m['label']}] (state {m['state']}), which took the same input "
                f"first; the command now reaches this entry")
    if "taken_by" in r:
        t = r["taken_by"]
        if r.get("same_input"):
            return (f"never runs: [State -1, {t['label']}] (state {t['state']}) comes first and uses the "
                    f"same input; use another command, or change or remove that entry")
        return (f"never runs: [State -1, {t['label']}] (state {t['state']}) comes first and takes the "
                f"input; move this entry before it")
    if r.get("never_fires"):
        return ("did not fire in a test with its command from standing, crouching or jumping with "
                "control (opponent at 50 and 1 px): check its triggers")
    return None


def _command_problems(char, number: int | None = None) -> list[dict]:
    from ..tools import cmdtest
    out = []
    for i, ctl in enumerate(_minus1_entries(char)):
        if not hasattr(ctl, "triggers") or ctl.type.lower() != "changestate" or not cmdtest.entry_commands(ctl):
            continue
        if number is not None and _entry_state(ctl) != number:
            continue
        r = cmdtest.check_entry(char, i)
        if "taken_by" in r or "unknown" in r:
            out.append({"entry": f"[State -1, {ctl.label}]", "state": _entry_state(ctl), "problem": _command_note(r)})
    return out


@tool("Create or replace a whole statedef. `text` is the complete block in CNS syntax: the "
      "[Statedef N] header with its parameters followed by every [State N, label] controller. "
      "Replacing keeps the statedef's position in its file; a new one goes to the main states file "
      "(Statedef -1, -2 and -3 go where they already are). An existing statedef is only replaced with "
      "replace=true: for a NEW move use a free number, never an existing move's. Validate afterwards.",
      {"number": _int("Statedef number"), "text": _str("Complete CNS text of the statedef"),
       "replace": _bool("Confirm replacing a statedef the character already has (default false)")},
      ("number", "text"), writes=True)
def set_state(host, char, number: int, text: str, replace: bool = False):
    if f"statedef {number}" not in text.lower().replace("  ", " "):
        raise ToolError(f"the text must start with [Statedef {number}]")
    if number >= 0 and not replace and number in char.all_statedefs(include_common=False):
        what = _entries_by_state(char).get(number)
        sd = char.all_statedefs(include_common=False)[number][0]
        anim = (sd.params().get("anim") or "").strip() if hasattr(sd, "params") else ""
        name = _anim_name(char, int(anim)) if anim.isdigit() else ""
        desc = f" ('{name}')" if name else ""
        used = f", reached by {'; '.join(what)}" if what else ""
        raise ToolError(f"nothing changed: statedef {number} already exists{desc}{used}. To change that move "
                        f"pass replace=true; for a new move use a free number such as "
                        f"{_free_number(char, 1000 if number < 1000 else number)}")
    out = ops.set_statedef_text(char, number, text)
    if number == -1:
        cmd = _command_problems(char)
        if cmd:
            out["command_problems"] = cmd
    check = _verdict(char, number)
    if check:
        out["simulation"] = check
    return out


@tool("Add one state controller to a statedef: at the end, or before another controller. In "
      "[Statedef -1] the order is the command priority (the first ChangeState whose triggers pass "
      "wins), so a special must come before the normal attack that uses its button: a new ChangeState "
      "there is tested and, if an earlier entry would take its input, moved right before that entry.",
      {"number": _int("Statedef number"),
       "text": _str("Controller in CNS syntax: '[State N, label]' then type = ..., trigger1 = ... and its parameters"),
       "before": _str("Label of a controller of this statedef to insert before, or 'start' (default: the end)")},
      ("number", "text"), writes=True)
def add_controller(host, char, number: int, text: str, before: str | None = None):
    from ..tools import cmdtest
    st = char.states_file_for(number)
    at = None
    if before and number in st.statedefs:
        ctls = st.statedefs[number].controllers
        want = before.strip().strip("[]").lower()
        if want == "start":
            at = 0
        else:
            at = next((k for k, c in enumerate(ctls) if c.label.lower() == want
                       or f"state {c.owner}, {c.label}".lower() == want), None)
            if at is None:
                raise ToolError(f"no controller labelled '{before}' in statedef {number}; labels: "
                                + ", ".join(c.label for c in ctls))
    if number in st.statedefs:
        def norm(t):
            return [ln.split(";")[0].strip().lower().replace(" ", "") for ln in t.splitlines()
                    if ln.split(";")[0].strip()]
        same = [c.label for c in st.statedefs[number].controllers if hasattr(c, "section")
                and norm(c.section.text()) == norm(text)]
        if same:
            raise ToolError(f"nothing changed: statedef {number} already has this exact controller "
                            f"([State {number}, {same[0]}])")
    out = ops.add_controller(char, number, text)
    index = len(st.statedefs[number].controllers) - 1
    if at is not None:
        order = list(range(index))
        order.insert(at, index)
        st.reorder_controllers(number, order)
        index = at
    if number == -1 and out["type"].lower() == "changestate":
        r = cmdtest.check_entry(char, index) if before else cmdtest.place_entry(char, index)
        note = _command_note(r)
        if note:
            out["command_check"] = note
    check = _verdict(char, number)
    if check:
        out["simulation"] = check
    return out


@tool("Delete a statedef from the character's files, and the [Statedef -1] entries that change to it "
      "(otherwise they would send the command to a state that no longer exists).",
      {"number": _int("Statedef number"),
       "keep_entries": _bool("Keep the [Statedef -1] entries that change to it (default false)")},
      ("number",), writes=True)
def remove_state(host, char, number: int, keep_entries: bool = False):
    if number == -1 or not ops.remove_statedef(char, number):
        raise ToolError(f"statedef {number} does not exist in the character's files")
    out = {"removed": number}
    st = char.states_file_for(-1)
    if not keep_entries and hasattr(st, "remove_controller"):
        gone = []
        for k in reversed(range(len(_minus1_entries(char)))):
            ctl = _minus1_entries(char)[k]
            if ctl.type.lower() == "changestate" and _entry_state(ctl) == number:
                gone.append(f"[State -1, {st.remove_controller(-1, k).label}]")
        if gone:
            char.mark("commands" if st is char.commands.states else "states")
            out["removed_entries"] = gone[::-1]
    return out


@tool("Delete a state controller from a statedef, by its label (e.g. an entry of [Statedef -1]). "
      "When several have that label, pass all=true to delete every one, or occurrence to pick one.",
      {"number": _int("Statedef number"), "label": _str("Label of the controller, as in [State N, label]"),
       "all": _bool("Delete every controller with this label (default false)"),
       "occurrence": _int("Which one when several share the label: 1 = first in the file")},
      ("number", "label"), writes=True)
def remove_controller(host, char, number: int, label: str, all: bool = False, occurrence: int | None = None):
    st, hits = _labelled(char, number, label)
    if occurrence is not None:
        if not 1 <= occurrence <= len(hits):
            raise ToolError(f"occurrence must be 1 to {len(hits)}")
        hits = [hits[occurrence - 1]]
    elif len(hits) > 1 and not all:
        raise ToolError(f"{len(hits)} controllers are labelled '{label}' in statedef {number}: pass all=true "
                        f"to delete them all, or occurrence=1..{len(hits)}")
    for k in reversed(hits):
        st.remove_controller(number, k)
    char.mark("commands" if st is char.commands.states else "states")
    return {"statedef": number, "removed": len(hits), "label": label}


def _labelled(char, number: int, label: str):
    """(arquivo de estados, posições dos controladores com esse rótulo) num statedef CNS."""
    st = char.states_file_for(number)
    if st is None or number not in st.statedefs or not hasattr(st, "remove_controller"):
        raise ToolError(f"statedef {number} does not exist in the character's CNS files")
    ctls = st.statedefs[number].controllers
    want = label.strip().strip("[]").lower()
    hits = [k for k, c in enumerate(ctls) if c.label.lower() == want or f"state {c.owner}, {c.label}".lower() == want]
    if not hits:
        raise ToolError(f"no controller labelled '{label}' in statedef {number}; labels: "
                        + ", ".join(c.label for c in ctls))
    return st, hits


def _body_end(sec) -> int:
    """Linha logo depois do último parâmetro da seção (o que vem depois é comentário ou vazio)."""
    from ..formats import mtext
    last = 0
    for i, line in enumerate(sec.lines[1:], 1):
        if mtext.strip_comment(line).strip():
            last = i
    return last + 1


def _state_anims(sd) -> list[int]:
    """Ações que um statedef mostra: anim = N do cabeçalho e os ChangeAnim com número fixo."""
    out: list[int] = []
    if not hasattr(sd, "params"):
        return out
    vals = [sd.params().get("anim", "")]
    vals += [c.section.get("value") or "" for c in sd.controllers if c.type.lower() in ("changeanim", "changeanim2")]
    for v in vals:
        m = re.match(r"^\s*(\d+)\s*(?:;.*)?$", v or "")
        if m and int(m[1]) not in out:
            out.append(int(m[1]))
    return out


@tool("Everything about one move in one call: the statedef text, the AIR text of the animations it "
      "shows, the [Statedef -1] entries that lead to it with their commands, the other states that change "
      "to it (combos, phases of a move) and, for an attack, a quick simulation verdict. Start here "
      "before changing a move.",
      {"number": _int("Statedef number of the move")}, ("number",))
def get_move(host, char, number: int):
    sd = char.all_statedefs().get(number)
    if not sd:
        raise ToolError(f"statedef {number} does not exist (list_states shows them)")
    out: dict = {"statedef": ops.get_statedef_text(char, number)}
    anims, missing = {}, []
    for n in _state_anims(sd[0])[:4]:
        if char.anims.get(n) is None:
            missing.append(n)
        else:
            anims[str(n)] = (f"; {_anim_name(char, n)}\n" if _anim_name(char, n) else "") + ops.get_action_text(char, n)
    out["animations"] = anims
    if missing:
        out["missing_animations"] = missing
    entries = []
    for ctl in _minus1_entries(char):
        if hasattr(ctl, "triggers") and ctl.type.lower() == "changestate" and _entry_state(ctl) == number:
            from ..tools import cmdtest
            cmds = [{"name": nm, "sequence": c.sequence} for nm in cmdtest.entry_commands(ctl)
                    for c in char.commands.find(nm)[:1]]
            entries.append({"entry": ctl.section.text().strip(), "commands": cmds})
    out["entries"] = entries or "no [Statedef -1] entry leads here"
    chains = []                       # outros estados que mudam para este (combos, fases do golpe)
    for n, (other, _) in sorted(char.all_statedefs(include_common=False).items()):
        if n == -1 or not hasattr(other, "controllers"):
            continue
        for ctl in other.controllers:
            if ctl.type.lower() in ("changestate", "selfstate") and _entry_state(ctl) == number:
                from ..tools import cmdtest
                cmds = cmdtest.entry_commands(ctl) if hasattr(ctl, "triggers") else []
                chains.append(f"statedef {n} [State {ctl.owner}, {ctl.label}]"
                              + (f" on command {', '.join(cmds)}" if cmds else ""))
    if chains:
        out["reached_from"] = chains[:12] + ([f"… {len(chains) - 12} more"] if len(chains) > 12 else [])
    check = _verdict(char, number)
    if check:
        out["simulation"] = check
    return out


@tool("Change parameters of one part of a statedef in place, without rewriting it: the [Statedef N] "
      "header (target 'header') or one controller by its label. `set` adds or replaces parameters and "
      "keeps everything else (comments included); `remove` deletes every line with that key and runs "
      "first, so remove 'trigger1' and set it again to replace a trigger that has several lines. Much "
      "cheaper and safer than set_state for small fixes: damage, a trigger, velset, anim, pausetime.",
      {"number": _int("Statedef number"),
       "target": _str("'header', or the controller label as in [State N, label]"),
       "set": {"type": "array", "description": "Parameters to set, in order",
               "items": {"type": "object", "properties": {
                   "key": _str("Parameter name, e.g. 'damage', 'trigger1', 'velset'"),
                   "value": _str("Value as written in the file, e.g. '30, 5'")}, "required": ["key", "value"]}},
       "remove": {"type": "array", "items": {"type": "string"}, "description": "Parameter names to delete"},
       "occurrence": _int("Which controller when several share the label: 1 = first in the file")},
      ("number", "target"), writes=True)
def edit_state(host, char, number: int, target: str, set: list | None = None,  # noqa: A002
               remove: list | None = None, occurrence: int | None = None):
    if not set and not remove:
        raise ToolError("pass `set` and/or `remove`")
    ctl = None
    if target.strip().lower() in ("header", "statedef", f"statedef {number}"):
        st = char.states_file_for(number)
        if st is None or number not in st.statedefs or not hasattr(st, "remove_controller"):
            raise ToolError(f"statedef {number} does not exist in the character's CNS files")
        sec = st.statedefs[number].section
    else:
        st, hits = _labelled(char, number, target)
        if len(hits) > 1 and occurrence is None:
            raise ToolError(f"{len(hits)} controllers are labelled '{target}': pass occurrence=1..{len(hits)}")
        if occurrence is not None and not 1 <= occurrence <= len(hits):
            raise ToolError(f"occurrence must be 1 to {len(hits)}")
        ctl = st.statedefs[number].controllers[hits[(occurrence or 1) - 1]]
        sec = ctl.section
    pairs = []
    for item in set or []:
        key, value = str(item.get("key", "")).strip(), item.get("value", "")
        if not key or "=" in key or "\n" in key or ";" in key:
            raise ToolError(f"bad parameter name: {key!r}")
        value = str(int(value)) if isinstance(value, bool) else str(value).strip()
        if "\n" in value:
            raise ToolError(f"{key}: one line per parameter (for a new controller use add_controller)")
        pairs.append((key, value))
    gone = [k for k in (remove or []) if sec.remove(str(k).strip())]
    for key, value in pairs:
        if sec.get(key) is not None:
            sec.set(key, value)                  # troca no lugar, mantendo o comentário da linha
        else:
            # depois do último parâmetro: o fim da seção costuma ter os comentários do próximo estado
            sec.lines.insert(_body_end(sec), f"{key} = {value}")
    char.mark("commands" if st is char.commands.states else "states")
    st._index()
    # só as linhas que mudaram: o resto o modelo já leu (get_move/get_state mostram tudo)
    from ..formats import mtext
    keys = {k.lower() for k, _ in pairs}
    out: dict = {"in": sec.lines[0].strip(),
                 "lines_now": [mtext.strip_comment(ln).strip() for ln in sec.lines[1:]
                               if mtext.strip_comment(ln).split("=")[0].strip().lower() in keys]}
    if gone:
        out["removed"] = gone
    missing = [k for k in (remove or []) if k not in gone]
    if missing:
        out["not_found"] = missing
    if ctl is not None:
        from .. import validate
        from ..formats import zssread
        issues = validate._controller_issues(ctl, "", number, zssread._ikemen_consts())
        if issues:
            out["issues"] = [f"{i.severity}: {i.message}" for i in issues]
    check = _verdict(char, number)
    if check:
        out["simulation"] = check
    return out


@tool("Constants of the character's .cns ([Data], [Size], [Velocity], [Movement], [Quotes]).")
def get_constants(host, char):
    return ops.get_constants(char)


@tool("Set one constant of the .cns, e.g. section 'Data', key 'life', value '1000'.",
      {"section": _str("Section name without brackets"), "key": _str("Parameter name"),
       "value": _str("New value, as written in the file")}, ("section", "key", "value"), writes=True)
def set_constant(host, char, section: str, key: str, value: str):
    return ops.set_constant(char, section, key, value)


# ======================================================================= comandos

@tool("List the commands of the .cmd: name, input sequence and time.")
def list_commands(host, char):
    return ops.list_commands(char)


@tool("Add a [Command] to the .cmd. It still needs an entry in [Statedef -1] that changes to the "
      "move's state when the command is pressed: add_controller(number=-1, ...) with a ChangeState, "
      "or use create_move, which does all three.",
      {"name": _str("Command name, e.g. 'qcf_x'"),
       "sequence": _str("MUGEN input sequence, e.g. '~D, DF, F, x'"),
       "time": _int("Time window in ticks (default of the .cmd when omitted)")},
      ("name", "sequence"), writes=True)
def add_command(host, char, name: str, sequence: str, time: int | None = None):
    return ops.add_command(char, name, sequence, time)


@tool("Which state a joystick input leads to: the commands it completes and the [Statedef -1] entry "
      "that takes it, from standing, crouching (holding down) or jumping, with control. Pass `input` in "
      "numpad notation, one step per space: '2 3 6 a' = down, down-forward, forward + a; '6 6' = dash; "
      "'x+y' = together; '4*40 6 a' = hold back 40 ticks. Or pass `command` to try that command's own "
      "sequence. Use it after wiring a move to confirm the input reaches its state.",
      {"input": _str("Input in numpad notation (forward is always 6)"),
       "command": _str("Name of a [Command] to try instead of `input`"),
       "pose": {"type": "string", "enum": ["stand", "crouch", "air"],
                "description": "Where the player is when pressing it (default stand)"},
       "distance": {"type": "number", "description": "Distance to the opponent in pixels (default 50)"}})
def test_command(host, char, input: str | None = None, command: str | None = None, pose: str = "stand",
                 distance: float = 50.0):
    from ..tools import cmdtest
    if pose not in cmdtest.POSES:
        raise ToolError("pose must be stand, crouch or air")
    if command:
        found = char.commands.find(command)
        if not found:
            raise ToolError(f"command '{command}' is not defined in the .cmd")
        ticks = cmdtest.sequence_ticks(found[0].sequence)
        if ticks is None:
            raise ToolError(f"could not build an input for '{found[0].sequence}': pass `input` instead")
    elif input:
        ticks = cmdtest.parse_input(input)
    else:
        raise ToolError("pass `input` or `command`")
    active = [r["name"] for r in cmdtest.test_ticks(char, cmdtest.pose_ticks(ticks, pose))["active"]]
    fired = cmdtest.route(char, active, pose, float(distance))
    out = {"commands": active, "state": fired[0]["state"] if fired else None,
           "entry": f"[State -1, {fired[0]['label']}]" if fired else None}
    if fired[1:]:
        out["also_matching_later"] = [f"[State -1, {f['label']}] -> {f['state']}" for f in fired[1:6]]
    if not fired:
        out["note"] = "no [Statedef -1] entry fires: the player stays in the current state"
    return out


# ======================================================================= golpes

_MOVE_KINDS = ("normal", "crouch", "air", "special", "air_special", "super")


def _check_input(char, command: str, pose: str, replace_input: bool, own: tuple = ()) -> None:
    """A entrada já é de outro golpe, que vem antes no [Statedef -1]: só segue com replace_input.
    `own`: estados do próprio golpe (refazer um golpe não briga com a entrada dele)."""
    from ..tools import cmdtest
    owner = cmdtest.input_owner(char, command, pose)
    if owner is not None and owner["state"] not in own and not replace_input:
        raise ToolError(f"nothing changed: '{command}' is already the input of [State -1, {owner['label']}] "
                        f"(state {owner['state']}), which comes first. Pass replace_input=true if the user "
                        "wants this move on that input instead; otherwise free inputs: "
                        + ", ".join(cmdtest.free_inputs(char, pose=pose)))


def _take_input(char, command: str, pose: str, own: tuple = ()) -> str | None:
    """O golpe que tinha a entrada perde a entrada do -1 (o estado dele fica). Devolve o rótulo."""
    from ..tools import cmdtest
    taken = None
    owner = cmdtest.input_owner(char, command, pose)
    while owner is not None and owner["state"] not in own:
        taken = char.states_file_for(-1).remove_controller(-1, owner["index"]).label
        owner = cmdtest.input_owner(char, command, pose)
    return taken


@tool("Starting point for a new attack: one call writes a basic statedef (the animation plays in "
      "place, a HitDef on its first Clsn1 frame, back to stand at the end), a [Command] and its "
      "[Statedef -1] entry, placed so that normal attacks do not take the input. For anything more "
      "(moving, jumping, turning, several hits, projectiles, throws), rewrite the statedef afterwards "
      "with set_state(replace=true): mugen_guide explains how each part works. The animation must "
      "exist with Clsn1 on its attack frames (build_animation).",
      {"state": _int("Number of the new statedef (free; usually 200-699 normals, 1000-2999 specials, "
                     "3000+ supers)"),
       "anim": _int("Action number of the move's animation"),
       "kind": {"type": "string", "enum": list(_MOVE_KINDS),
                "description": "normal/crouch/air = basic attacks; special and super can cancel normals "
                               "on contact; super costs one power bar (1000) and has a super pause"},
       "command": _str("Input sequence, e.g. '~D, DF, F, x' or 'x' for a normal"),
       "command_name": _str("Name for the new [Command] (default cmd_<state>), or an existing command "
                            "with the same sequence"),
       "name": _str("Move name, used as the label of the [Statedef -1] entry"),
       "damage": _int("Damage on hit (default per kind); guard damage is about 1/8"),
       "hit_frame": _int("Animation element (1 = first) where the HitDef starts; default: the first "
                         "frame with Clsn1"),
       "replace_input": _bool("The input belongs to another move and the user wants this one there "
                              "instead: removes that move's [Statedef -1] entry (its state stays)")},
      ("state", "anim", "kind", "command"), writes=True)
def create_move(host, char, state: int, anim: int, kind: str, command: str, command_name: str | None = None,
                name: str | None = None, damage: int | None = None, hit_frame: int | None = None,
                replace_input: bool = False):
    from ..tools import movegen
    if kind not in _MOVE_KINDS:
        raise ToolError(f"kind must be one of: {', '.join(_MOVE_KINDS)}")
    if state in char.all_statedefs():
        raise ToolError(f"statedef {state} already exists: pick a free number such as "
                        f"{_free_number(char, state)}, or change that move with set_state(replace=true)")
    act = char.anims.get(anim)
    if act is None or not act.frames:
        raise ToolError(f"action {anim} does not exist: create it first with build_animation")
    if hit_frame is None and not any(f.clsn1 for f in act.frames):
        raise ToolError(f"action {anim} has no Clsn1 (attack box): remake it with build_animation "
                        "marking the active frames as attack, or pass hit_frame")
    cmd_name = command_name or f"cmd_{state}"
    same = char.commands.find(cmd_name)
    if same and same[0].sequence.replace(" ", "") != command.replace(" ", ""):
        raise ToolError(f"command '{cmd_name}' already exists with '{same[0].sequence}': pass another "
                        "command_name")
    if -1 not in char.all_statedefs():
        raise ToolError("the character has no [Statedef -1] to put the command in")
    pose = {"crouch": "crouch", "air": "air", "air_special": "air"}.get(kind, "stand")
    _check_input(char, command, pose, replace_input)
    # só o super gasta barra (uma); o padrão do gerador também cobra 1000 dos especiais
    data = {"state": state, "anim": anim, "kind": kind, "name": name or f"Move {state}",
            "command_name": cmd_name, "command_seq": command, "power": 1000 if kind == "super" else 0}
    if damage is not None:
        data["damage"] = (int(damage), max(0, int(damage) // 8))
    if hit_frame is not None:
        data["hit_elem"] = int(hit_frame)
    taken = _take_input(char, command, pose)
    r = movegen.apply_move_to_character(char, data)
    out = {"state": state, "anim": anim, "command": cmd_name, "sequence": command,
           "command_added": r["command_added"], "statedef_-1_entry_added": r["minus1_added"]}
    if taken:
        out["input_taken_from"] = f"[State -1, {taken}] (entry removed; its state is still there)"
    note = _command_note(r.get("minus1_check") or {})
    if note:
        out["command_check"] = note
    check = _verdict(char, state)
    if check:
        out["simulation"] = check
    out["text"] = r["cns_text"]
    return out


_COMBO_START = {"normal": 1000, "special": 1000, "super": 3000}
_MISS_HINT = {
    "strike": "its attack box does not reach the opponent: pick an animation that reaches further, "
              "or put a dash step before it",
    "finisher": "its attack box does not reach the opponent: pick an animation that reaches further, "
                "or put a dash step before it",
    "launcher": "its attack box does not reach the opponent: pick an animation that reaches further, "
                "or put a dash step before it",
    "air_strike": "the character does not meet the opponent in the air: pick an air attack that "
                  "reaches further or comes out sooner",
    "air_finisher": "the character does not meet the opponent in the air: pick an air attack that "
                    "reaches further or comes out sooner, or add an air_strike before it",
}


def _free_block(char, start: int, count: int) -> int:
    """Primeiro número (de 10 em 10) com `count` estados livres em seguida."""
    used = set(char.all_statedefs())
    n = start - start % 10
    while any(n + k in used for k in range(count)):
        n += 10
    return n


@tool("Build a whole move from a list of steps, without writing CNS: you choose what each step "
      "does (its role) and its animation; the tool writes every statedef (one per step, each one "
      "chained only if the step before it hit), the HitDefs with enough hitstun for the next step, "
      "the launch, the jump after the opponent, the landing, the [Command] and the [Statedef -1] "
      "entry, then tunes launch height, jump strength and timing in the simulator until every hit "
      "connects. Use it for any new attack, from one hit to a long combo. Roles: "
      + "; ".join(f"{k} = {v}" for k, v in combo.ROLES.items())
      + ". Call it again with the same `state` to change the move (it replaces its own states).",
      {"steps": {"type": "array", "description": "The steps in order, e.g. two strikes, a launcher "
                 "and an air_finisher",
                 "items": {"type": "object", "properties": {
                     "role": {"type": "string", "enum": list(combo.ROLES)},
                     "anim": _int("Action number of the pose for this step (from list_animations; a "
                                  "jump step may leave it out)"),
                     "damage": _int("Damage of each hit of this step (default per role and kind)"),
                     "hits": {"type": "array", "items": {"type": "integer"},
                              "description": "Animation elements (1 = first) where each hit starts; "
                                             "default: each run of frames with Clsn1"}},
                     "required": ["role"]}},
       "command": _str("Input sequence, e.g. '~D, DF, F, x'"),
       "kind": {"type": "string", "enum": list(combo.KINDS),
                "description": "normal, special (default; cancels normals on contact) or super (costs "
                               "one power bar, super pause)"},
       "state": _int("First statedef of the move (the steps use the numbers after it); default: a "
                     "free block from 1000 (3000 for a super)"),
       "name": _str("Move name"),
       "command_name": _str("Name for the new [Command] (default cmd_<state>)"),
       "replace_input": _bool("The input belongs to another move and the user wants this one there "
                              "instead: removes that move's [Statedef -1] entry (its state stays)")},
      ("steps", "command"), writes=True)
def build_move(host, char, steps: list, command: str, kind: str = "special", state: int | None = None,
               name: str | None = None, command_name: str | None = None, replace_input: bool = False):
    from ..tools import movegen
    try:
        plan = combo.plan(char, [s if isinstance(s, dict) else {} for s in steps], kind)
    except combo.ComboError as e:
        raise ToolError(str(e)) from None
    defs = char.all_statedefs()
    if state is None:
        base = _free_block(char, _COMBO_START[kind], len(plan))
    else:
        base = int(state)
        for n in range(base, base + len(plan)):
            sd = defs.get(n)
            if sd and combo.MARK not in sd[0].text():
                raise ToolError(f"statedef {n} belongs to another move: pick a free number such as "
                                f"{_free_block(char, base, len(plan))}")
    if -1 not in defs:
        raise ToolError("the character has no [Statedef -1] to put the command in")
    air = plan[0].role in combo.AIR_ROLES
    pose = "air" if air else "stand"
    _check_input(char, command, pose, replace_input, own=(base,))
    cmd_name = command_name or f"cmd_{base}"
    same = char.commands.find(cmd_name)
    if same and same[0].sequence.replace(" ", "") != command.replace(" ", ""):
        raise ToolError(f"command '{cmd_name}' already exists with '{same[0].sequence}': pass another "
                        "command_name")
    try:
        combo.place(char, plan, base, kind)
    except combo.ComboError as e:
        raise ToolError(str(e)) from None
    name = name or f"Combo {base}"
    taken = _take_input(char, command, pose, own=(base,))
    entry_kind = {"normal": "air", "special": "air_special"}.get(kind, kind) if air else kind
    r = movegen.apply_move_to_character(char, {
        "state": base, "anim": plan[0].anim, "kind": entry_kind, "name": name, "command_name": cmd_name,
        "command_seq": command, "power": 1000 if kind == "super" else 0})
    tuned = combo.tune(char, plan, kind, name)
    for n in combo.leftovers(char, base, len(plan)):
        ops.remove_statedef(char, n)
    rep = combo.run(char, plan, 50.0)
    rows, missed = [], None
    for k, s in enumerate(plan, 1):
        row = {"step": k, "state": s.state, "role": s.role, "anim": s.anim}
        if s.role in combo.HIT_ROLES:
            t = rep.hit.get(s.state)
            row["hit"] = f"tick {t}" if t is not None else "MISSES"
            if t is None and missed is None:
                missed = (k, s)
        if s.note:
            row["note"] = s.note
        rows.append(row)
    if missed is None:
        verdict = (f"every hit connects, in order, on an opponent 50 px away ({rep.hits} hits, "
                   f"{rep.ticks} ticks until both are idle)")
    else:
        k, s = missed
        verdict = (f"step {k} ({s.role}, anim {s.anim}) MISSES on an opponent 50 px away, so the move "
                   f"stops there: {_MISS_HINT[s.role]}")
    works = [d for d in (30, 50, 70, 90) if all(t is not None for t in combo.run(char, plan, d).hit.values())]
    roles = {s.role for s in plan}
    out = {"verdict": verdict, "steps": rows, "states": [s.state for s in plan],
           "connects_from_distances": works,
           "tuned": {k: getattr(tuned, k) for k, role in combo.NEEDS.items() if role in roles},
           "command": cmd_name, "sequence": command}
    if taken:
        out["input_taken_from"] = f"[State -1, {taken}] (entry removed; its state is still there)"
    note = _command_note(r.get("minus1_check") or {})
    if note:
        out["command_check"] = note
    errs = [i for i in validate.report(validate.validate_character(char))["issues"]
            if i["severity"] == "error" and any(i["where"].startswith(f"[State {s.state},") or
                                                i["where"].startswith(f"[Statedef {s.state}]") for s in plan)]
    if errs:
        out["problems"] = errs[:10]
    return out


# ======================================================================= animações

@tool("List the animations (actions of the .air), one line each, with what each one is: its name "
      "(the comment in the .air or the engine's standard meaning), frame count and total time, the "
      "states that use it and the input that reaches them. Read the names before picking poses for a "
      "move, then look at the candidates with view_animations. Filter with `search` ('kick', 'jump') "
      "or `numbers` on a big character.",
      {"numbers": _str("Only this range, e.g. '200-699'"),
       "search": _str("Only animations whose name, state or input contains this text")})
def list_animations(host, char, numbers: str | None = None, search: str | None = None):
    users, entries = _anim_users(char), _entries_by_state(char)
    rng, want = _range(numbers), (search or "").strip().lower()
    lines = []
    for n in char.anims.numbers():
        if rng and not rng[0] <= n <= rng[1]:
            continue
        a = char.anims.get(n)
        name = _anim_name(char, n)
        row = f"{n}" + (f' "{name}"' if name else "") + f" {len(a.frames)}f {a.total_time}t"
        if users.get(n):
            row += " state " + ",".join(map(str, users[n][:4]))
            inputs = [e for st in users[n] for e in entries.get(st, [])]
            if inputs:
                row += " <- " + "; ".join(inputs[:2])
        if not want or want in row.lower():
            lines.append(row)
    if not lines:
        return "no animation matches"
    return "action \"name\" frames(f) ticks(t, -1 = holds forever) state(s) <- input\n" + "\n".join(lines)


@tool("Contact sheet: one picture with a few frames of each animation listed (key poses, the attack "
      "frame in red boxes), labelled with number and name. Use it to find the right poses for a move "
      "(a jump, a kick in the air) before building its animation.",
      {"numbers": {"type": "array", "items": {"type": "integer"},
                   "description": "Action numbers, up to 16 (e.g. the candidates from list_animations)"},
       "palette": _int("Palette number 1-12 (default 1)")}, ("numbers",))
def view_animations(host, char, numbers: list, palette: int | None = None):
    from PIL import Image, ImageDraw
    from ..tools import render
    nums = [int(n) for n in numbers][:16]
    rows, missing = [], []
    for n in nums:
        act = char.anims.get(n)
        if act is None or not act.frames:
            missing.append(n)
            continue
        k = len(act.frames)
        pick = sorted({0, k // 3, (2 * k) // 3, k - 1})      # começo, meio e fim
        hit = next((i for i, f in enumerate(act.frames) if f.clsn1), None)
        if hit is not None and hit not in pick:              # o quadro do golpe sempre aparece
            pick = sorted(set(pick[:3]) | {hit})
        cells = []
        for i in pick[:4]:
            im = render.render_frame(char, act, i, palette, clsn=True, scale=1, axis=False)
            im.thumbnail((150, 150))
            cells.append((i, im))
        rows.append((n, _anim_name(char, n), cells))
    if not rows:
        raise ToolError(f"none of these actions exist: {missing}")
    cw, ch, lh = 152, 152, 16
    sheet = Image.new("RGB", (cw * 4, (ch + lh) * len(rows)), (24, 24, 32))
    d = ImageDraw.Draw(sheet)
    for r, (n, name, cells) in enumerate(rows):
        y = r * (ch + lh)
        d.text((4, y + 2), f"{n}  {name}"[:90], fill=(255, 230, 120))
        for c, (i, im) in enumerate(cells):
            sheet.paste(im, (c * cw + (cw - im.width) // 2, y + lh + ch - im.height))
            d.text((c * cw + 3, y + lh + 2), f"#{i + 1}", fill=(170, 170, 190))
    text = "rows top to bottom: " + "; ".join(f"{n} {name}" for n, name, _ in rows)
    if missing:
        text += f" (not found: {missing})"
    return Result(text, [_png(sheet)])


@tool("AIR text of an animation: [Begin Action N] with every frame (group, item, x, y, time, flip, "
      "transparency) and its Clsn1/Clsn2 boxes.", {"number": _int("Action number")}, ("number",))
def get_animation(host, char, number: int):
    return ops.get_action_text(char, number)


_AIR_SYNTAX = (
    "[Begin Action 1450]\n"
    "Clsn2Default: 2\n Clsn2[0] = -15, -90, 15, 0\n Clsn2[1] = -8, -100, 8, -90\n"
    "210,0, 0,0, 4\n"
    "Clsn1: 1\n Clsn1[0] = 20, -80, 60, -60\n"
    "210,1, 0,0, 3\n")


def _air_problems(text: str) -> list[str]:
    """Linhas que o leitor do AIR ignoraria sem avisar (caixa sem cabeçalho, contagem errada…)."""
    from ..formats import air, mtext
    out = []
    left, head, waiting = 0, 0, False      # caixas que faltam, linha do cabeçalho, caixas à espera de quadro
    inside = False                          # antes da 1ª ação o leitor ignora tudo (BOM, notas)
    for no, raw in enumerate(text.splitlines(), 1):
        line = mtext.strip_comment(raw).strip()
        inside = inside or line.lower().startswith("[begin action")
        if not line or not inside:
            continue
        m = air._CLSN_HDR.match(line)
        if line.lower().startswith("[begin action") or m or air._is_frame_line(line):
            if left:
                out.append(f"line {head}: the header announces {left} more box(es) than follow it")
            left = 0
            if m:
                left, head = int(m[3]), no
                waiting = waiting or (not m[2] and left > 0)
            else:
                waiting = False
            continue
        m = air._CLSN_BOX.match(line)
        if m:
            if left:
                left -= 1
            else:
                out.append(f"line {no}: '{line}' ignored: boxes go right after a 'Clsn{m[1]}: <count>' "
                           f"(or 'Clsn{m[1]}Default: <count>') line, as many as the count says")
            continue
        if line.lower().startswith("loopstart") or air._INTERP.match(line):
            continue
        out.append(f"line {no}: '{line}' not understood")
    if left:
        out.append(f"line {head}: the header announces {left} more box(es) than follow it")
    if waiting:
        out.append("Clsn boxes after the last frame belong to no frame: put them before their frame")
    return out[:20]


@tool("Create or replace animations from AIR text: one or more complete [Begin Action N] blocks. Each "
      "frame is 'group,item, x,y, time[, flip[, trans]]'. Boxes come BEFORE the frame they belong to: "
      "'Clsn2: <count>' (or 'Clsn1: <count>') and then exactly that many 'Clsn2[i] = x1, y1, x2, y2' "
      "lines apply to the next frame only; 'Clsn2Default: <count>' at the top applies to every frame "
      "without its own Clsn2. Clsn2 (hurt) on every frame, Clsn1 (attack) only on the active ones. "
      "Syntax example (placeholder numbers: take real boxes from suggest_clsn or a similar animation, "
      "or use build_animation, which fits them):\n" + _AIR_SYNTAX + "Text the parser would ignore is "
      "refused with the lines to fix. The result lists, per action, the frames that got Clsn1/Clsn2.",
      {"text": _str("AIR text with [Begin Action N] blocks")}, ("text",), writes=True)
def set_animation(host, char, text: str):
    from ..formats import air
    bad = _air_problems(text)
    if bad:
        raise ToolError("nothing changed; fix these lines (syntax example below):\n" + "\n".join(bad)
                        + "\n\n" + _AIR_SYNTAX)
    parsed = air.loads(text)
    out = ops.set_action_text(char, text)
    out["actions"] = [{"number": a.number, "frames": len(a.frames),
                       "frames_with_clsn1": [k + 1 for k, f in enumerate(a.frames) if f.clsn1],
                       "frames_without_clsn2": [k + 1 for k, f in enumerate(a.frames) if not f.clsn2],
                       "total_time": a.total_time}
                      for a in (parsed.actions[n] for n in sorted(parsed.actions))]
    return out


@tool("Create or replace an animation from a list of frames, with the collision boxes fitted to each "
      "sprite: Clsn2 (head, torso, legs) on every frame and a Clsn1 on the extended limb of the frames "
      "marked attack. The easiest way to make a move's animation; adjust boxes later with "
      "set_animation if needed.",
      {"number": _int("Action number"),
       "frames": {"type": "array", "description": "Frames in order",
                  "items": {"type": "object", "properties": {
                      "group": _int("Sprite group"), "item": _int("Sprite number"),
                      "time": _int("Ticks the frame lasts (-1 = forever, only for the last frame)"),
                      "attack": _bool("Active frame: gets a Clsn1 (default false)"),
                      "x": _int("Offset x (default 0)"), "y": _int("Offset y (default 0)"),
                      "flip": _str("'H', 'V' or 'HV' to mirror (default none)")},
                      "required": ["group", "item", "time"]}},
       "loopstart": _int("Frame index (0 = first) where the loop restarts (default 0)")},
      ("number", "frames"), writes=True)
def build_animation(host, char, number: int, frames: list, loopstart: int = 0):
    if not frames:
        raise ToolError("frames is empty")
    rows, missing, no_reach = [], [], []
    for k, fd in enumerate(frames, 1):
        if not isinstance(fd, dict) or any(key not in fd for key in ("group", "item", "time")):
            raise ToolError(f"frame {k}: each frame is an object with group, item and time")
        g, i = int(fd["group"]), int(fd["item"])
        if char.sprites.find(g, i) is None:
            missing.append(f"{g},{i}")
            continue
        row = {"group": g, "item": i, "time": int(fd["time"]), "x": int(fd.get("x", 0)),
               "y": int(fd.get("y", 0)), "flip": fd.get("flip", ""),
               "clsn2": ops.anatomical_clsn(char, g, i)}
        if fd.get("attack"):
            row["clsn1"] = ops.attack_clsn(char, g, i)
            if not row["clsn1"]:
                no_reach.append(k)
        rows.append(row)
    if missing:
        raise ToolError("sprites not found: " + ", ".join(missing) + " (list_sprites shows what exists)")
    existed = char.anims.get(number) is not None
    ops.create_action(char, number, rows, loopstart, auto_clsn2=False)
    out = {"number": number, "replaced": existed, "frames": len(rows),
           "frames_with_clsn1": [k + 1 for k, r in enumerate(rows) if r.get("clsn1")],
           "total_time": sum(max(r["time"], 0) for r in rows)}
    if no_reach:
        out["note"] = (f"no limb reaching out on frame(s) {no_reach}, so no Clsn1 there: pick a pose with "
                       "the hit extended, or add the box with set_animation")
    return out


_FRAME_FIELDS = {
    "group": _int("Sprite group"), "item": _int("Sprite number"), "time": _int("Ticks"),
    "x": _int("Offset x"), "y": _int("Offset y"), "flip": _str("'', H, V, HV"), "trans": _str("'', A, S…"),
    "attack": _bool("true fits a Clsn1, false removes it"),
    "refit_hurt": _bool("Refit Clsn2 (automatic on a new sprite)")}


def _fit(char, f, fd: dict) -> list[int] | None:
    """Caixas de um frame conforme o pedido; devolve o frame se pediu ataque e nada alcança."""
    if fd.get("refit_hurt") or "group" in fd or "item" in fd:
        f.clsn2 = list(ops.anatomical_clsn(char, f.group, f.item))
    if fd.get("attack") is True:
        f.clsn1 = list(ops.attack_clsn(char, f.group, f.item))
        return None if f.clsn1 else [f.group, f.item]
    if fd.get("attack") is False:
        f.clsn1 = []
    return None


@tool("Change an animation in place, without rewriting it: only the frames and fields you pass. "
      "Frame numbers count from 1 and always refer to the animation as it was before this call. "
      "`frames`: changes, each with `frame` plus the fields to change (sprite, time, offset, flip, "
      "trans; attack=true fits a Clsn1, false removes it). `delete`: frame numbers to remove. "
      "`insert`: new frames, each with `after` (0 = before the first) and group, item, time; their "
      "hurt boxes are fitted. Cheaper and safer than set_animation for timing, offsets and boxes.",
      {"number": _int("Action number"),
       "frames": {"type": "array", "description": "Changes to existing frames",
                  "items": {"type": "object", "properties": {"frame": _int("1 = first"), **_FRAME_FIELDS},
                            "required": ["frame"]}},
       "delete": {"type": "array", "items": {"type": "integer"}, "description": "Frame numbers to delete"},
       "insert": {"type": "array", "description": "New frames",
                  "items": {"type": "object", "properties": {"after": _int("0 = at the start"),
                                                             **{k: v for k, v in _FRAME_FIELDS.items()
                                                                if k != "refit_hurt"}},
                            "required": ["after", "group", "item", "time"]}},
       "loopstart": _int("Frame number (1 = first) where the loop restarts")},
      ("number",), writes=True)
def edit_animation(host, char, number: int, frames: list | None = None, delete: list | None = None,
                   insert: list | None = None, loopstart: int | None = None):
    import copy
    from ..formats import air
    old = char.anims.get(number)
    if old is None:
        raise ToolError(f"action {number} does not exist: create it with build_animation")
    if not (frames or delete or insert or loopstart is not None):
        raise ToolError("pass `frames`, `delete`, `insert` and/or `loopstart`")
    act = copy.deepcopy(old)
    count = len(act.frames)

    def frame_no(v, what: str, low: int = 1) -> int:
        if not isinstance(v, int) or isinstance(v, bool) or not low <= v <= count:
            raise ToolError(f"{what} must be {low} to {count} (the animation has {count} frames)")
        return v

    def sprite_ok(g, i):
        if char.sprites.find(int(g), int(i)) is None:
            raise ToolError(f"sprite {g},{i} does not exist (list_sprites shows what exists)")

    no_reach = []
    for fd in frames or []:
        f = act.frames[frame_no(fd.get("frame"), "frame") - 1]
        for k in ("group", "item", "time", "x", "y"):
            if k in fd:
                setattr(f, k, int(fd[k]))
        for k in ("flip", "trans"):
            if k in fd:
                setattr(f, k, str(fd[k] or "").strip())
        if "group" in fd or "item" in fd:
            sprite_ok(f.group, f.item)
        miss = _fit(char, f, fd)
        if miss:
            no_reach.append(fd["frame"])
    added: dict[int, list] = {}
    for fd in insert or []:
        sprite_ok(fd["group"], fd["item"])
        f = air.Frame(int(fd["group"]), int(fd["item"]), int(fd.get("x", 0)), int(fd.get("y", 0)),
                      int(fd["time"]), str(fd.get("flip") or ""), str(fd.get("trans") or ""))
        f.clsn2 = list(ops.anatomical_clsn(char, f.group, f.item))
        if _fit(char, f, {"attack": fd.get("attack")}):
            no_reach.append(f"new after {fd['after']}")
        added.setdefault(frame_no(fd.get("after"), "after", 0), []).append(f)
    gone = {frame_no(v, "delete") for v in delete or []}
    new, loop = list(added.get(0, [])), None
    for k, f in enumerate(act.frames, 1):
        if loopstart is not None and k == frame_no(loopstart, "loopstart"):
            loop = len(new)
        elif loopstart is None and act.loopstart > 0 and k - 1 == act.loopstart:
            loop = len(new)                    # loop desde o 1º frame inclui os inseridos no começo
        if k not in gone:
            new.append(f)
        new += added.get(k, [])
    if not new:
        raise ToolError("that would leave the animation with no frames: use remove_animation")
    act.frames, act.loopstart = new, min(loop or 0, len(new) - 1)
    char.anims.set_action(act)
    char.mark("anims")
    out = {"text": ops.get_action_text(char, number), "total_time": act.total_time}
    if no_reach:
        out["note"] = (f"no limb reaching out on frame(s) {no_reach}, so no Clsn1 there: pick a pose with "
                       "the hit extended, or write the box with set_animation")
    return out


@tool("Delete an animation.", {"number": _int("Action number")}, ("number",), writes=True)
def remove_animation(host, char, number: int):
    if not ops.remove_action(char, number):
        raise ToolError(f"action {number} does not exist")
    return f"action {number} removed"


@tool("Picture of every frame of an animation side by side, with the Clsn boxes (blue Clsn2, red "
      "Clsn1) and the axis. Use it to check poses, alignment and boxes.",
      {"number": _int("Action number"), "palette": _int("Palette number 1-12 (default 1)"),
       "boxes": _bool("Draw the collision boxes (default true)")}, ("number",))
def view_animation(host, char, number: int, palette: int | None = None, boxes: bool = True):
    from ..tools import render
    act = char.anims.get(number)
    if act is None or not act.frames:
        raise ToolError(f"action {number} does not exist or has no frames")
    img = render.render_strip(char, number, palette, boxes, scale=1 if len(act.frames) > 8 else 2)
    return Result(f"action {number}: {len(act.frames)} frame(s), left to right", [_png(img)])


@tool("Collision boxes fitted to a sprite, relative to its axis, ready for the .air: anatomical "
      "Clsn2 (head, torso, legs) and, for attack poses, a Clsn1 on the extended limb; also the AIR "
      "lines of a frame with them (change its time), to paste into set_animation.",
      {"group": _int("Sprite group"), "item": _int("Sprite number")}, ("group", "item"))
def suggest_clsn(host, char, group: int, item: int):
    if char.sprites.find(group, item) is None:
        raise ToolError(f"sprite {group},{item} does not exist")
    c2 = [list(b) for b in ops.anatomical_clsn(char, group, item)]
    c1 = [list(b) for b in ops.attack_clsn(char, group, item)]

    def block(n, boxes):
        return [f"Clsn{n}: {len(boxes)}"] + [f" Clsn{n}[{i}] = {', '.join(map(str, b))}" for i, b in enumerate(boxes)]
    frame = f"{group},{item}, 0,0, 4"
    return {"clsn2": c2, "clsn1": c1,
            "air_frame": "\n".join(block(2, c2) + [frame]),
            "air_frame_attacking": "\n".join(block(2, c2) + (block(1, c1) if c1 else []) + [frame])}


# ======================================================================= sprites, paletas e sons

@tool("List the sprites (group, item, size, axis) and, for a group, the animations that use it (their "
      "names say what the poses are). Pass a group: the full list can have thousands.",
      {"group": _int("Only this group")})
def list_sprites(host, char, group: int | None = None):
    rows = ops.list_sprites(char, group)
    keep = ("group", "item", "width", "height", "axis", "linked_to")
    if group is None and len(rows) > 150:
        groups: dict[int, int] = {}
        for r in rows:
            groups[r["group"]] = groups.get(r["group"], 0) + 1
        return {"total": len(rows), "sprites_per_group": groups,
                "note": "too many to list: call again with a group"}
    sprites = [{k: r[k] for k in keep if r.get(k) is not None} for r in rows]
    if group is None:
        return sprites
    used = sorted({n for n in char.anims.numbers() for f in char.anims.get(n).frames if f.group == group})
    return {"sprites": sprites, "used_in_actions": [f"{n} {_anim_name(char, n)}".strip() for n in used[:12]]}


@tool("Picture of one sprite with the chosen palette (the axis is the sprite's origin).",
      {"group": _int("Sprite group"), "item": _int("Sprite number"),
       "palette": _int("Palette number 1-12 (default 1)")}, ("group", "item"))
def view_sprite(host, char, group: int, item: int, palette: int | None = None):
    from ..tools import render
    img = render.sprite_image(char, group, item, palette)
    if max(img.size) < 200:
        img = img.resize((img.width * 2, img.height * 2), 0)
    s = char.sprites.find(group, item)
    return Result(f"sprite {group},{item}: {s.width}x{s.height}, axis {s.axis_x},{s.axis_y}", [_png(img)])


@tool("List the palettes of the SFF (group, item and number of colors).")
def list_palettes(host, char):
    return ops.list_palettes(char)


@tool("List the sounds of the .snd: group, number and duration.")
def list_sounds(host, char):
    return ops.list_sounds(char)


# ======================================================================= imagens e paletas

_IMPORT_MODES = ["embedded", "char", "palette", "swap", "own", "truecolor"]


def _image_paths(paths) -> list[str]:
    out = [str(p) for p in ([paths] if isinstance(paths, str) else list(paths or []))]
    if not out:
        raise ToolError("give at least one image path")
    missing = [p for p in out if not Path(p).is_file()]
    if missing:
        raise ToolError(f"file(s) not found: {', '.join(missing[:5])}")
    return out


@tool("Look at image files before importing them as sprites: size, whether each one is indexed with "
      "its own embedded palette (and how many colors it uses) or true color, transparency, and the "
      "palette mode the import window would recommend, with a small picture of the first one.",
      {"paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute image paths"}},
      ("paths",))
def inspect_images(host, char, paths: list):
    from ..tools import spriteimport as SI
    files = _image_paths(paths)
    images = SI.open_images(files)
    mode, why = SI.recommend(images, char)
    rows = []
    for p, im in zip(files, images):
        info = SI.image_info(im)
        info["file"] = Path(p).name
        rows.append(info)
    out = {"images": rows, "recommended_mode": mode, "why": why,
           "note": "big images (more than 1024 px) usually need scale < 1 to match the character "
                   f"(standing sprite 0,0 is {char.sprites.find(0, 0).height if char.sprites.find(0, 0) else '?'} px tall)"}
    return Result(json.dumps(out, ensure_ascii=False, default=str), [_png(images[0].convert("RGBA"))])


@tool("Import image files as sprites (the same as the Import images window). Palette `mode`: "
      "embedded = the image's own palette, indices and colors exactly as they are (indexed PNG/PCX/GIF); "
      "char = fit to the character palette 1,1 (changes with the 12 player palettes, for the body); "
      "palette = fit to another SFF palette; swap = keep the indices and use an SFF palette; "
      "own = a new palette made of the image colors; truecolor = 32-bit PNG (MUGEN 1.1/Ikemen). "
      "Call inspect_images first to pick the mode and the scale.",
      {"paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute image paths, in order"},
       "group": _int("Sprite group"), "item": _int("First sprite number (the next images follow)"),
       "mode": {"type": "string", "enum": _IMPORT_MODES, "description": "Palette mode (default: the recommended one)"},
       "palette": {"type": "array", "items": {"type": "integer"},
                   "description": "[group, item] of the SFF palette for palette/swap"},
       "as_char_palette": _bool("embedded: the image palette also becomes the character palette 1,1"),
       "scale": {"type": "number", "description": "Size factor applied first, e.g. 0.25 (default 1)"},
       "smooth": _bool("Smooth reduction (HD art; leave off for pixel art)"),
       "anchor": {"type": "string", "enum": ["tl", "t", "tr", "l", "c", "r", "bl", "b", "br"],
                  "description": "Where the axis goes on the image (default b: feet, bottom center)"},
       "step": {"type": "integer", "enum": [1, 10], "description": "Numbering step (default 1)"},
       "from_name": _bool("Read group and number from the file names (kfm_200-3.png)"),
       "autocrop": _bool("Crop the empty border (default true)")},
      ("paths", "group", "item"), writes=True)
def import_sprites(host, char, paths: list, group: int, item: int, mode: str | None = None,
                   palette: list | None = None, as_char_palette: bool = False, scale: float = 1.0,
                   smooth: bool = False, anchor: str = "b", step: int = 1, from_name: bool = False,
                   autocrop: bool = True):
    from ..tools import spriteimport as SI
    files = _image_paths(paths)
    if mode is None:
        mode, _ = SI.recommend([SI.scale_image(im, SI.ImportOptions(scale=scale)) for im in SI.open_images(files)], char)
    if mode in ("palette", "swap") and not palette:
        raise ToolError("palette and swap need palette=[group, item]; list_palettes shows them")
    opts = SI.ImportOptions(mode=mode, palette=tuple(palette) if palette else None, as_char_palette=as_char_palette,
                            scale=float(scale or 1), smooth=smooth, anchor=anchor, step=step,
                            from_name=from_name, autocrop=autocrop)
    r = SI.import_images(char, files, group, item, opts)
    r["mode"] = mode
    return r


@tool("Replace the colors of a palette with the palette embedded in an image (indexed PNG/PCX/GIF/BMP, "
      "index by index) or, for a true-color image, up to 256 of its colors.",
      {"path": _str("Absolute path of the image"),
       "slot": _int("Character palette 1-12 to replace (default 1)")}, ("path",), writes=True)
def import_palette_from_image(host, char, path: str, slot: int = 1):
    import tempfile
    from ..formats import act
    from ..tools import spriteimport as SI
    cols = SI.palette_from_image(_image_paths([path])[0])
    tmp = Path(tempfile.gettempdir()) / f"charfactory_ai_pal_{slot}.act"
    act.save((cols + [(0, 0, 0)] * 256)[:256], tmp)
    try:
        return ops.import_palette(char, slot, tmp)
    finally:
        tmp.unlink(missing_ok=True)


# ======================================================================= rodar no motor

@tool("List the run profiles saved in the app (Debug > Run profiles): engine, opponent, stage, who "
      "plays each side, life, power, rounds and the Ikemen GO options of each one.", needs_char=False)
def list_run_profiles(host):
    from ..tools import runner
    s = getattr(getattr(host, "ctx", None), "settings", None) or runner.gui_settings()
    active = runner.active_profile(s).name
    return {"active": active, "profiles": [
        {k: v for k, v in p.to_dict().items() if v not in (None, "", 0, False) or k == "name"}
        for p in runner.load_profiles(s)]}


@tool("Open the character in MUGEN or Ikemen GO with a run profile (the active one by default), "
      "for the user to play: the real engine shows what the simulator cannot. Only when the user asks "
      "to run or test in the engine; it saves nothing.",
      {"profile": _str("Profile name (list_run_profiles); default the active one"),
       "engine": {"type": "string", "enum": ["auto", "mugen", "ikemen"], "description": "Only this time"}},
      writes=True)
def run_in_engine(host, char, profile: str | None = None, engine: str | None = None):
    from ..tools import runner
    s = getattr(getattr(host, "ctx", None), "settings", None) or runner.gui_settings()
    names = [p.name for p in runner.load_profiles(s)]
    if profile and profile not in names:
        raise ToolError(f"profile '{profile}' not found; saved: {', '.join(names)}")
    prof = runner.active_profile(s, profile)
    launch = getattr(host, "run_engine", None)
    if launch is not None:                    # na janela: roda sem travar e a saída vai para o painel
        return launch(prof, engine)
    return runner.run(char, engine=engine or "auto", profile=prof, mugen_root=s.get("mugen_dir") or None,
                      ikemen_root=s.get("ikemen_dir") or None)


# ======================================================================= referência

@tool("How MUGEN works, topic by topic, to build any move by hand: states (header, triggers, ending "
      "a move), timing (time, AnimElem, AnimTime), movement (velocities, gravity, jump math), sides "
      "(Turn, getting behind or over the opponent), hitting (HitDef, several hits), projectiles, "
      "throws, supers, commands ([Command] syntax, [Statedef -1] order), animation (AIR, boxes) "
      "and style (how most characters write moves: helpers, their own sparks and sounds, effect angles, "
      "facing and scale). Read the "
      "topics a move needs, and style, before writing a statedef by hand.",
      {"topic": {"type": "string", "enum": ["states", "timing", "movement", "sides", "hitting",
                                            "projectiles", "throws", "supers", "commands", "animation",
                                            "style"],
                 "description": "Topic to read"}}, ("topic",), needs_char=False)
def mugen_guide(host, topic: str):
    from . import guide
    return guide.topic(topic)



@tool("Reference of a MUGEN state controller or trigger: description and every parameter with its "
      "syntax, from the same grammar the validator uses. Pass an exact name ('HitDef', 'AnimElemTime') "
      "or a word to search for ('velocity', 'palette'). Check it before writing a controller you are "
      "not sure about.", {"name": _str("Controller or trigger name, or a word to search for")},
      ("name",), needs_char=False)
def mugen_reference(host, name: str):
    from ..tools import generate
    g = validate.grammar()
    key = name.strip().lower()
    spec = g["controllers"].get(key)
    if spec:
        return {"controller": spec["name"], "description": spec["description"],
                "required": spec.get("required", []), "required_one_of": spec.get("required_any", []),
                "parameters": {k: v.get("syntax", "") for k, v in spec.get("params", {}).items()}}
    spec = g["triggers"].get(key)
    if spec:
        return {"trigger": spec["name"], "description": spec["description"]}
    found = generate.search_reference(name)
    if not found["controllers"] and not found["triggers"]:
        raise ToolError(f"nothing about '{name}' in the reference")
    return found


# ======================================================================= simulação

def _verdict(char, number: int) -> dict | None:
    """Simulação rápida de um estado de ataque, para a resposta de quem acabou de escrevê-lo: o
    modelo vê na hora se o golpe acerta, quantas vezes e de que lado, sem precisar pedir."""
    from ..tools import simulate
    sd = char.all_statedefs().get(number)
    if number < 0 or not sd or not hasattr(sd[0], "params"):
        return None
    if (sd[0].params().get("movetype") or "").strip().upper()[:1] != "A":
        return None
    try:
        sim = simulate.simulate(char, number, max_ticks=600, from_ground=simulate.starts_on_ground(char, number))
    except Exception:  # noqa: BLE001  (o veredito é um extra: estado quebrado aparece no validate)
        return None
    fd = simulate.frame_data(sim)
    hits = [e for e in sim.events if e["kind"] in ("acerto", "defesa") and e.get("who") == "P1"]
    path = _path(sim)
    if hits:
        fh = path.get("first_hit", {})
        side = f" from {fh['from']}" if fh else ""
        air = " in the air" if fh.get("in_air") else ""
        text = (f"hits {len(hits)} time(s) on an opponent 50 px away; first hit at tick {hits[0]['t']}"
                f"{side}{air}")
    else:
        text = ("MISSES: no hit on an opponent standing 50 px away. Check the Clsn1 frames, when the "
                "HitDef is on, and where the character is when it attacks (simulate_state path)")
    out = {"verdict": text, "startup": fd["startup"], "total": fd["total"]}
    if "crossed_opponent_at_tick" in path:
        out["crossed_opponent_at_tick"] = path["crossed_opponent_at_tick"]
    probs = [p.get("message", p) if isinstance(p, dict) else p for p in simulate.audit(sim)[:3]]
    if probs:
        out["problems"] = probs
    return out


def _path(sim) -> dict:
    """Por onde o P1 passou: dx = distância até o oponente no sentido em que ele começou virado
    (negativo = na frente do oponente, positivo = já passou para as costas dele)."""
    pts = []
    for r in sim.track:
        p1 = next((e for e in r["ents"] if e["name"] == "P1"), None)
        if p1 is not None:
            pts.append((r["t"], p1, r["p2"]))
    if not pts:
        return {}
    f0 = pts[0][1]["facing"] or 1
    rows, last, crossed = [], None, None
    for t, p1, p2 in pts:
        dx = round((p1["pos"][0] - p2["pos"][0]) * f0, 1)
        if crossed is None and dx > 0 and pts[0][1]["pos"][0] * f0 < pts[0][2]["pos"][0] * f0:
            crossed = t
        key = (p1["state"], p1["elem"], bool(p1["clsn1"]), p1["facing"])
        if key != last or t % 6 == 0:
            rows.append({"t": t, "dx": dx, "y": round(p1["pos"][1], 1), "state": p1["state"], "elem": p1["elem"],
                         "faces_opponent": (p2["pos"][0] - p1["pos"][0]) * p1["facing"] > 0,
                         "attack_box": bool(p1["clsn1"])})
            last = key
    out = {"path": rows[:60]}
    if crossed is not None:
        out["crossed_opponent_at_tick"] = crossed
    hits = [e for e in sim.events if e["kind"] in ("acerto", "defesa") and e.get("who") == "P1"]
    if hits:
        t = hits[0]["t"]
        at = min(pts, key=lambda x: abs(x[0] - t))
        dx = (at[1]["pos"][0] - at[2]["pos"][0]) * f0
        out["first_hit"] = {"tick": t, "from": "behind" if dx > 0 else "front",
                            "in_air": at[1]["pos"][1] < 0}
    return out


_OUTCOME = {"acerto": "hit", "defesa": "guarded", "errou": "whiff"}
_EVENTS = {"estado": "state", "acerto": "hit", "defesa": "guard", "pausa": "pause"}


@tool("Run a state tick by tick in the built-in simulator against a standing opponent, without "
      "opening the engine: frame data (startup, active, recovery, advantage), state changes, hits "
      "and problems found, plus the `path` (in each row dx < 0: in front of the opponent, dx > 0: "
      "past it, behind its back; y < 0: in the air). Optionally a picture of the move frame by frame.",
      {"number": _int("State number to start in, e.g. 200"),
       "distance": {"type": "number", "description": "Distance to the opponent in pixels (default 50)"},
       "picture": _bool("Also return a sheet with each frame of the move")}, ("number",))
def simulate_state(host, char, number: int, distance: float = 50.0, picture: bool = False):
    from ..tools import simulate
    if number not in char.all_statedefs():
        raise ToolError(f"statedef {number} does not exist")
    sim = simulate.simulate(char, number, p2_dist=float(distance), max_ticks=900,
                            from_ground=simulate.starts_on_ground(char, number))
    fd = simulate.frame_data(sim)
    events = []
    for e in sim.events:
        kind = _EVENTS.get(e["kind"])
        if kind:
            ev = {"tick": e["t"], "kind": kind, "who": e.get("who")}
            for k in ("value", "label", "damage", "p2state"):
                if e.get(k) not in (None, ""):
                    ev[k] = e[k]
            events.append(ev)
    out = {"ticks": sim.tick, "startup": fd["startup"], "active": fd["active"], "recovery": fd["recovery"],
           "total": fd["total"], "outcome": _OUTCOME.get(fd["outcome"], fd["outcome"]),
           "advantage": fd["advantage"], "knockdown": fd["knockdown"], "events": events[:200],
           "problems": simulate.audit(sim)[:50]}
    out.update(_path(sim))
    if getattr(sim, "unsupported", None):
        out["not_simulated"] = dict(sim.unsupported)
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)
    if picture:
        return Result(text, [_png(simulate.sheet(sim, per_frame=True))])
    return text
