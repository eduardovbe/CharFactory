"""Executa o MUGEN ou o Ikemen GO com um personagem.

Perfis de execução (`RunProfile`): cada um guarda o motor, o oponente, o palco, a IA, a cor, a
vida e a energia de cada lado, os rounds, o tempo, som/música e as opções que só o Ikemen GO
tem (janela, velocidade, energia infinita, sem barras). As opções são as da ajuda de cada
executável (`mugen.exe -h`, `Ikemen_GO.exe -h`): o que um motor não aceita não vai na linha
de comando dele (o MUGEN fecha com "Invalid option")."""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..project import Character, find_mugen_root
from ..i18n import tr

ENGINES = ("auto", "mugen", "ikemen")
ENGINE_NAMES = {"mugen": "MUGEN", "ikemen": "Ikemen GO"}


def char_argument(char: Character, mugen_root: Path, engine: str = "mugen") -> str:
    """Caminho que o motor espera: relativo a chars/ (ex.: 'kfm/kfm.def'); fora de chars/, o
    Ikemen GO carrega o .def pelo caminho completo e o MUGEN não carrega."""
    chars = (mugen_root / "chars").resolve()
    try:
        rel = char.def_path.relative_to(chars)
    except ValueError:
        if engine == "ikemen":
            return char.def_path.as_posix()
        raise ValueError(tr("the character must be inside {0} for the engine to load it").format(chars))
    return rel.as_posix()


