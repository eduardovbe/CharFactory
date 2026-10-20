"""Testes da interface gráfica em modo offscreen (sem janela na tela)."""
import os
import shutil

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from charfactory import project  # noqa: E402


@pytest.fixture()
def gui(mugen_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
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
    app.processEvents()
    yield app, ctx, win, work
    if ctx.char:
        ctx.char.dirty.clear()
    for page in win.code_pages:
        for f in page.files:
            f["edit"].document().setModified(False)
    win.close()


def test_open_loads_only_the_visible_code_page(gui):
    app, ctx, win, work = gui
    # abrir mostra Definições: os outros editores de código só carregam quando abertos
    assert win.current_page() is win.defs and win.defs.files
    assert not win.states.files and not win.cmds.files and not win.air_code.files
    win.show_page(win.states)
    assert [f["path"].name for f in win.states.files] == ["kfm.cns"]
    assert win.states.tree.topLevelItemCount()
    # trocar de personagem recarrega a página à vista e esvazia as outras
    other = work.parent / "kfm2"
    shutil.copytree(work, other)
    win.open_path(str(other / "kfm.def"))
    app.processEvents()
    assert win.current_page() is win.states and win.states.files[0]["path"].parent == other
    assert not win.defs.files
    # quem precisa do texto (Problemas, busca) carrega a página na hora
    win.goto_code("kfm.cmd", "[Statedef -1]")
    assert win.current_page() is win.cmds and win.cmds.files
    for c in list(ctx.projects):
        c.dirty.clear()


def test_open_and_all_themes(gui):
    app, ctx, win, _ = gui
    assert ctx.char.name == "Kung Fu Man"
    assert win.sprites.nav.count == 281
    assert win.anims.nav.count == 117
    for name in list(ctx.themes):
        ctx.set_theme(name)
        for page in win.pages:
            win.show_page(page)
            app.processEvents()
            assert win.center.currentWidget() is page and win.left_stack.currentWidget() is page.left
        assert app.styleSheet() == ctx.theme.stylesheet()


def test_edit_boxes_and_save(gui):
    app, ctx, win, work = gui
    win.anims.select_action(200)
    win.anims.frame_nav.set_index(0)
    win.anims.canvas.boxes.append([1, [5, -60, 30, -50]])
    win.anims.canvas.boxesChanged.emit()
    assert ctx.char.anims.get(200).frames[0].clsn1 == [(5, -60, 30, -50)]
    assert win.save()
    again = project.load(work / "kfm.def")
    assert again.anims.get(200).frames[0].clsn1 == [(5, -60, 30, -50)]


def test_code_editor_apply_and_validate(gui):
    app, ctx, win, _ = gui
    win.show_page(win.states)
    f = win.states.files[0]
    text = f["edit"].toPlainText() + "\n[Statedef 9400]\ntype = S\n\n[State 9400, x]\ntype = ChangeStat\ntrigger1 = 1\n"
    f["edit"].setPlainText(text)
    win.states.apply(f)
    assert 9400 in ctx.char.all_statedefs(False)
    win.validate()
    bad = [i for i in ctx.last_issues if i.severity == "error"]
    assert bad and "ChangeStat" in bad[0].message
    assert f["edit"].issue_lines  # erro marcado na margem


def test_sprite_axis_and_palette_variant(gui):
    app, ctx, win, _ = gui
    win.sprites.select(0, 0)
    win.sprites.on_axis_picked(10, 100)
    s = ctx.char.sprites.find(0, 0)
    assert (s.axis_x, s.axis_y) == (10, 100)
    from charfactory.gui.editors.palettes import PaletteEditor
    adv = PaletteEditor(ctx)
    adv.load()
    adv.target.setValue(9)
    adv.hue.setValue(120)
    adv.make_variant()
    assert ctx.char.sprites.find_palette(1, 9) >= 0


def test_theme_import_colorset_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from charfactory.gui import theme
    xml = tmp_path / "Teste.xml"
    xml.write_text('<?xml version="1.0"?><colors><color name="background" value="10,20,30"/>'
                   '<color name="foreground" value="200,210,220"/>'
                   '<color name="comment" italic="true" value="1,2,3"/></colors>', "utf-8")
    t = theme.import_colorset(xml, "Teste XML")
    assert t.ui["window"] == "#0a141e" and t.c("comment").italic
    t.save()
    loaded = theme.load_all()["Teste XML"]
    assert loaded.c("background").rgb == (10, 20, 30) and not loaded.builtin
    assert "QWidget" in loaded.stylesheet()


def test_offset_viewer_dialog_inserts_pos(gui):
    app, ctx, win, _ = gui
    from charfactory.gui.dialogs import OffsetViewerDialog
    win.show_page(win.states)
    d = OffsetViewerDialog(ctx, win.insert_code, win)
    d.anim[1].setCurrentText("200")
    d.preview.dragged.emit(5, -3)
    assert d.pos_text() == "pos = 45, -43"
    assert d.preview.image is not None
    d.do_insert()
    assert "pos = 45, -43" in win.states.current_edit().toPlainText()
    d.close()


def test_animation_canvas_scale_and_open_zip(gui, tmp_path):
    app, ctx, win, work = gui
    win.anims.select_action(0)
    win.anims.frame_nav.set_index(0)
    w0 = win.anims.canvas.image.width()
    win.anims.xs.setValue(2)
    assert win.anims.canvas.image.width() == 2 * w0
    assert ctx.char.anims.get(0).frames[0].extra[0] == "2"
    ctx.char.dirty.clear()
    from charfactory import archive
    z = tmp_path / "dl" / "lutador.zip"  # destino chars/lutador ainda não existe: nenhuma pergunta
    archive.pack(work, z)
    ctx.settings["mugen_dir"] = str(tmp_path)
    win.open_path(str(z))
    assert ctx.char.def_path == (tmp_path / "chars" / "lutador" / "kfm.def").resolve()


def test_go_to_definition_palette_and_panels(gui):
    app, ctx, win, work = gui
    # F12 / Ctrl+clique: "anim = 200" no statedef 200 abre a animação 200
    win.show_page(win.states)
    ed = win.states.current_edit()
    lines = ed.toPlainText().split("\n")
    n = next(i for i, ln in enumerate(lines) if ln.strip().lower().startswith("[statedef 200"))
    k = next(i for i in range(n, n + 15) if lines[i].strip().lower().startswith("anim"))
    ed.goto_line(k + 1)
    assert ed.go_to_definition()
    assert win.center.currentWidget() is win.anims and win.anims.action().number == 200
    win.open_definition("state", 200)
    assert win.center.currentWidget() is win.states
    # paleta de comandos: menus + recursos do personagem
    labels = [e[0] for e in win.palette_entries()]
    assert "Project › Save" in labels and any(lb.startswith("anim 200") for lb in labels)
    assert any(lb.startswith("state 200") for lb in labels) and any(lb.startswith("sound ") for lb in labels)
    # painel lateral segmentado, painel inferior que abre e fecha (Ctrl+J) e contador de erros
    win.left_tabs.setCurrentIndex(1)
    assert win.left_tabs.stack.currentWidget().isAncestorOf(win.explorer)
    errors = [i for i in ctx.last_issues if i.severity == "error"]
    assert win.bottom.isVisible() == bool(errors)
    win.set_bottom_open(True)
    app.processEvents()
    assert win.bottom.isVisible() and win.badges[0][0].isVisible() == bool(errors)
    win.toggle_bottom()
    assert not win.bottom.isVisible()


def test_drop_images_become_sprites(gui, tmp_path):
    app, ctx, win, _ = gui
    from PIL import Image
    p = tmp_path / "novo.png"
    Image.new("RGBA", (20, 30), (200, 40, 40, 255)).save(p)
    win.show_page(win.sprites)
    win.sprites.select(0, 0)
    before = len(ctx.char.sprites.sprites)
    win.import_dropped_images([p])
    assert len(ctx.char.sprites.sprites) == before + 1
    cur = win.sprites.current()
    assert cur.group == 0 and cur.width == 20


def test_air_transparency_blends_in_canvas(gui):
    """A, A1, S e AS…D… mudam o que o canvas mostra (antes só o alfa normal era desenhado)."""
    import numpy as np
    from PIL import Image
    from charfactory.tools import render
    base = Image.new("RGBA", (4, 1), (100, 100, 100, 255))
    spr = Image.new("RGBA", (4, 1), (80, 80, 80, 255))
    got = {}
    for t in ("", "A", "A1", "S", "AS128D128"):
        c = base.copy()
        render.paste_blended(c, spr, (0, 0), t)
        got[t] = c.getpixel((0, 0))[0]
    assert got == {"": 80, "A": 180, "A1": 130, "S": 20, "AS128D128": 90}
    app, ctx, win, _ = gui
    win.show_page(win.anims)
    an = win.anims
    means = {}
    for idx in (0, 1, 3):          # nenhuma, A, S
        an.trans.setCurrentIndex(idx)
        app.processEvents()
        img = an.canvas.grab().toImage()
        r, d = an.canvas._sprite_rect(), img.devicePixelRatio()
        sub = img.copy(int(r.x() * d), int(r.y() * d), int(r.width() * d), int(r.height() * d))
        arr = np.frombuffer(sub.constBits(), np.uint8, sub.bytesPerLine() * sub.height())
        means[idx] = float(arr.mean())
    assert means[1] > means[0] > means[3]
    an.trans.setCurrentIndex(0)
    an.src.setValue(200)             # mexer na origem liga o modo AS…D…
    assert an.frame().trans.upper().startswith("AS200D")


def test_wheel_needs_focus_and_no_ambiguous_shortcuts(gui):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QAction, QWheelEvent
    from PySide6.QtWidgets import QMenu, QMenuBar, QWidget
    app, ctx, win, _ = gui
    win.show_page(win.anims)
    spin = win.anims.time
    before = spin.value()
    c = QPointF(4, 4)
    ev = QWheelEvent(c, spin.mapToGlobal(c), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(spin, ev)
    assert spin.value() == before          # sem foco: a roda rola o painel, não muda o tempo
    # a roda não pode dar foco ao campo (o Qt faz isso antes do filtro, com Qt.WheelFocus)
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox
    assert spin.focusPolicy() != Qt.FocusPolicy.WheelFocus
    assert not [w for t in (QAbstractSpinBox, QComboBox) for w in win.findChildren(t) if w.isVisible()
                and w.focusPolicy() == Qt.FocusPolicy.WheelFocus]
    # fora de área rolável (barra do editor de código) a roda também não muda campo sem foco
    win.show_page(win.states)
    app.processEvents()
    combo = win.states.issue_combo
    combo.addItem("extra")
    idx = combo.currentIndex()
    QApplication.sendEvent(combo, QWheelEvent(c, combo.mapToGlobal(c), QPoint(0, 0), QPoint(0, -120),
                                              Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                              Qt.ScrollPhase.NoScrollPhase, False))
    assert combo.currentIndex() == idx
    # duas ações ativas com o mesmo atalho fazem o Qt não executar nenhuma. Como no Qt, uma
    # ação de menu só vale se o menu estiver aberto ou preso (barra de menus, botão, outro
    # menu) a algo visível: o menu '⋯' de um editor escondido não conta
    def live_menu(m):
        return m.isVisible() or any(isinstance(o, QMenuBar) or (isinstance(o, QMenu) and live_menu(o))
                                    or (isinstance(o, QWidget) and o.isVisible())
                                    for o in m.menuAction().associatedObjects())

    for page in win.pages:
        win.show_page(page)
        app.processEvents()
        seen = {}
        for a in win.findChildren(QAction):
            if a.shortcut().isEmpty() or not a.isEnabled():
                continue
            live = any(live_menu(o) if isinstance(o, QMenu) else isinstance(o, QWidget) and o.isVisible()
                       for o in a.associatedObjects())
            if live:
                key = a.shortcut().toString()
                assert key not in seen, f"{key}: '{seen[key]}' e '{a.text()}' em {page.title}"
                seen[key] = a.text()


def test_states_tree_modes_and_anim_preview(gui):
    app, ctx, win, _ = gui
    page = win.states
    win.show_page(page)
    app.processEvents()
    tops = [page.tree.topLevelItem(k).text(0) for k in range(page.tree.topLevelItemCount())]
    assert "Specials" in tops and "Normal moves" in tops
    # tudo fechado ao abrir o personagem, e a prévia (painel direito) também começa fechada
    assert not any(page.tree.topLevelItem(k).isExpanded() for k in range(page.tree.topLevelItemCount()))
    assert not page.preview_section.is_open()
    page.set_view_mode("flat")
    tops = [page.tree.topLevelItem(k).text(0) for k in range(page.tree.topLevelItemCount())]
    assert any(t.startswith("1000 ") for t in tops) and "Especiais" not in tops
    assert ctx.settings["states_view"] == "flat"
    page.set_view_mode("grouped")
    # cursor dentro do [Statedef 200] -> prévia toca a anim 200
    ed = page.current_edit()
    ln = next(i for i, l in enumerate(ed.toPlainText().split("\n"), 1) if l.strip().lower().startswith("[statedef 200]"))
    ed.goto_line(ln + 1)
    page.sync_to_cursor()
    assert page.preview.number is None             # fechada: não desenha
    page.preview_section.head.setChecked(True)     # abrir mostra a ação do estado do cursor
    page.preview._load()
    assert page.preview.number == 200 and page.preview.frames
    assert "200" in page.crumb.text()


def test_simulator_runs_off_the_ui_thread(gui):
    """Simular, auditar e desenhar os ticks não podem prender a interface (antes um super
    deixava a janela parada por dezenas de segundos)."""
    import time
    app, ctx, win, _ = gui
    sp = win.simulator
    win.show_page(sp)
    t = time.perf_counter()
    sp.run_state(200)
    assert time.perf_counter() - t < 0.5          # volta na hora: o trabalho segue na fila
    assert sp.sim is None and not sp.run_btn.isEnabled()
    t = time.perf_counter()
    while sp.sim is None and time.perf_counter() - t < 60:
        app.processEvents()
    assert sp.sim is not None and sp.run_btn.isEnabled() and sp.slider.isEnabled()
    while sp._rendering or sp._key(sp.slider.value()) not in sp.cache:   # 1º tick desenhado fora
        app.processEvents()
        assert time.perf_counter() - t < 60
    assert sp.view._pix is not None
    # outra simulação no meio descarta o resultado velho
    old = sp.sim
    sp.run_state(200)
    sp.on_done((sp._gen - 1, None, None, None))   # chegou atrasado: não troca nada
    assert sp.sim is old
    while sp.sim is old and time.perf_counter() - t < 60:
        app.processEvents()
    assert sp.sim is not old and sp.sim is not None


def test_simulator_frame_data_guard_and_sound(gui, monkeypatch):
    """P2 defendendo, barra de frame data que leva ao tick com o clique e play que começa o
    som do tick atual e anda pelo relógio."""
    import time
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    app, ctx, win, _ = gui
    sp = win.simulator
    win.show_page(sp)
    sp.p2_mode.setCurrentText("Standing guard")
    sp.run_state(200)
    t = time.perf_counter()
    while sp.sim is None and time.perf_counter() - t < 60:
        app.processEvents()
    assert sp.sim is not None, sp.info.text()
    assert "on guard" in sp.fd_label.text() and "Startup" in sp.fd_label.text()
    assert "GUARD" in " ".join(sp.events.item(i).text() for i in range(sp.events.count()))
    tl = sp.timeline
    assert len(tl.phases) == len(sp.sim.track) and tl.phases[0] == "preparo"
    row_y = lambda key: tl.RULER_H + [k for k, _ in tl.rows_def].index(key) * tl.ROW_H + tl.ROW_H // 2
    QTest.mouseClick(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(int(tl._x(5.5)), row_y("fases")))
    assert sp.slider.value() == 5 and tl.current == 5

    played = []
    monkeypatch.setattr(sp.audio, "play", lambda sim, tick: played.append(tick))
    sp.slider.setValue(0)
    sp.play_action.setChecked(True)
    t = time.perf_counter()
    while sp.slider.value() < 8 and time.perf_counter() - t < 30:
        QTest.qWait(10)
    sp.play_action.setChecked(False)
    assert sp.slider.value() >= 8 and played and played[0] == sp.sim.track[0]["t"]
    assert not sp.timer.isActive()


def test_move_timeline_drags_a_sound_and_opens_the_code(gui):
    """Arrastar o PlaySnd na linha do tempo reescreve o Time = N do gatilho e simula de novo;
    duplo clique no evento abre a linha do controlador nos Estados."""
    import time
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    app, ctx, win, _ = gui
    sp = win.simulator
    win.show_page(sp)
    sp.run_state(200)
    t = time.perf_counter()
    while sp.sim is None and time.perf_counter() - t < 60:
        app.processEvents()
    tl = sp.timeline
    sound = next(it for it in tl.data["rows"]["sons"] if not it.get("hitsound"))
    assert sound["i0"] == 1 and sp._can_drag(sound)
    hit = tl.data["rows"]["acertos"][-1]
    assert not sp._can_drag(hit)
    y = tl.RULER_H + [k for k, _ in tl.rows_def].index("sons") * tl.ROW_H + tl.ROW_H // 2
    old = sp.sim
    _drag(tl, QPoint(int(tl._x(1) + 3), y), QPoint(int(tl._x(3) + 3), y))
    ctrl, _ = sp._controller(sound)
    assert ctrl.section.get("trigger1").replace(" ", "").lower() == "time=3"
    t = time.perf_counter()
    while (sp.sim is None or sp.sim is old) and time.perf_counter() - t < 60:
        app.processEvents()
    assert next(it for it in tl.data["rows"]["sons"] if not it.get("hitsound"))["i0"] == 3
    old = sp.sim
    assert ctx.undo()                                     # Ctrl+Z volta o Time = 1 e simula de novo
    ctrl, _ = sp._controller(sound)
    assert ctrl.section.get("trigger1").replace(" ", "").lower() == "time=1"
    while (sp.sim is None or sp.sim is old) and time.perf_counter() - t < 60:
        app.processEvents()
    assert next(it for it in tl.data["rows"]["sons"] if not it.get("hitsound"))["i0"] == 1

    # duplo clique abre o código na linha do [State 200, ...] do som
    x = int(tl._x(sound["i0"]) + 3)
    QTest.mouseClick(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    QTest.mouseDClick(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    QTest.qWait(50)
    page = win.states
    assert page.isVisible()
    ed = page.current_edit()
    line = ed.textCursor().block().text()
    assert line.lower().startswith("[state 200") and "playsnd" in ed.textCursor().block().next().text().lower()


def test_image_editor_paints_fills_and_applies(gui, monkeypatch):
    """Editor de imagem embutido: balde, reta, desfazer e aplicar no sprite (mesmo eixo e paleta)."""
    import numpy as np
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from charfactory.gui import imageeditor as ie
    a = np.zeros((5, 6), np.uint8)
    a[2, :] = 3
    assert ie.flood_fill(a, 0, 0, 7) and (a[:2] == 7).all() and (a[3:] == 0).all() and (a[2] == 3).all()
    assert ie.line_points(0, 0, 3, 1) == [(0, 0), (1, 0), (2, 1), (3, 1)]

    app, ctx, win, _ = gui
    sp = win.sprites
    win.show_page(sp)
    app.processEvents()
    spr = sp.current()
    g, i, axis, pal = spr.group, spr.item, (spr.axis_x, spr.axis_y), spr.palette_index
    before = ctx.char.sprites.pixels(spr)

    def run(dlg):
        dlg.show()
        app.processEvents()
        dlg.canvas.fit()
        c = dlg.canvas
        o, z = c.origin(), c.zoom
        dlg.set_color(9)
        pt = QPoint(int(o.x() + 1.5 * z), int(o.y() + 1.5 * z))
        QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
        assert c.arr[1, 1] == 9 and dlg.modified
        dlg.undo()
        assert c.arr[1, 1] != 9 or before[spr.width + 1] == 9
        dlg.redo()
        dlg.accept()
        return dlg.result() == 1

    monkeypatch.setattr(ie.ImageEditor, "exec", run)
    sp.edit_image()
    app.processEvents()
    new = ctx.char.sprites.find(g, i)
    assert ctx.char.sprites.pixels(new)[new.width + 1] == 9
    assert (new.axis_x, new.axis_y) == axis and new.palette_index == pal
    assert ctx.undo()                               # Ctrl+Z do app volta o sprite
    assert ctx.char.sprites.pixels(ctx.char.sprites.find(g, i)) == before


def _drag(widget, a, b, mods=None):
    """Arrasto de mouse de verdade (press, move, release) entre dois pontos da tela do widget."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    mods = mods or Qt.KeyboardModifier.NoModifier
    a, b = QPoint(round(a.x()), round(a.y())), QPoint(round(b.x()), round(b.y()))
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, mods, a)
    QTest.mouseMove(widget, QPoint((a.x() + b.x()) // 2, (a.y() + b.y()) // 2))
    QTest.mouseMove(widget, b)
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, mods, b)


def test_draw_move_resize_delete_boxes_with_mouse(gui, monkeypatch):
    """A ferramenta de caixa ligada deixa arrastar e criar a caixa sem Shift (antes só os modos
    automáticos criavam caixas); a borda redimensiona, o meio move, Del apaga, Esc desliga."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from charfactory.gui.context import UndoStack
    monkeypatch.setattr(UndoStack, "MERGE_SECONDS", 0)       # cada arrasto vira um passo de desfazer
    app, ctx, win, _ = gui
    an = win.anims
    win.resize(1400, 900)
    win.show_page(an)
    an.select_action(200)
    an.frame_nav.set_index(0)
    an.delete_frame_boxes()
    app.processEvents()
    cv = an.canvas
    tools = {t.split(" (")[0]: act for t, act in an.box_tools.actions_by_tip.items()}
    atk, col = tools["Draw attack box"], tools["Draw collision box"]
    frame = lambda: ctx.char.anims.get(200).frames[0]

    _drag(cv, cv.to_screen(-30, -80), cv.to_screen(30, -10))      # sem ferramenta: não cria nada
    assert frame().clsn1 == [] and frame().clsn2 == []

    col.trigger()
    assert col.isChecked() and cv.draw_box_kind == 2
    assert cv.cursor().shape() == Qt.CursorShape.CrossCursor
    _drag(cv, cv.to_screen(-30, -80), cv.to_screen(30, -10))
    assert frame().clsn2 == [(-30, -80, 30, -10)]

    atk.trigger()                                                    # trocar de ferramenta desliga a outra
    assert atk.isChecked() and not col.isChecked() and cv.draw_box_kind == 1
    _drag(cv, cv.to_screen(40, -70), cv.to_screen(90, -50))
    assert frame().clsn1 == [(40, -70, 90, -50)] and len(frame().clsn2) == 1

    QTest.keyClick(cv, Qt.Key.Key_Escape)
    assert not atk.isChecked() and not col.isChecked() and cv.draw_box_kind is None

    _drag(cv, cv.to_screen(30, -45), cv.to_screen(45, -45))           # borda direita: redimensiona
    assert frame().clsn2 == [(-30, -80, 45, -10)]
    _drag(cv, cv.to_screen(0, -45), cv.to_screen(5, -40))             # meio: move
    assert frame().clsn2 == [(-25, -75, 50, -5)]
    col.trigger()                                                     # com a ferramenta, a borda ainda redimensiona
    _drag(cv, cv.to_screen(-25, -40), cv.to_screen(-35, -40))
    assert frame().clsn2 == [(-35, -75, 50, -5)]
    col.trigger()
    assert cv.draw_box_kind is None

    QTest.mouseClick(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     cv.to_screen(0, -40).toPoint())
    QTest.keyClick(cv, Qt.Key.Key_Delete)
    assert frame().clsn2 == [] and frame().clsn1 == [(40, -70, 90, -50)]
    assert ctx.undo() and frame().clsn2 == [(-35, -75, 50, -5)]

    statuses = []
    ctx.status.connect(statuses.append)
    an.copy_prev_boxes()                                              # frame 0: avisa em vez de nada
    assert statuses and "first frame" in statuses[-1]

    x0, y0 = frame().x, frame().y                                     # Ctrl+arrastar: offset do frame
    _drag(cv, cv.to_screen(0, -40), cv.to_screen(6, -43), Qt.KeyboardModifier.ControlModifier)
    assert (frame().x, frame().y) == (x0 + 6, y0 - 3)

    tl = an.timeline                                                  # clique na régua vai ao frame
    total = sum(tl.durations)
    px = tl.LEFT + (tl.frame_start(2) + 0.5) * (tl.width() - tl.LEFT - 12) / total
    QTest.mouseClick(tl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(px), 40))
    assert an.frame_nav.index() == 2


def test_closed_editors_stop_listening(gui, monkeypatch, mugen_dir):
    """O editor de paletas fechado seguia recarregando (e quebrava ao apagar o sprite da prévia);
    a janela de stage fechada quebrava na troca de tema."""
    import sys

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QDialog, QMessageBox

    from charfactory.gui.editors.palettes import PaletteEditor
    from charfactory.gui.stageeditor import open_window
    app, ctx, win, _ = gui
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    flush = lambda: (app.processEvents(), QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete),
                     app.processEvents())

    editors = lambda: sum(isinstance(w, PaletteEditor) for w in app.allWidgets())
    flush()
    n0 = editors()
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    win.sprites.open_advanced()
    flush()
    assert editors() == n0                       # fechado, o editor é destruído

    ed = PaletteEditor(ctx)                      # aberto, com a prévia no sprite que vai ser apagado
    ed.load()
    key = ed.preview_sprite.itemData(3)
    ed.preview_sprite.setCurrentIndex(3)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.sprites.select(*key)
    app.processEvents()
    win.sprites.delete()
    flush()
    assert ctx.char.sprites.find(*key) is None and ed.preview_sprite.findData(key) < 0
    ed.deleteLater()

    stage = next(iter(sorted((mugen_dir / "stages").glob("*.def"))), None)
    if stage is not None:
        w = open_window(ctx, str(stage), "stage")
        app.processEvents()
        w.close()
        flush()
        for name in list(ctx.themes)[:2]:
            ctx.set_theme(name)
    assert not errors, errors


def test_simulator_player_waits_for_a_simulation(gui):
    app, ctx, win, _ = gui
    sp = win.simulator
    win.show_page(sp)
    app.processEvents()
    assert not any(a.isEnabled() for a in sp.player.actions_by_tip.values())


def test_mouse_on_axis_palette_wave_trees_and_timeline(gui, monkeypatch):
    """Cliques e arrastos de verdade nos pontos de interação que só eram testados por código."""
    import time

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QColorDialog
    app, ctx, win, _ = gui
    win.resize(1400, 900)

    # Sprites: clique define o eixo; Ctrl+arrastar move a figura (o eixo anda ao contrário)
    sp = win.sprites
    win.show_page(sp)
    sp.select(0, 0)
    app.processEvents()
    cv, spr = sp.canvas, sp.current()
    sp.pick.setChecked(True)
    corner = cv.image_pos
    QTest.mouseClick(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     cv.to_screen(corner.x() + 7, corner.y() + 9).toPoint())
    spr = sp.current()
    assert (spr.axis_x, spr.axis_y) == (7, 9)
    sp.pick.setChecked(False)
    _drag(cv, cv.to_screen(0, -40), cv.to_screen(3, -38), Qt.KeyboardModifier.ControlModifier)
    spr = sp.current()
    assert (spr.axis_x, spr.axis_y) == (4, 7)

    # grade de paleta: clique seleciona, duplo clique troca a cor
    grid = sp.pal_grid
    s = grid._cell()
    QTest.mouseClick(grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(5.5 * s), int(s / 2)))
    assert grid.sel == (5, 5)
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor(1, 2, 3))
    QTest.mouseDClick(grid, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(5.5 * s), int(s / 2)))
    pal = ctx.char.sprites.palettes[ctx.char.sprites.find_palette(1, sp.selected_palette_slot())]
    assert tuple(pal.colors[5][:3]) == (1, 2, 3)

    # Sons: arrastar na onda seleciona um trecho
    so = win.sounds
    win.show_page(so)
    app.processEvents()
    wv = so.wave
    assert len(wv.samples) and wv.width() > 100
    y = wv.RULER + 40
    _drag(wv, QPoint(wv.width() // 4, y), QPoint(wv.width() // 2, y))
    a, b = sorted(wv.sel)
    assert 0 < a < b and abs(a - wv.sample_at(wv.width() // 4)) <= 1

    # Estados: clique na árvore leva à linha; duplo clique num controller insere o modelo
    st = win.states
    win.show_page(st)
    app.processEvents()
    tree = st.tree
    item = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).childCount())
    item.setExpanded(True)
    app.processEvents()
    child = item.child(0)
    tree.scrollToItem(child)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     tree.visualItemRect(child).center())
    ed = st.current_edit()
    from charfactory.gui.editors.codepages import ROLE_KEY, ROLE_LINE
    assert ed.textCursor().blockNumber() + 1 == child.data(0, ROLE_LINE)
    ctrls = st.ctrls
    ctrls.topLevelItem(0).setExpanded(True)
    ctrl = ctrls.topLevelItem(0).child(0)
    ctrls.scrollToItem(ctrl)
    assert ctrls.isVisible()
    before = ed.toPlainText()
    at = ctrls.visualItemRect(ctrl).center()       # clique e duplo clique, como a mão faz
    QTest.mouseClick(ctrls.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    QTest.mouseDClick(ctrls.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at)
    assert ed.toPlainText() != before and f"type = {ctrl.data(0, ROLE_KEY)}".lower() in ed.toPlainText().lower()

    # Simulador: depois de simular, as setas na linha do tempo trocam o tick
    sim = win.simulator
    win.show_page(sim)
    app.processEvents()
    sim.run()
    t = time.perf_counter()
    while sim.sim is None and time.perf_counter() - t < 90:
        QTest.qWait(50)
    assert sim.sim is not None and sim.play_action.isEnabled()
    label = sim.tick_label.text()
    sim.slider.setFocus()
    QTest.keyClick(sim.slider, Qt.Key.Key_Right)
    assert sim.slider.value() == 1
    t = time.perf_counter()
    while sim.tick_label.text() == label and time.perf_counter() - t < 10:
        QTest.qWait(20)
    assert sim.tick_label.text() != label


def test_image_editor_tools_with_mouse_and_keys(gui):
    """Atalhos de uma tecla e cada ferramenta com mouse de verdade: reta, retângulo, borracha,
    conta-gotas, cor secundária no botão direito, Shift+clique e desfazer."""
    import numpy as np
    from PIL import Image
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from charfactory.gui.imageeditor import ImageEditor
    app, ctx, win, _ = gui
    img = Image.new("P", (20, 16), 0)
    img.putpalette([0, 0, 0] + [i * 10 % 256 for i in range(3, 768)])
    dlg = ImageEditor(win, img, [tuple(img.getpalette()[i * 3:i * 3 + 3]) for i in range(256)], (10, 15), "teste")
    dlg.show()
    dlg.activateWindow()
    app.processEvents()
    c = dlg.canvas
    c.fit()
    o, z = c.origin(), c.zoom
    at = lambda x, y: QPoint(int(o.x() + (x + 0.5) * z), int(o.y() + (y + 0.5) * z))

    QTest.keyClick(c, Qt.Key.Key_L)
    assert c.tool == "line"
    dlg.set_color(5)
    _drag(c, at(1, 1), at(8, 1))
    assert (c.arr[1, 1:9] == 5).all() and c.arr[1, 9] == 0

    QTest.keyClick(c, Qt.Key.Key_R)
    assert c.tool == "rect"
    dlg.set_color(6, right=True)
    QTest.mousePress(c, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, at(2, 4))
    QTest.mouseMove(c, at(6, 8))
    QTest.mouseRelease(c, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, at(6, 8))
    assert c.arr[4, 2] == 6 and c.arr[8, 6] == 6 and c.arr[6, 4] == 0      # contorno, miolo vazio

    QTest.keyClick(c, Qt.Key.Key_E)
    assert c.tool == "eraser"
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(1, 1))
    assert c.arr[1, 1] == 0

    QTest.keyClick(c, Qt.Key.Key_I)
    assert c.tool == "picker"
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(4, 1))
    assert c.primary == 5

    QTest.keyClick(c, Qt.Key.Key_B)
    assert c.tool == "pencil"
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(0, 12))
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, at(5, 12))
    assert (c.arr[12, 0:6] == 5).all()                                     # Shift+clique: reta

    QTest.keyClick(c, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert (c.arr[12, 1:6] == 0).all() and c.arr[12, 0] == 5
    snapshot = c.arr.copy()
    QTest.keyClick(c, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert not np.array_equal(c.arr, snapshot)
    dlg.modified = False
    dlg.reject()


def _image_editor(win, w=24, h=20, axis=(10, 18)):
    """Editor de imagem aberto num sprite indexado vazio; devolve (dlg, tela, pixel->ponto)."""
    from PIL import Image
    from PySide6.QtCore import QPoint

    from charfactory.gui.imageeditor import ImageEditor
    img = Image.new("P", (w, h), 0)
    img.putpalette([0, 0, 0] + [(i * 37) % 256 for i in range(3, 768)])
    pal = [tuple(img.getpalette()[i * 3:i * 3 + 3]) for i in range(256)]
    dlg = ImageEditor(win, img, pal, axis, "teste")
    dlg.show()
    dlg.activateWindow()
    QApplication.processEvents()
    c = dlg.canvas
    c.fit()
    o, z = c.origin(), c.zoom
    return dlg, c, lambda x, y: QPoint(int(o.x() + (x + 0.5) * z), int(o.y() + (y + 0.5) * z))


def test_image_editor_selection_confines_paint_moves_copies_and_pastes(gui):
    """Seleção retangular com o mouse: o lápis fica preso a ela, arrastar por dentro move os
    pixels, as setas empurram, Ctrl+C / Del / Ctrl+V e Esc tira a seleção."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, ctx, win, _ = gui
    dlg, c, at = _image_editor(win)
    c.arr[5:8, 5:8] = 4
    c.refresh()
    QTest.keyClick(c, Qt.Key.Key_M)
    assert c.tool == "select"
    _drag(c, at(4, 4), at(8, 8))
    assert c.selection is not None and c.selection[4:9, 4:9].all() and c.selection.sum() == 25

    QTest.keyClick(c, Qt.Key.Key_B)
    dlg.set_color(6)
    _drag(c, at(2, 6), at(12, 6))
    assert (c.arr[6, 4:9] == 6).all() and c.arr[6, 2] == 0 and c.arr[6, 11] == 0

    QTest.keyClick(c, Qt.Key.Key_M)
    _drag(c, at(6, 6), at(9, 7))                        # arrastar por dentro: move 3 → e 1 ↓
    assert (c.arr[7, 7:12] == 6).all() and c.arr[6, 8] == 4 and c.arr[5, 5] == 0
    assert c.selection[5:10, 7:12].all() and c.selection.sum() == 25

    QTest.keyClick(c, Qt.Key.Key_Left)                  # seta empurra 1 px
    assert (c.arr[7, 6:11] == 6).all() and c.arr[7, 11] == 0

    QTest.keyClick(c, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(c, Qt.Key.Key_Delete)
    assert (c.arr == 0).all()
    QTest.keyClick(c, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert (c.arr[7, 6:11] == 6).all() and c.selection is not None
    QTest.keyClick(c, Qt.Key.Key_Escape)
    assert c.selection is None

    QTest.keyClick(c, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)   # desfaz a colagem
    assert (c.arr == 0).all()
    dlg.modified = False
    dlg.reject()


def test_image_editor_wand_outline_replace_flip_fill_and_menu(gui, monkeypatch):
    """Varinha (Shift soma), pixels soltos, contorno, trocar cor, espelhar, balde na imagem
    toda, elipse cheia e cada ação do menu Imagem disparada sem erro."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from charfactory.gui import imageeditor as ie
    app, ctx, win, _ = gui
    dlg, c, at = _image_editor(win)
    c.arr[4:10, 4:8] = 3
    c.arr[0, 20] = 5
    c.refresh()
    QTest.keyClick(c, Qt.Key.Key_W)
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(5, 5))
    assert c.selection.sum() == 24
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, at(20, 0))
    assert c.selection.sum() == 25
    QTest.keyClick(c, Qt.Key.Key_Escape)

    dlg.menu_actions["despeckle"].trigger()
    assert c.arr[0, 20] == 0 and c.arr[5, 5] == 3
    dlg.set_color(9)
    QTest.keyClick(c, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert c.arr[3, 5] == 9 and c.arr[5, 8] == 9 and c.arr[3, 3] == 0      # por fora, sem diagonais

    dlg.set_color(2, right=True)
    QTest.keyClick(c, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    assert not (c.arr == 9).any() and c.arr[3, 5] == 2

    QTest.keyClick(c, Qt.Key.Key_H, Qt.KeyboardModifier.ShiftModifier)     # espelha a imagem toda
    assert c.arr[5, 19] == 3 and c.arr[5, 4] == 0

    QTest.keyClick(c, Qt.Key.Key_G)
    QTest.mouseClick(dlg.contiguous, Qt.MouseButton.LeftButton)
    assert not c.contiguous
    dlg.set_color(7)
    c.arr[0, 0] = 2
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(0, 0))
    assert not (c.arr == 2).any() and c.arr[3, 18] == 7                   # a cor na imagem toda

    QTest.keyClick(c, Qt.Key.Key_O)
    assert c.tool == "ellipse"
    _drag(c, at(1, 12), at(9, 18), Qt.KeyboardModifier.ShiftModifier)
    assert c.arr[15, 5] == 7 and c.arr[12, 1] == 0                         # cheia, cantos de fora

    monkeypatch.setattr(ie.CanvasSizeDialog, "exec", lambda self: 0)
    for a in dlg.image_menu.actions():
        if not a.isSeparator():
            a.trigger()                                  # com e sem seleção, nada pode quebrar
    dlg.modified = False
    dlg.reject()


def test_image_editor_canvas_size_axis_onion_and_navigation(gui, monkeypatch):
    """Tamanho da tela leva o eixo junto, onion skin tem os vizinhos do grupo, PgDown grava o
    sprite e abre o seguinte na mesma janela, e Aplicar grava esse também."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from charfactory.gui import imageeditor as ie
    app, ctx, win, _ = gui
    sp = win.sprites
    win.show_page(sp)
    sp.select(0, 0)
    app.processEvents()
    first = sp.current()
    g, i, w, h, ax, ay = first.group, first.item, first.width, first.height, first.axis_x, first.axis_y
    order = [(s.group, s.item) for s in sp.sprites()]
    g2, i2 = order[order.index((g, i)) + 1]

    def run(dlg):
        dlg.show()
        dlg.activateWindow()
        app.processEvents()
        c = dlg.canvas
        assert dlg.onion_action.isEnabled() and c.onion
        QTest.keyClick(c, Qt.Key.Key_F3)
        assert c.show_onion
        dlg.set_canvas_size(5, 3, 0, 0)
        assert c.axis == (ax + 5, ay + 3) and c.arr.shape == (h + 3, w + 5)
        QTest.keyClick(c, Qt.Key.Key_PageDown)
        assert dlg.windowTitle().endswith(f"{g2},{i2}") and dlg.saved_any
        c.fit()
        o, z = c.origin(), c.zoom
        dlg.set_color(9)
        QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(int(o.x() + 0.5 * z), int(o.y() + 0.5 * z)))
        assert c.arr[0, 0] == 9
        dlg.accept()
        return 1

    monkeypatch.setattr(ie.ImageEditor, "exec", run)
    sp.edit_image()
    app.processEvents()
    a = ctx.char.sprites.find(g, i)
    assert (a.width, a.height, a.axis_x, a.axis_y) == (w + 5, h + 3, ax + 5, ay + 3)
    b = ctx.char.sprites.find(g2, i2)
    assert ctx.char.sprites.pixels(b)[0] == 9
    assert (sp.current().group, sp.current().item) == (g2, i2)


def test_image_editor_swatch_hover_highlights_and_sprite_usage_links(gui):
    """Passar o mouse numa cor acende os pixels dela e marca as usadas; a página de sprites diz
    em quais animações o sprite aparece e o link abre a animação."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    app, ctx, win, _ = gui
    dlg, c, at = _image_editor(win)
    c.arr[2, 2] = 7
    dlg._fill_swatches()
    sw = dlg.swatches
    k = sw.values.index(7)
    pos = QPoint(1 + (k % 16) * sw.CELL + 5, 1 + (k // 16) * sw.CELL + 5)
    # passar o mouse sem botão: no offscreen o QTest.mouseMove não entrega o evento de hover
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(pos), QPointF(sw.mapToGlobal(pos)),
                       Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(sw, move)
    assert c.highlight == 7 and 7 in sw.used and 8 not in sw.used
    QApplication.sendEvent(sw, QEvent(QEvent.Type.Leave))
    assert c.highlight is None
    QTest.mouseClick(sw, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, pos)
    assert c.secondary == 7
    dlg.modified = False
    dlg.reject()

    sp = win.sprites
    win.show_page(sp)
    sp.select(0, 0)
    app.processEvents()
    uses = sp.anims_using(0, 0)
    assert uses and f'href="{uses[0]}"' in sp.usage.text()
    sp.usage.linkActivated.emit(str(uses[0]))
    app.processEvents()
    assert win.current_page() is win.anims


def test_simplified_layout_menus_and_problems_panel(gui):
    """Layout enxuto: uma barra só, menu do editor ativo, ferramentas raras no '⋯' e painel
    Problemas fechado até aparecer um erro novo."""
    app, ctx, win, _ = gui
    from PySide6.QtWidgets import QToolButton
    # sem faixa de projeto, sem cabeçalho do painel esquerdo e sem aba de arquivo única
    win.show_page(win.sprites)
    app.processEvents()
    assert win.project_tabs.isVisible() and win.extra.isVisible()
    assert not win.sprites.left.header.isVisible() and not win.sprites.file_tab.isVisible()
    assert win.states.tabs.tabBarAutoHide()
    # um menu de editor por vez, antes de Ferramentas; os outros seguem na paleta de comandos
    titles = [a.text().replace("&", "") for a in win.menuBar().actions()]
    assert titles == ["Project", "Edit", "View", "Debug", "Sprites", "Tools", "Help"]
    win.show_page(win.states)
    titles = [a.text().replace("&", "") for a in win.menuBar().actions()]
    assert "States" in titles and "Sprites" not in titles
    labels = [e[0] for e in win.palette_entries()]
    assert any(lb.startswith("Sprites › Advanced palette editor") for lb in labels)
    assert any(lb.startswith("View › Sprites") for lb in labels)
    # ferramentas raras no menu '⋯' da faixa, com nome e atalho, e o atalho continua valendo
    tools = win.sprites.tools
    more = [b for b in tools.findChildren(QToolButton) if b.objectName() == "moreButton"]
    assert len(more) == 1 and tools.more_menu is not None
    in_menu = [a.text() for a in tools.more_menu.actions() if not a.isSeparator()]
    assert "Slice sprite sheet…" in in_menu and "Add several images…" in in_menu
    multi = tools.actions_by_tip["Add several images… (Ctrl+I)"]
    assert multi.shortcut().toString() == "Ctrl+I" and tools in multi.associatedObjects()
    # erro novo abre o painel na aba Problemas; avisos ficam atrás do filtro
    win.set_bottom_open(False)
    f = win.states.files[0]
    f["edit"].setPlainText(f["edit"].toPlainText() + "\n[Statedef 9400]\ntype = S\n\n"
                           "[State 9400, x]\ntype = ChangeStat\ntrigger1 = 1\n")
    win.states.apply(f)
    win.validate()
    app.processEvents()
    assert win.bottom.isVisible() and win.bottom.currentIndex() == 0
    assert "1 error" in win.st_errors.text() and win.states.issue_combo.isVisible()
    errors = sum(i.severity == "error" for i in ctx.last_issues)
    assert win.msgs.rowCount() == errors
    win.show_warnings.setChecked(True)
    assert win.msgs.rowCount() == len(ctx.last_issues)
    # os mesmos erros não reabrem o painel que o usuário fechou
    win.toggle_bottom()
    win.validate()
    assert not win.bottom.isVisible()
    # tela inicial: nada do projeto na barra
    ctx.char.dirty.clear()
    for page in win.code_pages:
        for x in page.files:
            x["edit"].document().setModified(False)
    win.close_project()
    app.processEvents()
    assert not win.project_tabs.isVisible() and not any(a.isVisible() for a in win.editor_actions)


def test_simulator_chain_field_runs_a_combo(gui):
    import time
    app, ctx, win, _ = gui
    sp = win.simulator
    win.show_page(sp)
    sp.chain.setText("400 400 410")
    sp.dist.setValue(40)
    sp.run()
    t = time.perf_counter()
    while sp.sim is None and time.perf_counter() - t < 60:
        app.processEvents()
    assert sp.sim.chain == (400, 400, 410)
    text = sp.fd_label.text()
    assert "cancel" in text and "combo of <b>2</b>" in text


def test_simulator_compares_with_a_backup_side_by_side(gui):
    """A/B: com um backup escolhido em Comparar, a simulação roda nas duas versões, com a
    mesma câmera, e mostra B ao lado de A com o resumo dela."""
    import time
    app, ctx, win, work = gui
    win.create_backup()
    sd = ctx.char.all_statedefs()[200][0]
    hit = next(c for c in sd.controllers if c.type.lower() == "hitdef")
    hit.section.set("pausetime", "30, 30")                 # A: pausa bem maior que a do backup
    ctx.changed("states")
    sp = win.simulator
    win.show_page(sp)
    labels = [sp.compare.itemText(i) for i in range(sp.compare.count())]
    assert labels[:2] == ["Nothing", "Version saved on disk"] and labels[2].startswith("Backup ")
    sp.compare.setCurrentIndex(2)
    sp.run_state(200)
    t = time.perf_counter()
    while sp.sim is None and time.perf_counter() - t < 60:
        app.processEvents()
    assert sp.sim_b is not None and sp.view_b.isVisible() and "Backup" in sp.ab_label.text()
    assert len(sp.sim.track) > len(sp.sim_b.track)          # a pausa de 30 alonga o A
    assert sp.slider.maximum() == len(sp.sim.track) - 1
    while (sp._rendering or sp._key(sp.slider.value()) not in sp.cache) and time.perf_counter() - t < 60:
        app.processEvents()
    a, b = sp.cache[sp._key(sp.slider.value())]
    assert a is not None and b is not None and a.size == b.size     # mesma câmera
    sp.compare.setCurrentIndex(0)
    sp.run_state(200)
    old = sp.sim
    while (sp.sim is None or sp.sim is old) and time.perf_counter() - t < 60:
        app.processEvents()
    assert sp.sim_b is None and not sp.view_b.isVisible()


def test_palettes12_dialog_generates_and_writes(gui, monkeypatch):
    from charfactory.gui import palettes12 as G
    app, ctx, win, _ = gui
    dlg = G.PaletteSetDialog(ctx, win)
    assert dlg.strip.count() == 12 and dlg.faixas.topLevelItemCount() == len(dlg.rngs)
    dlg.generate()
    assert dlg.touched == set(range(2, 13))
    dlg.strip.setCurrentRow(3)                                  # paleta 4
    free = next(k for k in range(dlg.faixas.topLevelItemCount())
                if dlg.faixas.topLevelItem(k).checkState(2) == G.Qt.CheckState.Unchecked)
    dlg.faixas.setCurrentItem(dlg.faixas.topLevelItem(free))
    dlg.bm.setValue(0.5)                                        # ajuste fino: só essa faixa na 4
    assert dlg.adj[4][free][2] == 0.5
    monkeypatch.setattr(G.QMessageBox, "question", lambda *a, **k: G.QMessageBox.StandardButton.Yes)
    dlg.accept()
    s = ctx.char.sprites
    i = dlg.rngs[free]["indices"][0]
    assert s.palettes[s.find_palette(1, 4)].colors[i] == tuple(dlg.colors(4)[i])
    assert ctx.undo()                                           # Ctrl+Z volta as paletas
