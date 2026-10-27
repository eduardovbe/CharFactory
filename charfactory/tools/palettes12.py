"""As 12 paletas de um char (as cores que o jogador escolhe na seleção) de uma vez: a paleta é
separada em faixas (as rampas de cor da pele, do cabelo, da roupa...), cada faixa pode ser
travada ou girada por conta própria e um esquema gera as outras 11 a partir da 1."""
from __future__ import annotations

import colorsys
import random

from ..formats import sff
from ..project import Character
from ..i18n import tr

HUE_NAMES = [(15, tr("red")), (40, tr("orange")), (65, tr("yellow")), (160, tr("green")), (200, tr("cyan")),
             (255, tr("blue")), (290, tr("purple")), (335, tr("pink")), (360, tr("red"))]
OUTLINE, SKIN, BEIGE, DARK = tr("outline/shadow"), tr("skin?"), tr("beige/brown"), tr("black/dark")

SCHEMES = {"claro_escuro": tr("Light and dark: white, black, gray, cream or navy clothes (the main part changes "
                              "its lightness, not only its color)"),
           "temas": tr("Themes: each palette a different look (shadow, gold, ice, fire, neon…)"),
           "classico": tr("Fighting game classics: red, blue, green, black, white… with a contrasting detail"),
           "tons": tr("Tones of one color: each palette one color, the bands in lighter and darker tones of it"),
           "vizinhos": tr("Neighbor colors: each palette one color and its neighbors on the color wheel"),
           "complementar": tr("Complementary: the main band and the detail in opposite colors"),
           "metais": tr("Metals and gems: gold, silver, bronze, jade, ruby, sapphire…"),
           "natureza": tr("Nature: spring, autumn, winter, forest, sunset, deep sea, lava…"),
           "sortear": tr("Surprise: random color harmonies (generate again for others)"),
           "arco": tr("Rainbow: the hue turns 30° on each palette"),
           "desencontrado": tr("Staggered bands: each band turns by a different step"),
           "escuro_claro": tr("Five colors and their dark versions: 2–6 colored, 7–12 darker")}
SCHEME_TITLES = {"claro_escuro": tr("Light and dark"), "mistura": tr("Mix of the chosen styles"),
                 "temas": tr("Themes"), "classico": tr("Classics"), "tons": tr("Tones of one color"),
                 "vizinhos": tr("Neighbor colors"), "complementar": tr("Complementary"),
                 "metais": tr("Metals and gems"), "natureza": tr("Nature"), "sortear": tr("Surprise"),
                 "arco": tr("Rainbow"), "desencontrado": tr("Staggered bands"),
                 "escuro_claro": tr("Colors and dark versions")}


# temas das paletas 2 a 12: (nome, matiz principal, matiz de destaque, saturação ×, brilho ×,
# tinta nos brancos/cinzas). A faixa maior recebe o principal, a segunda o destaque, e assim
# alternando; os cinzas ganham a cor principal (girar o matiz de um cinza não muda nada)
THEMES = [(tr("Shadow"), 0, 275, 0.75, 0.5, 0.12), (tr("Gold"), 45, 28, 1.35, 1.05, 0.35),
          (tr("Ice"), 195, 222, 0.85, 1.18, 0.25), (tr("Toxic"), 100, 285, 1.45, 1.0, 0.3),
          (tr("Royal"), 275, 48, 1.25, 0.95, 0.25), (tr("Fire"), 10, 38, 1.45, 1.05, 0.3),
          (tr("Ocean"), 215, 170, 1.2, 0.95, 0.25), (tr("Ghost"), 200, 200, 0.08, 1.22, 0.0),
          (tr("Pastel"), 330, 175, 0.55, 1.25, 0.2), (tr("Military"), 85, 35, 0.6, 0.8, 0.2),
          (tr("Neon"), 300, 160, 1.6, 1.1, 0.45)]
