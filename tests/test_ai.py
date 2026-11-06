"""Ferramentas de IA, servidor MCP (HTTP e stdio), provedores (com a API simulada) e o loop do
assistente. Usam um personagem esqueleto: não precisam do MUGEN nem de rede."""
import io
import json
import sys
import urllib.error
import urllib.request

import pytest

from charfactory.ai import agent, keys, mcp, providers, tools
from charfactory.tools import generate

STATE = ("[Statedef 200]\ntype = S\nmovetype = A\nanim = 200\n\n"
         "[State 200, end]\ntype = ChangeState\ntrigger1 = AnimTime = 0\nvalue = 0\nctrl = 1\n")


@pytest.fixture()
def host(tmp_path):
    h = tools.Host()
    h.open_character(generate.new_character(tmp_path / "test", "Test"))
    return h


# ======================================================================= ferramentas

def test_tools_need_an_open_character():
    r = tools.run(tools.Host(), "character_info", {})
    assert r.is_error and "open_character" in r.text


def test_set_and_get_state_changes_the_model_in_memory(host):
    r = tools.run(host, "set_state", {"number": 200, "text": STATE})
    assert not r.is_error, r.text
    assert "states" in host.char.dirty or "commands" in host.char.dirty
    assert "[State 200, end]" in tools.run(host, "get_state", {"number": 200}).text
    assert "\n200 S/A/N anim 200" in tools.run(host, "list_states", {}).text


def test_bad_arguments_come_back_as_errors_for_the_model(host):
    assert "missing argument" in tools.run(host, "get_state", {}).text
    assert "unknown argument" in tools.run(host, "get_state", {"number": 1, "foo": 2}).text
    assert tools.run(host, "get_state", {"number": 98765}).is_error
    assert tools.run(host, "set_state", {"number": 300, "text": STATE}).is_error   # cabeçalho de outro número
    assert tools.run(host, "nope", {}).is_error


def test_save_project_writes_to_disk(host):
    tools.run(host, "set_constant", {"section": "Data", "key": "life", "value": "1200"})
    r = tools.run(host, "save_project", {})
    assert not r.is_error
    assert "1200" in host.char.def_path.with_suffix(".cns").read_text("utf-8", errors="replace")


def test_every_tool_has_a_valid_schema():
    for t in tools.describe():
        s = t.schema()
        assert t.description and s["type"] == "object"
        assert set(t.required) <= set(s["properties"])
        for p in s["properties"].values():
            assert p["type"] in ("integer", "string", "boolean", "number", "array") and p["description"]
            if p["type"] == "array" and p["items"]["type"] == "object":
                assert set(p["items"]["required"]) <= set(p["items"]["properties"])


NORMAL_X = '[State -1, Soco]\ntype = ChangeState\nvalue = 200\ntriggerall = command = "x"\ntrigger1 = ctrl\n'
SPECIAL = ('[State -1, Especial]\ntype = ChangeState\nvalue = 1000\ntriggerall = command = "qcf_x"\n'
           'trigger1 = ctrl\n')


def _minus1(host):
    return [c.label for c in host.char.states_file_for(-1).statedefs[-1].controllers]


def _json(host, tool_name, /, **args):
    r = tools.run(host, tool_name, args)
    assert not r.is_error, r.text
    return json.loads(r.text)


