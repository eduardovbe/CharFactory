"""Golpes a partir de TOML e importação de grupos de outro char (sobre uma cópia do kfm oficial)."""
import shutil

import pytest

from charfactory import project, validate
from charfactory.tools import charkit, movespec

SPEC = '''
[defaults]
hitsound = "S5,0"
guardsound = "S6,0"
sparkno = 2

[[move]]
state = 1500
name = "Teste"
kind = "special"
command = "~D, DF, F, y"
power = 500
frames = [[200,0,3],[200,1,3],[200,2,4,"hit"],[200,1,6]]
[[move.hit]]
elem = 3
damage = [40, 6]
[[move.vfx]]
elem = 3
anim = 1501
pos = [30, -40]
'''


@pytest.fixture
def kfm(mugen_dir, tmp_path):
    dst = tmp_path / "kfm"
    shutil.copytree(mugen_dir / "chars" / "kfm", dst)
    shutil.copy2(mugen_dir / "data" / "common1.cns", dst / "common1.cns")
    return project.load(dst / "kfm.def", mugen_dir)


def _statedef_text(char, number: int) -> str | None:
    sd = char.all_statedefs().get(number)
    return sd[0].text() if sd else None


def test_spec_builds_move_and_is_idempotent(kfm, tmp_path):
    spec = tmp_path / "moves.toml"
    spec.write_text(SPEC, "utf-8")
    charkit.import_groups(kfm, kfm.def_path, [200], offset=1301)     # anim 1501 de efeito
    movespec.build_from_spec(kfm, spec)
    movespec.build_from_spec(kfm, spec)
    a = kfm.anims.get(1500)
    assert len(a.frames) == 4 and a.frames[2].clsn1 and not a.frames[0].clsn1
    text = _statedef_text(kfm, 1500)
    assert "type = HitDef" in text and "poweradd = -500" in text and "anim = 1501" in text
    m1 = _statedef_text(kfm, -1)
    assert m1.count("spec: Teste") == 1
    assert kfm.commands.find("spec_1500")
    kfm.save(backup=False)
    again = project.load(kfm.def_path)
    rep = validate.report(validate.validate_character(again))
    assert rep["errors"] == 0, rep["issues"][:5]
