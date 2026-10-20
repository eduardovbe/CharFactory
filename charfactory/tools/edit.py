"""Edições em lote: sprites, animações, paletas e sons.

Comportamentos de paleta:
gradient = interpolação linear com extremos exatos; sortByTones = cada posição recebe a cor
mais próxima da anterior, índice 0 fixo; clearDuplicated = repetidas viram a cor 'fill'.
Diferença deliberada: ao reordenar cores, os pixels dos sprites são remapeados.
"""
from __future__ import annotations

import copy
import io
import wave

import numpy as np
from PIL import Image

from ..formats import snd as sndfmt
from ..project import Character
from .render import frame_scale_angle
from ..i18n import tr


def _rgb(c) -> tuple[int, int, int]:
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[j:j + 2], 16) for j in (0, 2, 4))
    return tuple(int(v) for v in c[:3])


# ================================================================ sprites

def _sprite(char: Character, group: int, item: int):
    s = char.sprites.find(group, item)
    if s is None:
        raise ValueError(tr("sprite {0},{1} does not exist").format(group, item))
    return s


def _editable(char: Character, s) -> Image.Image:
    if s.is_truecolor:
        return char.sprites.image(s)
    return Image.frombytes("P", (s.width, s.height), char.sprites.pixels(s))


def duplicate_sprite(char: Character, group: int, item: int, new_group: int, new_item: int) -> dict:
    src = _sprite(char, group, item)
    spr = char.sprites.set_sprite(new_group, new_item, _editable(char, src), src.axis_x, src.axis_y,
                                  None if src.is_truecolor else src.palette_index)
    char.mark("sprites")
    return spr.info()


def renumber_group(char: Character, group: int, new_group: int, item_offset: int = 0) -> dict:
    """Move os sprites de um grupo para outro e atualiza as animações que os usam."""
    targets = [s for s in char.sprites.sprites if s.group == group]
    for s in targets:
        other = char.sprites.find(new_group, s.item + item_offset)
        if other is not None and other not in targets:
            raise ValueError(tr("{0},{1} already exists").format(new_group, s.item + item_offset))
    for s in targets:
        s.group, s.item = new_group, s.item + item_offset
    for act in char.anims.actions.values():
        for f in act.frames:
            if f.group == group:
                f.group, f.item = new_group, f.item + item_offset
    char.mark("sprites", "anims")
    return {"moved": len(targets)}


def delete_group(char: Character, group: int) -> dict:
    items = [s.item for s in char.sprites.sprites if s.group == group]
    for i in items:
        char.sprites.remove_sprite(group, i)
    if items:
        char.mark("sprites")
    return {"deleted": len(items)}


