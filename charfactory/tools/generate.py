"""Geradores: controllers a partir da gramática, arremesso (throw), move list HTML,
personagem novo."""
from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from ..formats import mtext
from ..project import Character
from ..validate import grammar
from ..i18n import tr

# ================================================================ controllers


def controller_template(ctype: str, statedef: int = 0, label: str | None = None,
                        optional: bool = False) -> str:
    """Esqueleto de um [State] com os parâmetros obrigatórios (e opcionais, se pedido)."""
    spec = grammar()["controllers"].get(ctype.lower())
    if spec is None:
        raise ValueError(tr("unknown controller '{0}'").format(ctype))
    lines = [f"[State {statedef}, {label or spec['name']}]", f"type = {spec['name']}",
             "trigger1 = Time = 0"]
    names = list(spec["required"])
    for group in spec.get("required_any", []):
        names.append(group[0])
    if optional:
        names += [p for p in spec["params"] if p not in names]
    for p in names:
        syntax = spec["params"].get(p, {}).get("syntax", "")
        hint = syntax.split("=", 1)[1].strip() if "=" in syntax else ""
        if p in spec["required"] or any(p == g[0] for g in spec.get("required_any", [])):
            lines.append(f"{p} = ;{hint} (required)")
        else:
            lines.append(f";{p} = ;{hint}")
    return "\n".join(lines) + "\n"


def describe_controller(ctype: str) -> dict:
    spec = grammar()["controllers"].get(ctype.lower())
    if spec is None:
        raise ValueError(tr("unknown controller '{0}'").format(ctype))
    return spec


def search_reference(term: str) -> dict:
    """Busca controllers e triggers por nome ou descrição."""
    t = term.lower()
    g = grammar()
    ctrl = [{"name": c["name"], "description": c["description"][:200]}
            for c in g["controllers"].values() if t in c["name"].lower() or t in c["description"].lower()]
    trig = [{"name": x["name"], "description": x["description"][:200]}
            for x in g["triggers"].values() if t in x["name"].lower() or t in x["description"].lower()]
    return {"controllers": ctrl[:30], "triggers": trig[:30]}


# ================================================================ arremesso


def throw_states(statedef: int, p1_anim: int, p2_anim: int, binds: list[dict],
                 damage: int = 80, release_vel: tuple[float, float] = (-4.0, -6.0),
                 attempt_anim: int | None = None, command: str = "y",
                 distance: int = 5, power_add: int = 60) -> dict:
    """Gera os estados de um arremesso.

    binds: [{"elem": 1, "x": 30, "y": 0, "sprpriority": 1}] — posição de P2 relativa a P1
    em cada elemento da animação p1_anim. P2 é solto depois do último bind.

    Estados: N (tentativa, HitDef de arremesso), N+1 (P1 arremessando),
    N+10 (P2 preso, usa a animação p2_anim do AIR de P1), N+11 (P2 voando).
    """
    if not binds:
        raise ValueError(tr("set at least one bind point"))
    n, held, flying = statedef, statedef + 10, statedef + 11
    last = max(b["elem"] for b in binds)
    vx, vy = release_vel
    a_anim = attempt_anim if attempt_anim is not None else p1_anim
    parts = [f"""[Statedef {n}]
type = S
movetype = A
physics = S
juggle = 0
velset = 0,0
ctrl = 0
anim = {a_anim}

[State {n}, Grab]
type = HitDef
trigger1 = Time = 0
attr = S, NT
hitflag = M-
priority = 1, Miss
sparkno = -1
guard.dist = 0
p1facing = IfElse(command = "holdback", -1, 1)
p2facing = 1
p1stateno = {n + 1}
p2stateno = {held}
p2getp1state = 1
fall = 1

[State {n}, Miss]
type = ChangeState
trigger1 = AnimTime = 0
value = 0
ctrl = 1

[Statedef {n + 1}]
type = S
movetype = A
physics = N
anim = {p1_anim}
poweradd = {power_add}
"""]
    for b in sorted(binds, key=lambda b: b["elem"]):
        if b.get("sprpriority") is not None:
            parts.append(f"""[State {n + 1}, Priority {b['elem']}]
type = SprPriority
trigger1 = AnimElem = {b['elem']}
value = {b['sprpriority']}
""")
        parts.append(f"""[State {n + 1}, Bind {b['elem']}]
type = TargetBind
trigger1 = AnimElem = {b['elem']}
pos = {b['x']}, {b['y']}
""")
    parts.append(f"""[State {n + 1}, Damage]
type = TargetLifeAdd
trigger1 = AnimElem = {last}
value = -{abs(damage)}

[State {n + 1}, Release]
type = TargetState
trigger1 = AnimElem = {last}
value = {flying}

[State {n + 1}, End]
type = ChangeState
trigger1 = AnimTime = 0
value = 0
ctrl = 1

[Statedef {held}]
type = A
movetype = H
physics = N
velset = 0,0

[State {held}, Anim]
type = ChangeAnim2
trigger1 = Time = 0
value = {p2_anim}

[Statedef {flying}]
type = A
movetype = H
physics = N
velset = {vx}, {vy}

[State {flying}, Gravity]
type = VelAdd
trigger1 = 1
y = .45

[State {flying}, Air recovery]
type = SelfState
triggerall = Vel Y > 0
triggerall = Alive
triggerall = CanRecover
trigger1 = Command = "recovery"
value = 5210

[State {flying}, Ground]
type = SelfState
trigger1 = Vel Y > 0
trigger1 = Pos Y >= 0
value = 5100
""")
    cmd = f"""[State -1, Throw]
type = ChangeState
value = {n}
triggerall = StateType = S
triggerall = Ctrl
triggerall = Command = "{command}"
triggerall = Command = "holdfwd" || Command = "holdback"
trigger1 = P2BodyDist X < {distance}
trigger1 = P2StateType != A
trigger1 = P2MoveType != H
"""
    return {"states": "\n".join(parts), "cmd_trigger": cmd,
            "statedefs": [n, n + 1, held, flying]}


