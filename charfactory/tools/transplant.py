"""Trazer partes de outro personagem: animações, grupos de som e golpes inteiros.

A partir de um estado (o golpe), segue os estados que ele chama (ChangeState,
Helper, p1/p2stateno), junta as animações, os grupos de sprite e de som que eles usam e o
comando do .cmd, e copia tudo renumerando por deslocamentos para não pisar no que já existe.
"""
from __future__ import annotations

import copy
import re

from .. import project
from ..formats import air, snd as sndfmt
from ..project import Character
from . import charkit

_STATE_KEYS = ("value",)                       # de ChangeState/SelfState/TargetState
_STATE_CTRLS = {"changestate", "selfstate", "targetstate"}
_STATE_PARAMS = {"p1stateno", "p2stateno", "stateno"}
_ANIM_PARAMS = {"anim", "projanim", "projhitanim", "projremanim", "projcancelanim"}
_ANIM_CTRLS = {"changeanim", "changeanim2"}
_SOUND_PARAMS = {"hitsound", "guardsound"}
_COMMON = set(range(0, 200)) | set(range(5000, 5200))   # estados comuns do common1: não se copiam


def _int(v: str | None) -> int | None:
    m = re.match(r"^\s*(-?\d+)\s*(?:;.*)?$", v or "")
    return int(m[1]) if m else None


def _snd_group(v: str | None) -> int | None:
    m = re.match(r"^\s*(\d+)\s*,", v or "")           # 'S' ou 'F' na frente = outro arquivo
    return int(m[1]) if m else None


def analyze(src: Character, root: int) -> dict:
    """Estados, animações, grupos de sprite e grupos de som de um golpe e do que ele chama."""
    sds = src.all_statedefs(include_common=False)
    states: list[int] = []
    todo = [root]
    anims: set[int] = set()
    sounds: set[int] = set()
    while todo:
        n = todo.pop()
        if n in states or n not in sds or (n in _COMMON and n != root):
            continue
        states.append(n)
        sd = sds[n][0]
        a = _int(sd.section.get("anim"))
        if a is not None:
            anims.add(a)
        for c in sd.controllers:
            t = c.type.lower()
            for e in c.section.entries():
                k = e.key.lower().replace(" ", "")
                if (t in _STATE_CTRLS and k == "value") or k in _STATE_PARAMS:
                    v = _int(e.value)
                    if v is not None:
                        todo.append(v)
                if (t in _ANIM_CTRLS and k == "value") or (k in _ANIM_PARAMS and not e.value.strip().upper().startswith("F")):
                    v = _int(e.value)
                    if v is not None:
                        anims.add(v)
                if (t == "playsnd" and k == "value") or k in _SOUND_PARAMS:
                    g = _snd_group(e.value)
                    if g is not None:
                        sounds.add(g)
    anims &= set(src.anims.actions)
    groups = sorted({f.group for a in anims for f in src.anims.get(a).frames if f.group >= 0}
                    & set(src.sprites.groups()))
    snd_groups = sorted(sounds & {s.group for s in src.sounds.sounds})
    commands = []
    for st in [src.commands.states]:
        sd = st.statedefs.get(-1)
        for c in (sd.controllers if sd else []):
            if c.type.lower() == "changestate" and _int(c.section.get("value")) == root:
                commands.append(c)
    names = {n.lower() for c in commands for ex in c.triggers().values() for e in ex
             for n in re.findall(r'command\s*=\s*"([^"]+)"', e, re.I)}
    cmd_defs = [c for c in src.commands.commands() if c.name.lower() in names]
    return {"states": sorted(states), "anims": sorted(anims), "sprite_groups": groups,
            "sound_groups": snd_groups, "entries": commands, "commands": cmd_defs}


def collisions(dst: Character, info: dict, state_off: int, anim_off: int, sprite_off: int, sound_off: int) -> dict:
    sds = dst.all_statedefs(include_common=False)
    return {"states": [n + state_off for n in info["states"] if n + state_off in sds],
            "anims": [a + anim_off for a in info["anims"] if dst.anims.get(a + anim_off) is not None],
            "sprite_groups": [g + sprite_off for g in info["sprite_groups"] if g + sprite_off in dst.sprites.groups()],
            "sound_groups": [g + sound_off for g in info["sound_groups"]
                             if any(s.group == g + sound_off for s in dst.sounds.sounds)]}


