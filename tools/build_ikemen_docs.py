"""Gera charfactory/data/ikemen_docs.json: o que faz cada parâmetro que o Ikemen GO acrescentou
ou mudou nos controllers do MUGEN, para a janela de parâmetros do editor de estados.

Lê a página "State controllers (changed)" da wiki do Ikemen GO (git clone
https://github.com/ikemen-engine/Ikemen-GO.wiki.git) e liga cada trecho aos nomes de
charfactory/data/ikemen_params.json (shader.texX.spr -> shader.tex1.spr e shader.tex2.spr,
Size -> size.depth, size.weight…). O que a wiki não cobre vem de EXTRA, escrito à mão.

Uso: python tools/build_ikemen_docs.py C:/caminho/Ikemen-GO.wiki
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "charfactory" / "data"
OUT = DATA / "ikemen_docs.json"

# parâmetros que a wiki não descreve (ou descreve só num exemplo); "*" vale para qualquer controller
EXTRA: dict[str, dict[str, str]] = {
    "helper": {
        "size.weight": "Weight of the helper, like the weight constant in [Size]: it changes how much "
                       "it is pushed by other players. Defaults to the root's value.",
        "size.depth": "Depth of the helper's push area on the Z axis (front, back). Defaults to the "
                      "root's value.",
        "size.pushfactor": "Multiplier of the push between the helper and other players. Defaults to "
                           "the root's value.",
    },
    "explod": {"bindid": "ID of the player or helper the explod is bound to (with bindtime). "
                         "Defaults to the player that created it."},
    "modifyexplod": {"bindid": "ID of the player or helper the explod is bound to (with bindtime). "
                               "Defaults to the player that created it."},
    "*": {
        "redirectid": "Runs the controller on the player or helper with this ID instead of the one running "
                      "the state (like the redirection in triggers). Defaults to the player itself.",
        "xangle": "Rotation around the X axis, in degrees (3D tilt, seen with projection).",
        "yangle": "Rotation around the Y axis, in degrees (3D turn, seen with projection).",
        "xshear": "Shears the sprite horizontally (italic-like slant). 0 = no shear.",
        "window": "Clipping rectangle x1, y1, x2, y2 relative to the position: only the part of the "
                  "sprite inside it is drawn.",
        "layerno": "Drawing layer: -1, 0 or 1. 0 = together with the characters (default); 1 = in "
                   "front of them, like the old ontop parameter; -1 = behind them.",
        "projection": "How the 3D angles are projected: orthographic (default), perspective or "
                      "perspective2.",
        "focallength": "Focal length used by perspective projection. Defaults to 2048.",
        "space": "Coordinate space of pos and vel: screen or stage.",
        "angle": "Rotation of the sprite in degrees, counterclockwise.",
        "alpha": "Transparency source,dest (0-256), used with trans = addalpha.",
        "velocity": "Old name of vel (x, y velocity), still accepted.",
        "volume": "Old name of volumescale (sound volume), still accepted.",
        "id": "ID of the target, explod or object to affect. -1 affects all of them.",
        "index": "Which one of the matching items to affect, counting from 0 (for example, the "
                 "second target is index = 1). Defaults to affecting all of them.",
        "playerno": "Player number whose objects are affected. Defaults to the player itself.",
        "playerid": "Player ID (from the ID trigger) of the player to affect.",
        "posz": "Z (depth) position, for stages with a Z axis.",
        "z": "Z (depth) component, for stages with a Z axis.",
        "zaccel": "Z (depth) acceleration applied to the target.",
        "xaccel": "X acceleration applied to the target while it is in the hit state.",
        "priority": "Priority: when two of them compete, the highest one wins. Defaults to 0.",
        "pausebg": "If 1, the stage background also stops during the pause. Defaults to 1.",
        "dir": "Direction of the shake, in degrees. 0 = vertical (the MUGEN default).",
        "diradd": "Degrees added to the shake direction each frame (spins the shake).",
        "decay": "How fast the shake amplitude falls off over time. 0 = it does not decay.",
        "mul": "Multiplier applied to the frequency of the shake.",
        "value": "The value to set or add.",
        "palhue": "Rotates the hue of the palette, in degrees (-180 to 180).",
        "palinvertblend": "Inverts the blend mode (Add becomes Sub and vice versa) when palinvertall is on.",
        "hue": "Rotates the hue of the colors, in degrees (-180 to 180).",
        "invertblend": "Inverts the blend mode (Add becomes Sub and vice versa) when invertall is on.",
        "sinmul": "Sine wave amplitude added to mul: r, g, b, period.",
        "sincolor": "Sine wave amplitude added to color: amount, period.",
        "sinhue": "Sine wave amplitude added to hue: amount, period.",
        "removeexplods": "If 1, also removes the explods of the player.",
        "removetexts": "If 1, also removes the texts created by the Text controller.",
        "recursive": "If 1, also applies to the helpers of the helpers (all descendants).",
        "slot": "Which slot to use, so several of them can be active at the same time.",
        "interpolation.xangle": "Target X-axis angle (xangle) for the interpolation.",
        "interpolation.yangle": "Target Y-axis angle (yangle) for the interpolation.",
        "afterimage.ignorehitpause": "If 1, the afterimage keeps updating during the hit pause.",
        "shader.tex1.spr": "Sprite group, index sent to the custom shader as texture 1.",
        "shader.tex2.spr": "Sprite group, index sent to the custom shader as texture 2.",
        "shader.tex1.anim": "Animation sent to the custom shader as texture 1.",
        "shader.tex2.anim": "Animation sent to the custom shader as texture 2.",
        "stack": "If 1, adds a new HitBy/NotHitBy slot instead of replacing the current one.",
        "enabled": "Set to 0 to turn the effect off instead of on. Defaults to 1.",
        "sysv": "Index of a system int variable (sysvar).",
        "sysfv": "Index of a system float variable (sysfvar).",
        "standby": "Forces the standby state of the helper (tag team), instead of copying the root's.",
        "teamside": "Team side the helper belongs to: 1 or 2. Defaults to the side of the root.",
        "under": "If 1, the effect is drawn under the characters.",
        "brightness": "Brightness of the darkened screen during the pause (0-256). Defaults to 0 "
                      "(black overlay), MUGEN uses the darken parameter.",
        "endcmdbuftime": "Ticks at the end of the pause during which the command buffer still records input.",
        "movetime": "Ticks the player can still move during the pause.",
        "animelem": "Animation element (frame) to start from, counting from 1.",
        "animelemtime": "Time inside the starting element, in ticks.",
        "elemtime": "Time inside the starting element, in ticks.",
        "animplayerno": "Player number whose animation is used. Defaults to the player itself.",
        "spriteplayerno": "Player number whose sprites are used. Defaults to the player itself.",
        "readplayerid": "ID of the player whose state data (vars, anims) is read.",
        "pivot": "Rotation center offset x, y from the axis.",
        "scale": "Scale x, y.",
        "time": "Duration in ticks.",
        "width": "Width.",
        "height": "Height.",
        "depth": "Depth (Z size) front, back.",
        "attr": "Attribute of the attack, like in HitDef (state type and attack type, e.g. S, NA).",
        "flag4": "Fourth flag to assert (up to eight flags at once).",
        "flag5": "Fifth flag to assert.",
        "flag6": "Sixth flag to assert.",
        "flag7": "Seventh flag to assert.",
        "flag8": "Eighth flag to assert.",
        "lo": "Strength of the low-frequency (left) motor of the gamepad, 0-255.",
        "hi": "Strength of the high-frequency (right) motor of the gamepad, 0-255.",
        "interpolation": "Turns interpolation on. Use the interpolation.* parameters to set what changes.",
        "remappal": "Remaps palette source group, index to destination group, index (like RemapPal).",
        "ownpal": "If 1, the effect uses its own copy of the palette, so PalFX on the player does not change it.",
        "sprpriority": "Drawing order: higher is drawn in front.",
        "stagebound": "If 0, the camera is not limited by the stage edges.",
        "syncid": "ID of an explod to follow: this one copies its position and, with syncparams, other values.",
        "synclayer": "If 1, the explod is drawn right above (1) or below (-1) the synced explod.",
        "syncparams": "If 1, also copies the visual parameters (scale, angle, palfx…) of the synced explod.",
        "dizzy": "If 0, this life change does not add dizzy points.",
        "dizzypoints": "Dizzy points added to the opponent (stun bar).",
        "guardpoints": "Guard points removed from the opponent (guard crush bar).",
        "redlife": "Red life (recoverable damage) dealt.",
        "damage": "Multiplier for the damage.",
        "multype": "How the multiplier combines: 0 multiplies, 1 replaces.",
        "onhit": "If 1, the change only happens when the attack hits.",
        "affectteam": "Who is affected: E (enemies), F (friends) or B (both). Defaults to E.",
        "loopstart": "Sample where the loop starts.",
        "loopend": "Sample where the loop ends.",
        "loopcount": "How many times the sound loops. -1 loops forever.",
        "startposition": "Sample where the sound starts playing.",
        "stoponchangestate": "If 1, the sound stops when the player changes state.",
        "stopongethit": "If 1, the sound stops when the player is hit.",
        "removeonchangestate": "If 1, the explod is removed when the player changes state.",
        "continue": "If 1, the animation keeps playing from where it was when changing state.",
        "kovelocity": "If 1, the helper flies like a defeated player at KO.",
        "hidewithbars": "If 1, hides the explod while the fight screen (lifebars) is hidden.",
        "keepstate": "If 1, the target stays in its current state instead of going to the hit state.",
        "missonoverride": "If 1, the attack counts as a miss when a HitOverride catches it.",
        "p2clsncheck": "Which Clsn box of P2 the hit checks: 1 = Clsn1, 2 = Clsn2, 0 = none.",
        "p2clsnrequire": "Which Clsn box P2 must have for the hit to connect: 1 = Clsn1, 2 = Clsn2.",
        "score": "Score points given to the attacker (hit) and to the defender (guard).",
        "unhittabletime": "Ticks the target cannot be hit again after the hit.",
        "forcecrouch": "If 1, forces the target into a crouching hit.",
        "forceguard": "If 1, the attack is guarded even when the player would not guard.",
        "ignorereversaldef": "If 1, this HitDef cannot be caught by a ReversalDef.",
        "immortal": "If 1, the life of the helper can't fall to 0.",
        "preserve": "If 1, the helper is kept to the next round.",
        "ownclsnscale": "If 1, the Clsn boxes of the helper use its own size.xscale and size.yscale.",
        "ownprojectile": "If 1, the helper owns its projectiles instead of the root.",
        "clsnproxy": "If 1, hits on the helper's Clsn boxes count as hits on its parent.",
        "extendsmap": "If 1, the helper inherits the maps of its parent.",
        "inheritchannels": "1 shares the parent's sound channels, 2 shares the root's.",
        "inheritjuggle": "1 shares the juggle points and targets of the parent, 2 those of the root.",
    },
}

INHERIT = {"modifyexplod": "explod", "projectile": "hitdef", "reversaldef": "hitdef",
           "nothitby": "hitby", "selfstate": "changestate", "changeanim2": "changeanim"}
PREFIX = {"palfx": "palfx", "afterimage": "afterimage", "fall.envshake": "fallenvshake",
          "envshake": "envshake"}

_HEAD2 = re.compile(r"^## (.*)$", re.M)
_HEAD3 = re.compile(r"^### (.*)$", re.M)


def _plain(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).replace("(nightly build only)", "").replace("(changed)", "").strip()


def _clean(body: str) -> str:
    """Markdown da wiki -> texto corrido (sem exemplos de código)."""
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    body = re.sub(r"^\*\*Example:?\*\*:?\s*$", "", body, flags=re.M | re.I)
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)
    body = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", body)
    body = re.sub(r"`([^`]+)`", r"\1", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    body = body.replace("\u200b", "")
    body = body.replace("\\>", ">").replace("\\<", "<")
    body = re.sub(r"\s*\[TODO[^\]]*\]", "", body)
    body = re.sub(r"(\w+)/#[\w-]+\)", r"\1", body)              # link interno quebrado da wiki
    lines = [ln.strip() for ln in body.split("\n")]
    out, para, label = [], [], ""
    for ln in lines + [""]:
        h = re.match(r"^#{4,}\s*(.*)$", ln)
        if h:                                        # "#### Flag" vira "Flag: texto" no parágrafo seguinte
            label = _plain(h[1]) + ": "
            continue
        if not ln:
            if para:
                out.append(label + " ".join(para))
                para, label = [], ""
        elif re.match(r"^[*-] ", ln):
            if para:
                out.append(label + " ".join(para))
                para, label = [], ""
            out.append("• " + ln[2:].strip())
        else:
            para.append(ln)
    # rótulos de exemplo cujo código saiu ("Examples:", "• Object fading out in 30 ticks:")
    out = [ln for ln in out if not re.fullmatch(r"(?:• .*|Examples?\b.*):", ln)]
    return "\n".join(out).strip()


_SYNTAX = re.compile(r"^\*\*\s*([A-Za-z][\w.]*)\s*=\s*(.*?)\*\*\s*$", re.M)


def _name(head: str) -> str:
    """Título da wiki -> nome do parâmetro ('ShaderTexX.spr' -> 'shader.texX.spr')."""
    n = head.replace(" ", "")
    return re.sub(r"^(shader)(?=[A-Za-z])", r"\1.", n, flags=re.I)


def _pattern(name: str, family: bool) -> re.Pattern:
    """'shader.texX.spr' -> casa shader.tex1.spr…; family: 'Size' casa também size.weight."""
    n = re.escape(name.lower())
    if re.search(r"[a-z]X(\.|$)", name):
        n = re.sub(r"(?<=[a-z])x(?=\\\.|$)", r"\\d+", n)
    return re.compile(rf"^{n}\..+$" if family else rf"^{n}$")


def _sections(text: str):
    """(controllers, nome, sintaxe, texto) de cada ### da página."""
    h2 = list(_HEAD2.finditer(text))
    for k, m in enumerate(h2):
        title = re.sub(r"\s+parameters?$", "", _plain(m[1]), flags=re.I)
        ctrls = [c.strip().lower() for c in title.split(",") if c.strip()]
        body = text[m.end(): h2[k + 1].start() if k + 1 < len(h2) else len(text)]
        h3 = list(_HEAD3.finditer(body))
        for j, s in enumerate(h3):
            chunk = body[s.end(): h3[j + 1].start() if j + 1 < len(h3) else len(body)]
            # várias sintaxes em negrito no mesmo trecho (Interpolation): um pedaço para cada
            subs = list(_SYNTAX.finditer(chunk))
            if len({x[1].lower() for x in subs}) > 1:
                for i, x in enumerate(subs):
                    end = subs[i + 1].start() if i + 1 < len(subs) else len(chunk)
                    yield ctrls, x[1], f"{x[1]} = {x[2]}", chunk[x.end(): end]
            else:
                yield ctrls, _name(_plain(s[1])), f"{subs[0][1]} = {subs[0][2]}" if subs else "", _SYNTAX.sub("", chunk)


