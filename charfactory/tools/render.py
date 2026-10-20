"""Renderização de sprites e animações (PNG, folha de frames e GIF animado)."""
from __future__ import annotations

import copy
import functools
import io
import math
import re

from PIL import Image, ImageDraw

from ..formats import air, sff
from ..project import Character
from ..i18n import tr

TICK_MS = 1000 / 60  # o MUGEN roda a 60 ticks por segundo
CLSN1_COLOR = (255, 40, 40)
CLSN2_COLOR = (40, 120, 255)


def palette_for(char: Character, palette: int | None) -> sff.Palette | None:
    """Paleta de personagem nº `palette` (1..12): 1,N do SFF v2 ou o .act de palN."""
    if palette is None:
        return None
    s = char.sprites
    idx = s.find_palette(1, palette)
    if idx >= 0:
        return s.palettes[idx]
    act_path = char.palette_files().get(palette)
    if act_path and act_path.exists():
        from ..formats import act
        return sff.Palette(1, palette, act.load(act_path))
    raise ValueError(tr("palette {0} does not exist in this character").format(palette))


def sprite_image(char: Character, group: int, item: int, palette: int | None = None) -> Image.Image:
    s = char.sprites
    spr = s.find(group, item)
    if spr is None:
        raise ValueError(tr("sprite {0},{1} does not exist").format(group, item))
    pal = palette_for(char, palette)
    use = None
    if pal is not None and spr.palette_index is not None and not spr.is_truecolor:
        cur = s.palettes[spr.palette_index] if 0 <= spr.palette_index < len(s.palettes) else None
        if cur is None or (cur.group, cur.item) == (1, 1) or cur.group == 1:
            use = pal
    return s.image(spr, use).convert("RGBA")


def frame_scale_angle(frame: air.Frame) -> tuple[float, float, float]:
    """Escala X/Y e ângulo (MUGEN 1.1) de um frame; (1, 1, 0) quando ausentes."""
    vals = []
    for i, default in enumerate((1.0, 1.0, 0.0)):
        try:
            vals.append(float(frame.extra[i]) if i < len(frame.extra) and frame.extra[i].strip() else default)
        except ValueError:
            vals.append(default)
    return vals[0], vals[1], vals[2]


def transform_about_axis(img: Image.Image, ax: float, ay: float, xscale: float = 1.0,
                         yscale: float = 1.0, angle: float = 0.0) -> tuple[Image.Image, float, float]:
    """Escala e gira a imagem em torno do eixo (ax, ay); devolve a imagem e a nova posição do eixo.

    O ângulo é em graus, anti-horário, como no MUGEN 1.1. Escala negativa espelha."""
    if xscale < 0:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        ax = img.width - ax
    if yscale < 0:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        ay = img.height - ay
    sx, sy = abs(xscale), abs(yscale)
    if (sx, sy) != (1, 1):
        w, h = max(1, round(img.width * sx)), max(1, round(img.height * sy))
        img = img.resize((w, h), Image.Resampling.NEAREST)
        ax, ay = ax * sx, ay * sy
    if angle % 360:
        vx, vy = ax - img.width / 2, ay - img.height / 2
        img = img.rotate(angle, Image.Resampling.NEAREST, expand=True)
        r = math.radians(angle)
        ax = img.width / 2 + vx * math.cos(r) + vy * math.sin(r)
        ay = img.height / 2 - vx * math.sin(r) + vy * math.cos(r)
    return img, ax, ay


def trans_factors(trans: str) -> tuple[float, float, bool] | None:
    """Mistura do AIR como (fonte, destino, subtrai): A = soma, A1 = soma com o fundo a 50%,
    S = subtração, AS<s>D<d> = fonte·s/256 + destino·d/256. None = desenho normal."""
    t = (trans or "").strip().upper()
    if not t:
        return None
    if t == "S" or t == "SUB":
        return 1.0, 1.0, True
    if t == "A1":
        return 1.0, 0.5, False
    m = re.match(r"AS(\d+)D(\d+)", t)
    if m:
        return min(256, int(m[1])) / 256, min(256, int(m[2])) / 256, False
    if t.startswith("A"):
        return 1.0, 1.0, False
    return None


