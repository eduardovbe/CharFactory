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
    # vários personagens: um botão com o atual (+N outros) e a lista num menu, no lugar das abas
    assert win.project_switcher.text().startswith("Kung Fu Man") and "+1" in win.project_switcher.text()
    win._fill_project_menu()
    chars = [a for a in win.project_menu.actions() if a.isCheckable()]
    assert len(chars) == 2 and chars[1].isChecked() and not chars[0].isChecked()
    chars[0].trigger()
    app.processEvents()
    assert ctx.char is ctx.projects[0]
    for c in list(ctx.projects):
        c.dirty.clear()


def test_open_and_all_themes(gui):
    app, ctx, win, _ = gui
    assert ctx.char.name == "Kung Fu Man"
    assert win.sprites.nav.count == 281
    assert win.anims.nav.count == len(ctx.char.anims.actions)   # 117 no kfm do 1.0, 118 no do 1.1
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


def test_palette_changes_show_live(gui, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog

    from charfactory.gui.editors.palettes import PaletteEditor
    app, ctx, win, _ = gui
    ed = PaletteEditor(ctx)
    ed.load()
    before = list(ed.current_palette().colors)
    shown = ed.preview.image.copy()
    # matiz/saturação/brilho: a prévia mostra a variante antes de gravar e o modelo fica igual
    ed.tabs.setCurrentIndex(ed.TAB_NEW)                          # a variante aparece na aba Nova paleta
    ed.hue.setValue(90)
    assert ed.preview.image != shown and ed.current_palette().colors == before
    assert not ed.preview_note.isHidden()
    target = ed.target.value()
    assert ctx.char.sprites.find_palette(1, target) < 0          # vem numa paleta livre
    ed.make_variant()
    app.processEvents()
    assert ed.current_slot() == target and ed.hue.value() == 0   # a nova aparece, controles zeram

    # editar uma cor repinta a cada escolha; Cancelar volta como estava
    ed.select_slot(1)
    seen = []

    def cancel(dlg):
        dlg.currentColorChanged.emit(QColor(1, 2, 3))
        seen.append((ed.current_palette().colors[5], ed.preview.image.copy()))
        return 0
    monkeypatch.setattr(QColorDialog, "exec", cancel)
    ed.edit_color(5)
    assert seen[0][0] == (1, 2, 3) and ed.current_palette().colors[5] == before[5]

    def choose(dlg):
        dlg.setCurrentColor(QColor(10, 20, 30))
        dlg.accept()
        return 1
    monkeypatch.setattr(QColorDialog, "exec", choose)
    ed.edit_color(5)
    app.processEvents()
    assert ed.current_palette().colors[5] == (10, 20, 30)

    # conta-gotas: clicar no sprite seleciona o índice do pixel
    spr = ctx.char.sprites.find(*ed.preview_sprite.currentData())
    px = ctx.char.sprites.pixels(spr)
    k = next(i for i, v in enumerate(px) if v)
    ed.pick_from_preview(k % spr.width, k // spr.width)
    assert ed.grid.sel == (px[k], px[k])
    ed.deleteLater()

    # desfazer dentro do editor (janela modal: o Ctrl+Z da principal não chega lá)
    ed = PaletteEditor(ctx)
    ed.load()
    orig = list(ed.current_palette().colors)
    assert not ed.revert_btn.isEnabled()
    ed.effect("grayscale")
    app.processEvents()
    assert ed.current_palette().colors != orig and ed.undo_btn.isEnabled()
    ed.undo()
    app.processEvents()
    assert ed.current_palette().colors == orig and ed.redo_btn.isEnabled()
    ed.redo()
    app.processEvents()
    gray = list(ed.current_palette().colors)
    assert gray != orig
    ed.effect("invert")
    app.processEvents()
    ed.revert()                                  # tudo como ao abrir, numa vez só…
    app.processEvents()
    assert ed.current_palette().colors == orig and not ed.revert_btn.isEnabled()
    ed.undo()                                    # …e a volta também se desfaz
    app.processEvents()
    assert ed.current_palette().colors != orig
    ed.deleteLater()

    # painel do editor de sprites: escolher outra paleta mostra o sprite com ela (antes voltava)
    sp = win.sprites
    sp.select(0, 0)
    assert sp.use_sprite_pal.isChecked()
    sp.pal_nav.set_index(2)
    assert not sp.use_sprite_pal.isChecked() and sp.palette_key() == sp.char_palettes()[2].item


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


def test_drop_images_become_sprites(gui, tmp_path, monkeypatch):
    app, ctx, win, _ = gui
    from PIL import Image

    from charfactory.gui.spriteimport import ImportSpritesDialog
    p = tmp_path / "novo.png"
    Image.new("RGBA", (20, 30), (200, 40, 40, 255)).save(p)
    win.show_page(win.sprites)
    win.sprites.select(0, 0)
    before = len(ctx.char.sprites.sprites)
    # soltar imagens abre a janela de importar (aqui: Importar direto, com o que ela sugere)
    monkeypatch.setattr(ImportSpritesDialog, "exec", lambda dlg: (dlg.do_import(), dlg.result is not None)[1])
    win.import_dropped_images([p])
    assert len(ctx.char.sprites.sprites) == before + 1
    cur = win.sprites.current()
    assert cur.group == 0 and cur.width == 20


def test_sheet_slicer_edits_boxes_in_the_preview(gui, tmp_path):
    """Recorte de sheet: as caixas detectadas se movem, ganham uma nova e perdem uma na prévia,
    o eixo arrastado fica, Ctrl+Z volta, e o que se importa é o que está na tela."""
    from PIL import Image
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest

    from charfactory.gui.sheetslicer import SheetDialog
    app, ctx, win, _ = gui
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    px = img.load()
    for x0, color in ((20, (200, 0, 0)), (100, (0, 150, 0))):
        for x in range(x0, x0 + 30):
            for y in range(30, 80):
                px[x, y] = color
    for x in range(160, 180):                # terceiro sprite, mais baixo
        for y in range(60, 80):
            px[x, y] = (0, 0, 200)
    p = tmp_path / "sheet.png"
    img.save(p)
    dlg = SheetDialog(ctx)
    dlg.resize(1100, 700)
    dlg.show()
    dlg.group.setValue(7300)
    dlg.path.setText(str(p))
    assert dlg.detect_now(force=True)
    v = dlg.view
    v.fit()
    assert [(s.group, s.item) for s in v.slices] == [(7300, 0), (7300, 1), (7300, 2)]
    # arrasta a 2ª caixa 10 px de imagem para a direita
    c = v._srect(v.slices[1]).center()
    to = c + QPointF(10 * v.zoom, 0)
    QTest.mousePress(v, Qt.MouseButton.LeftButton, pos=c.toPoint())
    QTest.mouseMove(v, to.toPoint())
    QTest.mouseRelease(v, Qt.MouseButton.LeftButton, pos=to.toPoint())
    assert v.slices[1].left == 110 and dlg.edited
    dlg.undo()
    assert v.slices[1].left == 100
    # apaga a 3ª e eixo manual na 1ª
    v.select(2)
    QTest.keyClick(v, Qt.Key.Key_Delete)
    assert len(v.slices) == 2
    v.select(0)
    a = v._saxis(v.slices[0]).toPoint()
    up = a + QPoint(0, -round(20 * v.zoom))
    QTest.mousePress(v, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(v, up)
    QTest.mouseRelease(v, Qt.MouseButton.LeftButton, pos=up)
    assert v.slices[0].axis_y == 29 and v.slices[0].extra.get("manual_axis")
    dlg.axis.setCurrentIndex(dlg.axis.findData("center"))   # o eixo manual não muda
    assert v.slices[0].axis_y == 29 and v.slices[1].axis_y == 25
    # mudar a tolerância não desfaz as edições
    dlg.tol.setValue(20)
    dlg.detect_now()
    assert len(v.slices) == 2
    dlg.run_import()
    s = ctx.char.sprites
    assert s.find(7300, 0).axis_y == 29 and s.find(7300, 1) is not None and s.find(7300, 2) is None
    ctx.char.dirty.clear()


def test_import_dialog_modes_preview_and_background(gui, tmp_path):
    """A janela de importar: paleta do personagem avisa quando as cores mudam muito; paleta
    própria guarda as cores com o fundo no índice 0; cores reais não reduzem nada."""
    import numpy as np
    from PIL import Image

    from charfactory.gui.spriteimport import ImportSpritesDialog
    app, ctx, win, _ = gui
    a = np.zeros((40, 30, 3), np.uint8)
    a[:] = (0, 255, 0)                                  # fundo verde
    a[5:35, 8:22] = (40, 200, 255)                      # azul-claro: longe das cores do kfm
    p = tmp_path / "efeito.png"
    Image.fromarray(a, "RGB").save(p)
    dlg = ImportSpritesDialog(ctx, [str(p)], 7200, 0)
    dlg.r_char.setChecked(True)
    dlg.update_preview()
    assert "change a lot" in dlg.info.text()
    dlg.r_own.setChecked(True)
    assert "fits in one palette" in dlg.info.text() and not dlg.strip.isHidden()
    dlg.r_true.setChecked(True)
    assert "kept as they are" in dlg.info.text() and not dlg.dither.isEnabled()
    dlg.r_own.setChecked(True)
    dlg.do_import()
    s = ctx.char.sprites
    spr = s.find(7200, 0)
    assert dlg.result and spr is not None and s.palettes[spr.palette_index].colors[:2] == [(0, 255, 0), (40, 200, 255)]
    assert ctx.settings["sprite_import"]["mode"] == "own"     # a próxima abre igual
    dlg.deleteLater()
    ctx.char.dirty.clear()


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
    # Clássico: todos os estados por número; a escolha fica à vista
    assert [page.view_combo.itemData(k) for k in range(page.view_combo.count())] == ["grouped", "classic"]
    page.view_combo.setCurrentIndex(page.view_combo.findData("classic"))
    page.view_combo.activated.emit(page.view_combo.currentIndex())
    nums = [int(page.tree.topLevelItem(k).text(0).split()[0]) for k in range(page.tree.topLevelItemCount())
            if page.tree.topLevelItem(k).text(0).split()[0].lstrip("-").isdigit()]
    assert ctx.settings["states_view"] == "classic" and nums == sorted(nums) and len(nums) > 20
    assert "Specials" not in [page.tree.topLevelItem(k).text(0) for k in range(page.tree.topLevelItemCount())]
    ctx.settings["states_view"] = "flat"                    # quem tinha a antiga "ordem do arquivo"
    assert page.view_mode() == "classic"
    page.set_view_mode("grouped")
    assert page.view_combo.currentData() == "grouped"
    # cursor dentro do [Statedef 200] -> prévia toca a anim 200
    ed = page.current_edit()
    ln = next(i for i, l in enumerate(ed.toPlainText().split("\n"), 1) if l.strip().lower().startswith("[statedef 200]"))
    ed.goto_line(ln + 1)
    page.sync_to_cursor()
    assert page.preview.number is None             # fechada: não desenha
    page.preview_section.head.setChecked(True)     # abrir mostra a ação do estado do cursor
    page.preview._load()
    import time
    t = time.perf_counter()
    while page.preview.loading and time.perf_counter() - t < 30:   # desenha fora da interface
        app.processEvents()
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


def test_simulator_state_list_follows_the_model(gui):
    """Um estado ou golpe novo (aplicado, ainda não salvo) entra na lista do simulador na hora,
    sem trocar o estado escolhido; o botão de recarregar refaz a lista do mesmo jeito."""
    from charfactory.formats import cmd, cns, mtext
    app, ctx, win, _ = gui
    sp, c = win.simulator, ctx.char
    sp.run_state(200)
    sp.play_action.setChecked(False)
    ids = lambda: [sp.state.itemData(i) for i in range(sp.state.count())]   # noqa: E731
    assert 1777 not in ids() and 1888 not in ids()
    st = c.states[0]
    c.states[0] = cns.States(mtext.parse(st.doc.to_text() + "\n[Statedef 1777]\ntype = S\n\n"
                                         "[State 1777, fim]\ntype = ChangeState\ntrigger1 = 1\nvalue = 0\n"),
                             st.path)
    ctx.changed("states")
    assert 1777 in ids() and sp.current_state() == 200
    c.__dict__["commands"] = cmd.Commands(mtext.parse(c.commands.doc.to_text()
                                                      + "\n[State -1, novo]\ntype = ChangeState\nvalue = 1888\n"
                                                        "trigger1 = command = \"x\"\n"), c.commands.path)
    sp.state.clear()
    sp.reload_states.click()                    # o botão sozinho também acha o golpe novo
    assert 1888 in ids() and "★" in sp.state.itemText(sp.state.findData(1888))


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
    assert ctx.char.sprites.find(*key) is None and ed._find_sprite(key) < 0
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
    monkeypatch.setattr(QColorDialog, "exec", lambda dlg: (dlg.setCurrentColor(QColor(1, 2, 3)), dlg.accept(), 1)[-1])
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
    from charfactory.gui import ctrldialog          # a janela de parâmetros aceita como veio
    monkeypatch.setattr(ctrldialog.ControllerDialog, "exec", lambda self: self.accept_text() or 1)
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
    assert win.project_switcher.isVisible() and win.extra.isVisible()
    assert not win.sprites.left.header.isVisible() and not win.sprites.file_tab.isVisible()
    assert win.states.tabs.tabBarAutoHide()
    # um menu de editor por vez, antes de Ferramentas; os outros seguem na paleta de comandos
    titles = [a.text().replace("&", "") for a in win.menuBar().actions()]
    assert titles == ["Project", "Edit", "View", "Debug", "Sprites", "Tools", "AI", "Help"]
    win.show_page(win.states)
    titles = [a.text().replace("&", "") for a in win.menuBar().actions()]
    assert "States" in titles and "Sprites" not in titles
    labels = [e[0] for e in win.palette_entries()]
    assert any(lb.startswith("Sprites › Palette editor") for lb in labels)
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
    assert not win.project_switcher.isVisible() and not any(a.isVisible() for a in win.editor_actions)


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


def test_palette_editor_generates_the_12_palettes(gui, monkeypatch):
    """As 12 paletas por faixas ficam numa aba do editor de paletas: gerar, ajustar uma faixa numa
    paleta (na hora, com Desfazer) e o lugar vazio da tira levando à aba Nova paleta."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QMessageBox

    from charfactory.gui.editors.palettes import PaletteEditor
    app, ctx, win, _ = gui
    ed = PaletteEditor(ctx)
    ed.load()
    sff = lambda: ctx.char.sprites                               # o Desfazer troca o objeto
    assert ed.strip.count() == 12 and ed.bands.topLevelItemCount() == len(ed.rngs) > 0
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    ed.tabs.setCurrentIndex(ed.TAB_SET)
    ed.generate_ideas()
    ed.generate()
    app.processEvents()
    assert all(sff().find_palette(1, n) >= 0 for n in range(2, 13)) and ed.current_slot() == 2
    assert ctx.undo() and sff().find_palette(1, 12) < 0               # gerar é um passo só
    assert ctx.redo()
    app.processEvents()

    ed.select_slot(4)
    free = next(k for k in range(ed.bands.topLevelItemCount())
                if ed.bands.topLevelItem(k).checkState(2) == Qt.CheckState.Unchecked)
    ed.bands.setCurrentItem(ed.bands.topLevelItem(free))
    i = next(x for x in ed.rngs[free]["indices"] if any(sff().palettes[sff().find_palette(1, 4)].colors[x]))
    pal = lambda n: sff().palettes[sff().find_palette(1, n)].colors
    before4, before5 = pal(4)[i], list(pal(5))
    ed.bm.setValue(40)                                            # só essa faixa, só na paleta 4
    ed.bm.setValue(30)
    app.processEvents()
    assert pal(4)[i] != before4 and pal(5) == before5
    assert ctx.undo() and pal(4)[i] == before4                    # o arraste todo volta de uma vez
    app.processEvents()

    ed.select_slot(12)
    ed.delete_palette()
    app.processEvents()
    assert sff().find_palette(1, 12) < 0
    ed.select_slot(12)                                            # vazio: vai criar nele
    assert ed.tabs.currentIndex() == ed.TAB_NEW and ed.target.value() == 12
    ed.deleteLater()

    # o painel de paletas do editor de sprites abre o mesmo editor, na aba pedida
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda dlg: opened.append(dlg.findChild(PaletteEditor).tabs.currentIndex()) or 0)
    win.sprites.open_palettes12()
    win.sprites.new_palette()
    win.sprites.open_advanced()
    assert opened == [PaletteEditor.TAB_SET, PaletteEditor.TAB_NEW, PaletteEditor.TAB_COLORS]


def test_palette_editor_opens_on_the_sprite_and_its_palette(gui, monkeypatch):
    """O editor de paletas abre sempre no sprite da tela e na paleta padrão dele (mesmo com outra
    escolhida no painel); um efeito com paleta própria abre nela; na página de animações, no
    sprite do frame."""
    from PySide6.QtWidgets import QDialog

    from charfactory.gui.editors.palettes import PaletteEditor
    app, ctx, win, _ = gui
    sp = win.sprites
    win.show_page(sp)
    seen = []

    def grab(dlg):
        ed = dlg.findChild(PaletteEditor)
        seen.append((ed.current_slot(), ed.preview_sprite.currentData(), ed.tabs.isTabEnabled(ed.TAB_SET)))
        return 0
    monkeypatch.setattr(QDialog, "exec", grab)
    sp.select(200, 1)
    sp.pal_nav.set_index(2)                       # paleta 3 escolhida no painel
    sp.open_advanced()
    sp.use_sprite_pal.setChecked(True)
    sp.select(9000, 1)                            # retrato: paleta própria 9000,1
    app.processEvents()
    sp.open_advanced()
    assert seen[0] == (1, (200, 1), True)
    assert seen[1] == ((9000, 1), (9000, 1), False)
    win.show_page(win.anims)
    f = win.anims.frame()
    win.open_palette_editor()
    assert seen[2][1] == (f.group, f.item)


def test_all_palettes_list_and_color_effects(gui):
    """Painel Paletas › Todas as paletas do SFF: o slider passa por todas (como no FFS); escolher
    uma leva a um sprite que a usa e a grade edita ela. No editor de paletas, Azul → Vermelho
    muda só os tons azuis e é um passo do Desfazer."""
    from charfactory.gui.editors.palettes import PaletteEditor
    app, ctx, win, _ = gui
    sp = win.sprites
    win.show_page(sp)
    s = ctx.char.sprites
    sp.pal_scope.setCurrentIndex(sp.pal_scope.findData("all"))
    assert str(len(s.palettes)) in sp.pal_scope.currentText() and sp.pal_nav.count == len(s.palettes)
    k = s.find_palette(9000, 1)
    sp.pal_nav.set_index(k)
    assert sp.current().palette_index == k and sp.palette_key() == (9000, 1, k)
    assert "9000,1" in sp.pal_label.text()
    sp.select(0, 0)                               # o slider segue a paleta do sprite
    assert sp.pal_nav.index() == sp.current().palette_index
    sp.pal_scope.setCurrentIndex(sp.pal_scope.findData("char"))
    assert sp.pal_nav.count == len(sp.char_palettes()) and ctx.settings["sprite_pal_scope"] == "char"

    ed = PaletteEditor(ctx)
    ed.load()
    p = s.palettes[s.find_palette(1, 1)]
    blue = [i for i, c in enumerate(p.colors) if i and c[2] > c[0] + 60 and c[2] > c[1] + 30]
    before = list(p.colors)
    assert blue
    ed.recolor("blue", "red")
    p = s.palettes[s.find_palette(1, 1)]
    assert all(p.colors[i][0] > p.colors[i][2] for i in blue)
    assert sum(1 for a, b in zip(before, p.colors) if a != b) < len(before) // 2
    ed.undo()
    s = ctx.char.sprites                          # o Desfazer troca o objeto
    assert s.palettes[s.find_palette(1, 1)].colors == before
    ed.deleteLater()
    ctx.char.dirty.clear()


def test_palette_editor_uses_a_ready_set_and_fixes_the_def(gui):
    """12 paletas: um conjunto pronto de cada esquema com prévia; usar um grava 2 a 12, as que já
    existiam vão para 13 em diante sem mudar os sprites que as usam, o .def é acertado e um
    Ctrl+Z volta tudo junto."""
    from charfactory.gui.editors.palettes import PaletteEditor
    from charfactory.tools import palettes12 as P
    app, ctx, win, _ = gui
    keymap = ctx.char.definition.section("Palette Keymap")
    for b in P.BUTTONS:
        keymap.set(b, "1")                              # como no Jotaro: todo botão na paleta 1
    ctx.char.definition.section("Info").remove("pal.defaults")
    ctx.changed("def")                                  # como uma edição feita na aba Definições
    sff = lambda: ctx.char.sprites
    # um retrato com a 1,3 como paleta própria: não pode mudar de cor
    portrait = len(sff().sprites) - 1
    old_index = sff().sprites[portrait].palette_index
    sff().sprites[portrait].palette_index = sff().find_palette(1, 3)
    look = sff().image(sff().sprites[portrait]).tobytes()
    ed = PaletteEditor(ctx)
    ed.load()
    assert not ed.map_row.isHidden() and "2, 3, 4, 5, 6" in ed.map_note.text()
    before = {n: list(sff().palettes[sff().find_palette(1, n)].colors) for n in range(1, 7)}
    ed.tabs.setCurrentIndex(ed.TAB_SET)
    app.processEvents()
    assert ed.results.isHidden() and ed.sets.count() == 0          # as ideias só aparecem depois de gerar
    assert set(ed.chosen_styles()) == set(P.DEFAULT_STYLES)
    ed.generate_ideas()
    assert not ed.results.isHidden() and ed.sets.item(0).text() == P.SCHEME_TITLES["mistura"]
    assert ed.sets.count() == len(P.candidate_sets(ed.rngs, styles=ed.chosen_styles()))
    assert not ed.sets.item(0).icon().isNull()
    assert "2–6" in ed.gen_note.text() and "13–17" in ed.gen_note.text()
    ed.sets.setCurrentRow(1)
    assert not ed.preview_note.isHidden() and ed.sets.item(1).text() in ed.preview_note.text()
    ed.strength.setCurrentIndex(2)                       # mudar uma opção pede para gerar de novo
    assert ed.results.isHidden()
    ed.generate_ideas()
    ed.sets.setCurrentRow(1)
    ed.generate()
    app.processEvents()
    assert all(sff().find_palette(1, n) >= 0 for n in range(1, 18))
    assert all(sff().palettes[sff().find_palette(1, n + 11)].colors == before[n] for n in range(2, 7))
    assert sff().palettes[sff().find_palette(1, 1)].colors == before[1]
    assert sff().palettes[sff().find_palette(1, 2)].colors != before[2]
    assert sff().image(sff().sprites[portrait]).tobytes() == look        # o retrato não mudou
    m = P.def_mapping(ctx.char)
    assert not m["problems"] and m["keymap"]["c"] == 6
    assert ed.map_row.isHidden()
    ed.undo()                                           # um clique: paletas e .def voltam
    app.processEvents()
    assert sff().find_palette(1, 13) < 0
    assert sff().palettes[sff().find_palette(1, 2)].colors == before[2]
    assert P.def_mapping(ctx.char)["keymap"]["c"] == 1 and not ed.map_row.isHidden()
    ed.fix_def()                                        # o botão do aviso
    assert not P.def_mapping(ctx.char)["problems"]
    sff().sprites[portrait].palette_index = old_index
    ed.deleteLater()
    ctx.char.dirty.clear()


def test_run_in_mugen_or_ikemen_from_the_debug_menu(gui, monkeypatch):
    """Depurar › Rodar no MUGEN / no Ikemen GO: o executável certo, o kfm pelo caminho que cada
    motor aceita e a saída do console do Ikemen no painel do motor."""
    import time
    from PySide6.QtWidgets import QMessageBox
    from charfactory.gui import app as app_mod
    app, ctx, win, work = gui
    root = work.parent.parent
    (root / "mugen.exe").write_bytes(b"")
    ikemen = root / "ikemen"
    (ikemen / "chars").mkdir(parents=True)
    (ikemen / "data").mkdir()
    (ikemen / "Ikemen_GO.exe").write_bytes(b"")
    ctx.settings["ikemen_dir"] = str(ikemen)
    calls = []

    class FakeProc:
        pid = 42

        def __init__(self, args, **kw):
            calls.append((args, kw))
            self.stdout = iter(["WARNING: Kung Fu Man's state 1027: Unknown parameter(s): x, y\n"])

        def poll(self):
            return 0
    monkeypatch.setattr(app_mod.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    names = {a.text().replace("&", ""): a for m in win.menuBar().actions() if m.menu() for a in m.menu().actions()}
    names["Run in Ikemen GO"].trigger()
    names["Run in MUGEN"].trigger()
    until = time.time() + 5
    while time.time() < until and win.engine_out.toPlainText().count("closed") < 2:
        app.processEvents()
    (ik_args, ik_kw), (mu_args, _) = calls
    assert ik_args[0] == str(ikemen / "Ikemen_GO.exe") and ik_args[1] == (work / "kfm.def").as_posix()
    assert ik_kw["cwd"] == ikemen
    assert mu_args[0] == str(root / "mugen.exe") and mu_args[1] == "kfm/kfm.def"
    out = win.engine_out.toPlainText()
    assert "Unknown parameter(s): x, y" in out and "Ikemen GO closed" in out and "MUGEN closed" in out


def test_find_ranks_the_exact_number_first():
    from charfactory.gui import quickfind
    labels = ["1200  Palm", "200  Light punch", "2000  Super", "210  Strong punch"]
    ranked = sorted(labels, key=lambda lb: -(quickfind.score("200", lb, "") or -1))
    assert ranked[:2] == ["200  Light punch", "2000  Super"]
    assert quickfind.score("200,3", "200,3", "") > quickfind.score("200,3", "200,30", "") or \
        quickfind.score("200,3", "200,30", "") is None
    assert quickfind.score("200", "200,3", "") > quickfind.score("200", "2001,0", "")
    assert quickfind.score("punch", "200  Light punch", "") is not None


def test_find_searches_only_the_open_tab(gui, monkeypatch):
    app, ctx, win, _ = gui
    from PySide6.QtCore import Qt
    from charfactory.gui import dialogs
    seen = []
    monkeypatch.setattr(dialogs.CommandPalette, "exec", lambda self: seen.append(self) or 0)

    def search(page, text):
        win.show_page(page)
        app.processEvents()
        win.goto_anything()
        pal = seen[-1]
        pal.edit.setText(text)
        return pal, [pal.entries[pal.list.item(k).data(Qt.ItemDataRole.UserRole)][0] for k in range(pal.list.count())]

    pal, rows = search(win.anims, "210")
    assert rows[0].startswith("210 ")
    pal.run(pal.list.item(0))
    assert win.anims.action().number == 210
    s = win.sprites.sprites()[3]
    pal, rows = search(win.sprites, f"{s.group},{s.item}")
    assert rows[0] == f"{s.group},{s.item}"
    pal.run(pal.list.item(0))
    assert (win.sprites.sprites()[win.sprites.nav.index()].group, win.sprites.sprites()[win.sprites.nav.index()].item) == (s.group, s.item)
    pal, rows = search(win.states, "1000")
    assert rows[0].startswith("1000")
    pal.run(pal.list.item(0))
    app.processEvents()
    assert "statedef 1000" in win.current_code_edit().textCursor().block().text().lower()
    pal, rows = search(win.states, "VelSet")                    # o texto do arquivo, no fim
    assert "VelSet" in rows[-1] and "text" in pal.entries[pal.list.item(pal.list.count() - 1).data(Qt.ItemDataRole.UserRole)][1]
    pal, rows = search(win.cmds, "QCF")
    assert rows and all("QCF" in r or "qcf" in r.lower() or "VelSet" not in r for r in rows)
    snd = win.sounds.sounds()[2]
    pal, rows = search(win.sounds, f"{snd.group},{snd.number}")
    assert rows[0] == f"{snd.group},{snd.number}"
    pal, rows = search(win.simulator, "200")
    assert rows and rows[0].startswith("200")
    win.show_page(win.defs)                                    # Definições: só o texto
    n = len(seen)
    win.goto_anything()
    assert len(seen) == n and win.defs.find_bar.isVisible()
    win.show_page(win.states)                                  # Ctrl+F no código: a barra de busca
    n = len(seen)
    win.find_text()
    assert len(seen) == n and win.states.find_bar.isVisible()


def test_run_profiles_dialog_edits_previews_and_runs(gui, monkeypatch):
    """Perfis: um modelo novo entra na lista, a linha de comando acompanha o que se edita, o que
    o MUGEN não aceita fica de fora e Salvar e rodar roda com o perfil escolhido."""
    from charfactory.gui.runprofiles import RunProfilesDialog, presets
    app, ctx, win, work = gui
    ctx.settings["mugen_dir"] = str(work.parents[1])
    dlg = RunProfilesDialog(ctx, win)
    label, values = presets()[1]                           # testar supers
    dlg.add(label, values)
    assert dlg.current().name == label and dlg.max_power.isChecked()
    dlg.side[2]["life"].setValue(500)
    assert "-p2.life 500" in dlg.preview.text()
    dlg.engine.setCurrentIndex(dlg.engine.findData("mugen"))
    assert "-maxpowermode" not in dlg.preview.text() and "Ikemen GO only" in dlg.warn.text()
    ran = []
    monkeypatch.setattr(win, "run_mugen", lambda engine=None, profile=None: ran.append(profile))
    dlg.run_requested.connect(lambda prof: win.run_mugen(profile=prof))
    dlg._run()
    assert ran and ran[0].name == label and ran[0].p2_life == 500
    assert ctx.settings["run_profile"] == label and len(ctx.settings["run_profiles"]) == 2


def test_import_dialog_embedded_palette_and_big_image(gui, tmp_path):
    """Imagem indexada: a paleta embutida vem marcada como recomendada; imagem grande sugere
    reduzir, a prévia usa uma cópia menor e a importação não trava a janela."""
    import time

    import numpy as np
    from PIL import Image

    from charfactory.gui.spriteimport import ImportSpritesDialog
    app, ctx, win, _ = gui
    a = np.zeros((40, 30), np.uint8)
    a[5:35, 5:25] = 9
    im = Image.fromarray(a, "P")
    im.putpalette([0, 0, 0] + [200, 10, 10] * 255)
    p = tmp_path / "idx.png"
    im.save(p)
    dlg = ImportSpritesDialog(ctx, [str(p)], 7300, 0)
    assert dlg.mode_name() == "embedded" and "recommended" in dlg.r_emb.text().lower()
    dlg.do_import()
    spr = ctx.char.sprites.find(7300, 0)
    assert spr is not None and ctx.char.sprites.palettes[spr.palette_index].colors[9] == (200, 10, 10)
    big = tmp_path / "big.png"
    rgb = np.random.default_rng(0).integers(0, 256, (2400, 1600, 3), dtype=np.uint8)
    Image.fromarray(rgb, "RGB").save(big)
    t = time.perf_counter()
    dlg = ImportSpritesDialog(ctx, [str(big)], 7301, 0)
    assert time.perf_counter() - t < 20
    assert dlg.scale.value() < 100 and not dlg.size_note.isHidden()      # já sugere a altura do char
    dlg.scale.setValue(100)
    assert not dlg.reduced.isHidden()
    dlg.r_true.setChecked(True)
    dlg.do_import()
    while dlg.result is None and time.perf_counter() - t < 60:
        app.processEvents()
    assert ctx.char.sprites.find(7301, 0) is not None
    ctx.char.dirty.clear()


def test_grammar_documents_every_parameter():
    """Cada parâmetro traz o texto da doc, o padrão e as opções; o Helper ganha os size.*."""
    from charfactory import validate
    g = validate.grammar()["controllers"]
    explod = g["explod"]["params"]
    assert [c[0] for c in explod["postype"]["choices"]][:2] == ["p1", "p2"] and explod["postype"]["default"] == "p1"
    assert "offset" in explod["pos"]["doc"] and explod["removetime"]["default"] == "-2"
    helper = g["helper"]["params"]
    assert {"size.xscale", "size.height", "size.head.pos"} <= set(helper)
    assert "root's CNS" in helper["size.xscale"]["doc"]          # "See below." herda o texto seguinte
    documented = sum(1 for c in g.values() for p in c["params"].values() if p.get("doc"))
    assert documented == sum(len(c["params"]) for c in g.values())
    # obsoleto é só o controller que a doc diz que é, não quem tem um parâmetro obsoleto
    assert {k for k, c in g.items() if c["deprecated"]} == {"gamemakeanim", "hitvelset", "makedust"}
    assert helper["helpertype"]["deprecated"] and explod["supermove"]["deprecated"]
    # todo parâmetro só do IKEMEN GO que a janela mostra tem descrição
    from charfactory import sctrldocs
    from charfactory.gui.ctrldialog import COMMON, ikemen_params
    docs = sctrldocs.ikemen_docs()
    missing = [f"{k}.{p}" for k, c in g.items() for p in ikemen_params().get(k, {}).get("params", [])
               if p not in c["params"] and p not in COMMON and not docs.get(k, {}).get(p, {}).get("doc")]
    assert not missing, missing[:10]
    assert "z velocity" in docs["hitdef"]["air.velocity"]["doc"]       # nota do IKEMEN num parâmetro do MUGEN


def test_controller_dialog_lists_every_parameter_and_edits_in_place(gui):
    """A janela mostra todos os parâmetros (e os do IKEMEN à parte), digitar marca o parâmetro,
    os não usados podem ir comentados, e editar um [State] mantém triggers, comentários e o que
    a janela não conhece."""
    from charfactory.gui.ctrldialog import ControllerDialog, parse_block
    app, ctx, win, _ = gui
    dlg = ControllerDialog(ctx, "Explod", 1000)
    assert {"anim", "pos", "postype", "removetime", "scale", "ownpal"} <= set(dlg.rows)
    assert dlg.rows["anim"]["required"] and dlg.rows["anim"]["check"].isChecked()
    assert any(r["spec"].get("ikemen") for r in dlg.rows.values())          # angle, layerno…
    assert "Missing a value: anim" in dlg.missing.text()
    dlg.rows["anim"]["edit"].setEditText("7000")
    dlg.rows["pos"]["edit"].setText("40, -60")
    assert dlg.rows["pos"]["check"].isChecked()                            # digitar já marca
    dlg.show_help("postype")
    assert "p2" in dlg.help.toPlainText() and "default" in dlg.help.toPlainText().lower()
    dlg.comment_rest.setChecked(True)
    text = dlg.text()
    assert text.startswith("[State 1000, Explod]\ntype = Explod\ntrigger1 = Time = 0\n")
    assert "anim = 7000" in text and "pos = 40, -60" in text and ";removetime = -2" in text
    assert "angle" not in text                       # os do IKEMEN não entram comentados
    dlg.comment_rest.setChecked(False)
    assert ";removetime" not in dlg.text()

    lines = ["[Statedef 200]", "type = S", "", "[State 200, spark]  ; faísca", "type = Explod",
             "triggerall = var(1)", "trigger1 = AnimElem = 3", "; perto da mão", "anim = 7001",
             "ID = 5  ; o id", "pos = 10, -20", "zzz = 1", "", "[State 200, end]", "type = ChangeState"]
    start, last, data = parse_block(lines, 8)
    assert (start, last) == (3, 11) and data["type"] == "Explod" and data["label"] == "spark"
    edit = ControllerDialog(ctx, "Explod", 200, existing=data)
    assert edit.rows["id"]["check"].isChecked() and edit.value("pos") == "10, -20"
    edit.rows["pos"]["edit"].setText("12, -20")
    edit.rows["removetime"]["check"].setChecked(True)
    out = edit.text().split("\n")
    assert out[:4] == ["[State 200, spark]", "type = Explod", "triggerall = var(1)", "trigger1 = AnimElem = 3"]
    assert "ID = 5  ; o id" in out and "pos = 12, -20" in out and "removetime = -2" in out
    assert "zzz = 1" in out and "; perto da mão" in out


def test_controller_dialog_from_the_states_page(gui, monkeypatch):
    """Inserir pelo painel e Ctrl+Shift+Espaço no [State] trocam o bloco pelo resultado."""
    from charfactory.gui import ctrldialog
    app, ctx, win, _ = gui
    win.show_page(win.states)
    app.processEvents()
    ed = win.states.current_edit()
    lines = ed.toPlainText().split("\n")
    k = next(i for i, t in enumerate(lines) if t.strip().lower() == "type = velset")
    ed.goto_line(k + 1)

    def fake_exec(self):
        if "x" in self.rows:
            self.rows["x"]["edit"].setText("9")
        self.accept_text()
        return 1
    monkeypatch.setattr(ctrldialog.ControllerDialog, "exec", fake_exec)
    win.states.edit_controller()
    block = ed.toPlainText().split("\n")
    start = next(i for i in range(k, -1, -1) if block[i].lstrip().startswith("["))
    end = next(i for i in range(start + 1, len(block)) if block[i].lstrip().startswith("["))
    assert "x = 9" in block[start:end] and block[start + 1].lower() == "type = velset"
    n = ed.blockCount()
    win.states.insert_controller("PlaySnd")
    assert ed.blockCount() > n and "type = PlaySnd" in ed.toPlainText()


def test_find_bar_searches_any_text_as_you_type(gui):
    """Ctrl+F nas abas de código: barra embutida que marca tudo, conta, anda e substitui."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    app, ctx, win, _ = gui
    win.show_page(win.states)
    app.processEvents()
    ed = win.states.current_edit()
    bar = win.states.find_bar
    win.find_text()
    assert bar.isVisible()
    total = ed.toPlainText().lower().count("velset")
    bar.find_edit.setText("velset")
    assert len(bar.hits) == total > 1 and bar.count.text() == f"{bar.current + 1} of {total}"
    assert ed.textCursor().selectedText().lower() == "velset" and ed.search_hits
    first = bar.current
    QTest.keyClick(bar.find_edit, Qt.Key.Key_Return)
    assert bar.current == (first + 1) % total
    QTest.keyClick(bar.find_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert bar.current == first
    bar.toggles["case"].setChecked(True)
    assert len(bar.hits) == ed.toPlainText().count("velset")
    bar.toggles["case"].setChecked(False)
    bar.toggles["regex"].setChecked(True)
    bar.find_edit.setText(r"vel(set|add)")
    assert len(bar.hits) >= total
    bar.find_edit.setText("(")
    assert bar.count.text() == "invalid expression" and not ed.search_hits
    bar.toggles["regex"].setChecked(False)
    bar.find_edit.setText("palavra que nao existe")
    assert bar.count.text() == "no results"
    ed.fold_all()                                    # achado dentro de bloco dobrado desdobra
    bar.find_edit.setText("velset")
    assert ed.textCursor().block().isVisible()
    win.find_text(replace=True)
    bar.repl_edit.setText("VelSet")
    bar.replace_all()
    assert ed.toPlainText().count("VelSet") == total
    QTest.keyClick(bar.find_edit, Qt.Key.Key_Escape)
    assert not bar.isVisible() and not ed.search_hits
    ed.document().undo()


def test_sprite_canvas_drags_the_sprite_without_ctrl(gui):
    """Editor de sprites: arrastar a figura muda o eixo (Shift trava num eixo), arrastar o vazio
    move a vista; com fundo de stage o eixo fica parado no chão ao trocar de sprite."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    app, ctx, win, _ = gui
    win.show_page(win.sprites)
    app.processEvents()
    sp = win.sprites
    ctx.settings["backdrop"] = {"kind": "simple"}
    win.apply_backdrop()
    cv = sp.canvas
    assert cv.backdrop["kind"] == "simple" and not cv.center_image
    cv.resize(800, 600)
    sp.nav.set_index(1)
    cv.fit()
    spr = sp.current()
    ax, ay = spr.axis_x, spr.axis_y
    c = cv._sprite_rect().center()
    to = c + QPointF(6 * cv.zoom, -3 * cv.zoom)
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, pos=c.toPoint())
    QTest.mouseMove(cv, to.toPoint())
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, pos=to.toPoint())
    spr = sp.current()
    assert (spr.axis_x, spr.axis_y) == (ax - 6, ay + 3)
    # Shift: só na direção que andou mais
    c = cv._sprite_rect().center()
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, pos=c.toPoint())
    end = c + QPointF(8 * cv.zoom, 3 * cv.zoom)
    cv.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, end, end, Qt.MouseButton.LeftButton,
                                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.ShiftModifier))
    spr = sp.current()
    assert (spr.axis_x, spr.axis_y) == (ax - 14, ay + 3)
    # o vazio move a vista
    pan = QPointF(cv.pan)
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, pos=QPointF(60, 60).toPoint())
    QTest.mouseMove(cv, QPointF(90, 70).toPoint())
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, pos=QPointF(90, 70).toPoint())
    assert cv.pan != pan
    pan = QPointF(cv.pan)
    sp.nav.set_index(2)                       # trocar de sprite não mexe na vista
    assert cv.pan == pan
    ctx.settings["backdrop"] = {"kind": "none"}
    win.apply_backdrop()
    assert cv.center_image and cv.backdrop is None and win.anims.canvas.backdrop is None
    ctx.char.dirty.clear()


def test_stage_backdrop_puts_the_stage_ground_on_the_axis(gui, mugen_dir):
    from charfactory.gui import backdrop
    app, ctx, win, _ = gui
    ctx.settings["mugen_dir"] = str(mugen_dir)
    ctx.settings["backdrop"] = {"kind": "stage", "path": str(mugen_dir / "stages" / "stage0.def")}
    bg, warn = backdrop.build(ctx)
    assert not warn and bg["kind"] == "image" and bg["image"].width() > 320
    # o zoffset do stage (a altura do chão na tela) cai no y = 0 do personagem
    from charfactory.tools.stage import Stage
    zoff = float(Stage(mugen_dir / "stages" / "stage0.def").get("StageInfo", "zoffset"))
    assert bg["pos"][1] == -zoff * bg["scale"]
    ctx.settings["backdrop"] = {"kind": "stage", "path": str(mugen_dir / "nao-existe.def")}
    bg, warn = backdrop.build(ctx)
    assert bg["kind"] == "simple" and warn


def test_timeline_drags_frame_edges_like_a_video_editor(gui):
    """Arrastar a borda de um frame muda o tempo dele; Shift+arrastar estica/comprime tudo; Esc cancela."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from charfactory.gui.widgets import Timeline
    app, ctx, win, _ = gui
    win.show_page(win.anims)
    app.processEvents()
    an = win.anims
    tl = an.timeline
    tl.resize(900, tl.height())
    a = an.action()
    times = [f.time for f in a.frames]
    assert len(times) > 2 and all(t > 0 for t in times[:-1])
    scale = (tl.width() - tl.LEFT - 12) / sum(tl.durations)
    edge = QPointF(tl.LEFT + times[0] * scale, 40)
    assert tl._edge_at(edge.x()) == 0
    QTest.mousePress(tl, Qt.MouseButton.LeftButton, pos=edge.toPoint())
    to = edge + QPointF(3 * scale, 0)
    QTest.mouseMove(tl, to.toPoint())
    QTest.mouseRelease(tl, Qt.MouseButton.LeftButton, pos=to.toPoint())
    assert a.frames[0].time == times[0] + 3 and [f.time for f in a.frames[1:]] == times[1:]
    assert ctx.undo()
    a = an.action()
    assert [f.time for f in a.frames] == times
    # Shift na borda do último finito: tudo na mesma proporção
    an.show_action()
    k = len(times) - 2 if times[-1] < 0 else len(times) - 1
    end = sum(max(1, t) for t in times[:k + 1])
    scale = (tl.width() - tl.LEFT - 12) / sum(tl.durations)
    p0 = QPointF(tl.LEFT + end * scale, 40)
    tl.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, p0, p0, Qt.MouseButton.LeftButton,
                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier))
    p1 = QPointF(p0.x() - end * scale / 2, 40)      # comprime para a metade
    tl.mouseMoveEvent(QMouseEvent(QEvent.Type.MouseMove, p1, p1, Qt.MouseButton.NoButton,
                                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier))
    tl.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, p1, p1, Qt.MouseButton.LeftButton,
                                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.ShiftModifier))
    new = [f.time for f in a.frames]
    assert abs(sum(new) - sum(times) / 2) <= 1 and all(abs(n - t / 2) <= 1 for n, t in zip(new, times))
    assert ctx.undo()
    a = an.action()
    # Esc no meio do arrasto devolve os tempos
    an.show_action()
    QTest.mousePress(tl, Qt.MouseButton.LeftButton, pos=edge.toPoint())
    QTest.mouseMove(tl, (edge + QPointF(5 * scale, 0)).toPoint())
    QTest.keyClick(tl, Qt.Key.Key_Escape)
    QTest.mouseRelease(tl, Qt.MouseButton.LeftButton, pos=(edge + QPointF(5 * scale, 0)).toPoint())
    assert [f.time for f in a.frames] == times
    assert Timeline.stretch([3, 3, 3, -1], 0.5) == [2, 1, 2, -1]
    # arrastar o meio do 1º frame para depois do 3º muda a ordem; o início do loop segue o frame dele
    an.show_action()
    first, third = a.frames[0], a.frames[2]
    scale = (tl.width() - tl.LEFT - 12) / sum(tl.durations)

    def ev(kind, x, buttons):
        pt = QPointF(x, 40)
        return QMouseEvent(kind, pt, pt, Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
    mid0 = tl.LEFT + times[0] * scale / 2
    drop = tl.LEFT + (sum(times[:3]) + times[3] * 0.25) * scale
    tl.mousePressEvent(ev(QEvent.Type.MouseButtonPress, mid0, Qt.MouseButton.LeftButton))
    tl.mouseMoveEvent(ev(QEvent.Type.MouseMove, drop, Qt.MouseButton.LeftButton))
    assert tl._move["active"] and tl._move["dest"] == 3
    tl.mouseReleaseEvent(ev(QEvent.Type.MouseButtonRelease, drop, Qt.MouseButton.NoButton))
    a = an.action()
    assert a.frames[2] is first and a.frames[1] is third and an.frame_nav.index() == 2
    assert ctx.undo()
    a = an.action()
    assert [f.time for f in a.frames] == times
    # dividir no tick clicado e o menu do clique direito
    an.show_action()
    an.split_frame(0, 4)
    a = an.action()
    assert [f.time for f in a.frames[:2]] == [4, times[0] - 4] and len(a.frames) == len(times) + 1
    assert ctx.undo()
    an.show_action()
    got = []
    tl.menuRequested.disconnect()
    tl.menuRequested.connect(lambda i, t, pos: got.append((i, t)))
    pt = QPointF(tl.LEFT + (times[0] + 2.5) * scale, 40)
    tl.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, pt, pt, Qt.MouseButton.RightButton,
                                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier))
    assert got == [(1, 2)]
    tl.menuRequested.disconnect()
    tl.menuRequested.connect(an.frame_menu)
    ctx.char.dirty.clear()