def test_a_special_added_after_the_normals_moves_before_them(host):
    tools.run(host, "set_state", {"number": 200, "text": STATE})
    tools.run(host, "set_state", {"number": 1000, "text": STATE.replace("200", "1000")})
    tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X})
    tools.run(host, "add_command", {"name": "qcf_x", "sequence": "~D, DF, F, x"})
    r = _json(host, "add_controller", number=-1, text=SPECIAL)
    assert "moved before [State -1, Soco]" in r["command_check"]
    assert _minus1(host) == ["Especial", "Soco"]
    t = _json(host, "test_command", input="2 3 6 x")
    assert t["state"] == 1000 and "qcf_x" in t["commands"]
    assert _json(host, "test_command", input="x")["state"] == 200
    assert "command_problems" not in _json(host, "validate_character")
    # o mesmo comando duas vezes: avisa em vez de mexer na ordem
    r = _json(host, "add_controller", number=-1, text=SPECIAL.replace("Especial", "De novo"))
    assert "same input" in r["command_check"] and _minus1(host)[-1] == "De novo"
    assert _json(host, "validate_character")["command_problems"][0]["entry"] == "[State -1, De novo]"
    _json(host, "add_controller", number=-1, text=NORMAL_X.replace("Soco", "Primeiro"), before="start")
    assert _minus1(host)[0] == "Primeiro"
    assert tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X, "before": "nope"}).is_error
    assert _json(host, "remove_controller", number=-1, label="Primeiro")["removed"] == 1
    assert "Primeiro" not in _minus1(host)
    r = _json(host, "remove_state", number=1000)
    assert r["removed_entries"] == ["[State -1, Especial]", "[State -1, De novo]"] and _minus1(host) == ["Soco"]


ANIM = ("[Begin Action 200]\nClsn2Default: 1\n Clsn2[0] = -10, -60, 10, 0\n0,0, 0,0, 4\n"
        "Clsn1: 1\n Clsn1[0] = 5, -50, 40, -40\n0,1, 0,0, 3\n")


def test_set_animation_refuses_boxes_the_parser_would_drop(host):
    bad = "[Begin Action 200]\n0,0, 0,0, 4\n  clsns = 2\n  Clsn2[0] = -20,-100,20,0\n0,1, 0,0, 4\n"
    r = tools.run(host, "set_animation", {"text": bad})
    assert r.is_error and "line 3: 'clsns = 2'" in r.text and "line 4" in r.text
    assert host.char.anims.get(200) is None
    r = tools.run(host, "set_animation", {"text": ANIM.replace("Clsn2Default: 1", "Clsn2Default: 2")})
    assert r.is_error and "1 more box" in r.text
    assert _json(host, "set_animation", text=ANIM)["actions"] == [
        {"number": 200, "frames": 2, "frames_with_clsn1": [2], "frames_without_clsn2": [], "total_time": 7}]


def test_create_move_writes_state_command_and_entry(host):
    tools.run(host, "set_animation", {"text": ANIM})
    tools.run(host, "set_state", {"number": 200, "text": STATE})
    tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X})
    out = _json(host, "create_move", state=1000, anim=200, kind="special", command="~D, DF, F, x",
                name="Palma", damage=80)
    assert "moved before [State -1, Soco]" in out["command_check"] and _minus1(host) == ["Palma", "Soco"]
    text = tools.run(host, "get_state", {"number": 1000}).text
    assert "damage = 80, 10" in text and "poweradd" not in text and "[State 1000," in text
    assert _json(host, "test_command", command="cmd_1000")["state"] == 1000
    assert _json(host, "validate_character", number=1000)["errors"] == 0
    # a mesma entrada de outro golpe: recusa sem mexer em nada e sugere entradas livres
    r = tools.run(host, "create_move", {"state": 1100, "anim": 200, "kind": "special", "command": "~D, DF, F, x"})
    assert r.is_error and "[State -1, Palma]" in r.text and "~D, DB, B, x" in r.text
    assert 1100 not in host.char.all_statedefs()
    assert tools.run(host, "create_move", {"state": 1000, "anim": 200, "kind": "special", "command": "y"}).is_error
    sup = _json(host, "create_move", state=3000, anim=200, kind="super", command="~D, DF, F, D, DF, F, x")
    assert sup["state"] == 3000
    text = tools.run(host, "get_state", {"number": 3000}).text
    assert "poweradd = -1000" in text and "SuperPause" in text
    assert "power >= 1000" in tools.run(host, "get_state", {"number": -1}).text


