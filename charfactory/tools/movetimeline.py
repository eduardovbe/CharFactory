"""Linha do tempo de um golpe: o que a simulação viu em cada tick, separado por faixa (quadros,
frame data, acertos, sons, efeitos, tela), com o caminho de volta ao controlador no código.

Cada item guarda o estado e a posição do controlador no statedef (`ci`). Com isso a interface
abre a linha do controlador e, quando o gatilho é um simples `Time = N`, arrasta o evento para
outro tick reescrevendo o N.
"""
from __future__ import annotations

import re

from . import simulate
from ..i18n import tr

ROWS = [("quadros", tr("Frames")), ("fases", tr("Frame data")), ("acertos", tr("Hits")),
        ("sons", tr("Sounds")), ("efeitos", tr("Effects")), ("tela", tr("Screen and color"))]

# o que muda a leitura do golpe na tela (o resto dos eventos "ctrl" é movimento e regra)
VISUAL = {"envshake", "palfx", "bgpalfx", "allpalfx", "envcolor", "afterimage", "superpause",
          "pause", "assertspecial", "angledraw", "makedust"}

_TIME = re.compile(r"^\s*time\s*=\s*(\d+)\s*$", re.I)


def build(sim: simulate.Sim) -> dict:
    """{"ticks", "end", "phases", "frame_data", "rows": {faixa: [item]}}. Item: i0/i1 (índices
    do registro, i1 exclusivo), text, tip, kind, state, ci e who (dono do evento)."""
    track = sim.track
    index = {r["t"]: i for i, r in enumerate(track)}
    at = lambda t: index.get(t, min(len(track) - 1, max(0, t)))
    fd = simulate.frame_data(sim)
    rows: dict[str, list[dict]] = {k: [] for k, _ in ROWS}

    # quadros do P1: um trecho por elemento da anim
    run = None
    for i, r in enumerate(track):
        p1 = next((e for e in r["ents"] if e["name"] == "P1"), None)
        key = (p1["anim"], p1["elem"]) if p1 else None
        if run and run["key"] == key:
            run["i1"] = i + 1
            continue
        if key is not None:
            run = {"key": key, "i0": i, "i1": i + 1, "kind": "quadro", "text": str(key[1]),
                   "tip": tr("anim {0}, frame {1} (state {2})").format(key[0], key[1], p1['state']), "state": p1["state"]}
            rows["quadros"].append(run)
        else:
            run = None
    for it in rows["quadros"]:
        it.pop("key")

    # pausa do acerto do P1: trechos na faixa dos acertos
    paused = [ph == "pausa" for ph in fd["phases"]]
    i = 0
    while i < len(paused):
        if paused[i]:
            j = i
            while j < len(paused) and paused[j]:
                j += 1
            rows["acertos"].append({"i0": i, "i1": j, "kind": "pausa", "text": "",
                                    "tip": tr("pause of {0} tick(s)").format(j - i)})
            i = j
        else:
            i += 1

    for e in sim.events:
        i0 = at(e["t"])
        item = {"i0": i0, "i1": i0 + 1, "state": e.get("state"), "ci": e.get("ci"), "who": e.get("who", ""),
                "t": e["t"]}
        kind, value = e["kind"], e.get("value")
        if kind in ("acerto", "defesa"):
            row = "acertos"
            item.update(kind=kind, text="✕" if kind == "acerto" else "▣",
                        tip=(tr("hit") if kind == "acerto" else tr("guard")) + f" [State {e['label']}]"
                            + (tr(" · damage {0}").format(e['damage']) if e.get("damage") else "")
                            + (tr(" · pause {0},{1}").format(e['pause'][0], e['pause'][1]) if e.get("pause") else ""))
            if e.get("snd"):
                g, n, common = e["snd"]
                rows["sons"].append({**item, "kind": "som", "text": f"{'F' if common else ''}{g},{n}",
                                     "hitsound": True,
                                     "tip": tr("{0} sound (HitDef): {1}{2},{3}").format(kind, 'F' if common else '', g, n)})
        elif kind == "ctrl" and value == "playsnd":
            row = "sons"
            snd = e.get("snd")
            label = f"{'F' if snd[2] else ''}{snd[0]},{snd[1]}" if snd else e.get("arg", "?")
            item.update(kind="som", text=label, tip=f"PlaySnd {label} [State {e['label']}]")
        elif kind in ("explod", "helper"):
            row = "efeitos"
            item.update(kind=kind, text=str(value), tip=f"{kind} {'anim' if kind == 'explod' else tr('state')} "
                                                        f"{value} [State {e['label']}]")
        elif kind == "ctrl" and value == "projectile":
            row = "efeitos"
            item.update(kind="projectile", text="proj", tip=f"Projectile [State {e['label']}]")
        elif kind == "ctrl" and value in VISUAL:
            row = "tela"
            item.update(kind=value, text=value, tip=f"{value} [State {e['label']}]"
                        + (f" · {e['arg']}" if e.get("arg") else ""))
        else:
            continue
        prev = rows[row][-1] if rows[row] else None
        if prev is not None and prev["i1"] == i0 and kind != "acerto" and all(
                prev.get(k) == item.get(k) for k in ("kind", "text", "state", "ci", "who")):
            prev["i1"] = i0 + 1       # o mesmo controlador tick após tick vira um trecho
            continue
        rows[row].append(item)

    marks = [it["i1"] for k in ("acertos", "sons", "efeitos", "tela") for it in rows[k]]
    free = next((i for i, ph in enumerate(fd["phases"]) if ph == "livre"), len(track))
    end = min(len(track), max([free, *marks]) + 12)
    return {"ticks": len(track), "end": end, "phases": fd["phases"], "frame_data": fd, "rows": rows}


