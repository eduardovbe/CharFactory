"""IA na janela (offscreen): ferramentas chamadas de outra thread rodam na da interface, entram
no desfazer e aparecem nos editores; o servidor MCP liga pelas configurações."""
import json
import os
import socket
import threading
import urllib.request

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QUrl  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from charfactory.ai import mcp, providers  # noqa: E402
from charfactory.tools import generate  # noqa: E402

STATE = "[Statedef 777]\ntype = S\nanim = 0\n\n[State 777, end]\ntype = ChangeState\ntrigger1 = 1\nvalue = 0\n"


@pytest.fixture()
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    app = QApplication.instance() or QApplication([])
    from charfactory.gui.app import MainWindow
    from charfactory.gui.context import AppContext
    ctx = AppContext()
    with socket.socket() as sock:              # uma porta livre (a padrão pode estar em uso)
        sock.bind(("127.0.0.1", 0))
        ctx.settings["ai_mcp_port"] = sock.getsockname()[1]
    win = MainWindow(ctx)
    win.show()
    win.open_path(str(generate.new_character(tmp_path / "test", "Test")))
    app.processEvents()
    yield app, ctx, win
    if ctx.char:
        ctx.char.dirty.clear()
    from conftest import dispose_window
    dispose_window(win)


def _wait(app, thread):
    while thread.is_alive():
        app.processEvents()
        thread.join(0.01)


def test_tool_from_another_thread_runs_in_the_ui_and_can_be_undone(gui):
    app, ctx, win = gui
    out = []
    t = threading.Thread(target=lambda: out.append(win.ai_host.call("set_state", {"number": 777, "text": STATE})))
    t.start()
    _wait(app, t)
    assert not out[0].is_error, out[0].text
    assert 777 in ctx.char.all_statedefs() and "mark" not in ctx.char.__dict__
    assert ctx.undo()
    assert 777 not in ctx.char.all_statedefs()


def test_pending_code_edits_are_applied_before_the_agent_reads(gui):
    app, ctx, win = gui
    win.show_page(win.states)
    ed = win.states.files[0]["edit"]
    ed.setPlainText(ed.toPlainText() + "\n" + STATE)
    ed.document().setModified(True)
    assert not win.ai_host.call("get_state", {"number": 777}).is_error


def test_confirmation_can_decline_a_change(gui, monkeypatch):
    app, ctx, win = gui
    ctx.settings["ai_confirm"] = True
    monkeypatch.setattr(type(win.ai_host), "_confirm", lambda self, t, a: False)
    r = win.ai_host.call("set_state", {"number": 777, "text": STATE})
    assert r.is_error and "declined" in r.text and 777 not in ctx.char.all_statedefs()


def test_mcp_server_follows_the_settings(gui):
    app, ctx, win = gui
    assert win.mcp.server is None
    ctx.settings["ai_mcp_enabled"] = True
    win.mcp.apply()
    srv = win.mcp.server
    assert srv is not None
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "character_info", "arguments": {}}}).encode()
    req = urllib.request.Request(srv.url, body, headers={"Authorization": f"Bearer {win.mcp.token()}",
                                                         "Content-Type": "application/json"})
    out = []
    t = threading.Thread(target=lambda: out.append(json.loads(urllib.request.urlopen(req, timeout=10).read())))
    t.start()
    _wait(app, t)
    assert json.loads(out[0]["result"]["content"][0]["text"])["name"] == "Test"
    ctx.settings["ai_mcp_enabled"] = False
    win.mcp.apply()
    assert win.mcp.server is None


def test_settings_dialog_and_assistant_panel(gui, monkeypatch):
    app, ctx, win = gui
    from charfactory.gui.aipanel import AISettingsDialog
    stored = {}
    monkeypatch.setattr("charfactory.ai.keys.set", lambda name, key: stored.__setitem__(name, key))
    dlg = AISettingsDialog(ctx, win.mcp, win, tab=1)
    dlg.provider.setCurrentIndex(dlg.provider.findData("openai"))
    dlg.key.setText("sk-test")
    dlg.model.setEditText("gpt-test")
    dlg.client.setCurrentText("Claude Code")
    assert win.mcp.token() in dlg.snippet.toPlainText() and "claude mcp add" in dlg.snippet.toPlainText()
    dlg.accept()
    assert stored == {"openai": "sk-test"}
    assert ctx.settings["ai_provider"] == "openai" and ctx.settings["ai_model_openai"] == "gpt-test"
    assert "sk-test" not in json.dumps(dict(ctx.settings))       # a chave não vai para o config.json
    win.toggle_assistant(True)
    assert win.assistant.isVisible() and win.assistant_action.isChecked()
    win.assistant.refresh()
    assert win.assistant.model_btn.text() == "gpt-test"                 # o nome já diz o provedor
    win.toggle_assistant(False)
    assert not win.assistant.isVisible()


