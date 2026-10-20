"""SFF (Elecbyte sprite file) v1.01 / v2.00 / v2.01: leitura, edição e gravação.

Implementação própria baseada na especificação pública da Elecbyte.
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .codecs import decode_lz5, decode_rle5, decode_rle8, encode_lz5, encode_rle8
from ..i18n import tr

SIGNATURE = b"ElecbyteSpr\0"
WRITER_COMMENT = b"Saved by Char Factory"

FMT_RAW, FMT_INVALID, FMT_RLE8, FMT_RLE5, FMT_LZ5 = 0, 1, 2, 3, 4
FMT_PNG8, FMT_PNG24, FMT_PNG32 = 10, 11, 12
FMT_PCX = -1  # só existe em SFF v1
FMT_NAMES = {0: "raw", 1: "invalid", 2: "rle8", 3: "rle5", 4: "lz5",
             10: "png8", 11: "png24", 12: "png32", -1: "pcx"}
_PNG_FORMATS = (FMT_PNG8, FMT_PNG24, FMT_PNG32)


class SFFError(Exception):
    pass


@dataclass
class Palette:
    group: int
    item: int
    colors: list[tuple[int, int, int]]  # índice 0 = cor transparente

    def flat(self) -> list[int]:
        out = [c for rgb in self.colors[:256] for c in rgb]
        return out + [0] * (768 - len(out))

    def key(self) -> tuple:
        return tuple(self.colors)


@dataclass
class Sprite:
    group: int
    item: int
    width: int
    height: int
    axis_x: int
    axis_y: int
    fmt: int
    color_depth: int
    palette_index: int | None
    linked_to: int | None = None
    _data: bytes = field(default=b"", repr=False)       # dados codificados como no arquivo
    _image: Image.Image | None = field(default=None, repr=False)  # imagem nova (ainda não codificada)

    @property
    def format_name(self) -> str:
        return FMT_NAMES.get(self.fmt, f"unknown({self.fmt})")

    @property
    def is_truecolor(self) -> bool:
        if self._image is not None:
            return self._image.mode != "P"
        return self.fmt in (FMT_PNG24, FMT_PNG32)

    def info(self) -> dict:
        return {"group": self.group, "item": self.item, "width": self.width,
                "height": self.height, "axis": [self.axis_x, self.axis_y],
                "format": self.format_name, "color_depth": self.color_depth,
                "palette": self.palette_index, "linked_to": self.linked_to}


@dataclass
class SFF:
    version: tuple[int, int, int, int] = (2, 0, 0, 0)
    sprites: list[Sprite] = field(default_factory=list)
    palettes: list[Palette] = field(default_factory=list)

    # ---------------------------------------------------------- consulta

    @property
    def version_str(self) -> str:
        return ".".join(str(v) for v in self.version)

    def find(self, group: int, item: int) -> Sprite | None:
        return next((s for s in self.sprites if s.group == group and s.item == item), None)

    def index_of(self, group: int, item: int) -> int:
        return next((i for i, s in enumerate(self.sprites)
                     if s.group == group and s.item == item), -1)

    def find_palette(self, group: int, item: int) -> int:
        return next((i for i, p in enumerate(self.palettes)
                     if p.group == group and p.item == item), -1)

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for s in self.sprites:
            out.setdefault(s.group, []).append(s.item)
        return out

    # ---------------------------------------------------------- pixels

    def pixels(self, spr: Sprite) -> bytes:
        """Índices de cor (1 byte por pixel) de um sprite indexado."""
        if spr._image is not None:
            return spr._image.tobytes()
        w, h, size = spr.width, spr.height, spr.width * spr.height
        body = spr._data
        if spr.fmt == FMT_PCX or spr.fmt == FMT_PNG8:
            img = Image.open(io.BytesIO(body if spr.fmt == FMT_PCX else body[4:]))
            if img.mode not in ("P", "L"):  # Pillow abre PCX de paleta cinza como 'L'
                raise SFFError(tr("sprite {0},{1} is not indexed").format(spr.group, spr.item))
            return img.tobytes()
        if spr.fmt == FMT_RAW:
            return bytes(body[:size]).ljust(size, b"\0")
        decoder = {FMT_RLE8: decode_rle8, FMT_RLE5: decode_rle5, FMT_LZ5: decode_lz5}.get(spr.fmt)
        if decoder is None:
            raise SFFError(tr("format {0} is not indexed").format(spr.format_name))
        return bytes(decoder(body[4:], size)).ljust(size, b"\0")

    def image(self, spr: Sprite, palette: Palette | int | None = None) -> Image.Image:
        """Imagem PIL: modo 'P' (transparência = índice 0) ou 'RGBA' para truecolor."""
        if spr.is_truecolor:
            if spr._image is not None:
                return spr._image.convert("RGBA")
            img = Image.open(io.BytesIO(spr._data[4:]))
            return img.convert("RGBA")
        img = Image.frombytes("P", (spr.width, spr.height), self.pixels(spr))
        if isinstance(palette, int):
            palette = self.palettes[palette]
        if palette is None and spr.palette_index is not None and 0 <= spr.palette_index < len(self.palettes):
            palette = self.palettes[spr.palette_index]
        if palette is not None:
            img.putpalette(palette.flat())
        img.info["transparency"] = 0
        return img

    # ---------------------------------------------------------- edição

    def add_palette(self, colors: list[tuple[int, int, int]], group: int | None = None,
                    item: int | None = None) -> int:
        """Adiciona (ou reaproveita) uma paleta e devolve seu índice. Com grupo/item já
        existentes, troca as cores dela: duas paletas com o mesmo número confundem o MUGEN, que
        usa a primeira (a arte saía com as cores de uma versão antiga)."""
        key = tuple(colors)
        for i, p in enumerate(self.palettes):
            if p.key() == key and (group is None or (p.group, p.item) == (group, item)):
                return i
        if group is not None:
            for i, p in enumerate(self.palettes):
                if (p.group, p.item) == (group, item):
                    p.colors = list(colors)
                    return i
        if group is None:
            group, item = 1, 1 + max((p.item for p in self.palettes if p.group == 1), default=0)
        self.palettes.append(Palette(group, item, list(colors)))
        return len(self.palettes) - 1

    def set_sprite(self, group: int, item: int, image: Image.Image, axis_x: int = 0,
                   axis_y: int = 0, palette_index: int | None = None) -> Sprite:
        """Cria ou substitui um sprite a partir de uma imagem PIL.

        Imagens 'P' viram sprites indexados (a paleta da imagem é adicionada se
        palette_index não for dado). Outras viram truecolor (PNG32, exige SFF 2.01).
        """
        if image.mode == "P":
            if palette_index is None:
                raw = image.getpalette() or []
                ncol = max(image.tobytes(), default=0) + 1
                cols = [tuple(raw[i * 3:i * 3 + 3]) for i in range(max(ncol, 1))]
                cols = [c if len(c) == 3 else (0, 0, 0) for c in cols]
                palette_index = self.add_palette(cols)
            depth = 5 if max(image.tobytes(), default=0) < 32 else 8
            img = image.copy()
        else:
            img = image.convert("RGBA")
            palette_index, depth = None, 32
        spr = Sprite(group, item, img.width, img.height, axis_x, axis_y,
                     FMT_RAW if img.mode == "P" else FMT_PNG32, depth, palette_index,
                     None, b"", img)
        idx = self.index_of(group, item)
        if idx >= 0:
            self._unlink(idx)
            self.sprites[idx] = spr
        else:
            self.sprites.append(spr)
        return spr

    def remove_sprite(self, group: int, item: int) -> bool:
        idx = self.index_of(group, item)
        if idx < 0:
            return False
        self._unlink(idx)
        del self.sprites[idx]
        for s in self.sprites:
            if s.linked_to is not None and s.linked_to > idx:
                s.linked_to -= 1
        return True

    def _unlink(self, idx: int) -> None:
        """Sprites que apontam para idx ganham cópia própria dos dados."""
        for s in self.sprites:
            if s.linked_to == idx:
                s.linked_to = None

    # ---------------------------------------------------------- gravação

    def save(self, path: str | Path, version: int = 2, compress5: str = "rle8",
             compress8: str = "rle8") -> None:
        data = self.to_bytes(version, compress5, compress8)
        Path(path).write_bytes(data)

    def to_bytes(self, version: int = 2, compress5: str = "rle8", compress8: str = "rle8") -> bytes:
        if version == 1:
            return _write_v1(self)
        if version == 2:
            return _write_v2(self, compress5, compress8)
        raise SFFError(tr("the save version must be 1 or 2"))


# ================================================================== leitura

def load(path: str | Path) -> SFF:
    return loads(Path(path).read_bytes())


def loads(data: bytes) -> SFF:
    if data[:12] != SIGNATURE:
        raise SFFError(tr("invalid signature (not ElecbyteSpr)"))
    ver = (data[15], data[14], data[13], data[12])
    if ver[0] == 1:
        return _load_v1(data, ver)
    if ver[0] == 2:
        return _load_v2(data, ver)
    raise SFFError(tr("SFF version {0} not supported").format('.'.join(map(str, ver))))


def _pcx_size(body: bytes) -> tuple[int, int]:
    if len(body) < 128:
        return 0, 0
    xmin, ymin, xmax, ymax = struct.unpack_from("<HHHH", body, 4)
    return xmax - xmin + 1, ymax - ymin + 1


def _pcx_palette(body: bytes) -> list[tuple[int, int, int]] | None:
    if len(body) >= 769 and body[-769] == 0x0C:
        p = body[-768:]
        return [(p[i], p[i + 1], p[i + 2]) for i in range(0, 768, 3)]
    return None


def _load_v1(data: bytes, ver) -> SFF:
    sff = SFF(ver)
    _groups, count, off, _subsize, _paltype = struct.unpack_from("<IIIIB", data, 16)
    prev_pal: int | None = None
    for _ in range(count):
        if off == 0 or off + 32 > len(data):
            break
        nxt, length, ax, ay, grp, item, link, samepal = struct.unpack_from("<IIhhHHHB", data, off)
        body = data[off + 32: off + 32 + length]
        linked = None
        if length == 0 and link < len(sff.sprites):
            linked = link
            body = sff.sprites[link]._data
        pal = _pcx_palette(body)
        if samepal and prev_pal is not None:
            pal_idx = prev_pal
        elif linked is not None:
            pal_idx = sff.sprites[linked].palette_index
        elif pal is not None:
            pal_idx = sff.add_palette(pal)
        else:
            pal_idx = prev_pal
        w, h = _pcx_size(body)
        sff.sprites.append(Sprite(grp, item, w, h, ax, ay, FMT_PCX, 8, pal_idx, linked, body))
        prev_pal = pal_idx
        off = nxt
    return sff


def _load_v2(data: bytes, ver) -> SFF:
    sff = SFF(ver)
    (spr_off, spr_count, pal_off, pal_count,
     ldata_off, _ldata_len, tdata_off, _tdata_len) = struct.unpack_from("<8I", data, 36)
    raw_pals: list[bytes] = []
    for i in range(pal_count):
        grp, item, ncol, link, off, length = struct.unpack_from("<HHHHII", data, pal_off + i * 16)
        raw = raw_pals[link] if length == 0 and link < len(raw_pals) else \
            data[ldata_off + off: ldata_off + off + length]
        raw_pals.append(raw)
        cols = [(raw[j], raw[j + 1], raw[j + 2]) for j in range(0, min(len(raw), ncol * 4), 4)]
        sff.palettes.append(Palette(grp, item, cols))
    for i in range(spr_count):
        (grp, item, w, h, ax, ay, link, fmt, depth,
         off, length, palidx, flags) = struct.unpack_from("<HHHHhhHBBIIHH", data, spr_off + i * 28)
        if length == 0 and link < len(sff.sprites):
            src = sff.sprites[link]
            sff.sprites.append(Sprite(grp, item, src.width, src.height, ax, ay, src.fmt,
                                      src.color_depth, palidx, link, src._data))
            continue
        base = tdata_off if flags & 1 else ldata_off
        sff.sprites.append(Sprite(grp, item, w, h, ax, ay, fmt, depth, palidx, None,
                                  data[base + off: base + off + length]))
    return sff


# ================================================================== gravação v2

def _encode_v2(sff: SFF, spr: Sprite, compress5: str, compress8: str) -> tuple[int, int, bytes]:
    """Devolve (formato, profundidade, dados) para gravar o sprite."""
    if spr._image is None and spr.fmt in (FMT_RLE8, FMT_RLE5, FMT_LZ5, FMT_RAW, *_PNG_FORMATS):
        return spr.fmt, spr.color_depth, spr._data  # mantém a codificação original
    if spr.is_truecolor:
        buf = io.BytesIO()
        sff.image(spr).save(buf, "PNG", optimize=True)
        return FMT_PNG32, 32, struct.pack("<I", spr.width * spr.height * 4) + buf.getvalue()
    pixels = sff.pixels(spr)
    size = len(pixels)
    if max(pixels, default=0) < 32:
        depth, method = 5, compress5
    else:
        depth, method = 8, compress8
    if method == "lz5" and depth == 5:
        return FMT_LZ5, depth, struct.pack("<I", size) + encode_lz5(pixels)
    if method in ("rle8", "lz5"):
        # RLE8 sempre com profundidade 8: o MUGEN 1.1 fecha ao abrir alguns sprites que o
        # nosso codificador LZ5 gera (ex.: efeitos vindos de PCX), então LZ5 só sob pedido
        return FMT_RLE8, 8, struct.pack("<I", size) + encode_rle8(pixels)
    return FMT_RAW, depth, pixels


def _write_v2(sff: SFF, compress5: str, compress8: str) -> bytes:
    uses_png = False
    ldata = bytearray()

    # paletas (duplicatas viram link)
    pal_nodes = bytearray()
    seen_pal: dict[bytes, int] = {}
    # paleta usada por sprite de 8 bits precisa de 256 cores: com menos (ex.: 40 de uma
    # sheet) o MUGEN 1.1 lê além do fim e fecha no começo da luta (exit 3, sem log)
    # Sprite recodificado com todos os pixels < 32 (ex.: quadro vazio) também sai em RLE8 de
    # 8 bits (_encode_v2), então a paleta dele também precisa de 256
    kept = (FMT_RLE8, FMT_RLE5, FMT_LZ5, FMT_RAW, *_PNG_FORMATS)
    eight = {s.palette_index for s in sff.sprites if not s.is_truecolor and (
        s.color_depth == 8 or (compress5 != "lz5" and not (s._image is None and s.fmt in kept)))}
    for i, p in enumerate(sff.palettes):
        cols = list(p.colors[:256])
        need = 256 if i in eight or len(cols) > 32 else 32
        cols += [(0, 0, 0)] * (need - len(cols))
        # 4º byte = alfa no SFF 2.01 (o MUGEN 1.1 desenha alfa 0 como invisível):
        # índice 0 transparente, demais opacos, como grava o sprmake2
        raw = b"".join(bytes((r, g, b, 0 if k == 0 else 255))
                       for k, (r, g, b) in enumerate(cols))
        if raw in seen_pal:
            pal_nodes += struct.pack("<HHHHII", p.group, p.item, len(cols), seen_pal[raw], 0, 0)
        else:
            seen_pal[raw] = i
            pal_nodes += struct.pack("<HHHHII", p.group, p.item, len(cols), 0, len(ldata), len(raw))
            ldata += raw

    # sprites (dados idênticos viram link)
    spr_nodes = bytearray()
    seen_data: dict[tuple, int] = {}
    root: list[int] = []  # índice do sprite que de fato guarda os dados
    for i, s in enumerate(sff.sprites):
        root.append(i)
        if s.linked_to is not None and s._image is None and s.linked_to < i:
            root[i] = root[s.linked_to]
            spr_nodes += struct.pack("<HHHHhhHBBIIHH", s.group, s.item, s.width, s.height,
                                     s.axis_x, s.axis_y, root[i], s.fmt, s.color_depth,
                                     0, 0, s.palette_index or 0, 0)
            continue
        fmt, depth, body = _encode_v2(sff, s, compress5, compress8)
        uses_png |= fmt in _PNG_FORMATS
        key = (fmt, s.width, s.height, body)
        if key in seen_data:
            root[i] = seen_data[key]
            spr_nodes += struct.pack("<HHHHhhHBBIIHH", s.group, s.item, s.width, s.height,
                                     s.axis_x, s.axis_y, seen_data[key], fmt, depth,
                                     0, 0, s.palette_index or 0, 0)
            continue
        seen_data[key] = i
        spr_nodes += struct.pack("<HHHHhhHBBIIHH", s.group, s.item, s.width, s.height,
                                 s.axis_x, s.axis_y, 0, fmt, depth, len(ldata), len(body),
                                 s.palette_index or 0, 0)
        ldata += body

    ver = (2, 0, 1, 0) if uses_png else (2, 0, 0, 0)
    pal_off = 512
    spr_off = pal_off + len(pal_nodes)
    ldata_off = spr_off + len(spr_nodes)
    hdr = bytearray(512)
    hdr[0:12] = SIGNATURE
    hdr[12:16] = bytes((ver[3], ver[2], ver[1], ver[0]))
    hdr[24:28] = bytes((ver[3], ver[2], ver[1], ver[0]))  # versão compatível
    struct.pack_into("<8I", hdr, 36, spr_off, len(sff.sprites), pal_off, len(sff.palettes),
                     ldata_off, len(ldata), ldata_off + len(ldata), 0)
    hdr[76:76 + len(WRITER_COMMENT)] = WRITER_COMMENT
    sff.version = ver
    return bytes(hdr + pal_nodes + spr_nodes + ldata)


# ================================================================== gravação v1

def _write_v1(sff: SFF) -> bytes:
    subs: list[bytes] = []
    groups = set()
    for i, s in enumerate(sff.sprites):
        groups.add(s.group)
        if s.is_truecolor:
            raise SFFError(tr("SFF v1 does not support truecolor (sprite {0},{1})").format(s.group, s.item))
        if s.fmt == FMT_PCX and s._image is None and s.linked_to is None:
            body = s._data
        elif s.linked_to is not None and s._image is None and s.linked_to < i:
            body = b""
        else:
            buf = io.BytesIO()
            sff.image(s).save(buf, "PCX")
            body = buf.getvalue()
        subs.append(body)
    out = bytearray(512)
    out[0:12] = SIGNATURE
    out[12:16] = bytes((0, 1, 0, 1))
    struct.pack_into("<IIIIB", out, 16, len(groups), len(sff.sprites), 512, 32, 0)
    out[36:36 + len(WRITER_COMMENT)] = WRITER_COMMENT
    pos = 512
    for i, (s, body) in enumerate(zip(sff.sprites, subs)):
        nxt = pos + 32 + len(body) if i + 1 < len(subs) else 0
        link = s.linked_to if not body else 0
        out += struct.pack("<IIhhHHHB13x", nxt, len(body), s.axis_x, s.axis_y,
                           s.group, s.item, link or 0, 0)
        out += body
        pos += 32 + len(body)
    sff.version = (1, 0, 1, 0)
    return bytes(out)
