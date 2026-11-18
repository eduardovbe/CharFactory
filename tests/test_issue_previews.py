"""Regressions for issues #12 and #13, using self-contained characters."""
import os
from types import SimpleNamespace

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

from charfactory.formats import air
from charfactory.gui.context import AppContext
from charfactory.gui.editors.animations import AnimationEditor
from charfactory.gui.editors.codepages import CodePage
from charfactory.gui.editors.simulator import TickView
from charfactory.tools import generate, simulate


@pytest.fixture
def editor_context(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARFACTORY_CONFIG_HOME", str(tmp_path / "config"))
    app = QApplication.instance() or QApplication([])
    path = generate.new_character(tmp_path / "chars" / "test", "test")
    ctx = AppContext()
    ctx.open(str(path))
    yield app, ctx
    ctx.char.dirty.clear()


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("zss", [False, True])
def test_common_tab_applies_undoes_and_saves(editor_context, tmp_path, external, zss):
    app, ctx = editor_context
    c = ctx.char
    folder = tmp_path / "data" if external else c.dir
    folder.mkdir(exist_ok=True)
    path = folder / ("common1.cns.zss" if zss else "common1.cns")
    original = "[StateDef 0; type: S; anim: 0;]\n" if zss else "[Statedef 0]\ntype = S\nanim = 0\n"
    path.write_text(original, encoding="utf-8")
    c.mugen_root = tmp_path
    ctx.reset_cache("common_states")
    page = CodePage(ctx, "states", "States")
    try:
        page.load()
        source = next(f for f in page.files if f["path"] == path)
        source["edit"].setPlainText(original.replace("anim: 0", "anim: 10").replace("anim = 0", "anim = 10"))
        page.apply(source)
        assert c.common_states.statedefs[0].section.get("anim") == "10"
        assert path.read_text("utf-8") == original
        assert ctx.undo()
        app.processEvents()
        assert c.common_states.statedefs[0].section.get("anim") == "0"
        assert "10" not in source["edit"].toPlainText()
        assert ctx.redo()
        app.processEvents()
        assert "10" in source["edit"].toPlainText()
        saved = c.save_to(tmp_path / "backup")
        assert (str(tmp_path / "backup" / path.name) in saved) == (not external)
        assert c.save() == [str(path)]
        assert "10" in path.read_text("utf-8")
        assert path.with_suffix(path.suffix + ".bak").read_text("utf-8") == original
    finally:
        from conftest import dispose_window
        dispose_window(page)


def test_common_already_listed_as_state_has_one_tab(editor_context):
    _, ctx = editor_context
    ctx.char.definition.find("files").set("stcommon", "test.cns")
    page = CodePage(ctx, "states", "States")
    try:
        page.load()
        assert len(page.files) == 1
    finally:
        from conftest import dispose_window
        dispose_window(page)


def test_interpolation_can_be_scrubbed_paused_and_resumed(editor_context):
    _, ctx = editor_context
    c = ctx.char
    c.sprites.set_sprite(0, 0, Image.new("RGBA", (10, 20), "red"), 5, 20)
    c.__dict__["anims"] = air.loads("[Begin Action 0]\n0,0, 0,0, 10\nInterpolate Offset\n0,0, 100,0, 10\n")
    page = AnimationEditor(ctx)
    try:
        page.load()
        before = c.anims.to_text()
        page.goto_tick(5)
        assert page.frame_nav.index() == 0
        assert page.tick == page.timeline.current_tick == 5
        assert page.canvas.image_pos.x() == 45
        page.goto_tick(8)  # same AIR element: the pose must still update
        assert page.canvas.image_pos.x() == 75
        page.tick_nav.set_index(3)
        assert page.tick == page.timeline.current_tick == 3
        page.play_action.setChecked(True)
        page.on_tick()
        assert page.tick == 4
        page.play_action.setChecked(False)
        assert page.tick == 4
        assert page.canvas.image_pos.x() == 35
        page.goto_tick(15)
        assert page.frame_nav.index() == 1 and page.tick == 5
        page.frame_nav.set_index(0)
        assert page.tick == 0
        page.stop()
        assert page.timeline.current_tick == 0
        assert c.anims.to_text() == before
        assert not c.dirty
    finally:
        from conftest import dispose_window
        dispose_window(page)


def test_preview_size_is_independent_of_distant_trajectories():
    sim = SimpleNamespace(stand_h=100, lc_ratio=1)
    base = simulate.preview_window(sim)
    rec = {"camera_x": 20, "ents": [{"name": "P1", "pos": (0, 0)},
                                      {"name": "helper", "pos": (9999, -9999)}]}
    win = simulate.preview_tick_window(rec, base)
    assert win == (base[0] + 20, base[1] + 20, base[2], base[3])
    rec["ents"][0]["pos"] = (100, -2000)
    air_win = simulate.preview_tick_window(rec, base)
    assert air_win[1] - air_win[0] == win[1] - win[0]
    assert air_win[3] - air_win[2] == win[3] - win[2]
    assert air_win[2] < -2000 < air_win[3]
    assert (base[1] - base[0]) / (base[3] - base[2]) >= 4 / 3


def test_tick_view_large_pixmap_does_not_expand_layout(editor_context):
    app, _ = editor_context
    parent = QWidget()
    layout = QVBoxLayout(parent)
    view = TickView()
    layout.addWidget(view)
    parent.resize(640, 480)
    parent.show()
    try:
        for size in [(800, 600), (400, 5000), (5000, 400)]:
            view.set_image(Image.new("RGBA", size, "red"))
            app.processEvents()
            assert parent.width() == 640 and parent.height() == 480
            assert view.pixmap().width() <= view.width()
            assert view.pixmap().height() <= view.height()
    finally:
        from conftest import dispose_window
        dispose_window(parent)
