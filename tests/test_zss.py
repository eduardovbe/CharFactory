"""Tradução CNS → ZSS (formats/zss.py)."""
import pytest

from charfactory.formats import cns, zss


def sd(text: str) -> cns.StateDef:
    return next(iter(cns.loads(text).statedefs.values()))


def body(text: str) -> str:
    warns: list[str] = []
    out = zss.statedef_to_zss(sd(text), warns)
    return out.split("\n", 1)[1].strip(), warns


# ------------------------------------------------------------------ triggers

def test_condition_groups_and_triggerall():
    c = zss.condition({"triggerall": ["ctrl"], "1": ["time = 0", "var(1)"], "2": ["animtime = 0"]})
    assert c == "ctrl && ((time = 0 && var(1)) || animtime = 0)"


def test_condition_stops_at_missing_number():
    assert zss.condition({"1": ["a"], "3": ["b"]}) == "a"


def test_condition_constants():
    assert zss.condition({"1": ["1"]}) == ""                 # sempre
    assert zss.condition({"1": ["0"], "2": ["x"]}) == "x"    # grupo morto
    assert zss.condition({"1": ["0"]}) is None               # nunca
    assert zss.condition({"triggerall": ["1"], "1": ["x"]}) == "x"
    with pytest.raises(ValueError):
        zss.condition({"2": ["x"]})


def test_condition_wraps_comma_and_or():
    c = zss.condition({"1": ["movehit", "animelem = 4, >= 0", "a || b"]})
    assert c == "movehit && (animelem = 4, >= 0) && (a || b)"


# ------------------------------------------------------------------ blocos

def test_always_true_goes_to_root():
    out, _ = body("[Statedef 200]\ntype = S\n[State 200, x]\ntype = VelSet\ntrigger1 = 1\nx = 0\n")
    assert out == "# x\nvelSet{x: 0}"


def test_persistent_and_ignorehitpause():
    out, _ = body("[Statedef 200]\n[State 200, s]\ntype = PlaySnd\ntrigger1 = time = 0\n"
                  "value = 0,0\npersistent = 0\nignorehitpause = 1\n")
    assert "ignoreHitPause persistent(0) if time = 0 {" in out


def test_persistent_over_128_is_default():
    out, _ = body("[Statedef 200]\n[State 200]\ntype = PlaySnd\ntrigger1 = time = 0\n"
                  "value = 0,0\npersistent = 200\n")
    assert "persistent" not in out


def test_negative_state_drops_persistent_and_header():
    out = zss.statedef_to_zss(sd("[Statedef -2]\ntype = S\n[State -2]\ntype = VarSet\n"
                                 "trigger1 = 1\nvar(1) = 0\npersistent = 0\n"))
    assert out.startswith("[StateDef -2]\n")
    assert "persistent" not in out and "varSet{v: 1; value: 0}" in out


def test_explod_keeps_ignorehitpause_param():
    out, _ = body("[Statedef 200]\n[State 200]\ntype = Explod\ntrigger1 = time = 0\nanim = 1\n"
                  "ignorehitpause = 1\n")
    assert out.startswith("ignoreHitPause if time = 0 {") and "ignorehitpause: 1" in out


def test_merge_only_safe_runs():
    text = ("[Statedef 200]\n"
            "[State 200, a]\ntype = PlaySnd\ntrigger1 = time = 0\nvalue = 0,0\n"
            "[State 200, b]\ntype = Explod\ntrigger1 = time = 0\nanim = 1\n"
            "[State 200, c]\ntype = ChangeAnim\ntrigger1 = time = 0\nvalue = 2\n"
            "[State 200, d]\ntype = VelSet\ntrigger1 = time = 0\nx = 1\n")
    out, _ = body(text)
    assert out.count("if time = 0 {") == 2                   # a,b,c juntos; d depois do ChangeAnim
    text2 = text.replace("time = 0", "var(1) = 0")
    out2, _ = body(text2)
    assert out2.count("if var(1) = 0 {") == 4                # var: não junta


# ------------------------------------------------------------------ parâmetros

def test_cns_shorthand_normalized():
    out, _ = body("[Statedef 200]\n[State 200]\ntype = HitDef\ntrigger1 = 1\nattr = S, NA\n"
                  "animtype = Med\nground.type = H\nair.type = L\n")
    assert "animtype: Medium" in out and "ground.type: High" in out and "air.type: Low" in out


def test_postype_and_empty_values():
    out, _ = body("[Statedef 200]\n[State 200]\ntype = Explod\ntrigger1 = 1\nanim = 1\n"
                  "postype = Fr\nscale =\n")
    assert "postype: front" in out and "scale" not in out


def test_reversal_attr_empty_kept():
    out, _ = body("[Statedef 200]\n[State 200]\ntype = ReversalDef\ntrigger1 = time = 9\n"
                  "reversal.attr =\n")
    assert "reversal.attr: }" in out or "reversalDef{reversal.attr: }" in out


def test_unknown_param_dropped_with_warning():
    out, warns = body("[Statedef 200]\n[State 200]\ntype = PosFreeze\ntrigger1 = 1\nx = 1\n")
    assert out == "posFreeze{}" and any("'x'" in w for w in warns)


def test_duplicate_param_first_wins():
    out, warns = body("[Statedef 200]\n[State 200]\ntype = VelSet\ntrigger1 = 1\nx = 1\nx = 2\n")
    assert out == "velSet{x: 1}" and warns


def test_sctrl_name_casing():
    assert zss.sctrl_name("ChangeState") == "changeState"
    assert zss.sctrl_name("hitdef") == "hitDef"
    assert zss.sctrl_name("bgpalfx") == "bgPalFx"
    assert zss.sctrl_name("weirdthing") == "weirdthing"


def test_param_table_has_core_controllers():
    t = zss.ikemen_params()
    assert {"value", "ctrl", "anim"} <= set(t["changestate"]["params"])
    assert "sparkxy" in t["hitdef"]["params"]
    # o Explod só aceita além da lista o formato shader.paramN (pausemove é recusado no Ikemen)
    assert not t["explod"]["open"] and zss.param_known(t["explod"], "shader.param2")
    assert not zss.param_known(t["explod"], "pausemove")


# ------------------------------------------------------------------ exportação


