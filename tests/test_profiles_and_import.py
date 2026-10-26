"""Perfis de execução, importação com paleta embutida/escala e os caches de sprite."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from charfactory.formats import sff
from charfactory.tools import charkit, ops, runner
from charfactory.tools import spriteimport as SI


# ------------------------------------------------------------------ perfis

def test_profile_leaves_ikemen_only_options_out_of_mugen():
    p = runner.RunProfile("t", p2="kfm", stage="s.def", p2_ai=4, p1_color=3, p2_life=500, rounds=1,
                          time=-1, speed=2, windowed=True, max_power=True, no_music=True, extra='-log "a b.txt"')
    ike = runner.profile_args(Path("x.exe"), "ikemen", "a/a.def", p)
    assert ike[1:] == ["a/a.def", "kfm", "-s", "s.def", "-p2.ai", "4", "-rounds", "1", "-p1.color", "3",
                       "-p2.life", "500", "-time", "-1", "-speed", "2", "-windowed", "-maxpowermode",
                       "-nomusic", "-log", "a b.txt"]
    mugen = runner.profile_args(Path("x.exe"), "mugen", "a/a.def", p)
    assert not {"-time", "-speed", "-windowed", "-maxpowermode"} & set(mugen)
    assert p.unsupported("mugen") == ["windowed", "time", "speed", "max_power"] and not p.unsupported("ikemen")


def test_profiles_start_from_the_old_run_options():
    old = {"run_engine": "ikemen", "run_p2": "kfm", "run_stage": "x.def", "run_p2_ai": 5}
    (p,) = runner.load_profiles(old)
    assert (p.engine, p.p2, p.stage, p.p2_ai) == ("ikemen", "kfm", "x.def", 5)
    s = {"run_profiles": [runner.RunProfile("A").to_dict(), runner.RunProfile("B", p2_ai=8).to_dict()],
         "run_profile": "B", "unknown": 1}
    assert runner.active_profile(s).p2_ai == 8 and runner.active_profile(s, "A").name == "A"
    assert runner.RunProfile.from_dict({"name": "x", "campo_velho": 1}).name == "x"


def test_lists_chars_and_stages_of_an_engine_folder(tmp_path):
    (tmp_path / "chars" / "kfm").mkdir(parents=True)
    (tmp_path / "chars" / "kfm" / "kfm.def").write_text("")
    (tmp_path / "chars" / "pack").mkdir()
    (tmp_path / "chars" / "pack" / "a.def").write_text("")
    (tmp_path / "stages").mkdir()
    (tmp_path / "stages" / "stage0.def").write_text("")
    assert runner.list_dir_entries(tmp_path, "chars") == ["kfm", "pack/a.def"]
    assert runner.list_dir_entries(tmp_path, "stages") == ["stage0.def"]


# ------------------------------------------------------------------ cores

def test_unique_colors_and_nearest_match_the_slow_versions():
    rng = np.random.default_rng(1)
    rgb = rng.integers(0, 256, (400, 300, 3), dtype=np.uint8)
    assert np.array_equal(ops.unique_colors(rgb), np.unique(rgb.reshape(-1, 3), axis=0))
    pal = [tuple(map(int, c)) for c in rng.integers(0, 256, (256, 3))]
    pal[9] = pal[40]                                     # repetida: fica o primeiro índice
    P = np.array(pal[1:], np.int32)
    flat = rgb.reshape(-1, 3).astype(np.int32)
    ref = np.concatenate([((flat[k:k + 4096, None] - P[None]) ** 2).sum(2).argmin(1)
                          for k in range(0, len(flat), 4096)]) + 1
    assert np.array_equal(ops._nearest_indices(rgb, pal), ref)


# ------------------------------------------------------------------ importar

class _Char:
    def __init__(self):
        self.sprites = sff.SFF()
        self.sprites.palettes.append(sff.Palette(1, 1, [(0, 0, 0)] + [(k, k, k) for k in range(1, 256)]))
        self.marked = set()

    def mark(self, *what):
        self.marked.update(what)


def _indexed(path, colors, size=(20, 30)):
    a = np.zeros(size[::-1], np.uint8)
    a[5:25, 4:16] = 3
    a[10:12, 6:8] = 7
    im = Image.fromarray(a, "P")
    im.putpalette([v for c in colors for v in c])
    im.save(path)
    return path


def test_embedded_palette_keeps_indices_and_can_become_the_character_palette(tmp_path):
    cols = [(255, 0, 255)] + [(k, 200, 30) for k in range(1, 256)]
    p = _indexed(tmp_path / "a.png", cols)
    char = _Char()
    imgs = SI.open_images([p])
    assert SI.recommend(imgs, char)[0] == "embedded" and SI.image_info(imgs[0])["indexed"]
    r = SI.import_images(char, [p], 200, 0, SI.ImportOptions(mode="embedded"))
    spr = char.sprites.find(200, 0)
    assert r["palettes"] and char.sprites.palettes[spr.palette_index].colors[3] == cols[3]
    assert set(np.unique(np.frombuffer(char.sprites.pixels(spr), np.uint8))) == {3, 7}      # a borda vazia foi recortada
    SI.import_images(char, [p], 200, 1, SI.ImportOptions(mode="embedded", as_char_palette=True))
    assert char.sprites.palettes[char.sprites.find_palette(1, 1)].colors[7] == cols[7]
    with pytest.raises(ValueError):                      # RGB não tem paleta embutida
        rgb = tmp_path / "b.png"
        Image.new("RGB", (4, 4), (1, 2, 3)).save(rgb)
        SI.convert(SI.open_images([rgb]), char, SI.ImportOptions(mode="embedded"))


def test_scale_reduces_before_importing_and_keeps_indices(tmp_path):
    p = _indexed(tmp_path / "a.png", [(0, 0, 0)] * 256, size=(200, 300))
    char = _Char()
    SI.import_images(char, [p], 0, 0, SI.ImportOptions(mode="embedded", scale=0.5, autocrop=False))
    spr = char.sprites.find(0, 0)
    assert (spr.width, spr.height) == (100, 150) and (spr.axis_x, spr.axis_y) == (50, 150)


def test_palette_from_image(tmp_path):
    cols = [(k, 255 - k, 7) for k in range(256)]
    p = _indexed(tmp_path / "a.png", cols)
    assert SI.palette_from_image(p)[:4] == cols[:4]


# ------------------------------------------------------------------ caches

def test_sprite_index_follows_edits_made_directly_in_the_list():
    s = sff.SFF()
    for g in range(5):
        s.set_sprite(g, 0, Image.new("P", (2, 2)), palette_index=0)
    s.palettes.append(sff.Palette(1, 1, [(0, 0, 0)]))
    assert s.find(3, 0).group == 3
    s.find(3, 0).group = 30                              # renumerado direto no sprite
    assert s.find(3, 0) is None and s.find(30, 0).group == 30
    del s.sprites[0]
    assert s.index_of(4, 0) == 3 and s.find(0, 0) is None
    s.sprites.append(sff.Sprite(9, 9, 1, 1, 0, 0, sff.FMT_RAW, 8, 0, None, b"\x01"))
    assert s.find(9, 9) is not None


def test_rgba_cache_sees_palette_edits():
    class C:
        sprites = sff.SFF()
    c = C()
    c.sprites.palettes.append(sff.Palette(1, 1, [(0, 0, 0), (255, 0, 0)]))
    c.sprites.set_sprite(0, 0, Image.new("P", (2, 2), 1), palette_index=0)
    im, *_ = charkit.sprite_rgba(c, 0, 0)
    assert im.getpixel((0, 0)) == (255, 0, 0, 255)
    c.sprites.palettes[0].colors[1] = (0, 0, 255)
    im, *_ = charkit.sprite_rgba(c, 0, 0)
    assert im.getpixel((0, 0)) == (0, 0, 255, 255)


def test_big_zoomed_sprite_is_drawn_not_dropped():
    # 1500x1500 com zoom 3 passava do limite antigo e sumia da tela do simulador
    im = Image.new("RGBA", (1500, 1500), (255, 0, 0, 255))
    cv = charkit._Canvas((-100, 100, -150, 15), 3)
    cv.blit(im, 750, 1500, 0, 0)
    assert cv.im.getpixel((300, 300))[:3] == (255, 0, 0)
    cv = charkit._Canvas((-100, 100, -150, 15), 3)
    cv.blit(im, 750, 1500, 0, 0, angle=30)
    assert cv.im.getpixel((300, 300))[:3] == (255, 0, 0)
