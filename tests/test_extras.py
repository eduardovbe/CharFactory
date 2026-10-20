"""Ferramentas extras do editor e regressões de bugs corrigidos."""
import copy
import shutil
import wave
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from charfactory import project, validate
from charfactory.formats import air, cns, mtext
from charfactory.tools import cmdtest, edit, ops, render, stage, transplant


@pytest.fixture()
def kfm(mugen_dir, tmp_path):
    work = tmp_path / "chars" / "kfm"
    shutil.copytree(mugen_dir / "chars" / "kfm", work)
    return project.load(work / "kfm.def", mugen_dir)


# ------------------------------------------------------------------ AIR: comentários

AIR_TEXT = """; Parado
[Begin Action 0]
Clsn2Default: 1
  Clsn2[0] = -10, -80, 10, 0
0,0, 0,0, 10   ; respira
; meio da pose
0,1, 0,0, 10

; Andar para frente
[Begin Action 20]
20,0, 0,0, 5
"""


def test_editing_an_action_keeps_comments_and_next_name():
    a = air.loads(AIR_TEXT)
    a.get(0).frames[0].time = 12
    text = a.to_text()
    assert "; Andar para frente" in text          # nome da próxima ação (era apagado)
    assert "; meio da pose" in text and "; respira" in text
    assert "  Clsn2[0]" in text                    # recuo original das caixas
    again = air.loads(text)
    assert again.get(0).frames[0].time == 12 and again.name_of(20) == "Andar para frente"


def test_action_name_after_editing_the_action_above():
    # name_of só põe em dia a seção de cima (não o arquivo todo): editar a ação 0 sem gerar o
    # texto não pode esconder o nome da 20, que mora no fim da seção dela
    a = air.loads(AIR_TEXT)
    a.get(0).frames[0].time = 12
    a.get(0).frames.append(copy.deepcopy(a.get(0).frames[0]))
    assert a.name_of(20) == "Andar para frente" and a.name_of(0) == "Parado"
    assert air.loads(a.to_text()).name_of(20) == "Andar para frente"


def test_action_name_and_renumber_in_place():
    a = air.loads(AIR_TEXT)
    a.set_name(20, "Andar")
    a.renumber(20, 21)
    text = a.to_text()
    assert "[Begin Action 21]" in text and "; Andar\n[Begin Action 21]" in text
    assert text.index("Begin Action 0") < text.index("Begin Action 21")   # posição mantida
    assert air.loads(text).name_of(0) == "Parado"


# ------------------------------------------------------------------ importar imagem

class _NoPal:
    class sprites:
        palettes = []

        @staticmethod
        def find_palette(g, i):
            return -1


def test_to_indexed_without_char_palette_keeps_colors():
    im = Image.new("RGBA", (4, 1))
    im.putdata([(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (0, 0, 0, 0)])
    out = ops._to_indexed(im, _NoPal())
    rgb = out.convert("RGB")
    assert [rgb.getpixel((i, 0)) for i in range(3)] == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    assert out.getpixel((3, 0)) == 0                 # transparente vira o índice 0


def test_black_pixel_never_becomes_transparent(kfm):
    pal = kfm.sprites.palettes[kfm.sprites.find_palette(1, 1)].colors
    im = Image.new("RGBA", (2, 1))
    im.putdata([(*pal[0], 255), (0, 0, 0, 0)])        # a cor do índice 0, mas opaca
    out = ops._to_indexed(im, kfm)
    assert out.getpixel((0, 0)) != 0 and out.getpixel((1, 0)) == 0


# ------------------------------------------------------------------ sprites e paletas

def test_crop_resize_rotate_keep_axis_point(kfm):
    s = kfm.sprites.find(0, 0)
    w, h = s.width, s.height
    edit.crop_sprite(kfm, 0, 0, 2, 3, w - 4, h - 6)
    s = kfm.sprites.find(0, 0)
    assert (s.width, s.height) == (w - 4, h - 6)
    edit.resize_sprite(kfm, 0, 0, s.width * 2, s.height * 2)
    assert kfm.sprites.find(0, 0).width == (w - 4) * 2
    edit.rotate_sprite(kfm, 0, 0, 90)
    s = kfm.sprites.find(0, 0)
    assert abs(s.width - (h - 6) * 2) <= 2


def test_renumber_sprite_updates_frames(kfm):
    n = sum(1 for a in kfm.anims.actions.values() for f in a.frames if (f.group, f.item) == (0, 0))
    r = edit.renumber_sprite(kfm, 0, 0, 7777, 1)
    assert r["frames_updated"] == n > 0
    assert kfm.sprites.find(7777, 1) and not kfm.sprites.find(0, 0)
    with pytest.raises(ValueError):
        edit.renumber_sprite(kfm, 7777, 1, 0, 1)        # 0,1 já existe


def test_palette_tools(kfm):
    before = render.sprite_image(kfm, 0, 0).tobytes()
    used = edit.used_palette_indices(kfm, 1)
    r = edit.clear_unused_colors(kfm, 1, "#123456")
    assert render.sprite_image(kfm, 0, 0).tobytes() == before
    assert r["cleared"] >= 0 and all(i in used for i in used)
    edit.set_palette_size(kfm, 1, 64)
    assert len(kfm.sprites.palettes[kfm.sprites.find_palette(1, 1)].colors) == 64
    edit.set_palette_size(kfm, 1, 256)
    a = sorted(used)[1]
    edit.remap_palette_index(kfm, 1, a, 0)
    assert a not in edit.used_palette_indices(kfm, 1)


def test_delete_palette_reassigns_sprites(kfm):
    ops.make_palette_variant(kfm, 9, 90)
    pi = kfm.sprites.find_palette(1, 9)
    edit.set_sprites_palette(kfm, [[0, 0]], 9)
    assert kfm.sprites.find(0, 0).palette_index == pi
    edit.delete_palette(kfm, 9)
    assert kfm.sprites.find_palette(1, 9) < 0
    assert kfm.sprites.find(0, 0).palette_index == kfm.sprites.find_palette(1, 1)
    with pytest.raises(ValueError):
        edit.delete_palette(kfm, 1)


# ------------------------------------------------------------------ sons

def _wav(n=1000, rate=22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.sin(np.arange(n) / 5) * 20000).astype(np.int16).tobytes())
    return buf.getvalue()


