import shutil

import pytest

from charfactory import project, validate
from charfactory.formats import cns
from charfactory.tools import generate, ops, render


@pytest.fixture()
def kfm(mugen_dir, tmp_path):
    work = tmp_path / "chars" / "kfm"
    shutil.copytree(mugen_dir / "chars" / "kfm", work)
    (tmp_path / "data").mkdir()
    shutil.copy(mugen_dir / "data" / "common1.cns", tmp_path / "data")
    return project.load(work / "kfm.def")


def test_palette_variant_changes_colors_but_keeps_index0(kfm):
    ops.make_palette_variant(kfm, 9, hue_shift=180, keep_indices=[5])
    base = kfm.sprites.palettes[kfm.sprites.find_palette(1, 1)].colors
    new = kfm.sprites.palettes[kfm.sprites.find_palette(1, 9)].colors
    assert new[0] == base[0] and new[5] == base[5] and new != base
    assert render.sprite_image(kfm, 0, 0, palette=9).size == render.sprite_image(kfm, 0, 0).size


def test_align_bottom(kfm):
    ops.align_sprites(kfm, "bottom", group=0)
    s = kfm.sprites.find(0, 0)
    assert (s.axis_x, s.axis_y) == (round(s.width / 2), s.height)


def test_var_usage_finds_kfm_vars(kfm):
    u = ops.var_usage(kfm)
    assert "var(1)" in u["used"] and u["used"]["var(1)"]["write"]
    assert 0 in u["free_var"] and 1 not in u["free_var"]


def test_export_import_sprite_roundtrip(kfm, tmp_path):
    ops.export_sprites(kfm, tmp_path / "out", group=0)
    before = kfm.sprites.pixels(kfm.sprites.find(0, 0))
    ops.import_sprites_csv(kfm, tmp_path / "out" / "sprites.csv", autocrop=False)
    after = kfm.sprites.pixels(kfm.sprites.find(0, 0))
    assert before == after
    kfm.save()
    again = project.load(kfm.def_path)
    assert again.sprites.pixels(again.sprites.find(0, 0)) == before


def test_throw_generator_validates(kfm):
    g = generate.throw_states(5800, 810, 820, [{"elem": 1, "x": 25, "y": 0},
                                               {"elem": 3, "x": 20, "y": -40, "sprpriority": 1}])
    for n, sd in cns.loads(g["states"]).statedefs.items():
        ops.set_statedef_text(kfm, n, sd.text())
    ops.add_controller(kfm, -1, g["cmd_trigger"])
    kfm.save()
    issues = validate.validate_character(project.load(kfm.def_path))
    assert not [i for i in issues if i.severity == "error"], issues


def test_controller_template_is_valid_after_filling():
    tpl = generate.controller_template("ChangeState", 300).replace("value = ;", "value = 0 ;")
    issues = validate.validate_states(cns.loads("[Statedef 300]\n\n" + tpl), "t")
    assert not [i for i in issues if i.severity == "error"], issues


def test_movelist_html(kfm, tmp_path):
    html = generate.movelist_html(kfm, tmp_path / "m.html")
    assert "Kung Fu Man" in html and "↓" in html and len(generate.movelist(kfm)) > 20


def test_export_aligned_common_canvas(kfm, tmp_path):
    r = ops.export_sprites_aligned(kfm, tmp_path / "al", group=200)
    from PIL import Image
    sizes = {Image.open(p).size for p in (tmp_path / "al").glob("*.png")}
    assert sizes == {tuple(r["canvas"])}
    assert (tmp_path / "al" / "axis.txt").read_bytes() == f"X axis: {r['axis'][0]}\r\nY axis: {r['axis'][1]}".encode()


def test_var_list_comment(kfm):
    txt = ops.var_list_comment(kfm)
    assert "Integer variable list" in txt and "; var(1):= -1" in txt


def test_palfx_preset_and_preview(kfm, mugen_dir):
    from charfactory.tools import render
    tpl = generate.palfx_controller(10, 5, add=(10, 0, 0), sinadd=(5, 5, 5, 8), color=128)
    assert "sinadd = 5,5,5,8" in tpl and "color = 128" in tpl
    issues = validate.validate_states(cns.loads("[Statedef 10]\n\n" + tpl), "t")
    assert not [i for i in issues if i.severity == "error"]
    img = render.sprite_image(kfm, 0, 0)
    red = render.apply_palfx(img, add=(255, 0, 0))
    assert red.getpixel((img.width // 2, img.height // 2))[0] >= img.getpixel((img.width // 2, img.height // 2))[0]
