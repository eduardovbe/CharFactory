"""Controladores iguais em estados diferentes: editar um estado não pode mexer no outro."""
from charfactory.formats import cns

TXT = """[Statedef 200]
type = S

[State 200, fim]
type = ChangeState
trigger1 = !animtime
value = 0

[Statedef 210]
type = S

[State 210, golpe]
type = VelSet
trigger1 = 1
x = 2

[State 200, fim]
type = ChangeState
trigger1 = !animtime
value = 0
"""


def test_set_statedef_text_keeps_identical_controllers_in_other_states(tmp_path):
    p = tmp_path / "t.cns"
    p.write_text(TXT, "latin-1")
    st = cns.load(p)
    old = st.statedefs[210].text()
    st.set_statedef_text(210, old.rstrip() + "\n\n[State 210, extra]\ntype = PlaySnd\ntrigger1 = 1\nvalue = 5,0\n")
    assert [c.type for c in st.statedefs[200].controllers] == ["ChangeState"]
    assert [c.type for c in st.statedefs[210].controllers] == ["VelSet", "ChangeState", "PlaySnd"]
    st.add_controller(210, "[State 210, mais]\ntype = Null\ntrigger1 = 1\n")
    assert [c.type for c in st.statedefs[200].controllers] == ["ChangeState"]
    assert st.statedefs[210].controllers[-1].type == "Null"
