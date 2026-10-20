"""Utilidades de personagem usadas pelo editor e pelo simulador: achar o MUGEN, instalar o
char, sprite em RGBA, quadro de uma animação num tick, tela de composição e importar grupos
de sprites (com as animações) de outro personagem."""
from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from ..formats import sff as sffmod
from ..project import Character
from .. import project
from ..i18n import tr

# ====================================================================== MUGEN


def find_mugen_exe(root: str | Path) -> Path:
    """mugen.exe ou, se não houver, outro .exe do MUGEN na pasta (ex.: MUGEN-ORIGINAL.exe)."""
    root = Path(root)
    if (root / "mugen.exe").exists():
        return root / "mugen.exe"
    exes = [p for p in root.glob("*.exe") if "mugen" in p.name.lower()
            and not any(k in p.name.lower() for k in ("setup", "unins", "stagen", "config"))]
    if not exes:
        raise FileNotFoundError(tr("no MUGEN executable in {0}").format(root))
    return sorted(exes, key=lambda p: len(p.name))[0]


def default_stage(root: str | Path) -> str | None:
    """Um palco que existe em stages/ (o MUGEN fecha sem -s quando não tem o padrão). O
    `test_stage` da configuração ganha (o primeiro em ordem alfabética pode ter um pilar
    na frente da luta, como o Amegakure)."""
    st = Path(root) / "stages"
    try:
        from .. import config
        pref = config.load().get("test_stage")
    except Exception:
        pref = None
    if pref and (st / pref).exists():
        return pref
    if (st / "stage0.def").exists():
        return None
    defs = sorted(st.glob("*.def")) if st.is_dir() else []
    return defs[0].name if defs else None


def install(src_dir: str | Path, mugen_root: str | Path, folder: str | None = None,
            ignore: tuple[str, ...] = ("build", "*.bak", "=", "__pycache__")) -> dict:
    """Copia a pasta do personagem para chars/<folder> (substitui a cópia anterior)."""
    src = Path(src_dir)
    dst = Path(mugen_root) / "chars" / (folder or src.name)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ignore))
    return {"installed": str(dst), "files": sorted(p.name for p in dst.iterdir())}


def sprite_rgba(char: Character, g: int, i: int, palette: int | None = None):
    s = char.sprites
    spr = s.find(g, i)
    if spr is None:
        return None, 0, 0
    if spr.is_truecolor:
        im = s.image(spr)
    else:
        idx = np.frombuffer(s.pixels(spr), np.uint8).reshape(spr.height, spr.width)
        pi = palette if palette is not None and palette >= 0 else spr.palette_index
        pal = s.palettes[pi]
        cols = np.array(list(pal.colors) + [(0, 0, 0)] * (256 - len(pal.colors)), np.uint8)
        im = Image.fromarray(np.dstack([cols[idx], np.where(idx > 0, 255, 0).astype(np.uint8)]),
                            "RGBA")
    return im, spr.axis_x, spr.axis_y


def frame_at(char: Character, anim: int, age: int):
    a = char.anims.get(anim)
    if a is None or not a.frames:
        return None
    total = a.total_time
    if total > 0 and age >= total:
        # no MUGEN a anim repete: do Loopstart, ou do começo sem ele (explod com removetime
        # maior que a anim continua na tela; antes a folha sumia com os selos do super)
        loop = sum(f.time for f in a.frames[a.loopstart:]) if a.loopstart else total
        pre = total - loop
        age = pre + (age - pre) % max(1, loop)
    t = 0
    for f in a.frames:
        if f.time < 0 or age < t + f.time:
            return f
        t += f.time
    return a.frames[-1]