def blend_layer(dst_rgb, src_rgba, trans: str):
    """Mistura (numpy, float 0..1) uma camada RGBA sobre o fundo RGB com a transparência do AIR.
    Pixels com alfa parcial misturam o resultado com o fundo na proporção do alfa."""
    import numpy as np
    f = trans_factors(trans)
    a = src_rgba[..., 3:4]
    src = src_rgba[..., :3]
    if f is None:
        mixed = src
    else:
        ks, kd, sub = f
        mixed = np.clip(dst_rgb * kd - src * ks, 0, 1) if sub else np.clip(src * ks + dst_rgb * kd, 0, 1)
    return dst_rgb * (1 - a) + mixed * a


@functools.lru_cache(maxsize=32)
def blend_lut(trans: str, alpha: int):
    """Tabela [fonte, fundo] -> canal resultante (uint8 256x256) de blend_layer para pixels com
    alfa 0 ou 255, calculada pela própria blend_layer: dá o mesmo valor, byte a byte, que misturar
    em float, e custa uma consulta por canal em vez de umas dez contas em float32."""
    import numpy as np
    v = np.arange(256, dtype=np.float32) / 255
    dst = np.broadcast_to(v[None, :, None], (256, 256, 3))
    src = np.empty((256, 256, 4), np.float32)
    src[..., :3] = v[:, None, None]
    src[..., 3] = alpha / 255
    out = blend_layer(dst, src, trans)[..., 0]
    return (out * 255 + 0.5).astype(np.uint8)


def paste_blended(canvas: Image.Image, img: Image.Image, pos: tuple[int, int], trans: str) -> None:
    """canvas (RGBA, opaco) recebe img na posição com a transparência do AIR (in place)."""
    import numpy as np
    if trans_factors(trans) is None:
        canvas.alpha_composite(img, pos)
        return
    x, y = pos
    l, t = max(0, x), max(0, y)
    r, b = min(canvas.width, x + img.width), min(canvas.height, y + img.height)
    if r <= l or b <= t:
        return
    region = np.asarray(canvas.crop((l, t, r, b)).convert("RGBA"), dtype=np.float32) / 255
    src = np.asarray(img.crop((l - x, t - y, r - x, b - y)).convert("RGBA"), dtype=np.float32) / 255
    out = blend_layer(region[..., :3], src, trans)
    rgba = np.concatenate([out, region[..., 3:4]], axis=-1)
    canvas.paste(Image.fromarray((rgba * 255 + 0.5).astype("uint8"), "RGBA"), (l, t))


