"""Utilidades de personagem usadas pelo editor e pelo simulador: achar o MUGEN, instalar o
char, sprite em RGBA, quadro de uma animação num tick, tela de composição e importar grupos
de sprites (com as animações) de outro personagem."""
from __future__ import annotations

import copy
import math
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


TEST_SUFFIX = "__teste"
_SKIP = ("_backups", "build", "__pycache__")


def sync_copy(src: Path, dst: Path) -> int:
    """Deixa `dst` igual a `src` copiando só o que mudou (tamanho ou data): o SFF de um char
    passa de dezenas de MB e copiar tudo a cada teste demorava."""
    n = 0
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if not f.is_file() or f.suffix.lower() == ".bak" or any(part in _SKIP or part.endswith(TEST_SUFFIX)
                                                                for part in rel.parts):
            continue
        out = dst / rel
        st = f.stat()
        if out.exists():
            o = out.stat()
            if o.st_size == st.st_size and int(o.st_mtime) == int(st.st_mtime):
                continue
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        n += 1
    return n


def test_copy(char: Character, dst: Path | None = None) -> Path:
    """Cópia do char com o que está editado e ainda não foi salvo, para rodar no motor sem
    gravar nada nos arquivos do projeto: a pasta irmã <pasta>__teste (na mesma altura dentro
    de chars/, para os caminhos relativos do .def valerem igual)."""
    dst = Path(dst) if dst else char.dir.parent / (char.dir.name + TEST_SUFFIX)
    sync_copy(char.dir, dst)
    char.save_to(dst, set(char.dirty))
    return dst


def install(src_dir: str | Path, mugen_root: str | Path, folder: str | None = None,
            ignore: tuple[str, ...] = ("build", "*.bak", "=", "__pycache__")) -> dict:
    """Copia a pasta do personagem para chars/<folder> (substitui a cópia anterior)."""
    src = Path(src_dir)
    dst = Path(mugen_root) / "chars" / (folder or src.name)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ignore))
    return {"installed": str(dst), "files": sorted(p.name for p in dst.iterdir())}


# sprite já em RGBA por (dados, paleta): o simulador desenha os mesmos quadros em centenas de
# ticks, e converter cada vez custava ~2 ms por sprite (metade do tempo de um tick)
_RGBA = sffmod.ByteLRU(256 * 1024 * 1024)