class _Canvas:
    def __init__(self, window, zoom):
        self.x0, self.x1, self.y0, self.y1 = window
        self.sc = zoom
        self.w, self.h = (self.x1 - self.x0) * zoom, (self.y1 - self.y0) * zoom
        self.im = Image.new("RGBA", (self.w, self.h), (30, 44, 38, 255))

    def P(self, x, y):
        return (x - self.x0) * self.sc, (y - self.y0) * self.sc

    def blit(self, im, ax, ay, x, y, sx=1.0, sy=1.0, facing=1, angle=0.0, mode="normal",
             alpha=1.0):
        if im is None:
            return
        w = max(1, round(im.width * abs(sx) * self.sc))
        h = max(1, round(im.height * abs(sy) * self.sc))
        if w * h > 4_000_000:
            return
        im = im.resize((w, h), Image.NEAREST)
        ax, ay = ax * abs(sx) * self.sc, ay * abs(sy) * self.sc
        if facing < 0:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
            ax = w - ax
        if angle:
            R = int(max(w, h) * 2 + 4)
            big = Image.new("RGBA", (R, R))
            big.paste(im, (int(R / 2 - ax), int(R / 2 - ay)))
            im = big.rotate(angle * (1 if facing > 0 else -1), resample=Image.BILINEAR)
            ax = ay = R / 2
        px, py = self.P(x, y)
        px, py = int(px - ax), int(py - ay)
        # só a área do sprite: uma camada do tamanho da tela por sprite levava ~0,5 s por tick
        # quando um helper alargava a janela (e segurava o GIL, travando a interface)
        x0, y0 = max(0, px), max(0, py)
        x1, y1 = min(self.im.width, px + im.width), min(self.im.height, py + im.height)
        if x0 >= x1 or y0 >= y1:
            return
        im = im.crop((x0 - px, y0 - py, x1 - px, y1 - py))
        if mode == "normal":
            self.im.alpha_composite(im, (x0, y0))
            return
        src = np.asarray(im, np.float32)
        box = (x0, y0, x1, y1)
        dst = np.asarray(self.im.crop(box), np.float32)
        rgb = src[..., :3] * (src[..., 3:4] / 255) * alpha
        o = dst.copy()
        o[..., :3] = np.clip(dst[..., :3] - rgb if mode == "sub" else dst[..., :3] + rgb, 0, 255)
        self.im.paste(Image.fromarray(o.astype(np.uint8), "RGBA"), box)


def _trans(frame) -> tuple[str, float]:
    t = (frame.trans or "").upper()
    m = re.match(r"AS(\d+)D", t)
    return ("add" if t.startswith("A") else "sub" if t == "S" else "normal",
            int(m.group(1)) / 256 if m else 1.0)


def _frame_img(char, f, palette=None):
    if f is None or f.group < 0:
        return None, 0, 0
    im, ax, ay = sprite_rgba(char, f.group, f.item, palette)
    if im is not None and "H" in (f.flip or "").upper():
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
        ax = im.width - ax
    return im, ax - f.x, ay - f.y


def import_groups(dst: Character, src_def: str | Path, groups: list[int],
                  offset: int | dict[int, int] = 0, make_anims: bool = True, ticks: int = 2,
                  trans: str = "A", palette_group: int = 10) -> dict:
    """Copia grupos de sprite de outro personagem (SFF v1 ou v2) com as paletas deles
    (gravadas em palette_group, i), resolvendo links. Novo grupo = g + offset (ou
    offset[g]). make_anims cria uma anim por grupo com o mesmo número do grupo novo."""
    src = project.load(src_def)
    rs, ds = src.sprites, dst.sprites
    pal_map: dict[int, int] = {}
    made = {}
    for g in groups:
        ng = offset.get(g, g) if isinstance(offset, dict) else g + offset
        items = sorted(rs.groups().get(g, []))
        for it in items:
            spr = rs.find(g, it)
            new = copy.copy(spr)
            new.group, new.linked_to = ng, None
            if spr.linked_to is not None:
                root = rs.sprites[spr.linked_to]
                new.fmt, new.color_depth, new._data = root.fmt, root.color_depth, root._data
                new.width, new.height = root.width, root.height
                new.palette_index = root.palette_index
            if new.fmt not in (sffmod.FMT_PNG32, sffmod.FMT_PNG24):
                pi = new.palette_index
                if pi not in pal_map:
                    item = sum(1 for q in ds.palettes if q.group == palette_group)
                    ds.palettes.append(sffmod.Palette(palette_group, item, list(rs.palettes[pi].colors)))
                    pal_map[pi] = len(ds.palettes) - 1
                new.palette_index = pal_map[pi]
            idx = ds.index_of(ng, it)
            if idx >= 0:
                ds.sprites[idx] = new
            else:
                ds.sprites.append(new)
        if make_anims and items:
            from ..formats import air
            tail = f", , {trans}" if trans else ""
            txt = f"[Begin Action {ng}]\n" + "".join(f"{ng},{i}, 0,0, {ticks}{tail}\n" for i in items)
            dst.anims.set_action(air.loads(txt).get(ng))
        made[g] = {"new_group": ng, "sprites": len(items)}
    dst.mark("sprites")
    if make_anims:
        dst.mark("anims")
    return made
