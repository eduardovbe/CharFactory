"""Loop do assistente: manda a conversa ao provedor, roda as ferramentas que o modelo pede e
devolve os resultados, até ele responder sem pedir mais nada."""
from __future__ import annotations

from typing import Callable

from . import tools
from .providers import Provider

MAX_STEPS = 40                 # etapas por resposta; ao chegar nisso o painel oferece Continuar
# chamadas só de leitura seguidas antes de o loop lembrar o modelo de agir: modelos rápidos
# ficam explorando (lendo golpes, olhando animações) até a cota acabar
READS_BEFORE_NUDGE = 8
NUDGE = ("\n\n(Char Factory: {0} read-only calls in a row. You have enough to act: make or change "
         "the move now (build_move for a new one), or answer the user.)")
# a resposta bateu no limite de saída: a última chamada pode ter vindo pela metade
TRUNCATED = {"max_tokens", "length", "MAX_TOKENS"}
# histórico acima disso (caracteres; imagem conta IMAGE_CHARS) faz os resultados de ferramenta
# antigos virarem um aviso curto, menos os KEEP_RESULTS últimos. A Anthropic limpa no servidor
HISTORY_BUDGET = 200_000
KEEP_RESULTS = 6
IMAGE_CHARS = 6_000

# ferramentas que o assistente embutido não usa (agrupar tarefas é coisa dos agentes MCP)
HIDDEN = {"begin_edit_task", "end_edit_task"}
# conjunto enxuto para modelos pequenos (locais): o caminho do golpe novo, leitura e ajuste;
# sai o que é raro (apagar, paletas, sons, recursos sem uso), que eles confundem com o resto
LEAN = {"character_info", "validate_character", "save_project", "list_states", "get_state", "get_move",
        "set_state", "edit_state", "get_constants", "set_constant", "list_commands", "add_command",
        "create_move", "build_move", "list_animations", "view_animations", "get_animation",
        "set_animation", "edit_animation", "suggest_clsn", "list_sprites", "view_sprite", "inspect_images",
        "import_sprites", "run_in_engine", "mugen_guide", "mugen_reference", "simulate_state", "more_output",
        "character_brief", "review_move", "save_note", "build_spec_move"}

BASE = (
    "You are the assistant built into Char Factory, an editor for M.U.G.E.N and Ikemen GO fighting game "
    "characters, and an expert in MUGEN's file formats (DEF, CNS, CMD, AIR, SFF, SND), state controllers, "
    "triggers and move design. You work on the character the user has open through your tools: read "
    "what you are about to change, keep the character's own style and numbering, and prefer small, "
    "exact edits to rewriting whole files. After changing states or animations by hand, run "
    "validate_character, and simulate_state to check a move's frame data. Your changes stay in memory "
    "and the user can undo each one with Ctrl+Z; never call save_project unless the user asks to save. "
    "Answer in the language the user writes in, briefly, and say what you changed (state, animation "
    "or command numbers). Each user message starts with an <editor> note from the editor (not typed "
    "by the user) saying which character is open, whether it has unsaved changes and what is on the "
    "screen. Use what is on the screen only when the user refers to it ('this move', 'this "
    "animation', 'here'); otherwise ignore it and do not read it. The first message about a "
    "character also brings a <character> summary (its moves and inputs, animations with names, free "
    "numbers, hit style and the user's notes): rely on it instead of reading those again. When the "
    "user states a lasting decision or preference about the character (numbering, damage, style, "
    "what to avoid), save it with save_note. " +
    "To bring images in as sprites, call inspect_images and then import_sprites (indexed images keep "
    "their embedded palette with mode embedded; big HD images need a scale). run_in_engine opens the "
    "real engine with a run profile, only when the user asks to play or test in the engine. ")
# modelos pequenos e rápidos seguem a receita passo a passo; os fortes recebem o objetivo e o que
# conta como pronto (detalhar cada passo rende menos neles) e não levam o lembrete de agir
SYSTEM = BASE + tools.MOVE_RECIPE
SYSTEM_GOALS = BASE + tools.MOVE_GOALS
STYLES = ("guided", "goals")


def style_for(provider: Provider) -> str:
    """'goals' para os modelos fortes de cada provedor (Provider.STRONG), 'guided' para os outros."""
    rx = provider.STRONG
    return "goals" if rx is not None and rx.search(provider.model or "") else "guided"


def _size(o) -> int:
    """Tamanho aproximado do histórico em caracteres (imagens em base64 contam um valor fixo)."""
    if isinstance(o, str):
        return len(o)
    if isinstance(o, dict):
        if o.get("type") in ("image", "image_url") or "inlineData" in o:
            return IMAGE_CHARS
        return sum(_size(v) for v in o.values())
    if isinstance(o, list):
        return sum(_size(v) for v in o)
    return 8


def catalog(lean: bool = False) -> list[tools.Tool]:
    return [t for t in tools.describe() if t.name not in HIDDEN and (not lean or t.name in LEAN)]