CLASSICS = [(tr("Red"), 0, 215, 1.75, 0.92, 0.3), (tr("Blue"), 222, 45, 1.75, 0.92, 0.3),
            (tr("Green"), 125, 40, 1.6, 0.9, 0.25), (tr("Yellow"), 52, 225, 1.6, 1.02, 0.3),
            (tr("Black"), 240, 0, 0.35, 0.45, 0.05), (tr("White"), 210, 45, 0.15, 1.3, 0.05),
            (tr("Purple"), 280, 50, 1.6, 0.9, 0.3), (tr("Orange"), 28, 205, 1.6, 0.98, 0.3),
            (tr("Gray"), 0, 0, 0.0, 0.95, 0.0), (tr("Pink"), 330, 185, 1.4, 1.02, 0.3),
            (tr("Teal"), 180, 20, 1.6, 0.9, 0.3)]
METALS = [(tr("Gold"), 45, 35, 1.3, 1.05, 0.35), (tr("Silver"), 210, 210, 0.12, 1.15, 0.05),
          (tr("Bronze"), 28, 20, 0.9, 0.85, 0.3), (tr("Copper"), 18, 8, 1.1, 0.95, 0.3),
          (tr("Rose gold"), 350, 30, 0.7, 1.1, 0.25), (tr("Platinum"), 200, 45, 0.2, 1.2, 0.08),
          (tr("Obsidian"), 270, 260, 0.5, 0.45, 0.1), (tr("Steel"), 215, 200, 0.45, 0.95, 0.15),
          (tr("Jade"), 150, 45, 0.8, 0.95, 0.25), (tr("Ruby"), 350, 45, 1.3, 0.9, 0.3),
          (tr("Sapphire"), 225, 45, 1.3, 0.9, 0.3)]
NATURE = [(tr("Spring"), 110, 330, 1.0, 1.1, 0.2), (tr("Summer"), 50, 195, 1.25, 1.1, 0.25),
          (tr("Autumn"), 25, 45, 1.1, 0.9, 0.3), (tr("Winter"), 205, 230, 0.55, 1.2, 0.2),
          (tr("Forest"), 130, 30, 0.9, 0.8, 0.2), (tr("Desert"), 38, 15, 0.8, 1.05, 0.3),
          (tr("Sunset"), 15, 285, 1.3, 1.0, 0.3), (tr("Deep sea"), 220, 175, 1.1, 0.7, 0.2),
          (tr("Lava"), 8, 45, 1.5, 0.95, 0.35), (tr("Aurora"), 160, 290, 1.3, 1.05, 0.3),
          (tr("Night"), 245, 55, 0.8, 0.6, 0.15)]
# claro e escuro: (nome, matiz da parte principal, matiz do destaque, saturação × da principal,
# faixa de luz da principal, quantas faixas grandes viram a principal)
LIGHT_DARK = [(tr("White"), 40, 45, 0.08, (0.4, 0.96), 1), (tr("All white"), 40, 0, 0.06, (0.4, 0.96), 2),
              (tr("Black"), 230, 0, 0.35, (0.06, 0.35), 1), (tr("All black"), 230, 45, 0.3, (0.06, 0.35), 2),
              (tr("Gray"), 215, 200, 0.08, (0.2, 0.66), 1), (tr("Cream"), 38, 15, 0.45, (0.38, 0.92), 1),
              (tr("Charcoal"), 210, 25, 0.15, (0.14, 0.45), 1), (tr("Navy"), 225, 45, 0.7, (0.1, 0.42), 1),
              (tr("Snow"), 205, 215, 0.12, (0.45, 0.98), 1), (tr("Ivory"), 45, 280, 0.25, (0.4, 0.94), 1),
              (tr("Dark red"), 355, 45, 0.8, (0.1, 0.4), 1)]
DEFAULT_STYLES = ("classico", "claro_escuro", "metais", "natureza", "sortear")
STRENGTHS = {tr("Soft"): 0.6, tr("Medium"): 1.0, tr("Vivid"): 1.4}
THEME_LISTS = {"temas": THEMES, "classico": CLASSICS, "metais": METALS, "natureza": NATURE}
# brilho por posição da faixa: alternar claro e escuro separa as partes da roupa na mesma cor
_STEPS = (1.0, 0.8, 1.14, 0.9, 1.06)
HARMONIES = {tr("complementary"): (0, 180), tr("triad"): (0, 120, 240), tr("analogous"): (0, 30, -30),
             tr("split complementary"): (0, 150, 210), tr("square"): (0, 90, 180, 270)}
