"""Recorte de sprite sheets em sprites prontos para importar no MUGEN.

Detecta o fundo, separa cada frame (componentes conectados ou grade), calcula o eixo
(pés + linha de chão da fileira), monta uma paleta única com índice 0 transparente e
grava PNGs indexados + sprites.csv (formato de import_sprites_csv).
"""
from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from ..i18n import tr


@dataclass
class Slice:
    left: int
    top: int
    right: int    # exclusivo
    bottom: int   # exclusivo
    row: int = 0
    axis_x: int = 0   # relativo ao recorte
    axis_y: int = 0
    group: int = 0
    item: int = 0
    file: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return self.right - self.left, self.bottom - self.top

    def info(self) -> dict:
        return {"group": self.group, "item": self.item, "file": self.file,
                "box": [self.left, self.top, self.right, self.bottom], "size": list(self.size),
                "axis": [self.axis_x, self.axis_y], "row": self.row}


# ------------------------------------------------------------------ fundo

def _parse_color(c) -> tuple[int, int, int]:
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(int(v) for v in c[:3])


def foreground_mask(img: Image.Image, bg_color=None, tolerance: int = 8,
                    ignore_colors: list | None = None, alpha_threshold: int = 16) -> tuple[np.ndarray, dict]:
    """Máscara booleana do que é sprite. Devolve também como o fundo foi detectado."""
    rgba = np.asarray(img.convert("RGBA")).astype(np.int16)
    alpha = rgba[..., 3]
    info: dict = {}
    if bg_color is None and (alpha < 255).mean() > 0.02:
        mask = alpha > alpha_threshold
        info["background"] = tr("transparency")
    else:
        if bg_color is None:
            border = np.concatenate([rgba[0, :, :3], rgba[-1, :, :3], rgba[:, 0, :3], rgba[:, -1, :3]])
            bg = Counter(map(tuple, border.tolist())).most_common(1)[0][0]
        else:
            bg = _parse_color(bg_color)
        info["background"] = "#%02x%02x%02x" % tuple(bg)
        diff = np.abs(rgba[..., :3] - np.array(bg)).max(axis=2)
        mask = (diff > tolerance) & (alpha > alpha_threshold)
    for c in ignore_colors or []:
        diff = np.abs(rgba[..., :3] - np.array(_parse_color(c))).max(axis=2)
        mask &= diff > tolerance
    return mask, info


# ------------------------------------------------------------------ detecção

def detect_auto(mask: np.ndarray, merge_distance: int = 4, min_area: int = 12) -> list[Slice]:
    """Um recorte por grupo de pixels conectados (pedaços a até merge_distance px se juntam)."""
    grown = ndimage.binary_dilation(mask, iterations=merge_distance) if merge_distance > 0 else mask
    labels, n = ndimage.label(grown, structure=np.ones((3, 3), bool))
    labels = np.where(mask, labels, 0)
    out = []
    for idx, sl in enumerate(ndimage.find_objects(labels), 1):
        if sl is None:
            continue
        area = int((labels[sl] == idx).sum())
        if area < min_area:
            continue
        ys, xs = sl
        out.append(Slice(xs.start, ys.start, xs.stop, ys.stop, extra={"area": area}))
    return _order_rows(out)


def _order_rows(slices: list[Slice]) -> list[Slice]:
    """Agrupa em fileiras (sobreposição vertical) e ordena da esquerda para a direita."""
    rows: list[list[Slice]] = []
    for s in sorted(slices, key=lambda s: (s.top + s.bottom) / 2):
        for row in rows:
            top = min(r.top for r in row)
            bottom = max(r.bottom for r in row)
            overlap = min(bottom, s.bottom) - max(top, s.top)
            if overlap > 0.5 * min(bottom - top, s.bottom - s.top):
                row.append(s)
                break
        else:
            rows.append([s])
    rows.sort(key=lambda r: min(s.top for s in r))
    ordered = []
    for ri, row in enumerate(rows):
        for s in sorted(row, key=lambda s: s.left):
            s.row = ri
            ordered.append(s)
    return ordered