# ------------------------------------------------------------------ volta ao código

def controller(char, state: int, ci: int):
    """(Controller, States) do controlador `ci` do statedef `state`, ou (None, None)."""
    sd = char.all_statedefs().get(state)
    if not sd or ci is None or not 0 <= ci < len(sd[0].controllers):
        return None, None
    return sd[0].controllers[ci], sd[1]


def part_of(char, states) -> str:
    """Parte do modelo (para gravar e desfazer) do arquivo de estados."""
    try:
        return "commands" if states is char.commands.states else "states"
    except Exception:  # noqa: BLE001
        return "states"


def code_line(states, ctrl) -> int | None:
    """Linha (1 = primeira) do cabeçalho do controlador no arquivo."""
    n = len(states.doc.preamble) + 1
    for sec in states.doc.sections:
        if sec is ctrl.section:
            return n
        n += len(sec.lines)
    return None


def time_trigger(ctrl) -> tuple[int, int] | None:
    """(linha na seção, N) quando o controlador dispara só por `trigger1 = Time = N`."""
    trig = [e for e in ctrl.section.entries() if re.fullmatch(r"trigger\d+", e.key.lower().replace(" ", ""))]
    if len(trig) != 1:
        return None
    m = _TIME.match(trig[0].value)
    return (trig[0].line, int(m[1])) if m else None


def state_ticks(sim: simulate.Sim, i0: int, i1: int, who: str = "P1") -> int:
    """Ticks do Time do estado entre dois índices do registro: a pausa do acerto e a do
    SuperPause não contam (o Time de quem está parado não anda)."""
    a, b = sorted((i0, i1))
    n = 0
    for r in sim.track[a:b]:
        ent = next((e for e in r["ents"] if e["name"] == who), None)
        if not r["pause"] and not (ent and ent.get("frozen")):
            n += 1
    return n if i1 >= i0 else -n


def shift_time(char, state: int, ci: int, delta: int) -> dict:
    """Muda o `Time = N` do gatilho do controlador em `delta` ticks (nunca abaixo de 0)."""
    ctrl, states = controller(char, state, ci)
    if ctrl is None:
        raise ValueError(tr("controller {0} of state {1} does not exist").format(ci, state))
    tt = time_trigger(ctrl)
    if tt is None:
        raise ValueError(tr("[{0}] is not triggered by a simple Time = N").format(ctrl.section.name))
    line, old = tt
    new = max(0, old + delta)
    text = ctrl.section.lines[line]
    ctrl.section.lines[line] = re.sub(r"(?i)(time\s*=\s*)\d+", lambda m: f"{m[1]}{new}", text, count=1)
    return {"state": state, "controller": ctrl.section.name, "old": old, "new": new,
            "part": part_of(char, states)}
