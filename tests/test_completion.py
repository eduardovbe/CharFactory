"""Pendências fechadas: escala/ângulo/interpolação 1.1, offset viewer e personagens compactados."""
import shutil
import subprocess
import zipfile

import pytest
from PIL import Image

from charfactory import archive, project
from charfactory.formats import air
from charfactory.project import ProjectError
from charfactory.tools import edit, render


@pytest.fixture()
def kfm(mugen_dir, tmp_path):
    work = tmp_path / "chars" / "kfm"
    shutil.copytree(mugen_dir / "chars" / "kfm", work)
    return project.load(work / "kfm.def")


# ---------------------------------------------------------------- escala, ângulo, interpolação

def test_transform_about_axis_keeps_axis():
    img = Image.new("RGBA", (10, 20), (255, 0, 0, 255))
    out, ax, ay = render.transform_about_axis(img, 5, 20, 2, 2, 0)
    assert out.size == (20, 40) and (ax, ay) == (10, 40)
    # 90° anti-horário: o eixo nos pés (embaixo, centro) vai para a direita, no meio da altura
    out, ax, ay = render.transform_about_axis(img, 5, 20, 1, 1, 90)
    assert out.size == (20, 10)
    assert (round(ax), round(ay)) == (20, 5)
    # escala negativa espelha em volta do eixo
    out, ax, ay = render.transform_about_axis(img, 2, 0, -1, 1, 0)
    assert ax == 8


def test_frame_scale_angle_render(kfm):
    edit.set_frame(kfm, 0, 0, xscale=2, yscale=2)
    f = kfm.anims.get(0).frames[0]
    assert f.extra == ["2", "2", "0"] and "anims" in kfm.dirty
    big, x2, y2 = render.frame_layer(kfm, f)
    spr = kfm.sprites.find(f.group, f.item)
    assert big.size == (spr.width * 2, spr.height * 2)
    assert (x2, y2) == (f.x - spr.axis_x * 2, f.y - spr.axis_y * 2)
    edit.set_frame(kfm, 0, 0, xscale=1, yscale=1, angle=0)
    assert f.extra == []  # valores padrão não são gravados
    edit.set_all_frames(kfm, 0, angle=45)
    assert all(fr.extra[2] == "45" for fr in kfm.anims.get(0).frames)


def test_interpolation(kfm):
    a = air.Action(9001, [air.Frame(0, 0, 0, 0, 10), air.Frame(0, 0, 20, -10, 10, extra=["2", "1", "90"])])
    a.frames[1].interpolate = ["offset", "scale", "angle"]
    mid = render.interpolated_frame(a, 0, 5)
    assert (mid.x, mid.y) == (10, -5)
    assert render.frame_scale_angle(mid) == (1.5, 1.0, 45.0)
    assert render.interpolated_frame(a, 0, 0).x == 0
    assert render.interpolated_frame(a, 1, 5) is a.frames[1]  # o último volta ao loopstart, sem interpolar
    kfm.anims.actions[9001] = a
    gif = render.render_gif(kfm, 9001, loops=1)
    frames = Image.open(__import__("io").BytesIO(gif)).n_frames
    assert frames > 2  # o trecho interpolado vira várias imagens
    with pytest.raises(ValueError):
        edit.set_frame(kfm, 9001, 1, interpolate=["giro"])


def test_interpolate_roundtrip_text(kfm):
    edit.set_frame(kfm, 0, 1, interpolate=["offset", "angle"])
    kfm.save(backup=False)
    again = project.load(kfm.def_path)
    assert again.anims.get(0).frames[1].interpolate == ["offset", "angle"]


# ---------------------------------------------------------------- offset viewer

def test_offset_viewer(kfm):
    img = render.render_offset(kfm, 0, 0, 40, -30)
    assert img.size == render.localcoord(kfm)
    ox, oy = render.offset_layout(img.size, 40, -30)
    px = img.load()
    assert px[ox, oy][:3] == render.AXIS1_COLOR
    assert px[ox + 40, oy - 30][:3] == render.AXIS2_COLOR
    flipped = render.render_offset(kfm, 0, 0, 40, -30, flip_h=True, resolution=(640, 480), scale=2)
    assert flipped.size == (1280, 960)
    with pytest.raises(ValueError):
        render.render_offset(kfm, 0, 987654, 0, 0)


# ---------------------------------------------------------------- compactados

def test_pack_and_extract_zip(kfm, tmp_path):
    (kfm.dir / "kfm.def.bak").write_text("x")
    r = archive.pack(kfm.def_path, tmp_path / "kfm.zip")
    names = archive.list_entries(r["zip"])
    assert "kfm/kfm.def" in names and not any(n.endswith(".bak") for n in names)
    out = archive.extract(r["zip"], tmp_path / "chars" / "kfm2")
    assert [p.split("\\")[-1] for p in out["defs"]] == ["kfm.def"]  # intro/ending não são personagem
    c = project.load(out["defs"][0])
    assert c.name == "Kung Fu Man" and len(c.sprites.sprites) == 281
    with pytest.raises(ProjectError, match="is not empty"):
        archive.extract(r["zip"], tmp_path / "chars" / "kfm2")
    archive.extract(r["zip"], tmp_path / "chars" / "kfm2", overwrite=True)


def test_extract_default_destination_is_chars(kfm, tmp_path):
    z = tmp_path / "downloads" / "lutador.zip"
    z.parent.mkdir()
    archive.pack(kfm.dir, z)
    out = archive.extract(z, mugen_root=tmp_path)
    assert out["folder"] == str(tmp_path / "chars" / "lutador")


def test_extract_rejects_unsafe_paths(tmp_path):
    z = tmp_path / "mal.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../fora.txt", "x")
    with pytest.raises(ProjectError, match="unsafe"):
        archive.extract(z, tmp_path / "dest")
    assert not (tmp_path / "fora.txt").exists()


def test_extract_7z(kfm, tmp_path):
    sz = archive._seven_zip()
    if not sz:
        pytest.skip("7-Zip não instalado")
    a = tmp_path / "kfm.7z"
    subprocess.run([sz, "a", str(a), str(kfm.dir)], capture_output=True, check=True)
    out = archive.extract(a, tmp_path / "x")
    assert project.load(out["defs"][0]).name == "Kung Fu Man"
