"""Textos da documentação dos controllers (MUGEN e Ikemen GO) na língua da interface.

grammar.json e ikemen_docs.json guardam os textos em inglês, como vêm da doc da Elecbyte e
da wiki do Ikemen GO. As traduções ficam em charfactory/data/docs_i18n/<língua>.json, que
mapeia o texto em inglês, exatamente como está nesses arquivos, para o texto traduzido (como
os catálogos de tr()). Texto sem tradução aparece em inglês. tools/i18n_check.py confere se
todos estão traduzidos.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import i18n

DATA = Path(__file__).parent / "data"
DOCS_DIR = DATA / "docs_i18n"


@lru_cache(maxsize=None)
def _catalog(lang: str) -> dict[str, str]:
    try:
        return json.loads((DOCS_DIR / f"{lang}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def doc(text: str) -> str:
    """O texto da doc na língua atual (ou o original, em inglês)."""
    if not text or i18n.language() == "en":
        return text
    return _catalog(i18n.language()).get(text, text)


@lru_cache(maxsize=1)
def ikemen_docs() -> dict[str, dict[str, dict]]:
    """controller -> parâmetro -> {doc, syntax?}, dos parâmetros que o Ikemen GO criou ou mudou."""
    try:
        return json.loads((DATA / "ikemen_docs.json").read_text("utf-8"))["controllers"]
    except (OSError, ValueError, KeyError):
        return {}


def english_texts() -> set[str]:
    """Todo texto de doc que a interface mostra (para conferir as traduções)."""
    from .validate import grammar
    out: set[str] = set()
    for c in grammar()["controllers"].values():
        out.add(c.get("description", ""))
        for p in c["params"].values():
            out.add(p.get("doc", ""))
            out.update(cd for _, cd in p.get("choices", []))
    for params in ikemen_docs().values():
        out.update(p["doc"] for p in params.values())
    out.discard("")
    return out