_NEUTRAL = (tr("white"), tr("gray"))


def _turn(target: float, band: dict) -> float:
    """Giro (graus, -180..180) que leva o matiz médio da faixa ao alvo."""
    d = (target - band["hue"] * 360) % 360
    return d - 360 if d > 180 else d


def _hls(rgb) -> tuple[float, float, float]:
    r, g, b = rgb
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)


def used_indices(char: Character, slot: int = 1, limit: int = 5000) -> set[int]:
    """Índices que os sprites da paleta usam. Lê todos (até `limit`): os 120 primeiros de antes
    deixavam cores de golpes e efeitos de fora das faixas (45 no Jotaro), e elas não mudavam
    em nenhuma paleta gerada; decodificar centenas de sprites leva centésimos de segundo."""
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


def index_counts(char: Character, slot: int = 1, limit: int = 400) -> dict[int, int]:
    """Quantos pixels de cada índice os sprites da paleta usam (até `limit` sprites): diz qual
    parte aparece mais no personagem, que é a que mais muda a cara de uma paleta."""
    import numpy as np
    s = char.sprites
    pi = s.find_palette(1, slot)
    counts = np.zeros(256, dtype=np.int64)
    n = 0
    for spr in s.sprites:
        if spr.palette_index == pi and not spr.is_truecolor and spr.linked_to is None:
            counts += np.bincount(np.frombuffer(s.pixels(spr), dtype=np.uint8), minlength=256)[:256]
            n += 1
            if n >= limit:
                break
    return {i: int(v) for i, v in enumerate(counts) if i and v}


def refine_dark(char: Character, rngs: list[dict], colors: list, counts: dict[int, int] | None = None,
                slot: int = 1, limit: int = 200) -> list[dict]:
    """A roupa preta usa cores de outras faixas: o preto do contorno como sombra e um cinza escuro
    como brilho (o casaco do Jotaro, que divide o cinza com a camisa). Um índice do contorno ou de
    um cinza escuro cujos vizinhos nos sprites são quase só os tons da faixa preto/escuro passa
    para ela; assim a roupa clareada (branca, cinza) muda inteira e não fica listrada."""
    import numpy as np
    dark = next((r for r in rngs if r["name"] == DARK), None)
    if dark is None:
        return rngs
    owner = {i: r for r in rngs for i in r["indices"]}
    cands = {i for r in rngs if r is not dark and (r["name"] == OUTLINE or (_neutral(r) and r["lmax"] < 0.4)
                                                 or (r["name"] in _NEUTRAL))
             for i in r["indices"] if _hls(colors[i])[1] < 0.35}
    s = char.sprites
    pi = s.find_palette(1, slot)
    sprites = [spr for spr in s.sprites if spr.palette_index == pi and not spr.is_truecolor
               and spr.linked_to is None][:limit]
    arrays = [np.frombuffer(s.pixels(spr), dtype=np.uint8).reshape(spr.height, spr.width) for spr in sprites]
    for _round in range(2):                     # o índice que entrou muda a vizinhança dos outros
        is_dark = np.zeros(256, dtype=bool)
        is_dark[dark["indices"]] = True
        is_cand = np.zeros(256, dtype=bool)
        is_cand[sorted(cands)] = True
        near_dark = np.zeros(256, dtype=np.int64)
        near_other = np.zeros(256, dtype=np.int64)
        for a in arrays:
            for x, y in ((a[:, :-1], a[:, 1:]), (a[:, 1:], a[:, :-1]), (a[:-1], a[1:]), (a[1:], a[:-1])):
                m = is_cand[x]
                xs, nb = x[m], y[m]
                np.add.at(near_dark, xs[is_dark[nb]], 1)
                np.add.at(near_other, xs[(~is_dark[nb]) & (nb != 0) & (nb != xs)], 1)
        moved = [i for i in sorted(cands) if near_dark[i] >= 20 and near_dark[i] >= 2 * near_other[i]]
        if not moved:
            break
        for i in moved:
            src = owner[i]
            src["indices"] = [k for k in src["indices"] if k != i]
            if counts:
                src["pixels"] = src.get("pixels", 0) - counts.get(i, 0)
                dark["pixels"] = dark.get("pixels", 0) + counts.get(i, 0)
            owner[i] = dark
            cands.discard(i)
        dark["indices"] = sorted(dark["indices"] + moved)
    ls = [_hls(colors[i])[1] for i in dark["indices"]]
    dark["lmin"], dark["lmax"] = min(ls), max(ls)
    return [r for r in rngs if r["indices"]]


