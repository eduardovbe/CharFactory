"""Recovery, per-file review, task undo and configurable bindings."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtGui import QKeySequence

from test_workspace import window  # shared self-contained GUI fixture
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from charfactory.gui.context import Settings
from charfactory.gui.changes import FileChange, RecoveryStore, editor_buffers, pending
from charfactory.gui.shortcuts import ShortcutsDialog
from charfactory.tools import ops


STATE = "[Statedef 777]\ntype = S\nanim = 50\n"
ANIM = "[Begin Action 50]\n0,0, 0,0, 4\n"


def test_pending_reads_unapplied_unicode_text_and_reverts_without_saving(window):
    app, ctx, win = window
    win.show_page(win.states)
    source = win.states.current()
    path = source["path"]
    saved = path.read_bytes()
    ed = source["edit"]
    ed.setPlainText(ed.toPlainText() + "\n; edição não aplicada\n" + STATE)
    ed.document().setModified(True)
    changes = pending(ctx.char, editor_buffers(win))
    change = next(c for c in changes if c.path == path)
    assert change.buffer and "edição" in change.data.decode("utf-8")
    assert 777 not in ctx.char.all_statedefs()
    assert path.read_bytes() == saved
    win.revert_pending_file(change)
    assert path.read_bytes() == saved
    assert not pending(ctx.char, editor_buffers(win))
    ed.undo()
    assert "edição" in ed.toPlainText()
    # Keep the fixture teardown from asking about the deliberately restored text.
    ed.document().setModified(False)


def test_revert_one_state_file_preserves_other_file_changes(window):
    app, ctx, win = window
    c = ctx.char
    extra = c.dir / "extra.cns"
    extra.write_text("[Statedef 888]\ntype = S\nanim = 0\n", "utf-8")
    c.definition.find("files").set("st1", extra.name)
    ctx.reset_cache("states")
    ctx.prime_all()
    original = c.states[0].doc.to_text()
    c.states[0].doc.preamble.append("; first changed")
    c.states[1].statedefs[888].section.set("anim", "20")
    ctx.changed("states")
    changes = pending(c)
    first = next(ch for ch in changes if ch.path == c.states[0].path)
    win.revert_pending_file(first)
    assert c.states[0].doc.to_text() == original
    assert c.states[1].statedefs[888].section.get("anim") == "20"
    assert "states" in c.dirty
    assert extra.read_text("utf-8").endswith("anim = 0\n")


def test_recovery_keeps_unapplied_text_and_external_common_files(window, tmp_path):
    app, ctx, win = window
    c = ctx.char
    external = tmp_path / "shared.cns"
    external.write_text("[Statedef 0]\ntype = S\nanim = 0\n", "utf-8")
    c.definition.find("files").set("stcommon", str(external))
    ctx.reset_cache("common_states")
    ctx.prime_all()
    c.common_states.statedefs[0].section.set("anim", "10")
    ctx.changed("common_states")
    win.show_page(win.states)
    source = next(f for f in win.states.files if f["kind"] == "states")
    disk = source["path"].read_bytes()
    source["edit"].setPlainText(source["edit"].toPlainText() + "\n; recuperar ação\n" + STATE)
    source["edit"].document().setModified(True)
    win.autosave()
    store = RecoveryStore(c)
    changes = store.candidates()
    assert {ch.part for ch in changes} == {"states", "common_states"}
    assert external.read_text("utf-8").endswith("anim = 0\n")
    assert source["path"].read_bytes() == disk
    source["edit"].setPlainText(disk.decode("utf-8"))
    source["edit"].document().setModified(False)
    assert win.recover_files(changes)
    assert "recuperar ação" in source["edit"].toPlainText()
    assert source["edit"].document().isModified()
    assert c.common_states.statedefs[0].section.get("anim") == "10"
    assert source["path"].read_bytes() == disk
    assert external.read_text("utf-8").endswith("anim = 0\n")


def test_recovery_snapshot_replaces_stale_entries_and_dismisses(window):
    _, ctx, win = window
    c = ctx.char
    store = RecoveryStore(c)
    path = c.commands.path
    store.write([FileChange(path, "commands", b"; first\n", True)])
    assert len(store.candidates()) == 1
    store.write([FileChange(c.def_path, "def", b"; second\n", True)])
    assert [ch.path for ch in store.candidates()] == [c.def_path]
    store.dismiss()
    assert store.candidates() == []


def test_recovery_preserves_applied_model_and_unapplied_buffer_together(window):
    _, ctx, win = window
    path = ctx.char.states[0].path
    saved = path.read_bytes()
    assert not win.ai_host.call("set_state", {"number": 777, "text": STATE}).is_error
    win.show_page(win.states)
    source = win.states.current()
    source["edit"].setPlainText(source["edit"].toPlainText() + "\n; text still being edited\n")
    source["edit"].document().setModified(True)
    win.autosave()
    changes = RecoveryStore(ctx.char).candidates()
    state = next(ch for ch in changes if ch.path == path)
    assert state.buffer and state.model_data is not None
    ctx.reopen()
    assert 777 not in ctx.char.all_statedefs()
    assert win.recover_files(changes)
    assert 777 in ctx.char.all_statedefs()
    source = win.states.current()
    assert "text still being edited" in source["edit"].toPlainText()
    assert source["edit"].document().isModified()
    assert path.read_bytes() == saved


def test_recovery_does_not_replace_newer_disk_file_or_corrupt_payload(window):
    _, ctx, win = window
    store = RecoveryStore(ctx.char)
    path = ctx.char.commands.path
    store.write([FileChange(path, "commands", b"; recovered\n", True)])
    manifest = store.read()
    payload = store.root / manifest["files"][0]["payload"]
    payload.write_bytes(b"broken")
    assert store.candidates() == []
    store.write([FileChange(path, "commands", b"; recovered\n", True)])
    created = store.read()["created_ns"]
    os.utime(path, ns=(created + 1_000_000, created + 1_000_000))
    assert store.candidates() == []


def test_ai_task_undo_redo_restores_all_parts_together(window):
    app, ctx, win = window
    host = win.ai_host
    token = host.call("begin_edit_task", {"title": "New move"})
    assert not token.is_error
    assert not host.call("set_state", {"number": 777, "text": STATE}).is_error
    assert not host.call("set_animation", {"text": ANIM}).is_error
    assert not host.call("end_edit_task", {}).is_error
    task = host.tasks[-1]
    assert len(task["steps"]) == 2
    assert host.can_undo_task(task)
    assert len(ctx.undo_stack().undo) == 1
    win.show_page(win.states)
    app.processEvents()
    win._edit("undo")
    assert 777 not in ctx.char.all_statedefs()
    assert ctx.char.anims.get(50) is None
    win._edit("redo")
    assert 777 in ctx.char.all_statedefs()
    assert ctx.char.anims.get(50) is not None


def test_manual_edit_is_not_swallowed_by_ai_task(window):
    _, ctx, win = window
    host = win.ai_host
    host.begin_task("Task")
    host.call("set_state", {"number": 777, "text": STATE})
    ops.create_action(ctx.char, 9999, [{"group": 0, "item": 0, "time": 4}])
    ctx.changed("anims")
    host.call("set_state", {"number": 778, "text": STATE.replace("777", "778")})
    task = host.end_task()
    assert not host.can_undo_task(task)
    assert not host.undo_task(task)
    ctx.undo()  # second AI call
    assert ctx.char.anims.get(9999) is not None
    ctx.undo()  # the user's edit
    assert host.can_undo_task(task)
    assert host.undo_task(task)
    assert 777 not in ctx.char.all_statedefs()


def test_shortcuts_detect_conflicts_persist_and_restore_defaults(window):
    _, ctx, win = window
    manager = win.shortcuts
    entries = manager.collect()
    save = entries["main/Save"]
    opened = entries["main/Open…"]
    overrides = {save.key: ["Ctrl+O"]}
    assert any(a.key == save.key or b.key == save.key for a, b, _ in manager.conflicts(entries, overrides))
    overrides[save.key] = ["Ctrl+Alt+F11"]
    assert manager.conflicts(entries, overrides) == []
    ctx.settings["shortcuts"] = overrides
    ctx.settings.save()
    assert Settings.load()["shortcuts"] == overrides
    manager.apply()
    assert save.nodes[0].shortcut() == QKeySequence("Ctrl+Alt+F11")
    win.show_page(win.states)
    assert entries["main/Save"].nodes[0].shortcut() == QKeySequence("Ctrl+Alt+F11")
    dialog = ShortcutsDialog(manager, win)
    dialog.reset_all()
    dialog.save()
    assert save.nodes[0].shortcut() == QKeySequence("Ctrl+S")
    assert ctx.settings["shortcuts"] == {}


def test_rebinding_code_definition_key_disables_the_old_key(window):
    app, ctx, win = window
    ops.create_action(ctx.char, 50, [{"group": 0, "item": 0, "time": 4}])
    ctx.changed("anims")
    win.show_page(win.states)
    ed = win.states.current_edit()
    ed.setPlainText("[Statedef 777]\nanim = 50\n")
    ed.goto_line(2)
    ctx.settings["shortcuts"] = {"states/Go to definition": ["Ctrl+Alt+F12"]}
    win.shortcuts.apply()
    win.activateWindow()
    ed.setFocus()
    app.processEvents()
    QTest.keyClick(ed, Qt.Key.Key_F12)
    assert win.current_page() is win.states
    QTest.keyClick(ed, Qt.Key.Key_F12, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    app.processEvents()
    assert win.current_page() is win.anims
    assert win.anims.action().number == 50


def test_recovery_supports_legacy_autosave_and_keeps_two_generations(window):
    _, ctx, win = window
    store = RecoveryStore(ctx.char)
    store.root.mkdir(parents=True)
    path = ctx.char.commands.path
    legacy = store.root / path.name
    legacy.write_bytes(b"; older Char Factory automatic backup\n")
    timestamp = path.stat().st_mtime_ns + 1_000_000
    os.utime(legacy, ns=(timestamp, timestamp))
    assert [ch.path for ch in store.candidates()] == [path]
    for i in range(3):
        store.write([FileChange(path, "commands", f"; snapshot {i}\n".encode(), True)])
    assert len(list((store.root / "snapshots").iterdir())) == 2
    assert store.candidates()[0].data == b"; snapshot 2\n"
