"""Erros que o MUGEN recusa ao carregar e o validador tem de pegar antes."""
from charfactory import project, validate
from charfactory.tools import ops


def test_animelem_in_param_and_loose_redirect(mugen_dir):
    c = project.load(mugen_dir / "chars" / "kfm" / "kfm.def", mugen_dir)
    ops.set_statedef_text(c, 9960, """[Statedef 9960]
type = S
anim = 0

[State 9960, rastro]
type = Explod
trigger1 = (animelem = 3) || (animelem = 6)
anim = 0
angle = ifelse(animelem = 6, 20, -10)

[State 9960, raio]
type = VarSet
trigger1 = 1
fv = 1
value = ifelse(root, stateno = 1101, 0, 26)

[State 9960, certo]
type = VarSet
trigger1 = 1
fv = 2
value = ifelse((root, stateno) = 1101, animelemno(0), 26)
""")
    msgs = [str(i) for i in validate.validate_character(c) if "9960" in str(i)]
    assert any("animelem only works in triggers" in m and "angle" in m for m in msgs)
    assert any("loose redirection" in m and "raio" in m for m in msgs)
    assert not any("certo" in m for m in msgs)
    assert not any("rastro" in m and "trigger1" in m for m in msgs)     # em trigger vale