def detect_grid(mask: np.ndarray, cell_width: int | None = None, cell_height: int | None = None,
                columns: int | None = None, rows: int | None = None, offset_x: int = 0,
                offset_y: int = 0, spacing_x: int = 0, spacing_y: int = 0,
                axis_in_cell: tuple[float, float] | None = None, min_area: int = 1) -> list[Slice]:
    """Células de tamanho fixo; cada célula é aparada, e o eixo vem da posição na célula
    (padrão: centro horizontal, base da célula)."""
    h, w = mask.shape
    if cell_width is None:
        if not columns:
            raise ValueError(tr("set cell_width or columns"))
        cell_width = (w - offset_x - spacing_x * (columns - 1)) // columns
    if cell_height is None:
        if not rows:
            raise ValueError(tr("set cell_height or rows"))
        cell_height = (h - offset_y - spacing_y * (rows - 1)) // rows
    ax_f, ay_f = axis_in_cell or (0.5, 1.0)
    out = []
    r = 0
    y = offset_y
    while y + cell_height <= h:
        x = offset_x
        while x + cell_width <= w:
            cell = mask[y:y + cell_height, x:x + cell_width]
            if cell.sum() >= min_area:
                ys, xs = np.nonzero(cell)
                s = Slice(x + xs.min(), y + ys.min(), x + xs.max() + 1, y + ys.max() + 1, row=r)
                s.axis_x = round(x + cell_width * ax_f) - s.left
                s.axis_y = round(y + cell_height * ay_f) - s.top
                s.extra["grid_axis"] = True
                out.append(s)
            x += cell_width + spacing_x
        y += cell_height + spacing_y
        r += 1
    return out


# ------------------------------------------------------------------ eixo

def compute_axes(slices: list[Slice], mask: np.ndarray, mode: str = "feet", feet_band: int = 6) -> None:
    """mode: 'feet' (centro dos pés + chão da fileira), 'bottom-center', 'center', 'row-feet'."""
    # no MUGEN o eixo Y fica na última linha de pixels que toca o chão (ex.: altura 106, eixo 105)
    baseline = {}
    for s in slices:
        baseline[s.row] = max(baseline.get(s.row, 0), s.bottom - 1)
    for s in slices:
        if s.extra.get("grid_axis"):
            continue
        sub = mask[s.top:s.bottom, s.left:s.right]
        w, h = s.size
        if mode == "center":
            s.axis_x, s.axis_y = w // 2, h // 2
            continue
        if mode == "bottom-center":
            s.axis_x, s.axis_y = w // 2, h - 1
            continue
        band = sub[max(0, h - feet_band):, :]
        cols = np.nonzero(band.any(axis=0))[0]
        s.axis_x = int(round((cols.min() + cols.max()) / 2)) if cols.size else w // 2
        s.axis_y = (baseline[s.row] - s.top) if mode in ("feet", "row-feet") else h - 1


# ------------------------------------------------------------------ paleta

def shared_palette(crops: list[Image.Image], max_colors: int = 256) -> tuple[list[tuple], bool]:
    """Paleta única para todos os recortes: índice 0 reservado ao transparente.
    Devolve (cores, exata) — exata=False quando houve quantização."""
    colors: Counter = Counter()
    for im in crops:
        a = np.asarray(im)
        opaque = a[a[..., 3] > 0][:, :3]
        colors.update(map(tuple, np.unique(opaque, axis=0).tolist()) if opaque.size else [])
    if len(colors) <= max_colors - 1:
        return [(255, 0, 255)] + sorted(colors), True
    # quantiza juntando todos os pixels opacos num mosaico
    allpx = np.concatenate([np.asarray(im)[np.asarray(im)[..., 3] > 0][:, :3] for im in crops])
    tile = Image.fromarray(allpx.reshape(1, -1, 3).astype(np.uint8), "RGB")
    q = tile.quantize(colors=max_colors - 1, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: (max_colors - 1) * 3]
    return [(255, 0, 255)] + [tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)], False