def transform_sprite(char: Character, group: int, item: int, flip_h: bool = False,
                     flip_v: bool = False, rotate: int = 0, scale: float = 1.0,
                     trim: bool = False) -> dict:
    """Espelha, gira (90/180/270, horário), redimensiona (vizinho mais próximo) e/ou apara,
    mantendo o eixo no mesmo ponto da figura."""
    s = _sprite(char, group, item)
    img = _editable(char, s)
    ax, ay = s.axis_x, s.axis_y
    if flip_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        ax = img.width - 1 - ax
    if flip_v:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        ay = img.height - 1 - ay
    for _ in range((rotate // 90) % 4):
        h = img.height
        img = img.transpose(Image.Transpose.ROTATE_270)
        ax, ay = h - 1 - ay, ax
    if scale != 1.0:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.Resampling.NEAREST)
        ax, ay = round(ax * scale), round(ay * scale)
    if trim:
        mask = img.getchannel("A") if img.mode == "RGBA" else \
            Image.frombytes("L", img.size, bytes(1 if v else 0 for v in img.tobytes()))
        box = mask.getbbox()
        if box:
            img = img.crop(box)
            ax, ay = ax - box[0], ay - box[1]
    spr = char.sprites.set_sprite(group, item, img, ax, ay, None if s.is_truecolor else s.palette_index)
    char.mark("sprites")
    return spr.info()


def crop_sprite(char: Character, group: int, item: int, x: int, y: int, width: int, height: int) -> dict:
    """Recorta o retângulo (x, y, largura, altura) da imagem; o eixo fica no mesmo ponto da figura."""
    s = _sprite(char, group, item)
    img = _editable(char, s)
    box = (max(0, x), max(0, y), min(img.width, x + width), min(img.height, y + height))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError(tr("rectangle outside the image"))
    spr = char.sprites.set_sprite(group, item, img.crop(box), s.axis_x - box[0], s.axis_y - box[1],
                                  None if s.is_truecolor else s.palette_index)
    char.mark("sprites")
    return spr.info()


def resize_sprite(char: Character, group: int, item: int, width: int, height: int) -> dict:
    """Redimensiona para o tamanho exato (vizinho mais próximo, paleta preservada)."""
    s = _sprite(char, group, item)
    img = _editable(char, s)
    sx, sy = width / img.width, height / img.height
    out = img.resize((max(1, width), max(1, height)), Image.Resampling.NEAREST)
    spr = char.sprites.set_sprite(group, item, out, round(s.axis_x * sx), round(s.axis_y * sy),
                                  None if s.is_truecolor else s.palette_index)
    char.mark("sprites")
    return spr.info()


def rotate_sprite(char: Character, group: int, item: int, angle: float) -> dict:
    """Gira em qualquer ângulo (graus, anti-horário) em torno do eixo, sem cortar a figura."""
    s = _sprite(char, group, item)
    img = _editable(char, s)
    pad = int((img.width ** 2 + img.height ** 2) ** 0.5) + 2
    mode_fill = 0 if img.mode == "P" else (0, 0, 0, 0)
    canvas = Image.new(img.mode, (pad * 2, pad * 2), mode_fill)
    if img.mode == "P":
        canvas.putpalette(img.getpalette() or [])
    canvas.paste(img, (pad - s.axis_x, pad - s.axis_y))
    rot = canvas.rotate(angle, resample=Image.Resampling.NEAREST, center=(pad, pad), fillcolor=mode_fill)
    mask = rot.getchannel("A") if rot.mode == "RGBA" else \
        Image.fromarray((np.asarray(rot) != 0).astype(np.uint8) * 255, "L")
    box = mask.getbbox() or (pad, pad, pad + 1, pad + 1)
    spr = char.sprites.set_sprite(group, item, rot.crop(box), pad - box[0], pad - box[1],
                                  None if s.is_truecolor else s.palette_index)
    char.mark("sprites")
    return spr.info()


def renumber_sprite(char: Character, group: int, item: int, new_group: int, new_item: int,
                    update_anims: bool = True) -> dict:
    """Muda o número de um sprite e, por padrão, os frames de animação que apontam para ele."""
    s = _sprite(char, group, item)
    if char.sprites.find(new_group, new_item):
        raise ValueError(tr("sprite {0},{1} already exists").format(new_group, new_item))
    s.group, s.item = new_group, new_item
    char.mark("sprites")
    frames = 0
    if update_anims:
        try:
            for act in char.anims.actions.values():
                for f in act.frames:
                    if (f.group, f.item) == (group, item):
                        f.group, f.item = new_group, new_item
                        frames += 1
        except Exception:  # noqa: BLE001 — personagem sem AIR
            pass
        if frames:
            char.mark("anims")
    return {"sprite": [new_group, new_item], "frames_updated": frames}


def set_sprites_palette(char: Character, sprites: list[list[int]], slot: int) -> dict:
    """Faz os sprites (indexados) usarem a paleta 1,slot."""
    i = char.sprites.find_palette(1, slot)
    if i < 0:
        raise ValueError(tr("palette 1,{0} does not exist").format(slot))
    n = 0
    for g, it in sprites:
        s = char.sprites.find(g, it)
        if s is not None and not s.is_truecolor:
            s.palette_index = i
            n += 1
    if n:
        char.mark("sprites")
    return {"slot": slot, "sprites": n}


def remap_palette_index(char: Character, slot: int, src: int, dst: int) -> dict:
    """Remap: os pixels de índice `src` dos sprites que usam a paleta passam a `dst`."""
    p = _pal(char, slot)
    mapping = list(range(256))
    mapping[src] = dst
    n = _remap_sprites(char, char.sprites.palettes.index(p), mapping)
    char.mark("sprites")
    return {"slot": slot, "sprites_remapped": n}


def used_palette_indices(char: Character, slot: int) -> set[int]:
    p = _pal(char, slot)
    pi = char.sprites.palettes.index(p)
    used: set[int] = set()
    for s in char.sprites.sprites:
        if s.palette_index == pi and not s.is_truecolor:
            used |= set(char.sprites.pixels(s))
    return used


def clear_unused_colors(char: Character, slot: int, fill="#000000") -> dict:
    """Cores que nenhum sprite da paleta usa viram 'fill'."""
    p = _pal(char, slot)
    used = used_palette_indices(char, slot)
    n = 0
    for i in range(1, len(p.colors)):
        if i not in used and p.colors[i] != _rgb(fill):
            p.colors[i] = _rgb(fill)
            n += 1
    if n:
        char.mark("sprites")
    return {"slot": slot, "cleared": n}


def set_palette_size(char: Character, slot: int, count: int) -> dict:
    """Muda a quantidade de cores da paleta (16, 32, 64, 128, 256)."""
    p = _pal(char, slot)
    count = max(2, min(256, int(count)))
    p.colors = (list(p.colors) + [(0, 0, 0)] * 256)[:count]
    char.mark("sprites")
    return {"slot": slot, "colors": count}


def delete_palette(char: Character, slot: int) -> dict:
    """Apaga a paleta 1,slot; os sprites que a usavam passam para a 1,1."""
    s = char.sprites
    i = s.find_palette(1, slot)
    if i < 0:
        raise ValueError(tr("palette 1,{0} does not exist").format(slot))
    base = s.find_palette(1, 1)
    if i == base:
        raise ValueError(tr("palette 1,1 is the character's own and cannot be deleted"))
    del s.palettes[i]
    for spr in s.sprites:
        if spr.palette_index is None:
            continue
        if spr.palette_index == i:
            spr.palette_index = base if base < i else base - 1
        elif spr.palette_index > i:
            spr.palette_index -= 1
    char.mark("sprites")
    return {"deleted": slot}


def trim_all_sprites(char: Character, group: int | None = None) -> dict:
    """Apara a borda transparente de todos os sprites, mantendo o eixo."""
    n = 0
    for s in list(char.sprites.sprites):
        if group is not None and s.group != group:
            continue
        before = (s.width, s.height)
        info = transform_sprite(char, s.group, s.item, trim=True)
        n += (info["width"], info["height"]) != before
    return {"trimmed": n}


# ================================================================ animações

def _act(char: Character, number: int):
    a = char.anims.get(number)
    if a is None:
        raise ValueError(tr("action {0} does not exist").format(number))
    return a


def duplicate_action(char: Character, number: int, new_number: int) -> dict:
    a = copy.deepcopy(_act(char, number))
    a.number = new_number
    char.anims.set_action(a)
    char.mark("anims")
    return a.info(frames=False)


def set_all_frames(char: Character, number: int, time: int | None = None, offset_dx: int = 0,
                   offset_dy: int = 0, flip: str | None = None, trans: str | None = None,
                   xscale: float | None = None, yscale: float | None = None,
                   angle: float | None = None) -> dict:
    """Altera todos os frames da ação de uma vez (escala/ângulo do MUGEN 1.1 inclusive)."""
    a = _act(char, number)
    for i, f in enumerate(a.frames):
        if time is not None:
            f.time = time
        f.x += offset_dx
        f.y += offset_dy
        if flip is not None:
            f.flip = flip
        if trans is not None:
            f.trans = trans
        if (xscale, yscale, angle) != (None, None, None):
            set_frame(char, number, i, xscale=xscale, yscale=yscale, angle=angle)
    char.mark("anims")
    return a.info(frames=False)


def set_frame(char: Character, number: int, index: int, **fields) -> dict:
    """Altera um frame: group, item, x, y, time, flip, trans, clsn1, clsn2, e os campos do
    MUGEN 1.1 xscale, yscale, angle e interpolate (lista com offset/blend/scale/angle)."""
    f = _act(char, number).frames[index]
    sa = {k: fields.pop(k) for k in ("xscale", "yscale", "angle") if k in fields}
    for k, v in fields.items():
        if v is None:
            continue
        if k == "interpolate":
            bad = [t for t in v if t.lower() not in INTERPOLATE_KINDS]
            if bad:
                raise ValueError(tr("invalid interpolation: {0} (use {1})").format(bad, ', '.join(INTERPOLATE_KINDS)))
            v = [t.lower() for t in v]
        setattr(f, k, [tuple(b) for b in v] if k in ("clsn1", "clsn2") else v)
    if any(v is not None for v in sa.values()):
        cur = dict(zip(("xscale", "yscale", "angle"), frame_scale_angle(f)))
        cur.update({k: v for k, v in sa.items() if v is not None})
        vals = (cur["xscale"], cur["yscale"], cur["angle"])
        f.extra = [] if vals == (1, 1, 0) else [f"{v:g}" for v in vals]
    char.mark("anims")
    return f.info()


INTERPOLATE_KINDS = ("offset", "blend", "scale", "angle")


def apply_clsn_to_sprite(char: Character, group: int, item: int, clsn1: list | None = None,
                         clsn2: list | None = None) -> dict:
    """Copia caixas para todos os frames, em todas as ações, que usam o sprite."""
    n = 0
    for a in char.anims.actions.values():
        for f in a.frames:
            if (f.group, f.item) == (group, item):
                if clsn1 is not None:
                    f.clsn1 = [tuple(b) for b in clsn1]
                if clsn2 is not None:
                    f.clsn2 = [tuple(b) for b in clsn2]
                n += 1
    if n:
        char.mark("anims")
    return {"frames_updated": n}


def optimize_action(char: Character, number: int) -> dict:
    """Junta frames consecutivos idênticos somando o tempo."""
    a = _act(char, number)
    out = []
    for i, f in enumerate(a.frames):
        p = out[-1] if out else None
        same = p is not None and (p.group, p.item, p.x, p.y, p.flip, p.trans, p.clsn1, p.clsn2, p.extra) == \
            (f.group, f.item, f.x, f.y, f.flip, f.trans, f.clsn1, f.clsn2, f.extra)
        if same and p.time > 0 and f.time > 0 and not f.interpolate and i != a.loopstart:
            p.time += f.time
        else:
            if i == a.loopstart:
                a.loopstart = len(out)
            out.append(f)
    removed = len(a.frames) - len(out)
    a.frames = out
    if removed:
        char.mark("anims")
    return {"removed_frames": removed, **a.info(frames=False)}


# ================================================================ paletas

def _pal(char: Character, slot: int):
    i = char.sprites.find_palette(1, slot)
    if i < 0:
        raise ValueError(tr("palette 1,{0} does not exist").format(slot))
    return char.sprites.palettes[i]


def _grow(p, n: int) -> None:
    while len(p.colors) <= n:
        p.colors.append((0, 0, 0))


def set_palette_colors(char: Character, slot: int, colors: dict) -> dict:
    """colors: {índice: [r,g,b] ou '#rrggbb'}."""
    p = _pal(char, slot)
    for k, v in colors.items():
        _grow(p, int(k))
        p.colors[int(k)] = _rgb(v)
    char.mark("sprites")
    return {"slot": slot, "set": len(colors)}


def palette_gradient(char: Character, slot: int, start: int, end: int, color_a, color_b) -> dict:
    """Preenche os índices start..end com degradê linear de color_a até color_b."""
    p = _pal(char, slot)
    ca, cb = _rgb(color_a), _rgb(color_b)
    n = end - start
    _grow(p, end)
    for k in range(n + 1):
        t = k / n if n else 0
        p.colors[start + k] = tuple(round(a + (b - a) * t) for a, b in zip(ca, cb))
    char.mark("sprites")
    return {"slot": slot, "filled": n + 1}


def palette_effect(char: Character, slot: int, effect: str, indices: list[int] | None = None) -> dict:
    """effect: 'grayscale' ou 'invert'. O índice 0 (transparente) é preservado."""
    if effect not in ("grayscale", "invert"):
        raise ValueError(tr("the effect must be 'grayscale' or 'invert'"))
    p = _pal(char, slot)
    sel = [i for i in (indices or range(1, len(p.colors))) if 0 < i < len(p.colors)]
    for i in sel:
        r, g, b = p.colors[i]
        if effect == "grayscale":
            y = round(0.299 * r + 0.587 * g + 0.114 * b)
            p.colors[i] = (y, y, y)
        else:
            p.colors[i] = (255 - r, 255 - g, 255 - b)
    char.mark("sprites")
    return {"slot": slot, "changed": len(sel)}


def _remap_sprites(char: Character, palette_index: int, mapping: list[int]) -> int:
    """Reescreve os pixels dos sprites que usam a paleta: novo índice = mapping[antigo]."""
    s = char.sprites
    lut = bytes(mapping[i] if i < len(mapping) else i for i in range(256))
    n = 0
    for spr in list(s.sprites):
        if spr.palette_index == palette_index and not spr.is_truecolor:
            img = Image.frombytes("P", (spr.width, spr.height), s.pixels(spr).translate(lut))
            s.set_sprite(spr.group, spr.item, img, spr.axis_x, spr.axis_y, palette_index)
            n += 1
    return n


def swap_palette_colors(char: Character, slot: int, a: int, b: int, remap_sprites: bool = True) -> dict:
    """Troca duas cores de posição; com remap_sprites a imagem continua igual."""
    p = _pal(char, slot)
    _grow(p, max(a, b))
    p.colors[a], p.colors[b] = p.colors[b], p.colors[a]
    n = 0
    if remap_sprites:
        mapping = list(range(256))
        mapping[a], mapping[b] = b, a
        n = _remap_sprites(char, char.sprites.palettes.index(p), mapping)
    char.mark("sprites")
    return {"slot": slot, "sprites_remapped": n}


def sort_palette_by_tones(char: Character, slot: int = 1, remap_sprites: bool = True) -> dict:
    """Ordena por proximidade de tom. Com remap_sprites a imagem não muda."""
    p = _pal(char, slot)
    cols = list(p.colors)
    order = list(range(len(cols)))
    for i in range(1, len(cols) - 1):
        prev = cols[order[i - 1]]
        best = min(range(i, len(cols)), key=lambda j: sum((x - y) ** 2 for x, y in zip(cols[order[j]], prev)))
        order[i], order[best] = order[best], order[i]
    p.colors = [cols[k] for k in order]
    n = 0
    if remap_sprites:
        inverse = list(range(256))
        for new, old in enumerate(order):
            inverse[old] = new
        n = _remap_sprites(char, char.sprites.palettes.index(p), inverse)
    char.mark("sprites")
    return {"slot": slot, "sprites_remapped": n}


def clear_duplicate_colors(char: Character, slot: int, fill="#000000") -> dict:
    """Cores repetidas de um índice anterior viram 'fill'."""
    p = _pal(char, slot)
    seen, n = set(), 0
    for i, c in enumerate(p.colors):
        if i and c in seen:
            p.colors[i] = _rgb(fill)
            n += 1
        seen.add(c)
    if n:
        char.mark("sprites")
    return {"slot": slot, "cleared": n}


def merge_same_number_palettes(char: Character) -> dict:
    """Paletas repetidas com o mesmo grupo/item: fica a primeira (é a que o MUGEN usa) com as
    cores da última (a mais nova), os sprites passam a apontar para ela e as outras saem."""
    s = char.sprites
    first: dict[tuple, int] = {}
    remap, new, latest = {}, [], {}
    for i, p in enumerate(s.palettes):
        k = (p.group, p.item)
        latest[k] = p.colors
        if k in first:
            remap[i] = first[k]
        else:
            first[k] = len(new)
            remap[i] = len(new)
            new.append(p)
    removed = len(s.palettes) - len(new)
    if removed:
        for p in new:
            p.colors = list(latest[(p.group, p.item)])
        for spr in s.sprites:
            if spr.palette_index is not None:
                spr.palette_index = remap.get(spr.palette_index, spr.palette_index)
        s.palettes = new
        char.mark("sprites")
    return {"removed": removed}


def delete_duplicate_palettes(char: Character) -> dict:
    """Remove paletas idênticas a uma anterior (exceto as de personagem 1,n) e reaponta os sprites."""
    s = char.sprites
    keep: dict[tuple, int] = {}
    remap, new = {}, []
    for i, p in enumerate(s.palettes):
        k = p.key()
        if k in keep and p.group != 1:
            remap[i] = keep[k]
        else:
            keep.setdefault(k, len(new))
            remap[i] = len(new)
            new.append(p)
    removed = len(s.palettes) - len(new)
    if removed:
        for spr in s.sprites:
            if spr.palette_index is not None:
                spr.palette_index = remap.get(spr.palette_index, spr.palette_index)
        s.palettes = new
        char.mark("sprites")
    return {"removed": removed}


# ================================================================ sons

def _wav_arrays(wav: bytes):
    with wave.open(io.BytesIO(wav)) as w:
        params = w.getparams()
        raw = w.readframes(w.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[params.sampwidth]
    data = np.frombuffer(raw, dtype=dtype).astype(np.float64)
    if params.sampwidth == 1:
        data -= 128
    return params, data.reshape(-1, params.nchannels)


def _wav_bytes(params, data) -> bytes:
    full = {1: 127, 2: 32767, 4: 2147483647}[params.sampwidth]
    d = np.clip(np.round(data), -full - 1, full)
    d = (d + 128).astype(np.uint8) if params.sampwidth == 1 else d.astype({2: np.int16, 4: np.int32}[params.sampwidth])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(d.tobytes())
    return buf.getvalue()


def edit_sound(char: Character, group: int, number: int, volume: float = 1.0,
               fade_in_ms: int = 0, fade_out_ms: int = 0, resample_hz: int | None = None,
               trim_start_ms: int = 0, trim_end_ms: int = 0) -> dict:
    """Volume (multiplicador), fade in/out, corte de início/fim e reamostragem linear."""
    s = char.sounds.find(group, number)
    if s is None:
        raise ValueError(tr("sound {0},{1} does not exist").format(group, number))
    params, data = _wav_arrays(s.wav)
    rate = params.framerate
    a = int(rate * trim_start_ms / 1000)
    b = len(data) - int(rate * trim_end_ms / 1000)
    data = data[a:max(a, b)] * volume
    for ms, at_end in ((fade_in_ms, False), (fade_out_ms, True)):
        n = min(len(data), int(rate * ms / 1000))
        if n:
            ramp = np.linspace(0, 1, n)[:, None]
            if at_end:
                data[-n:] *= ramp[::-1]
            else:
                data[:n] *= ramp
    if resample_hz and resample_hz != rate:
        t_old = np.arange(len(data)) / rate
        t_new = np.arange(int(len(data) * resample_hz / rate)) / resample_hz
        data = np.stack([np.interp(t_new, t_old, data[:, c]) for c in range(data.shape[1])], axis=1)
        params = params._replace(framerate=resample_hz)
    s.wav = _wav_bytes(params, data)
    char.mark("sounds")
    return {"group": group, "number": number, **s.info()}


SAMPLE_OPS = ("silence", "remove", "keep", "fade_in", "fade_out", "volume", "reverse")


def edit_sound_range(char: Character, group: int, number: int, start: int, end: int, op: str,
                     factor: float = 1.0) -> dict:
    """Edição de um trecho de amostras [start, end):
    silence (preenche com silêncio), remove (corta o trecho), keep (fica só o trecho),
    fade_in, fade_out, volume (multiplica por `factor`) e reverse."""
    if op not in SAMPLE_OPS:
        raise ValueError(tr("op must be one of {0}").format(', '.join(SAMPLE_OPS)))
    s = char.sounds.find(group, number)
    if s is None:
        raise ValueError(tr("sound {0},{1} does not exist").format(group, number))
    params, data = _wav_arrays(s.wav)
    data = data.copy()
    a, b = sorted((max(0, int(start)), min(len(data), int(end))))
    if b <= a:
        raise ValueError(tr("empty selection"))
    n = b - a
    if op == "silence":
        data[a:b] = 0
    elif op == "remove":
        data = np.concatenate([data[:a], data[b:]])
    elif op == "keep":
        data = data[a:b]
    elif op == "fade_in":
        data[a:b] *= np.linspace(0, 1, n)[:, None]
    elif op == "fade_out":
        data[a:b] *= np.linspace(1, 0, n)[:, None]
    elif op == "volume":
        data[a:b] *= factor
    elif op == "reverse":
        data[a:b] = data[a:b][::-1]
    s.wav = _wav_bytes(params, data)
    char.mark("sounds")
    return {"group": group, "number": number, "op": op, "samples": len(data)}


def insert_samples(char: Character, group: int, number: int, at: int, wav: bytes) -> dict:
    """Cola as amostras de outro WAV na posição `at` (convertidas para canais/taxa/bits deste)."""
    s = char.sounds.find(group, number)
    if s is None:
        raise ValueError(tr("sound {0},{1} does not exist").format(group, number))
    params, data = _wav_arrays(s.wav)
    p2, d2 = _wav_arrays(wav)
    scale = {1: 128, 2: 32768, 4: 2 ** 31}
    d2 = d2 / scale[p2.sampwidth] * scale[params.sampwidth]
    if d2.shape[1] != data.shape[1]:
        d2 = np.repeat(d2.mean(axis=1, keepdims=True), data.shape[1], axis=1)
    if p2.framerate != params.framerate and len(d2):
        t_old = np.arange(len(d2)) / p2.framerate
        t_new = np.arange(int(len(d2) * params.framerate / p2.framerate)) / params.framerate
        d2 = np.stack([np.interp(t_new, t_old, d2[:, c]) for c in range(d2.shape[1])], axis=1)
    at = max(0, min(len(data), int(at)))
    s.wav = _wav_bytes(params, np.concatenate([data[:at], d2, data[at:]]))
    char.mark("sounds")
    return {"group": group, "number": number, "inserted": len(d2)}


def samples_wav(char: Character, group: int, number: int, start: int, end: int) -> bytes:
    """WAV só com o trecho [start, end) (para copiar)."""
    s = char.sounds.find(group, number)
    if s is None:
        raise ValueError(tr("sound {0},{1} does not exist").format(group, number))
    params, data = _wav_arrays(s.wav)
    a, b = sorted((max(0, int(start)), min(len(data), int(end))))
    return _wav_bytes(params, data[a:b])


def renumber_sound_group(char: Character, group: int, new_group: int) -> dict:
    taken = {s.number for s in char.sounds.sounds if s.group == new_group}
    mine = [s for s in char.sounds.sounds if s.group == group]
    if new_group != group and any(s.number in taken for s in mine):
        raise ValueError(tr("group {0} already has sounds with the same numbers").format(new_group))
    for s in mine:
        s.group = new_group
    if mine:
        char.mark("sounds")
    return {"renumbered": len(mine)}


def delete_sound_group(char: Character, group: int) -> dict:
    before = len(char.sounds.sounds)
    char.sounds.sounds = [s for s in char.sounds.sounds if s.group != group]
    n = before - len(char.sounds.sounds)
    if n:
        char.mark("sounds")
    return {"deleted": n}


def audio_to_wav(path: str, ffmpeg: str | None = None) -> bytes:
    """Converte OGG/MP3/FLAC (ou qualquer áudio que o ffmpeg leia) para WAV PCM 16 bits."""
    import shutil
    import subprocess
    exe = ffmpeg or shutil.which("ffmpeg")
    if not exe:
        raise FileNotFoundError(tr("ffmpeg not found: set its path in Tools › Options"))
    r = subprocess.run([exe, "-v", "error", "-i", str(path), "-acodec", "pcm_s16le", "-f", "wav", "-"],
                       capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        raise ValueError(tr("ffmpeg failed: {0}").format(r.stderr.decode(errors='replace')[:300]))
    # o ffmpeg escreve o tamanho do cabeçalho como 'desconhecido' na saída por pipe; regrava certo
    data = r.stdout
    import struct
    if data[:4] == b"RIFF":
        i = data.find(b"data")
        if i > 0:
            fmt_i = data.find(b"fmt ")
            ch, rate = struct.unpack("<HI", data[fmt_i + 10:fmt_i + 16])
            bits = struct.unpack("<H", data[fmt_i + 22:fmt_i + 24])[0]
            pcm = data[i + 8:]
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(bits // 8)
                w.setframerate(rate)
                w.writeframes(pcm)
            return buf.getvalue()
    return data


def duplicate_sound(char: Character, group: int, number: int, new_group: int, new_number: int) -> dict:
    s = char.sounds.find(group, number)
    if s is None:
        raise ValueError(tr("sound {0},{1} does not exist").format(group, number))
    sndfmt.set_sound(char.sounds, new_group, new_number, s.wav)
    char.mark("sounds")
    return {"group": new_group, "number": new_number}


def remove_sound(char: Character, group: int, number: int) -> bool:
    before = len(char.sounds.sounds)
    char.sounds.sounds = [s for s in char.sounds.sounds if (s.group, s.number) != (group, number)]
    changed = len(char.sounds.sounds) != before
    if changed:
        char.mark("sounds")
    return changed


# ================================================================ texto genérico (stage, motif…)

def text_sections(path: str) -> list[dict]:
    from ..formats import mtext
    doc = mtext.load(path)
    return [{"index": i, "name": s.name, "values": s.as_dict()} for i, s in enumerate(doc.sections)]


def text_set_value(path: str, section: str, key: str, value: str, occurrence: int = 0) -> dict:
    """Altera chave em qualquer arquivo de texto do MUGEN (stage, motif, storyboard…).
    occurrence escolhe entre seções de mesmo nome (ex.: vários [BG ...])."""
    from ..formats import mtext
    doc = mtext.load(path)
    secs = doc.find_all(section)
    if not secs:
        raise ValueError(tr("section [{0}] does not exist").format(section))
    secs[occurrence].set(key, value)
    doc.save(path)
    return secs[occurrence].as_dict()


def text_add_section(path: str, name: str, values: dict, after: str | None = None) -> dict:
    from ..formats import mtext
    doc = mtext.load(path)
    idx = None
    if after:
        ref = doc.find(after)
        idx = (next(k for k, x in enumerate(doc.sections) if x is ref) + 1) if ref else None   # identidade
    sec = doc.append_section(name, [f"{k} = {v}" for k, v in values.items()], index=idx)
    doc.save(path)
    return {"name": sec.name, "values": sec.as_dict()}


# ================================================================ formatação de código

def format_cns(text: str) -> str:
    """Padroniza CNS/CMD/AIR: 'chave = valor', uma linha em branco entre seções,
    sem espaços sobrando; comentários e ordem preservados."""
    from ..formats import mtext
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue
        if stripped.startswith("["):
            if out and out[-1] != "":
                out.append("")
            out.append(stripped)
            continue
        body = mtext.strip_comment(stripped)
        comment = stripped[len(body):].strip()
        if "=" in body and not body.lstrip().startswith(";"):
            k, v = body.split("=", 1)
            body = f"{k.strip()} = {v.strip()}"
        out.append(f"{body} {comment}".rstrip() if body else comment)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"