def config_problem(root: Path, engine: str) -> str | None:
    """O que faz a luta rápida fechar antes de carregar o personagem: no Ikemen GO, um Motif em
    save/config.ini que não existe (sem ele não há fight.def)."""
    if engine != "ikemen":
        return None
    try:
        text = (root / "save" / "config.ini").read_text("utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"(?im)^\s*Motif\s*=\s*(.+?)\s*$", text)
    if m and not (root / m[1].strip('"')).exists():
        return tr("save/config.ini: Motif = {0} does not exist; Ikemen GO closes without its fight.def").format(m[1])
    return None


def is_ikemen(root: str | Path) -> bool:
    """Pasta do Ikemen GO: tem o Ikemen_GO.exe ou os scripts dele (external/script/main.lua)."""
    root = Path(root)
    return any("ikemen" in p.name.lower() for p in root.glob("*.exe")) or \
        (root / "external" / "script" / "main.lua").exists()


def find_ikemen_exe(root: str | Path) -> Path:
    """Ikemen_GO.exe ou outro nome com 'ikemen' (ex.: Ikemen_GO_x64.exe)."""
    exes = [p for p in Path(root).glob("*.exe") if "ikemen" in p.name.lower()
            and not any(k in p.name.lower() for k in ("setup", "unins", "prev"))]
    if not exes:
        raise FileNotFoundError(tr("no Ikemen GO executable in {0}").format(root))
    return sorted(exes, key=lambda p: len(p.name))[0]


def engine_exe(root: str | Path) -> tuple[Path, str]:
    """(executável, 'mugen' ou 'ikemen') da pasta do motor."""
    from .charkit import find_mugen_exe
    if is_ikemen(root):
        return find_ikemen_exe(root), "ikemen"
    return find_mugen_exe(root), "mugen"   # mugen.exe ou outro nome (ex.: MUGEN-ORIGINAL.exe)


def _containing_engine(char: Character) -> Path | None:
    """Pasta do motor em cujo chars/ o personagem está."""
    for p in char.dir.parents:
        if (p / "chars").is_dir() and (p / "data").is_dir() and p / "chars" in char.dir.parents:
            return p
    return None


def engine_root(char: Character, engine: str = "auto", mugen_dir: str | Path | None = None,
                ikemen_dir: str | Path | None = None) -> tuple[Path | None, str]:
    """(pasta, motor) onde rodar: 'auto' usa o motor da pasta em que o personagem está; fora
    de uma, ZSS vai para o Ikemen GO e o resto para o MUGEN (ou o Ikemen, se só ele estiver
    configurado)."""
    if engine not in ENGINES:
        raise ValueError(tr("engine must be auto, mugen or ikemen"))
    ikemen = Path(ikemen_dir) if ikemen_dir else None
    mugen = Path(mugen_dir) if mugen_dir else (char.mugen_root or find_mugen_root(char.dir))
    if mugen and is_ikemen(mugen) and not ikemen:
        ikemen = mugen                           # a "pasta do MUGEN" informada é um Ikemen
    ikemen = ikemen or char.ikemen_root()
    if engine == "auto":
        here = _containing_engine(char)
        if here:
            return here, "ikemen" if is_ikemen(here) else "mugen"
        if char.is_zss or not mugen or is_ikemen(mugen):
            engine = "ikemen" if ikemen else "mugen"
        else:
            engine = "mugen"
    root = ikemen if engine == "ikemen" else mugen
    return (Path(root) if root else None), engine


def engine_args(exe: Path, p1: str, p2: str | None = None, stage: str | None = None,
                p1_ai: int | None = None, p2_ai: int | None = None, rounds: int | None = None) -> list[str]:
    """Linha de comando da luta rápida; o MUGEN e o Ikemen GO aceitam a mesma: os dois
    personagens primeiro (relativos a chars/), depois -s, -pN.ai e -rounds."""
    args = [str(exe), p1, p2 or p1]
    if stage:
        args += ["-s", stage]
    if p1_ai is not None:
        args += ["-p1.ai", str(p1_ai)]
    if p2_ai is not None:
        args += ["-p2.ai", str(p2_ai)]
    if rounds:
        args += ["-rounds", str(rounds)]
    return args


def engine_log(root: Path, engine: str, since: float = 0) -> str | None:
    """Fim do log que o motor gravou depois de `since`: mugen.log, ou o do Ikemen GO
    (save/logs/Ikemen_<data>.log; versões antigas gravam Ikemen.log na pasta)."""
    if engine == "ikemen":
        logs = [*(root / "save" / "logs").glob("Ikemen*.log"), root / "Ikemen.log"]
    else:
        logs = [root / "mugen.log"]
    new = [p for p in logs if p.exists() and p.stat().st_size and p.stat().st_mtime > since]
    if not new:
        return None
    p = max(new, key=lambda q: q.stat().st_mtime)
    return p.read_text("utf-8" if engine == "ikemen" else "cp1252", errors="replace")[-4000:]


# ====================================================================== perfis

# opções que só o Ikemen GO entende (no MUGEN elas fecham o jogo com "Invalid option")
IKEMEN_ONLY = ("windowed", "time", "speed", "max_power", "no_lifebars", "p1_life_max", "p2_life_max")


@dataclass
class RunProfile:
    """Um jeito de rodar o char: cada campo vazio (0, None, "") fica no padrão do motor."""
    name: str = "Default"
    engine: str = "auto"                   # auto, mugen ou ikemen
    p2: str = ""                           # oponente (relativo a chars/); vazio = o próprio char
    stage: str = ""                        # relativo a stages/; vazio = um que existe
    p1_ai: int = 0                         # 0 = humano; 1-8 = nível da IA
    p2_ai: int = 0
    p1_color: int = 0                      # paleta 1-12; 0 = a padrão
    p2_color: int = 0
    p1_life: int | None = None
    p2_life: int | None = None
    p1_power: int | None = None
    p2_power: int | None = None
    p1_life_max: int | None = None         # Ikemen GO
    p2_life_max: int | None = None
    rounds: int = 0                        # 0 = os do motor; N = joga N rounds e fecha
    time: int | None = None                # Ikemen GO: tempo do round (-1 = sem tempo)
    ai_level: int = 0                      # dificuldade geral 1-8 (0 = a da configuração)
    speed: int | None = None               # Ikemen GO: velocidade do jogo, -9 a 9
    windowed: bool = False                 # Ikemen GO
    no_music: bool = False
    no_sound: bool = False
    max_power: bool = False                # Ikemen GO: energia sempre cheia (testar supers)
    no_lifebars: bool = False              # Ikemen GO
    extra: str = ""                        # outras opções, como na linha de comando

    @classmethod
    def from_dict(cls, d: dict) -> "RunProfile":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    def to_dict(self) -> dict:
        return asdict(self)

    def unsupported(self, engine: str) -> list[str]:
        """Campos preenchidos que o motor não aceita (a janela avisa; a linha os deixa de fora)."""
        if engine == "ikemen":
            return []
        default = RunProfile()
        return [k for k in IKEMEN_ONLY if getattr(self, k) != getattr(default, k)]


def profile_args(exe: Path, engine: str, p1: str, prof: RunProfile, stage: str | None = None) -> list[str]:
    """Linha de comando do perfil para o motor: o que o motor não aceita fica de fora."""
    args = engine_args(exe, p1, prof.p2 or None, stage if stage is not None else (prof.stage or None),
                       prof.p1_ai or None, prof.p2_ai or None, prof.rounds or None)
    ike = engine == "ikemen"
    for n in (1, 2):
        for key, opt in (("color", "color"), ("life", "life"), ("power", "power"), ("life_max", "lifeMax")):
            v = getattr(prof, f"p{n}_{key}")
            if v is None or (key == "color" and not v) or (key == "life_max" and not ike):
                continue
            args += [f"-p{n}.{opt}", str(v)]
    if prof.ai_level:
        args += ["-ailevel", str(prof.ai_level)]
    if ike:
        if prof.time is not None:
            args += ["-time", str(prof.time)]
        if prof.speed:
            args += ["-speed", str(max(-9, min(9, prof.speed)))]
        if prof.windowed:
            args.append("-windowed")
        if prof.max_power:
            args.append("-maxpowermode")
        if prof.no_lifebars:
            args.append("-togglelifebars")
    if prof.no_sound:
        args.append("-nosound")
    elif prof.no_music:
        args.append("-nomusic")
    if prof.extra.strip():
        args += [t[1:-1] if len(t) > 1 and t[0] == t[-1] == '"' else t
                 for t in shlex.split(prof.extra, posix=False)]
    return args


def gui_settings() -> dict:
    """As opções gravadas pela janela (perfis, pastas do MUGEN e do Ikemen), para a linha de
    comando e a IA rodarem com os mesmos perfis."""
    import json
    import os
    try:
        path = Path(os.environ.get("APPDATA", Path.home())) / "charfactory" / "config.json"
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def load_profiles(settings: dict) -> list[RunProfile]:
    """Perfis gravados; a primeira vez, um "Padrão" com as opções antigas (run_p2, run_stage…)."""
    saved = settings.get("run_profiles")
    if saved:
        return [RunProfile.from_dict(d) for d in saved]
    return [RunProfile(tr("Default"), settings.get("run_engine") or "auto", settings.get("run_p2") or "",
                       settings.get("run_stage") or "", p2_ai=int(settings.get("run_p2_ai") or 0))]


def active_profile(settings: dict, name: str | None = None) -> RunProfile:
    profs = load_profiles(settings)
    want = name or settings.get("run_profile")
    return next((p for p in profs if p.name == want), profs[0])


def list_dir_entries(root: Path, what: str) -> list[str]:
    """chars ('kfm', 'pasta/outro.def') ou stages ('stage0.def') de uma pasta do motor, para os
    campos de oponente e palco."""
    base = Path(root) / what
    if not base.is_dir():
        return []
    if what == "stages":
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*.def"))
    out = []
    for d in sorted(base.iterdir(), key=lambda q: q.name.lower()):
        if d.is_dir():
            defs = sorted(d.glob("*.def"))
            if (d / f"{d.name}.def").exists():
                out.append(d.name)
            elif defs:
                out += [f"{d.name}/{x.name}" for x in defs]
        elif d.suffix.lower() == ".def":
            out.append(d.name)
    return out


def run(char: Character, opponent: str | None = None, stage: str | None = None,
        mugen_root: str | Path | None = None, watch_seconds: float = 4.0,
        p1_ai: int | None = None, p2_ai: int | None = None, rounds: int | None = None,
        engine: str = "auto", ikemen_root: str | Path | None = None,
        profile: RunProfile | None = None) -> dict:
    """Roda o char e olha os primeiros segundos. Com `profile`, as opções vêm dele (os
    argumentos soltos que vierem preenchidos ainda mandam)."""
    from .charkit import default_stage
    if profile is not None:
        profile = RunProfile.from_dict(profile.to_dict())
        if engine != "auto":
            profile.engine = engine
        for k, v in (("p2", opponent), ("stage", stage), ("p1_ai", p1_ai), ("p2_ai", p2_ai), ("rounds", rounds)):
            if v:
                setattr(profile, k, v)
        engine = profile.engine
    root, engine = engine_root(char, engine, mugen_root, ikemen_root)
    if not root:
        raise FileNotFoundError(tr("Ikemen GO folder not found; set ikemen_root") if engine == "ikemen"
                                else tr("MUGEN folder not found; set mugen_root"))
    if engine == "mugen" and char.is_zss:
        raise ValueError(tr("ZSS states only run in Ikemen GO"))
    exe, engine = engine_exe(root)
    if profile is not None:
        args = profile_args(exe, engine, char_argument(char, root, engine), profile,
                            profile.stage or default_stage(root))
    else:
        args = engine_args(exe, char_argument(char, root, engine), opponent, stage or default_stage(root),
                           p1_ai, p2_ai, rounds)
    started = time.time()
    proc = subprocess.Popen(args, cwd=root)
    time.sleep(watch_seconds)
    result = {"engine": engine, "pid": proc.pid, "args": args[1:], "running": proc.poll() is None,
              "exit_code": proc.poll()}
    if profile is not None:
        result["profile"] = profile.name
        skipped = profile.unsupported(engine)
        if skipped:
            result["skipped_options"] = skipped
    problem = config_problem(root, engine)
    if problem:
        result["problem"] = problem
    log = engine_log(root, engine, started - 1)
    if log:
        result["mugen_log_tail" if engine == "mugen" else "log_tail"] = log[-3000:]
    return result
