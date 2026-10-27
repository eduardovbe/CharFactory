"""Gera charfactory/data/grammar.json a partir da documentação oficial do MUGEN (Elecbyte).

Uso: python tools/build_grammar.py <pasta do MUGEN>
Lê docs/sctrls.html e docs/trigger.html. Os dados gerados são fatos de referência
(nomes de controllers, parâmetros e triggers) usados pelo validador; cada parâmetro leva
também o texto da doc, o valor padrão e as opções (postype = p1, p2…) para a janela de
parâmetros do editor de estados.
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "charfactory" / "data" / "grammar.json"


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _sections(doc: str) -> list[tuple[str, str]]:
    """(título h2, html da seção) para cada seção de nível 2."""
    parts = re.split(r'<div class="section" id="[^"]*">\s*<h2>', doc)
    out = []
    for p in parts[1:]:
        m = re.match(r"(.*?)</h2>(.*)", p, re.S)
        if m:
            out.append((_text(m[1]), m[2]))
    return out


def _param_block(body: str, label: str) -> str:
    """Junta todos os blocos '<label> parameters[ (variante)]:' da seção."""
    blocks = re.findall(rf"<dt>{label} (?:parameters|arguments|syntax)[^<]*:</dt>\s*<dd>(.*?)(?=<dt>(?:Required|Optional) (?:parameters|arguments)|<dt>Alternate|<dt>Notes?:|<dt>Example|</div>)",
                        body, re.S | re.I)
    return "".join(blocks)


def _params(block: str) -> list[dict]:
    out = []
    for dt in re.findall(r"<dt>(.*?)</dt>", block, re.S):
        t = _text(dt)
        if "=" not in t:
            continue
        if re.search(r"\bOR\b", t):  # "value = x  OR  value2 = y": exige um dos dois
            alts = [a.split("=")[0].strip().lower() for a in re.split(r"\bOR\b", t)]
            out.extend({"name": a, "type": "", "syntax": t, "any_of": alts} for a in alts)
            continue
        names = [n.strip().lower() for n in t.split("=")[0].split(",")]
        typ = re.search(r"\(([^()]*)\)\s*$", t)
        for n in names:
            if re.match(r"^[a-z0-9_.]+$", n):
                out.append({"name": n, "type": typ[1].strip() if typ else "", "syntax": t})
    return out


class _Node:
    def __init__(self, tag: str, parent=None):
        self.tag, self.parent, self.kids = tag, parent, []

    def text(self) -> str:
        out = []
        for k in self.kids:
            out.append(k if isinstance(k, str) else ("\n" if k.tag == "p" else "") + k.text())
        return "".join(out)

    def find(self, tag: str) -> list["_Node"]:
        return [k for k in self.kids if not isinstance(k, str) and k.tag == tag]


def _tree(fragment: str) -> _Node:
    """Árvore mínima de dl/dt/dd/p (o resto vira texto) para ler os parâmetros aninhados."""
    from html.parser import HTMLParser
    root = _Node("root")

    class P(HTMLParser):
        cur = root

        def handle_starttag(self, tag, attrs):
            if tag in ("dl", "dt", "dd", "p", "pre", "blockquote"):
                n = _Node(tag, self.cur)
                self.cur.kids.append(n)
                self.cur = n

        def handle_endtag(self, tag):
            n = self.cur
            while n is not root and n.tag != tag:
                n = n.parent
            if n is not root:
                self.cur = n.parent

        def handle_data(self, data):
            self.cur.kids.append(data)
    P(convert_charrefs=True).feed(fragment)
    return root


def _clean(t: str) -> str:
    t = re.sub(r"[ \t]*\n[ \t]*", "\n", t.strip())
    t = re.sub(r"(?<!\n)\n(?!\n)", " ", t)          # quebras do HTML viram espaço; parágrafos ficam
    return re.sub(r"[ \t]{2,}", " ", t).strip()


_DEFAULT = re.compile(r"\b(?:defaults?\s+(?:to|is|are)|default\s+value\s+(?:of\s+\S+\s+)?is|"
                      r"defaults?\s+for\s+\S+\s+(?:is|are))\s+(.+?)(?:\.(?:\s|$)|;|,\s+(?:if|and|which|unless|"
                      r"when|so|meaning)\b|\s+\(|$)", re.I)


def _default(doc: str) -> str:
    """'Defaults to 0,0.' -> '0,0' (só quando parece um valor, não uma frase)."""
    m = _DEFAULT.search(doc)
    if not m:
        return ""
    v = m[1].strip().strip('"').rstrip(".")
    v = re.sub(r"^(?:the\s+)?(?:value\s+)?", "", v, flags=re.I)
    v = re.sub(r"\s+if\s+omitted$", "", v, flags=re.I)
    v = {"zero": "0", "one": "1"}.get(v.lower(), v)
    if len(v) > 24 or len(v.split()) > 3 and not re.fullmatch(r"[-\d.,\s]+", v):
        return ""
    return v


def param_docs(body: str) -> dict[str, dict]:
    """Nome do parâmetro -> {doc, default, choices} a partir dos <dl> da seção do controller.
    "See below." herda o texto do parâmetro seguinte (como os size.* do Helper)."""
    out: dict[str, dict] = {}
    pending: list[str] = []
    top = next(iter(_tree(body).find("dl")), None)
    if top is None:
        return out
    for block_dt, block_dd in zip(top.find("dt"), top.find("dd")):
        label = _clean(block_dt.text()).lower()
        if not re.match(r"(required|optional) (parameters|arguments)|alternate", label):
            continue
        inner = next(iter(block_dd.find("dl")), None)
        if inner is None:
            continue
        for dt, dd in zip(inner.find("dt"), inner.find("dd")):
            head = _clean(dt.text())
            names = [n.strip().lower() for n in re.split(r"\bOR\b", head)]
            names = [re.split(r"[=(\s]", n.split("=")[0].strip())[0] for n in names]
            names = [n for part in names for n in part.split(",") if re.match(r"^[a-z0-9_.]+$", n)]
            choices, default = [], ""
            for sub in dd.find("dl"):
                for cdt, cdd in zip(sub.find("dt"), sub.find("dd")):
                    word = _clean(cdt.text())
                    if len(word) <= 24 and "=" not in word:
                        cdoc = _clean(cdd.text())
                        choices.append([word, cdoc])
                        if re.search(r"\bthis is the default\b|\bdefault value for\b|\(default\)", cdoc, re.I):
                            default = word
            prose = [k if isinstance(k, str) else k.text() for k in dd.kids
                     if isinstance(k, str) or k.tag != "dl"]
            doc = _clean("".join(prose))
            info = {"doc": doc, "default": default or _default(doc), "choices": choices,
                    "deprecated": bool(re.match(r"this parameter is deprecated", doc, re.I))}
            if re.fullmatch(r"see below\.?", doc, re.I):
                pending += names
                continue
            for n in pending + names:
                out.setdefault(n, info)
            pending = []
    return out


def _bare_params(body: str) -> list[dict]:
    """Parâmetros listados sem '=' na doc, como 'size.xscale (float)' do Helper."""
    out = []
    for dt in re.findall(r"<dt>(.*?)</dt>", _param_block(body, "Optional"), re.S):
        m = re.fullmatch(r"([a-z0-9_.]+)\s*\(([^()]*)\)", _text(dt), re.I)
        if m and "." in m[1]:
            out.append({"name": m[1].lower(), "type": m[2].strip(), "syntax": _text(dt)})
    return out


_MISSING_DOCS = {
    ("angledraw", "scale"): "Scales the player sprite while it is drawn (x scale, y scale). Defaults to 1,1.",
}


def build(mugen: Path) -> dict:
    sctrl_doc = (mugen / "docs" / "sctrls.html").read_text("utf-8", errors="replace")
    sctrl_doc = sctrl_doc[sctrl_doc.find('id="controller-reference"'):]
    controllers = {}
    for title, body in _sections(sctrl_doc):
        name = title.split("(")[0].strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9]+$", name):
            continue
        req_block = _param_block(body, "Required")
        required = [] if re.match(r"\s*none", _text(req_block), re.I) else _params(req_block)
        if "<dt>Required arguments:</dt>" in body and not required:
            required = _params(re.search(r"<dt>Required arguments:</dt>\s*<dd>(.*?)</dd>\s*<dt>", body, re.S)[1])
        optional = _params(_param_block(body, "Optional")) + _params(_param_block(body, "Alternate")) \
            + _bare_params(body)
        docs = param_docs(body)
        desc = re.search(r"<p>(.*?)</p>", body, re.S)
        variants = len(re.findall(r"<dt>Required parameters \(", body, re.I))
        controllers[name.lower()] = {
            "required_one_of_variants": variants > 1,
            "alternate_var_syntax": "Alternate syntax" in body,
            "name": name,
            "description": _text(desc[1]) if desc else "",
            "required": list(dict.fromkeys(p["name"] for p in required if "any_of" not in p)),
            "required_any": [list(x) for x in dict.fromkeys(tuple(p["any_of"]) for p in required if "any_of" in p)],
            "params": {p["name"]: {"type": p["type"], "syntax": p["syntax"],
                                   **{k: v for k, v in docs.get(p["name"], {}).items() if v}}
                       for p in required + optional},
            # só o controller: "This controller is deprecated" (um parâmetro obsoleto, como o
            # helpertype do Helper, ou citar o GameMakeAnim, como o Explod, não conta)
            "deprecated": "deprecated" in title.lower()
                          or bool(re.search(r"\bthis controller\b[^.]*\bdeprecated\b", _text(desc[1]) if desc else "", re.I)),
        }

    # parâmetros que a doc lista sem texto
    for (c, p), doc in _MISSING_DOCS.items():
        spec = controllers.get(c, {}).get("params", {}).get(p)
        if spec is not None and not spec.get("doc"):
            spec["doc"] = doc

    # "Like ChangeState…", "This is the same as DisplayToClipboard…": herda os parâmetros do
    # controller citado; ModifyExplod ("Syntax is basically the same as Explod") só muda os que
    # vierem, então não herda os obrigatórios
    for c in controllers.values():
        m = re.search(r"\b(?:Like|Same as|Identical to)\s+(\w+)", c["description"], re.I)
        base = controllers.get(m[1].lower()) if m else None
        if base and not c["params"]:
            c["params"] = dict(base["params"])
            c["required"] = [] if c["name"].lower().startswith("modify") else list(base["required"])
            c["inherits"] = base["name"]

    trig_doc = (mugen / "docs" / "trigger.html").read_text("utf-8", errors="replace")
    triggers = {}
    for title, body in _sections(trig_doc):
        name = title.split("(")[0].strip()
        if re.match(r"^[A-Za-z][A-Za-z0-9]*$", name):
            desc = re.search(r"<p>(.*?)</p>", body, re.S)
            info = {"name": name, "description": _text(desc[1]) if desc else ""}
            triggers[name.lower()] = info
            fmt = re.search(r"Format:(.*?)(?:Arguments:|Return type:)", _text(body), re.S)
            for variant in re.findall(r"^\s*(?:\d+\.)?\s*([A-Za-z][A-Za-z0-9]*)\s*$", fmt[1] if fmt else "", re.M):
                triggers.setdefault(variant.lower(), {**info, "name": variant})

    # aliases aceitos pelo MUGEN e usados pelo KFM oficial, mas sem seção própria na doc
    triggers.setdefault("statetime", {"name": "StateTime", "description": "Alias de Time."})
    for name in ("reversaldef", "projectile"):  # a doc diz: aceitam também os parâmetros do HitDef
        for k, v in controllers["hitdef"]["params"].items():
            controllers[name]["params"].setdefault(k, v)

    return {
        "source": "Elecbyte M.U.G.E.N 1.0 documentation (docs/sctrls.html, docs/trigger.html)",
        "controllers": controllers,
        "triggers": triggers,
        "statedef_params": ["type", "movetype", "physics", "anim", "velset", "ctrl", "poweradd",
                            "juggle", "facep2", "hitdefpersist", "movehitpersist",
                            "hitcountpersist", "sprpriority"],
        "state_common_params": ["type", "triggerall", "persistent", "ignorehitpause"],
        "redirects": ["parent", "root", "helper", "target", "partner", "enemy", "enemynear", "playerid"],
    }


if __name__ == "__main__":
    g = build(Path(sys.argv[1]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(g, indent=1, ensure_ascii=False), "utf-8", newline="\n")
    print(f"{len(g['controllers'])} controllers, {len(g['triggers'])} triggers -> {OUT}")
