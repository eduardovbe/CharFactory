"""Simulador: camadas, fundo, zoom da imagem, teclado, repetição e simular de novo sozinho."""
import os
import shutil
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture()
def page(mugen_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("CHARFACTORY_CONFIG_HOME", str(tmp_path / "appdata"))
    work = tmp_path / "chars" / "kfm"
    shutil.copytree(mugen_dir / "chars" / "kfm", work)
    (tmp_path / "data").mkdir()
    shutil.copy(mugen_dir / "data" / "common1.cns", tmp_path / "data")
    app = QApplication.instance() or QApplication([])
    from charfactory.gui.app import MainWindow
    from charfactory.gui.context import AppContext
    ctx = AppContext()
    win = MainWindow(ctx)
    win.show()
    win.open_path(str(work / "kfm.def"))
    win.show_page(win.simulator)
    app.processEvents()
    yield app, ctx, win, win.simulator
    ctx.char.dirty.clear()
    for p in win.code_pages:
        for f in p.files:
            f["edit"].document().setModified(False)
    from conftest import dispose_window
    dispose_window(win)


def wait(app, cond, seconds=60):
    t = time.perf_counter()
    while not cond() and time.perf_counter() - t < seconds:
        app.processEvents()
    assert cond()


def simulated(app, sp, state=200):
    sp.run_state(state)
    wait(app, lambda: sp.sim is not None and sp.run_btn.isEnabled())
    return sp.sim


def test_render_hides_layers_and_changes_the_background(mugen_dir):
    from charfactory import project
    from charfactory.tools import simulate
    c = project.load(mugen_dir / "chars" / "kfm" / "kfm.def", str(mugen_dir))
    sim = simulate.simulate(c, 200)
    rec = next(r for r in sim.track if any(e["clsn1"] for e in r["ents"]))
    win = simulate.preview_tick_window(rec, simulate.preview_window(sim))
    base = simulate.render_tick(sim, rec, win).tobytes()
    for layer in ("p1", "p2", "clsn1", "clsn2", "ground"):
        assert simulate.render_tick(sim, rec, win, hide=frozenset({layer})).tobytes() != base, layer
    only_boxes = simulate.render_tick(sim, rec, win, hide=frozenset({"sprites"}))
    nothing = simulate.render_tick(sim, rec, win, hide=frozenset({"sprites", "clsn1", "clsn2", "ground"}))
    assert only_boxes.tobytes() != nothing.tobytes()
    assert len(nothing.getcolors()) == 1                 # sem camada nenhuma, só o fundo
    green = simulate.render_tick(sim, rec, win, background=(0, 177, 64))
    assert green.getpixel((0, 0)) == (0, 177, 64, 255)
    assert simulate.render_tick(sim, rec, win, hurtboxes=True).tobytes() != base


def test_layers_presets_and_items(page):
    app, ctx, win, sp = page
    simulated(app, sp)
    key = sp._key(sp.slider.value())
    assert sp.preset.currentData() == "all"
    sp.layer_checks["explods"].setChecked(False)
    assert sp.preset.currentData() is None and sp._key(sp.slider.value()) != key   # desenha de novo
    sp._apply_preset(next(i for i in range(sp.preset.count()) if sp.preset.itemData(i) == "boxes"))
    assert not sp.layer_checks["sprites"].isChecked() and sp.layer_checks["explods"].isChecked()
    assert sp.preset.currentData() == "boxes"
    sp._save_prefs()
    assert ctx.settings["simulator"]["hidden"] == ["sprites"]


def test_image_zoom_keeps_the_point_under_the_mouse(page):
    app, ctx, win, sp = page
    simulated(app, sp)
    wait(app, lambda: sp.view._pix is not None)
    v = sp.view
    s0 = v._scale()
    v.zoom_by(2.0)
    assert v._scale() == pytest.approx(s0 * 2) and sp.zoom_label.text() == "200%"
    at = QPointF(v.width() * 0.75, v.height() * 0.5)
    c = QPointF(v.width() / 2, v.height() / 2)
    before = (at - c - v.view_pan) / v._scale()
    v.zoom_by(1.5, at)
    after = (at - c - v.view_pan) / v._scale()
    assert before.x() == pytest.approx(after.x(), abs=1e-6)
    v.reset_view()
    assert v._scale() == pytest.approx(s0) and sp.zoom_label.text() == "100%"


def test_space_on_the_simulate_button_is_not_a_new_simulation(page):
    app, ctx, win, sp = page
    assert sp.run_btn.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_navigation_marks_and_repeat_range(page):
    app, ctx, win, sp = page
    simulated(app, sp)
    assert sp._marks
    sp.goto_end(True)
    assert sp.slider.value() == sp.slider.maximum()
    sp.goto_end(False)
    sp.jump_mark(1)
    assert sp.slider.value() == sp._marks[0]
    sp.slider.setValue(2)
    sp.mark_loop(True)
    sp.slider.setValue(5)
    sp.mark_loop(False)
    assert sp._loop == (2, 5) and sp.timeline.loop == (2, 5)
    sp.clear_loop()
    assert sp._loop is None


def test_play_stops_at_the_end_without_repeat(page):
    app, ctx, win, sp = page
    simulated(app, sp)
    sp.sound.setChecked(False)
    sp.repeat_action.setChecked(False)
    sp.speed.setCurrentIndex(sp.speed.findData(2.0))
    sp.slider.setValue(max(0, sp.slider.maximum() - 3))
    sp.play_action.setChecked(True)
    wait(app, lambda: not sp.play_action.isChecked())
    assert sp.slider.value() >= sp.slider.maximum() - 1 and not sp.timer.isActive()


def test_simulates_again_when_coming_back_after_an_edit(page):
    app, ctx, win, sp = page
    old = simulated(app, sp)
    sp.slider.setValue(4)
    win.show_page(win.states)
    app.processEvents()
    ctx.changed("states")
    assert sp.sim is None
    win.show_page(sp)
    wait(app, lambda: sp.sim is not None and sp.sim is not old and sp.run_btn.isEnabled())
    assert sp.slider.value() == 4


def test_parameter_change_simulates_again(page):
    app, ctx, win, sp = page
    old = simulated(app, sp)
    sp.dist.setValue(sp.dist.value() + 15)
    wait(app, lambda: sp.sim is not old and sp.sim is not None and sp.run_btn.isEnabled())
    assert sp.sim.p2.pos is not None


def test_remembers_the_last_move_of_the_character(page):
    app, ctx, win, sp = page
    simulated(app, sp, 210)
    sp._save_prefs()
    assert ctx.settings["simulator"]["chars"][str(ctx.char.def_path)]["state"] == 210
    sp.state.setCurrentIndex(0)
    sp.load()
    assert sp.current_state() == 210
