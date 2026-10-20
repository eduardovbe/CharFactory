"""Gera charfactory/data/ikemen_params.json: parâmetros que cada controlador aceita no Ikemen GO.

Lê o fonte do Ikemen (src/compiler.go e src/compiler_functions.go) e segue as chamadas de
cada função de controlador (scmap) até stateParam/paramValue, resolvendo prefixos como
`prefix+"time"`. Controlador que varre o IniSection inteiro (`for k := range is`, ex.: Helper
com map.*, Dialogue com textN, VarSet com var(n)) sai como "open": a tradução não descarta
nada dele.

Uso: python tools/build_ikemen_params.py C:/caminho/Ikemen-GO
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_FUNC = re.compile(r"^func \(c \*CharCompiler\) (\w+)\(([^)]*)\)[^{]*\{", re.M)
# varrem o IniSection só para conferir sobras, não para aceitar chaves livres
_NOT_OPEN = {"stateSec", "parseSection"}
_CALL = re.compile(r"\bc\.(\w+)\(")
_ISKEY = re.compile(r"\bis\[([^\]]+)\]")
_PRIM = {"stateParam": 1, "paramValue": 2, "paramValueLoose": 2}
# funções que varrem o IniSection atrás de chaves de um formato (regex da chave, minúsculas)
_DYNAMIC = {"helper": r"map\..+", "mapSetSub": r"map\s*\(.*\)",
            "varSetSub": r"(?:sys|sysf|f)?var\s*\(.*\)"}


def split_args(s: str, start: int) -> tuple[list[str], int]:
    """Argumentos da chamada que começa em s[start] == '('; devolve (args, fim)."""
    depth, quoted, cur, args = 0, False, "", []
    i = start
    while i < len(s):
        ch = s[i]
        if quoted:
            cur += ch
            if ch == "\\":
                cur += s[i + 1]
                i += 1
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
            cur += ch
        elif ch in "([{":
            depth += 1
            if depth > 1:
                cur += ch
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append(cur.strip())
                return args, i
            cur += ch
        elif ch == "," and depth == 1:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    return args, i


def parse_funcs(text: str) -> dict[str, tuple[list[str], str]]:
    out = {}
    ms = list(_FUNC.finditer(text))
    for k, m in enumerate(ms):
        end = ms[k + 1].start() if k + 1 < len(ms) else len(text)
        formals = []
        for part in m[2].split(","):
            bits = part.split()
            if bits:
                formals.append(bits[0])
        # "a, b string" → nomes sem tipo também contam
        out[m[1]] = (formals, text[m.end():end])
    return out


def eval_str(expr: str, env: dict[str, str | None]) -> str | None:
    """Avalia uma expressão de string simples: "lit", nome, a+"lit"; None se não der."""
    parts = [p.strip() for p in expr.split("+")]
    acc = ""
    for p in parts:
        if len(p) >= 2 and p[0] == p[-1] == '"':
            acc += p[1:-1]
        elif re.fullmatch(r"\w+", p) and p in env and env[p] is not None:
            acc += env[p]
        else:
            return None
    return acc


def collect(funcs, name: str, env: dict[str, str | None], seen: set, out: set, flags: dict) -> None:
    key = (name, tuple(sorted(env.items(), key=str)))
    if key in seen or name not in funcs:
        return
    seen.add(key)
    formals, body = funcs[name]
    if name not in _NOT_OPEN and re.search(r"for\s+\w+\s*(?:,\s*\w+\s*)?:=\s*range\s+is\b", body):
        # a varredura aceita só chaves de um formato (map.x, shader.param3, var(1)); o resto
        # continua sendo "Unknown state controller parameter"
        dyn = _DYNAMIC.get(name)
        if name == "shaderSub":
            dyn = re.escape(env["prefix"]) + r"param\d+" if env.get("prefix") is not None else None
        if dyn:
            flags.setdefault("dynamic", set()).add(dyn)
        else:
            flags["open"] = True
    for m in _ISKEY.finditer(body):
        v = eval_str(m[1], env)
        if v:
            out.add(v.lower())
    for m in _CALL.finditer(body):
        callee = m[1]
        args, _ = split_args(body, m.end() - 1)
        if callee in _PRIM:
            i = _PRIM[callee]
            if len(args) > i:
                v = eval_str(args[i], env)
                if v:
                    out.add(v.lower())
                elif args[i].strip():
                    # nome montado em tempo de execução (ex.: fmt.Sprintf("flag%d", i)):
                    # não dá para listar, então o controlador não descarta nada
                    flags.setdefault("unresolved", []).append(f"{name}: {args[i]}")
                    flags["open"] = True
            continue
        if callee not in funcs:
            continue
        cformals, _ = funcs[callee]
        cenv = {}
        for f, a in zip(cformals, args):
            cenv[f] = eval_str(a, env)
        collect(funcs, callee, cenv, seen, out, flags)


def main(root: str) -> None:
    src = Path(root) / "src"
    text = (src / "compiler.go").read_text("utf-8") + "\n" + (src / "compiler_functions.go").read_text("utf-8")
    funcs = parse_funcs(text)
    scmap = dict(re.findall(r'^\s*"(\w+)":\s*c\.(\w+),', text, re.M))
    result = {}
    for sname, fname in sorted(scmap.items()):
        out: set[str] = set()
        flags: dict = {}
        collect(funcs, fname, {}, set(), out, flags)
        result[sname] = {"params": sorted(out), "open": bool(flags.get("open"))}
        if flags.get("dynamic") and not flags.get("open"):
            result[sname]["dynamic"] = sorted(flags["dynamic"])
        if flags.get("unresolved"):
            result[sname]["unresolved"] = sorted(set(flags["unresolved"]))
    commit = ""
    head = Path(root) / ".git" / "HEAD"
    if head.exists():
        ref = head.read_text().strip()
        if ref.startswith("ref:"):
            p = Path(root) / ".git" / ref[5:]
            commit = p.read_text().strip()[:8] if p.exists() else ""
        else:
            commit = ref[:8]
    # gatilhos: o triggerMap do compilador; constantes: os case do switch de const(...)
    tm = re.search(r"var triggerMap = map\[string\]int\{(.*?)\n\}", text, re.S)
    triggers = set(re.findall(r'"([\w.]+)"\s*:', tm.group(1))) if tm else set()
    # e os case da função que lê as expressões (jugglepoints, frame, ... não estão no mapa)
    ev = re.search(r"func \(c \*CharCompiler\) expValue\((.*?)\nfunc ", text, re.S)
    if ev:
        triggers |= set(re.findall(r'case ((?:"[\w.]+",?\s*)+):', ev.group(1)) and
                        re.findall(r'"([\w.]+)"', "\n".join(re.findall(r'case ([^:\n]+):', ev.group(1)))))
    triggers = sorted(triggers)
    cs = re.search(r'case "const":(.*?)\n\t\tdefault:', text, re.S)
    consts = sorted(set(re.findall(r'"([\w.]+)"', "\n".join(
        re.findall(r'case ([^:\n]+):', cs.group(1)))))) if cs else []
    dest = Path(__file__).resolve().parents[1] / "charfactory" / "data" / "ikemen_params.json"
    dest.write_text(json.dumps({"source": "Ikemen-GO " + commit, "sctrls": result,
                                "triggers": triggers, "consts": consts}, indent=1,
                               ensure_ascii=False), "utf-8")
    print(f"{len(result)} controladores, {len(triggers)} gatilhos, {len(consts)} constantes -> {dest}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("uso: python tools/build_ikemen_params.py <clone do Ikemen-GO>")
    main(sys.argv[1])
