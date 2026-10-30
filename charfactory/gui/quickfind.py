"""Ctrl+F de cada aba: procura só no que a aba mostra e leva até o item escolhido. Animações
por número ou nome, sprites por grupo e item, sons por grupo e número, statedefs por número ou
comentário, comandos pelo nome ou pela sequência, estados do simulador. Nas abas de código,
procurar o texto no arquivo continua sendo o último item da lista.
"""
from __future__ import annotations

import re

from ..i18n import tr

# "200", "200,3", "200 3": a busca é um número (ou grupo e item)
_NUMS = re.compile(r"^\s*(-?\d+)(?:\s*[, ]\s*(-?\d+))?\s*$")
_LEAD = re.compile(r"^\s*(-?\d+)(?:\s*,\s*(-?\d+))?")


def score(query: str, label: str, detail: str) -> int | None:
    """Busca por número: o item com esse número (ou grupo e item) primeiro, depois os do mesmo
    grupo e os que começam com ele. Texto: a busca aproximada da paleta de comandos."""
    from .dialogs import fuzzy_score
    m = _NUMS.match(query)
    if not m:
        return fuzzy_score(query, f"{label} {detail}")
    want = [int(x) for x in m.groups() if x is not None]
    lead = _LEAD.match(label)
    have = [int(x) for x in lead.groups() if x is not None] if lead else []
    if have[:len(want)] == want:
        return 3000 if len(have) == len(want) else 2000
    if len(want) == 1 and have and str(have[0]).startswith(m[1]):
        return 1000 - len(str(have[0]))
    q = query.strip()
    return 100 if q in label or q in detail else None


def entries(win, page) -> tuple[str, list] | None:
    """(texto de ajuda, itens) do Ctrl+F da aba, ou None quando ela só tem a busca no texto."""
    c = win.ctx.char
    if c is None:
        return None
    if page is win.anims:
        from ..ai.tools import STANDARD_ACTIONS
        out = []
        for n in page.numbers():
            a = c.anims.get(n)
            name = c.anims.name_of(n) or STANDARD_ACTIONS.get(n, "")
            out.append((f"{n}  {name}".rstrip(), tr("{0} frames").format(len(a.frames) if a else 0),
                        lambda n=n: page.select_action(n)))
        return tr("Animation number or name (e.g. 200, jump)"), out
    if page is win.sprites:
        return tr("Sprite group or group,item (e.g. 200 or 200,3)"), [
            (f"{s.group},{s.item}", f"{s.width}×{s.height}", lambda g=s.group, i=s.item: page.select(g, i))
            for s in page.sprites()]
    if page is win.sounds:
        out = []
        for s in page.sounds():
            secs = s.info().get("seconds")
            out.append((f"{s.group},{s.number}", f"{secs:.2f} s" if isinstance(secs, (int, float)) else "",
                        lambda g=s.group, n=s.number: page.select(g, n)))
        return tr("Sound group or group,number (e.g. 5 or 5,0)"), out
    if page is win.simulator:
        box = page.state
        return tr("State to simulate: number or name"), [
            (box.itemText(i), "", lambda n=box.itemData(i): page.run_state(n))
            for i in range(box.count()) if box.itemData(i) is not None]
    if page in (win.states, win.cmds, win.air_code):
        return _code_entries(win, page)
    return None


_SD = re.compile(r"^\s*\[\s*statedef\s+(-?\d+)\s*[\];]", re.I)      # ; no cabeçalho do ZSS
_ACTION = re.compile(r"^\s*\[\s*begin\s+action\s+(-?\d+)\s*\]", re.I)
_COMMAND = re.compile(r"^\s*\[\s*command\s*\]", re.I)
_ENTRY = re.compile(r"^\s*\[\s*state\s+-1\s*,\s*([^\]]*)\]", re.I)


def _comment(lines: list[str], k: int) -> str:
    """Comentário na linha do cabeçalho ou logo acima dele (o nome do golpe, em geral)."""
    same = lines[k].split(";", 1)
    if len(same) > 1 and same[1].strip():
        return same[1].strip()
    j = k - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j >= 0 and lines[j].lstrip().startswith(";"):
        return lines[j].lstrip(" \t;-=").strip()
    return ""


def _zss_comment(lines: list[str], k: int) -> str:
    """No ZSS o nome do golpe vem num comentário # logo acima do [StateDef]."""
    j = k - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j >= 0 and lines[j].lstrip().startswith("#"):
        return lines[j].lstrip(" \t#-=").strip()
    return ""


def _value(lines: list[str], k: int, key: str) -> str:
    """`key = valor` dentro do bloco que começa na linha k."""
    for line in lines[k + 1:]:
        if line.lstrip().startswith("["):
            break
        m = re.match(rf"^\s*{key}\s*=\s*(.*)$", line.split(";", 1)[0], re.I)
        if m:
            return m[1].strip().strip('"')
    return ""


def _code_entries(win, page) -> tuple[str, list]:
    out = []
    many = len(page.files) > 1
    for fi, f in enumerate(page.files):
        lines = f["edit"].toPlainText().split("\n")
        where = f["path"].name if many else ""

        def go(fi=fi, ln=1):
            page.tabs.setCurrentIndex(fi)
            page.files[fi]["edit"].goto_line(ln)
        for k, line in enumerate(lines):
            if page is win.cmds and _COMMAND.match(line):
                name, seq = _value(lines, k, "name"), _value(lines, k, "command")
                out.append((f"{name}  {seq}", where or tr("command"), lambda g=go, ln=k + 1: g(ln=ln)))
                continue
            m = (_ACTION if page is win.air_code else _SD).match(line)
            if m:
                n = int(m[1])
                zss = bool(f.get("zss"))
                anim = _value(lines, k, "anim") if page is win.states and not zss else ""
                name = (_zss_comment(lines, k) if zss else _comment(lines, k)) or (tr("anim {0}").format(anim) if anim else "")
                out.append((f"{n}  {name}".rstrip(), where, lambda g=go, ln=k + 1: g(ln=ln)))
                continue
            m = _ENTRY.match(line) if page is not win.air_code else None
            if m:
                value = _value(lines, k, "value")
                out.append((f"{value}  ← [State -1, {m[1].strip()}]", where or tr("entry"),
                            lambda g=go, ln=k + 1: g(ln=ln)))
    hint = {win.states: tr("Statedef number or name (e.g. 1000, palm)"),
            win.cmds: tr("Command name or input (e.g. QCF_x, D, DF, F)"),
            win.air_code: tr("Animation number or name")}[page]
    return hint, out
