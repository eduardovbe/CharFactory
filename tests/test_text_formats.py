import pytest

from conftest import MUGEN
from charfactory.formats import air, mtext

TEXT_FILES = sorted(p for ext in ("*.def", "*.air", "*.cns", "*.cmd", "*.st")
                    for p in MUGEN.rglob(ext)) if MUGEN.exists() else []
AIR_FILES = [p for p in TEXT_FILES if p.suffix.lower() == ".air"]


@pytest.mark.parametrize("path", TEXT_FILES, ids=lambda p: p.name)
def test_text_roundtrip_identical(path):
    text, enc = mtext.read_text(path)
    assert mtext.parse(text, enc).to_text() == text


def test_section_edit_preserves_comment():
    doc = mtext.parse("[Data]\r\nlife = 1000 ;vida\r\npower = 3000\r\n")
    doc.find("data").set("life", "1200")
    doc.find("DATA").set("attack", "100")
    assert doc.to_text() == "[Data]\r\nlife = 1200 ;vida\r\npower = 3000\r\nattack = 100\r\n"


@pytest.mark.parametrize("path", AIR_FILES, ids=lambda p: p.name)
def test_air_roundtrip_and_canonical(path):
    text, _ = mtext.read_text(path)
    a = air.loads(text)
    assert a.to_text() == text  # sem edição: idêntico
    for act in a.actions.values():  # canônico tem o mesmo significado
        again = air.parse_action(act.number, act.to_lines())
        assert [f.info() for f in again.frames] == [f.info() for f in act.frames]
        assert again.loopstart == act.loopstart


def test_air_kfm_semantics(mugen_dir):
    a = air.load(mugen_dir / "chars/kfm/kfm.air")
    stand = a.get(0)
    assert stand.frames[0].clsn2 and stand.total_time > 0
    assert any(f.clsn1 for f in a.get(200).frames), "soco fraco precisa de caixa de ataque"
    assert not any(f.clsn1 for f in stand.frames)
    assert len(a.actions) > 50


def test_air_edit_regenerates_only_changed():
    src = "[Begin Action 1]\n;comentário\n1,0, 0,0, 5\n\n[Begin Action 2]\n2,0, 0,0, 5 ;x\n"
    a = air.loads(src)
    a.get(1).frames[0].time = 9
    out = a.to_text()
    # a ação editada é regravada no formato canônico, mas os comentários ficam no lugar
    assert "1,0, 0,0, 9" in out and "2,0, 0,0, 5 ;x" in out and ";comentário\n1,0, 0,0, 9" in out


def test_air_duplicate_action_keeps_first():
    # o MUGEN usa a primeira definição de um número repetido (zeno.air: 1411 com o rastro de
    # garra e depois uma 1411 vazia)
    a = air.loads("[Begin Action 1411]\n1052,7, 0,0, 1, , A\n1052,8, 0,0, 1, , A\n\n"
                  "[Begin Action 1411]\n-1,0, 0,0, 10\n")
    act = a.get(1411)
    assert len(act.frames) == 2 and act.frames[0].group == 1052


def test_entries_cache_follows_line_edits():
    doc = mtext.parse("[State 200, a]\ntype = ChangeState\ntrigger1 = 1\nvalue = 0\n")
    sec = doc.sections[0]
    assert sec.get("value") == "0"
    sec.set("value", "5")                   # edição pelo próprio Section
    assert sec.get("value") == "5"
    sec.lines[1] = "type = VelSet"          # edição direta nas linhas
    assert sec.get("type") == "VelSet"
    sec.lines.append("x = 3")
    assert sec.get("x") == "3"
    got = sec.entries()
    got.clear()                             # quem recebe a lista pode mexer nela sem estragar o cache
    assert sec.get("x") == "3"
    import copy
    clone = copy.deepcopy(sec)
    assert "_entries" not in clone.__dict__ and clone.get("x") == "3"
