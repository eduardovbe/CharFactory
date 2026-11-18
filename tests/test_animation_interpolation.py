"""Interpolação do AIR: valores iguais aos do motor, inspeção tick a tick no editor, simulador e prévia."""
import os

import pytest
from PIL import Image

from charfactory import project
from charfactory.formats import air
from charfactory.tools import charkit, generate, render

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OFFSET = "[Begin Action 0]\n0,0, 0,0, 10\nInterpolate Offset\n0,0, 100,0, 10\n"


def blend(a, b):
    act = air.loads(f"[Begin Action 0]\n0,0, 0,0, 10, ,{a}\nInterpolate Blend\n0,0, 0,0, 10, ,{b}\n").get(0)
    return render.interpolated_frame(act, 0, 5).trans


@pytest.mark.parametrize("start, end, expected", [
    ("A1", "AS0D0", "AS128D64"), ("AS128", "AS0D0", "AS64D0"), ("AS128D128", "AS0D0", "AS64D64"),
    ("A", "AS0D0", "AS128D128"), ("", "AS128D128", ""), ("S", "AS0D0", "S")])
def test_blend_follows_the_engine(start, end, expected):
    assert blend(start, end) == expected


@pytest.fixture()
def editor(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARFACTORY_CONFIG_HOME", str(tmp_path / "appdata"))
    app = QApplication.instance() or QApplication([])
    from charfactory.gui.context import AppContext
    from charfactory.gui.editors.animations import AnimationEditor
    ctx = AppContext()
    ctx.open(str(generate.new_character(tmp_path / "src", "S")))
    ctx.char.sprites.set_sprite(0, 0, Image.new("RGBA", (10, 20), "red"), 5, 20)
    ctx.char.__dict__["anims"] = air.loads(OFFSET)
    page = AnimationEditor(ctx)
    page.resize(1200, 700)
    page.load()
    yield app, ctx, page
    from conftest import dispose_window
    dispose_window(page)


def drag(tl, x0, x1, mods=Qt.KeyboardModifier.NoModifier):
    left = Qt.MouseButton.LeftButton

    def ev(kind, x, buttons):
        pt = QPointF(x, 40)
        return QMouseEvent(kind, pt, pt, left, buttons, mods)
    tl.mousePressEvent(ev(QEvent.Type.MouseButtonPress, x0, left))
    tl.mouseMoveEvent(ev(QEvent.Type.MouseMove, x1, left))
    pos = tl.current_tick
    tl.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, x1, Qt.MouseButton.NoButton))
    return pos


def test_dragging_the_timeline_inspects_and_ctrl_drag_reorders(editor):
    _, ctx, page = editor
    tl = page.timeline
    tl.resize(900, 72)
    scale = tl._scale()
    a = page.action()
    first = a.frames[0]
    assert drag(tl, tl.LEFT + scale * 2.5, tl.LEFT + scale * 7.5) == 7
    assert page.action().frames[0] is first and page.tick == 7 and page.canvas.image_pos.x() == 65
    drag(tl, tl.LEFT + scale * 2.5, tl.LEFT + scale * 18, Qt.KeyboardModifier.ControlModifier)
    assert page.action().frames[0] is not first


def test_editing_keeps_the_inspected_tick_and_pose(editor):
    _, ctx, page = editor
    page.goto_tick(5)
    page.on_image_drag(10, 0, True)
    assert page.tick == 5 and page.frame().x == 10 and page.canvas.image_pos.x() == 50
    page.xs.setValue(1.0)
    page.ang.setValue(0.0)
    page.src.setValue(page.src.value())
    assert page.tick == 5 and page.canvas.image_pos.x() == 50


def test_interpolation_checkbox_belongs_to_the_destination_frame(editor):
    _, ctx, page = editor
    ctx.char.__dict__["anims"] = air.loads("[Begin Action 0]\n0,0, 0,0, 10\n0,0, 100,0, 10\n")
    page.load()
    page.frame_nav.set_index(1)
    page.interp["offset"].setChecked(True)
    assert render.interpolated_frame(page.action(), 0, 5).x == 50
    page.goto_tick(5)
    assert page.interp_info.text() == "offset 50,0"


def test_counter_keys_and_infinite_frames(editor):
    app, ctx, page = editor
    page.goto_tick(0)
    assert page.tick_nav.label.text() == "0 / 9"
    page.step_tick(-1)
    assert page.timeline.current_tick == 0
    for _ in range(11):
        page.step_tick(1)
    assert page.frame_nav.index() == 1 and page.tick == 1
    ctx.char.__dict__["anims"] = air.loads("[Begin Action 0]\n0,0, 0,0, -1\n")
    page.load()
    for _ in range(150):
        page.on_tick()
    assert page.tick == 59 and page.timeline.current_tick == 59


def test_one_redraw_when_playback_changes_frame(editor, monkeypatch):
    _, ctx, page = editor
    page.tick = 9
    calls = []
    original = page.show_frame
    monkeypatch.setattr(page, "show_frame", lambda *a, **k: (calls.append(1), original(*a, **k)))
    page.frame_nav.indexChanged.disconnect()
    page.frame_nav.indexChanged.connect(lambda _: page.show_frame(reset_tick=True))
    page.on_tick()
    assert len(calls) == 1 and page.frame_nav.index() == 1


def test_onion_skin_starts_off_even_with_an_old_saved_choice(editor):
    _, ctx, page = editor
    from charfactory.gui.editors.animations import AnimationEditor
    ctx.settings["onion_on"] = True
    other = AnimationEditor(ctx)
    try:
        assert not other.onion.isChecked()
    finally:
        from conftest import dispose_window
        dispose_window(other)


def test_simulator_and_state_preview_follow_the_interpolation(tmp_path):
    c = project.load(generate.new_character(tmp_path / "src", "S"))
    c.sprites.set_sprite(0, 0, Image.new("RGBA", (10, 20), "red"), 5, 20)
    c.__dict__["anims"] = air.loads(OFFSET)
    assert charkit.frame_at(c, 0, 5).x == 50 and charkit.frame_at(c, 0, 15).x == 100
    from charfactory.gui.animpreview import AnimPreview
    QApplication.instance() or QApplication([])
    frames, loop, _, msg = AnimPreview._render_images(c, 0)
    assert not msg and sum(t for _, t in frames) == 20 and len(frames) == 6 and loop == 0
