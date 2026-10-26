"""Servidor MCP (Model Context Protocol) com as ferramentas de ai.tools.

Dois transportes, os dois só com a biblioteca padrão:

- HTTP ("Streamable HTTP" do MCP, respostas em JSON, sem SSE), aberto pela janela enquanto ela
  está aberta: http://127.0.0.1:<porta>/mcp com o cabeçalho Authorization: Bearer <token>. Só
  escuta no 127.0.0.1 e recusa pedidos com Origin de outro site (proteção contra DNS rebinding).
- stdio (`charfactory mcp`): uma mensagem JSON-RPC por linha. Se a janela estiver com o servidor
  ligado, repassa tudo para ela (o agente mexe no personagem aberto, com desfazer); senão roda as
  ferramentas ali mesmo, no personagem passado na linha de comando.
"""
from __future__ import annotations

import base64
import hmac
import json
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import tools

SERVER_NAME = "charfactory"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PORT = 47913
INSTRUCTIONS = (
    "Char Factory is an editor for M.U.G.E.N and Ikemen GO fighting game characters. These tools read "
    "and change the character that is open: states (CNS), commands (CMD), animations and collision "
    "boxes (AIR), constants, sprites and sounds, and run moves in a tick-by-tick simulator for frame "
    "data. Start with character_info. Changes stay in memory (and can be undone in the editor) until "
    "save_project writes them to disk; only save when the user asks. After editing states or "
    "animations by hand, run validate_character, and simulate_state to check a move. " +
    "To bring images in as sprites, call inspect_images and then import_sprites (indexed images keep "
    "their embedded palette with mode embedded; big HD images need a scale). run_in_engine opens the "
    "real engine with a run profile, only when the user asks to play or test in the engine. " + tools.MOVE_RECIPE)


def new_token() -> str:
    return secrets.token_urlsafe(24)


def version() -> str:
    from .. import __version__
    return __version__


# ======================================================================= protocolo

def tool_list() -> list[dict]:
    out = []
    for t in tools.describe():
        out.append({"name": t.name, "description": t.description, "inputSchema": t.schema(),
                    "annotations": {"readOnlyHint": not t.writes,
                                    "destructiveHint": t.name.startswith("remove_"),
                                    "openWorldHint": False}})
    return out


