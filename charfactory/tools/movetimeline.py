"""Linha do tempo de um golpe: o que a simulação viu em cada tick, separado por faixa (quadros,
frame data, acertos, sons, efeitos, tela), com o caminho de volta ao controlador no código.

Cada item guarda o estado e a posição do controlador no statedef (`ci`). Com isso a interface
abre a linha do controlador e, quando o momento vem de um gatilho só (`Time = N`, `!Time`,
`AnimElem = N` ou `AnimElemTime(N) = M`), arrasta o evento para outro tick reescrevendo esse
gatilho: por tempo continua por tempo, por quadro da animação continua por quadro.
"""
from __future__ import annotations

import re

from . import simulate
from ..i18n import tr

ROWS = [("quadros", tr("Frames")), ("fases", tr("Frame data")), ("acertos", tr("Hits")),
        ("p2", tr("P2")), ("sons", tr("Sounds")), ("efeitos", tr("Effects")),
        ("tela", tr("Screen and color"))]

# situação do P2 em cada tick (faixa "P2"): rótulo e explicação
P2_STATUS = {"p2_shake": (tr("shake"), tr("hit shake: P2 frozen by the hit (pausetime, 2nd value)")),
             "p2_stun": (tr("hitstun"), tr("hitstun: P2 sliding back until HitOver (ground.hittime)")),
             "p2_guard": (tr("guard"), tr("guard stun: P2 blocking (guard.hittime)")),
             "p2_air": (tr("air"), tr("knocked into the air / falling")),
             "p2_down": (tr("down"), tr("P2 lying down")),
             "p2_state": (tr("custom"), tr("P2 in a state of the attacker (p2stateno / TargetState)"))}


def _p2_status(p2: dict) -> str | None:
    st = p2["state"]
    if st == "stand":
        return None
    if st in ("hit", "guardhit"):
        if p2.get("sub") == "shake" or p2.get("shake", 0) > 0:
            return "p2_shake"
        return "p2_stun" if st == "hit" else "p2_guard"
    return "p2_down" if st == "down" else "p2_air"


def p2_rows(track) -> list[dict]:
    """Trechos da faixa P2: tremor, recuo, defesa, no ar e no chão, e o tick em que volta."""
    out: list[dict] = []
    for i, r in enumerate(track):
        k = _p2_status(r["p2"])
        if out and out[-1]["kind"] == k and out[-1]["i1"] == i:
            out[-1]["i1"] = i + 1
            continue
        if k is not None:
            out.append({"i0": i, "i1": i + 1, "kind": k, "text": P2_STATUS[k][0], "t": r["t"]})
        elif out and out[-1]["kind"] != "p2_free" and out[-1]["i1"] == i:
            # voltou ao parado: o combo acaba aqui
            out.append({"i0": i, "i1": i + 1, "kind": "p2_free", "text": "▲", "t": r["t"]})
    for it in out:
        n = it["i1"] - it["i0"]
        it["tip"] = (tr("P2 recovered (back to idle) at tick {0}: a hit after this starts a new combo").format(it["t"])
                     if it["kind"] == "p2_free" else
                     P2_STATUS[it["kind"]][1] + tr(" · {0} tick(s)").format(n))
    return out

# o que muda a leitura do golpe na tela (o resto dos eventos "ctrl" é movimento e regra)
VISUAL = {"envshake", "palfx", "bgpalfx", "allpalfx", "envcolor", "afterimage", "superpause",
          "pause", "assertspecial", "angledraw", "makedust"}

_TIME = re.compile(r"^\s*time\s*=\s*(\d+)\s*$", re.I)
_NOT_TIME = re.compile(r"^\s*!\s*time\s*$", re.I)
_ELEM = re.compile(r"^\s*animelem\s*=\s*(\d+)\s*(?:,\s*=?\s*0\s*)?$", re.I)
_ELEM_TIME = re.compile(r"^\s*animelemtime\s*\(\s*(\d+)\s*\)\s*=\s*(\d+)\s*$", re.I)
_MOMENT = re.compile(r"\b(time|animelem|animelemtime|animtime|animelemno)\b", re.I)
_TRIGGER_LINE = re.compile(r"^(\s*trigger\d+\s*=\s*)(.*?)(\s*;.*)?$", re.I | re.S)


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
            combo = e.get("combo")
            item.update(kind=kind, text=(f"✕{combo}" if combo else "✕") if kind == "acerto" else "▣",
                        tip=(tr("hit") if kind == "acerto" else tr("guard")) + f" [State {e['label']}]"
                            + (tr(" · combo {0}").format(combo) if combo else "")
                            + (tr(" · damage {0}").format(e['damage']) if e.get("damage") else "")
                            + (tr(" · pause {0},{1}").format(e['pause'][0], e['pause'][1]) if e.get("pause") else "")
                            + (tr(" · hittime {0}").format(e['hittime']) if e.get("hittime") is not None else ""))
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

    rows["p2"] = p2_rows(track)
    marks = [it["i1"] for k in ("acertos", "p2", "sons", "efeitos", "tela") for it in rows[k]]
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


