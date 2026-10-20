"""Funções de produtividade: ir para a definição, paleta de comandos, recursos não usados."""
import os
import shutil

import pytest

from charfactory import project
from charfactory.tools import usage

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from charfactory.gui.dialogs import fuzzy_score  # noqa: E402
from charfactory.gui.editors.code import reference_at, reference_doc  # noqa: E402

CNS = """[Statedef 200, soco]
type = S
anim = 200

[State 200, som]
type = PlaySnd
trigger1 = Time = 2
value = 5, 0

[State 200, fim]
type = ChangeState
trigger1 = AnimTime = 0
value = 0

[State 200, golpe]
type = HitDef
trigger1 = 1
hitsound = S1, 2
guardsound = 6, 0
p2stateno = 820

[State 200, calc]
type = ChangeAnim
trigger1 = 1
value = 200 + var(1)
""".split("\n")


def _line(text):
    return next(i for i, ln in enumerate(CNS) if ln.startswith(text))


def test_reference_at():
    assert reference_at(CNS, 0) == ("state", 200, 0)
    assert reference_at(CNS, _line("anim =")) == ("anim", 200, 0)
    assert reference_at(CNS, _line("value = 5, 0")) == ("sound", 5, 0)
    assert reference_at(CNS, _line("value = 0")) == ("state", 0, 0)
    assert reference_at(CNS, _line("hitsound")) == ("sound", 1, 2)
    assert reference_at(CNS, _line("guardsound")) is None  # sem S: som comum do fightfx
    assert reference_at(CNS, _line("p2stateno")) == ("state", 820, 0)
    assert reference_at(CNS, _line("value = 200 + var")) is None  # expressão: não dá para resolver
    assert reference_at(CNS, _line("trigger1 = Time")) is None


def test_reference_doc():
    assert "HitDef" in reference_doc("hitdef") and "controller" in reference_doc("HitDef")
    assert "trigger" in reference_doc("AnimTime")
    assert reference_doc("banana") is None


def test_fuzzy_score():
    assert fuzzy_score("", "qualquer") == 0
    assert fuzzy_score("anim 20", "anim 20 · Walking") > fuzzy_score("anim 20", "anim 200 · Punch")
    assert fuzzy_score("slv", "Projeto › Salvar") is not None
    assert fuzzy_score("xyz", "Projeto › Salvar") is None


@pytest.fixture()
def kfm(mugen_dir, tmp_path):
    work = tmp_path / "chars" / "kfm"
    shutil.copytree(mugen_dir / "chars" / "kfm", work)
    (tmp_path / "data").mkdir()
    shutil.copy(mugen_dir / "data" / "common1.cns", tmp_path / "data")
    return project.load(work / "kfm.def")


def test_unused_resources(kfm):
    r = usage.unused_resources(kfm)
    assert all(g != usage.PORTRAIT_GROUP for g, _ in r["sprites"])
    used = {(f.group, f.item) for a in kfm.anims.actions.values() for f in a.frames}
    assert not any(tuple(s) in used for s in r["sprites"])
    assert r["dynamic_references"] > 0  # o common1 calcula animações de tomar golpe
    # uma animação nova sem nenhuma referência aparece; referenciada por ChangeAnim some
    from charfactory.tools import ops
    ops.create_action(kfm, 7777, [{"group": 0, "item": 0, "time": 5}])
    assert 7777 in usage.unused_resources(kfm)["animations"]
    ops.add_controller(kfm, 200, "[State 200, t]\ntype = ChangeAnim\ntrigger1 = 0\nvalue = 7777\n")
    assert 7777 not in usage.unused_resources(kfm)["animations"]