def test_build_animation_fits_boxes_to_the_sprites(mugen_dir, host):
    host.open_character(str(mugen_dir / "chars" / "kfm" / "kfm.def"))
    frames = [{"group": 210, "item": 0, "time": 4}, {"group": 210, "item": 3, "time": 6, "attack": True},
              {"group": 210, "item": 5, "time": 8}]
    r = _json(host, "build_animation", number=1450, frames=frames)
    assert r == {"number": 1450, "replaced": False, "frames": 3, "frames_with_clsn1": [2], "total_time": 18}
    act = host.char.anims.get(1450)
    assert all(f.clsn2 for f in act.frames) and act.frames[1].clsn1 and not act.frames[0].clsn1
    assert "9999,0" in tools.run(host, "build_animation", {"number": 1451, "frames": [
        {"group": 9999, "item": 0, "time": 4}]}).text


def test_existing_moves_are_protected_and_repeats_are_cleaned(host):
    tools.run(host, "set_state", {"number": 200, "text": STATE})
    tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X})
    r = tools.run(host, "set_state", {"number": 200, "text": STATE.replace("ctrl = 1", "ctrl = 0")})
    assert r.is_error and "Soco (x)" in r.text and "replace=true" in r.text and "such as 1000" in r.text
    assert not tools.run(host, "set_state", {"number": 200, "text": STATE, "replace": True}).is_error
    # a mesma entrada de novo é recusada; repetidas com o mesmo rótulo saem com all=true
    assert "exact controller" in tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X}).text
    tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X.replace("trigger1 = ctrl", "trigger1 = 1")})
    r = tools.run(host, "remove_controller", {"number": -1, "label": "Soco"})
    assert r.is_error and "all=true" in r.text
    assert _json(host, "remove_controller", number=-1, label="Soco", occurrence=2)["removed"] == 1
    tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X.replace("trigger1 = ctrl", "trigger1 = 1")})
    assert _json(host, "remove_controller", number=-1, label="Soco", all=True)["removed"] == 2
    assert _minus1(host) == []


def test_animations_say_what_they_are(host):
    tools.run(host, "set_animation", {"text": ANIM + ANIM.replace("Action 200", "Action 41")})
    tools.run(host, "set_state", {"number": 200, "text": STATE})
    tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X})
    rows = {ln.split()[0]: ln for ln in tools.run(host, "list_animations", {}).text.splitlines()[1:]}
    assert '"jump up (neutral)"' in rows["41"]                     # número padrão do MUGEN
    assert rows["200"].endswith("state 200 <- Soco (x)")
    assert tools.run(host, "list_animations", {"search": "neutral"}).text.count("\n") == 1
    assert "200 S/A/N anim 200 <- Soco (x)" in tools.run(host, "list_states", {"search": "soco"}).text
    assert tools.run(host, "list_states", {"numbers": "1-5"}).text == "no statedef matches"
    assert tools.run(host, "list_states", {"numbers": "abc"}).is_error
    assert "used_in_actions" in _json(host, "list_sprites", group=0)


CROSS_UP = """[Statedef 1450]
type = A
movetype = A
physics = A
ctrl = 0
anim = 42
velset = 2.97, -8.9
facep2 = 1

[State 1450, keep going]
type = VelMul
trigger1 = p2dist x < -5
x = -0.25

[State 1450, turn]
type = Turn
trigger1 = p2dist x < -5

[State 1450, kick]
type = ChangeAnim
trigger1 = time = 30
value = 640

[State 1450, hit]
type = HitDef
trigger1 = anim = 640 && animelemtime(1) = 0
attr = A, SA
damage = 60, 7
guardflag = HA
pausetime = 10, 12
ground.velocity = -4, -5
fall = 1
"""