def _renumber_text(text: str, states: dict[int, int], anims: dict[int, int], sounds: dict[int, int]) -> str:
    """Troca números de estado, animação e grupo de som no texto CNS dos estados copiados."""
    out = []
    ctrl = ""
    for line in text.split("\n"):
        code, sep, comment = line.partition(";")
        m = re.match(r"^(\s*\[\s*(?:statedef|state)\s+)(-?\d+)(.*)$", code, re.I)
        if m:
            n = int(m[2])
            code = f"{m[1]}{states.get(n, n)}{m[3]}"
            ctrl = ""
            out.append(code + sep + comment)
            continue
        kv = re.match(r"^(\s*)([A-Za-z0-9_.]+)(\s*=\s*)(.*)$", code)
        if kv:
            key, val = kv[2].lower(), kv[4]
            if key == "type":
                ctrl = val.strip().lower()
            elif (ctrl in _STATE_CTRLS and key == "value") or key in _STATE_PARAMS:
                val = re.sub(r"^(\s*)(-?\d+)", lambda x: f"{x[1]}{states.get(int(x[2]), int(x[2]))}", val)
            elif (ctrl in _ANIM_CTRLS and key == "value") or (key in _ANIM_PARAMS and not val.strip().upper().startswith("F")):
                val = re.sub(r"^(\s*)(-?\d+)", lambda x: f"{x[1]}{anims.get(int(x[2]), int(x[2]))}", val)
            elif (ctrl == "playsnd" and key == "value") or key in _SOUND_PARAMS:
                val = re.sub(r"^(\s*S?)(\d+)(\s*,)", lambda x: f"{x[1]}{sounds.get(int(x[2]), int(x[2]))}{x[3]}", val)
            if key.startswith("trigger"):
                val = re.sub(r"(?i)\b(stateno\s*=\s*)(\d+)", lambda x: f"{x[1]}{states.get(int(x[2]), int(x[2]))}", val)
                val = re.sub(r"(?i)\b(anim\s*=\s*)(\d+)", lambda x: f"{x[1]}{anims.get(int(x[2]), int(x[2]))}", val)
                val = re.sub(r"(?i)\b((?:self)?animexist\s*\(\s*)(\d+)", lambda x: f"{x[1]}{anims.get(int(x[2]), int(x[2]))}", val)
            code = f"{kv[1]}{kv[2]}{kv[3]}{val}"
        out.append(code + sep + comment)
    return "\n".join(out)


def import_anims(dst: Character, src: Character, numbers: list[int], offset: int = 0,
                 sprite_offset: int | None = None, src_def=None) -> dict:
    """Copia ações do AIR de outro char (número + offset). Com `sprite_offset`, traz também os
    grupos de sprite usados (grupo + sprite_offset) e ajusta os frames."""
    groups = sorted({f.group for n in numbers if src.anims.get(n) for f in src.anims.get(n).frames
                     if f.group >= 0} & set(src.sprites.groups()))
    if sprite_offset is not None and groups:
        charkit.import_groups(dst, src_def or src.def_path, groups, sprite_offset, make_anims=False)
    done = []
    for n in numbers:
        a = src.anims.get(n)
        if a is None:
            continue
        new = copy.deepcopy(a)
        new.number = n + offset
        new._raw = new._snapshot = new._comments = None
        if sprite_offset:
            for f in new.frames:
                if f.group in groups:
                    f.group += sprite_offset
        dst.anims.set_action(new)
        done.append(new.number)
    if done:
        dst.mark("anims")
    return {"anims": done, "sprite_groups": [g + (sprite_offset or 0) for g in groups] if sprite_offset is not None else []}


def import_sound_groups(dst: Character, src: Character, groups: list[int], offset: int = 0) -> dict:
    n = 0
    for s in src.sounds.sounds:
        if s.group in groups:
            sndfmt.set_sound(dst.sounds, s.group + offset, s.number, s.wav)
            n += 1
    if n:
        dst.mark("sounds")
    return {"sounds": n}


def transplant(dst: Character, src_def, root: int, state_offset: int = 0, anim_offset: int = 0,
               sprite_offset: int = 0, sound_offset: int = 0, with_command: bool = True) -> dict:
    """Copia um golpe de outro personagem para `dst` com tudo o que ele usa (ver o módulo)."""
    src = project.load(src_def)
    info = analyze(src, root)
    smap = {n: n + state_offset for n in info["states"]}
    amap = {a: a + anim_offset for a in info["anims"]}
    gmap = {g: g + sound_offset for g in info["sound_groups"]}
    # sprites e animações
    charkit.import_groups(dst, src_def, info["sprite_groups"], sprite_offset, make_anims=False)
    for a in info["anims"]:
        new = copy.deepcopy(src.anims.get(a))
        new.number = amap[a]
        new._raw = new._snapshot = new._comments = None
        for f in new.frames:
            if f.group in info["sprite_groups"]:
                f.group += sprite_offset
        dst.anims.set_action(new)
    dst.mark("anims")
    import_sound_groups(dst, src, info["sound_groups"], sound_offset)
    # estados
    sds = src.all_statedefs(include_common=False)
    text = "\n\n".join(sds[n][0].text() for n in info["states"])
    text = _renumber_text(text, smap, amap, gmap)
    st = dst.states_file_for(smap[root])
    from ..formats import cns, mtext
    new_doc = mtext.parse(text)
    for n, sd in cns.States(new_doc).statedefs.items():
        st.set_statedef_text(n, sd.text())
    dst.mark("states")
    # comando e entrada no [Statedef -1]
    added_cmds = []
    if with_command and info["entries"]:
        have = {c.name.lower() for c in dst.commands.commands()}
        for c in info["commands"]:
            if c.name.lower() not in have:
                t = c.section.get("time")
                dst.commands.add_command(c.name, c.sequence, int(t) if t and t.strip().isdigit() else None)
                added_cmds.append(c.name)
        cst = dst.commands.states
        if -1 not in cst.statedefs:
            cst.set_statedef_text(-1, "[Statedef -1]\n")
        for e in info["entries"]:
            cst.add_controller(-1, _renumber_text(e.section.text(), smap, amap, gmap))
        dst.mark("commands")
    return {"states": [smap[n] for n in info["states"]], "anims": [amap[a] for a in info["anims"]],
            "sprite_groups": [g + sprite_offset for g in info["sprite_groups"]],
            "sound_groups": [gmap[g] for g in info["sound_groups"]], "commands": added_cmds}
