"""Conversa com os provedores de modelo pela API HTTP de cada um (urllib, sem SDKs):
Anthropic (Messages API), OpenAI e compatíveis (Chat Completions: OpenRouter, Ollama, LM Studio…)
e Google Gemini (generateContent).

Cada provedor guarda o histórico no formato nativo dele, devolvendo as mensagens do modelo sem
mexer: blocos de raciocínio (thinking da Anthropic, thoughtSignature do Gemini) precisam voltar
intactos no pedido seguinte. Na Anthropic o histórico é só acrescentado (nunca editado): o
raciocínio preservado dos modelos novos vale só para a conversa exata que o produziu, e quem
limpa os resultados antigos de ferramenta é o servidor (context editing).

Anthropic e OpenAI respondem em streaming (SSE) quando o loop pede: o texto aparece enquanto o
modelo escreve, e a resposta montada no fim tem o mesmo formato da resposta sem streaming.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .tools import Result, Tool

TIMEOUT = 600


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Call:
    id: str
    name: str
    args: dict
    bad_args: str | None = None          # JSON inválido vindo do modelo: volta como erro


@dataclass
class Turn:
    text: str = ""
    calls: list[Call] = field(default_factory=list)
    stop: str = ""
    # tokens desta etapa: input (sem cache), output, cache_read, cache_write
    usage: dict = field(default_factory=dict)


def _sse(lines):
    """Eventos de um corpo text/event-stream: (nome do evento, dados)."""
    event, data = "", []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "", []
        elif not line.startswith(":"):
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
    if data:
        yield event, "\n".join(data)


def _read_stream(r, stream) -> dict:
    """Passa cada evento ao montador do provedor. Uma queda no meio não é repetida: parte do
    texto já apareceu; o Tentar de novo do painel refaz a etapa inteira."""
    try:
        for _event, data in _sse(r):
            if data.strip() == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            stream.feed(obj)
    except (TimeoutError, OSError) as e:
        raise ProviderError(f"the connection dropped while the model was answering ({e})") from None
    return stream.result()


def _request(url: str, headers: dict, body: dict | None = None, method: str | None = None,
             stream=None) -> dict:
    """Pedido com novas tentativas nos erros passageiros (provedor sobrecarregado, limite de
    taxa, conexão caída), esperando o Retry-After quando o provedor manda. Com `stream` (o
    montador de um provedor), lê a resposta em SSE e devolve o que o montador juntou."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(url, data, method=method or ("POST" if data else "GET"),
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                if stream is not None:
                    return _read_stream(r, stream)
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            hint = e.headers.get("Retry-After") if e.headers else None
            m = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', raw)      # o Gemini manda no corpo
            hint = hint or (m[1] if m else None)
            if e.code in RETRY_STATUS and attempt < RETRIES and _seconds(hint) <= MAX_WAIT:
                _sleep(_wait(attempt, hint))
                continue
            try:
                err = json.loads(raw)
                err = err[0] if isinstance(err, list) and err else err
                msg = (err.get("error") or {}).get("message") if isinstance(err.get("error"), dict) else err.get("error")
                msg = msg or err.get("message") or raw
            except (ValueError, AttributeError):
                msg = raw or e.reason
            raise ProviderError(f"HTTP {e.code}: {msg}", e.code) from None
        except TimeoutError:
            raise ProviderError(f"{url.split('/')[2]}: no answer in {TIMEOUT} s") from None
        except (urllib.error.URLError, OSError) as e:
            if attempt < RETRIES:
                _sleep(_wait(attempt, None))
                continue
            raise ProviderError(f"{url.split('/')[2]}: {getattr(e, 'reason', e)}") from None
    raise AssertionError("unreachable")


RETRIES = 3
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}      # 529: Anthropic sobrecarregada
_sleep = time.sleep


MAX_WAIT = 60.0          # pedir para esperar mais que isso (cota do dia) não vale a nova tentativa


