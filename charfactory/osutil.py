"""O que muda entre Windows, macOS e Linux: pasta de configuração, abrir um arquivo no programa
padrão, rodar um .exe (o MUGEN só existe para Windows; fora dele vai pelo Wine), o executável
nativo do Ikemen GO e nomes de arquivo sem diferença de maiúsculas (os .def do MUGEN foram
escritos no Windows, onde KFM.SFF e kfm.sff são o mesmo arquivo)."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def config_dir() -> Path:
    """Pasta das configurações do app: %APPDATA%/charfactory no Windows, ~/Library/Application
    Support/charfactory no macOS e $XDG_CONFIG_HOME (ou ~/.config)/charfactory no Linux. Fora do
    Windows, quem já tinha ~/charfactory (versões antigas) continua nela. A variável
    CHARFACTORY_CONFIG_HOME troca a pasta base em qualquer sistema (testes, instalação portátil)."""
    base = os.environ.get("CHARFACTORY_CONFIG_HOME")
    if base:
        return Path(base) / "charfactory"
    if IS_WIN:
        return Path(os.environ.get("APPDATA", Path.home())) / "charfactory"
    legacy = Path.home() / "charfactory"
    if (legacy / "config.json").exists():
        return legacy
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "charfactory"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "charfactory"


def open_path(path: str | Path) -> None:
    """Abre o arquivo ou a pasta no programa padrão do sistema."""
    if IS_WIN:
        os.startfile(str(path))  # noqa: S606
        return
    opener = "open" if IS_MAC else shutil.which("xdg-open") or shutil.which("gio")
    if not opener:
        raise OSError("xdg-open not found")
    cmd = [opener, "open", str(path)] if opener.endswith("gio") else [opener, str(path)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_with(program: str | Path, path: str | Path) -> None:
    """Abre o arquivo num programa escolhido (no macOS, um .app vai pelo `open -a`)."""
    program = str(program)
    if IS_MAC and program.rstrip("/").endswith(".app"):
        subprocess.Popen(["open", "-a", program, str(path)])
    else:
        subprocess.Popen([program, str(path)])


def wine() -> str | None:
    return shutil.which("wine") or shutil.which("wine64")


def command(args: list[str]) -> list[str]:
    """Linha de comando pronta para rodar no sistema: fora do Windows, um .exe vai pelo Wine e um
    executável nativo que perdeu a permissão de execução (zip baixado) a recebe de volta."""
    if IS_WIN or not args:
        return args
    exe = Path(args[0])
    if exe.suffix.lower() == ".exe":
        w = wine()
        if not w:
            from .i18n import tr
            raise FileNotFoundError(tr("{0} is a Windows program; install Wine to run it on this system").format(exe.name))
        return [w, *args]
    if exe.is_file() and not os.access(exe, os.X_OK):
        try:
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass
    return args


def no_window_flags() -> int:
    """creationflags que impedem a janela de console no Windows (0 nos outros sistemas)."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def program_filter() -> str:
    """Filtro do diálogo de escolher programa: no Windows os .exe; nos outros, qualquer arquivo
    (executáveis não têm extensão; no macOS um .app aparece como arquivo)."""
    if IS_WIN:
        from .i18n import tr
        return tr("Program (*.exe)")
    return ""


def executables(root: str | Path, word: str) -> list[Path]:
    """Executáveis da pasta com `word` no nome: os nativos do sistema primeiro (Ikemen_GO_Linux,
    Ikemen_GO_MacOS, o binário dentro de Ikemen_GO.app), depois os .exe."""
    root = Path(root)
    native, exe = [], []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for p in entries:
        low = p.name.lower()
        if word not in low:
            continue
        if p.suffix.lower() == ".exe" and p.is_file():
            exe.append(p)
        elif IS_WIN:
            continue
        elif IS_MAC and p.suffix.lower() == ".app" and p.is_dir():
            inner = sorted((p / "Contents" / "MacOS").glob("*"))
            native += [q for q in inner if q.is_file()][:1]
        elif p.is_file() and not p.suffix and (IS_MAC or "mac" not in low):
            if IS_MAC and "linux" in low:
                continue
            native.append(p)
        elif p.is_file() and p.suffix.lower() in (".x86_64", ".amd64", ".arm64", ".aarch64") and IS_LINUX:
            native.append(p)
    return native + exe


def find_case(path: Path) -> Path:
    """O arquivo como está no disco quando só a caixa difere (Linux; o macOS já não diferencia).
    Sem correspondência, devolve o caminho pedido."""
    if IS_WIN or path.exists():
        return path
    parts = path.parts
    cur = Path(parts[0])
    for i, part in enumerate(parts[1:], 1):
        nxt = cur / part
        if not nxt.exists():
            try:
                low = part.lower()
                hit = next((c for c in cur.iterdir() if c.name.lower() == low), None)
            except OSError:
                hit = None
            if hit is None:
                return path
            nxt = hit
        cur = nxt
    return cur
