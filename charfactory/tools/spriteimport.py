"""Importar imagens como sprites.

Paleta (`mode`):
- "embedded": a paleta que já vem na imagem indexada (PNG/PCX/GIF de até 256 cores), com os
  índices e as cores como estão; com `as_char_palette` ela vira a paleta do personagem (1,1);
- "char": a paleta do personagem (1,1); o sprite muda junto com as 12 paletas do jogador;
- "palette": adapta a imagem a uma paleta que já está no SFF (cor mais próxima);
- "swap": mantém os índices da imagem indexada e usa a paleta escolhida (a "troca" do FF:
  sprite desenhado já nos índices da paleta do personagem);
- "own": uma paleta nova, feita das cores da imagem (uma por imagem ou uma para o lote todo,
  até `colors` cores); imagem indexada que já cabe entra com a paleta dela como está; com
  `reuse`, uma paleta igual que já existe no SFF é usada em vez de criar outra;
- "truecolor": PNG 32 bits sem paleta (SFF 2.01: MUGEN 1.1 e Ikemen), para imagens com mais
  cores do que uma paleta comporta; PalFX e as paletas do jogador não mudam esses sprites.
`reversed` inverte a ordem da paleta de uma imagem indexada (a última cor vira a 0).
`scale` muda o tamanho antes de tudo (imagem HD grande demais para um char); `smooth` suaviza
a redução (só em imagens de cores reais: as indexadas sempre por vizinho mais próximo).

Transparência (`trans`): o que vira o índice 0 (o fundo, que o motor não desenha) —
"auto" (canal alfa se houver, senão o índice 0 de imagem indexada, senão a cor do canto),
"alpha", "topleft", "index0" ou "color" (`trans_color`). Nas paletas novas a cor do fundo fica
no índice 0, onde o MUGEN a espera.

Números: `from_name` lê grupo e índice do nome do arquivo (o par de números do fim: kfm_200-3,
200,3); senão grupo fixo e índices a partir do primeiro, de `step` em `step` (1 ou 10). Eixo:
uma das 9 âncoras da imagem original (`anchor`) ou `axis` exato. `share`: um sprite igual a
outro que já existe vira link dele (o SFF guarda os dados uma vez só).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..i18n import tr
from ..project import Character

MODES = ("embedded", "char", "palette", "swap", "own", "truecolor")
TRANS = ("auto", "alpha", "topleft", "index0", "color")
ANCHORS = {"tl": (0, 0), "t": (0.5, 0), "tr": (1, 0), "l": (0, 0.5), "c": (0.5, 0.5), "r": (1, 0.5),
           "bl": (0, 1), "b": (0.5, 1), "br": (1, 1)}


@dataclass
class ImportOptions:
    mode: str = "char"
    palette: tuple[int, int] | None = None   # "palette"/"swap": (grupo, item) da paleta do SFF
    shared: bool = True                      # "own": uma paleta para todas as imagens
    colors: int = 255                        # "own": cores além da transparente (2 a 255)
    reuse: bool = True                       # "own": usar uma paleta igual que já existe
    palette_number: tuple[int, int] | None = None   # "own": número da paleta nova (None = automático)
    reversed: bool = False
    dither: bool = False
    trans: str = "auto"
    trans_color: tuple[int, int, int] | None = None
    autocrop: bool = True
    from_name: bool = False
    step: int = 1
    anchor: str = "b"                        # uma das ANCHORS, ou "custom" com `axis`
    axis: tuple[int, int] | None = None
    share: bool = True
    scale: float = 1.0                       # tamanho final (1 = como está)
    smooth: bool = False                     # redução suave (cores reais)
    as_char_palette: bool = False            # "embedded": a paleta da imagem vira a 1,1


# ------------------------------------------------------------------ tamanho e análise

BIG_SIDE = 1024           # acima disso a janela sugere reduzir: chars costumam ter 60-250 px


def scale_image(img: Image.Image, opts: ImportOptions) -> Image.Image:
    """A imagem no tamanho pedido (`scale`); indexada sempre por vizinho mais próximo, para os
    índices continuarem os da paleta."""
    k = float(opts.scale or 1.0)
    if abs(k - 1.0) < 1e-6:
        return img
    w, h = max(1, round(img.width * k)), max(1, round(img.height * k))
    smooth = opts.smooth and img.mode != "P" and k < 1
    out = img.resize((w, h), Image.Resampling.LANCZOS if smooth else Image.Resampling.NEAREST)
    if img.mode == "P" and "transparency" in img.info:
        out.info["transparency"] = img.info["transparency"]
    return out


def has_embedded_palette(img: Image.Image) -> bool:
    return img.mode == "P" and bool(img.getpalette())


def image_info(img: Image.Image) -> dict:
    """O que a janela mostra sobre a imagem: tamanho, se é indexada (paleta embutida), quantas
    cores a paleta dela tem e quantas a imagem usa, e se tem transparência."""
    from .ops import unique_colors
    indexed = has_embedded_palette(img)
    info = {"size": img.size, "mode": img.mode, "indexed": indexed, "big": max(img.size) > BIG_SIDE,
            "alpha": img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info}
    if indexed:
        info["palette_size"] = len(img.getpalette()) // 3
        info["used"] = int(np.asarray(img).max()) + 1 if img.width * img.height else 0
    else:
        sample = img if img.width * img.height <= 4_000_000 else img.resize(
            (max(1, img.width // 2), max(1, img.height // 2)), Image.Resampling.NEAREST)
        a = np.asarray(sample.convert("RGBA"))
        info["colors"] = len(unique_colors(a[a[..., 3] >= 128][:, :3])) if a.size else 0
    return info


def recommend(images: list[Image.Image], char: Character) -> tuple[str, str]:
    """(modo, motivo) que a janela marca como recomendado para estas imagens."""
    if not images:
        return "own", ""
    if all(has_embedded_palette(im) for im in images):
        k = char.sprites.find_palette(1, 1)
        if k >= 0:
            mine = [tuple(c[:3]) for c in char.sprites.palettes[k].colors]
            for im in images:
                raw = im.getpalette() or []
                cols = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
                used = int(np.asarray(im).max()) + 1
                if cols[1:used] != mine[1:used]:
                    break
            else:
                return "char", tr("the image already uses the character's palette")
        return "embedded", tr("the image has its own palette: it goes in exactly as it is")
    n = max(image_info(im).get("colors", 0) for im in images)
    k = char.sprites.find_palette(1, 1)
    if k >= 0 and n <= 255:
        # desenhada com as cores do personagem: entra na 1,1 sem mudar nada
        from .ops import unique_colors
        mine = {tuple(c[:3]) for c in char.sprites.palettes[k].colors[1:]}
        for im in images:
            a = np.asarray(im.convert("RGBA"))
            if not {tuple(c) for c in unique_colors(a[a[..., 3] >= 128][:, :3]).tolist()} <= mine:
                break
        else:
            return "char", tr("every color of the image is in the character's palette")
    if n > 4096:
        return "truecolor", tr("{0} colors: a palette of 256 would change them a lot").format(f"{n:,}".replace(",", "."))
    return "own", tr("{0} colors: they fit in a palette of their own").format(n)


def palette_from_image(path) -> list[tuple[int, int, int]]:
    """A paleta embutida de uma imagem indexada (PNG/PCX/GIF/BMP), na ordem dos índices; numa
    imagem de cores reais, até 256 cores dela (a 0 é a do canto, o fundo)."""
    img = Image.open(Path(path))
    img.load()
    if has_embedded_palette(img):
        raw = img.getpalette()
        return [tuple(raw[i:i + 3]) for i in range(0, min(len(raw), 768), 3)]
    mask, bg = background(img, ImportOptions(trans="auto"))
    pix = np.asarray(img.convert("RGB"))[~mask]
    return [bg or (0, 0, 0), *_make_palette(pix, 255)]


# ------------------------------------------------------------------ números e eixo

def parse_name(path) -> tuple[int, int] | None:
    """Grupo e índice no nome do arquivo: os dois últimos números (kfm_200-3.png, 200,3.pcx)."""
    nums = re.findall(r"\d+", Path(path).stem)
    return (int(nums[-2]), int(nums[-1])) if len(nums) >= 2 else None


def targets(paths: list, group: int, first_item: int, opts: ImportOptions) -> list[tuple[int, int]]:
    """Grupo e índice de cada imagem: pelo nome (se pedido e houver) ou em sequência."""
    out, k = [], 0
    for p in paths:
        hit = parse_name(p) if opts.from_name else None
        if hit is None:
            hit = (group, first_item + k * max(1, opts.step))
            k += 1
        out.append(hit)
    return out


def axis_for(w: int, h: int, opts: ImportOptions) -> tuple[int, int]:
    """Eixo na imagem original: a âncora escolhida (centro de baixo por padrão) ou o exato."""
    if opts.anchor == "custom" and opts.axis is not None:
        return tuple(opts.axis)
    fx, fy = ANCHORS.get(opts.anchor, ANCHORS["b"])
    return int(w * fx), int(h * fy)


# ------------------------------------------------------------------ transparência

def _trans_index(img: Image.Image) -> int:
    """Índice transparente de uma imagem indexada (o do PNG/GIF, senão o 0)."""
    t = img.info.get("transparency")
    return t if isinstance(t, int) else 0


def reverse_palette(img: Image.Image) -> Image.Image:
    """Imagem indexada com a paleta de trás para frente (o índice i vira 255 - i)."""
    if img.mode != "P":
        return img
    pal = (img.getpalette() or []) + [0] * 768
    cols = [pal[i:i + 3] for i in range(0, 768, 3)][::-1]
    out = Image.fromarray((255 - np.asarray(img)).astype(np.uint8), "P")
    out.putpalette([v for c in cols for v in c])
    t = img.info.get("transparency")
    if isinstance(t, int):
        out.info["transparency"] = 255 - t
    return out


def background(img: Image.Image, opts: ImportOptions) -> tuple[np.ndarray, tuple[int, int, int] | None]:
    """(máscara dos pixels de fundo, cor do fundo para o índice 0 da paleta nova ou None)."""
    rgba = np.asarray(img.convert("RGBA"))
    rgb, alpha = rgba[..., :3], rgba[..., 3]
    how = opts.trans
    if how == "auto":
        if (alpha < 255).any():
            how = "alpha"
        elif img.mode == "P":
            how = "index0"
        else:
            how = "topleft"
    if how == "alpha":
        return alpha < 128, None
    if how == "index0" and img.mode == "P":
        k = _trans_index(img)
        pal = img.getpalette() or []
        color = tuple(pal[k * 3:k * 3 + 3]) if len(pal) >= k * 3 + 3 else None
        return np.asarray(img) == k, color
    color = opts.trans_color if how == "color" and opts.trans_color else tuple(int(v) for v in rgb[0, 0])
    return (rgb == np.array(color, np.uint8)).all(axis=2) | (alpha < 128), tuple(color)


def count_colors(img: Image.Image, mask: np.ndarray) -> int:
    from .ops import unique_colors
    rgb = np.asarray(img.convert("RGB"))[~mask]
    return len(unique_colors(rgb)) if rgb.size else 0


# ------------------------------------------------------------------ cores

def _quantize_to(rgb: np.ndarray, colors: list, dither: bool) -> np.ndarray:
    """Índices (1 em diante) da cor mais próxima em `colors` (o índice 0 é o fundo e não entra:
    um pixel preto não pode virar buraco). Com `dither`, Floyd–Steinberg do Pillow."""
    usable = [tuple(c[:3]) for c in colors[1:256]] or [(0, 0, 0)]
    if not dither:
        from .ops import _nearest_indices
        return _nearest_indices(rgb, [(0, 0, 0), *usable]).reshape(rgb.shape[:2])
    pal_img = Image.new("P", (1, 1))
    flat = [v for c in usable for v in c]
    pal_img.putpalette(flat + flat[-3:] * (256 - len(usable)))      # completa repetindo a última
    q = Image.fromarray(rgb, "RGB").quantize(palette=pal_img, dither=Image.Dither.FLOYDSTEINBERG)
    return np.minimum(np.asarray(q), len(usable) - 1).astype(np.uint8) + 1


def _make_palette(pixels: np.ndarray, n: int) -> list[tuple[int, int, int]]:
    """Até `n` cores para os pixels (N×3): todas se couberem, senão o Pillow escolhe."""
    from .ops import unique_colors
    uniq = unique_colors(pixels) if pixels.size else np.zeros((1, 3), np.uint8)
    if len(uniq) <= n:
        return [tuple(int(v) for v in c) for c in uniq]
    if len(pixels) > 1_000_000:
        # foto grande: a paleta sai de uma amostra de 1 milhão de pixels (a mediana das cores
        # quase não muda e o corte mediano em milhões de pixels levava dezenas de segundos)
        k = np.linspace(0, len(pixels) - 1, 1_000_000).astype(np.int64)
        pixels = pixels.reshape(-1, 3)[k]
    side = int(np.ceil(np.sqrt(len(pixels))))
    strip = np.zeros((side * side, 3), np.uint8)
    strip[:len(pixels)] = pixels
    strip[len(pixels):] = pixels[0]
    q = Image.fromarray(strip.reshape(side, side, 3), "RGB").quantize(colors=n, method=Image.Quantize.MEDIANCUT,
                                                                        dither=Image.Dither.NONE)
    raw = q.getpalette()[:n * 3]
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]


def _crop(img: Image.Image, mask: np.ndarray | None):
    """Caixa do que não é fundo (None se a imagem é toda fundo)."""
    keep = ~mask if mask is not None else np.ones((img.height, img.width), bool)
    ys, xs = np.nonzero(keep)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _target_palette(char: Character, opts: ImportOptions) -> list:
    key = (1, 1) if opts.mode == "char" else tuple(opts.palette or (1, 1))
    k = char.sprites.find_palette(*key)
    if k < 0:
        raise ValueError(tr("palette {0},{1} does not exist").format(*key))
    return list(char.sprites.palettes[k].colors)


def convert(images: list[Image.Image], char: Character, opts: ImportOptions) -> list[dict]:
    """Converte as imagens como vão entrar: [{"image": P ou RGBA, "colors": paleta ou None,
    "lost": 0..1 (o quanto as cores mudaram), "mask": fundo}]. Não mexe no personagem."""
    if opts.reversed:
        images = [reverse_palette(im) for im in images]
    masks, bgs = zip(*[background(im, opts) for im in images]) if images else ((), ())
    out = []
    if opts.mode == "truecolor":
        for im, m in zip(images, masks):
            rgba = np.array(im.convert("RGBA"))
            rgba[m, 3] = 0
            out.append({"image": Image.fromarray(rgba, "RGBA"), "colors": None, "lost": 0.0, "mask": m})
        return out
    if opts.mode == "swap":                         # os índices como estão, a paleta escolhida
        target = _target_palette(char, opts)
        for im in images:
            if im.mode != "P":
                raise ValueError(tr("Keeping the indices needs an indexed image (PNG, PCX or GIF with "
                                    "up to 256 colors)"))
            res = im.copy()
            flat = [v for c in target for v in c[:3]][:768]
            res.putpalette(flat + [0] * (768 - len(flat)))
            out.append({"image": res, "colors": [tuple(c[:3]) for c in target], "lost": 0.0,
                        "mask": np.asarray(im) == 0})
        return out
    if opts.mode == "embedded":
        for im in images:
            if not has_embedded_palette(im):
                name = Path(getattr(im, "filename", "") or "image").name
                raise ValueError(tr("{0}: this image has no palette of its own (it is not indexed). "
                                    "Use Own palette or True colors").format(name))
        per_image = [None] * len(images)
    elif opts.mode in ("char", "palette"):
        per_image = [_target_palette(char, opts)] * len(images)
    else:
        n = max(2, min(255, int(opts.colors)))
        same_pal = all(im.mode == "P" for im in images) and len({tuple(im.getpalette() or []) for im in images}) == 1
        per_image = []
        if opts.shared and len(images) > 1 and not same_pal:
            pix = np.concatenate([np.asarray(im.convert("RGB"))[~m] for im, m in zip(images, masks)] or
                                 [np.zeros((1, 3), np.uint8)])
            bg = next((b for b in bgs if b is not None), (0, 0, 0))
            shared = [bg, *_make_palette(pix, n)]
            per_image = [shared] * len(images)
        else:
            for im, m, bg in zip(images, masks, bgs):
                if im.mode == "P" and count_colors(im, m) <= n:
                    per_image.append(None)          # indexada que cabe: entra como está (abaixo)
                else:
                    pix = np.asarray(im.convert("RGB"))[~m]
                    per_image.append([bg or (0, 0, 0), *_make_palette(pix, n)])
    for im, m, bg, colors in zip(images, masks, bgs, per_image):
        if colors is None:                          # indexada: a paleta dela, com o fundo no 0
            idx = np.array(im)
            pal = im.getpalette() or []
            cols = [tuple(pal[i:i + 3]) for i in range(0, min(len(pal), 768), 3)]
            t = _trans_index(im) if opts.trans in ("auto", "index0") else None
            if t:                                   # troca o índice do fundo com o 0
                a, b = idx == t, idx == 0
                idx[a], idx[b] = 0, t
                cols[0], cols[t] = cols[t], cols[0]
            idx[m] = 0
            res = Image.fromarray(idx.astype(np.uint8), "P")
            used = int(idx.max()) + 1 if idx.size else 1
            # a embutida entra inteira (as imagens de um lote com a mesma paleta dividem uma só)
            colors = cols if opts.mode == "embedded" else cols[:max(used, 1)]
            lost = 0.0
        else:
            rgb = np.asarray(im.convert("RGB"))
            idx = _quantize_to(rgb, colors, opts.dither)
            idx[m] = 0
            res = Image.fromarray(idx.astype(np.uint8), "P")
            got = np.array([c[:3] for c in colors], np.float32)[idx]
            diff = np.abs(got - rgb.astype(np.float32))[~m]
            lost = float(diff.mean() / 255) if diff.size else 0.0
        flat = [v for c in colors for v in c[:3]][:768]
        res.putpalette(flat + [0] * (768 - len(flat)))
        out.append({"image": res, "colors": [tuple(c[:3]) for c in colors], "lost": lost, "mask": m})
    return out


# ------------------------------------------------------------------ gravar no SFF

def _free_palette(char: Character, group: int, item: int) -> tuple[int, int]:
    """Número para uma paleta nova: (grupo, item) do sprite se livre. Nunca no grupo 1, que é o
    das paletas do personagem (a paleta própria viraria uma das 12 do jogador)."""
    g = group if group != 1 else 0
    s = char.sprites
    while s.find_palette(g, item) >= 0:
        item += 1
    return g, item


def _own_palette(char: Character, colors: list, group: int, item: int, opts: ImportOptions) -> tuple[int, bool]:
    """Índice da paleta própria no SFF: uma igual que já existe (com `reuse`) ou uma nova, no
    número pedido ou no automático. (índice, criou?)"""
    s = char.sprites
    cols = [tuple(c[:3]) for c in colors]
    if opts.reuse:
        for k, p in enumerate(s.palettes):
            if [tuple(c[:3]) for c in p.colors] == cols:
                return k, False
    if opts.palette_number is not None:
        g, i = opts.palette_number
        while s.find_palette(g, i) >= 0:           # não sobrescreve uma paleta que já existe
            i += 1
    else:
        g, i = _free_palette(char, group, item)
    return s.add_palette(cols, g, i), True


def _payload(s, spr) -> tuple:
    """O que torna dois sprites iguais: tamanho, paleta e os pixels (ou o RGBA do truecolor)."""
    data = s.image(spr).tobytes() if spr.is_truecolor else s.pixels(spr)
    return spr.width, spr.height, spr.palette_index if not spr.is_truecolor else None, data


def identical(char: Character, spr) -> tuple[int, int] | None:
    """Outro sprite igual a `spr` já no SFF (grupo, item), ou None."""
    s = char.sprites
    me = _payload(s, spr)
    for other in s.sprites:
        if other is spr or (other.width, other.height) != (spr.width, spr.height) or \
                other.is_truecolor != spr.is_truecolor:
            continue
        try:
            if _payload(s, other) == me:
                return other.group, other.item
        except Exception:  # noqa: BLE001 — sprite que não decodifica: não é igual a nada
            continue
    return None


def _link(char: Character, spr, to: tuple[int, int]) -> None:
    """Faz `spr` usar os dados de `to` (link do SFF: os dados ficam uma vez só no arquivo)."""
    s = char.sprites
    j = s.index_of(*to)
    i = s.index_of(spr.group, spr.item)
    if j < 0 or i < 0 or j >= i:                    # o SFF só liga a um sprite anterior
        return
    target = s.sprites[j]
    spr.linked_to = j
    spr.fmt, spr.color_depth = target.fmt, target.color_depth
    spr._data, spr._image = target._data, target._image


def import_images(char: Character, paths: list, group: int, first_item: int,
                  opts: ImportOptions) -> dict:
    """Importa as imagens nos números de `targets` com o eixo de `axis_for`. Devolve os sprites
    feitos, as paletas criadas, os que viraram link e o quanto as cores mudaram."""
    images = [scale_image(im, opts) for im in open_images(paths)]
    conv = convert(images, char, opts)
    s = char.sprites
    shared_index = None
    made, palettes, linked = [], [], []
    for (g, item), im, c in zip(targets(paths, group, first_item, opts), images, conv):
        ax, ay = axis_for(im.width, im.height, opts)
        res = c["image"]
        if opts.autocrop:
            box = _crop(res, c["mask"])
            if box is not None:
                res = res.crop(box)
                ax, ay = ax - box[0], ay - box[1]
        if opts.mode == "truecolor":
            pi = None
        elif opts.mode in ("char", "palette", "swap"):
            pi = s.find_palette(*((1, 1) if opts.mode == "char" else opts.palette))
        elif opts.mode == "embedded" and opts.as_char_palette:
            k = s.find_palette(1, 1)
            cols = list(c["colors"])
            if k >= 0 and len(s.palettes[k].colors) > len(cols):     # não encolhe a 1,1
                cols += s.palettes[k].colors[len(cols):]
            pi = s.add_palette(cols, 1, 1)
            if [1, 1] not in palettes:
                palettes.append([1, 1])
        elif opts.mode == "embedded":
            pi, created = _own_palette(char, c["colors"], g, item, ImportOptions(reuse=True))
            if created:
                palettes.append([s.palettes[pi].group, s.palettes[pi].item])
        elif opts.shared and len(paths) > 1 and shared_index is not None:
            pi = shared_index
        else:
            pi, created = _own_palette(char, c["colors"], g, item, opts)
            if created:
                palettes.append([s.palettes[pi].group, s.palettes[pi].item])
            if opts.shared and len(paths) > 1:
                shared_index = pi
        spr = s.set_sprite(g, item, res, ax, ay, pi)
        if opts.share:
            twin = identical(char, spr)
            if twin is not None:
                _link(char, spr, twin)
                linked.append([g, item, *twin])
        made.append([g, item])
    char.mark("sprites")
    lost = max((c["lost"] for c in conv), default=0.0)
    return {"sprites": made, "palettes": palettes, "palette": palettes[0] if len(palettes) == 1 else None,
            "linked": linked, "lost": round(lost, 3)}


def open_images(paths: list) -> list[Image.Image]:
    out = []
    for p in paths:
        im = Image.open(Path(p))
        im.load()
        out.append(im)
    return out