def test_run_uses_the_unsaved_edits_without_saving(gui, monkeypatch):
    """Rodar com mudanças não salvas: o motor recebe uma cópia de teste (chars/kfm__teste) com o
    que está editado, os arquivos do projeto ficam como estavam e o char continua por salvar."""
    from charfactory.gui import app as app_mod
    app, ctx, win, work = gui
    root = work.parent.parent
    (root / "mugen.exe").write_bytes(b"")
    calls = []

    class FakeProc:
        pid = 7
        stdout = iter(())

        def __init__(self, args, **kw):
            calls.append(args)

        def poll(self):
            return 0
    monkeypatch.setattr(app_mod.subprocess, "Popen", FakeProc)
    air = work / "kfm.air"
    before = air.read_bytes()
    a = ctx.char.anims.get(0)
    old = a.frames[0].time
    a.frames[0].time = old + 7
    ctx.changed("anims")
    win.run_mugen("mugen")
    assert calls and calls[-1][1] == "kfm__teste/kfm.def"
    test = work.parent / "kfm__teste"
    assert (test / "kfm.sff").exists() and (test / "kfm.def").exists()
    from charfactory import project
    assert project.load(test / "kfm.def").anims.get(0).frames[0].time == old + 7
    assert air.read_bytes() == before and "anims" in ctx.char.dirty
    ctx.char.dirty.clear()
    a.frames[0].time = old
    win.run_mugen("mugen")                               # salvo: roda o original de novo
    assert calls[-1][1] == "kfm/kfm.def"