def to_indexed(crop: Image.Image, palette: list[tuple]) -> Image.Image:
    """RGBA -> 'P' com a paleta dada (cor mais próxima; alfa 0 vira índice 0)."""
    a = np.asarray(crop).astype(np.int32)
    rgb, alpha = a[..., :3], a[..., 3]
    pal = np.array(palette[1:], dtype=np.int32)
    flat = rgb.reshape(-1, 3)
    uniq, inv = np.unique(flat, axis=0, return_inverse=True)
    d = ((uniq[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
    idx = (d.argmin(axis=1) + 1)[inv.ravel()].reshape(alpha.shape)
    idx[alpha == 0] = 0
    img = Image.fromarray(idx.astype(np.uint8), "P")
    flatpal = [c for col in palette for c in col]
    img.putpalette(flatpal + [0] * (768 - len(flatpal)))
    return img


# ------------------------------------------------------------------ principal

def slice_sheet(image_path: str | Path, out_dir: str | Path, group: int = 0, start_item: int = 0,
                mode: str = "auto", bg_color=None, tolerance: int = 8,
                ignore_colors: list | None = None, merge_distance: int = 4, min_area: int = 12,
                axis_mode: str = "feet", max_colors: int = 256, group_per_row: bool = False,
                palette: list[tuple] | None = None, **grid) -> dict:
    """Recorta a sheet e grava <out_dir>/<grupo>-<nº>.png + sprites.csv + preview.png."""
    src = Image.open(image_path)
    src.load()
    rgba = src.convert("RGBA")
    mask, info = foreground_mask(src, bg_color, tolerance, ignore_colors)
    if mode == "grid":
        slices = detect_grid(mask, min_area=min_area, **grid)
    else:
        slices = detect_auto(mask, merge_distance, min_area)
    if not slices:
        raise ValueError(tr("no sprite found (adjust bg_color/tolerance/min_area)"))
    compute_axes(slices, mask, axis_mode)

    # recortes RGBA com o fundo zerado
    clean = np.asarray(rgba).copy()
    clean[~mask] = 0
    clean_img = Image.fromarray(clean, "RGBA")
    crops = [clean_img.crop((s.left, s.top, s.right, s.bottom)) for s in slices]
    pal, exact = (palette, False) if palette else shared_palette(crops, max_colors)
    if palette:
        exact = all(tuple(c) in set(map(tuple, palette)) for im in crops
                    for c in np.unique(np.asarray(im)[np.asarray(im)[..., 3] > 0][:, :3], axis=0).tolist())

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    item, row_prev = start_item, None
    g = group
    for s, crop in zip(slices, crops):
        if group_per_row:
            if row_prev is not None and s.row != row_prev:
                g, item = g + 1, start_item
            row_prev = s.row
        s.group, s.item = g, item
        s.file = f"{g}-{item}.png"
        to_indexed(crop, pal).save(out / s.file, transparency=0)
        item += 1
    with open(out / "sprites.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group", "item", "file", "axis_x", "axis_y"])
        for s in slices:
            w.writerow([s.group, s.item, s.file, s.axis_x, s.axis_y])
    preview = render_preview(rgba, slices)
    preview.save(out / "preview.png")
    big = [s.info() for s in slices if s.size[0] * s.size[1] > 0.5 * mask.size]
    return {"count": len(slices), "rows": max(s.row for s in slices) + 1, **info,
            "palette_colors": len(pal), "palette_exact": exact, "dir": str(out),
            "csv": str(out / "sprites.csv"), "preview": str(out / "preview.png"),
            "warnings": ([tr("a cut takes more than half the image: the background may be wrong or there are grid lines (use ignore_colors or mode='grid')")] if big else []),
            "sprites": [s.info() for s in slices]}


def render_preview(rgba: Image.Image, slices: list[Slice], max_side: int = 1400) -> Image.Image:
    """Sheet com caixa numerada e eixo de cada recorte, para conferência visual."""
    scale = min(1.0, max_side / max(rgba.size))
    base = Image.new("RGBA", rgba.size, (40, 40, 52, 255))
    base.alpha_composite(rgba)
    if scale < 1:
        base = base.resize((int(base.width * scale), int(base.height * scale)), Image.Resampling.NEAREST)
    d = ImageDraw.Draw(base)
    for s in slices:
        box = [s.left * scale, s.top * scale, s.right * scale - 1, s.bottom * scale - 1]
        d.rectangle(box, outline=(0, 255, 120))
        ax, ay = (s.left + s.axis_x) * scale, (s.top + s.axis_y) * scale
        d.line([(ax - 4, ay), (ax + 4, ay)], fill=(255, 220, 0))
        d.line([(ax, ay - 4), (ax, ay + 4)], fill=(255, 220, 0))
        d.text((box[0] + 2, box[1] + 1), f"{s.group},{s.item}", fill=(255, 255, 255))
    return base
