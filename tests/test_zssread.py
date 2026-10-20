"""Leitor de ZSS: árvore, rebaixamento para CNS, edição do texto e simulação de char em ZSS."""
import pytest

from charfactory.formats import cns, zssread
from charfactory.tools import simexpr


def _lower(text, funcs=None):
    w = []
    return cns.loads(zssread.to_cns(text, funcs, w)), w


def test_parse_tree_and_labels():
    f = zssread.parse("""# topo
[StateDef 200; type: S; anim: 200;]

# golpe
if animElem = 3 {
	hitDef{attr: S, NA; damage: 20}
} else if time > 9 {
	changeState{value: 0; ctrl: 1}
} else {
	ctrlSet{value: 0}
}
ignoreHitPause persistent(0) {
	varSet{v: 1; value: 3}
}
[Function F(a) r]
let r = $a * 2;
""")
    sd, fn = f.statedefs[0], f.functions[0]
    assert sd.number == 200 and sd.params == [("type", "S"), ("anim", "200")]
    top = sd.body[0]
    assert isinstance(top, zssread.If) and top.label == "golpe" and top.line == 5
    assert isinstance(top.orelse[0], zssread.If) and isinstance(top.orelse[0].orelse[0], zssread.Block)
    blk = sd.body[1]
    assert blk.ihp and blk.pers == 0
    assert fn.name == "F" and fn.args == ["a"] and fn.rets == ["r"]


def test_syntax_error_has_line():
    with pytest.raises(zssread.ZssError) as e:
        zssread.parse("[StateDef 0]\n\nif time = 1 {\n\tnull{}\n")
    assert "missing '}'" in str(e.value)


def test_block_condition_is_evaluated_once():
    # o varSet no corpo não pode cancelar o changeState seguinte (no Ikemen o if vale uma vez)
    st, _ = _lower("""[StateDef 200]
if var(1) = 0 {
	varSet{v: 1; value: 1}
	changeState{value: 300}
}
""")
    ctrls = st.statedefs[200].controllers
    assert ctrls[0].type == "VarSet" and ctrls[0].section.get("zsslocal(1)") == "var(1) = 0"
    assert all(c.triggers()["triggerall"] == ["zsslocal(1)"] for c in ctrls[1:])


def test_single_controller_if_is_inlined_and_attrs_go_down():
    st, _ = _lower("[StateDef 200]\nignoreHitPause persistent(3) if time = 2 {\n\tnull{}\n}\n")
    c = st.statedefs[200].controllers[0]
    assert c.triggers()["triggerall"] == ["time = 2"]
    assert c.section.get("persistent") == "3" and c.section.get("ignorehitpause") == "1"
    assert c.label == "@3"


def test_switch_let_call_and_assignment():
    st, w = _lower("""[Function Twice(x) r]
let r = $x * 2;
[StateDef 200]
let a = call Twice(var(2) + 1);
switch $a {
case 4; 6:
	changeAnim{value: $a}
default:
	var(3) := 9;
}
""")
    text = st.statedefs[200].text()
    assert "zsslocal(1) = var(2) + 1" in text                 # argumento vira local
    assert "Twice: @2" in text                                 # corpo expandido, linha da função
    assert "zsslocal(3) = zsslocal(2)" in text                 # retorno para `a`
    assert "zsslocal(4) = zsslocal(3) = 4 || zsslocal(3) = 6" in text
    assert "triggerall = (var(3) := 9) || 1" in text
    assert not w


def test_loops_warn_and_const_statedef_skipped():
    _, w = _lower("[StateDef 1]\nwhile time < 3 {\n\tnull{}\n}\n[StateDef const(X)]\nnull{}\n")
    assert any("loop while" in x for x in w) and any("const(X)" in x for x in w)


def test_simexpr_assignment():
    class Ctx:
        def __init__(self):
            self.v = {}

        def set_var(self, n, i, x):
            self.v[(n, i)] = x
            return x

        def trigger(self, n, a):
            return self.v.get((n, a), 0)
    c = Ctx()
    assert simexpr.evaluate("(var(1) := 2 + 3) || 0", c) == 1 and c.v[("var", 1)] == 5
    assert simexpr.evaluate("var(1) * 2", c) == 10


def test_check_flags_what_ikemen_refuses():
    f = zssread.parse("[StateDef -1]\npersistent(0) if 1 {\n\tnull{}\n}\n"
                      "[StateDef 0]\nposFreeze{value: 1; x: 1}\nbogusCtrl{a: 1}\nhitDef{damage: ; damage: 3}\n")
    msgs = [(ln, m) for _, ln, m in zssread.check(f)]
    assert (2, "persistent does not work in a negative state") in msgs
    assert any(ln == 6 and "'x' does not exist" in m for ln, m in msgs)
    assert any(ln == 7 and "bogusCtrl" in m for ln, m in msgs)
    assert any("'damage' without a value" in m for _, m in msgs) and any("repeated" in m for _, m in msgs)