def sprite_rgba(char: Character, g: int, i: int, palette: int | None = None):
    """(imagem RGBA, eixo x, eixo y) do sprite. A imagem vem do cache e é compartilhada: quem
    precisar alterar os pixels faz uma cópia antes."""
    s = char.sprites
    spr = s.find(g, i)
    if spr is None:
        return None, 0, 0
    src = spr._image if spr._image is not None else spr._data
    pi = None if spr.is_truecolor else         palette if palette is not None and palette >= 0 else spr.palette_index
    pal = s.palettes[pi] if pi is not None and 0 <= pi < len(s.palettes) else None
    if pi is not None and pal is None:
        pal = sffmod.Palette(1, 1, [(0, 0, 0)])        # índice de paleta inválido: sai preto
    pkey = pal.key() if pal is not None else None
    key = (id(src), spr.width, spr.height, pi)
    hit = _RGBA.get(key, lambda v: v[0] is src and v[1] == pkey)
    if hit is not None:
        return hit[2], spr.axis_x, spr.axis_y
    try:
        if spr.is_truecolor:
            im = s.image(spr)
        else:
            px = s.pixels(spr)
    except sffmod.SFFError:          # sprite estragado: não desenha, como se faltasse (o resto segue)
        return None, 0, 0
    if not spr.is_truecolor:
        idx = np.frombuffer(px, np.uint8).reshape(spr.height, spr.width)
        cols = np.zeros((256, 4), np.uint8)
        c = np.array(pal.colors[:256], np.uint8).reshape(-1, 3)
        cols[:len(c), :3] = c
        cols[1:, 3] = 255
        im = Image.fromarray(cols[idx], "RGBA")
    _RGBA.put(key, (src, pkey, im), im.width * im.height * 4 + 64)
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
             alpha=1.0, dst=1.0):
        """mode add/sub: fundo × dst ± fonte × alpha (o AS<s>D<d> e o alpha = s,d do MUGEN)."""
        if im is None:
            return
        w = max(1, round(im.width * abs(sx) * self.sc))
        h = max(1, round(im.height * abs(sy) * self.sc))
        ax, ay = ax * abs(sx) * self.sc, ay * abs(sy) * self.sc
        if facing < 0:
            ax = w - ax
        px, py = self.P(x, y)
        if angle:
            # sprite enorme na tela: a escala vai junto na transformação (ampliar ele inteiro antes
            # passava de 4 milhões de pixels); os comuns ampliam antes e giram suave
            direct = w * h > 4_000_000
            kx, ky = (w / im.width, h / im.height) if direct else (1.0, 1.0)
            if not direct:
                im = im.resize((w, h), Image.NEAREST)
            if facing < 0:
                im = im.transpose(Image.FLIP_LEFT_RIGHT)
            # gira em volta do eixo direto na parte visível da tela (uma transformação afim só):
            # a tela quadrada de 2x o lado em volta do eixo custava 4x a área e travava com
            # efeitos grandes girando
            r = math.radians(angle * (1 if facing > 0 else -1))
            cs, sn = math.cos(r), math.sin(r)
            xs, ys = [], []
            for cx, cy in ((0, 0), (w, 0), (0, h), (w, h)):
                dx, dy = cx - ax, cy - ay
                xs.append(px + dx * cs + dy * sn)
                ys.append(py - dx * sn + dy * cs)
            x0, y0 = max(0, math.floor(min(xs))), max(0, math.floor(min(ys)))
            x1, y1 = min(self.im.width, math.ceil(max(xs))), min(self.im.height, math.ceil(max(ys)))
            if x0 >= x1 or y0 >= y1:
                return
            ox, oy = x0 - px, y0 - py
            im = im.transform((x1 - x0, y1 - y0), Image.AFFINE,
                              (cs / kx, -sn / kx, (ax + cs * ox - sn * oy) / kx,
                               sn / ky, cs / ky, (ay + sn * ox + cs * oy) / ky),
                              resample=Image.NEAREST if direct else Image.BILINEAR)
        else:
            px, py = int(px - ax), int(py - ay)
            # só a área do sprite que cai na tela: uma camada do tamanho da tela por sprite levava
            # ~0,5 s por tick, e ampliar o sprite inteiro antes de recortar sumia com os grandes
            x0, y0 = max(0, px), max(0, py)
            x1, y1 = min(self.im.width, px + w), min(self.im.height, py + h)
            if x0 >= x1 or y0 >= y1:
                return
            if w * h <= 1_000_000:
                im = im.resize((w, h), Image.NEAREST)
                if facing < 0:
                    im = im.transpose(Image.FLIP_LEFT_RIGHT)
                im = im.crop((x0 - px, y0 - py, x1 - px, y1 - py))
            else:
                # sprite enorme na tela: amplia só o recorte (o box do resize dá os mesmos pixels)
                c0, c1 = x0 - px, x1 - px
                if facing < 0:
                    c0, c1 = w - c1, w - c0
                kx, ky = im.width / w, im.height / h
                im = im.resize((x1 - x0, y1 - y0), Image.NEAREST,
                               box=(c0 * kx, (y0 - py) * ky, c1 * kx, (y1 - py) * ky))
                if facing < 0:
                    im = im.transpose(Image.FLIP_LEFT_RIGHT)
        if mode == "normal":
            self.im.alpha_composite(im, (x0, y0))
            return
        src = np.asarray(im, np.float32)
        box = (x0, y0, x1, y1)
        dst_ = np.asarray(self.im.crop(box), np.float32)
        dst_k = dst
        cover = src[..., 3:4] / 255
        rgb = src[..., :3] * cover * alpha
        keep = dst_[..., :3] * (1 - cover * (1 - dst_k))     # o fundo só escurece onde há sprite
        o = dst_.copy()
        o[..., :3] = np.clip(keep - rgb if mode == "sub" else keep + rgb, 0, 255)
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
