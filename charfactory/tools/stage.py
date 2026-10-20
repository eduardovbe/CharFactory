"""Stage do MUGEN: leitura das camadas de fundo ([BG …], [BGCtrlDef …]) e prévia renderizada.

Posição de uma camada, como no MUGEN 1.0/1.1 (bgs.html): `start` é o eixo da camada em
relação ao centro do topo da tela com a câmera em (0, 0); a câmera desloca a camada por
`delta` (x = W/2 + start.x - câmera.x * delta.x, y = start.y - câmera.y * delta.y). `velocity`
move a camada a cada tick e `tile` repete o sprite (1 = infinito, n = n vezes).
Os tipos `normal`, `anim` e `parallax` são desenhados (o parallax como normal, sem a
deformação das linhas); BGCtrls não são simulados.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from ..formats import air, mtext, sff
from ..i18n import tr

_BG = re.compile(r"^bg(?!def\b|ctrl)", re.I)


def _nums(v: str | None, n: int, default: float = 0.0) -> list[float]:
    out = []
    for t in (v or "").split(";")[0].split(","):
        try:
            out.append(float(t.strip()))
        except ValueError:
            pass
    return (out + [default] * n)[:n]


@dataclass
class Layer:
    section: mtext.Section
    name: str
    type: str = "normal"
    spriteno: tuple[int, int] = (0, 0)
    actionno: int | None = None
    start: tuple[float, float] = (0.0, 0.0)
    delta: tuple[float, float] = (1.0, 1.0)
    velocity: tuple[float, float] = (0.0, 0.0)
    tile: tuple[int, int] = (0, 0)
    tilespacing: tuple[int, int] = (0, 0)
    trans: str = ""
    alpha: tuple[int, int] = (256, 0)
    layerno: int = 0
    mask: bool = False
    window: tuple[int, int, int, int] | None = None
    scale: tuple[float, float] = (1.0, 1.0)
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_section(cls, sec: mtext.Section) -> "Layer":
        d = {k.lower(): v for k, v in sec.as_dict().items()}
        name = re.sub(r"(?i)^bg\s*", "", sec.name).strip()
        lay = cls(sec, name or sec.name)
        lay.type = (d.get("type") or "normal").split(";")[0].strip().lower()
        sp = _nums(d.get("spriteno"), 2)
        lay.spriteno = (int(sp[0]), int(sp[1]))
        if d.get("actionno"):
            lay.actionno = int(_nums(d["actionno"], 1)[0])
        lay.start = tuple(_nums(d.get("start"), 2))
        lay.delta = tuple(_nums(d.get("delta"), 2, 1.0))
        lay.velocity = tuple(_nums(d.get("velocity"), 2))
        t = _nums(d.get("tile"), 2)
        lay.tile = (int(t[0]), int(t[1]))
        ts = _nums(d.get("tilespacing"), 2)
        lay.tilespacing = (int(ts[0]), int(ts[1] if d.get("tilespacing", "").count(",") else ts[0]))
        lay.trans = (d.get("trans") or "").split(";")[0].strip().lower()
        a = _nums(d.get("alpha"), 2, 256)
        lay.alpha = (int(a[0]), int(a[1]))
        lay.layerno = int(_nums(d.get("layerno"), 1)[0])
        lay.mask = (d.get("mask") or "0").split(";")[0].strip() not in ("0", "")
        w = d.get("window") or d.get("maskwindow")
        if w:
            v = _nums(w, 4)
            lay.window = tuple(int(x) for x in v)
        for k in ("width", "xscale", "yscalestart", "yscaledelta"):
            if d.get(k):
                lay.extra[k] = d[k]
        sc = _nums(d.get("scalestart"), 2, 1.0)
        lay.scale = (sc[0] or 1.0, sc[1] or 1.0)
        return lay


class Stage:
    def __init__(self, def_path: str | Path, mugen_root: str | Path | None = None):
        self.path = Path(def_path).resolve()
        self.doc = mtext.load(self.path)
        self.mugen_root = Path(mugen_root) if mugen_root else self._find_root()
        self._sff: sff.SFF | None = None
        self.reload()

    def _find_root(self) -> Path | None:
        for p in [self.path.parent, *self.path.parents]:
            if (p / "data").is_dir() and ((p / "chars").is_dir() or (p / "stages").is_dir()):
                return p
        return None

    def reload(self) -> None:
        self.anims = air.AIR(self.doc)
        self.layers = [Layer.from_section(s) for s in self.doc.sections if _BG.match(s.name)]
        self.controls = [s for s in self.doc.sections if s.name.lower().startswith("bgctrldef")]

    # ------------------------------------------------------------ dados

    def get(self, section: str, key: str, default: str = "") -> str:
        return (self.doc.get(section, key) or default).split(";")[0].strip().strip('"')

    @property
    def name(self) -> str:
        return self.get("Info", "displayname") or self.get("Info", "name") or self.path.stem

    @property
    def localcoord(self) -> tuple[int, int]:
        v = _nums(self.get("StageInfo", "localcoord", "320,240"), 2, 240)
        return int(v[0] or 320), int(v[1] or 240)

    def sff_path(self) -> Path | None:
        rel = self.get("BGdef", "spr")
        if not rel:
            return None
        rel = rel.replace("\\", "/")
        for base in [self.path.parent, *( [self.mugen_root, self.mugen_root / "stages"] if self.mugen_root else [])]:
            if (base / rel).exists():
                return base / rel
        return self.path.parent / Path(rel).name

    @property
    def sprites(self) -> sff.SFF:
        if self._sff is None:
            p = self.sff_path()
            if not p or not p.exists():
                raise FileNotFoundError(tr("stage SFF not found: {0}").format(self.get('BGdef', 'spr')))
            self._sff = sff.load(p)
        return self._sff

    def problems(self) -> list[str]:
        out = []
        try:
            s = self.sprites
        except (FileNotFoundError, sff.SFFError) as e:
            return [str(e)]
        for lay in self.layers:
            if lay.type in ("normal", "parallax") and s.find(*lay.spriteno) is None:
                out.append(tr("[BG {0}]: sprite {1},{2} does not exist").format(lay.name, lay.spriteno[0], lay.spriteno[1]))
            if lay.type == "anim" and (lay.actionno is None or self.anims.get(lay.actionno) is None):
                out.append(tr("[BG {0}]: actionno {1} does not exist").format(lay.name, lay.actionno))
        return out

    # ------------------------------------------------------------ desenho

    def _sprite(self, g: int, i: int) -> tuple[Image.Image, int, int] | None:
        s = self.sprites
        spr = s.find(g, i)
        if spr is None:
            return None
        return s.image(spr).convert("RGBA"), spr.axis_x, spr.axis_y

    def _layer_image(self, lay: Layer, tick: int):
        if lay.actionno is not None:         # anim (e parallax com actionno)
            act = self.anims.get(lay.actionno) if lay.actionno is not None else None
            if not act or not act.frames:
                return None
            total = sum(max(1, f.time) for f in act.frames if f.time >= 0) or 1
            t = tick
            if act.total_time >= 0 and t >= total:
                loop_start = sum(max(1, f.time) for f in act.frames[:act.loopstart])
                span = max(1, total - loop_start)
                t = loop_start + (t - loop_start) % span
            acc = 0
            frame = act.frames[-1]
            for f in act.frames:
                d = f.time if f.time > 0 else (10 ** 9 if f.time < 0 else 1)
                if t < acc + d:
                    frame = f
                    break
                acc += d
            got = self._sprite(frame.group, frame.item) if frame.group >= 0 else None
            if not got:
                return None
            img, ax, ay = got
            if "H" in frame.flip.upper():
                img, ax = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT), img.width - ax
            if "V" in frame.flip.upper():
                img, ay = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM), img.height - ay
            return img, ax - frame.x, ay - frame.y, frame.trans.lower()
        got = self._sprite(*lay.spriteno)
        if not got:
            return None
        img, ax, ay = got
        return img, ax, ay, ""

    @staticmethod
    def _factors(mode: str, alpha=(256, 0)) -> tuple[int, int] | None:
        """(fonte, destino) em 1/256 para os modos aditivos; None = desenho normal ou sub."""
        m = re.match(r"^as(\d+)d(\d+)$", mode)
        if m:
            return int(m[1]), int(m[2])
        return {"add": (256, 256), "a": (256, 256), "add1": (256, 128), "a1": (256, 128),
                "addalpha": alpha}.get(mode)

    @classmethod
    def _blend(cls, dst: Image.Image, img: Image.Image, x: int, y: int, mode: str, alpha=(256, 0)) -> None:
        f = cls._factors(mode, alpha)
        if f is not None:
            import numpy as np
            s, d = f
            region = (x, y, x + img.width, y + img.height)
            base = np.asarray(dst.crop(region).convert("RGB"), dtype=np.float32)
            src = np.asarray(img.convert("RGBA"), dtype=np.float32)
            a = (src[..., 3:4] > 0)
            out = np.where(a, base * d / 256 + src[..., :3] * s / 256, base)
            dst.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB"), (x, y))
        elif mode in ("sub", "s"):
            region = (x, y, x + img.width, y + img.height)
            base = dst.crop(region).convert("RGB")
            src = img.convert("RGBA")
            rgb = Image.composite(src.convert("RGB"), Image.new("RGB", img.size), src.getchannel("A"))
            dst.paste(ImageChops.subtract(base, rgb), (x, y))
        else:
            dst.alpha_composite(img, (x, y)) if 0 <= x and 0 <= y and x + img.width <= dst.width \
                and y + img.height <= dst.height else dst.paste(img, (x, y), img)

    def render(self, cam_x: float = 0.0, cam_y: float = 0.0, tick: int = 0, layers=(0, 1),
               players: bool = True, selected: int | None = None, only: int | None = None) -> Image.Image:
        """Imagem do tamanho de `localcoord` com a câmera em (cam_x, cam_y) no tick `tick`."""
        W, H = self.localcoord
        k_hi = 0.5 if self.get("StageInfo", "hires", "0") not in ("0", "") else 1.0   # hires: tudo em 2x
        bg = _nums(self.get("BGdef", "bgclearcolor", "0,0,0"), 3)
        im = Image.new("RGBA", (W, H), (int(bg[0]), int(bg[1]), int(bg[2]), 255))
        sel_box = None
        for front in (0, 1):
            if front not in layers:
                continue
            for k, lay in enumerate(self.layers):
                if lay.layerno != front or (only is not None and k != only):
                    continue
                got = self._layer_image(lay, tick)
                if not got:
                    continue
                img, ax, ay, ftrans = got
                if lay.type == "parallax" and (lay.extra.get("width") or lay.extra.get("xscale")):
                    self._draw_parallax(im, lay, img, ax, ay, cam_x, cam_y, tick, k_hi, lay.trans or ftrans)
                    continue
                sx_, sy_ = lay.scale[0] * k_hi, lay.scale[1] * k_hi
                if (sx_, sy_) != (1.0, 1.0):
                    img = img.resize((max(1, round(img.width * sx_)), max(1, round(img.height * sy_))),
                                     Image.Resampling.NEAREST)
                    ax, ay = ax * sx_, ay * sy_
                x = W / 2 + (lay.start[0] + lay.velocity[0] * tick) * k_hi - cam_x * lay.delta[0] - ax
                y = (lay.start[1] + lay.velocity[1] * tick) * k_hi - cam_y * lay.delta[1] - ay
                tx, ty = lay.tile
                sx, sy = img.width + lay.tilespacing[0], img.height + lay.tilespacing[1]
                xs = [x]
                if tx == 1 and sx > 0:
                    first = x - math.ceil((x + img.width) / sx) * sx
                    xs = [first + n * sx for n in range(int((W - first) / sx) + 2)]
                elif tx > 1:
                    xs = [x + n * sx for n in range(tx)]
                ys = [y]
                if ty == 1 and sy > 0:
                    first = y - math.ceil((y + img.height) / sy) * sy
                    ys = [first + n * sy for n in range(int((H - first) / sy) + 2)]
                elif ty > 1:
                    ys = [y + n * sy for n in range(ty)]
                mode = lay.trans or ftrans
                for yy in ys:
                    for xx in xs:
                        if xx + img.width < 0 or xx > W or yy + img.height < 0 or yy > H:
                            continue
                        self._blend(im, img, int(round(xx)), int(round(yy)), mode, lay.alpha)
                if k == selected:
                    sel_box = (min(xs), min(ys), max(xs) + img.width, max(ys) + img.height)
            if front == 0 and players:
                d = ImageDraw.Draw(im)
                z = _nums(self.get("StageInfo", "zoffset", str(H - 40)), 1)[0]
                for p, col in (("p1", (80, 160, 255, 230)), ("p2", (255, 90, 90, 230))):
                    px = W / 2 + _nums(self.get("PlayerInfo", f"{p}startx", "0"), 1)[0] - cam_x
                    d.rectangle([px - 12, z - cam_y - 70, px + 12, z - cam_y], outline=col)
                    d.text((px - 6, z - cam_y - 82), p.upper(), fill=col)
                d.line([(0, z - cam_y), (W, z - cam_y)], fill=(255, 255, 0, 120))
        if sel_box:
            d = ImageDraw.Draw(im)
            d.rectangle([max(0, sel_box[0]), max(0, sel_box[1]), min(W - 1, sel_box[2]), min(H - 1, sel_box[3])],
                        outline=(0, 255, 160, 255), width=2)
        return im

    def _draw_parallax(self, im, lay: Layer, img, ax, ay, cam_x, cam_y, tick, k_hi, mode) -> None:
        """Chão em perspectiva: cada linha do sprite é esticada de `width` (ou `xscale`) do topo
        à base e repetida na horizontal; `yscalestart` (em %) estica na vertical."""
        import numpy as np
        ay = 0                       # o chão começa no `start` y (o eixo vertical do sprite não conta)
        W, H = im.size
        src = np.asarray(img.convert("RGBA"))
        h, w = src.shape[:2]
        if lay.extra.get("width"):
            top, bot = (v * k_hi for v in _nums(lay.extra["width"], 2, w))
        else:
            xs = _nums(lay.extra["xscale"], 2, 1.0)
            top, bot = w * xs[0] * k_hi, w * xs[1] * k_hi
        ys = (_nums(lay.extra.get("yscalestart"), 1, 100)[0] or 100) / 100 * lay.scale[1] * k_hi
        x0 = W / 2 + (lay.start[0] + lay.velocity[0] * tick) * k_hi - cam_x * lay.delta[0]
        y0 = (lay.start[1] + lay.velocity[1] * tick) * k_hi - cam_y * lay.delta[1]
        y_top, y_bot = int(y0 - ay * ys), int(y0 + (h - ay) * ys)
        rows = range(max(0, y_top), min(H, y_bot))
        if not len(rows):
            return
        out = np.zeros((len(rows), W, 4), dtype=np.uint8)
        xs_screen = np.arange(W)
        for k, y in enumerate(rows):
            r = min(h - 1, max(0, int((y - y0) / ys + ay)))
            t = r / max(1, h - 1)
            wr = max(1.0, top + (bot - top) * t)
            sx = ((xs_screen - x0) / wr * w + ax) % w if lay.tile[0] else (xs_screen - x0) / wr * w + ax
            valid = (sx >= 0) & (sx < w)
            row = np.zeros((W, 4), dtype=np.uint8)
            row[valid] = src[r, sx[valid].astype(int)]
            out[k] = row
        layer = Image.fromarray(out, "RGBA")
        if self._factors(mode, lay.alpha) is not None or mode in ("sub", "s"):
            self._blend(im, layer, 0, rows[0], mode, lay.alpha)
        else:
            im.alpha_composite(layer, (0, rows[0]))

    def camera_bounds(self) -> tuple[int, int, int, int]:
        l = int(_nums(self.get("Camera", "boundleft", "-150"), 1)[0])
        r = int(_nums(self.get("Camera", "boundright", "150"), 1)[0])
        hi = int(_nums(self.get("Camera", "boundhigh", "-25"), 1)[0])
        lo = int(_nums(self.get("Camera", "boundlow", "0"), 1)[0])
        return l, r, hi, lo

    def save(self, path: str | Path | None = None) -> None:
        self.anims.to_text()           # as ações do stage vivem no mesmo .def
        self.doc.save(path or self.path)


def detect_kind(def_path: str | Path) -> str:
    """'character', 'stage', 'storyboard', 'font', 'select', 'system', 'fight' ou 'text'."""
    try:
        doc = mtext.load(def_path)
    except OSError:
        return "text"
    names = {s.name.lower() for s in doc.sections}
    files = doc.find("Files")
    keys = {k.lower() for k in files.as_dict()} if files else set()
    if keys & {"cmd", "cns", "anim", "sprite", "st"}:
        return "character"
    if "bgdef" in names or "stageinfo" in names:
        return "stage"
    if "scenedef" in names:
        return "storyboard"
    if any(n.startswith("fnt") for n in names) or "def" in names and "fnt v2" in names:
        return "font"
    if "characters" in names or "extrastages" in names:
        return "select"
    if "title info" in names or "select info" in names:
        return "system"
    if "lifebar" in names or "fightfx" in keys or "simul lifebar" in names:
        return "fight"
    return "text"