ATTACK = """[Statedef 200]
type = S
movetype = A
physics = S
anim = 0
ctrl = 0
velset = 0, 0

[State 200, marca]
type = VarSet
trigger1 = time = 2
var(5) = 1

[State 200, anda]
type = VelSet
trigger1 = var(5) = 1 && time < 6
x = 2

[State 200, golpe]
type = HitDef
trigger1 = time = 3
attr = S, NA
damage = 30
animtype = Med
pausetime = 8, 8
ground.velocity = -4

[State 200, fim]
type = ChangeState
trigger1 = time >= 20
value = 0
ctrl = 1
"""


def test_check_catches_what_ikemen_accepts_silently_or_refuses():
    f = zssread.parse("[StateDef 200]\n"
                      "if animelemm = 3 {\n\tnull{}\n}\n"                  # gatilho escrito errado
                      "velSet{x: const(velocity.runback.x)}\n"            # o Ikemen aceita e vale 0
                      "velSet{x: const(velocity.run.back.x) + const(MinhaConst)}\n"
                      "hitDef{attr: S, XX; damage: 5}\n"
                      "hitDef{attr: SCA, AA; damage: 5}\n"
                      "if mugenVersion(major) = 1 && min(1, 2) {\n\tnull{}\n}\n")
    msgs = [(ln, m) for _, ln, m in zssread.check(f, user_consts={"minhaconst"})]
    assert any(ln == 2 and "animelemm" in m for ln, m in msgs)
    assert any(ln == 5 and "velocity.runback.x" in m for ln, m in msgs)
    assert not any(ln == 6 for ln, m in msgs)                   # constante do motor e a do char
    assert any(ln == 7 and "XX" in m for ln, m in msgs)
    assert not any(ln in (8, 9) for ln, m in msgs)              # AA vale; min() e major também


def test_check_old_style_triggers_need_equals():
    # o Ikemen recusa "Missing '=' or '!='" (caso real: agente escreveu animElem <= 4)
    f = zssread.parse("[StateDef 200]\n"
                      "if animElem <= 4 && p2BodyDist x < 14 {\n\tnull{}\n}\n"
                      "if animElem = 3 || animElemNo(0) <= 4 || animElemTime(2) >= 0 {\n\tnull{}\n}\n"
                      "if command != \"x\" && timeMod = 4, 2 {\n\tnull{}\n}\n"
                      "if timeMod > 4 {\n\tnull{}\n}\n")
    msgs = [(ln, m) for _, ln, m in zssread.check(f)]
    assert any(ln == 2 and "animElem" in m and "animElemNo" in m for ln, m in msgs)
    assert not any(ln in (5, 8) for ln, m in msgs)
    assert any(ln == 11 and "timeMod" in m for ln, m in msgs)


def test_scanning_sctrls_only_accept_their_key_formats():
    # Explod e Helper varrem o IniSection só atrás de shader.paramN / map.x: o resto o Ikemen recusa
    f = zssread.parse("[StateDef 200]\nexplod{anim: 1; pausemove: 999; shader.param1: 1}\n"
                      "helper{stateno: 1; map.carga: 1; sprpriority: 2}\nvarSet{var(3): 1}\n")
    msgs = [(ln, m) for _, ln, m in zssread.check(f)]
    assert any(ln == 2 and "pausemove" in m for ln, m in msgs)
    assert any(ln == 3 and "sprpriority" in m for ln, m in msgs)
    assert not any("shader.param1" in m or "map.carga" in m or "var(3)" in m for _, m in msgs)


def test_translator_rewrites_animelem_not_equal():
    from charfactory.formats import zss
    z = zss.cns_to_zss("[Statedef 200]\n\n[State 200, a]\ntype = Null\n"
                       "triggerall = animelem != 5\ntrigger1 = time > 2\n").text
    assert "animElemTime(5) != 0" in z and "animelem !=" not in z.lower()
    assert not [m for s, _, m in zssread.check(zssread.parse(z)) if s == "error"]


def test_cns_validate_catches_const_and_attr():
    from charfactory import validate
    st = cns.loads("[Statedef 200]\n\n[State 200, a]\ntype = VelSet\ntrigger1 = 1\n"
                   "x = const(velocity.runback.x)\n\n[State 200, b]\ntype = HitDef\n"
                   "trigger1 = 1\nattr = S, XX\ndamage = 5\n")
    msgs = [i.message for i in validate.validate_states(st, "t.cns") if i.severity == "error"]
    assert any("velocity.runback.x" in m for m in msgs) and any("XX" in m for m in msgs)


def test_for_loop_is_unrolled_and_while_warns():
    st, w = _lower("[StateDef 300]\nfor i = 1; 3 {\n\tvarAdd{v: 1; value: $i}\n}\n"
                   "for j = 0; 10; 5 {\n\tif time = $j {\n\t\tnull{}\n\t}\n}\n"
                   "while time < 3 {\n\tnull{}\n}\n"
                   "for k = 0; 3 {\n\tif time = 1 {\n\t\tbreak;\n\t}\n}\n")
    ctrls = st.statedefs[300].controllers
    adds = [c.section.get("value") for c in ctrls if c.type == "varAdd"]
    assert adds == ["1", "2", "3"]
    assert [c.triggers()["triggerall"] for c in ctrls if c.type == "null"] == \
           [["time = 0"], ["time = 5"], ["time = 10"]]
    assert any("while" in x for x in w) and any("break" in x for x in w)
