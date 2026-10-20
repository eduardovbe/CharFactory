"""Linha de comando do charfactory.

  charfactory info <char.def>                         resumo do personagem
  charfactory validate <char.def>                     erros e avisos
  charfactory render <char.def> <ação> <saida.png|gif> [--palette N] [--clsn]
  charfactory export-sprites <char.def> <pasta> [--group G]
  charfactory movelist <char.def> <saida.html>
  charfactory convert-sff <entrada.sff> <saida.sff> --version 1|2
  charfactory new <pasta> <nome> [--template outro.def]
  charfactory run <char.def> [--p2 kfm] [--stage stage0]
  charfactory extract <arquivo.zip|rar|7z> [--dest pasta]   extrai um personagem compactado
  charfactory pack <char.def> <saida.zip>                   empacota a pasta do personagem
  charfactory unused <char.def>                             sprites, animações e sons sem uso
  charfactory offset <char.def> <anim1> <anim2> <x> <y> <saida.png> [--flip-h] [--flip-v]
  charfactory sim <char.def> <estado ...> [--dist N] [--out pasta] [--gif] [--ticks]
                                               simula o estado tick a tick; --out grava a folha
  charfactory view <char.def> <grupo> <nº> [--palette N] [--scale 2] [--out x.png]   um sprite em PNG
"""
from __future__ import annotations

import argparse
import json
import sys

from . import archive, project, validate
from .formats import sff
from .tools import generate, ops, render, runner
from .i18n import tr


def main(argv: list[str] | None = None) -> int:
    for f in (sys.stdout, sys.stderr):       # o console do Windows viria em cp1252
        if hasattr(f, "reconfigure"):
            f.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="charfactory", description=tr("M.U.G.E.N character editor"))
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("info"); s.add_argument("char")
    s = sub.add_parser("validate"); s.add_argument("char")
    s = sub.add_parser("render"); s.add_argument("char"); s.add_argument("action", type=int)
    s.add_argument("out"); s.add_argument("--palette", type=int); s.add_argument("--clsn", action="store_true")
    s.add_argument("--scale", type=int, default=2)
    s = sub.add_parser("export-sprites"); s.add_argument("char"); s.add_argument("out")
    s.add_argument("--group", type=int); s.add_argument("--palette", type=int)
    s = sub.add_parser("movelist"); s.add_argument("char"); s.add_argument("out")
    s = sub.add_parser("convert-sff"); s.add_argument("src"); s.add_argument("dst")
    s.add_argument("--version", type=int, choices=[1, 2], default=2)
    s = sub.add_parser("new"); s.add_argument("dir"); s.add_argument("name")
    s.add_argument("--template"); s.add_argument("--author", default="")
    s = sub.add_parser("run"); s.add_argument("char"); s.add_argument("--p2"); s.add_argument("--stage")
    s = sub.add_parser("extract"); s.add_argument("archive"); s.add_argument("--dest")
    s.add_argument("--overwrite", action="store_true")
    s = sub.add_parser("pack"); s.add_argument("char"); s.add_argument("out")
    s = sub.add_parser("unused"); s.add_argument("char")
    s = sub.add_parser("offset"); s.add_argument("char")
    for n in ("anim1", "anim2", "x", "y"):
        s.add_argument(n, type=int)
    s.add_argument("out"); s.add_argument("--frame1", type=int, default=0); s.add_argument("--frame2", type=int, default=0)
    s.add_argument("--flip-h", action="store_true"); s.add_argument("--flip-v", action="store_true")
    s.add_argument("--scale", type=int, default=2)
    s = sub.add_parser("sim"); s.add_argument("char"); s.add_argument("states", type=int, nargs="+")
    s.add_argument("--dist", type=float, default=50.0); s.add_argument("--out")
    s.add_argument("--gif", action="store_true"); s.add_argument("--ticks", action="store_true")
    s = sub.add_parser("view"); s.add_argument("char"); s.add_argument("group", type=int)
    s.add_argument("item", type=int); s.add_argument("--palette", type=int)
    s.add_argument("--scale", type=int, default=2); s.add_argument("--out")
    a = p.parse_args(argv)

    def show(obj):
        print(json.dumps(obj, ensure_ascii=False, indent=1))

    if a.cmd == "convert-sff":
        data = sff.load(a.src)
        if a.version == 1:
            # força recodificar tudo como PCX
            for spr in data.sprites:
                spr._image = data.image(spr) if not spr.is_truecolor else None
        data.save(a.dst, version=a.version)
        print(f"{a.dst}: SFF v{data.version_str}, {len(data.sprites)} sprites")
        return 0
    if a.cmd == "new":
        print(generate.new_character(a.dir, a.name, a.template, a.author))
        return 0
    if a.cmd == "extract":
        show(archive.extract(a.archive, a.dest, overwrite=a.overwrite))
        return 0
    if a.cmd == "pack":
        show(archive.pack(a.char, a.out))
        return 0

    c = project.load(a.char)
    if a.cmd == "info":
        show(c.summary())
    elif a.cmd == "validate":
        r = validate.report(validate.validate_character(c))
        for i in r["issues"]:
            print(f"[{i['severity']}] {i['file']} {i['where']}: {i['message']}")
        print(tr("{0} error(s), {1} warning(s)").format(r['errors'], r['warnings']))
        return 1 if r["errors"] else 0
    elif a.cmd == "render":
        if a.out.lower().endswith(".gif"):
            open(a.out, "wb").write(render.render_gif(c, a.action, a.palette, a.clsn, a.scale))
        else:
            render.render_strip(c, a.action, a.palette, a.clsn, a.scale).save(a.out)
        print(a.out)
    elif a.cmd == "export-sprites":
        show(ops.export_sprites(c, a.out, a.group, a.palette))
    elif a.cmd == "movelist":
        generate.movelist_html(c, a.out)
        print(a.out)
    elif a.cmd == "unused":
        from .tools import usage
        show(usage.unused_resources(c))
    elif a.cmd == "offset":
        render.render_offset(c, a.anim1, a.anim2, a.x, a.y, a.frame1, a.frame2, a.flip_h, a.flip_v,
                             scale=a.scale).save(a.out)
        print(f"{a.out}  (pos = {a.x}, {a.y})")
    elif a.cmd == "run":
        show(runner.run(c, a.p2, a.stage))
    elif a.cmd == "sim":
        _sim(c, a)
    elif a.cmd == "view":
        img = render.sprite_image(c, a.group, a.item, a.palette)
        if a.scale > 1:
            img = img.resize((img.width * a.scale, img.height * a.scale), 0)
        out = a.out or str(_out_dir(None) / f"sprite_{c.dir.name}_{a.group}_{a.item}.png")
        img.save(out)
        print(out)
    return 0