def build(wiki: Path) -> dict:
    params = json.loads((DATA / "ikemen_params.json").read_text("utf-8"))["sctrls"]
    grammar = json.loads((DATA / "grammar.json").read_text("utf-8"))["controllers"]
    text = (wiki / "State-controllers-(changed).md").read_text("utf-8")
    out: dict[str, dict[str, dict]] = {}

    def known(c: str) -> list[str]:
        return sorted(set(params.get(c, {}).get("params", [])) | set(grammar.get(c, {}).get("params", {})))

    entries = []
    for ctrls, name, syntax, raw in _sections(text):
        # o nome novo já é o que a janela mostra; a nota sobre a troca de sintaxe sobra
        doc = re.sub(r"\s*Note: In the nightly build the syntax changed to [\w.]+\.?", "", _clean(raw)).strip()
        if doc:
            entries.append((ctrls, name, re.sub(r"\*", "", syntax).strip(), doc))

    def assign(family: bool) -> None:
        for ctrls, name, syntax, doc in entries:
            pat = _pattern(name, family)
            for c in ctrls:
                for p in known(c):
                    if pat.match(p) and p not in out.get(c, {}):
                        # sintaxe com o nome antigo (shadertexX.spr) não serve para o nome atual
                        lhs = syntax.split("=")[0].strip()
                        ok = syntax and _pattern(lhs, False).match(p)
                        out.setdefault(c, {})[p] = {"doc": doc, **({"syntax": syntax} if ok else {})}

    def missing(c: str):
        mugen = grammar.get(c, {}).get("params", {})
        return [p for p in params.get(c, {}).get("params", [])
                if p not in mugen and p not in out.get(c, {}) and p not in ("persistent", "ignorehitpause")]

    # primeiro os nomes exatos; as famílias da wiki (Size -> size.*) só no que os textos
    # próprios (palfx.sinmul = PalFx sinmul) não cobrirem
    assign(False)
    for family in (False, True):
        if family:
            assign(True)
        # quem aceita os parâmetros de outro controller herda os textos dele
        for child, base in INHERIT.items():
            for p, v in out.get(base, {}).items():
                out.setdefault(child, {}).setdefault(p, v)
        for c in params:
            for p in missing(c):
                # prefixos que repetem outro controller: hitdef palfx.color = PalFx color
                pre = next((x for x in PREFIX if p.startswith(x + ".")), None)
                if pre:
                    rest, src = p[len(pre) + 1:], PREFIX[pre]
                    v = out.get(src, {}).get(rest) or grammar.get(src, {}).get("params", {}).get(rest)
                    if v and v.get("doc"):
                        out.setdefault(c, {})[p] = {"doc": v["doc"]}
                        continue
                doc = EXTRA.get(c, {}).get(p) or EXTRA["*"].get(p)
                if doc:
                    out.setdefault(c, {})[p] = {"doc": doc}
    # textos escritos para um controller valem mais que o trecho genérico da wiki
    for c, docs in EXTRA.items():
        if c != "*":
            for p, doc in docs.items():
                out.setdefault(c, {})[p] = {**out.get(c, {}).get(p, {}), "doc": doc}
    return {"source": "Ikemen GO wiki, State controllers (changed)",
            "controllers": {c: dict(sorted(v.items())) for c, v in sorted(out.items())}}


if __name__ == "__main__":
    g = build(Path(sys.argv[1]))
    OUT.write_text(json.dumps(g, indent=1, ensure_ascii=False) + "\n", "utf-8", newline="\n")
    n = sum(len(v) for v in g["controllers"].values())
    print(f"{n} parameters in {len(g['controllers'])} controllers -> {OUT}")