def _seconds(retry_after: str | None) -> float:
    try:
        return max(float(retry_after), 0.0) if retry_after is not None else 0.0
    except ValueError:
        return 0.0


def _wait(attempt: int, retry_after: str | None) -> float:
    if retry_after is not None and _seconds(retry_after) > 0:
        return min(_seconds(retry_after), MAX_WAIT)
    return (2, 5, 12, 30)[min(attempt, 3)]


EFFORTS = ("low", "medium", "high")         # nível de raciocínio escolhido nas configurações
# servidores (base URL) que recusaram um recurso opcional (proxy, modelo antigo, servidor local):
# não é pedido de novo a eles nesta sessão
_REFUSED: dict[str, set[str]] = {"stream": set(), "context": set()}
STUB = "(older tool result, {0} characters, removed to keep the conversation short; call the tool again if you need it)"
MIN_STUB = 400                     # resultados menores que isso ficam como estão


def _usage(inp=0, out=0, cache_read=0, cache_write=0) -> dict:
    return {"input": inp or 0, "output": out or 0, "cache_read": cache_read or 0, "cache_write": cache_write or 0}


def _b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def _stub(text) -> str | None:
    """O aviso que substitui um resultado de ferramenta antigo, ou None se ele fica como está."""
    if isinstance(text, str) and len(text) > MIN_STUB and not text.startswith(STUB.split("{")[0]):
        return STUB.format(len(text))
    return None


class Provider:
    name = ""
    label = ""
    default_model = ""
    needs_key = True
    default_base = ""
    STRONG: re.Pattern | None = None      # modelos fortes: recebem o objetivo, não a receita (agent)

    def __init__(self, api_key: str = "", model: str = "", base_url: str = "", effort: str = ""):
        self.api_key = api_key
        self.model = model or self.default_model
        self.base_url = (base_url or self.default_base).rstrip("/")
        self.effort = effort if effort in EFFORTS else ""        # vazio: o padrão de cada provedor
        self.transport = None             # outro _request (o eval grava e repete respostas)

    def _headers(self) -> dict:
        return {}

    def _send(self, url: str, body: dict, stream=None, optional: dict | None = None) -> dict:
        """POST do pedido. `optional` = {recurso: campos do corpo}: se o servidor recusar um campo
        (HTTP 400 que o cita), ele sai, a recusa fica anotada para este servidor e o pedido vai de
        novo. Sem streaming, max_tokens volta ao teto que cabe no tempo de uma resposta inteira."""
        send = self.transport or _request
        optional = dict(optional or {})
        while True:
            try:
                return send(url, self._headers(), body, stream=stream)
            except ProviderError as e:
                name = next((n for n, keys in optional.items()
                             if e.status == 400 and any(k in str(e) for k in keys)), None)
                if name is None:
                    raise
                _REFUSED[name].add(self.base_url)
                for k in optional.pop(name):
                    body.pop(k, None)
                if name == "stream":
                    stream = None
                    if "max_tokens" in body:
                        body["max_tokens"] = min(body["max_tokens"], 16000)

    def price(self, usage: dict) -> float | None:
        """Custo estimado em US$ de uma etapa, ou None quando o preço do modelo não é conhecido."""
        return None

    def add_user(self, msgs: list, text: str) -> None:
        msgs.append({"role": "user", "content": text})

    def _image(self, png: bytes) -> dict:
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(png)}}

    def add_user_images(self, msgs: list, text: str, images: list[bytes]) -> None:
        """Mensagem do usuário com imagens PNG antes do texto."""
        msgs.append({"role": "user", "content": [*map(self._image, images), {"type": "text", "text": text}]})

    def step(self, msgs: list, system: str, tools: list[Tool], on_text=None) -> Turn:
        """Uma etapa do modelo. Com `on_text`, os provedores que sabem fazer streaming chamam
        on_text(pedaço) enquanto o texto chega; os outros ignoram."""
        raise NotImplementedError

    def compact(self, msgs: list, keep: int) -> int:
        """Troca o texto dos resultados de ferramenta antigos (todos menos os `keep` últimos) por
        um aviso curto; devolve quantos caracteres saíram. A Anthropic não precisa: lá o servidor
        limpa (e editar o histórico invalidaria o raciocínio preservado)."""
        return 0

    def add_results(self, msgs: list, results: list[tuple[Call, Result]]) -> None:
        raise NotImplementedError

    def forget_images(self, msgs: list) -> None:
        """Tira do histórico as imagens que o modelo já viu (fica só a última leva): sem cache, cada
        etapa reenviava todas as folhas de animação. A Anthropic guarda o histórico no cache, e
        mexer no começo dele faria pagar tudo de novo; lá elas ficam."""

    def list_models(self) -> list[str]:
        raise NotImplementedError