def result_content(r: tools.Result) -> dict:
    content = [{"type": "text", "text": r.text}]
    content += [{"type": "image", "data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"}
                for png in r.images]
    return {"content": content, "isError": r.is_error}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(host: tools.Host, msg) -> dict | None:
    """Responde uma mensagem JSON-RPC (None para notificações)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if "id" not in msg:                      # notificação (initialized, cancelled…): sem resposta
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": SERVER_NAME, "title": "Char Factory", "version": version()},
                  "instructions": INSTRUCTIONS}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        name = params.get("name")
        if name not in tools.REGISTRY:
            return _error(msg_id, -32602, f"unknown tool: {name}")
        result = result_content(host.call(name, params.get("arguments") or {}))
    elif method in ("resources/list", "prompts/list"):
        result = {method.split("/")[0]: []}
    else:
        return _error(msg_id, -32601, f"method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


# ======================================================================= HTTP

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _local_origin(origin: str) -> bool:
    """O Origin é deste computador? Compara o nome inteiro: 'http://localhost.exemplo.com' não é."""
    try:
        u = urllib.parse.urlsplit(origin)
        return u.scheme in ("http", "https") and (u.hostname or "") in _LOCAL_HOSTS
    except ValueError:
        return False


class _Handler(BaseHTTPRequestHandler):
    server_version = "CharFactoryMCP"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):            # sem log no console (no executável nem existe console)
        pass

    def _send(self, code: int, body: dict | None = None, headers: dict | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        # lê o corpo antes de qualquer resposta: recusar sem ler deixa bytes no socket e o
        # Windows derruba a conexão (WinError 10053) antes de o cliente ler o 401/403
        n = int(self.headers.get("Content-Length") or 0)
        self.body = self.rfile.read(n) if n > 0 else b""
        origin = self.headers.get("Origin")
        if origin and not _local_origin(origin):
            self._send(403, _error(None, -32000, "origin not allowed"))
            return False
        if self.path.split("?")[0].rstrip("/") != "/mcp":
            self._send(404, _error(None, -32000, "not found: use /mcp"))
            return False
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        if not hmac.compare_digest(auth.encode(), expected.encode()):
            self._send(401, _error(None, -32001, "missing or wrong token (Authorization: Bearer <token>)"),
                       {"WWW-Authenticate": "Bearer"})
            return False
        return True

    def do_POST(self):  # noqa: N802
        if not self._allowed():
            return
        try:
            msg = json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send(400, _error(None, -32700, "parse error"))
            return
        if isinstance(msg, list):
            replies = [r for r in (handle(self.server.host, m) for m in msg) if r is not None]
            self._send(200, replies) if replies else self._send(202)
            return
        reply = handle(self.server.host, msg)
        if reply is None:
            self._send(202)
        else:
            headers = {"Mcp-Session-Id": self.server.session} if msg.get("method") == "initialize" else None
            self._send(200, reply, headers)

    def do_GET(self):  # noqa: N802
        if self._allowed():                    # sem fluxo SSE: o servidor nunca manda nada sozinho
            self._send(405, None, {"Allow": "POST"})

    def do_DELETE(self):  # noqa: N802
        if self._allowed():
            self._send(200)


class HttpServer:
    """Servidor HTTP do MCP numa thread própria. `host.call` precisa ser seguro para chamar de
    outra thread (o da janela leva a chamada para a thread da interface)."""

    def __init__(self, host: tools.Host, token: str, port: int = DEFAULT_PORT, bind: str = "127.0.0.1"):
        self.httpd = ThreadingHTTPServer((bind, port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.host, self.httpd.token, self.httpd.session = host, token, secrets.token_hex(16)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="charfactory-mcp", daemon=True)

    def start(self) -> "HttpServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ======================================================================= stdio

def _remote(url: str, token: str):
    """Repassa mensagens para o servidor HTTP da janela; None se ele não responder."""
    def post(msg):
        req = urllib.request.Request(url, json.dumps(msg).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json, text/event-stream",
                                              "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=600) as r:
            body = r.read()
        return json.loads(body) if body else None
    try:
        post({"jsonrpc": "2.0", "id": 0, "method": "ping"})
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return post


def app_endpoint() -> tuple[str, str] | None:
    """URL e token do servidor da janela, lidos das configurações dela (se estiver ligado)."""
    from .. import i18n
    try:
        cfg = json.loads(i18n.settings_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not cfg.get("ai_mcp_enabled") or not cfg.get("ai_mcp_token"):
        return None
    return f"http://127.0.0.1:{int(cfg.get('ai_mcp_port') or DEFAULT_PORT)}/mcp", cfg["ai_mcp_token"]


def serve_stdio(host: tools.Host, forward=None, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = _error(None, -32700, "parse error")
        else:
            if forward is not None:
                try:
                    reply = forward(msg) if "id" in msg else (forward(msg), None)[1]
                except (urllib.error.URLError, OSError, ValueError) as e:
                    reply = _error(msg.get("id"), -32000, f"Char Factory window not reachable: {e}")
            else:
                reply = handle(host, msg)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()


def main_stdio(def_path: str | None = None, standalone: bool = False) -> int:
    """`charfactory mcp`: liga-se à janela aberta ou, sem ela, roda as ferramentas aqui."""
    for f in (sys.stdin, sys.stdout):
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8")
    forward = None
    if not standalone and not def_path:
        ep = app_endpoint()
        forward = _remote(*ep) if ep else None
    host = tools.Host()
    if forward is None and def_path:
        host.open_character(def_path)
    serve_stdio(host, forward)
    return 0
