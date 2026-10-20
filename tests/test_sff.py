import re

import pytest
from PIL import Image

from conftest import all_sffs
from charfactory.formats import codecs, sff

SFFS = all_sffs()


@pytest.mark.parametrize("path", SFFS, ids=lambda p: p.name)
def test_decode_all_sprites(path):
    s = sff.load(path)
    assert s.sprites
    for sp in s.sprites:
        img = s.image(sp)
        assert img.size == (sp.width, sp.height)


@pytest.mark.parametrize("path", SFFS, ids=lambda p: p.name)
def test_codec_roundtrip(path):
    s = sff.load(path)
    for sp in s.sprites:
        if sp.is_truecolor:
            continue
        px = s.pixels(sp)
        assert codecs.decode_rle8(codecs.encode_rle8(px), len(px)) == px
        if max(px, default=0) < 32:
            assert codecs.decode_lz5(codecs.encode_lz5(px), len(px)) == px


def _rebuild(src: sff.SFF) -> sff.SFF:
    """Força a recodificação de todos os sprites a partir das imagens."""
    new = sff.SFF(palettes=[sff.Palette(p.group, p.item, list(p.colors)) for p in src.palettes])
    for sp in _unique(src):
        img = Image.frombytes("P", (sp.width, sp.height), src.pixels(sp)) \
            if not sp.is_truecolor else src.image(sp)
        new.set_sprite(sp.group, sp.item, img, sp.axis_x, sp.axis_y,
                       sp.palette_index if not sp.is_truecolor else None)
    return new


def _unique(s: sff.SFF) -> list:
    """Sprites sem os números repetidos (o MUGEN usa o primeiro; chars baixados às vezes repetem)."""
    seen, out = set(), []
    for sp in s.sprites:
        if (sp.group, sp.item) not in seen:
            seen.add((sp.group, sp.item))
            out.append(sp)
    return out


@pytest.mark.parametrize("version", [2, 1])
@pytest.mark.parametrize("path", SFFS[:40], ids=lambda p: p.name)
def test_write_read_roundtrip(path, version):
    src = sff.load(path)
    if version == 1 and any(sp.is_truecolor for sp in src.sprites):
        pytest.skip("v1 não tem truecolor")
    back = sff.loads(_rebuild(src).to_bytes(version))
    assert len(back.sprites) == len(_unique(src))
    for a, b in zip(_unique(src), back.sprites):
        assert (a.group, a.item, a.axis_x, a.axis_y) == (b.group, b.item, b.axis_x, b.axis_y)
        if a.is_truecolor:
            assert src.image(a).tobytes() == back.image(b).tobytes()
            continue
        assert src.pixels(a) == back.pixels(b)
        ia, ib = src.image(a).convert("RGB"), back.image(b).convert("RGB")
        assert ia.tobytes() == ib.tobytes(), f"cores diferentes em {a.group},{a.item}"


def test_untouched_save_keeps_encoding(mugen_dir):
    src = sff.load(mugen_dir / "chars/kfm/kfm.sff")
    back = sff.loads(src.to_bytes())
    assert [s.format_name for s in back.sprites] == [s.format_name for s in src.sprites]
    for a, b in zip(src.sprites, back.sprites):
        assert src.pixels(a) == back.pixels(b)


@pytest.mark.parametrize("char", ["kfm", "kfm720"])
def test_lz5_matches_official_sources(mugen_dir, char):
    """Cada sprite LZ5 oficial bate pixel a pixel com o PNG-fonte (após autocrop)."""
    work = mugen_dir / "work" / char
    if not (work / f"{char}-sff.def").exists():
        pytest.skip("a pasta work/ (fontes do kfm) é da instalação oficial do MUGEN 1.0")
    s = sff.load(mugen_dir / "chars" / char / f"{char}.sff")
    checked = 0
    for line in (work / f"{char}-sff.def").read_text("latin-1").splitlines():
        m = re.match(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*([^,;?]+?\.png)\s*,\s*(-?\d+)\s*,\s*(-?\d+)", line, re.I)
        if not m or not (work / m[3].strip()).exists():
            continue
        sp = s.find(int(m[1]), int(m[2]))
        srcimg = Image.open(work / m[3].strip())
        if sp is None or srcimg.mode != "P":
            continue
        mask = Image.frombytes("L", srcimg.size, bytes(1 if v else 0 for v in srcimg.tobytes()))
        box = mask.getbbox()
        crop = srcimg.crop(box)
        a, b = crop.tobytes(), s.pixels(sp)
        assert crop.size == (sp.width, sp.height)
        fwd, rev = {}, {}
        for x, y in zip(a, b):  # sprmake2 remapeia índices: exige bijeção
            assert fwd.setdefault(x, y) == y and rev.setdefault(y, x) == x
        assert (sp.axis_x, sp.axis_y) == (int(m[4]) - box[0], int(m[5]) - box[1])
        checked += 1
    assert checked > 250


def test_add_palette_same_number_replaces_colors():
    from charfactory.formats import sff
    s = sff.SFF()
    a = s.add_palette([(0, 0, 0), (10, 10, 10)], 9, 10)
    b = s.add_palette([(0, 0, 0), (200, 0, 0)], 9, 10)        # mesma numeração, cores novas
    assert a == b and len(s.palettes) == 1 and s.palettes[0].colors[1] == (200, 0, 0)


def test_blank_8bit_sprite_palette_padded_to_256(tmp_path):
    """Quadro vazio (todos os pixels < 32) sai em RLE8 de 8 bits: paleta curta fechava o MUGEN."""
    import struct
    s = sff.SFF()
    pal = s.add_palette([(0, 0, 0)], 1, 1)
    s.set_sprite(9500, 10, Image.new("P", (8, 8), 0), 0, 0, pal)
    path = tmp_path / "blank.sff"
    s.save(path)
    d = path.read_bytes()
    spr_off, _, pal_off, _ = struct.unpack_from("<IIII", d, 0x24)
    depth = d[spr_off + 15]
    palidx = struct.unpack_from("<H", d, spr_off + 24)[0]
    ncol = struct.unpack_from("<H", d, pal_off + 16 * palidx + 4)[0]
    assert depth == 8 and ncol == 256


@pytest.mark.parametrize("path", SFFS, ids=lambda p: p.name)
def test_rle8_vectorized_matches_reference(path):
    s = sff.load(path)
    for sp in s.sprites:
        if sp.fmt == sff.FMT_RLE8 and sp._data is not None:
            body, size = sp._data[4:], sp.width * sp.height
            assert codecs.decode_rle8(body, size) == codecs.decode_rle8_py(body, size)


def test_rle8_vectorized_edge_cases():
    import random
    rng = random.Random(7)
    cases = [(b"", 4), (b"\x41", 4), (b"\x45\x41\x41\x07", 9), (b"\x7f\x7f\x7f", 200), (b"\x40\x40\x05", 3),
             (b"\x43\x09", 2), (b"\x10\x20", 0)]
    for _ in range(5000):
        src = bytes(rng.choice([rng.randint(0, 255), rng.randint(0x40, 0x7F), 0x40, 0x7F])
                    for _ in range(rng.randint(0, 40)))
        cases.append((src, rng.randint(0, 200)))
    for src, size in cases:
        try:
            ref = codecs.decode_rle8_py(src, size)
        except IndexError:          # marcador final sem cor antes de completar o tamanho
            continue
        assert codecs.decode_rle8(src, size) == ref, (src.hex(), size)
