import pytest
from conftest import MUGEN
from charfactory import project, validate
from charfactory.tools import clsnkit

KFM_PATH = MUGEN / "chars" / "kfm" / "kfm.def"


@pytest.fixture
def kfm():
    if not KFM_PATH.exists():
        pytest.skip("KFM não encontrado no caminho do MUGEN")
    return project.load(KFM_PATH)


def test_anatomical_clsn2_kfm(kfm):
    boxes = clsnkit.anatomical_clsn2(kfm, 0, 0, zones=3)
    assert len(boxes) == 3
    head, torso, legs = boxes
    # Cabeça fica no topo (y menor/mais negativo)
    assert head[1] < torso[1] < legs[1]
    # Pernas chegam perto do chão (y >= -1)
    assert legs[3] >= -2


def test_attack_clsn1_kfm(kfm):
    # Stand weak punch: frame (200, 1) tem punho estendido para frente
    hit = clsnkit.attack_clsn1(kfm, 200, 1)
    assert len(hit) == 1
    box = hit[0]
    # Caixa à frente do eixo X
    assert box[0] > 0
    assert box[2] > box[0]


def test_interpolate_boxes():
    a = [(0, -100, 20, -50)]
    b = [(20, -80, 60, -30)]
    mid = clsnkit.interpolate_boxes(a, b, 0.5)
    assert mid == [(10, -90, 40, -40)]


def test_infinite_loop_detector(kfm):
    st = kfm.states[0]
    st.set_statedef_text(4000, "[Statedef 4000]\ntype = S\n[State 4000, 1]\ntype = ChangeState\ntrigger1 = 1\nvalue = 4001")
    st.set_statedef_text(4001, "[Statedef 4001]\ntype = S\n[State 4001, 1]\ntype = ChangeState\ntrigger1 = 1\nvalue = 4000")
    issues = validate.validate_character(kfm)
    loop_errors = [i for i in issues if "infinite loop" in i.message]
    assert len(loop_errors) >= 1
    assert any("4000 -> 4001 -> 4000" in i.message for i in loop_errors)
