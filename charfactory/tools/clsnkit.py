"""Ferramentas inteligentes de caixas de colisão (Clsn1 e Clsn2) para o charfactory.

- Geração de Clsn2 anatômica (cabeça, tronco e pernas) baseada na silhueta e densidade de pixels.
- Detecção automática de Clsn1 (ataque) para membros estendidos, golpes e projeções.
- Interpolação geométrica de caixas de colisão entre quadros-chave (in-betweening).
- Aplicação em lote com consistência temporal para animações completas.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from ..project import Character
    from ..formats.air import Action, Frame


def _sprite_mask(char: Character, group: int, item: int):
    """Retorna (mask_2d_bool, axis_x, axis_y) ou None."""
    spr = char.sprites.find(group, item)
    if spr is None:
        return None
    try:
        img = char.sprites.image(spr)
    except Exception:
        return None
    if img is None:
        return None

    if img.mode == "RGBA":
        arr = np.asarray(img)[..., 3] > 0
    else:
        pix = char.sprites.pixels(spr)
        w, h = spr.width, spr.height
        if len(pix) == w * h:
            arr = np.frombuffer(pix, dtype=np.uint8).reshape((h, w)) > 0
        else:
            arr = np.asarray(img.convert("L")) > 0
    return arr, spr.axis_x, spr.axis_y


def anatomical_clsn2(char: Character, group: int, item: int,
                     zones: int = 3, margin: int = 2) -> list[tuple[int, int, int, int]]:
    """Gera caixas Clsn2 segmentadas anatomicamente (cabeça, tronco, pernas/base).
    
    Coordenadas retornadas no padrão MUGEN (x1, y1, x2, y2) relativas ao eixo:
    x1 < x2 e y1 < y2 (y negativo para cima).
    """
    data = _sprite_mask(char, group, item)
    if data is None:
        return []
    mask, ax, ay = data
    ys, xs = np.nonzero(mask)
    if not xs.size:
        return []

    min_x, max_x = int(xs.min()), int(xs.max())
    min_y, max_y = int(ys.min()), int(ys.max())
    height = max_y - min_y

    # Se o sprite for muito pequeno ou achatado (ex.: caído), gera caixa única
    if height < 30 or zones <= 1:
        return [(min_x - ax + margin, min_y - ay + margin,
                 max_x - ax - margin, max_y - ay)]

    # Se a altura for média (agachado), usa 2 zonas (superior e inferior)
    actual_zones = 2 if (height < 50 or zones == 2) else 3

    boxes = []
    if actual_zones == 2:
        splits = [min_y, min_y + int(height * 0.48), max_y + 1]
    else:
        # 3 zonas: Cabeça (0% - 22%), Tronco (22% - 62%), Pernas (62% - 100%)
        splits = [min_y, min_y + int(height * 0.22), min_y + int(height * 0.62), max_y + 1]

    for y0, y1 in zip(splits[:-1], splits[1:]):
        sub = mask[y0:y1, :]
        sub_ys, sub_xs = np.nonzero(sub)
        if not sub_xs.size:
            continue
        col_counts = sub.sum(axis=0)
        valid_cols = np.nonzero(col_counts >= max(1, (y1 - y0) * 0.08))[0]
        if not valid_cols.size:
            valid_cols = sub_xs

        bx0 = int(valid_cols.min()) - ax + margin
        bx1 = int(valid_cols.max()) - ax - margin
        if bx1 - bx0 < 8:
            mid = (bx0 + bx1) // 2
            bx0, bx1 = mid - 4, mid + 4

        by0 = int(y0) - ay + margin
        by1 = int(y1 - 1) - ay
        if y1 >= max_y:
            by1 = max(by1, 0)

        boxes.append((bx0, by0, bx1, by1))

    return boxes


def attack_clsn1(char: Character, group: int, item: int,
                 reach_margin: int = 2) -> list[tuple[int, int, int, int]]:
    """Detecta automaticamente membros estendidos ou armas que se projetam para fora
    da silhueta do corpo e gera caixas de ataque Clsn1.
    """
    data = _sprite_mask(char, group, item)
    if data is None:
        return []
    mask, ax, ay = data
    ys, xs = np.nonzero(mask)
    if not xs.size:
        return []

    # Projeção à frente do eixo X (direção padrão do golpe para a direita)
    front_xs = xs[xs >= ax]
    front_ys = ys[xs >= ax]
    if not front_xs.size:
        return []

    reach = int(front_xs.max())
    dist_front = reach - ax
    if dist_front < 12:
        return []

    cutoff = reach - max(12, int(dist_front * 0.35))
    hit_mask = (front_xs >= cutoff)
    hit_xs = front_xs[hit_mask]
    hit_ys = front_ys[hit_mask]
    if not hit_xs.size:
        return []

    hx0 = int(hit_xs.min()) - ax - reach_margin
    hx1 = int(hit_xs.max()) - ax + reach_margin
    hy0 = int(hit_ys.min()) - ay - reach_margin
    hy1 = int(hit_ys.max()) - ay + reach_margin

    return [(hx0, hy0, hx1, hy1)]


def interpolate_boxes(boxes_a: list[tuple[int, int, int, int]],
                      boxes_b: list[tuple[int, int, int, int]],
                      t: float) -> list[tuple[int, int, int, int]]:
    """Interpola linearmente entre duas listas de caixas de colisão.
    
    `t` varia de 0.0 (totalmente A) a 1.0 (totalmente B).
    """
    if not boxes_a and not boxes_b:
        return []
    if not boxes_a:
        return boxes_b
    if not boxes_b:
        return boxes_a

    if len(boxes_a) == len(boxes_b):
        result = []
        for ba, bb in zip(boxes_a, boxes_b):
            r = tuple(int(round(a + (b - a) * t)) for a, b in zip(ba, bb))
            result.append(r)
        return result

    result = []
    centers_b = [((bb[1] + bb[3]) / 2, bb) for bb in boxes_b]
    for ba in boxes_a:
        cy_a = (ba[1] + ba[3]) / 2
        _, bb = min(centers_b, key=lambda it: abs(it[0] - cy_a))
        r = tuple(int(round(a + (b - a) * t)) for a, b in zip(ba, bb))
        result.append(r)
    return result


def interpolate_action_clsn(action: Action, start_idx: int, end_idx: int,
                            box_type: int = 2) -> int:
    """Interpola as caixas de colisão de uma animação entre `start_idx` e `end_idx`.
    
    `box_type`: 1 para Clsn1 (ataque) ou 2 para Clsn2 (corpo).
    Retorna o número de frames intermediários atualizados.
    """
    n = len(action.frames)
    if not (0 <= start_idx < n and 0 <= end_idx < n):
        return 0
    if abs(end_idx - start_idx) <= 1:
        return 0

    step = 1 if end_idx > start_idx else -1
    total_steps = abs(end_idx - start_idx)

    fa = action.frames[start_idx]
    fb = action.frames[end_idx]

    boxes_a = fa.clsn1 if box_type == 1 else fa.clsn2
    boxes_b = fb.clsn1 if box_type == 1 else fb.clsn2

    updated = 0
    for idx_step, curr_idx in enumerate(range(start_idx + step, end_idx, step), 1):
        t = idx_step / total_steps
        interp = interpolate_boxes(boxes_a, boxes_b, t)
        curr_frame = action.frames[curr_idx]
        if box_type == 1:
            curr_frame.clsn1 = interp
        else:
            curr_frame.clsn2 = interp
        updated += 1

    return updated


def auto_clsn_action(char: Character, action_num: int,
                     anatomical: bool = True,
                     detect_attack: bool = True) -> int:
    """Aplica caixas inteligentes a todos os frames de uma ação no personagem.
    
    Retorna a quantidade de frames processados.
    """
    act = char.anims.get_action(action_num)
    if not act:
        return 0
    count = 0
    for fr in act.frames:
        if anatomical:
            fr.clsn2 = anatomical_clsn2(char, fr.group, fr.item)
        if detect_attack:
            c1 = attack_clsn1(char, fr.group, fr.item)
            if c1:
                fr.clsn1 = c1
        count += 1
    char.mark("anims")
    return count