def name_of(hue: float, light: float, sat: float) -> str:
    # só o preto quase puro é contorno: as outras cores muito escuras costumam ser roupa preta
    # (o casaco do Jotaro), que deve poder virar branco ou cinza
    if light < 0.025:
        return OUTLINE
    if light < 0.12:
        return DARK
    if sat < 0.14:
        return tr("white") if light > 0.8 else tr("gray")
    deg = hue * 360
    if 8 <= deg <= 45 and 0.18 <= sat <= 0.8 and 0.35 <= light <= 0.88:
        return SKIN
    return next(n for top, n in HUE_NAMES if deg <= top)


def ranges(colors: list, used: set[int] | None = None, counts: dict[int, int] | None = None) -> list[dict]:
    """Faixas de cor: os índices usados agrupados pela família da cor (contorno, pele, cada
    matiz, branco, cinza). Vale para paleta em rampas e para paleta embaralhada."""
    import math
    groups: dict[str, list[int]] = {}
    for i in range(1, len(colors)):
        if used is not None and i not in used:
            continue
        h, l, sat = _hls(colors[i])
        groups.setdefault(name_of(h, l, sat), []).append(i)
    # roupa bege e pele caem juntas em "pele?" (o quimono do KFM): duas rampas separadas na paleta
    # (de 3 cores ou mais) viram duas faixas, e só a mais saturada continua sendo a pele
    if SKIN in groups:
        runs: list[list[int]] = []
        for i in groups[SKIN]:
            if runs and i - runs[-1][-1] <= 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        runs = [r for r in runs if len(r) >= 3]
        if len(runs) >= 2:
            mean_sat = lambda r: sum(_hls(colors[i])[2] for i in r) / len(r)
            skin = max(runs, key=mean_sat)
            groups[BEIGE] = [i for i in groups[SKIN] if i not in skin]
            groups[SKIN] = skin
    res = []
    for name, run in groups.items():
        hs = [_hls(colors[i]) for i in run]
        x = sum(math.cos(2 * math.pi * h[0]) * max(h[2], 0.01) for h in hs)
        y = sum(math.sin(2 * math.pi * h[0]) * max(h[2], 0.01) for h in hs)
        res.append({"indices": run, "hue": (math.atan2(y, x) / (2 * math.pi)) % 1,
                    "light": sum(x[1] for x in hs) / len(hs), "sat": sum(x[2] for x in hs) / len(hs),
                    "lmin": min(x[1] for x in hs), "lmax": max(x[1] for x in hs),
                    "pixels": sum(counts.get(i, 0) for i in run) if counts else len(run),
                    "name": name,
                    # contorno e pele começam travados: quase sempre ficam iguais em todas
                    "locked": name in (OUTLINE, SKIN)})
    res.sort(key=lambda r: r["indices"][0])
    return res


def adjust(colors: list, rngs: list[dict], adj: dict[int, tuple]) -> list:
    """Cores novas: cada faixa j com (giro de matiz em graus, saturação ×, brilho ×[, saturação
    somada[, faixa de luz]]). A saturação somada tinge brancos e cinzas, que só girando o matiz não
    mudam. A faixa de luz (mín, máx) leva o sombreado da faixa para outra altura: um casaco azul-
    escuro vira branco ou preto mantendo as sombras na mesma ordem."""
    new = [tuple(c) for c in colors]
    for j, (dh, sm, bm, *rest) in adj.items():
        sa = rest[0] if rest else 0.0
        lm = rest[1] if len(rest) > 1 else None
        band = rngs[j]
        lo, hi = band.get("lmin"), band.get("lmax")
        if lm and (lo is None or hi is None):
            ls = [_hls(colors[i])[1] for i in band["indices"]]
            lo, hi = min(ls), max(ls)
        for i in band["indices"]:
            h, l, s = _hls(colors[i])
            top = 1.0
            if s < 0.02 and sa:                  # cinza puro: o matiz dele é 0; o giro já leva ao alvo
                h, top = 0.0, 0.88               # e o branco puro (luz 100%) não pega cor nenhuma
            if lm:
                f = (l - lo) / (hi - lo) if hi > lo else 0.5
                light = lm[0] + f * (lm[1] - lm[0])
            else:
                light = min(top, l * bm)
            r, g, b = colorsys.hls_to_rgb((h + dh / 360) % 1, max(0.0, min(1.0, light)),
                                          max(0.0, min(1.0, s * sm + sa)))
            new[i] = (round(r * 255), round(g * 255), round(b * 255))
    return new