# ================================================================ move list HTML

_ARROWS = {"B": "←", "DB": "↙", "D": "↓", "DF": "↘", "F": "→", "UF": "↗", "U": "↑", "UB": "↖"}


def pretty_command(seq: str) -> str:
    out = []
    for step in seq.split(","):
        parts = []
        for p in step.strip().split("+"):
            p = p.strip()
            mod = re.match(r"^([~$/>]*\d*)(.*)$", p)
            prefix, sym = mod[1], mod[2]
            glyph = _ARROWS.get(sym.upper(), sym.upper()) if sym.upper() in _ARROWS else sym.upper()
            note = ""
            if "~" in prefix:
                note = tr("release ")
            if "/" in prefix:
                note = tr("hold ")
            parts.append(note + glyph)
        out.append("+".join(parts))
    return " ".join(out)


def movelist(char: Character) -> list[dict]:
    """Golpes = ChangeState do [Statedef -1] com os comandos usados nos triggers."""
    cmds = {c.name.lower(): c.sequence for c in char.commands.commands()}
    moves = []
    sd = char.all_statedefs(include_common=False).get(-1)    # no CMD ou, em ZSS, num .zss
    sd = sd[0] if sd else None
    slots: dict[str, str] = {}             # ZSS: condição de bloco guardada em zsslocal(n)
    for c in (sd.controllers if sd else []):
        if c.type.lower() == "varset":
            for e in c.section.entries():
                if e.key.lower().startswith("zsslocal("):
                    slots[e.key.lower()] = e.value
        if c.type.lower() != "changestate":
            continue
        names = []
        for exprs in c.triggers().values():
            for e in exprs:
                e = re.sub(r"zsslocal\(\d+\)", lambda m: slots.get(m[0].lower(), ""), e, flags=re.I)
                names += re.findall(r'command\s*=\s*"([^"]+)"', e, re.I)
        names = [x for x in dict.fromkeys(names) if x.lower() not in ("holdfwd", "holdback", "holdup", "holddown")]
        if not names:
            continue
        moves.append({"name": re.sub(r"\s*@\d+$", "", c.label), "state": c.section.get("value"),
                      "commands": [{"name": n, "sequence": cmds.get(n.lower(), "?"),
                                    "pretty": pretty_command(cmds.get(n.lower(), ""))} for n in names]})
    return moves