# ======================================================================= Anthropic

class _AnthropicStream:
    """Junta os eventos SSE da Messages API na mesma mensagem que a resposta sem streaming."""

    def __init__(self, on_text):
        self.on_text = on_text
        self.msg: dict = {}
        self.blocks: dict[int, dict] = {}
        self.json: dict[int, str] = {}

    def feed(self, d: dict) -> None:
        kind, i = d.get("type"), d.get("index")
        if kind == "message_start":
            self.msg = {k: v for k, v in (d.get("message") or {}).items() if k != "content"}
        elif kind == "content_block_start":
            block = dict(d.get("content_block") or {})
            if block.get("type") == "tool_use":
                self.json[i] = ""
            self.blocks[i] = block
        elif kind == "content_block_delta" and i in self.blocks:
            b, delta = self.blocks[i], d.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta":
                b["text"] = b.get("text", "") + delta.get("text", "")
                self.on_text(delta.get("text", ""))
            elif dt == "thinking_delta":
                b["thinking"] = b.get("thinking", "") + delta.get("thinking", "")
            elif dt == "signature_delta":
                b["signature"] = b.get("signature", "") + delta.get("signature", "")
            elif dt == "input_json_delta":
                self.json[i] = self.json.get(i, "") + delta.get("partial_json", "")
        elif kind == "content_block_stop" and i in self.json:
            raw = self.json.pop(i)
            try:
                self.blocks[i]["input"] = json.loads(raw) if raw.strip() else {}
            except ValueError:               # cortado pelo limite de saída: o loop não roda a chamada
                self.blocks[i]["input"] = {}
        elif kind == "message_delta":
            self.msg.update({k: v for k, v in (d.get("delta") or {}).items() if v is not None})
            self.msg.setdefault("usage", {}).update(d.get("usage") or {})
            if d.get("context_management"):
                self.msg["context_management"] = d["context_management"]
        elif kind == "error":
            err = d.get("error") or {}
            status = 529 if err.get("type") == "overloaded_error" else None
            raise ProviderError(f"{err.get('type', 'error')}: {err.get('message', '')}", status)

    def result(self) -> dict:
        for i in self.json:                     # bloco sem content_block_stop (resposta cortada)
            self.blocks[i]["input"] = {}
        return {**self.msg, "content": [self.blocks[i] for i in sorted(self.blocks)]}


