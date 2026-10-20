"""Operações de edição de personagem.

Todas recebem um Character, alteram o modelo em memória e marcam o que mudou;
gravar no disco é uma chamada separada a Character.save().
"""
from __future__ import annotations

import colorsys
import csv
import json
from pathlib import Path

from PIL import Image

from ..formats import act, air, sff
from ..formats import snd as sndfmt
from ..project import Character
from ..i18n import tr

# ================================================================ sprites


def list_sprites(char: Character, group: int | None = None) -> list[dict]:
    return [s.info() for s in char.sprites.sprites if group is None or s.group == group]


def export_sprites(char: Character, out_dir: str | Path, group: int | None = None,
                   palette: int | None = None) -> dict:
    """Exporta sprites como PNG indexados + sprites.csv (grupo, nº, eixo)."""
    from .render import palette_for
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    s = char.sprites
    pal = palette_for(char, palette)
    rows = []
    for spr in s.sprites:
        if group is not None and spr.group != group:
            continue
        name = f"{spr.group}-{spr.item}.png"
        img = s.image(spr, pal if not spr.is_truecolor else None)
        img.save(out / name, transparency=0) if img.mode == "P" else img.save(out / name)
        rows.append([spr.group, spr.item, name, spr.axis_x, spr.axis_y])
    with open(out / "sprites.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "item", "file", "axis_x", "axis_y"])
        w.writerows(rows)
    return {"exported": len(rows), "dir": str(out)}


def _nearest_indices(rgb: "np.ndarray", palette: list, first: int = 1) -> "np.ndarray":
    """Índice da cor mais próxima (distância RGB) de cada pixel, procurando de `first` em diante
    (o 0 é a transparência: um pixel preto não pode virar buraco)."""
    import numpy as np
    pal = np.array(palette[first:256] or [(0, 0, 0)], dtype=np.int32)
    flat = rgb.reshape(-1, 3).astype(np.int32)
    uniq, inv = np.unique(flat, axis=0, return_inverse=True)
    best = np.empty(len(uniq), dtype=np.int32)
    for k in range(0, len(uniq), 4096):             # em blocos: foto com 100 mil cores cabe na memória
        d = ((uniq[k:k + 4096, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
        best[k:k + 4096] = d.argmin(axis=1)
    return (best + first)[inv.reshape(-1)].astype(np.uint8)


def _opaque_mask(img: Image.Image) -> "np.ndarray":
    """Pixels visíveis: alfa >= 128; numa imagem indexada sem transparência, tudo menos o
    índice 0 (a convenção do MUGEN/PCX: o 0 é o fundo)."""
    import numpy as np
    if img.mode == "P" and "transparency" not in img.info:
        return np.asarray(img).reshape(-1) != 0
    return np.asarray(img.convert("RGBA").getchannel("A")).reshape(-1) >= 128


def _to_indexed(img: Image.Image, char: Character) -> Image.Image:
    """Converte para a paleta 1,1 do personagem (cor mais próxima entre os índices 1-255;
    o que é transparente vira índice 0)."""
    import numpy as np
    s = char.sprites
    pidx = s.find_palette(1, 1)
    if img.mode == "P" and pidx < 0:
        return img
    base = s.palettes[pidx].colors if pidx >= 0 else None
    opaque = _opaque_mask(img)
    rgb = np.asarray(img.convert("RGB"))
    if base is None:
        # sem paleta do personagem: até 255 cores para os pixels opacos, índice 0 = transparente
        q = img.convert("RGB").quantize(colors=255, dither=Image.Dither.NONE)
        pal = (q.getpalette() or [])[:255 * 3]
        colors = [(0, 0, 0)] + [tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)]
        idx = _nearest_indices(rgb, colors)
    else:
        colors = list(base)
        idx = _nearest_indices(rgb, colors)
    idx[~opaque] = 0
    out = Image.fromarray(idx.reshape(rgb.shape[:2]), "P")
    flat = [c for col in colors for c in col][:768]
    out.putpalette(flat + [0] * (768 - len(flat)))
    return out


def import_sprite(char: Character, image_path: str | Path, group: int, item: int,
                  axis_x: int | None = None, axis_y: int | None = None,
                  keep_own_palette: bool = False, autocrop: bool = True,
                  keep_indices: bool = False, palette_index: int | None = None) -> dict:
    """Importa uma imagem como sprite. Por padrão usa a paleta do personagem (1,1);
    eixo padrão = centro inferior da imagem original. `keep_indices`: imagem indexada entra
    com os índices como estão (a edição externa de um sprite volta assim), na paleta
    `palette_index` do SFF (ou na do sprite que já existe)."""
    img = Image.open(image_path)
    img.load()
    w, h = img.size
    ax = w // 2 if axis_x is None else axis_x
    ay = h if axis_y is None else axis_y
    if keep_indices and img.mode == "P":
        old = char.sprites.find(group, item)
        pi = palette_index if palette_index is not None else (
            old.palette_index if old is not None and old.palette_index is not None else
            max(char.sprites.find_palette(1, 1), 0))
        spr = char.sprites.set_sprite(group, item, img, ax, ay, pi)
        char.mark("sprites")
        return spr.info()
    truecolor = img.mode not in ("P", "L") and keep_own_palette
    if not truecolor:
        img = img if keep_own_palette and img.mode == "P" else _to_indexed(img, char)
    if autocrop:
        box = (img.getchannel("A") if img.mode == "RGBA" else
               Image.frombytes("L", img.size, bytes(1 if v else 0 for v in img.tobytes()))).getbbox()
        if box:
            img = img.crop(box)
            ax, ay = ax - box[0], ay - box[1]
    pal_index = None if (keep_own_palette or char.sprites.find_palette(1, 1) < 0) else char.sprites.find_palette(1, 1)
    spr = char.sprites.set_sprite(group, item, img, ax, ay, pal_index)
    char.mark("sprites")
    return spr.info()


def import_sprites_csv(char: Character, csv_path: str | Path, **kw) -> dict:
    """Importa vários sprites a partir de um CSV no formato do export (group,item,file,axis_x,axis_y)."""
    base = Path(csv_path).parent
    done = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.append(import_sprite(char, base / row["file"], int(row["group"]), int(row["item"]),
                                      int(row["axis_x"]), int(row["axis_y"]), **kw))
    return {"imported": len(done)}


def remove_sprite(char: Character, group: int, item: int) -> bool:
    ok = char.sprites.remove_sprite(group, item)
    if ok:
        char.mark("sprites")
    return ok


def set_sprite_image(char: Character, group: int, item: int, image) -> dict:
    """Troca os pixels de um sprite mantendo eixo e paleta (o editor de imagem embutido).
    Imagem 'P' entra com os índices como estão; RGBA vira truecolor."""
    old = char.sprites.find(group, item)
    if old is None:
        raise ValueError(tr("sprite {0},{1} does not exist").format(group, item))
    pi = old.palette_index if image.mode == "P" else None
    if image.mode == "P" and pi is None:
        pi = max(char.sprites.find_palette(1, 1), 0)
    spr = char.sprites.set_sprite(group, item, image, old.axis_x, old.axis_y, pi)
    char.mark("sprites")
    return spr.info()


def set_sprite_axis(char: Character, group: int, item: int, axis_x: int, axis_y: int) -> dict:
    spr = char.sprites.find(group, item)
    if spr is None:
        raise ValueError(tr("sprite {0},{1} does not exist").format(group, item))
    spr.axis_x, spr.axis_y = axis_x, axis_y
    char.mark("sprites")
    return spr.info()


# ================================================================ paletas


def list_palettes(char: Character) -> list[dict]:
    out = [{"index": i, "group": p.group, "item": p.item, "colors": len(p.colors)}
           for i, p in enumerate(char.sprites.palettes)]
    for n, path in char.palette_files().items():
        out.append({"act_slot": n, "file": path.name, "exists": path.exists()})
    return out


def export_palette(char: Character, palette: int, path: str | Path) -> str:
    from .render import palette_for
    p = palette_for(char, palette)
    act.save(p.colors, path)
    return str(path)


def import_palette(char: Character, slot: int, act_path: str | Path) -> dict:
    """Coloca um .act como paleta 1,slot do SFF (v2)."""
    cols = act.load(act_path)
    s = char.sprites
    ncol = len(s.palettes[s.find_palette(1, 1)].colors) if s.find_palette(1, 1) >= 0 else 256
    cols = cols[:ncol]
    idx = s.find_palette(1, slot)
    if idx >= 0:
        s.palettes[idx].colors = cols
    else:
        s.palettes.append(sff.Palette(1, slot, cols))
    char.mark("sprites")
    return {"slot": slot, "colors": len(cols)}


def make_palette_variant(char: Character, slot: int, hue_shift: float = 0.0,
                         saturation: float = 1.0, brightness: float = 1.0,
                         base: int = 1, keep_indices: list[int] | None = None) -> dict:
    """Cria a paleta 1,slot girando matiz/saturação/brilho da paleta base.

    keep_indices: cores que não mudam (pele, olhos…). O índice 0 nunca muda.
    """
    s = char.sprites
    bi = s.find_palette(1, base)
    if bi < 0:
        raise ValueError(tr("base palette 1,{0} does not exist").format(base))
    keep = set(keep_indices or []) | {0}
    new = []
    for i, (r, g, b) in enumerate(s.palettes[bi].colors):
        if i in keep:
            new.append((r, g, b))
            continue
        h, l, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        h = (h + hue_shift / 360) % 1
        sat = min(1, sat * saturation)
        l = min(1, l * brightness)
        nr, ng, nb = colorsys.hls_to_rgb(h, l, sat)
        new.append((round(nr * 255), round(ng * 255), round(nb * 255)))
    idx = s.find_palette(1, slot)
    if idx >= 0:
        s.palettes[idx].colors = new
    else:
        s.palettes.append(sff.Palette(1, slot, new))
    char.mark("sprites")
    return {"slot": slot, "colors": len(new)}


# ================================================================ animações


def list_actions(char: Character) -> list[dict]:
    return [char.anims.actions[n].info(frames=False) for n in char.anims.numbers()]


def get_action(char: Character, number: int) -> dict:
    a = char.anims.get(number)
    if a is None:
        raise ValueError(tr("action {0} does not exist").format(number))
    return a.info()


def get_action_text(char: Character, number: int) -> str:
    a = char.anims.get(number)
    if a is None:
        raise ValueError(tr("action {0} does not exist").format(number))
    return "\n".join([f"[Begin Action {number}]", *a.to_lines()])


def set_action_text(char: Character, text: str) -> dict:
    """Cria/substitui ações a partir de texto AIR (um ou vários [Begin Action N])."""
    parsed = air.loads(text)
    if not parsed.actions:
        raise ValueError(tr("no [Begin Action N] in the text"))
    for act in parsed.actions.values():
        char.anims.set_action(act)
    char.mark("anims")
    return {"actions": sorted(parsed.actions)}


def create_action(char: Character, number: int, frames: list[dict], loopstart: int = 0,
                  auto_clsn2: bool = True) -> dict:
    """frames: [{"group":g,"item":i,"time":t,"x":0,"y":0,"flip":"","trans":"",
    "clsn1":[[x1,y1,x2,y2]],"clsn2":[...]}]."""
    act = air.Action(number, loopstart=loopstart)
    for fd in frames:
        f = air.Frame(int(fd["group"]), int(fd["item"]), int(fd.get("x", 0)), int(fd.get("y", 0)),
                      int(fd.get("time", 4)), fd.get("flip", ""), fd.get("trans", ""))
        f.clsn1 = [tuple(b) for b in fd.get("clsn1", [])]
        f.clsn2 = [tuple(b) for b in fd.get("clsn2", [])]
        if auto_clsn2 and not f.clsn2 and "clsn2" not in fd:
            f.clsn2 = auto_clsn(char, f.group, f.item)
        act.frames.append(f)
    char.anims.set_action(act)
    char.mark("anims")
    return act.info(frames=False)


def auto_clsn(char: Character, group: int, item: int, margin: int = 2) -> list[tuple]:
    """Caixa de colisão (Clsn2) a partir da área visível do sprite, relativa ao eixo."""
    spr = char.sprites.find(group, item)
    if spr is None:
        return []
    img = char.sprites.image(spr)
    mask = img.getchannel("A") if img.mode == "RGBA" else \
        Image.frombytes("L", img.size, bytes(255 if v else 0 for v in char.sprites.pixels(spr)))
    box = mask.getbbox()
    if not box:
        return []
    l, t, r, b = box
    return [(l - spr.axis_x + margin, t - spr.axis_y + margin,
             r - spr.axis_x - margin, b - spr.axis_y)]


def anatomical_clsn(char: Character, group: int, item: int, zones: int = 3, margin: int = 2) -> list[tuple]:
    """Caixas de colisão anatômicas (Clsn2) segmentadas (cabeça, tronco, pernas)."""
    from . import clsnkit
    return clsnkit.anatomical_clsn2(char, group, item, zones, margin)


def attack_clsn(char: Character, group: int, item: int, reach_margin: int = 2) -> list[tuple]:
    """Caixas de ataque (Clsn1) automáticas para projeções e membros estendidos."""
    from . import clsnkit
    return clsnkit.attack_clsn1(char, group, item, reach_margin)



def remove_action(char: Character, number: int) -> bool:
    ok = char.anims.remove_action(number)
    if ok:
        char.mark("anims")
    return ok


# ================================================================ estados e comandos


def list_statedefs(char: Character, include_common: bool = False) -> list[dict]:
    out = []
    for n, (sd, st) in sorted(char.all_statedefs(include_common).items()):
        d = sd.info()
        d["file"] = st.path.name if st.path else "?"
        out.append(d)
    return out


def get_statedef_text(char: Character, number: int, lowered: bool = False) -> str:
    """Texto do statedef no formato do arquivo (ZSS ou CNS). `lowered`: o CNS equivalente
    de um estado ZSS, que é o que a simulação lê."""
    sd = char.all_statedefs().get(number)
    if not sd:
        raise ValueError(tr("statedef {0} does not exist").format(number))
    if hasattr(sd[1], "statedef_zss") and not lowered:
        return sd[1].statedef_zss(number)
    return sd[0].text()


def set_statedef_text(char: Character, number: int, text: str) -> dict:
    st = char.states_file_for(number)
    sd = st.set_statedef_text(number, text)
    char.mark("commands" if st is char.commands.states else "states")
    return sd.info()


def add_controller(char: Character, number: int, text: str) -> dict:
    st = char.states_file_for(number)
    if number not in st.statedefs:
        raise ValueError(tr("statedef {0} does not exist in the character").format(number))
    c = st.add_controller(number, text)
    char.mark("commands" if st is char.commands.states else "states")
    return {"statedef": number, "label": c.label, "type": c.type}


def remove_statedef(char: Character, number: int) -> bool:
    st = char.states_file_for(number)
    ok = st.remove_statedef(number)
    if ok:
        char.mark("commands" if st is char.commands.states else "states")
    return ok


def list_commands(char: Character) -> list[dict]:
    return [c.info() for c in char.commands.commands()]


def add_command(char: Character, name: str, sequence: str, time: int | None = None) -> dict:
    c = char.commands.add_command(name, sequence, time)
    char.mark("commands")
    return c.info()


def get_constants(char: Character) -> dict:
    doc = char.constants
    return {s.name: s.as_dict() for s in doc.sections
            if s.kind in ("data", "size", "velocity", "movement", "quotes")}


def set_constant(char: Character, section: str, key: str, value: str) -> dict:
    sec = char.constants.find(section)
    if sec is None:
        raise ValueError(tr("section [{0}] does not exist in the constants file").format(section))
    sec.set(key, value)
    char.mark("states")
    return sec.as_dict()


# ================================================================ sons


def list_sounds(char: Character) -> list[dict]:
    return [{"group": s.group, "number": s.number, **s.info()} for s in char.sounds.sounds]


def import_sound(char: Character, wav_path: str | Path, group: int, number: int) -> dict:
    sndfmt.set_sound(char.sounds, group, number, Path(wav_path).read_bytes())
    char.mark("sounds")
    return {"group": group, "number": number}


def export_sound(char: Character, group: int, number: int, wav_path: str | Path) -> str:
    s = char.sounds.find(group, number)
    if s is None:
        raise ValueError(tr("sound {0},{1} does not exist").format(group, number))
    Path(wav_path).write_bytes(s.wav)
    return str(wav_path)


def info_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


# ================================================================ variáveis

_VAR_REF = __import__("re").compile(r"\b(sys)?(f)?var\s*\(\s*(\d+)\s*\)", __import__("re").I)
_VAR_WRITE_CTRLS = {"varset", "varadd", "varrandom", "varrangeset", "parentvarset", "parentvaradd"}


def var_usage(char: Character) -> dict:
    """Onde cada var/fvar/sysvar é lida e escrita (statedef e controller).

    Útil para escolher uma variável livre antes de criar lógica nova."""
    import re
    usage: dict[str, dict[str, set]] = {}

    def note(kind: str, n: int, mode: str, where: str) -> None:
        usage.setdefault(f"{kind}({n})", {"read": set(), "write": set()})[mode].add(where)

    files = [*char.states, char.commands.states]
    for st in files:
        for sd_num, sd in st.statedefs.items():
            for c in sd.controllers:
                where = f"{sd_num}:{c.label}"
                t = c.type.lower()
                for e in c.section.entries():
                    key = e.key.lower().replace(" ", "")
                    m = _VAR_REF.match(key)
                    if m and key == m.group(0).lower().replace(" ", ""):  # var(3) = valor
                        note(f"{'sys' if m[1] else ''}{'f' if m[2] else ''}var", int(m[3]), "write", where)
                    if t in _VAR_WRITE_CTRLS and key in ("v", "fv"):
                        n = re.match(r"\s*(\d+)", e.value)
                        if n:
                            note("fvar" if key == "fv" else "var", int(n[1]), "write", where)
                    for r in _VAR_REF.finditer(e.value):
                        kind = f"{'sys' if r[1] else ''}{'f' if r[2] else ''}var"
                        after = e.value[r.end():].lstrip()
                        note(kind, int(r[3]), "write" if after.startswith(":=") else "read", where)
                if t in _VAR_WRITE_CTRLS and c.section.get("first") is not None:
                    a = re.match(r"\s*(\d+)", c.section.get("first") or "")
                    b = re.match(r"\s*(\d+)", c.section.get("last") or "")
                    fv = c.section.get("fvalue") is not None
                    if a and b:
                        for n in range(int(a[1]), int(b[1]) + 1):
                            note("fvar" if fv else "var", n, "write", where)
    used = {k: {"read": sorted(v["read"]), "write": sorted(v["write"])}
            for k, v in sorted(usage.items(), key=lambda kv: (kv[0].split("(")[0], int(kv[0].split("(")[1][:-1])))}
    free_vars = [n for n in range(60) if f"var({n})" not in usage]
    free_fvars = [n for n in range(40) if f"fvar({n})" not in usage]
    return {"used": used, "free_var": free_vars, "free_fvar": free_fvars}


# ================================================================ alinhamento

_ANCHORS = {"topleft": (0, 0), "top": (.5, 0), "topright": (1, 0), "left": (0, .5),
            "center": (.5, .5), "right": (1, .5), "bottomleft": (0, 1), "bottom": (.5, 1),
            "bottomright": (1, 1)}


def align_sprites(char: Character, anchor: str, group: int | None = None,
                  sprites: list[list[int]] | None = None, offset_x: int = 0, offset_y: int = 0) -> dict:
    """Define o eixo pela âncora (ex.: 'bottom' = centro dos pés)."""
    fx, fy = _ANCHORS[anchor.lower().replace("_", "").replace("-", "")]
    targets = [char.sprites.find(g, i) for g, i in sprites] if sprites else \
        [s for s in char.sprites.sprites if group is None or s.group == group]
    n = 0
    for s in targets:
        if s is None:
            continue
        s.axis_x = round(s.width * fx) + offset_x
        s.axis_y = round(s.height * fy) + offset_y
        n += 1
    if n:
        char.mark("sprites")
    return {"aligned": n, "anchor": anchor}


def export_sprites_aligned(char: Character, out_dir: str | Path, group: int | None = None,
                           first: int | None = None, last: int | None = None,
                           palette: int | None = None, ext: str = "png",
                           write_axis_txt: bool = True) -> dict:
    """Exporta sprites num quadro comum, com todos os eixos no mesmo ponto
    Arquivos 'grupo-nº.ext' + axis.txt ('X axis: N')."""
    from .render import palette_for
    s = char.sprites
    sel = [sp for sp in s.sprites
           if (group is None or sp.group == group)
           and (first is None or sp.item >= first) and (last is None or sp.item <= last)]
    if not sel:
        raise ValueError(tr("no sprite in the range"))
    ax = max(sp.axis_x for sp in sel)
    ay = max(sp.axis_y for sp in sel)
    w = ax + max(sp.width - sp.axis_x for sp in sel)
    h = ay + max(sp.height - sp.axis_y for sp in sel)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pal = palette_for(char, palette)
    for sp in sel:
        img = s.image(sp, pal if not sp.is_truecolor else None)
        canvas = Image.new(img.mode, (w, h), 0 if img.mode == "P" else (0, 0, 0, 0))
        if img.mode == "P":
            canvas.putpalette(img.getpalette())
        canvas.paste(img, (ax - sp.axis_x, ay - sp.axis_y))
        name = out / f"{sp.group}-{sp.item}.{ext}"
        canvas.save(name, transparency=0) if canvas.mode == "P" and ext == "png" else canvas.save(name)
    if write_axis_txt:
        (out / "axis.txt").write_bytes(f"X axis: {ax}\r\nY axis: {ay}".encode())
    return {"exported": len(sel), "canvas": [w, h], "axis": [ax, ay], "dir": str(out)}


def var_list_comment(char: Character, only_used: bool = True) -> str:
    """Bloco de comentário com a lista de variáveis e onde são usadas (para inserir no CNS)."""
    u = var_usage(char)["used"]
    out = []
    for kind, count, title in (("var", 60, "Integer"), ("fvar", 40, "Float")):
        out.append(f";---------------------- {title} variable list ----------------------\n")
        for n in range(count):
            info = u.get(f"{kind}({n})")
            if only_used and not info:
                continue
            states = sorted({w.split(":")[0] for w in (info["read"] + info["write"]) if info} if info else set(),
                            key=lambda s: int(s) if s.lstrip("-").isdigit() else 0)
            out.append(f";{' ' if n < 10 else ''}{kind}({n}):= {', '.join(states)}")
        out.append("")
    return "\n".join(out)


# ================================================================ sprite sheet


def import_sheet(char: Character, csv_path: str | Path, palette: str = "character",
                 action: int | None = None, frame_time: int = 5, loopstart: int = 0) -> dict:
    """Importa um recorte de sprite sheet (sprites.csv de sheet.slice_sheet).

    palette: 'character' = converte para a paleta 1,1 do personagem;
             'main' = a paleta da sheet vira a 1,1 (personagem novo / redesenho completo);
             'own' = os sprites ficam com a paleta da sheet, separada.
    action: se dado, cria a animação com os sprites na ordem do recorte."""
    base = Path(csv_path).parent
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    s = char.sprites
    if s.find_palette(1, 1) < 0 and palette == "character":
        palette = "main"  # personagem sem paleta: a da sheet vira a principal
    pal_index = None
    if palette == "main":
        first = Image.open(base / rows[0]["file"])
        raw = first.getpalette() or []
        ncol = max(max(Image.open(base / r["file"]).tobytes()) for r in rows) + 1
        cols = [tuple(raw[i * 3:i * 3 + 3]) for i in range(ncol)]
        idx = s.find_palette(1, 1)
        if idx >= 0:
            s.palettes[idx].colors = cols
            pal_index = idx
        else:
            s.palettes.insert(0, sff.Palette(1, 1, cols))
            for spr in s.sprites:  # índices de paleta deslocados pela inserção
                if spr.palette_index is not None:
                    spr.palette_index += 1
            pal_index = 0
    done = []
    for r in rows:
        g, i = int(r["group"]), int(r["item"])
        ax, ay = int(r["axis_x"]), int(r["axis_y"])
        img = Image.open(base / r["file"])
        img.load()
        if palette == "character":
            done.append(import_sprite(char, base / r["file"], g, i, ax, ay, autocrop=False))
        else:
            spr = s.set_sprite(g, i, img, ax, ay, pal_index)  # 'own': paleta própria (deduplicada)
            done.append(spr.info())
    char.mark("sprites")
    result = {"imported": len(done), "palette_mode": palette}
    if action is not None:
        frames = [{"group": int(r["group"]), "item": int(r["item"]), "time": frame_time} for r in rows]
        result["action"] = create_action(char, action, frames, loopstart)
    return result


def generate_move_code(char: Character | None, **data) -> dict:
    """Gera o código CNS, entrada no CMD e gatilho em [Statedef -1] para o golpe."""
    from . import movegen
    cns = movegen.build_move_cns(char, data)
    cmd, minus1 = movegen.build_command_cns(data)
    return {"cns": cns, "command": cmd, "minus1": minus1}


def create_move(char: Character, **data) -> dict:
    """Aplica o golpe diretamente ao personagem aberto (StateDef + Command + Statedef -1)."""
    from . import movegen
    return movegen.apply_move_to_character(char, data)