def movelist_html(char: Character, path: str | Path | None = None) -> str:
    rows = "\n".join(
        f"<tr><td>{html.escape(m['name'])}</td><td>"
        + "<br>".join(html.escape(c["pretty"]) for c in m["commands"])
        + "</td><td class=s>" + html.escape(str(m["state"])) + "</td></tr>"
        for m in movelist(char))
    from ..i18n import language
    lang = language().replace("_", "-").lower()
    title = tr("{0}: moves").format(html.escape(char.name))
    by = tr("by {0}").format(html.escape(char.info("author")))
    head = "".join(f"<th>{h}</th>" for h in (tr("Move"), tr("Command"), tr("State")))
    doc = f"""<!doctype html><html lang="{lang}"><meta charset="utf-8">
<title>{title}</title>
<style>body{{font:15px system-ui;margin:2em;background:#15151c;color:#eee}}
table{{border-collapse:collapse}}td,th{{padding:.4em .8em;border-bottom:1px solid #333;text-align:left}}
.s{{color:#888}}h1{{margin:0}}p{{color:#aaa}}</style>
<h1>{html.escape(char.name)}</h1><p>{by}</p>
<table><tr>{head}</tr>
{rows}
</table></html>"""
    if path:
        Path(path).write_text(doc, "utf-8")
    return doc


# ================================================================ personagem novo

_EMPTY_CNS = """[Data]
life = 1000
power = 3000
attack = 100
defence = 100
fall.defence_up = 50
liedown.time = 60
airjuggle = 15
sparkno = 2
guard.sparkno = 40
KO.echo = 0
volumescale = 100
IntPersistIndex = 60
FloatPersistIndex = 40

[Size]
xscale = 1
yscale = 1
ground.back = 15
ground.front = 16
air.back = 12
air.front = 12
height = 60
attack.dist = 160
proj.attack.dist = 90
proj.doscale = 0
head.pos = -5, -90
mid.pos = -5, -60
shadowoffset = 0
draw.offset = 0,0

[Velocity]
walk.fwd  = 2.4
walk.back = -2.2
run.fwd  = 4.6, 0
run.back = -4.5,-3.8
jump.neu = 0,-8.4
jump.back = -2.55
jump.fwd = 2.5
runjump.back = -2.55,-8.1
runjump.fwd = 4,-8.1
airjump.neu = 0,-8.1
airjump.back = -2.55
airjump.fwd = 2.5
air.gethit.groundrecover = -.15,-3.5
air.gethit.airrecover.mul = .5,.2
air.gethit.airrecover.add = 0,-4.5
air.gethit.airrecover.back = -1
air.gethit.airrecover.fwd = 0
air.gethit.airrecover.up = -2
air.gethit.airrecover.down = 1.5

[Movement]
airjump.num = 1
airjump.height = 35
yaccel = .44
stand.friction = .85
crouch.friction = .82
stand.friction.threshold = 2
crouch.friction.threshold = .05
air.gethit.groundlevel = 25
air.gethit.groundrecover.ground.threshold = -20
air.gethit.groundrecover.groundlevel = 10
air.gethit.airrecover.threshold = -1
air.gethit.airrecover.yaccel = .35
air.gethit.trip.groundlevel = 15
down.bounce.offset = 0, 20
down.bounce.yaccel = .4
down.bounce.groundlevel = 12
down.friction.threshold = .05
"""

_EMPTY_CMD = """[Remap]
x = x
y = y
z = z
a = a
b = b
c = c
s = s

[Defaults]
command.time = 15
command.buffer.time = 1

[Command]
name = "holdfwd"
command = /$F
time = 1

[Command]
name = "holdback"
command = /$B
time = 1

[Command]
name = "holdup"
command = /$U
time = 1

[Command]
name = "holddown"
command = /$D
time = 1

[Command]
name = "recovery"
command = x+y
time = 1

[Command]
name = "x"
command = x
time = 1

[Command]
name = "y"
command = y
time = 1

[Statedef -1]
"""


