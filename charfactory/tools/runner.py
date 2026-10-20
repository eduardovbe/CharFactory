"""Executa o MUGEN com um personagem."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ..project import Character, find_mugen_root
from ..i18n import tr


def char_argument(char: Character, mugen_root: Path) -> str:
    """Caminho que o MUGEN espera: relativo a chars/ (ex.: 'kfm/kfm.def')."""
    chars = (mugen_root / "chars").resolve()
    try:
        rel = char.def_path.relative_to(chars)
    except ValueError:
        raise ValueError(tr("the character must be inside {0} for MUGEN to load it").format(chars))
    return rel.as_posix()


def run(char: Character, opponent: str | None = None, stage: str | None = None,
        mugen_root: str | Path | None = None, watch_seconds: float = 4.0,
        p1_ai: int | None = None, p2_ai: int | None = None, rounds: int | None = None) -> dict:
    from .charkit import default_stage, find_mugen_exe
    root = Path(mugen_root) if mugen_root else (char.mugen_root or find_mugen_root(char.dir))
    if not root:
        raise FileNotFoundError(tr("MUGEN folder not found; set mugen_root"))
    exe = find_mugen_exe(root)       # mugen.exe ou outro nome (ex.: MUGEN-ORIGINAL.exe)
    p1 = char_argument(char, root)
    args = [str(exe), p1, opponent or p1]
    stage = stage or default_stage(root)
    if stage:
        args += ["-s", stage]
    if p1_ai is not None:
        args += ["-p1.ai", str(p1_ai)]
    if p2_ai is not None:
        args += ["-p2.ai", str(p2_ai)]
    if rounds:
        args += ["-rounds", str(rounds)]
    log = root / "mugen.log"
    before = log.stat().st_mtime if log.exists() else 0
    proc = subprocess.Popen(args, cwd=root)
    time.sleep(watch_seconds)
    result = {"pid": proc.pid, "args": args[1:], "running": proc.poll() is None,
              "exit_code": proc.poll()}
    if log.exists() and log.stat().st_mtime > before:
        result["mugen_log_tail"] = log.read_text("cp1252", errors="replace")[-3000:]
    return result
