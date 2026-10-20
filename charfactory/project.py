"""Projeto de personagem MUGEN: o .def e todos os arquivos que ele referencia.

Os arquivos são carregados sob demanda e só os modificados são gravados em save().
"""
from __future__ import annotations

import shutil
from functools import cached_property
from pathlib import Path

from .formats import air, cmd, cns, mtext, sff, snd, zssread
from .i18n import tr


class ProjectError(Exception):
    pass


def find_mugen_root(start: Path) -> Path | None:
    """Sobe a partir de start até achar a pasta com mugen.exe (ou data/ + chars/)."""
    for p in [start, *start.parents]:
        if (p / "mugen.exe").exists() or ((p / "data").is_dir() and (p / "chars").is_dir()):
            return p
    from . import config
    root = config.mugen_root()
    if root and root.is_dir():
        return root
    return None


class Character:
    def __init__(self, def_path: str | Path, mugen_root: str | Path | None = None):
        self.def_path = Path(def_path).resolve()
        if not self.def_path.exists():
            raise ProjectError(tr("file not found: {0}").format(self.def_path))
        self.dir = self.def_path.parent
        self.mugen_root = Path(mugen_root) if mugen_root else find_mugen_root(self.dir)
        self.definition = mtext.load(self.def_path)
        self.dirty: set[str] = set()

    # ------------------------------------------------------------ caminhos

    def info(self, key: str, default: str = "") -> str:
        return (self.definition.get("info", key) or default).strip().strip('"')

    @property
    def name(self) -> str:
        return self.info("name", self.def_path.stem)

    def file_entry(self, key: str) -> str | None:
        v = self.definition.get("files", key)
        return v.strip().strip('"') if v else None

    def resolve(self, rel: str | None, search_data: bool = False) -> Path | None:
        """Resolve como o MUGEN: pasta do personagem, depois raiz do MUGEN e data/."""
        if not rel:
            return None
        rel = rel.replace("\\", "/")
        candidates = [self.dir / rel]
        if self.mugen_root:
            candidates += [self.mugen_root / rel]
            if search_data:
                candidates += [self.mugen_root / "data" / rel]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]

    def path_of(self, key: str) -> Path | None:
        p = self.resolve(self.file_entry(key), search_data=key == "stcommon")
        if key == "stcommon" and p and not p.exists() and p.suffix.lower() == ".zss":
            ik = self.ikemen_root()                  # personagem ZSS fora da pasta do Ikemen
            for c in ([ik / self.file_entry(key), ik / "data" / p.name] if ik else []):
                if c.exists():
                    return c
            if p.name.lower() in ("common1.cns.zss", "common1.zss"):
                cns_common = self.resolve("common1.cns", search_data=True)
                if cns_common and cns_common.exists():
                    return cns_common
        return p

    def ikemen_root(self) -> Path | None:
        """Pasta do Ikemen GO: a que contém o personagem, senão a do setup (config 'ikemen')."""
        for p in [self.dir, *self.dir.parents]:
            if (p / "data" / "common1.cns.zss").exists():
                return p
        from . import config
        saved = config.load().get("ikemen")
        return Path(saved) if saved and Path(saved).is_dir() else None

    @property
    def is_zss(self) -> bool:
        return any(p.suffix.lower() == ".zss" for p in self.state_files())

    def _load_states(self, p: Path):
        if p.suffix.lower() == ".zss":
            return zssread.load(p, self.zss_functions)
        return cns.load(p)

    @cached_property
    def zss_functions(self) -> dict:
        """[Function] de todos os .zss do personagem, do stcommon e do functions.zss do Ikemen."""
        funcs: dict = {}
        files = [p for p in self.state_files() if p.suffix.lower() == ".zss"]
        common = self.path_of("stcommon")
        if common and common.exists() and common.suffix.lower() == ".zss":
            files.append(common)
        ik = self.ikemen_root() if files else None
        if ik and (ik / "data" / "functions.zss").exists():
            files.append(ik / "data" / "functions.zss")
        for p in files:
            try:
                zssread.register(funcs, zssread.parse(p.read_text("utf-8", errors="replace")))
            except zssread.ZssError:
                pass                                 # o erro aparece ao carregar o arquivo
        return funcs

    def state_files(self) -> list[Path]:
        """Arquivos de estados na ordem do MUGEN: st, st0..st9 (cns também pode ter estados)."""
        out: list[Path] = []
        for key in ["cns", "st", *[f"st{i}" for i in range(10)]]:
            p = self.path_of(key)
            if p and p.exists() and p not in out:
                out.append(p)
        return out

    def palette_files(self) -> dict[int, Path]:
        out = {}
        for i in range(1, 13):
            p = self.path_of(f"pal{i}")
            if p:
                out[i] = p
        return out

    # ------------------------------------------------------------ arquivos (lazy)

    @cached_property
    def sprites(self) -> sff.SFF:
        p = self.path_of("sprite")
        if not p or not p.exists():
            raise ProjectError(tr("SFF not found ({0})").format(self.file_entry('sprite')))
        return sff.load(p)

    @cached_property
    def anims(self) -> air.AIR:
        p = self.path_of("anim")
        if not p or not p.exists():
            raise ProjectError(tr("AIR not found ({0})").format(self.file_entry('anim')))
        return air.load(p)

    @cached_property
    def sounds(self) -> snd.SND:
        p = self.path_of("sound")
        return snd.load(p) if p and p.exists() else snd.new()

    @cached_property
    def commands(self) -> cmd.Commands:
        p = self.path_of("cmd")
        if not p or not p.exists():
            raise ProjectError(tr("CMD not found ({0})").format(self.file_entry('cmd')))
        return cmd.load(p)

    @cached_property
    def states(self) -> list[cns.States]:
        return [self._load_states(p) for p in self.state_files()]

    @cached_property
    def common_states(self) -> cns.States | None:
        p = self.path_of("stcommon")
        return self._load_states(p) if p and p.exists() else None

    @cached_property
    def constants(self) -> mtext.Document:
        p = self.path_of("cns")
        if not p or not p.exists():
            raise ProjectError(tr("constants file (cns) not found"))
        for st in self.states:
            if st.path == p:
                return st.doc  # mesmo objeto: edições aparecem nos dois lugares
        return mtext.load(p)

    # ------------------------------------------------------------ estados

    def all_statedefs(self, include_common: bool = True) -> dict[int, tuple[cns.StateDef, cns.States]]:
        """Statedefs efetivos: os do personagem sobrescrevem os comuns."""
        own: dict[int, tuple[cns.StateDef, cns.States]] = {}
        for st in [*self.states, self.commands.states]:
            for n, sd in st.statedefs.items():
                own.setdefault(n, (sd, st))  # entre arquivos do personagem vale o primeiro
        out: dict[int, tuple[cns.StateDef, cns.States]] = {}
        if include_common and self.common_states:
            out.update({n: (sd, self.common_states) for n, sd in self.common_states.statedefs.items()})
        out.update(own)  # os do personagem sobrescrevem os comuns
        return out

    def states_file_for(self, number: int) -> cns.States:
        """Arquivo de estados onde um statedef está (ou deve ser criado)."""
        for st in [*self.states, self.commands.states]:
            if number in st.statedefs:
                return st
        if number == -1:
            return self.commands.states
        st_path = self.path_of("st")
        return next((s for s in self.states if s.path == st_path), self.states[-1])

    # ------------------------------------------------------------ resumo

    def summary(self) -> dict:
        s = {"name": self.name, "def": str(self.def_path),
             "author": self.info("author"), "mugenversion": self.info("mugenversion"),
             "localcoord": self.info("localcoord", "320,240"),
             "files": {k: self.file_entry(k) for k in
                       ["cmd", "cns", "st", "stcommon", "sprite", "anim", "sound"] if self.file_entry(k)},
             "palette_files": {i: str(p.name) for i, p in self.palette_files().items()}}
        try:
            s["sprites"] = {"count": len(self.sprites.sprites), "palettes": len(self.sprites.palettes),
                            "sff_version": self.sprites.version_str}
        except ProjectError as e:
            s["sprites"] = {"error": str(e)}
        try:
            s["animations"] = len(self.anims.actions)
        except ProjectError as e:
            s["animations"] = str(e)
        s["sounds"] = len(self.sounds.sounds)
        try:
            s["commands"] = len(self.commands.commands())
        except ProjectError as e:
            s["commands"] = str(e)
        s["statedefs"] = sorted({n for st in self.states for n in st.statedefs})
        return s

    # ------------------------------------------------------------ gravação

    def mark(self, *what: str) -> None:
        """Marca partes alteradas: 'sprites', 'anims', 'sounds', 'commands', 'states', 'def'."""
        self.dirty.update(what)

    def save(self, backup: bool = True) -> list[str]:
        written = []

        def write(path: Path, fn) -> None:
            if backup and path.exists():
                bak = path.with_suffix(path.suffix + ".bak")
                if not bak.exists():
                    shutil.copy2(path, bak)
            fn(path)
            written.append(str(path))

        if "def" in self.dirty:
            write(self.def_path, self.definition.save)
        if "sprites" in self.dirty:
            write(self.path_of("sprite"), self.sprites.save)
        if "anims" in self.dirty:
            write(self.path_of("anim"), self.anims.save)
        if "sounds" in self.dirty:
            write(self.path_of("sound"), lambda p: snd.save(self.sounds, p))
        if "commands" in self.dirty:
            write(self.commands.path, self.commands.save)
        if "states" in self.dirty:
            for st in self.states:
                write(st.path, st.save)
        self.dirty.clear()
        return written


    def project_files(self) -> list[Path]:
        """Arquivos do personagem que existem (def, sff, air, snd, cmd, estados, paletas)."""
        out = [self.def_path]
        for key in ["sprite", "anim", "sound", "cmd", "cns", "st", *[f"st{i}" for i in range(10)]]:
            p = self.path_of(key)
            if p and p.exists() and p not in out:
                out.append(p)
        out += [p for p in self.palette_files().values() if p.exists() and p not in out]
        return out

    def save_to(self, dest: str | Path, parts: set[str] | None = None) -> list[str]:
        """Grava as partes alteradas (ou `parts`) em outra pasta, com os mesmos nomes relativos,
        sem tocar nos arquivos do projeto e sem limpar as marcas de alteração. Serve para o
        backup automático e para o 'Salvar como'. Arquivos fora da pasta do personagem
        (o common1.cns do MUGEN, por exemplo) ficam de fora."""
        dest = Path(dest)
        parts = set(self.dirty if parts is None else parts)
        written = []

        def target(p: Path | None) -> Path | None:
            if not p:
                return None
            try:
                rel = p.resolve().relative_to(self.dir)
            except ValueError:
                return None
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            return out

        def write(p: Path | None, fn) -> None:
            t = target(p)
            if t:
                fn(t)
                written.append(str(t))

        if "def" in parts:
            write(self.def_path, self.definition.save)
        if "sprites" in parts:
            write(self.path_of("sprite"), self.sprites.save)
        if "anims" in parts:
            write(self.path_of("anim"), self.anims.save)
        if "sounds" in parts:
            write(self.path_of("sound"), lambda t: snd.save(self.sounds, t))
        if "commands" in parts:
            write(self.commands.path, self.commands.save)
        if "states" in parts:
            for st in self.states:
                write(st.path, st.save)
        return written


def load(def_path: str | Path, mugen_root: str | Path | None = None) -> Character:
    return Character(def_path, mugen_root)