def test_a_move_written_by_hand_can_be_checked_by_its_path(mugen_dir, host):
    host.open_character(str(mugen_dir / "chars" / "kfm" / "kfm.def"))
    r = tools.run(host, "view_animations", {"numbers": [42, 640, 99999]})
    assert len(r.images) == 1 and "42 " in r.text and "not found: [99999]" in r.text
    out = _json(host, "create_move", state=1450, anim=640, kind="special", command="a", replace_input=True)
    assert "Stand Light Kick" in out["input_taken_from"]
    _json(host, "set_state", number=1450, text=CROSS_UP, replace=True)
    assert _json(host, "test_command", input="a")["state"] == 1450
    from charfactory.tools import simulate
    assert simulate.starts_on_ground(host.char, 1450)          # a página do simulador sai do chão também
    sim = _json(host, "simulate_state", number=1450)
    assert sim["path"][0]["y"] > -20                           # sai do chão, não do alto de um pulo
    assert sim["outcome"] == "hit" and sim["crossed_opponent_at_tick"] < sim["first_hit"]["tick"]
    assert sim["first_hit"]["from"] == "behind" and sim["first_hit"]["in_air"]


HIT = ("[Statedef 1200]\ntype = S\nmovetype = A\nphysics = S\nanim = 200\nctrl = 0\n\n"
       "[State 1200, hit]\ntype = HitDef\ntrigger1 = AnimElem = 2\nattr = S, NA\ndamage = 20 ; dano\n\n"
       "[State 1200, end]\ntype = ChangeState\ntrigger1 = AnimTime = 0\nvalue = 0\nctrl = 1\n\n"
       ";---- next move: this comment belongs to whatever comes after\n")


def test_edit_state_changes_only_what_it_is_told(host):
    tools.run(host, "set_animation", {"text": ANIM})
    _json(host, "set_state", number=1200, text=HIT)
    out = _json(host, "edit_state", number=1200, target="hit",
                set=[{"key": "damage", "value": "35, 4"}, {"key": "pausetime", "value": "9, 9"}])
    assert out == {**out, "in": "[State 1200, hit]", "lines_now": ["damage = 35, 4", "pausetime = 9, 9"]}
    text = tools.run(host, "get_state", {"number": 1200}).text
    assert "damage = 35, 4 ; dano" in text                         # comentário da linha mantido
    assert text.index("pausetime = 9, 9") < text.index("[State 1200, end]")
    out = _json(host, "edit_state", number=1200, target="end", set=[{"key": "damage", "value": "1"}])
    assert "unknown parameter 'damage'" in out["issues"][0]
    out = _json(host, "edit_state", number=1200, target="header", remove=["ctrl", "nope"],
                set=[{"key": "velset", "value": "3, 0"}])
    assert out["removed"] == ["ctrl"] and out["not_found"] == ["nope"]
    sd = host.char.all_statedefs()[1200][0].params()
    assert "ctrl" not in sd and sd["velset"] == "3, 0"
    assert tools.run(host, "edit_state", {"number": 1200, "target": "nope", "remove": ["x"]}).is_error
    bad = tools.run(host, "edit_state", {"number": 1200, "target": "hit", "set": [{"key": "a", "value": "1\nb = 2"}]})
    assert bad.is_error


def test_get_move_brings_state_animation_input_and_chains(host):
    tools.run(host, "set_animation", {"text": ANIM})
    _json(host, "set_state", number=1200, text=HIT)
    tools.run(host, "add_controller", {"number": -1, "text": NORMAL_X.replace("200", "1200")})
    _json(host, "set_state", number=200, text=STATE.replace("trigger1 = AnimTime = 0\nvalue = 0",
                                                         'trigger1 = command = "x"\nvalue = 1200'))
    out = _json(host, "get_move", number=1200)
    assert "[State 1200, hit]" in out["statedef"] and "[Begin Action 200]" in out["animations"]["200"]
    assert out["entries"][0]["commands"] == [{"name": "x", "sequence": "x"}]
    assert out["reached_from"] == ["statedef 200 [State 200, end] on command x"]
    assert "verdict" in out["simulation"]


