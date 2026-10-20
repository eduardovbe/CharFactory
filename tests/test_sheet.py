import csv

import numpy as np
import pytest
from PIL import Image

from charfactory import project
from charfactory.tools import render, sheet

FRAMES = [(0, i) for i in range(4)] + [(200, i) for i in range(3)] + [(20, i) for i in range(5)]


@pytest.fixture(scope="module")
def kfm(mugen_dir):
    return project.load(mugen_dir / "chars/kfm/kfm.def")


def _rows(kfm):
    return [FRAMES[:4], FRAMES[4:7], FRAMES[7:]]


def _make_auto_sheet(kfm, bg):
    """Fileiras com os pés no mesmo chão; fundo sólido; espaço entre frames."""
    imgs = {f: render.sprite_image(kfm, *f) for f in FRAMES}
    cell_w = max(i.width for i in imgs.values()) + 30
    rows = _rows(kfm)
    sheet_img = Image.new("RGBA", (cell_w * 5, 150 * len(rows)), bg)
    truth = {}
    for r, row in enumerate(rows):
        ground = 150 * r + 130
        for c, f in enumerate(row):
            spr = kfm.sprites.find(*f)
            x = cell_w * c + 20
            y = ground - spr.axis_y
            sheet_img.alpha_composite(imgs[f], (x, y))
            truth[(r, c)] = (f, x, y)
    return sheet_img, truth


def _load_csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_auto_slicing_exact_pixels_and_ground(kfm, tmp_path):
    img, truth = _make_auto_sheet(kfm, (0, 128, 128, 255))
    img.convert("RGB").save(tmp_path / "sheet.png")  # sem alfa: fundo por cor
    r = sheet.slice_sheet(tmp_path / "sheet.png", tmp_path / "out", group=500)
    assert r["count"] == len(FRAMES) and r["rows"] == 3 and r["palette_exact"]
    assert r["background"] == "#008080"
    rows = _load_csv(tmp_path / "out" / "sprites.csv")
    for (ri, ci), row in zip(sorted(truth), rows):
        f, x, y = truth[(ri, ci)]
        orig = render.sprite_image(kfm, *f)
        cut = Image.open(tmp_path / "out" / row["file"]).convert("RGBA")
        # mesmos pixels do sprite original (após recorte justo)
        box = orig.getchannel("A").getbbox()
        a = np.asarray(orig.crop(box))
        b = np.asarray(cut)
        assert a.shape == b.shape
        assert ((a[..., 3] > 0) == (b[..., 3] > 0)).all()
        assert (a[a[..., 3] > 0][:, :3] == b[b[..., 3] > 0][:, :3]).all()
        # eixo Y = chão da fileira (última linha de pixels mais baixa da fileira)
        baseline = max(truth[(ri, k)][2] + render.sprite_image(kfm, *truth[(ri, k)][0]).getchannel("A").getbbox()[3] - 1
                       for k in range(len(_rows(kfm)[ri])))
        assert int(row["axis_y"]) == baseline - (y + box[1])
        if ri == 0:  # parado: os pés tocam o chão, então o eixo bate com o original
            assert int(row["axis_y"]) == kfm.sprites.find(*f).axis_y - box[1]


def test_grid_mode_recovers_original_axes(kfm, tmp_path):
    cw, ch = 140, 140
    img = Image.new("RGBA", (cw * 4, ch * 3), (0, 0, 0, 0))
    placed = []
    for n, f in enumerate(FRAMES):
        col, row = n % 4, n // 4
        spr = kfm.sprites.find(*f)
        x = col * cw + cw // 2 - spr.axis_x
        y = row * ch + ch - 10 - spr.axis_y
        img.alpha_composite(render.sprite_image(kfm, *f), (x, y))
        placed.append(f)
    img.save(tmp_path / "grid.png")
    r = sheet.slice_sheet(tmp_path / "grid.png", tmp_path / "g", mode="grid", group=7,
                          cell_width=cw, cell_height=ch, axis_in_cell=(0.5, (ch - 10) / ch))
    assert r["count"] == len(FRAMES) and r["background"] == "transparency"
    for f, row in zip(placed, _load_csv(tmp_path / "g" / "sprites.csv")):
        spr = kfm.sprites.find(*f)
        box = render.sprite_image(kfm, *f).getchannel("A").getbbox()
        assert (int(row["axis_x"]), int(row["axis_y"])) == (spr.axis_x - box[0], spr.axis_y - box[1])


def test_detached_parts_merge_and_noise_dropped(tmp_path):
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    px = img.load()
    for x in range(20, 40):          # corpo
        for y in range(20, 80):
            px[x, y] = (200, 0, 0)
    for x in range(42, 46):          # pedaço solto a 2 px (deve juntar)
        for y in range(30, 34):
            px[x, y] = (0, 0, 200)
    px[150, 50] = (0, 0, 0)          # ruído de 1 px (deve sumir)
    for x in range(100, 130):        # segundo sprite
        for y in range(40, 80):
            px[x, y] = (0, 150, 0)
    img.save(tmp_path / "s.png")
    r = sheet.slice_sheet(tmp_path / "s.png", tmp_path / "o")
    assert r["count"] == 2
    assert r["sprites"][0]["box"] == [20, 20, 46, 80]


def test_import_into_character(kfm, tmp_path, mugen_dir):
    import shutil
    from charfactory.tools import ops
    work = tmp_path / "chars" / "kfm"
    shutil.copytree(mugen_dir / "chars" / "kfm", work)
    c = project.load(work / "kfm.def")
    img, _ = _make_auto_sheet(kfm, (0, 128, 128, 255))
    img.save(tmp_path / "sheet.png")
    sheet.slice_sheet(tmp_path / "sheet.png", tmp_path / "out", group=6000)
    res = ops.import_sprites_csv(c, tmp_path / "out" / "sprites.csv", autocrop=False)
    assert res["imported"] == len(FRAMES)
    assert c.sprites.find(6000, 0) is not None