class Anthropic(Provider):
    name = "anthropic"
    label = "Anthropic (Claude)"
    default_model = "claude-opus-5-5"
    default_base = "https://api.anthropic.com/v1"
    # modelos que aceitam o fallback do servidor quando um classificador recusa o pedido
    FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
    # raciocínio adaptativo e esforço: Opus 4.6+, Sonnet 4.6 e a família 5 (o Haiku 4.5 e os
    # anteriores recusam). O Opus 5.5 usa 'medium' se nada for pedido: editar golpes em várias
    # etapas rende mais em 'high'
    ADAPTIVE = re.compile(r"^claude-(opus-4-[6-9]|sonnet-4-6|(opus|sonnet|haiku|fable|mythos)-5)")
    STRONG = re.compile(r"^claude-(opus-4-[6-9]|sonnet-4-6|(opus|sonnet|fable|mythos)-5)")
    EFFORT = "high"
    # US$ por milhão de tokens (entrada, saída, leitura do cache), novembro de 2026; a escrita no
    # cache (5 min) custa 1,25x a entrada. Mais específico primeiro
    PRICES = {
        "claude-fable-5-2": (8.0, 40.0, 0.20), "claude-mythos-5-2": (8.0, 40.0, 0.20),
        "claude-fable-5": (10.0, 50.0, 1.0), "claude-mythos-5": (10.0, 50.0, 1.0),
        "claude-opus-5-5": (4.0, 20.0, 0.20), "claude-opus-5": (5.0, 25.0, 0.50), "claude-opus-4": (5.0, 25.0, 0.50),
        "claude-sonnet-5-5": (2.0, 10.0, 0.10), "claude-sonnet-5": (2.0, 10.0, 0.20),
        "claude-sonnet-4-6": (3.0, 15.0, 0.30),
        "claude-haiku-5-5": (0.10, 0.50, 0.01), "claude-haiku-4-5": (1.0, 5.0, 0.10),
    }
    # o servidor limpa os resultados de ferramenta antigos quando o pedido passa de 80 mil
    # tokens, deixando os 6 últimos; limpar pouco de cada vez só gastaria escritas no cache.
    # Limpeza no servidor não conta como edição do histórico (o raciocínio preservado vale)
    CONTEXT_BETA = "context-management-2025-06-27"
    CONTEXT = {"type": "clear_tool_uses_20250919", "trigger": {"type": "input_tokens", "value": 80_000},
               "keep": {"type": "tool_uses", "value": 6},
               "clear_at_least": {"type": "input_tokens", "value": 20_000}}

    def _context(self) -> bool:
        return self.base_url not in _REFUSED["context"]

    def _headers(self) -> dict:
        h = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        betas = (["server-side-fallback-2026-07-01"] if self.model in self.FALLBACK_MODELS else []) + (
            [self.CONTEXT_BETA] if self._context() else [])
        if betas:
            h["anthropic-beta"] = ",".join(betas)
        return h

    def step(self, msgs, system, tools, on_text=None):
        # cache_control no topo: o prefixo (ferramentas, sistema e a conversa até aqui) é lido do
        # cache a cada etapa do loop em vez de cobrado inteiro de novo
        body = {"model": self.model, "max_tokens": 16000, "system": system, "messages": msgs,
                "tools": [{"name": t.name, "description": t.description, "input_schema": t.schema()}
                          for t in tools],
                "cache_control": {"type": "ephemeral"}}
        if not tools:                          # pedido simples (ex.: nomear animações)
            body.pop("tools")
        if self.ADAPTIVE.match(self.model):
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": self.effort or self.EFFORT}
        if self.model in self.FALLBACK_MODELS:
            body["fallbacks"] = "default"
        if self._context() and tools:
            body["context_management"] = {"edits": [self.CONTEXT]}
        stream = None
        if on_text is not None and self.base_url not in _REFUSED["stream"]:
            body["stream"] = True
            body["max_tokens"] = 64000            # sem risco de timeout: mais espaço para o modelo
            stream = _AnthropicStream(on_text)
        r = self._send(f"{self.base_url}/messages", body, stream,
                       {"context": ["context_management"], "stream": ["stream"]})
        content = r.get("content") or []
        # depois de um fallback no meio da resposta, o que veio antes do último bloco 'fallback'
        # (raciocínio e chamadas do modelo que recusou) não volta no histórico
        last = max((i for i, b in enumerate(content) if b.get("type") == "fallback"), default=-1)
        content = [b for i, b in enumerate(content)
                   if i > last or b.get("type") not in ("thinking", "redacted_thinking", "tool_use")]
        u = r.get("usage") or {}
        turn = Turn(stop=r.get("stop_reason") or "", usage=_usage(
            u.get("input_tokens"), u.get("output_tokens"), u.get("cache_read_input_tokens"),
            u.get("cache_creation_input_tokens")))
        if turn.stop == "refusal":
            details = r.get("stop_details") or {}
            turn.text = "The model declined this request" + (
                f" ({details['category']})" if details.get("category") else "") + "."
            return turn                          # a resposta recusada não entra no histórico
        msgs.append({"role": "assistant", "content": content})
        turn.text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        turn.calls = [Call(b["id"], b["name"], b.get("input") or {}) for b in content if b.get("type") == "tool_use"]
        return turn

    def add_results(self, msgs, results):
        blocks = []
        for call, res in results:
            parts = [{"type": "text", "text": res.text}, *map(self._image, res.images)]
            blocks.append({"type": "tool_result", "tool_use_id": call.id, "content": parts,
                           **({"is_error": True} if res.is_error else {})})
        msgs.append({"role": "user", "content": blocks})     # todos os resultados numa mensagem só

    def list_models(self):
        r = _request(f"{self.base_url}/models?limit=100", self._headers())
        return [m["id"] for m in r.get("data", [])]

    def price(self, usage):
        key = next((k for k in self.PRICES if self.model.startswith(k)), None)
        if key is None:
            return None
        inp, out, read = self.PRICES[key]
        return (usage.get("input", 0) * inp + usage.get("output", 0) * out + usage.get("cache_read", 0) * read
                + usage.get("cache_write", 0) * inp * 1.25) / 1_000_000


