"""Golpes como dados: um arquivo TOML descreve cada golpe e este módulo gera a animação
(com Clsn1/Clsn2 automáticas), o statedef (física, custo, sons, voz, HitDefs, VFX,
super pause, cancelamentos, fim) e os comandos + [Statedef -1].

Mudar um golpe = editar algumas linhas do TOML e rodar de novo (idempotente: refaz só o
que o arquivo descreve; os controladores do -1 gerados têm o rótulo "spec:" e são
substituídos a cada execução).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import numpy as np

from ..formats import air
from ..project import Character
from . import ops
from ..i18n import tr

KINDS = {"normal", "special", "super", "air", "air_special", "throw"}


# ====================================================================== caixas automáticas

def _mask(char: Character, g: int, i: int, colors=None):
    s = char.sprites
    spr = s.find(g, i)
    if spr is None:
        return None
    idx = np.frombuffer(s.pixels(spr), np.uint8).reshape(spr.height, spr.width)
    solid = idx > 0
    weapon = np.zeros_like(solid)
    if colors:
        pal = s.palettes[spr.palette_index].colors
        want = {tuple(c) for c in colors}
        weapon = np.isin(idx, [k for k, c in enumerate(pal) if tuple(c) in want])
    return solid, weapon, spr.axis_x, spr.axis_y


def clsn2_auto(char: Character, g: int, i: int, colors=None) -> list[tuple]:
    """Duas caixas de corpo (metade de cima e de baixo), sem a arma/rastro."""
    m = _mask(char, g, i, colors)
    if m is None:
        return []
    solid, weapon, ax, ay = m
    body = solid & ~weapon
    ys, xs = np.nonzero(body)
    if not xs.size:
        return []
    top, bot = ys.min(), ys.max()
    mid = top + (bot - top) // 2
    out = []
    for y0, y1 in ((top, mid), (mid, bot + 1)):
        cols = np.nonzero(body[y0:y1].sum(axis=0) >= 2)[0]
        if not cols.size:
            continue
        x0, x1 = max(cols.min() - ax, -20), min(cols.max() - ax, 26)
        if x1 - x0 < 10:
            c = (x0 + x1) // 2
            x0, x1 = c - 5, c + 5
        out.append((int(x0), int(y0 - ay), int(x1), int(min(y1 - 1 - ay, 0))))
    return out


def clsn1_auto(char: Character, g: int, i: int, colors=None, strips: int = 4,
               reach_back: int = -4) -> list[tuple]:
    """Caixas de ataque em faixas verticais seguindo a arma/rastro (pixels nas `colors`);
    sem arma no quadro, a ponta do membro mais à frente."""
    m = _mask(char, g, i, colors)
    if m is None:
        return []
    solid, weapon, ax, ay = m
    sel = weapon.copy()
    sel[:, :max(0, ax + reach_back)] = False
    if sel.sum() < 15:
        body = solid & ~weapon
        ys, xs = np.nonzero(body)
        tip = xs.max() if xs.size else ax
        sel = body.copy()
        sel[:, :max(ax + 9, tip - 16)] = False
        if sel.sum() < 6:
            sel = solid.copy()
            sel[:, :ax + 4] = False
    ys, xs = np.nonzero(sel)
    if not xs.size:
        return []
    n = max(1, min(strips, (xs.max() - xs.min()) // 18 + 1))
    edges = np.linspace(xs.min(), xs.max() + 1, n + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        k = (xs >= a) & (xs < b)
        if k.sum() >= 3:
            out.append((int(xs[k].min() - ax), int(ys[k].min() - ay),
                        int(xs[k].max() - ax), int(ys[k].max() - ay)))
    return out


# ====================================================================== geração

def _ctrl(kind: str, label: str, **kv) -> str:
    lines = [f"[State 0, {label}]", f"type = {kind}"]
    for k, v in kv.items():
        k = k.replace("__", ".")
        if isinstance(v, (list, tuple)):
            if k.startswith("trigger"):
                lines += [f"{k} = {x}" for x in v]
            else:
                lines.append(f"{k} = {', '.join(str(x) for x in v)}")
        elif v is not None:
            lines.append(f"{k} = {v}")
    return "\n".join(lines) + "\n\n"


def _pair(v, default="0, 0"):
    if v is None:
        return default
    return ", ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v)


# Clsn1 típica de golpe, em alturas do char parado: frente ~0,9, largura ~1,4, altura ~0,86.
# A caixa só sobre a ponta do membro fica estreita demais e quase não acerta.
CLSN1_FRONT = {"special": 0.85, "air_special": 0.85, "super": 0.85}


def grow_clsn1(boxes: list[tuple], height: float, kind: str = "normal") -> list[tuple]:
    """Uma caixa no tamanho típico em volta das caixas automáticas: começa no eixo, vai até a
    ponta do golpe + folga (no mínimo 0,7 h; 0,85 h em especial/super, nunca mais que
    0,35 h além da ponta — 0,5 h em especial/super) e tem ao menos 0,6 h de altura, sem descer do chão."""
    if not boxes or height <= 0:
        return boxes
    x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
    if x1 <= 0:                                  # golpe para trás: não mexe
        return boxes
    base = CLSN1_FRONT.get(kind, 0.7) * height
    cap = 0.5 if kind in CLSN1_FRONT else 0.35         # especial/super: mais folga
    nx1 = int(round(min(max(x1 + 0.12 * height, base), x1 + cap * height)))
    nx0 = min(x0, 0)
    hmin = 0.6 * height
    if y1 - y0 < hmin:
        cy = (y0 + y1) / 2
        y0, y1 = cy - hmin / 2, cy + hmin / 2
        if y1 > 0:
            y0, y1 = y0 - y1, 0
    return [(int(nx0), int(round(y0)), nx1, int(round(y1)))]


def build_anim(char: Character, move: dict, colors=None, clsn1_height: float | None = None) -> int:
    """frames: [[g, i, ticks, "hit"?, {x,y,flip,trans,clsn1,clsn2}?], ...]
    clsn1_height: altura do char parado; com ela a Clsn1 automática cresce ao tamanho típico."""
    number = int(move.get("anim", move["state"]))
    lines = [f"[Begin Action {number}]"]
    loop = int(move.get("loop", 0))
    for k, fr in enumerate(move["frames"]):
        g, i, t = int(fr[0]), int(fr[1]), int(fr[2])
        flags = [x for x in fr[3:] if isinstance(x, str)]
        opt = next((x for x in fr[3:] if isinstance(x, dict)), {})
        if loop and k == loop:
            lines.append("Loopstart")
        c2 = opt.get("clsn2", clsn2_auto(char, g, i, colors) if g >= 0 else [])
        c1 = opt.get("clsn1")
        if c1 is None:
            c1 = clsn1_auto(char, g, i, colors) if ("hit" in flags and g >= 0) else []
            if c1 and clsn1_height:
                c1 = grow_clsn1(c1, clsn1_height, move.get("kind", "normal"))
        for n, boxes in ((2, c2), (1, c1)):
            if boxes:
                lines.append(f"Clsn{n}: {len(boxes)}")
                lines += [f"  Clsn{n}[{j}] = {b[0]},{b[1]},{b[2]},{b[3]}" for j, b in enumerate(boxes)]
        line = f"{g},{i}, {opt.get('x', 0)},{opt.get('y', 0)}, {t}"
        if opt.get("flip") or opt.get("trans"):
            line += f", {opt.get('flip', '')}, {opt.get('trans', '')}"
        lines.append(line)
    char.anims.set_action(air.loads("\n".join(lines) + "\n").get(number))
    char.mark("anims")
    return number


def _hit_frames(char: Character, anim: int) -> list[int]:
    a = char.anims.get(anim)
    return [k + 1 for k, f in enumerate(a.frames) if f.clsn1] if a else []


def _when(x: dict) -> str:
    """Gatilho de um item de fase: `trigger` escrito, `elem` (quadro da anim) ou o 1º tick."""
    return x.get("trigger") or (f"animelem = {x['elem']}" if "elem" in x else "!time")


def _as_list(v) -> list:
    return v if isinstance(v, list) else [v] if v else []


def expand_phases(m: dict) -> list[dict]:
    """Golpe com `phases`: o próprio golpe é o 1º estado e cada fase é mais um estado.

    Uma fase tem os campos de um golpe (`anim`/`frames`, `hit`, `vfx`, `sounds`, `vel`,
    `velset`, `physics`, `extra`) e mais `name`, `state` (padrão: estado do golpe + posição),
    `next` (fase seguinte ao fim da anim), `on_hit` (troca de fase quando acerta), `helper = true`
    (estado de um helper, fora da sequência), `like` (copia uma fase anterior e muda campos),
    `helpers`, `palfx`, `assert`, `approach`, `bind_target`, `remove_vfx`, `angle`.
    `next`, `on_hit` e `helpers[].phase` aceitam o nome da fase, o número do estado ou "inicio".
    """
    phases = m.get("phases") or []
    if not phases:
        return [m]
    base = int(m["state"])
    names = {"inicio": base}
    built: list[dict] = []
    for k, p in enumerate(phases, 1):
        if p.get("like"):
            src = next((q for q in built if q.get("name") == p["like"]), None)
            if src is None:
                raise ValueError(tr("phase {0} of move {1}: like = {2!r} must be the name of an earlier phase").format(p.get('name', k), base, p['like']))
            p = {**{a: b for a, b in src.items() if a not in ("name", "state", "_phase_of")},
                 **{a: b for a, b in p.items() if a != "like"}}
        p = dict(p, kind=m.get("kind", "normal"), _phase_of=base)
        p.setdefault("state", base + k)
        if p.get("name"):
            names[p["name"]] = int(p["state"])
        built.append(p)

    def ref(x):
        if isinstance(x, int) or (isinstance(x, str) and x.lstrip("-").isdigit()):
            return int(x)
        if x not in names:
            raise ValueError(tr("move {0}: phase {1!r} does not exist (phases: {2})").format(base, x, ', '.join(names)))
        return names[x]

    root = {a: b for a, b in m.items() if a != "phases"}
    chain = [root] + [p for p in built if not p.get("helper")]
    for k, st in enumerate(chain):
        if "next" in st:
            st["_next"] = ref(st["next"])
        elif k + 1 < len(chain):
            st["_next"] = int(chain[k + 1]["state"])
    for st in [root] + built:
        oh = st.get("on_hit")
        if oh is not None:
            oh = oh if isinstance(oh, dict) else {"phase": oh}
            hid = oh.get("helper")
            st["_on_hit"] = {"state": ref(oh["phase"]), "helper": ref(hid) if hid is not None else None}
        st["helpers"] = [dict(h, **({"state": ref(h["phase"])} if "phase" in h else {}),
                              **({"id": ref(h["id"])} if "id" in h else {}))
                         for h in _as_list(st.get("helpers"))]
        if st is not root:
            st["name"] = f"{m.get('name', base)} / {st.get('name', st['state'])}"
    return [root] + built


def _phase_ctrls(move: dict) -> str:
    """Controladores das fases (helpers, PalFX, AssertSpecial, avanço, prender o oponente)."""
    out = ""
    for a in _as_list(move.get("assert")):
        a = a if isinstance(a, dict) else {"flags": [a]}
        flags = _as_list(a.get("flags"))
        kv = {("flag" if j == 0 else f"flag{j + 1}"): f for j, f in enumerate(flags)}
        out += _ctrl("AssertSpecial", "assert", trigger1=a.get("trigger", "1"), **kv, ignorehitpause=1)
    for a in _as_list(move.get("approach")):
        # avança até o oponente num quadro e para no seguinte (investida que encosta)
        gap, ticks = a.get("gap", 12), a.get("ticks", 14)
        out += _ctrl("VelSet", "move up to the opponent", trigger1=f"animelemno(0) = {a['elem']}",
                     x=f"ifelse(p2bodydist x > {gap}, (p2bodydist x - {gap}) / {float(ticks)}, 0)")
        out += _ctrl("VelSet", "stop", trigger1=f"animelemno(0) > {a['elem']}", x=0)
    for h in move.get("helpers", []):
        sc = h.get("scale")
        out += _ctrl("Helper", h.get("label", "helper"), trigger1=_when(h), stateno=h["state"],
                     id=h.get("id", h["state"]), pos=_pair(h.get("pos", [0, 0])),
                     postype=h.get("postype", "p1"), facing=h.get("facing"), ownpal=1,
                     size__xscale=sc[0] if sc else None, size__yscale=sc[1] if sc else None,
                     supermovetime=999 if h.get("in_pause") else None,
                     pausemovetime=999 if h.get("in_pause") else None, persistent=0)
    for p in _as_list(move.get("palfx")):
        out += _ctrl("PalFX", p.get("label", "palfx"), trigger1=_when(p), time=p.get("time", 10),
                     add=_pair(p["add"]) if "add" in p else None, mul=_pair(p["mul"]) if "mul" in p else None,
                     sinadd=_pair(p["sinadd"]) if "sinadd" in p else None, color=p.get("color"))
    for b in _as_list(move.get("bind_target")):
        out += _ctrl("TargetBind", "bind the opponent", trigger1=_when(b), time=b.get("time", 1),
                     pos=_pair(b.get("pos", [40, 0])), ignorehitpause=1)
    for r in _as_list(move.get("remove_vfx")):
        out += _ctrl("RemoveExplod", "remove the effect", trigger1=_when(r), id=r["id"])
    if move.get("angle") is not None:
        out += _ctrl("AngleDraw", "tilt", trigger1="1", value=move["angle"], ignorehitpause=1)
    oh = move.get("_on_hit")
    if oh:
        trig = (f"(numhelper({oh['helper']}) > 0) && (helper({oh['helper']}), movehit)"
                if oh.get("helper") is not None else "movehit")
        out += _ctrl("ChangeState", "hit: next phase", trigger1=trig, value=oh["state"])
    return out


def build_state(char: Character, move: dict, d: dict) -> str:
    kind = move.get("kind", "normal")
    air_move = kind in ("air", "air_special")
    helper = bool(move.get("helper"))
    n = int(move["state"])
    anim = int(move.get("anim", n))
    head = [f"[Statedef {n}]", f"; {move.get('name', '')} (generated from the spec)",
            f"type = {move.get('type', 'A' if air_move else 'S')}", "movetype = A",
            f"physics = {move.get('physics', 'N' if helper else 'A' if air_move else 'S')}",
            "juggle = 1", "ctrl = 0", f"anim = {anim}", f"sprpriority = {move.get('sprpriority', 2)}"]
    if not air_move and not helper:
        head.append("facep2 = 1")
    if "velset" in move:
        head.append(f"velset = {_pair(move['velset'])}")
    if move.get("power") and "_phase_of" not in move:
        head.append(f"poweradd = {-abs(int(move['power']))}")
    out = "\n".join(head) + "\n\n"
    if helper and move.get("noshadow", True):
        out += _ctrl("AssertSpecial", "no shadow", trigger1="1", flag="noshadow", ignorehitpause=1)
    if (kind == "super" and "_phase_of" not in move) or move.get("superpause"):
        sp = move.get("superpause", {}) if isinstance(move.get("superpause"), dict) else {}
        t_sp = int(sp.get("time", 30))
        # movetime 0: o golpe fica na pose de preparo durante o flash e só começa depois
        out += _ctrl("SuperPause", "super pause", trigger1="!time", time=t_sp,
                     movetime=sp.get("movetime", 0), anim=sp.get("anim", -1),
                     sound=sp.get("sound", d.get("super_sound", -1)), darken=int(sp.get("darken", 1)),
                     unhittable=1, poweradd=0, p2defmul=1)
        if sp.get("bgfade"):
            # cenário escurece (-100) e volta. Um helper que anda durante a pausa
            # (supermovetime) faz a rampa, já que o personagem está congelado
            hn = bgfade_helper(char, t_sp)
            out += _ctrl("Helper", "stage darkens", trigger1="!time", stateno=hn, id=hn,
                         pos="0,0", postype="p1", ownpal=1, supermovetime=999, pausemovetime=999)
        if sp.get("helper"):
            # cut-in: postype left = posição na tela e facing absoluto (o nome não sai espelhado)
            out += _ctrl("Helper", "portrait", trigger1="!time", stateno=sp["helper"], id=sp["helper"],
                         pos=sp.get("helper_pos", "0,0"), postype=sp.get("helper_postype", "p1"), facing=1,
                         ownpal=1, supermovetime=999, pausemovetime=999)
        if sp.get("flash"):
            fl = sp["flash"]
            out += _ctrl("Explod", "activation flash", trigger1="!time", anim=fl["anim"], id=fl["anim"],
                         pos=_pair(fl.get("pos", [0, -40])), postype="p1", bindtime=-1, removetime=-2,
                         scale=_pair(fl.get("scale", [0.4, 0.4])), sprpriority=4, ownpal=1,
                         removeongethit=1, supermovetime=999, ignorehitpause=1,
                         remappal=fl.get("remappal"), trans=fl.get("trans"))
    if move.get("voice"):
        out += _ctrl("PlaySnd", "voz", trigger1="!time", value=move["voice"],
                     channel=d.get("voice_channel", 0))
    for snd in move.get("sounds", []):
        out += _ctrl("PlaySnd", "sound", trigger1=f"animelem = {snd['elem']}", value=snd["value"])
    for vel in move.get("vel", []):
        out += _ctrl(vel.get("type", "VelSet"), "velocity", trigger1=vel.get("trigger", f"animelem = {vel['elem']}"),
                     x=vel.get("x"), y=vel.get("y"))
    def _elems(h):
        return h.get("elems") or ([h["elem"]] if "elem" in h else _hit_frames(char, anim)[:1])
    # a faísca de um acerto vale só até o quadro do próximo acerto: com "animelem = e, >= 0"
    # sozinho, o acerto k soltava as faíscas de todos os acertos anteriores junto (empilhava)
    all_elems = sorted({int(e) for h in move.get("hit", []) for e in _elems(h)})
    for h in move.get("hit", []):
        elems = _elems(h)
        for e in elems:
            nxt = next((x for x in all_elems if x > int(e)), None)
            spark_trig = f"(movehit = 1) && (animelem = {e}, >= 0)" + (
                f" && (animelem = {nxt}, < 0)" if nxt else "")
            kv = dict(trigger1=f"animelem = {e}",
                      attr=h.get("attr", ("A" if air_move else
                                          "C" if move.get("type") == "C" else "S") + ", " +
                                 {"normal": "NA", "air": "NA", "special": "SA",
                                  "air_special": "SA", "super": "HA", "throw": "NT"}[kind]),
                      animtype=h.get("animtype", "Medium"), damage=_pair(h.get("damage", [20, 4])),
                      guardflag=h.get("guardflag", "MA"),   # MA: defende em pé, agachado e no ar
                      pausetime=_pair(h.get("pausetime", [8, 9])),
                      hitsound=h.get("hitsound", d.get("hitsound", "S5,0")),
                      guardsound=h.get("guardsound", d.get("guardsound", "S6,0")),
                      sparkno=h.get("sparkno", d.get("sparkno", -1)),
                      guard__sparkno=h.get("guard_sparkno", d.get("guard_sparkno", -1)),
                      sparkxy=_pair(h.get("sparkxy", [0, -30])),
                      ground__type=h.get("ground_type", "High"),
                      ground__slidetime=h.get("slidetime", 12),
                      ground__hittime=h.get("hittime", 15),
                      ground__velocity=_pair(h.get("ground_velocity", [-3, 0])),
                      air__velocity=_pair(h.get("air_velocity", [-2.5, -3])),
                      fall=int(bool(h.get("fall", False))))
            if h.get("fall"):
                kv["fall__recover"] = int(bool(h.get("fall_recover", False)))
            for k2, v2 in h.get("extra", {}).items():
                kv[k2.replace(".", "__")] = v2
            out += _ctrl("HitDef", f"hit elem {e}", **kv)
            sps = h.get("spark_explod") or d.get("spark_explod")
            # uma faísca ou várias camadas (golpe de corte: explosão + marca do corte)
            for k_sp, sp in enumerate(sps if isinstance(sps, list) else [sps] if sps else []):
                px, py = sp.get("pos", [0, -30])
                jit = sp.get("jitter", 0)                # multi-acerto: faísca não empilha no mesmo ponto
                pos = (f"{px - jit // 2}+random%{jit},{py + jit // 2}-random%{jit}" if jit
                       else _pair([px, py]))
                if sp.get("helper"):                     # faísca de corte por helper (gira e cresce)
                    sc = sp.get("scale", [0.3, 0.3])
                    out += _ctrl("Helper", sp.get("label", "spark"),
                                 trigger1=spark_trig,
                                 stateno=sp["helper"], id=sp["helper"], pos=pos, postype="p2",
                                 facing=-1, ownpal=1, size__xscale=sc[0], size__yscale=sc[1],
                                 supermovetime=999, pausemovetime=999, ignorehitpause=1, persistent=0)
                    continue
                out += _ctrl("Explod", sp.get("label", "spark"),
                             trigger1=spark_trig,
                             anim=sp["anim"], id=7000 + k_sp, pos=pos,
                             postype="p2", bindtime=1, removetime=sp.get("removetime", -2),
                             scale=_pair(sp.get("scale", [0.3, 0.3])), angle=sp.get("angle", "random % 360"),
                             sprpriority=sp.get("sprpriority", 6), ownpal=1, removeongethit=0,
                             ignorehitpause=1, remappal=sp.get("remappal"), trans=sp.get("trans"),
                             persistent=0)
            # persistent = 0: na pausa do acerto o movehit fica em 1 (Ikemen: mctime só sobe fora
            # da pausa) e a faísca com ignorehitpause nasceria de novo a cada tick da pausa
    for v in move.get("vfx", []):
        trig = v.get("trigger", f"animelem = {v['elem']}")
        out += _ctrl("Explod", v.get("label", "vfx"), trigger1=trig, anim=v["anim"],
                     id=v.get("id", v["anim"]), pos=_pair(v.get("pos", [0, 0])),
                     postype=v.get("postype", "p1"), facing=v.get("facing", 1),
                     bindtime=v.get("bind", -1), removetime=v.get("removetime", -2),
                     scale=_pair(v.get("scale", [1, 1])), angle=v.get("angle", 0),
                     sprpriority=v.get("sprpriority", 3), ownpal=1, removeongethit=1,
                     remappal=v.get("remappal"), trans=v.get("trans"),
                     supermovetime=999, pausemovetime=999, ignorehitpause=1)
    for c in move.get("cancel", []):
        # "comando:estado" ou {command, state, condition (do golpe de destino), contact
        # (movecontact/movehit), ai (a IA também segue: passo de combo sem comando próprio)}
        if isinstance(c, str):
            cmd, st = c.split(":")[:2]
            c = {"command": cmd, "state": st}
        cond = f" && ({c['condition']})" if c.get("condition") else ""
        kv = dict(triggerall=f"(roundstate = 2) && ({c.get('contact', 'movecontact')})",
                  trigger1=f'(!ailevel) && (command = "{c["command"]}"){cond}' if c.get("ai")
                  else f'(command = "{c["command"]}"){cond}')
        if c.get("ai"):
            kv["trigger2"] = "(ailevel) && (random < ailevel * 70)"
        out += _ctrl("ChangeState", f"cancel into {c['state']}", value=c["state"], **kv)
    out += _phase_ctrls(move)
    if move.get("extra"):
        out += move["extra"].strip() + "\n\n"
    if move.get("_next") is not None:
        if air_move:
            out += _ctrl("ChangeState", "land", trigger1="(pos y >= 0) && (vel y > 0)",
                         value=move.get("land", 52))
        return out + _ctrl("ChangeState", "next phase", trigger1="!animtime", value=move["_next"])
    if helper:
        return out + _ctrl("DestroySelf", "fim", trigger1="!animtime")
    end = move.get("end", 50 if air_move else 0)
    if air_move:
        out += _ctrl("ChangeState", "land", trigger1="(pos y >= 0) && (vel y > 0)",
                     value=move.get("land", 52))
    out += _ctrl("ChangeState", "fim", trigger1="!animtime",
                 value=f"cond(pos y < 0, 50, {end})" if not air_move else end, ctrl=1)
    return out


def bgfade_helper(char: Character, pause: int) -> int:
    """Helper invisível que escurece o cenário (-100) durante a pausa e devolve a cor em
    ~17 ticks a partir de 2/3 dela (rampa em var(12)). Um por duração."""
    n = 3980 + pause // 10
    k0 = max(1, pause * 2 // 3)
    if n not in char.all_statedefs(include_common=False):
        ops.set_action_text(char, f"[Begin Action {n}]\n-1,0, 0,0, -1\n")
        ops.set_statedef_text(char, n, f"""[Statedef {n}]
