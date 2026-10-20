"""Gera charfactory/data/grammar.json a partir da documentação oficial do MUGEN (Elecbyte).

Uso: python tools/build_grammar.py <pasta do MUGEN>
Lê docs/sctrls.html e docs/trigger.html. Os dados gerados são fatos de referência
(nomes de controllers, parâmetros e triggers) usados pelo validador.
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
        optional = _params(_param_block(body, "Optional")) + _params(_param_block(body, "Alternate"))
        desc = re.search(r"<p>(.*?)</p>", body, re.S)
        variants = len(re.findall(r"<dt>Required parameters \(", body, re.I))
        controllers[name.lower()] = {
            "required_one_of_variants": variants > 1,
            "alternate_var_syntax": "Alternate syntax" in body,
            "name": name,
            "description": _text(desc[1]) if desc else "",
            "required": list(dict.fromkeys(p["name"] for p in required if "any_of" not in p)),
            "required_any": [list(x) for x in dict.fromkeys(tuple(p["any_of"]) for p in required if "any_of" in p)],
            "params": {p["name"]: {"type": p["type"], "syntax": p["syntax"]} for p in required + optional},
            "deprecated": "deprecated" in title.lower() or "deprecated" in _text(body)[:200].lower(),
        }

    # "Like ChangeState…": herda os parâmetros do controller citado
    for c in controllers.values():
        m = re.match(r"(?:Like|Same as|Identical to)\s+(\w+)", c["description"], re.I)
        base = controllers.get(m[1].lower()) if m else None
        if base and not c["params"]:
            c["params"], c["required"] = dict(base["params"]), list(base["required"])
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
    OUT.write_text(json.dumps(g, indent=1, ensure_ascii=False), "utf-8")
    print(f"{len(g['controllers'])} controllers, {len(g['triggers'])} triggers -> {OUT}")