def test_assistant_conversation_runs_tools_in_the_ui(gui, monkeypatch):
    app, ctx, win = gui
    replies = [{"choices": [{"message": {"content": None, "tool_calls": [{"id": "c", "type": "function", "function": {
                   "name": "set_state", "arguments": json.dumps({"number": 777, "text": STATE})}}]}}]},
               {"choices": [{"message": {"content": "Created **state 777**."}}]}]
    monkeypatch.setattr(providers, "_request", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr("charfactory.ai.keys.get", lambda name: "k")
    ctx.settings.update(ai_provider="openai", ai_model_openai="gpt-test")
    p = win.assistant
    p.input.setPlainText("make state 777")
    p.send_or_stop()
    while p.busy():
        app.processEvents()
        p.worker.thread.join(0.01)
    app.processEvents()
    assert 777 in ctx.char.all_statedefs()
    text = p.view.toPlainText()
    assert "make state 777" in text and "Wrote state 777" in text and "Created state 777." in text
    assert p.send_btn.text() == "Send"


def test_the_assistant_is_told_what_the_user_is_looking_at(gui, monkeypatch):
    app, ctx, win = gui
    win.show_page(win.states)
    ed = win.states.files[0]["edit"]
    ed.setPlainText(ed.toPlainText() + "\n" + STATE)
    cur = ed.textCursor()
    cur.setPosition(ed.toPlainText().index("[State 777, end]"))
    ed.setTextCursor(cur)
    note = win.ai_host.view_context()
    assert note.startswith("<editor>") and 'character "Test"' in note
    assert "inside [Statedef 777]" in note and win.states.files[0]["path"].name in note
    assert not win.ai_host.call("set_animation", {"text": "[Begin Action 50]\n0,0, 0,0, 5\n0,1, 0,0, 5\n"}).is_error
    win.show_page(win.anims)
    app.processEvents()
    assert "animation 50 in the animation editor (frame 1 of 2)" in win.ai_host.view_context()
    sent = []
    monkeypatch.setattr(providers, "_request", lambda url, headers, body=None, method=None: (
        sent.append(body), {"choices": [{"message": {"content": "ok"}}]})[1])
    monkeypatch.setattr("charfactory.ai.keys.get", lambda name: "k")
    ctx.settings.update(ai_provider="openai", ai_model_openai="gpt-test")
    p = win.assistant
    p.input.setPlainText("what does this do?")
    p.send_or_stop()
    while p.busy():
        app.processEvents()
        p.worker.thread.join(0.01)
    user = sent[0]["messages"][1]["content"]
    assert user.startswith("<editor>") and user.endswith("what does this do?")
    assert "<editor>" not in p.view.toPlainText()          # a nota não aparece na conversa


def _run(app, p):
    while p.busy():
        app.processEvents()
        p.worker.thread.join(0.01)
    app.processEvents()


def test_retry_after_an_error_continues_without_repeating_the_message(gui, monkeypatch):
    app, ctx, win = gui
    calls = []

    def fake(url, headers, body=None, method=None):
        calls.append(body)
        if len(calls) == 1:
            raise providers.ProviderError("HTTP 429: quota", 429)
        return {"choices": [{"message": {"content": "ok, done"}}]}
    monkeypatch.setattr(providers, "_request", fake)
    monkeypatch.setattr("charfactory.ai.keys.get", lambda name: "k")
    ctx.settings.update(ai_provider="openai", ai_model_openai="gpt-test")
    p = win.assistant
    p.suggestions()                                   # as sugestões existem traduzidas
    chip = p.welcome.findChildren(type(p.send_btn), "aiChip")[0]
    chip.click()                                      # uma sugestão manda a mensagem
    _run(app, p)
    assert p._items[-1][0] == "error" and "usage limit" in p.view.toPlainText()
    p._link(QUrl("action:retry"))
    _run(app, p)
    assert "ok, done" in p.view.toPlainText()
    users = [m for m in calls[1]["messages"] if m["role"] == "user"]
    assert len(users) == 1 and users[0]["content"].endswith(chip.text())
    p.input.setFocus()
    QTest.keyClick(p.input, Qt.Key.Key_Up)            # ↑ no campo vazio repete a última mensagem
    assert p.input.toPlainText() == chip.text()


def test_steps_are_described_and_changes_noted_at_the_end(gui, monkeypatch):
    app, ctx, win = gui
    replies = [{"choices": [{"message": {"content": None, "tool_calls": [{"id": "c", "type": "function", "function": {
                   "name": "set_state", "arguments": json.dumps({"number": 777, "text": STATE})}}]}}]},
               {"choices": [{"message": {"content": "Done."}}]}]
    monkeypatch.setattr(providers, "_request", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr("charfactory.ai.keys.get", lambda name: "k")
    ctx.settings.update(ai_provider="openai", ai_model_openai="gpt-test")
    p = win.assistant
    p.input.setPlainText("x")
    assert p.send_btn.isEnabled()
    p.send_or_stop()
    _run(app, p)
    text = p.view.toPlainText()
    assert "✓ Wrote state 777" in text and "AI changed: states" in text
    ctx.themeChanged.emit()                           # tema novo: a conversa é redesenhada igual
    app.processEvents()
    assert p.view.toPlainText() == text
    p.new_chat()
    assert p.stack.currentWidget() is p.welcome and not p._items and not p.send_btn.isEnabled()
