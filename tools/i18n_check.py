"""Confere as traduções: todo tr("...") do código tem entrada em cada catálogo, as lacunas
({0}, {1:.2f}…) são as mesmas nas três línguas e nenhum catálogo guarda texto que o código não usa.

    python tools/i18n_check.py          # relatório; sai com erro se faltar algo
    python tools/i18n_check.py --fix    # também tira dos catálogos as entradas sem uso

Para traduzir um texto novo: escreva-o em inglês dentro de tr() no código e acrescente a mesma
chave em charfactory/locale/pt_BR.json e charfactory/locale/es.json.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "charfactory"
LOCALES = ("pt_BR", "es")
HOLE = re.compile(r"(?<!\{)\{(\d+)[^{}]*\}(?!\})")


def code_keys() -> set[str]:
    """Textos passados a tr() no código, mais os nomes dos temas embutidos (traduzidos por nome)."""
    keys: set[str] = set()
    for p in PKG.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        for n in ast.walk(ast.parse(p.read_text("utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr" and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                keys.add(n.args[0].value)
    for p in (PKG / "gui" / "themes").glob("*.json"):
        keys.add(json.loads(p.read_text("utf-8"))["name"])
    return keys


def catalog(lang: str) -> dict[str, str]:
    return json.loads((PKG / "locale" / f"{lang}.json").read_text("utf-8"))


def holes(text: str) -> list[str]:
    return sorted(HOLE.findall(text))


def check() -> dict:
    keys = code_keys()
    report = {"missing": {}, "stale": {}, "holes": {}}
    for lang in LOCALES:
        cat = catalog(lang)
        report["missing"][lang] = sorted(keys - set(cat))
        report["stale"][lang] = sorted(set(cat) - keys)
        report["holes"][lang] = sorted(k for k in keys & set(cat) if holes(k) != holes(cat[k]))
    return report


def main() -> int:
    r = check()
    bad = False
    for kind in ("missing", "holes", "stale"):
        for lang, items in r[kind].items():
            if items:
                print(f"{kind} [{lang}]: {len(items)}")
                for k in items[:40]:
                    print("   ", repr(k))
                bad |= kind != "stale"
    if "--fix" in sys.argv:
        for lang in LOCALES:
            cat = {k: v for k, v in catalog(lang).items() if k not in set(r["stale"][lang])}
            (PKG / "locale" / f"{lang}.json").write_text(
                json.dumps(dict(sorted(cat.items())), ensure_ascii=False, indent=1) + "\n", "utf-8")
        print("unused entries removed")
    if not bad:
        print("translations complete:", len(code_keys()), "texts")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
