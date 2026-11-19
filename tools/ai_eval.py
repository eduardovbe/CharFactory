"""Avaliação do assistente: roda pedidos fixos num personagem e mede se deram certo, quantos
passos levaram, quantos tokens gastaram e quanto custaram. Serve para saber se uma mudança no
prompt, nas ferramentas ou no modelo melhorou ou piorou o assistente.

    python tools/ai_eval.py --char "C:/MUGEN/chars/kfm/kfm.def"
    python tools/ai_eval.py --char kfm.def --provider openai --model gpt-5 --effort low --repeat 3
    python tools/ai_eval.py --char kfm.def --style guided --only crouch-kick,life --out resultado.json
    python tools/ai_eval.py --user-cases                 # os casos salvos pelo painel (👍/👎, Salvar)
    python tools/ai_eval.py --char kfm.def --record gravado/    # guarda as respostas do modelo
    python tools/ai_eval.py --char kfm.def --replay gravado/    # repete sem rede e sem custo
    python tools/ai_eval.py --char kfm.def --via claude-code --model claude-sonnet-5-5

--via claude-code roda cada caso no Claude Code (`claude -p`, com a assinatura de quem está logado,
sem chave de API) ligado só ao servidor MCP do Char Factory, aberto aqui sobre a cópia do
personagem: mede o caminho dos agentes externos (instruções do MCP, character_brief) e julga o
personagem que ficou na memória do servidor. As ferramentas próprias do Claude Code ficam
desligadas, para ele só mexer no char pelo MCP.

Cada caso roda numa cópia do personagem (numa pasta temporária, levando as notas e os nomes de
animação dele): o original nunca é alterado. Usa a chave do provedor guardada nas configurações
de IA (ou a variável de ambiente). ATENÇÃO: sem --replay, chama o modelo de verdade e gasta
tokens; o custo estimado sai no fim (só com a Anthropic).

--replay repete as respostas gravadas com --record: o modelo "decide" igual, e o que muda é o que
as ferramentas do código atual fazem com essas decisões (regressão sem pagar de novo). Se o
código levar o modelo a outro caminho, as respostas gravadas acabam e o caso falha dizendo isso.

Os casos ficam em tools/ai_eval_cases.json (e, com --user-cases, os salvos no painel). `expect`
diz como o caso é julgado:
- no_writes: só responde, sem mudar o personagem
- fewer_errors: o validador termina com menos erros (ou sem nenhum)
- fewer_issues: termina com menos erros e avisos somados
- new_move_hits (`distance`: a que distância o boneco fica, 50 se faltar; agarrão pede perto): criou ao menos um estado novo e o primeiro deles acerta no simulador
- constant: a constante section.key termina com o valor pedido
- no_new_errors: só a regra geral abaixo
- uses_helper: como new_move_hits, e algum estado novo cria um Helper (projétil, onda…)
- anim_faster: a animação `anim` termina com menos ticks do que tinha
- startup_faster: o estado `state` acerta mais cedo (startup menor no simulador)
- note_saved: guardou uma nota com o texto `contains`, sem mudar o personagem
- asks: não mudou nada e a resposta faz uma pergunta ao usuário
Em todos, o caso falha se o validador terminar com mais erros do que começou. `setup` (lista de
{"tool", "args"}) roda antes, na cópia: serve para plantar um erro ou montar a situação do caso.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from charfactory import project, validate  # noqa: E402
from charfactory.ai import agent, animindex, keys, mcp, memory, providers, tools  # noqa: E402

CASES = Path(__file__).with_name("ai_eval_cases.json")
EXPECTS = ("no_writes", "fewer_errors", "new_move_hits", "constant", "no_new_errors", "uses_helper",
           "anim_faster", "startup_faster", "note_saved", "asks", "fewer_issues")


def _errors(char) -> int:
    return sum(i.severity == "error" for i in validate.validate_character(char))


def _issues(char) -> int:
    """Erros mais avisos (animação ou estado que não existe é aviso no validador)."""
    return len(validate.validate_character(char))


def _anim_ticks(char, n: int) -> int | None:
    act = char.anims.get(n)
    return sum(max(f.time, 0) for f in act.frames) if act is not None else None


def _startup(host, n: int):
    r = tools.run(host, "simulate_state", {"number": n})
    return None if r.is_error else json.loads(r.text).get("startup")


def _first_hit(host, new: list[int], distance: float = 50) -> tuple[bool, str]:
    r = tools.run(host, "simulate_state", {"number": new[0], "distance": distance})
    if r.is_error:
        return False, f"simulate_state {new[0]}: {r.text[:120]}"
    sim = json.loads(r.text)
    hit = sim.get("outcome") == "hit" or any(e.get("kind") == "hit" for e in sim.get("events", []))
    return hit, f"states {new[:6]}: {sim.get('outcome')}"


def judge(case: dict, host, before: dict, answer: str = "") -> tuple[bool, str]:
    """(passou, motivo) para o estado do personagem depois do pedido."""
    char = host.char
    errors = _errors(char)
    if errors > before["errors"]:
        return False, f"validator errors went from {before['errors']} to {errors}"
    expect = case["expect"]
    new = [n for n in sorted(set(char.all_statedefs(include_common=False)) - before["states"]) if n >= 0]
    if expect == "no_writes":
        return (not char.dirty, "no changes" if not char.dirty else f"changed {sorted(char.dirty)}")
    if expect == "asks":
        asked = "?" in answer
        return (not char.dirty and asked, f"{'asked' if asked else 'did not ask'}, "
                + ("no changes" if not char.dirty else f"changed {sorted(char.dirty)}"))
    if expect == "note_saved":
        notes = memory.notes(char.def_path)
        hit = [n for n in notes if case["contains"].lower() in n.lower()]
        return (bool(hit) and not new, f"notes {notes[:3]}" + (f"; new states {new[:4]}" if new else ""))
    if expect == "anim_faster":
        now = _anim_ticks(char, case["anim"])
        return (now is not None and now < before["anim"], f"anim {case['anim']}: {before['anim']} -> {now} ticks")
    if expect == "startup_faster":
        now = _startup(host, case["state"])
        ok = isinstance(now, (int, float)) and isinstance(before["startup"], (int, float)) and now < before["startup"]
        return ok, f"state {case['state']} startup {before['startup']} -> {now}"
    if expect == "uses_helper":
        if not new:
            return False, "no new state"
        own = char.all_statedefs()
        helper = any(re.search(r"^\s*type\s*=\s*helper\b", own[n][0].text(), re.I | re.M) for n in new)
        hit, why = _first_hit(host, new)
        return hit and helper, f"{why}; {'helper' if helper else 'no helper'}"
    if expect == "fewer_errors":
        ok = errors < before["errors"] or errors == 0
        return ok, f"errors {before['errors']} -> {errors}"
    if expect == "fewer_issues":
        now = _issues(char)
        return now < before["issues"], f"errors and warnings {before['issues']} -> {now}"
    if expect == "new_move_hits":
        return _first_hit(host, new, case.get("distance", 50)) if new else (False, "no new state")
    if expect == "constant":
        value = (char.constants.get(case["section"], case["key"], "") or "").strip()
        return value == case["value"], f"{case['section']}.{case['key']} = {value!r}"
    if expect == "no_new_errors":
        return True, f"errors {before['errors']} -> {errors}"
    raise ValueError(f"unknown expect: {expect}")


class Recorder:
    """Passa os pedidos ao provedor de verdade e guarda as respostas (--record)."""

    def __init__(self, real=None):
        self.real, self.log = real or providers._request, []

    def __call__(self, url, headers, body=None, method=None, stream=None):
        r = self.real(url, headers, body, method, stream=stream)
        self.log.append(r)
        return r


class Player:
    """Devolve as respostas gravadas, na ordem (--replay)."""

    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, url, headers, body=None, method=None, stream=None):
        if not self.replies:
            raise providers.ProviderError("replay: no more recorded answers (the assistant took another path)")
        return self.replies.pop(0)


def _prepare(tmp: Path, def_path: Path, case: dict, host_cls=tools.Host):
    """Cópia do personagem (com as notas e os nomes de animação dele), a preparação do caso e o
    estado antes do pedido."""
    copy = tmp / def_path.parent.name
    shutil.copytree(def_path.parent, copy)
    memory.copy(def_path, copy / def_path.name)
    # a raiz do MUGEN do original: o common1.cns (data/) é lido de lá, só para leitura
    host = host_cls(project.load(copy / def_path.name, project.find_mugen_root(def_path.parent)))
    for step in case.get("setup") or []:
        r = tools.run(host, step["tool"], step.get("args") or {})
        if r.is_error:
            raise SystemExit(f"setup of case {case['id']} failed: {step['tool']}: {r.text[:300]}")
    host.char.dirty.clear()                 # o que a preparação mudou não conta como mudança do agente
    before = {"errors": _errors(host.char), "states": set(host.char.all_statedefs(include_common=False))}
    if "anim" in case:
        before["anim"] = _anim_ticks(host.char, case["anim"])
    if "state" in case:
        before["startup"] = _startup(host, case["state"])
    if case["expect"] == "fewer_issues":
        before["issues"] = _issues(host.char)
    return host, before


def run_case(case: dict, def_path: Path, make_provider, lean: bool = False, style: str | None = None,
             transport=None) -> dict:
    """Roda um caso numa cópia do personagem e devolve a medição. `transport` vai no lugar do
    providers._request do provedor (gravar ou repetir respostas)."""
    with tempfile.TemporaryDirectory(prefix="cf-eval-") as tmp:
        host, before = _prepare(Path(tmp), def_path, case)
        provider = make_provider()
        provider.transport = transport
        a = agent.Agent(provider, host, lean=lean, style=style)
        calls = []
        error = ""
        limit = False

        def on_event(kind, data):
            nonlocal limit
            if kind == "tool":
                calls.append(data.name)
            elif kind == "limit":
                limit = True
        t0 = time.monotonic()
        try:
            answer = a.send(a.preface(host.char) + case["prompt"], on_event)
        except providers.ProviderError as e:
            answer, error = "", str(e)
        seconds = time.monotonic() - t0
        ok, why = (False, f"provider error: {error}") if error else judge(case, host, before, answer)
        if limit:
            ok, why = False, f"ran out of steps; {why}"
        return {"id": case["id"], "ok": ok, "why": why, "steps": len(calls), "tools": calls, "style": a.style,
                "tokens": dict(a.usage), "cost": a.cost, "seconds": round(seconds, 1), "answer": answer[:2000]}


class _CountingHost(tools.Host):
    """Host do servidor MCP no eval: guarda as ferramentas que o agente externo chamou."""

    def __init__(self, char):
        super().__init__(char)
        self.calls: list[str] = []

    def call(self, name, args):
        self.calls.append(name)
        return super().call(name, args)


# ferramentas do próprio Claude Code: desligadas, ele só mexe no personagem pelo MCP
CLAUDE_CODE_OFF = ["Bash", "PowerShell", "Read", "Edit", "Write", "Glob", "Grep", "NotebookEdit",
                   "WebFetch", "WebSearch", "Agent", "Task", "Skill"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_case_claude_code(case: dict, def_path: Path, model: str, effort: str = "", timeout: int = 1500) -> dict:
    """Roda um caso no Claude Code (`claude -p`) ligado ao servidor MCP aberto sobre a cópia."""
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("the claude command (Claude Code) was not found in PATH")
    with tempfile.TemporaryDirectory(prefix="cf-eval-") as tmp:
        host, before = _prepare(Path(tmp), def_path, case, _CountingHost)
        token = mcp.new_token()
        srv = mcp.HttpServer(host, token, _free_port()).start()
        cfg = Path(tmp) / "mcp.json"
        cfg.write_text(json.dumps({"mcpServers": {"charfactory": {
            "type": "http", "url": srv.url, "headers": {"Authorization": f"Bearer {token}"}}}}), "utf-8")
        cmd = [exe, "-p", case["prompt"], "--model", model, "--mcp-config", str(cfg), "--strict-mcp-config",
               "--allowedTools", "mcp__charfactory", "--disallowedTools", *CLAUDE_CODE_OFF,
               "--output-format", "json", "--no-session-persistence", "--setting-sources", "project"]
        if effort:
            cmd += ["--effort", effort]
        # sem chave de API no ambiente: o Claude Code usa a assinatura de quem está logado
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        out, error = {}, ""
        t0 = time.monotonic()
        try:
            p = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
            try:
                out = json.loads(p.stdout) if p.stdout.strip() else {}
            except ValueError:
                out = {}
            if p.returncode or out.get("is_error") or not out:
                error = str(out.get("result") or p.stderr.strip()[-400:] or f"exit code {p.returncode}")
        except subprocess.TimeoutExpired:
            error = f"no answer in {timeout} s"
        finally:
            srv.stop()
        seconds = time.monotonic() - t0
        ok, why = (False, f"claude code: {error}") if error else judge(case, host, before,
                                                                        str(out.get("result") or ""))
        u = out.get("usage") or {}
        tokens = {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
                  "cache_read": u.get("cache_read_input_tokens") or 0,
                  "cache_write": u.get("cache_creation_input_tokens") or 0}
        return {"id": case["id"], "ok": ok, "why": why, "steps": len(host.calls), "tools": host.calls,
                "style": "mcp", "tokens": tokens, "cost": out.get("total_cost_usd"), "turns": out.get("num_turns"),
                "seconds": round(seconds, 1), "answer": str(out.get("result") or "")[:2000]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--char", default="", help="the .def of the character to test on (cases may carry their own)")
    ap.add_argument("--via", default="api", choices=["api", "claude-code"],
                    help="api: the built-in assistant with an API key; claude-code: Claude Code through MCP")
    ap.add_argument("--provider", default="anthropic", choices=sorted(providers.PROVIDERS))
    ap.add_argument("--model", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--effort", default="", choices=["", *providers.EFFORTS])
    ap.add_argument("--style", default="", choices=["", *agent.STYLES],
                    help="instructions: guided (step by step) or goals; default by model")
    ap.add_argument("--lean", action="store_true", help="fewer tools (as for small local models)")
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--user-cases", action="store_true", help="also the cases saved from the assistant panel")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--repeat", type=int, default=1, help="runs per case (models vary between runs)")
    ap.add_argument("--index", action="store_true",
                    help="first name the character's unnamed animations by looking at them (costs tokens)")
    ap.add_argument("--record", default="", help="folder to keep the model's answers in")
    ap.add_argument("--replay", default="", help="folder with answers kept by --record: no network, no cost")
    ap.add_argument("--out", default="", help="write every measurement to this JSON file")
    args = ap.parse_args(argv)

    cases = json.loads(Path(args.cases).read_text("utf-8"))
    if args.user_cases and memory.user_cases_path().exists():
        cases += json.loads(memory.user_cases_path().read_text("utf-8"))
    if args.only:
        wanted = {c.strip() for c in args.only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    bad = [c["id"] for c in cases if c.get("expect") not in EXPECTS]
    if bad:
        print(f"unknown expect in cases: {bad}", file=sys.stderr)
        return 2
    if not args.char and any(not c.get("char") for c in cases):
        print("--char is needed for cases that do not name their character", file=sys.stderr)
        return 2
    if args.via == "claude-code":
        if args.record or args.replay or args.index:
            print("--record, --replay and --index work only with --via api", file=sys.stderr)
            return 2
        return _main_claude_code(args, cases)
    key = keys.get(args.provider)
    if providers.PROVIDERS[args.provider].needs_key and not key and not args.replay:
        print(f"no API key for {args.provider}: set it in AI settings or the environment", file=sys.stderr)
        return 2

    def make():
        return providers.make(args.provider, key or "replay", args.model, args.base_url, args.effort)
    model = make().model
    print(f"{args.provider} · {model} · effort {args.effort or 'default'} · style {args.style or 'by model'}"
          f"{' · lean' if args.lean else ''}{' · replay' if args.replay else ''}")
    if args.index:
        done = set()
        for c in cases:
            path = Path(c.get("char") or args.char).resolve()
            if path in done:
                continue
            done.add(path)
            ch = project.load(path)
            prov = providers.make(args.provider, key, animindex.CHEAP.get(args.provider) or args.model,
                                  args.base_url, "low")
            out = animindex.index(ch, prov)
            print(f"named {len(out.labels)} animations of {path.name} ({sum(out.usage.values())} tokens)"
                  + (f"; errors: {out.errors[0][:200]}" if out.errors else ""))
    results = []
    for case in cases:
        def_path = Path(case.get("char") or args.char).resolve()
        for i in range(args.repeat):
            tape = Path(args.replay or args.record) / f"{case['id']}-{i + 1}.json" if (
                args.replay or args.record) else None
            transport = None
            if args.replay:
                if not tape.exists():
                    print(f"SKIP  {case['id']:<16} no recording at {tape}")
                    continue
                transport = Player(json.loads(tape.read_text("utf-8")))
            elif args.record:
                transport = Recorder()
            r = run_case(case, def_path, make, args.lean, args.style or None, transport)
            if isinstance(transport, Recorder):
                tape.parent.mkdir(parents=True, exist_ok=True)
                tape.write_text(json.dumps(transport.log, ensure_ascii=False), "utf-8")
            results.append(r)
            print(_line(r, case["id"], f" #{i + 1}" if args.repeat > 1 else ""), flush=True)
    return _report(results, args, args.provider, model, priced=not args.replay)


def _line(r: dict, case_id: str, run: str) -> str:
    tok = sum(r["tokens"].values())
    cost = f" ${r['cost']:.3f}" if r["cost"] is not None else ""
    return (f"{'PASS' if r['ok'] else 'FAIL'}  {case_id:<16}{run}  {r['steps']:>2} steps  {tok:>8} tok{cost}"
            f"  {r['seconds']:>6}s  {r['why']}")


def _report(results: list, args, provider: str, model: str, priced: bool = True) -> int:
    passed = sum(r["ok"] for r in results)
    costs = [r["cost"] for r in results]
    total = f" · ${sum(costs):.2f}" if costs and None not in costs and priced else ""
    print(f"\n{passed}/{len(results)} passed · {sum(r['steps'] for r in results)} steps · "
          f"{sum(sum(r['tokens'].values()) for r in results)} tokens{total}")
    if args.out:
        Path(args.out).write_text(json.dumps({"via": args.via, "provider": provider, "model": model,
                                              "effort": args.effort, "style": args.style, "lean": args.lean,
                                              "results": results}, indent=2, default=str), "utf-8")
    return 0 if passed == len(results) else 1


def _main_claude_code(args, cases: list) -> int:
    model = args.model or "claude-sonnet-5-5"
    print(f"claude code (MCP) · {model} · effort {args.effort or 'default'}")
    results = []
    for case in cases:
        def_path = Path(case.get("char") or args.char).resolve()
        for i in range(args.repeat):
            r = run_case_claude_code(case, def_path, model, args.effort)
            results.append(r)
            print(_line(r, case["id"], f" #{i + 1}" if args.repeat > 1 else ""), flush=True)
    return _report(results, args, "claude-code", model)


if __name__ == "__main__":
    sys.exit(main())