def _neutral(band: dict) -> bool:
    return band["name"] in _NEUTRAL or band["sat"] < 0.14


def scheme(name: str, rngs: list[dict], seed: int | None = None,
           labels: dict | None = None, strength: float = 1.0) -> dict[int, dict[int, tuple]]:
    """Ajustes das paletas 2 a 12 (a 1 é a original) para as faixas não travadas. `labels`, se
    dado, recebe o nome de cada paleta (o tema ou a harmonia sorteada). `seed` fixa o sorteio.
    `strength` é a intensidade das cores (0.6 suave, 1 normal, 1.4 vibrante)."""
    free = [j for j, r in enumerate(rngs) if not r["locked"]]
    # a parte que mais aparece primeiro (pixels nos sprites; sem contagem, o número de cores)
    by_size = sorted(free, key=lambda j: -rngs[j].get("pixels", len(rngs[j]["indices"])))
    rnd = random.Random(seed)
    out = {}
    for k in range(1, 12):
        slot = k + 1
        if name == "claro_escuro":
            title, main, accent, msat, lm, n_main = LIGHT_DARK[k - 1]
            out[slot] = {}
            for rank, j in enumerate(by_size):
                if rank < n_main:                # a parte principal muda de claro/escuro
                    out[slot][j] = (_turn(main, rngs[j]), msat, 1.0, 0.04, lm)
                else:                            # os detalhes ganham a cor de destaque
                    target = accent + (rank % 2) * 18
                    neutral = _neutral(rngs[j])
                    out[slot][j] = (_turn(target, rngs[j]), 1.25, 1.0, 0.3 if neutral else 0.0)
            if labels is not None:
                labels[slot] = title
        elif name in THEME_LISTS:
            title, main, accent, sm, bm, tint = THEME_LISTS[name][k - 1]
            out[slot] = {}
            for rank, j in enumerate(by_size):
                # as faixas menores variam pouco em volta do principal/destaque (±12°): um
                # passo crescente levava a 5ª faixa do Sombrio para o amarelo
                target = (main if rank % 2 == 0 else accent) + ((rank // 2) % 3 - 1) * 12 * (rank > 1)
                neutral = _neutral(rngs[j])
                out[slot][j] = (_turn(main if neutral else target, rngs[j]), sm, bm, tint if neutral else 0.0)
            if labels is not None:
                labels[slot] = title
        elif name in ("tons", "vizinhos", "complementar"):
            # uma cor por paleta, espalhadas pelo círculo; as faixas se separam pelo brilho
            # (tons), pelos vizinhos no círculo (vizinhos) ou pela cor oposta (complementar)
            base = (k - 1) * 360 / 11 + 8
            out[slot] = {}
            for rank, j in enumerate(by_size):
                if name == "tons":
                    target, bm, sm = base + (rank % 2) * 10, _STEPS[rank % len(_STEPS)], 1.1
                elif name == "vizinhos":
                    target, bm, sm = base + (0, 32, -32, 64, -64)[rank % 5], (1.0, 0.92)[rank % 2], 1.15
                else:
                    target = base + (0 if rank % 2 == 0 else 180) + (rank // 2) * 14
                    bm, sm = (1.0, 0.82)[rank % 2], 1.2
                neutral = _neutral(rngs[j])
                out[slot][j] = (_turn(base if neutral else target, rngs[j]), sm, bm, 0.25 if neutral else 0.0)
            if labels is not None:
                labels[slot] = _hue_name(base)
        elif name == "sortear":
            title, offsets = rnd.choice(list(HARMONIES.items()))
            base = rnd.uniform(0, 360)
            sm, bm = rnd.uniform(0.85, 1.5), rnd.uniform(0.75, 1.15)
            tint = rnd.choice((0.0, 0.2, 0.35))
            out[slot] = {}
            for rank, j in enumerate(by_size):
                target = base + offsets[rank % len(offsets)] + rnd.uniform(-12, 12)
                neutral = rngs[j]["name"] in _NEUTRAL or rngs[j]["sat"] < 0.14
                out[slot][j] = (_turn(target, rngs[j]), sm, bm, tint if neutral else 0.0)
            if labels is not None:
                labels[slot] = title
        elif name == "arco":
            out[slot] = {j: (30 * k, 1.0, 1.0) for j in free}
        elif name == "desencontrado":
            out[slot] = {j: ((30 * k * (1 + n % 3) + 47 * n) % 360, 1.0, 1.0) for n, j in enumerate(free)}
        else:                                   # escuro_claro
            base = (k - 1) % 6                  # 2..6 coloridas; 7 = original escura; 8..12 = 2..6 escuras
            dark = k >= 6
            out[slot] = {j: (60 * base, 1.1 if dark else 1.0, 0.62 if dark else 1.0) for j in free}
    # branco e cinza tingidos de leve ficavam quase iguais em todas (o quimono branco do KFM):
    # mais tinta e um branco um pouco mais escuro dão a roupa colorida dos jogos de luta. Os
    # temas pálidos de propósito (Fantasma, Prata, Branco) têm tinta quase zero e ficam como estão
    for adj in out.values():
        for j, a in adj.items():
            if len(a) == 4 and a[3] > 0.15 and _neutral(rngs[j]):
                adj[j] = (a[0], a[1], a[2] * 0.82, min(0.6, a[3] * 1.7))
    if strength != 1.0:
        # intensidade: afasta (ou aproxima) a saturação e a tinta do neutro; o claro/escuro fica
        for adj in out.values():
            for j, a in adj.items():
                sm = max(0.0, 1 + (a[1] - 1) * strength) if a[1] >= 1 else a[1] ** strength
                tint = min(0.7, a[3] * strength) if len(a) > 3 else 0.0
                adj[j] = (a[0], sm, a[2], tint, *a[4:])
    return out


def _hue_name(deg: float) -> str:
    return next(n for top, n in HUE_NAMES if deg % 360 <= top)


def candidate_sets(rngs: list[dict], seeds: tuple = (1, 2, 3), styles=None,
                   strength: float = 1.0) -> list[dict]:
    """Um conjunto (paletas 2 a 12) de cada estilo escolhido (todos, sem `styles`) e um do
    Surpresa para cada semente, para escolher vendo a prévia: {key, title, description, adj,
    labels}. Com dois estilos ou mais, o primeiro é a "Mistura": as paletas alternam entre eles."""
    keys = [k for k in SCHEMES if styles is None or k in styles]
    out = []
    for key in keys:
        for seed in (seeds if key == "sortear" else (None,)):
            labels: dict = {}
            adj = scheme(key, rngs, seed=seed, labels=labels, strength=strength)
            title = SCHEME_TITLES[key] + (f" {seeds.index(seed) + 1}" if key == "sortear" else "")
            out.append({"key": key, "seed": seed, "title": title, "description": SCHEMES[key], "adj": adj,
                        "labels": labels})
    if styles is not None and len(keys) > 1:
        # mistura: a paleta n vem do estilo (n - 2) % k, pegando as primeiras de cada um
        firsts = [next(c for c in out if c["key"] == k) for k in keys]
        adj, labels = {}, {}
        for n in range(2, 13):
            src = firsts[(n - 2) % len(firsts)]
            pick = 2 + (n - 2) // len(firsts)
            adj[n] = src["adj"][pick]
            labels[n] = src["labels"].get(pick) or src["title"]
        out.insert(0, {"key": "mistura", "seed": None, "title": SCHEME_TITLES["mistura"],
                       "description": ", ".join(SCHEME_TITLES[k] for k in keys), "adj": adj, "labels": labels})
    return out


def sprite_users(char: Character | None, slot: int) -> int:
    """Quantos sprites usam a paleta 1,slot como a própria paleta (efeitos, retratos…)."""
    if char is None:
        return 0
    i = char.sprites.find_palette(1, slot)
    return 0 if i < 0 else sum(1 for spr in char.sprites.sprites if spr.palette_index == i)


def relocate(char: Character, slots) -> dict[int, int]:
    """Tira do caminho as paletas 1,n de `slots` que já existem: cada uma ganha o primeiro rótulo
    1,m livre a partir de 13. A posição na lista não muda, e é por ela que os sprites apontam
    para a paleta, então efeitos e retratos que usavam a 1,n continuam com as mesmas cores."""
    s = char.sprites
    taken = {p.item for p in s.palettes if p.group == 1}
    moved, m = {}, 13
    for n in sorted(slots):
        i = s.find_palette(1, n)
        if i < 0:
            continue
        while m in taken:
            m += 1
        s.palettes[i].item = m
        taken.add(m)
        moved[n] = m
    if moved:
        char.mark("sprites")
    return moved


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


# ------------------------------------------------------------------ paletas no .def

BUTTONS = ("x", "y", "z", "a", "b", "c")
DEFAULTS_MAX = 4                          # o MUGEN lê até 4 em pal.defaults


def _ints(v: str | None) -> list[int]:
    out = []
    for part in (v or "").split(";")[0].split(","):
        try:
            out.append(int(part.strip()))
        except ValueError:
            pass
    return out


def def_mapping(char: Character) -> dict:
    """Como o .def deixa escolher as paletas: botões x..c (Start + botão = +6) e pal.defaults
    (o que o motor usa quando escolhe sozinho, e para não repetir a cor num espelho).
    {"keymap", "defaults", "existing", "unreachable", "problems"}."""
    d = char.definition
    keymap = {}
    for i, b in enumerate(BUTTONS):
        v = _ints(d.get("palette keymap", b))
        keymap[b] = v[0] if v else i + 1      # sem a linha, vale o padrão do motor (x=1 … c=6)
    defaults = _ints(d.get("info", "pal.defaults"))
    existing = sorted(p.item for p in char.sprites.palettes if p.group == 1 and 1 <= p.item <= 12)
    reach = {v for v in keymap.values()} | {v + 6 for v in keymap.values()}
    unreachable = [n for n in existing if n not in reach]
    problems = []
    if unreachable:
        problems.append(tr("the buttons in [Palette Keymap] never reach palette(s) {0}").format(
            ", ".join(map(str, unreachable))))
    if len(existing) > 1 and len({n for n in defaults if n in existing}) < min(len(existing), 2):
        problems.append(tr("pal.defaults does not list other palettes: the engine falls back to "
                           "palette 1 when it picks (CPU, random, mirror match)"))
    return {"keymap": keymap, "defaults": defaults, "existing": existing,
            "unreachable": unreachable, "problems": problems}


def fix_mapping(char: Character) -> list[str]:
    """Acerta o .def para as paletas que o SFF tem: botões x..c nas paletas 1 a 6 (Start dá
    7 a 12) se algum ficava de fora, e pal.defaults com as primeiras paletas que existem.
    Devolve o que mudou (vazio se já estava certo)."""
    m = def_mapping(char)
    changes = []
    if m["unreachable"]:
        sec = char.definition.section("Palette Keymap")
        for i, b in enumerate(BUTTONS):
            sec.set(b, str(i + 1))
        changes.append(tr("[Palette Keymap]: x, y, z, a, b, c = palettes 1 to 6 (Start + button = 7 to 12)"))
    want = m["existing"][:DEFAULTS_MAX]
    if len(m["existing"]) > 1 and [n for n in m["defaults"] if n in m["existing"]][:len(want)] != want:
        char.definition.section("Info").set("pal.defaults", ",".join(map(str, want)))
        changes.append(tr("pal.defaults = {0}").format(",".join(map(str, want))))
    if changes:
        char.mark("def")
    return changes
