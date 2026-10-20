"""As 12 paletas de um char (as cores que o jogador escolhe na seleção) de uma vez: a paleta é
separada em faixas (as rampas de cor da pele, do cabelo, da roupa...), cada faixa pode ser
travada ou girada por conta própria e um esquema gera as outras 11 a partir da 1."""
from __future__ import annotations

import colorsys

from ..formats import sff
from ..project import Character
from ..i18n import tr

HUE_NAMES = [(15, tr("red")), (40, tr("orange")), (65, tr("yellow")), (160, tr("green")), (200, tr("cyan")),
             (255, tr("blue")), (290, tr("purple")), (335, tr("pink")), (360, tr("red"))]
OUTLINE, SKIN = tr("outline/shadow"), tr("skin?")

SCHEMES = {"arco": tr("Rainbow: the hue turns 30° on each palette"),
           "desencontrado": tr("Staggered bands: each band turns by a different step"),
           "escuro_claro": tr("Five colors and their dark versions: 2–6 colored, 7–12 darker")}


def _hls(rgb) -> tuple[float, float, float]:
    r, g, b = rgb
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)


def used_indices(char: Character, slot: int = 1, limit: int = 120) -> set[int]:
    """Índices que os sprites da paleta usam (os primeiros `limit` sprites bastam: as poses
    repetem as mesmas cores, e decodificar centenas de sprites demora)."""
    s = char.sprites
    pi = s.find_palette(1, slot)
    used: set[int] = set()
    n = 0
    for spr in s.sprites:
        if spr.palette_index == pi and not spr.is_truecolor and spr.linked_to is None:
            used |= set(s.pixels(spr))
            n += 1
            if n >= limit:
                break
    used.discard(0)
    if len(used) < 8:            # SFF v1 com paleta por sprite: vale a paleta toda
        used = {i for i, c in enumerate(s.palettes[pi].colors) if i and any(c)} if pi >= 0 else used
    return used


def name_of(hue: float, light: float, sat: float) -> str:
    if light < 0.12:
        return OUTLINE
    if sat < 0.14:
        return tr("white") if light > 0.8 else tr("gray")
    deg = hue * 360
    if 8 <= deg <= 45 and 0.18 <= sat <= 0.8 and 0.35 <= light <= 0.88:
        return SKIN
    return next(n for top, n in HUE_NAMES if deg <= top)


def ranges(colors: list, used: set[int] | None = None) -> list[dict]:
    """Faixas de cor: os índices usados agrupados pela família da cor (contorno, pele, cada
    matiz, branco, cinza). Vale para paleta em rampas e para paleta embaralhada."""
    import math
    groups: dict[str, list[int]] = {}
    for i in range(1, len(colors)):
        if used is not None and i not in used:
            continue
        h, l, sat = _hls(colors[i])
        groups.setdefault(name_of(h, l, sat), []).append(i)
    res = []
    for name, run in groups.items():
        hs = [_hls(colors[i]) for i in run]
        x = sum(math.cos(2 * math.pi * h[0]) * max(h[2], 0.01) for h in hs)
        y = sum(math.sin(2 * math.pi * h[0]) * max(h[2], 0.01) for h in hs)
        res.append({"indices": run, "hue": (math.atan2(y, x) / (2 * math.pi)) % 1,
                    "light": sum(x[1] for x in hs) / len(hs), "sat": sum(x[2] for x in hs) / len(hs),
                    "name": name,
                    # contorno e pele começam travados: quase sempre ficam iguais em todas
                    "locked": name in (OUTLINE, SKIN)})
    res.sort(key=lambda r: r["indices"][0])
    return res


def adjust(colors: list, rngs: list[dict], adj: dict[int, tuple[float, float, float]]) -> list:
    """Cores novas: cada faixa j com (giro de matiz em graus, saturação ×, brilho ×)."""
    new = [tuple(c) for c in colors]
    for j, (dh, sm, bm) in adj.items():
        for i in rngs[j]["indices"]:
            h, l, s = _hls(colors[i])
            r, g, b = colorsys.hls_to_rgb((h + dh / 360) % 1, max(0.0, min(1.0, l * bm)),
                                          max(0.0, min(1.0, s * sm)))
            new[i] = (round(r * 255), round(g * 255), round(b * 255))
    return new


def scheme(name: str, rngs: list[dict]) -> dict[int, dict[int, tuple[float, float, float]]]:
    """Ajustes das paletas 2 a 12 (a 1 é a original) para as faixas não travadas."""
    free = [j for j, r in enumerate(rngs) if not r["locked"]]
    out = {}
    for k in range(1, 12):
        slot = k + 1
        if name == "arco":
            out[slot] = {j: (30 * k, 1.0, 1.0) for j in free}
        elif name == "desencontrado":
            out[slot] = {j: ((30 * k * (1 + n % 3) + 47 * n) % 360, 1.0, 1.0) for n, j in enumerate(free)}
        else:                                   # escuro_claro
            base = (k - 1) % 6                  # 2..6 coloridas; 7 = original escura; 8..12 = 2..6 escuras
            dark = k >= 6
            out[slot] = {j: (60 * base, 1.1 if dark else 1.0, 0.62 if dark else 1.0) for j in free}
    return out


def write(char: Character, palettes: dict[int, list]) -> dict:
    """Grava as paletas 1,n (cria as que faltam)."""
    s = char.sprites
    for slot, cols in palettes.items():
        i = s.find_palette(1, slot)
        if i >= 0:
            s.palettes[i].colors = [tuple(c) for c in cols]
        else:
            s.palettes.append(sff.Palette(1, slot, [tuple(c) for c in cols]))
    char.mark("sprites")
    return {"slots": sorted(palettes)}


def preview_image(char: Character, group: int, item: int, colors: list):
    """O sprite com estas cores, sem gravar nada."""
    s = char.sprites
    spr = s.find(group, item)
    if spr is None:
        return None
    return s.image(spr, sff.Palette(1, 0, [tuple(c) for c in colors])).convert("RGBA")


POSES = [(0, tr("standing")), (11, tr("crouching")), (41, tr("jump")), (200, tr("attack")),
         (5000, tr("hit reaction")), (180, tr("win"))]


def pose_sprites(char: Character, n: int = 6) -> list[tuple[int, int, str]]:
    """Um sprite de cada pose que o char tem (a 1ª imagem da anim; se faltar, a próxima)."""
    out = []
    for anim, label in POSES:
        a = char.anims.get(anim) if char.anims else None
        f = next((f for f in (a.frames if a else []) if f.group >= 0 and char.sprites.find(f.group, f.item)), None)
        if f is not None and (f.group, f.item) not in {(g, i) for g, i, _ in out}:
            out.append((f.group, f.item, label))
    return out[:n]