def _sim(c, a) -> None:
    from pathlib import Path
    from .tools import simulate
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    for st in a.states:
        sim = simulate.simulate(c, st, p2_dist=a.dist, max_ticks=900)
        print(f"=== {st}  ticks {sim.tick}")
        for e in sim.events:
            if e["kind"] == "estado":
                print(f"  t{e['t']:4d} {e['who']:12s} -> {e['value']}")
            elif e["kind"] == "acerto":
                print(tr("  t{0:4d} {1:12s} HIT {2} vel={3} dmg={4} fall={5} p2state={6}").format(e['t'], e['who'], e['label'], e['value'], e['damage'], e['fall'], e.get('p2state')))
        if getattr(sim, "unsupported", None):
            print(tr("  not simulated:"), dict(sim.unsupported))
        if out:
            simulate.sheet(sim, per_frame=True).save(out / f"move_{st}.png")
            print(tr("  sheet:"), out / f"move_{st}.png")
            if a.gif:
                print("  gif:", simulate.animation(sim, str(out / f"sim_{st}.gif"), step=2, zoom=2))
            if a.ticks:
                simulate.tick_pages(sim, str(out), f"ticks_{st}", every=1, per_page=36, columns=6, zoom=2)
                print("  ticks:", out / f"ticks_{st}_NN.png")


def _out_dir(out):
    """Pasta das imagens: a pedida ou <temp>/charfactory_cli."""
    import tempfile
    from pathlib import Path
    d = Path(out) if out else Path(tempfile.gettempdir()) / "charfactory_cli"
    d.mkdir(parents=True, exist_ok=True)
    return d


if __name__ == "__main__":
    sys.exit(main())
