"""Recursos não usados: sprites, animações e sons que nada referencia.

Uma referência conta quando o número é literal:
- sprite: usado por algum frame de alguma animação (o grupo 9000, retratos, é do motor);
- animação: `anim` de statedef/Explod/Helper/Projectile, `value` de ChangeAnim/ChangeAnim2,
  `proj*anim`, animações exigidas pelos estados comuns e as faixas reservadas do MUGEN
  (0–199 movimentos básicos, 5000–5999 tomar golpe), que o motor escolhe sozinho;
- som: `value` de PlaySnd sem F, `hitsound`/`guardsound` com S, também nos estados comuns.
Referências calculadas (expressões como `value = 200 + var(1)`) não podem ser resolvidas
estaticamente e são contadas em `dynamic_references`: se houver alguma, confira antes de apagar.
"""
from __future__ import annotations

import re

from ..project import Character, ProjectError
from ..validate import required_anims
from ..i18n import tr

PORTRAIT_GROUP = 9000
RESERVED_ANIMS = (range(0, 200), range(5000, 6000))

_ANIM_KEYS = {"anim", "projanim", "projhitanim", "projremanim", "projcancelanim"}
_INT = re.compile(r"^\s*(-?\d+)\s*$")
_SND = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")
_OWN_SND = re.compile(r"^\s*S\s*(\d+)\s*,\s*(\d+)\s*$", re.I)


def references(char: Character) -> dict:
    """Animações e sons referenciados pelos estados (do personagem e comuns)."""
    anims: set[int] = set()
    sounds: set[tuple[int, int]] = set()
    dynamic: list[str] = []
    files = [*char.states, char.commands.states]
    if char.common_states:
        files.append(char.common_states)
    for st in files:
        for n, sd in st.statedefs.items():
            v = sd.section.get("anim")
            if v:
                m = _INT.match(v)
                (anims.add(int(m[1])) if m else dynamic.append(f"[Statedef {n}] anim = {v.strip()}"))
            for c in sd.controllers:
                t = c.type.lower()
                for e in c.section.entries():
                    key, val = e.key.lower(), (e.value or "").split(";")[0]
                    where = f"[State {n}, {c.label}] {e.key} = {val.strip()}"
                    if key in _ANIM_KEYS or (key == "value" and t in ("changeanim", "changeanim2")):
                        if val.strip().upper().startswith("F"):
                            continue  # animação do fightfx, não do personagem
                        m = _INT.match(val)
                        (anims.add(int(m[1])) if m else dynamic.append(where))
                    elif key == "value" and t == "playsnd":
                        if val.strip().upper().startswith("F"):
                            continue
                        m = _SND.match(val)
                        (sounds.add((int(m[1]), int(m[2]))) if m else dynamic.append(where))
                    elif key in ("hitsound", "guardsound"):
                        m = _OWN_SND.match(val)
                        if m:
                            sounds.add((int(m[1]), int(m[2])))
    return {"anims": anims, "sounds": sounds, "dynamic": dynamic}


def unused_resources(char: Character) -> dict:
    refs = references(char)
    try:
        actions = char.anims.actions
    except ProjectError:
        actions = {}
    used_sprites = {(f.group, f.item) for a in actions.values() for f in a.frames}
    sprites = [[s.group, s.item] for s in char.sprites.sprites
               if s.group != PORTRAIT_GROUP and (s.group, s.item) not in used_sprites]
    keep = refs["anims"] | required_anims(char)
    animations = [n for n in sorted(actions)
                  if n not in keep and not any(n in r for r in RESERVED_ANIMS)]
    sounds = [[s.group, s.number] for s in char.sounds.sounds if (s.group, s.number) not in refs["sounds"]]
    return {"sprites": sprites, "animations": animations, "sounds": sounds,
            "dynamic_references": len(refs["dynamic"]), "dynamic_examples": refs["dynamic"][:10],
            "notes": tr("sprites in group 9000 and animations 0–199/5000–5999 belong to the engine and are not listed")}