def test_edit_animation_changes_frames_in_place(mugen_dir, host):
    host.open_character(str(mugen_dir / "chars" / "kfm" / "kfm.def"))
    before = host.char.anims.get(200)
    n = len(before.frames)
    out = _json(host, "edit_animation", number=200, frames=[{"frame": 2, "time": 9, "x": 3}],
                delete=[n], insert=[{"after": 0, "group": 0, "item": 0, "time": 2}])
    a = host.char.anims.get(200)
    assert [f.time for f in a.frames][:3] == [2, before.frames[0].time, 9] and len(a.frames) == n
    assert a.frames[2].x == 3 and a.frames[0].clsn2 and "[Begin Action 200]" in out["text"]
    _json(host, "edit_animation", number=200, frames=[{"frame": 3, "attack": False}])
    assert not host.char.anims.get(200).frames[3 - 1].clsn1
    assert "anims" in host.char.dirty
    assert tools.run(host, "edit_animation", {"number": 200, "delete": [99]}).is_error
    assert tools.run(host, "edit_animation", {"number": 200, "frames": [{"frame": 1, "group": 99999}]}).is_error
    assert tools.run(host, "edit_animation", {"number": 200, "delete": list(range(1, n + 1))}).is_error


def test_validation_groups_repeated_problems(host):
    for k in range(5):
        tools.run(host, "add_controller", {"number": -1, "text": f"[State -1, e{k}]\ntype = ChangeState\n"
                                                                 f"trigger1 = ctrl\nvalue = {7000 + k}\n"})
    out = _json(host, "validate_character", warnings=True)
    grouped = [i for i in out["issues"] if i.get("count") == 5]
    assert grouped and grouped[0]["message"].startswith("e.g. ") and len(grouped[0]["where"]) == 4


def test_mugen_guide(host):
    assert "VelMul x = -1" in tools.run(host, "mugen_guide", {"topic": "sides"}).text
    assert "peak height" in tools.run(host, "mugen_guide", {"topic": "movement"}).text
    assert tools.run(host, "mugen_guide", {"topic": "nope"}).is_error


def test_mugen_reference(host):
    r = _json(host, "mugen_reference", name="HitDef")
    assert r["controller"] == "HitDef" and "attr" in r["required"] and "damage" in r["parameters"]
    assert _json(host, "mugen_reference", name="AnimElemTime")["trigger"] == "AnimElemTime"
    assert any(c["name"] == "VelSet" for c in _json(host, "mugen_reference", name="velocity")["controllers"])
    assert tools.run(host, "mugen_reference", {"name": "zzzzqqq"}).is_error


# ======================================================================= MCP

def _rpc(host, method, params=None, msg_id=1):
    return mcp.handle(host, {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})


def test_mcp_protocol(host):
    init = _rpc(host, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["protocolVersion"] == "2025-06-18" and "tools" in init["capabilities"]
    assert _rpc(host, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == mcp.PROTOCOLS[0]
    assert mcp.handle(host, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in _rpc(host, "tools/list")["result"]["tools"]}
    assert {"character_info", "set_state", "simulate_state"} <= names
    listed = {t["name"]: t for t in _rpc(host, "tools/list")["result"]["tools"]}
    assert listed["get_state"]["annotations"]["readOnlyHint"] and not listed["set_state"]["annotations"]["readOnlyHint"]
    ok = _rpc(host, "tools/call", {"name": "character_info", "arguments": {}})["result"]
    assert not ok["isError"] and json.loads(ok["content"][0]["text"])["name"] == "Test"
    bad = _rpc(host, "tools/call", {"name": "get_state", "arguments": {"number": 4321}})["result"]
    assert bad["isError"]
    assert _rpc(host, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert _rpc(host, "what")["error"]["code"] == -32601


def test_mcp_images_are_base64_png():
    out = mcp.result_content(tools.Result("x", [b"\x89PNG..."]))
    assert out["content"][1] == {"type": "image", "data": "iVBORy4uLg==", "mimeType": "image/png"}


def test_mcp_http_server_requires_the_token_and_a_local_origin(host):
    srv = mcp.HttpServer(host, "secret", port=0).start()

    def post(body, **headers):
        req = urllib.request.Request(srv.url, json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
    try:
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        assert post(ping)[0] == 401
        assert post(ping, Authorization="Bearer wrong")[0] == 401
        assert post(ping, Authorization="Bearer secret", Origin="https://evil.example")[0] == 403
        assert post(ping, Authorization="Bearer secret", Origin="http://localhost.evil.example")[0] == 403
        code, body = post(ping, Authorization="Bearer secret", Origin="http://localhost:3000")
        assert code == 200 and json.loads(body)["result"] == {}
        assert post({"jsonrpc": "2.0", "method": "notifications/initialized"}, Authorization="Bearer secret")[0] == 202
        code, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "list_commands", "arguments": {}}}, Authorization="Bearer secret")
        assert code == 200 and not json.loads(body)["result"]["isError"]
    finally:
        srv.stop()


def test_mcp_stdio_answers_one_line_per_request(host):
    lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
             json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
             "not json",
             json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "character_info", "arguments": {}}})]
    out = io.StringIO()
    mcp.serve_stdio(host, stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2]
    assert replies[1]["error"]["code"] == -32700