# ======================================================================= OpenAI e compatíveis

class _OpenAIStream:
    """Junta os pedaços (chunks) do Chat Completions numa resposta sem streaming."""

    def __init__(self, on_text):
        self.on_text = on_text
        self.content, self.refusal, self.finish = "", "", ""
        self.calls: dict[int, dict] = {}
        self.usage: dict | None = None

    def feed(self, d: dict) -> None:
        if d.get("error"):
            err = d["error"]
            raise ProviderError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        if d.get("usage"):
            self.usage = d["usage"]
        for ch in d.get("choices") or []:
            delta = ch.get("delta") or {}
            if delta.get("content"):
                self.content += delta["content"]
                self.on_text(delta["content"])
            if delta.get("refusal"):
                self.refusal += delta["refusal"]
            for tc in delta.get("tool_calls") or []:
                c = self.calls.setdefault(tc.get("index", len(self.calls)), {
                    "id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                fn = tc.get("function") or {}
                c["id"] = tc.get("id") or c["id"]
                if fn.get("name") and not c["function"]["name"]:
                    c["function"]["name"] = fn["name"]
                c["function"]["arguments"] += fn.get("arguments") or ""
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]

    def result(self) -> dict:
        msg = {"content": self.content or None, "refusal": self.refusal or None}
        if self.calls:
            msg["tool_calls"] = [self.calls[i] for i in sorted(self.calls)]
        return {"choices": [{"message": msg, "finish_reason": self.finish}],
                **({"usage": self.usage} if self.usage else {})}


class OpenAI(Provider):
    name = "openai"
    label = "OpenAI (GPT)"
    default_model = "gpt-5"
    default_base = "https://api.openai.com/v1"
    # modelos de raciocínio aceitam reasoning_effort; os outros (gpt-4o, *-chat) recusam o campo
    REASONING = re.compile(r"^(gpt-5(?!.*chat)|o\d)")
    STRONG = re.compile(r"^(gpt-5(?!.*(mini|nano|chat))|o3(?!-mini))")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def step(self, msgs, system, tools, on_text=None):
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, *msgs],
                "tools": [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                            "parameters": t.schema()}} for t in tools]}
        if not tools:
            body.pop("tools")
        if self.effort and self.name == "openai" and self.REASONING.match(self.model):
            body["reasoning_effort"] = self.effort
        stream = None
        if on_text is not None and self.base_url not in _REFUSED["stream"]:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
            stream = _OpenAIStream(on_text)
        r = self._send(f"{self.base_url}/chat/completions", body, stream, {"stream": ["stream"]})
        choices = r.get("choices") or []
        if not choices:
            raise ProviderError(f"empty response: {json.dumps(r)[:300]}")
        m, stop = choices[0].get("message") or {}, choices[0].get("finish_reason") or ""
        calls = m.get("tool_calls") or []
        msg = {"role": "assistant", "content": m.get("content") or (None if calls else "")}
        if calls:
            msg["tool_calls"] = calls
        msgs.append(msg)
        u = r.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        turn = Turn(text=m.get("content") or m.get("refusal") or "", stop=stop, usage=_usage(
            max((u.get("prompt_tokens") or 0) - cached, 0), u.get("completion_tokens"), cached))
        for c in calls:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
                turn.calls.append(Call(c.get("id", ""), fn.get("name", ""), args if isinstance(args, dict) else {}))
            except ValueError:
                turn.calls.append(Call(c.get("id", ""), fn.get("name", ""), {}, fn.get("arguments")))
        return turn

    def _image(self, png):
        return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _b64(png)}}

    def add_results(self, msgs, results):
        images = []
        for call, res in results:
            text = ("ERROR: " if res.is_error else "") + res.text
            msgs.append({"role": "tool", "tool_call_id": call.id, "content": text})
            images += res.images
        if images:          # mensagem de ferramenta só leva texto: as imagens vão numa do usuário
            msgs.append({"role": "user", "content": [
                {"type": "text", "text": "Images returned by the tool calls above:"}, *map(self._image, images)]})

    def forget_images(self, msgs):
        lots = [m for m in msgs if m.get("role") == "user" and isinstance(m.get("content"), list)
                and any(c.get("type") == "image_url" for c in m["content"])]
        for m in lots[:-1]:
            n = sum(c.get("type") == "image_url" for c in m["content"])
            m["content"] = f"({n} image(s) returned by the tool calls above, already seen: not sent again)"

    def compact(self, msgs, keep):
        saved = 0
        results = [m for m in msgs if m.get("role") == "tool"]
        for m in results[:max(len(results) - keep, 0)]:
            stub = _stub(m.get("content"))
            if stub:
                saved += len(m["content"]) - len(stub)
                m["content"] = stub
        return saved

    def list_models(self):
        r = _request(f"{self.base_url}/models", self._headers())
        return sorted(m["id"] for m in r.get("data", []))