def test_sound_range_ops(kfm):
    from charfactory.formats import snd
    snd.set_sound(kfm.sounds, 900, 0, _wav())
    edit.edit_sound_range(kfm, 900, 0, 100, 200, "silence")
    _, d = edit._wav_arrays(kfm.sounds.find(900, 0).wav)
    assert not d[100:200].any() and d[300:400].any()
    edit.edit_sound_range(kfm, 900, 0, 0, 500, "remove")
    assert len(edit._wav_arrays(kfm.sounds.find(900, 0).wav)[1]) == 500
    clip = edit.samples_wav(kfm, 900, 0, 0, 100)
    edit.insert_samples(kfm, 900, 0, 50, clip)
    assert len(edit._wav_arrays(kfm.sounds.find(900, 0).wav)[1]) == 600
    edit.renumber_sound_group(kfm, 900, 901)
    assert kfm.sounds.find(901, 0) and edit.delete_sound_group(kfm, 901)["deleted"] == 1


# ------------------------------------------------------------------ validação

def test_deprecated_lines_and_warning():
    text = "[Statedef 200]\n\n[State 200, a]\ntype = Explod\nanim = 1\nontop = 1\n\n[State 200, b]\ntype = MakeDust\n"
    assert validate.deprecated_lines(text) == [6, 9]
    st = cns.States(mtext.parse(text))
    msgs = [i.message for i in validate.validate_states(st, "x.cns")]
    assert any("deprecated" in m for m in msgs)


def test_reorder_statedef_minus_one():
    text = "[Statedef -1]\n\n[State -1, A]\ntype = Null\n\n[State -1, B]\ntype = Null\n\n[State -1, C]\ntype = Null\n"
    st = cns.States(mtext.parse(text))
    st.reorder_controllers(-1, [2, 0, 1])
    assert [c.label for c in st.statedefs[-1].controllers] == ["C", "A", "B"]


# ------------------------------------------------------------------ testador de comandos

@pytest.mark.parametrize("inp,seq,ok", [
    ("2 3 6 a", "~D, DF, F, a", True),
    ("2 3 6 a", "~F, D, DF, a", False),
    ("6 2 3 x+y", "~F, D, DF, x+y", True),
    ("6 5 6", "F, F", True),
    ("6", "F, F", False),
    ("4*40 6 a", "~30$B, F, a", True),
    ("4*10 6 a", "~30$B, F, a", False),
    ("a*30 ~a", "~30a", True),
])
def test_command_matching(inp, seq, ok):
    assert (cmdtest.matches(cmdtest.parse_input(inp), seq, 15) is not None) == ok


def test_command_tester_on_kfm(kfm):
    r = cmdtest.test(kfm, "2 3 6 x")
    names = {a["name"] for a in r["active"]}
    assert any("QCF_x" in n or "qcf_x" in n.lower() for n in names)


# ------------------------------------------------------------------ transplante e projeto

def test_transplant_move_between_chars(kfm, tmp_path, mugen_dir):
    from charfactory.tools import generate
    dst_def = generate.new_character(tmp_path / "chars" / "Novo", "Novo")
    dst = project.load(dst_def if isinstance(dst_def, (str, Path)) else dst_def["def"], mugen_dir)
    info = transplant.analyze(kfm, 1000)
    assert 1000 in info["states"] and info["anims"]
    r = transplant.transplant(dst, kfm.def_path, 1000, 2000, 2000, 0, 0)
    assert 3000 in dst.all_statedefs(include_common=False)
    for a in r["anims"]:
        assert dst.anims.get(a) is not None
    text = dst.states_file_for(3000).doc.to_text()
    assert "[Statedef 3000]" in text and "Statedef 1000]" not in text
    dst.save(backup=False)
    again = project.load(dst.def_path, mugen_dir)
    errs = [i for i in validate.validate_character(again) if i.severity == "error"]
    assert not errs, errs