# ======================================================================= provedores e agente

class Script:
    """Substitui providers._request: devolve as respostas na ordem e guarda os pedidos."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, url, headers, body=None, method=None):
        self.requests.append((url, headers, json.loads(json.dumps(body))))
        return self.replies.pop(0)


def test_anthropic_tool_loop(host, monkeypatch):
    script = Script(
        {"stop_reason": "tool_use", "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "tu1", "name": "set_state", "input": {"number": 200, "text": STATE}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done: state 200."}]})
    monkeypatch.setattr(providers, "_request", script)
    a = agent.Agent(providers.make("anthropic", "k"), host)
    events = []
    assert a.send("make a state", lambda kind, data: events.append(kind)) == "Done: state 200."
    assert events == ["text", "tool", "result", "text"]
    url, headers, body = script.requests[1]
    assert url.endswith("/messages") and headers["x-api-key"] == "k" and body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default" and headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert body["cache_control"] == {"type": "ephemeral"}                  # prefixo lido do cache a cada etapa
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"] == {"effort": "high"}
    assert body["messages"][1]["content"][0]["type"] == "thinking"           # volta intacto
    result = body["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu1" and "is_error" not in result
    assert "[State 200, end]" in tools.run(host, "get_state", {"number": 200}).text


def test_anthropic_refusal_is_not_kept_in_history(host, monkeypatch):
    monkeypatch.setattr(providers, "_request", Script(
        {"stop_reason": "refusal", "stop_details": {"category": "cyber"}, "content": []}))
    a = agent.Agent(providers.make("anthropic", "k", "claude-haiku-5-5"), host)
    assert "declined" in a.send("x") and len(a.messages) == 1


def test_anthropic_older_models_get_no_thinking_or_effort(host, monkeypatch):
    script = Script({"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]})
    monkeypatch.setattr(providers, "_request", script)
    agent.Agent(providers.make("anthropic", "k", "claude-haiku-4-5"), host).send("x")
    body = script.requests[0][2]
    assert "thinking" not in body and "output_config" not in body and "fallbacks" not in body


def test_calls_cut_by_the_output_limit_do_not_run(host, monkeypatch):
    cut = {"stop_reason": "max_tokens", "content": [
        {"type": "tool_use", "id": "t", "name": "set_state", "input": {"number": 4321, "text": "[Statedef 4321]\nty"}}]}
    monkeypatch.setattr(providers, "_request", Script(cut, {"stop_reason": "end_turn", "content": []}))
    a = agent.Agent(providers.make("anthropic", "k"), host)
    a.send("x")
    res = a.messages[2]["content"][0]
    assert res["is_error"] and "output limit" in res["content"][0]["text"]
    assert tools.run(host, "get_state", {"number": 4321}).is_error           # nada foi escrito


def test_an_internal_error_in_a_tool_comes_back_as_a_result(host, monkeypatch):
    def boom(host, char):
        raise RuntimeError("bug")
    monkeypatch.setitem(tools.REGISTRY, "boom", tools.Tool("boom", "", {}, boom))
    r = tools.run(host, "boom", {})
    assert r.is_error and "internal error in boom" in r.text


def test_openai_tool_loop_with_bad_json_and_images(host, monkeypatch):
    script = Script(
        {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "character_info", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "get_state", "arguments": "{oops"}}]}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]})
    monkeypatch.setattr(providers, "_request", script)
    a = agent.Agent(providers.make("openai", "k", "gpt-x"), host)
    assert a.send("hi") == "ok"
    msgs = script.requests[1][2]["messages"]
    assert msgs[0]["role"] == "system" and msgs[2]["tool_calls"][0]["id"] == "c1"
    assert msgs[3]["tool_call_id"] == "c1" and msgs[4]["content"].startswith("ERROR: arguments are not valid JSON")
    p = providers.make("openai", "k")
    out = []
    p.add_results(out, [(providers.Call("c", "view_sprite", {}), tools.Result("pic", [b"png"]))])
    assert out[-1]["role"] == "user" and out[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_gemini_tool_loop_keeps_parts_and_cleans_schemas(host, monkeypatch):
    part = {"functionCall": {"name": "list_commands", "args": {}}, "thoughtSignature": "abc"}
    script = Script({"candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP"}]},
                    {"candidates": [{"content": {"role": "model", "parts": [{"text": "fine"}]}}]})
    monkeypatch.setattr(providers, "_request", script)
    a = agent.Agent(providers.make("gemini", "k", "gemini-x"), host)
    assert a.send("hi") == "fine"
    url, headers, body = script.requests[1]
    assert url.endswith("/models/gemini-x:generateContent") and headers["x-goog-api-key"] == "k"
    assert body["contents"][1]["parts"][0] == part
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "list_commands"
    assert "additionalProperties" not in json.dumps(body["tools"])
    assert providers._gemini_schema({"type": "object", "additionalProperties": False, "default": 1}) == {"type": "object"}


def test_agent_stops_when_cancelled(host, monkeypatch):
    call = {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t", "name": "character_info", "input": {}}]}
    monkeypatch.setattr(providers, "_request", Script(call))
    a = agent.Agent(providers.make("anthropic", "k"), host)
    a.send("x", lambda kind, data: setattr(a, "cancelled", True) if kind == "text" or kind == "tool" else None)
    assert a.messages[-1]["content"][0]["type"] == "tool_result"           # a chamada teve resultado


def test_gemini_thinks_low_and_drops_the_level_on_models_without_it(host, monkeypatch):
    done = {"candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}}]}
    script = Script(done)
    monkeypatch.setattr(providers, "_request", script)
    p = providers.make("gemini", "k", "gemini-x")
    agent.Agent(p, host).send("hi")
    assert script.requests[0][2]["generationConfig"] == {"thinkingConfig": {"thinkingLevel": "low"}}
    sent = []

    def old_model(url, headers, body=None, method=None):
        sent.append(json.loads(json.dumps(body)))
        if "generationConfig" in body:
            raise providers.ProviderError("HTTP 400: Thinking level is not supported for this model.", 400)
        return done
    monkeypatch.setattr(providers, "_request", old_model)
    agent.Agent(p, host).send("hi")
    agent.Agent(p, host).send("again")
    assert ["generationConfig" in b for b in sent] == [True, False, False]   # tenta uma vez só


def test_images_already_seen_are_not_sent_again():
    calls = [(providers.Call("c", "view_animations", {}), tools.Result("rows: 200", [b"png"]))]
    g, msgs = providers.make("gemini", "k"), []
    g.add_results(msgs, calls)
    g.add_results(msgs, calls)
    g.forget_images(msgs)
    assert [len(m["parts"]) for m in msgs] == [1, 2]                  # só a última leva fica com a imagem
    assert msgs[0]["parts"][0]["functionResponse"]["response"] == {"result": "rows: 200"}
    o, msgs = providers.make("openai", "k"), []
    o.add_results(msgs, calls)
    o.add_results(msgs, calls)
    o.forget_images(msgs)
    assert "already seen" in msgs[1]["content"] and isinstance(msgs[3]["content"], list)
    a, msgs = providers.make("anthropic", "k"), []                     # no cache: fica como está
    a.add_results(msgs, calls)
    a.add_results(msgs, calls)
    a.forget_images(msgs)
    assert all(m["content"][0]["content"][1]["type"] == "image" for m in msgs)


def test_a_long_run_of_reads_gets_a_reminder_to_act(host, monkeypatch):
    read = {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t", "name": "list_states", "input": {}}]}
    monkeypatch.setattr(providers, "_request", Script(*[read] * agent.READS_BEFORE_NUDGE,
                                                      {"stop_reason": "end_turn", "content": []}))
    a = agent.Agent(providers.make("anthropic", "k"), host)
    a.send("x")
    texts = [m["content"][0]["content"][0]["text"] for m in a.messages[2::2]]
    assert len(texts) == agent.READS_BEFORE_NUDGE
    assert "read-only calls in a row" in texts[-1] and not any("read-only" in t for t in texts[:-1])


def test_the_editor_note_is_only_for_when_the_user_points_at_it():
    assert "only when the user refers to it" in agent.SYSTEM and "build_move" in agent.SYSTEM


def test_http_errors_show_the_provider_message(monkeypatch):
    def fail(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error": {"message": "invalid x-api-key"}}'))
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(providers.ProviderError, match="invalid x-api-key"):
        providers.make("anthropic", "bad").list_models()


def test_temporary_errors_are_retried(monkeypatch):
    calls, waits = [], []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def flaky(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {"Retry-After": "1"},
                                         io.BytesIO(b'{"error": {"message": "high demand"}}'))
        return Reply(b'{"data": [{"id": "m"}]}')
    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    monkeypatch.setattr(providers, "_sleep", waits.append)
    assert providers.make("anthropic", "k").list_models() == ["m"]
    assert len(calls) == 3 and waits == [1.0, 1.0]
    monkeypatch.setattr(providers, "RETRIES", 1)

    def busy(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "busy", {},
                                     io.BytesIO(b'{"error": {"message": "high demand"}}'))
    monkeypatch.setattr(urllib.request, "urlopen", busy)
    with pytest.raises(providers.ProviderError, match="HTTP 503: high demand"):
        providers.make("anthropic", "k").list_models()


def test_gemini_retry_delay_in_the_body_is_honored(monkeypatch):
    waits, delay = [], ["20s"]

    def quota(req, timeout):
        body = {"error": {"message": "quota", "details": [{"retryDelay": delay[0]}]}}
        raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(urllib.request, "urlopen", quota)
    monkeypatch.setattr(providers, "_sleep", waits.append)
    monkeypatch.setattr(providers, "RETRIES", 1)
    with pytest.raises(providers.ProviderError, match="HTTP 429: quota"):
        providers.make("gemini", "k").list_models()
    assert waits == [20.0]
    waits.clear()
    delay[0] = "3600s"                     # cota do dia: esperar não adianta, erra logo
    with pytest.raises(providers.ProviderError):
        providers.make("gemini", "k").list_models()
    assert waits == []


@pytest.mark.skipif(sys.platform != "win32", reason="Gerenciador de Credenciais do Windows")
def test_api_keys_go_to_the_credential_manager():
    keys.set("pytest-provider", "sk-çé-123")
    try:
        assert keys.get("pytest-provider") == "sk-çé-123"
    finally:
        keys.delete("pytest-provider")
    assert keys.get("pytest-provider") == ""


def test_problems_have_english_codes_and_translated_messages(host):
    tools.run(host, "set_animation", {"text": ANIM})
    _json(host, "set_state", number=200, text=STATE)          # caixa de ataque, mas nenhum HitDef
    probs = _json(host, "simulate_state", number=200, distance=200)["problems"]
    assert {"kind": "no-hit", "message": "no hit on the dummy at 200 px"} in probs