class Compatible(OpenAI):
    name = "compatible"
    label = "OpenAI-compatible (OpenRouter, Ollama, LM Studio…)"
    default_model = ""
    default_base = "http://localhost:11434/v1"          # Ollama
    needs_key = False
    STRONG = None                                        # modelos locais: a receita passo a passo


# ======================================================================= Gemini

_GEMINI_DROP = {"additionalProperties", "default", "$schema", "examples"}


def _gemini_schema(s):
    """O Gemini aceita um subconjunto do JSON Schema (OpenAPI): tira o que ele recusa."""
    if isinstance(s, dict):
        return {k: _gemini_schema(v) for k, v in s.items() if k not in _GEMINI_DROP}
    if isinstance(s, list):
        return [_gemini_schema(v) for v in s]
    return s


class Gemini(Provider):
    name = "gemini"
    label = "Google Gemini"
    default_model = "gemini-flash-latest"         # apelido: sempre o Flash mais novo
    default_base = "https://generativelanguage.googleapis.com/v1beta"
    # raciocínio curto a cada etapa: no nível padrão (alto) o Flash pensava ~40 s por chamada de
    # ferramenta. Modelos sem níveis (2.5 e anteriores) recusam o campo: aí ele sai e fica fora
    THINKING = "low"
    STRONG = re.compile(r"pro")

    def __init__(self, api_key: str = "", model: str = "", base_url: str = "", effort: str = ""):
        super().__init__(api_key, model, base_url, effort)
        self.thinking = self.effort or self.THINKING

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key}

    def add_user(self, msgs, text):
        msgs.append({"role": "user", "parts": [{"text": text}]})

    def _image(self, png):
        return {"inlineData": {"mimeType": "image/png", "data": _b64(png)}}

    def add_user_images(self, msgs, text, images):
        msgs.append({"role": "user", "parts": [*map(self._image, images), {"text": text}]})

    def step(self, msgs, system, tools, on_text=None):
        decls = []
        for t in tools:
            d = {"name": t.name, "description": t.description}
            if t.params:
                d["parameters"] = _gemini_schema(t.schema())
            decls.append(d)
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": msgs,
                "tools": [{"functionDeclarations": decls}]}
        if not decls:
            body.pop("tools")
        model = self.model if self.model.startswith("models/") else f"models/{self.model}"
        url = f"{self.base_url}/{model}:generateContent"
        if self.thinking:
            body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": self.thinking}}
        try:
            r = self._send(url, body)
        except ProviderError as e:
            if not (self.thinking and e.status == 400 and "think" in str(e).lower()):
                raise
            self.thinking = ""
            body.pop("generationConfig")
            r = self._send(url, body)
        cands = r.get("candidates") or []
        if not cands:
            block = (r.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(f"no answer from the model{f' (blocked: {block})' if block else ''}")
        content = cands[0].get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        content.setdefault("parts", [])
        msgs.append(content)                         # partes intactas (thoughtSignature volta junto)
        u = r.get("usageMetadata") or {}
        cached = u.get("cachedContentTokenCount") or 0
        turn = Turn(stop=cands[0].get("finishReason") or "", usage=_usage(
            max((u.get("promptTokenCount") or 0) - cached, 0),
            (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0), cached))
        turn.text = "".join(p.get("text", "") for p in content["parts"] if not p.get("thought"))
        for i, p in enumerate(content["parts"]):
            fc = p.get("functionCall")
            if fc:
                turn.calls.append(Call(fc.get("id") or f"call_{len(msgs)}_{i}", fc.get("name", ""), fc.get("args") or {}))
        return turn

    def add_results(self, msgs, results):
        parts = []
        for call, res in results:
            fr = {"name": call.name, "response": {"error": res.text} if res.is_error else {"result": res.text}}
            if not call.id.startswith("call_"):
                fr["id"] = call.id
            parts += [{"functionResponse": fr}, *map(self._image, res.images)]
        msgs.append({"role": "user", "parts": parts})

    def forget_images(self, msgs):
        lots = [m for m in msgs if m.get("role") == "user" and any("inlineData" in p for p in m.get("parts", []))]
        for m in lots[:-1]:
            # a resposta da ferramenta já diz o que a folha mostrava (números e nomes das linhas)
            m["parts"] = [p for p in m["parts"] if "inlineData" not in p]

    def compact(self, msgs, keep):
        saved = 0
        responses = [p["functionResponse"] for m in msgs if m.get("role") == "user"
                     for p in m.get("parts", []) if "functionResponse" in p]
        for fr in responses[:max(len(responses) - keep, 0)]:
            key = "error" if "error" in (fr.get("response") or {}) else "result"
            text = str((fr.get("response") or {}).get(key, ""))
            stub = _stub(text)
            if stub:
                fr["response"] = {key: stub}
                saved += len(text) - len(stub)
        return saved

    def list_models(self):
        r = _request(f"{self.base_url}/models?pageSize=200", self._headers())
        return sorted(m["name"].removeprefix("models/") for m in r.get("models", [])
                      if "generateContent" in m.get("supportedGenerationMethods", []))


PROVIDERS: dict[str, type[Provider]] = {p.name: p for p in (Anthropic, OpenAI, Gemini, Compatible)}


def make(name: str, api_key: str = "", model: str = "", base_url: str = "", effort: str = "") -> Provider:
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ProviderError(f"unknown provider: {name}")
    return cls(api_key, model, base_url, effort)