def frame_layer(char: Character, frame: air.Frame, palette: int | None = None, apply_trans: bool = True):
    """(imagem RGBA, x, y) do frame relativo à origem do personagem, com flip, escala,
    ângulo e (apply_trans) o alfa de AS/D aplicados; None se o sprite não existe. Sem
    apply_trans quem desenha mistura com trans_factors/paste_blended (soma, subtração)."""
    if frame.group < 0:
        return None
    spr = char.sprites.find(frame.group, frame.item)
    if spr is None:
        return None
    img = sprite_image(char, frame.group, frame.item, palette)
    ax, ay = float(spr.axis_x), float(spr.axis_y)
    flip = frame.flip.upper()
    if "H" in flip:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        ax = spr.width - spr.axis_x
    if "V" in flip:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        ay = spr.height - spr.axis_y
    img, ax, ay = transform_about_axis(img, ax, ay, *frame_scale_angle(frame))
    m = re.match(r"AS(\d+)", frame.trans.upper())
    if apply_trans and m and int(m[1]) < 256:
        alpha = img.getchannel("A").point(lambda a, k=int(m[1]): a * k // 256)
        img.putalpha(alpha)
    return img, round(frame.x - ax), round(frame.y - ay)


def interpolated_frame(action: air.Action, index: int, tick: int) -> air.Frame:
    """O frame `index` no tick `tick` dentro dele, com a interpolação do MUGEN 1.1 aplicada.

    `Interpolate Offset/Scale/Angle/Blend` antes do frame i+1 faz o frame i ir, ao longo do
    seu tempo, dos próprios valores até os valores do frame seguinte (o último volta ao loopstart)."""
    f = action.frames[index]
    nxt_i = index + 1 if index + 1 < len(action.frames) else action.loopstart
    nxt = action.frames[nxt_i]
    if not nxt.interpolate or f.time <= 0 or nxt_i == index:
        return f
    k = max(0.0, min(1.0, tick / f.time))
    out = copy.copy(f)
    kinds = {t.lower() for t in nxt.interpolate}
    if "offset" in kinds:
        out.x = round(f.x + (nxt.x - f.x) * k)
        out.y = round(f.y + (nxt.y - f.y) * k)
    if kinds & {"scale", "angle"}:
        a, b = frame_scale_angle(f), frame_scale_angle(nxt)
        xs, ys, an = a
        if "scale" in kinds:
            xs, ys = a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
        if "angle" in kinds:
            an = a[2] + (b[2] - a[2]) * k
        out.extra = [f"{xs:g}", f"{ys:g}", f"{an:g}"]
    if "blend" in kinds:
        def src_dst(t: str) -> tuple[int, int]:
            m = re.match(r"AS(\d+)D(\d+)", t.upper())
            return (int(m[1]), int(m[2])) if m else (256, 0) if not t else (256, 256)
        (s0, d0), (s1, d1) = src_dst(f.trans), src_dst(nxt.trans)
        out.trans = f"AS{round(s0 + (s1 - s0) * k)}D{round(d0 + (d1 - d0) * k)}"
    return out


def has_interpolation(action: air.Action) -> bool:
    return any(f.interpolate for f in action.frames)


def _bounds(char: Character, action: air.Action, palette: int | None, clsn: bool):
    xs, ys = [0], [0]
    frames = list(action.frames)
    if has_interpolation(action):
        frames += [interpolated_frame(action, i, f.time * k // 4) for i, f in enumerate(action.frames)
                   if f.time > 0 for k in (1, 2, 3)]
    for f in frames:
        lay = frame_layer(char, f, palette)
        if lay:
            img, x, y = lay
            xs += [x, x + img.width]
            ys += [y, y + img.height]
        if clsn:
            for b in f.clsn1 + f.clsn2:
                xs += [b[0], b[2]]
                ys += [b[1], b[3]]
    return min(xs) - 8, min(ys) - 8, max(xs) + 8, max(ys) + 8


def render_frame(char: Character, action: air.Action, index: int, palette: int | None = None,
                 clsn: bool = True, scale: int = 2, background=(48, 48, 64, 255),
                 bounds=None, tick: int | None = None, axis: bool = True) -> Image.Image:
    """Um frame da ação; com `tick`, aplica a interpolação 1.1 naquele tick do frame.
    `axis` desenha a cruz amarela do eixo."""
    f = action.frames[index] if tick is None else interpolated_frame(action, index, tick)
    x0, y0, x1, y1 = bounds or _bounds(char, action, palette, clsn)
    canvas = Image.new("RGBA", (x1 - x0, y1 - y0), background)
    lay = frame_layer(char, f, palette, apply_trans=False)
    if lay:
        img, x, y = lay
        paste_blended(canvas, img, (x - x0, y - y0), f.trans)
    d = ImageDraw.Draw(canvas)
    if clsn:
        for boxes, color in ((f.clsn2, CLSN2_COLOR), (f.clsn1, CLSN1_COLOR)):
            for b in boxes:
                l, r = sorted((b[0], b[2]))
                t, btm = sorted((b[1], b[3]))
                d.rectangle([l - x0, t - y0, r - x0, btm - y0], outline=color)
    if axis:
        d.line([(-x0 - 4, -y0), (-x0 + 4, -y0)], fill=(255, 255, 0))
        d.line([(-x0, -y0 - 4), (-x0, -y0 + 4)], fill=(255, 255, 0))
    if scale != 1:
        canvas = canvas.resize((canvas.width * scale, canvas.height * scale), Image.Resampling.NEAREST)
    return canvas


def render_strip(char: Character, number: int, palette: int | None = None, clsn: bool = True,
                 scale: int = 2, columns: int = 8) -> Image.Image:
    """Todos os frames de uma ação lado a lado (folha de contato)."""
    act = _action(char, number)
    b = _bounds(char, act, palette, clsn)
    frames = [render_frame(char, act, i, palette, clsn, scale, bounds=b) for i in range(len(act.frames))]
    w, h = frames[0].size
    cols = min(columns, len(frames))
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGBA", (w * cols, h * rows), (20, 20, 28, 255))
    for i, fr in enumerate(frames):
        sheet.paste(fr, ((i % cols) * w, (i // cols) * h))
    return sheet


def render_gif(char: Character, number: int, palette: int | None = None, clsn: bool = False,
               scale: int = 2, loops: int = 2) -> bytes:
    """GIF animado com o tempo real de cada frame (tempo -1 vira 1 s)."""
    act = _action(char, number)
    b = _bounds(char, act, palette, clsn)
    images, durations = [], []
    order = list(range(len(act.frames)))
    order += list(range(act.loopstart, len(act.frames))) * max(loops - 1, 0)
    interp = has_interpolation(act)
    for i in order:
        t = act.frames[i].time
        nxt = act.frames[i + 1] if i + 1 < len(act.frames) else act.frames[act.loopstart]
        if interp and t > 1 and nxt.interpolate:
            step = 2  # 2 ticks por imagem: o GIF não tem resolução para 1 tick (16,7 ms)
            for tk in range(0, t, step):
                images.append(render_frame(char, act, i, palette, clsn, scale, bounds=b, tick=tk).convert("RGB"))
                durations.append(max(20, round(min(step, t - tk) * TICK_MS)))
            continue
        images.append(render_frame(char, act, i, palette, clsn, scale, bounds=b).convert("RGB"))
        durations.append(1000 if t < 0 else max(20, round(t * TICK_MS)))
    buf = io.BytesIO()
    images[0].save(buf, "GIF", save_all=True, append_images=images[1:], duration=durations, loop=0)
    return buf.getvalue()


def _action(char: Character, number: int) -> air.Action:
    act = char.anims.get(number)
    if act is None:
        raise ValueError(tr("action {0} does not exist").format(number))
    if not act.frames:
        raise ValueError(tr("action {0} has no frames").format(number))
    return act


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def apply_palfx(img: Image.Image, add=(0, 0, 0), mul=(256, 256, 256), sinadd=(0, 0, 0, 0),
                color: int = 256, invertall: bool = False, tick: int = 0) -> Image.Image:
    """Aplica a fórmula do PalFX (doc oficial) a uma imagem RGBA, no tick dado.

    cor -> (cinza conforme 'color') -> inverte -> (c + add + sinadd*sin(2πt/p)) * mul / 256
    """
    import math
    rgba = img.convert("RGBA")
    px = rgba.load()
    s = [0.0, 0.0, 0.0]
    if len(sinadd) > 3 and sinadd[3]:
        k = math.sin(2 * math.pi * tick / sinadd[3])
        s = [sinadd[i] * k for i in range(3)]
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = px[x, y]
            if not a:
                continue
            if color != 256:
                gray = (r + g + b) / 3
                r, g, b = (gray + (c - gray) * color / 256 for c in (r, g, b))
            if invertall:
                r, g, b = 255 - r, 255 - g, 255 - b
            out = [max(0, min(255, int((c + add[i] + s[i]) * mul[i] / 256))) for i, c in enumerate((r, g, b))]
            px[x, y] = (*out, a)
    return rgba


# ================================================================ offset viewer

AXIS1_COLOR = (70, 130, 255)   # animação 1: eixo azul
AXIS2_COLOR = (60, 220, 90)    # animação 2: eixo verde


def localcoord(char: Character) -> tuple[int, int]:
    try:
        w, h = (int(float(v)) for v in char.info("localcoord", "320,240").split(",")[:2])
        return (w, h) if w > 0 and h > 0 else (320, 240)
    except ValueError:
        return 320, 240


def offset_layout(resolution: tuple[int, int], dx: int, dy: int) -> tuple[int, int]:
    """Onde fica o eixo da animação 1 na tela de prévia: o par fica centralizado e no chão."""
    w, h = resolution
    return round(w / 2 - dx / 2), round(h * 0.82 - min(dy, 0) / 2)


def render_offset(char: Character, anim1: int, anim2: int, dx: int, dy: int, frame1: int = 0,
                  frame2: int = 0, flip_h: bool = False, flip_v: bool = False,
                  palette: int | None = None, resolution: tuple[int, int] | None = None,
                  scale: int = 1, background=(40, 40, 52, 255), label: bool = True) -> Image.Image:
    """Offset viewer do FFS: duas animações na tela, a 2ª com o eixo a (dx, dy) do eixo da 1ª,
    nas coordenadas locais do personagem. Serve para achar o `pos` de Explod, Helper,
    Projectile e TargetBind. flip_h/flip_v espelham a 2ª animação (ex.: P2 virado para P1)."""
    w, h = resolution or localcoord(char)
    canvas = Image.new("RGBA", (w, h), background)
    d = ImageDraw.Draw(canvas)
    ox, oy = offset_layout((w, h), dx, dy)
    d.line([(0, oy), (w, oy)], fill=(90, 90, 110))
    a1, a2 = _action(char, anim1), _action(char, anim2)
    f1 = a1.frames[max(0, min(frame1, len(a1.frames) - 1))]
    f2 = copy.copy(a2.frames[max(0, min(frame2, len(a2.frames) - 1))])
    if flip_h:
        f2.flip = f2.flip.upper().replace("H", "") if "H" in f2.flip.upper() else f2.flip + "H"
        f2.x = -f2.x
    if flip_v:
        f2.flip = f2.flip.upper().replace("V", "") if "V" in f2.flip.upper() else f2.flip + "V"
        f2.y = -f2.y
    for f, (bx, by) in ((f1, (ox, oy)), (f2, (ox + dx, oy + dy))):
        lay = frame_layer(char, f, palette)
        if lay:
            img, x, y = lay
            layer = Image.new("RGBA", canvas.size)
            layer.paste(img, (bx + x, by + y))
            canvas.alpha_composite(layer)
    d.line([(ox, oy), (ox + dx, oy + dy)], fill=(255, 255, 255, 160))
    for (cx, cy), color in (((ox, oy), AXIS1_COLOR), ((ox + dx, oy + dy), AXIS2_COLOR)):
        d.line([(cx - 6, cy), (cx + 6, cy)], fill=color, width=1)
        d.line([(cx, cy - 6), (cx, cy + 6)], fill=color, width=1)
    if label:       # a interface mostra o pos ao lado; a imagem do CLI leva o texto
        d.text((4, 4), f"pos = {dx}, {dy}", fill=(255, 255, 255))
    if scale != 1:
        canvas = canvas.resize((w * scale, h * scale), Image.Resampling.NEAREST)
    return canvas
