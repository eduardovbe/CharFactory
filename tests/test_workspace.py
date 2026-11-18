"""Session and navigation regressions with self-contained characters."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from charfactory.gui.app import MainWindow
from charfactory.gui.context import AppContext
from charfactory.tools import generate, ops
from charfactory.tools.resource_refs import find_usages


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARFACTORY_CONFIG_HOME", str(tmp_path / "config"))
    app = QApplication.instance() or QApplication([])
    ctx = AppContext()
    win = MainWindow(ctx)
    win.show()
    path = generate.new_character(tmp_path / "one", "one")
    win.open_path(str(path))
    app.processEvents()
    yield app, ctx, win
    for c in ctx.projects:
        c.dirty.clear()
    for page in win.code_pages:
        for f in page.files:
            f["edit"].document().setModified(False)
    from conftest import dispose_window
    dispose_window(win)


def test_history_round_trip_and_branch(window):
    _, ctx, win = window
    ops.create_action(ctx.char, 0, [{"group": 0, "item": 0, "time": 3}])
    win.anims.load()
    win.show_page(win.states)
    ed = win.states.current_edit()
    ed.goto_line(5)
    source = ed.textCursor().position()
    win.open_definition("anim", 0)
    assert win.current_page() is win.anims
    win.workspace.travel(-1)
    assert win.current_page() is win.states
    assert win.states.current_edit().textCursor().position() == source
    win.workspace.travel(1)
    assert win.current_page() is win.anims
    win.workspace.travel(-1)
    win.show_page(win.sounds)
    assert not win.forward_action.isEnabled()
    assert len(win.workspace.entries) <= win.workspace.LIMIT


def test_session_restores_projects_active_editor_cursors_and_frame(window, tmp_path):
    app, ctx, win = window
    first = ctx.char
    ops.create_action(first, 7777, [{"group": 0, "item": 0, "time": 3},
                                   {"group": 0, "item": 0, "time": 4}])
    first.save()
    win.show_page(win.states)
    win.states.current_edit().goto_line(5)
    position = win.states.current_edit().textCursor().position()
    win.open_definition("anim", 7777)
    win.anims.frame_nav.set_index(1)
    second_path = generate.new_character(tmp_path / "two", "two")
    win.open_path(str(second_path))
    win.show_page(win.cmds)
    ctx.activate(first)
    assert win.current_page() is win.anims
    assert win.anims.action().number == 7777
    assert win.anims.frame_nav.index() == 1
    win.workspace.save_session()
    session = json.loads(json.dumps(ctx.settings["session"]))
    assert "cursors" in json.dumps(session)
    other_ctx = AppContext()
    other_ctx.settings["session"] = session
    other = MainWindow(other_ctx)
    other.show()
    try:
        assert other.workspace.restore_session() == []
        app.processEvents()
        assert len(other_ctx.projects) == 2
        assert other_ctx.char.def_path == first.def_path
        assert other.current_page() is other.anims
        assert other.anims.action().number == 7777
        assert other.anims.frame_nav.index() == 1
        other.show_page(other.states)
        assert other.states.current_edit().textCursor().position() == position
    finally:
        from conftest import dispose_window
        dispose_window(other)


def test_missing_project_and_restore_disabled(window, tmp_path):
    _, ctx, win = window
    missing = str(tmp_path / "deleted.def")
    ctx.settings["session"] = {"projects": [{"path": missing}], "active": missing}
    assert win.workspace.restore_session() == [missing]
    ctx.settings["restore_session"] = False
    assert win.workspace.restore_session() == []


def test_history_between_projects_and_closing_skips_closed_destinations(window, tmp_path):
    _, ctx, win = window
    first = ctx.char
    win.show_page(win.states)
    win.states.current_edit().goto_line(5)
    position = win.states.current_edit().textCursor().position()
    win.open_path(str(generate.new_character(tmp_path / "two", "two")))
    second = ctx.char
    win.show_page(win.cmds)
    # Jump back through the new project's initial page, then into the first one.
    for _ in range(5):
        win.workspace.travel(-1)
        if ctx.char is first:
            break
    assert ctx.char is first
    assert win.current_page() is win.states
    assert win.states.current_edit().textCursor().position() == position
    assert win.close_project()
    assert ctx.char is second
    for _ in range(5):
        win.workspace.travel(-1)
    assert ctx.char is second and first not in ctx.projects
    assert win.close_project()
    assert win.current_page() is None
    assert not win.back_action.isEnabled()
    win.workspace.save_session()
    assert ctx.settings["session"]["projects"] == []


def test_usages_live_buffer_and_jump(window):
    _, ctx, win = window
    win.show_page(win.states)
    ed = win.states.current_edit()
    ed.setPlainText("[Statedef 7777]\nanim = 8888\n[State 7777, next]\ntype = ChangeState\nvalue = 0\n")
    ed.goto_line(2)
    win.find_usages()
    item = win.usages_results.item(0)
    assert "anim = 8888" in item.text()
    win.on_search_result(item)
    assert win.current_page() is win.states
    assert win.states.current_edit().textCursor().blockNumber() == 1
    # Querying did not apply or save the edited text.
    assert 7777 not in ctx.char.all_statedefs()
    ed.document().setModified(False)


def test_resource_references_exact_dynamic_foreign_and_offsets():
    text = ("; preamble\n[Statedef 200]\nanim = 200\n"
            "[State 200, sound]\ntype = PlaySnd\nvalue = S5, 0\n"
            "[State 200, foreign]\ntype = PlaySnd\nvalue = F5, 0\n"
            "[State 200, calculated]\ntype = ChangeAnim\nvalue = 200 + var(1)\n"
            "[State 200, next]\ntype = ChangeState\nvalue = 1000\n"
            "[State 200, spark]\ntype = HitDef\nsparkno = S200\nguard.sparkno = 200\n")
    sources = [("test.cns", text, False)]
    anims, skipped = find_usages(sources, "anim", 200)
    assert not skipped
    assert [(x.line, x.dynamic) for x in anims] == [(3, False), (12, True), (18, False)]
    sounds, _ = find_usages(sources, "sound", 5, 0)
    assert len(sounds) == 1 and sounds[0].line == 6
    states, _ = find_usages(sources, "state", 1000)
    assert len(states) == 1 and states[0].line == 15
    air = "; sprites\n[Begin Action 200]\nClsn2: 0\n200,0, 0,0, 4\n200,1, 0,0, 4\n"
    sprites, _ = find_usages([("test.air", air, False)], "sprite", 200, 0)
    assert len(sprites) == 1 and sprites[0].line == 4


def test_zss_usages_link_to_source_state():
    text = "# intro\n[StateDef 200; type: S; anim: 200;]\nif time = 0 {\n playSnd{value: 5, 0}\n}\n"
    results, skipped = find_usages([("test.zss", text, True)], "sound", 5, 0)
    assert not skipped
    assert len(results) == 1 and results[0].line == 2