def timing_trigger(ctrl) -> dict | None:
    """O gatilho que marca o momento do controlador, quando dá para mudá-lo arrastando:
    {"line", "kind": "time", "n"} para `Time = N`/`!Time`, ou {"line", "kind": "elem", "elem",
    "off"} para `AnimElem = E`/`AnimElemTime(E) = k`. Vale com um só triggerN; os triggerall
    podem existir desde que não falem de tempo nem de quadro (senão mudar um não basta)."""
    entries = ctrl.section.entries()
    trig = [e for e in entries if re.fullmatch(r"trigger\d+", e.key.lower().replace(" ", ""))]
    alls = [e for e in entries if e.key.lower().replace(" ", "") == "triggerall"]
    if len(trig) != 1 or any(_MOMENT.search((e.value or "").split(";")[0]) for e in alls):
        return None
    v = (trig[0].value or "").split(";")[0]
    if m := _TIME.match(v):
        return {"line": trig[0].line, "kind": "time", "n": int(m[1])}
    if _NOT_TIME.match(v):
        return {"line": trig[0].line, "kind": "time", "n": 0}
    if m := _ELEM.match(v):
        return {"line": trig[0].line, "kind": "elem", "elem": int(m[1]), "off": 0}
    if m := _ELEM_TIME.match(v):
        return {"line": trig[0].line, "kind": "elem", "elem": int(m[1]), "off": int(m[2])}
    return None


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


def _still(rec, who: str) -> bool:
    """Tick em que o tempo do estado (e a animação) de `who` não anda: pausa ou acerto."""
    ent = next((e for e in rec["ents"] if e["name"] == who), None)
    return bool(rec["pause"] or (ent and ent.get("frozen")))


def elem_at(sim: simulate.Sim, i: int, who: str = "P1") -> tuple[int, int, int] | None:
    """(estado, elemento da anim, ticks dentro do elemento) de `who` no índice i do registro."""
    track = sim.track
    find = lambda r: next((e for e in r["ents"] if e["name"] == who), None)
    ent = find(track[i]) if 0 <= i < len(track) else None
    if ent is None or not ent.get("elem"):
        return None
    key = (ent["state"], ent["anim"], ent["elem"])
    off, k = 0, i - 1
    while k >= 0:
        e = find(track[k])
        if e is None or (e["state"], e["anim"], e["elem"]) != key:
            break
        if not _still(track[k], who):
            off += 1
        k -= 1
    return ent["state"], ent["elem"], off


def move_event(char, sim: simulate.Sim, item: dict, to: int) -> dict:
    """Leva o evento do item ao índice `to` do registro reescrevendo o gatilho de momento:
    `Time = N` anda o que o tempo do estado andou (pausas não contam); `AnimElem` vira o quadro
    mostrado ali (`AnimElemTime(E) = k` quando cai no meio de um quadro)."""
    ctrl, states = controller(char, item["state"], item["ci"])
    if ctrl is None:
        raise ValueError(tr("controller {0} of state {1} does not exist").format(item["ci"], item["state"]))
    tt = timing_trigger(ctrl)
    if tt is None:
        raise ValueError(tr("[{0}] has no single Time or AnimElem trigger to move").format(ctrl.section.name))
    who = item.get("who") or "P1"
    old_text = (ctrl.section.lines[tt["line"]].split("=", 1)[1]).split(";")[0].strip()
    if tt["kind"] == "time":
        delta = state_ticks(sim, item["i0"], to, who)
        if not delta:
            raise ValueError(tr("The event fell in a pause: the state Time does not change there"))
        new_text = f"Time = {max(0, tt['n'] + delta)}"
    else:
        at = elem_at(sim, to, who)
        if at is None or at[0] != item["state"]:
            raise ValueError(tr("The event can only move inside state {0}").format(item["state"]))
        _, elem, off = at
        new_text = f"AnimElem = {elem}" if off == 0 else f"AnimElemTime({elem}) = {off}"
    if new_text.replace(" ", "").lower() == old_text.replace(" ", "").lower():
        raise ValueError(tr("The event is already there"))
    m = _TRIGGER_LINE.match(ctrl.section.lines[tt["line"]])
    ctrl.section.lines[tt["line"]] = f"{m[1]}{new_text}{m[3] or ''}"
    # solto na pausa do acerto: o tempo do estado está parado ali, e o evento sai quando ela acaba
    paused = 0 <= to < len(sim.track) and _still(sim.track[to], who)
    return {"state": item["state"], "controller": ctrl.section.name, "old": old_text, "new": new_text,
            "part": part_of(char, states),
            "note": tr("dropped in the hit pause, where the state's time stands still: it plays when the "
                       "pause ends") if paused else ""}
