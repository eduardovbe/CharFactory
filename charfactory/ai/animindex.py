"""Nomes das animações tirados das imagens: a maioria dos chars não comenta as ações do .air, e
sem nome o modelo precisa olhar dezenas de folhas para achar "o chute agachado". Aqui as folhas
vão em lotes para um modelo com visão (de preferência um barato), que devolve um nome curto por
animação; os nomes ficam na memória do char (memory.save_labels) e aparecem no list_animations.

`plan` só separa os lotes e anota a assinatura dos quadros (rápido); a folha de cada lote é
desenhada por `render` na hora de enviar. Na janela, render roda a ferramenta view_animations na
thread da interface, um lote por vez, enquanto a conversa com o provedor fica em outra thread.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from . import memory, tools
from .providers import Provider, ProviderError

BATCH = 12                          # animações por folha (o view_animations aceita até 16)
SYSTEM = ("You label the animations of a 2D fighting game character from contact sheets. Each row "
          "of the sheet is one animation: its number on the left, then a few of its frames (red boxes "
          "mark attack frames).")
PROMPT = ("Give each row a short English label (2 to 6 words) saying what the motion is, the way a "
          "fighting game designer would: 'standing light punch', 'crouching low kick', 'jump rising', "
          "'uppercut launcher', 'dash forward', 'hit reaction high', 'fireball throw', 'super charge "
          "pose', 'win pose'. Say crouching, standing or in the air when it shows. If a row is unclear, "
          "describe the pose. Answer only with one line per row, 'NUMBER: label', nothing else.\n"
          "Rows: {0}")
LINE = re.compile(r"^\s*[-*]?\s*(-?\d+)\s*[:=\-–]\s*(.+?)\s*$")
# modelo barato com visão para indexar, quando o usuário não escolhe outro
CHEAP = {"anthropic": "claude-haiku-5-5", "gemini": "gemini-flash-latest"}


@dataclass
class Batch:
    numbers: list[int]
    sigs: dict[int, str]            # assinatura dos quadros de cada animação, para o nome valer


@dataclass
class Outcome:
    labels: dict[int, str] = field(default_factory=dict)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    errors: list[str] = field(default_factory=list)


def unnamed(char) -> list[int]:
    """Animações com quadros e sem nome nenhum (nem no .air, nem do motor, nem das imagens)."""
    return [n for n in char.anims.numbers()
            if (act := char.anims.get(n)) is not None and act.frames and not tools._anim_name(char, n)]


def plan(char, numbers: list[int] | None = None) -> list[Batch]:
    numbers = unnamed(char) if numbers is None else numbers
    sigs = {n: memory.signature(act) for n in numbers if (act := char.anims.get(n)) is not None}
    return [Batch(chunk, {n: sigs[n] for n in chunk if n in sigs})
            for chunk in (numbers[i:i + BATCH] for i in range(0, len(numbers), BATCH))]


def sheet(char, numbers: list[int]) -> bytes:
    return tools.view_animations(None, char, numbers).images[0]


def parse(text: str, numbers: list[int]) -> dict[int, str]:
    want, out = set(numbers), {}
    for line in text.splitlines():
        m = LINE.match(line)
        if m and int(m[1]) in want:
            label = m[2].strip().strip("'\"`.").lower()
            if label:
                out[int(m[1])] = label[:60]
    return out


def label(def_path, batches: list[Batch], provider: Provider, render: Callable[[list[int]], bytes],
          progress=lambda done, total: None) -> Outcome:
    """Desenha a folha de cada lote, manda ao provedor e grava os nomes na memória a cada lote."""
    res = Outcome()
    for k, b in enumerate(batches):
        msgs: list = []
        provider.add_user_images(msgs, PROMPT.format(", ".join(map(str, b.numbers))), [render(b.numbers)])
        try:
            turn = provider.step(msgs, SYSTEM, [])
        except ProviderError as e:
            res.errors.append(str(e))
            if e.status in (401, 403, 404) or len(res.errors) >= 2:
                break                     # chave, modelo ou visão recusados: não adianta seguir
            continue
        for key in res.usage:
            res.usage[key] += turn.usage.get(key, 0)
        got = {n: name for n, name in parse(turn.text, b.numbers).items() if n in b.sigs}
        if got:
            memory.save_labels(def_path, {n: (name, b.sigs[n]) for n, name in got.items()})
            res.labels.update(got)
        progress(k + 1, len(batches))
    return res


def index(char, provider: Provider, numbers: list[int] | None = None) -> Outcome:
    """Tudo de uma vez, na mesma thread (linha de comando, eval e testes)."""
    return label(char.def_path, plan(char, numbers), provider, lambda nums: sheet(char, nums))
