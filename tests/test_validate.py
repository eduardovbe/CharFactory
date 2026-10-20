import pytest

from charfactory import validate
from charfactory.formats import cmd, cns


def _issues(text):
    return validate.validate_states(cns.loads(text), "t.cns")


def _msgs(text, sev="error"):
    return [i.message for i in _issues(text) if i.severity == sev]


def test_clean_state_has_no_issues():
    assert _issues("[Statedef 200]\ntype = S\n\n[State 200, 1]\ntype = ChangeState\n"
                   "trigger1 = AnimTime = 0\nvalue = 0\nctrl = 1\n") == []


@pytest.mark.parametrize("body,expected", [
    ("type = ChangeStat\ntrigger1 = 1\nvalue = 0", "unknown"),
    ("type = ChangeState\ntrigger1 = 1", "missing required parameter 'value'"),
    ("trigger1 = 1\nvalue = 0", "missing 'type'"),
    ("type = ChangeState\nvalue = 0", "no trigger1"),
    ("type = ChangeState\ntrigger2 = 1\nvalue = 0", "start at trigger2"),
    ("type = ChangeState\ntrigger1 = (Time = 0\nvalue = 0", "opening without a matching closing"),
    ("type = ChangeState\ntrigger1 = command = \"x\nvalue = 0", "unclosed quotes"),
    ("type = NotHitBy\ntrigger1 = 1\ntime = 1", "value, value2"),
])
def test_detects_errors(body, expected):
    msgs = _msgs(f"[Statedef 200]\n\n[State 200, t]\n{body}\n")
    assert any(expected in m for m in msgs), msgs


def test_warnings():
    text = "[Statedef 200]\n\n[State 200, t]\ntype = VelSet\ntrigger1 = Tiem = 0\nx = 1\nfoo = 2\n"
    w = _msgs(text, "warning")
    assert any("'Tiem'" in m for m in w) and any("'foo'" in m for m in w)


def test_var_syntax_and_intervals_accepted():
    text = ("[Statedef 200]\n\n[State 200, v]\ntype = VarSet\ntrigger1 = StateNo != [200,210)\n"
            "var(3) = 1\n")
    assert _msgs(text) == []


def test_orphan_controller():
    assert any("outside a [Statedef]" in m for m in _msgs("[State 5, x]\ntype = Null\ntrigger1 = 1\n"))


@pytest.mark.parametrize("seq,ok", [("~D, DF, F, x", True), ("/$F", True), ("x+y", True),
                                    ("D, DF, F, w", False), ("D,,F", False)])
def test_command_sequences(seq, ok):
    assert (cmd.check_sequence(seq) == []) == ok


def test_expression_checker():
    assert validate.check_expression("ifelse(var(1), 1, 0) && P2BodyDist X < 30")[0] == []
    assert validate.check_expression("const(velocity.walk.fwd.x) > 0") == ([], [])


def test_functions_mugen_does_not_have_are_errors():
    """sqrt/min/max não existem no MUGEN: ele recusa o CNS inteiro ("Invalid trigger: sqrt"). O simulador aceita, então só a validação pega."""
    errs, _ = validate.check_expression("9 * fvar(0) / sqrt(fvar(0) * fvar(0) + 1)")
    assert errs and "sqrt" in errs[0] and "** 0.5" in errs[0]
    assert validate.check_expression("max(var(1), 3)")[0]
    assert validate.check_expression("(fvar(0) * fvar(0)) ** 0.5")[0] == []