def test_save_to_writes_copy_without_touching_project(kfm, tmp_path):
    original = kfm.path_of("anim").read_bytes()
    kfm.anims.get(0).frames[0].time += 1
    kfm.mark("anims")
    out = kfm.save_to(tmp_path / "copia")
    assert any(p.endswith("kfm.air") for p in out)
    assert kfm.path_of("anim").read_bytes() == original and "anims" in kfm.dirty


# ------------------------------------------------------------------ stage

def test_stage_render_and_detect(mugen_dir):
    defs = sorted((mugen_dir / "stages").glob("*.def"))
    if not defs:
        pytest.skip("sem stages")
    st = stage.Stage(defs[0], mugen_dir)
    im = st.render(0, 0, 10)
    assert im.size == st.localcoord and st.layers
    assert stage.detect_kind(defs[0]) == "stage"
    assert stage.detect_kind(mugen_dir / "chars" / "kfm" / "kfm.def") == "character"
    if (mugen_dir / "data" / "fight.def").exists():
        assert stage.detect_kind(mugen_dir / "data" / "fight.def") == "fight"


# ------------------------------------------------------------------ desfazer/refazer

def test_undo_stack_restores_parts(kfm):
    pytest.importorskip("PySide6")
    from charfactory.gui.context import UndoStack
    h = UndoStack(kfm)
    for part in ("sprites", "anims", "sounds"):
        h.prime(part)
    t0 = kfm.anims.get(0).frames[0].time
    kfm.anims.get(0).frames[0].time = t0 + 9
    h.record("anims")
    ax0 = kfm.sprites.find(0, 0).axis_x
    kfm.sprites.find(0, 0).axis_x = ax0 + 4
    h.record("sprites")
    assert h.do_undo() == "sprites" and kfm.sprites.find(0, 0).axis_x == ax0
    assert h.do_undo() == "anims" and kfm.anims.get(0).frames[0].time == t0
    assert h.do_redo() == "anims" and kfm.anims.get(0).frames[0].time == t0 + 9
    assert h.do_redo() == "sprites" and kfm.sprites.find(0, 0).axis_x == ax0 + 4


def test_blend_lut_matches_float_blend():
    # o canvas usa a tabela para sprites de alfa 0/255: tem de dar o mesmo byte que a conta em float
    rng = np.random.default_rng(3)
    dst = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    src = rng.integers(0, 256, (64, 64, 4), dtype=np.uint8)
    src[..., 3] = np.where(src[..., 3] > 127, 255, 0)
    for trans in ("A", "A1", "S", "AS128D128", "AS64D192", "AS256D256", "AS0D256"):
        out = render.blend_layer(dst.astype(np.float32) / 255, src.astype(np.float32) / 255, trans)
        want = (out * 255 + 0.5).astype(np.uint8)
        got = np.empty_like(dst)
        for c in range(3):
            idx = (src[..., c].astype(np.uint16) << 8) | dst[..., c]
            on = render.blend_lut(trans, 255).ravel().take(idx)
            off = render.blend_lut(trans, 0).ravel().take(idx)
            got[..., c] = np.where(src[..., 3] == 255, on, off)
        assert np.array_equal(got, want), trans


def test_palettes12_bands_schemes_and_locked_bands(mugen_dir, tmp_path):
    """12 paletas: as faixas agrupam as cores por família; o esquema gera 2 a 12 e não mexe
    nas travadas; gravar cria as paletas 1,n."""
    import shutil
    from charfactory import project
    from charfactory.tools import palettes12 as P
    shutil.copytree(mugen_dir / "chars" / "kfm", tmp_path / "kfm")
    c = project.load(tmp_path / "kfm" / "kfm.def", mugen_dir)
    base = [tuple(x) for x in c.sprites.palettes[c.sprites.find_palette(1, 1)].colors]
    rngs = P.ranges(base, P.used_indices(c))
    assert rngs and sum(len(r["indices"]) for r in rngs) == len({i for r in rngs for i in r["indices"]})
    skin = next(j for j, r in enumerate(rngs) if r["name"] == P.SKIN)
    assert rngs[skin]["locked"]
    for name in P.SCHEMES:
        adj = P.scheme(name, rngs)
        assert sorted(adj) == list(range(2, 13)) and all(skin not in a for a in adj.values())
    adj = P.scheme("arco", rngs)
    pals = {n: P.adjust(base, rngs, adj[n]) for n in adj}
    free = next(j for j, r in enumerate(rngs) if not r["locked"])
    i = rngs[free]["indices"][0]
    assert len({pals[n][i] for n in pals}) > 6                 # a faixa livre muda de cor
    assert all(pals[n][j] == base[j] for n in pals for j in rngs[skin]["indices"])   # a pele não
    P.write(c, pals)
    assert all(c.sprites.find_palette(1, n) >= 0 for n in range(2, 13))
    img = P.preview_image(c, 0, 0, pals[5])
    assert img is not None and img.size == P.preview_image(c, 0, 0, base).size