def new_character(dest_dir: str | Path, name: str, template_def: str | Path | None = None,
                  author: str = "") -> Path:
    """Cria um personagem. Com template_def, copia o personagem-modelo renomeando os
    arquivos; sem modelo, cria um esqueleto vazio (constantes padrão, CMD básico)."""
    dest = Path(dest_dir)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(tr("the folder {0} already exists and is not empty").format(dest))
    dest.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "", name.replace(" ", "_")) or "char"
    if template_def:
        src = Character(template_def)
        renamed: dict[str, str] = {}
        for f in src.dir.iterdir():
            if f.is_file() and f.suffix.lower() in (".def", ".sff", ".air", ".cmd", ".cns", ".st",
                                                   ".snd", ".act", ".ai", ".txt"):
                new_name = f.name.replace(src.def_path.stem, slug, 1) if f.stem.startswith(src.def_path.stem) else f.name
                shutil.copy2(f, dest / new_name)
                renamed[f.name.lower()] = new_name
        def_path = dest / f"{slug}.def"
        if not def_path.exists():
            shutil.copy2(src.def_path, def_path)
        doc = mtext.load(def_path)
        files = doc.find("files")
        if files:
            for e in files.entries():
                v = e.value.strip().strip('"')
                if v.lower() in renamed:
                    files.set(e.key, renamed[v.lower()])
    else:
        (dest / f"{slug}.cns").write_text(_EMPTY_CNS.replace("\n", "\r\n"), "cp1252")
        (dest / f"{slug}.cmd").write_text(_EMPTY_CMD.replace("\n", "\r\n"), "cp1252")
        (dest / f"{slug}.air").write_text("", "cp1252")
        from ..formats import sff, snd
        sff.SFF().save(dest / f"{slug}.sff")
        snd.save(snd.new(), dest / f"{slug}.snd")
        def_path = dest / f"{slug}.def"
        doc = mtext.parse(
            "[Info]\r\nname = \"\"\r\ndisplayname = \"\"\r\nversiondate = 01,01,2026\r\n"
            "mugenversion = 1.0\r\nauthor = \"\"\r\npal.defaults = 1\r\nlocalcoord = 320,240\r\n\r\n"
            f"[Files]\r\ncmd = {slug}.cmd\r\ncns = {slug}.cns\r\nst = {slug}.cns\r\n"
            f"stcommon = common1.cns\r\nsprite = {slug}.sff\r\nanim = {slug}.air\r\nsound = {slug}.snd\r\n",
            "cp1252")
    info = doc.section("Info")
    info.set("name", f'"{name}"')
    info.set("displayname", f'"{name}"')
    if author:
        info.set("author", f'"{author}"')
    doc.save(def_path)
    return def_path


# ================================================================ PalFX

def load_palfx_preset(path: str | Path) -> dict:
    """Lê um preset de PalFX (XML de preset)."""
    import xml.etree.ElementTree as ET
    r = ET.parse(path).getroot()

    def rgb(tag, default):
        e = r.find(tag)
        return [int(e.get(c, d)) for c, d in zip(("red", "green", "blue"), default)] if e is not None else list(default)

    sin = r.find("sin")
    inv = r.find("invert")
    col = r.find("color")
    return {"add": rgb("add", (0, 0, 0)), "mul": rgb("mul", (256, 256, 256)),
            "sinadd": rgb("sin", (0, 0, 0)) + [int(sin.get("period", 0)) if sin is not None else 0],
            "color": int(col.get("value", 256)) if col is not None else 256,
            "invertall": inv is not None and inv.get("value", "false").lower() in ("1", "true")}


def palfx_controller(statedef: int, time: int = 10, add=(0, 0, 0), mul=(256, 256, 256),
                     sinadd=(0, 0, 0, 0), color: int = 256, invertall: bool = False,
                     trigger: str = "Time = 0", ctype: str = "PalFX", label: str = "PalFX") -> str:
    lines = [f"[State {statedef}, {label}]", f"type = {ctype}", f"trigger1 = {trigger}",
             f"time = {time}", f"add = {add[0]},{add[1]},{add[2]}", f"mul = {mul[0]},{mul[1]},{mul[2]}"]
    if any(sinadd[:3]) and len(sinadd) > 3 and sinadd[3]:
        lines.append(f"sinadd = {sinadd[0]},{sinadd[1]},{sinadd[2]},{sinadd[3]}")
    if color != 256:
        lines.append(f"color = {color}")
    if invertall:
        lines.append("invertall = 1")
    return "\n".join(lines) + "\n"