; darkens the stage on the special/super flash (generated)
type = S
movetype = I
physics = N
ctrl = 0
anim = {n}
velset = 0,0

[State {n}, no shadow]
type = AssertSpecial
trigger1 = 1
flag = noshadow
ignorehitpause = 1

[State {n}, stage]
type = BGPalFX
trigger1 = 1
time = 1
add = {",".join([f"ifelse(time < {k0}, -100, -100 + 6 * (time - {k0}))"] * 3)}
ignorehitpause = 1

[State {n}, end]
type = DestroySelf
trigger1 = time >= {k0 + 16}
""")
    return n


def _minus1(moves: list[dict], d: dict) -> str:
    order = {"super": 0, "air_special": 1, "special": 1, "throw": 2, "normal": 3, "air": 3}
    normals = "[" + ", ".join(str(m["state"]) for m in moves if m.get("kind", "normal") in ("normal", "air")) + "]"
    lo_hi = sorted(int(m["state"]) for m in moves if m.get("kind", "normal") in ("normal", "air"))
    contact = f"((stateno = [{lo_hi[0]}, {lo_hi[-1]}]) && (movecontact))" if lo_hi else "0"
    out = ""
    for m in sorted([m for m in moves if m.get("command") or m.get("command_name")],
                    key=lambda m: (order.get(m.get("kind", "normal"), 3), -len(m.get("command", "")))):
        kind = m.get("kind", "normal")
        cname = m.get("command_name", f"spec_{m['state']}")
        cond = ["roundstate = 2"]
        cond.append("statetype = A" if kind in ("air", "air_special") else "statetype != A")
        cond.append("ctrl" if kind in ("normal", "air", "throw") else f"(ctrl) || {contact}")
        if m.get("power"):
            cond.append(f"power >= {abs(int(m['power']))}")
        if m.get("condition"):
            cond.append(m["condition"])
        ai = m.get("ai")
        kv = dict(triggerall=[f"({c})" for c in cond], trigger1=f'(!ailevel) && (command = "{cname}")')
        if ai:
            kv["trigger2"] = f"(ailevel) && ({ai}) && (random < (ailevel * {m.get('ai_chance', 4)}))"
        out += _ctrl("ChangeState", f"spec: {m.get('name', m['state'])}", value=m["state"], **kv)
    return out


def build_from_spec(char: Character, spec_path: str | Path) -> dict:
    """Lê o TOML e gera anims, statedefs, comandos e o bloco gerado do [Statedef -1]."""
    spec = tomllib.loads(Path(spec_path).read_text("utf-8"))
    return build_moves(char, spec.get("move", []), spec.get("defaults", {}))


def build_moves(char: Character, moves: list[dict], d: dict | None = None) -> dict:
    """Mesmo que build_from_spec, com os golpes já em dicionários (usado pelo autochar)."""
    d = d or {}
    made = {"anims": [], "states": [], "commands": []}
    taken = {int(m["state"]) for m in moves}
    for m in moves:
        if m.get("kind", "normal") not in KINDS:
            raise ValueError(tr("invalid kind in {0}: {1}").format(m.get('state'), m.get('kind')))
        for st in expand_phases(m):
            if st is not m and "_phase_of" in st and int(st["state"]) in taken:
                raise ValueError(tr("phase {0}: state {1} already belongs to another move").format(st['name'], st['state']))
            if "frames" in st:
                made["anims"].append(build_anim(char, st, d.get("weapon_colors"), d.get("clsn1_height")))
            ops.set_statedef_text(char, int(st["state"]), build_state(char, st, d))
            made["states"].append(int(st["state"]))
        if m.get("command"):
            cname = m.get("command_name", f"spec_{m['state']}")
            if not char.commands.find(cname):
                ops.add_command(char, cname, m["command"], m.get("command_time"))
                made["commands"].append(cname)
    # -1: tira os controladores gerados antes e põe os novos logo no começo
    import re
    sd = char.all_statedefs(include_common=False).get(-1)
    old = sd[0].text() if sd else "[Statedef -1]\n\n"
    first, rest = (old.split("\n", 1) + [""])[:2]          # "[Statedef -1]" e o resto
    marker = "; ---- generated by build_moves_from_spec (do not edit: run it again)\n"
    rest = rest.replace(marker, "")
    blocks = re.split(r"(?im)^(?=\[state )", rest)
    keep = [b for b in blocks if not re.match(r"(?i)\[state [^,\]]*,\s*spec:", b)]
    gen = marker + "\n" + _minus1(moves, d)
    ops.set_statedef_text(char, -1, first + "\n\n" + gen + "".join(keep).lstrip("\n"))
    return made