class Agent:
    """Uma conversa. `host.call` roda as ferramentas (na janela, na thread da interface).

    As ferramentas ficam fixas a conversa toda (mudar a lista no meio invalidaria o cache e o
    raciocínio preservado da Anthropic). `stream` liga o texto chegando aos poucos (evento
    'delta'); `usage` e `cost` somam os tokens e o custo estimado da conversa."""

    def __init__(self, provider: Provider, host: tools.Host, system: str | None = None, lean: bool = False,
                 stream: bool = False, style: str | None = None):
        self.provider = provider
        self.host = host
        self.style = style if style in STYLES else style_for(provider)
        self.system = system or (SYSTEM_GOALS if self.style == "goals" else SYSTEM)
        self.lean = lean
        self.tools = catalog(lean)
        self.stream = stream
        self.messages: list = []
        self.cancelled = False
        self.reads = 0                 # chamadas só de leitura desde a última escrita
        self.usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.cost: float | None = 0.0  # None: algum modelo da conversa não tem preço conhecido
        self.briefed = ""              # personagem cujo resumo já foi na conversa
        self.char_path = ""            # personagem da conversa (onde o painel a guarda)

    def preface(self, char) -> str:
        """O resumo do personagem, na primeira mensagem sobre ele (e quando o usuário troca de
        personagem no meio da conversa). Vai na mensagem, não no sistema: o sistema fica fixo."""
        if char is None or str(char.def_path) == self.briefed:
            return ""
        self.briefed = str(char.def_path)
        return f"<character>\n{tools.brief(char)}\n</character>\n\n"

    def state(self) -> dict:
        """O que é preciso para retomar a conversa depois (memory.save_conversation)."""
        return {"provider": self.provider.name, "model": self.provider.model, "style": self.style,
                "lean": self.lean, "messages": self.messages, "usage": self.usage, "cost": self.cost,
                "briefed": self.briefed, "char_path": self.char_path}

    @classmethod
    def restore(cls, data: dict, provider: Provider, host: tools.Host, stream: bool = False) -> "Agent":
        """Retoma com as mesmas instruções e ferramentas (o cache e o raciocínio preservado só
        valem para a conversa exata que os produziu)."""
        a = cls(provider, host, lean=bool(data.get("lean")), stream=stream, style=data.get("style"))
        a.messages = list(data.get("messages") or [])
        a.usage.update({k: int(v) for k, v in (data.get("usage") or {}).items() if k in a.usage})
        a.cost = data.get("cost", 0.0)
        a.briefed = data.get("briefed") or ""
        a.char_path = data.get("char_path") or ""
        return a

    def send(self, text: str, on_event: Callable[[str, object], None] = lambda kind, data: None) -> str:
        """Manda uma mensagem do usuário e roda até a resposta final. Eventos: 'delta' (pedaço
        de texto, com stream), 'text' (str), 'tool' (Call, antes de rodar), 'result' ((Call,
        Result)), 'usage' (totais), 'stopped' e 'limit' (acabaram as etapas: resume continua)."""
        self.provider.add_user(self.messages, text)
        self.reads = 0
        return self.resume(on_event)

    def resume(self, on_event: Callable[[str, object], None] = lambda kind, data: None) -> str:
        """Continua de onde parou (depois de um erro do provedor) sem repetir a mensagem."""
        self.cancelled = False
        final = ""
        on_text = (lambda piece: on_event("delta", piece)) if self.stream else None
        for _ in range(MAX_STEPS):
            turn = self.provider.step(self.messages, self.system, self.tools, on_text)
            self._account(turn.usage, on_event)
            if turn.text:
                final = turn.text
                on_event("text", turn.text)
            if not turn.calls:
                return final
            results = []
            for call in turn.calls:
                if self.cancelled:
                    res = tools.Result("cancelled by the user", is_error=True)
                elif turn.stop in TRUNCATED:
                    res = tools.Result("not run: your reply hit the output limit, so this call may be cut "
                                       "short; make smaller changes (one statedef or animation per call)",
                                       is_error=True)
                elif call.bad_args is not None:
                    res = tools.Result(f"arguments are not valid JSON: {call.bad_args[:200]}", is_error=True)
                else:
                    on_event("tool", call)
                    res = self.host.call(call.name, call.args)
                    res = self._count(call, res)
                on_event("result", (call, res))
                results.append((call, res))
            self.provider.add_results(self.messages, results)    # toda chamada precisa de resultado
            self.provider.forget_images(self.messages)
            if type(self.provider).compact is not Provider.compact and _size(self.messages) > HISTORY_BUDGET:
                self.provider.compact(self.messages, KEEP_RESULTS)
            if self.cancelled:
                on_event("stopped", None)
                return final
        on_event("limit", MAX_STEPS)
        return final

    def _account(self, usage: dict, on_event) -> None:
        if not any(usage.values()):              # provedor que não informa o uso
            return
        for k in self.usage:
            self.usage[k] += usage.get(k, 0)
        c = self.provider.price(usage)
        self.cost = None if c is None or self.cost is None else self.cost + c
        on_event("usage", {**self.usage, "cost": self.cost})

    def _count(self, call, res: tools.Result) -> tools.Result:
        """Conta as leituras seguidas; na N-ésima, o resultado leva o lembrete (uma vez só, e só
        na receita guiada: os modelos fortes decidem sozinhos quando já leram o bastante)."""
        if self.style == "goals":
            return res
        t = tools.REGISTRY.get(call.name)
        if t is not None and t.writes and not res.is_error:
            self.reads = 0
            return res
        self.reads += 1
        if self.reads != READS_BEFORE_NUDGE:
            return res
        return tools.Result(res.text + NUDGE.format(self.reads), res.images, res.is_error)
