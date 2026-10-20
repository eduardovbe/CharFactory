"""Personagens compactados: abrir de .zip/.rar/.7z e empacotar em .zip.

O arquivo é extraído para
uma pasta (por padrão `chars/<nome>` do MUGEN) e aberto como um projeto normal; `pack`
faz o caminho inverso para distribuir o personagem.

ZIP usa a biblioteca padrão. RAR e 7z usam o 7-Zip, se instalado, ou o `tar.exe` do Windows
(libarchive). Entradas com caminho absoluto ou `..` são recusadas antes de extrair.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .formats import mtext
from .project import ProjectError, find_mugen_root
from .i18n import tr

ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z")


def _seven_zip() -> str | None:
    for p in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if p and Path(p).exists():
            return p
    return None


def _system_tar() -> str | None:
    p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    return str(p) if p.exists() else None


def _safe_name(name: str) -> bool:
    p = PurePosixPath(name.replace("\\", "/"))
    return not (p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]))


def list_entries(archive: str | Path) -> list[str]:
    """Nomes dos arquivos dentro do compactado (separador '/')."""
    archive = Path(archive)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            return [i.filename for i in z.infolist() if not i.is_dir()]
    sz = _seven_zip()
    if sz:
        out = subprocess.run([sz, "l", "-slt", "-ba", str(archive)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if out.returncode:
            raise ProjectError(tr("7-Zip could not read {0}: {1}").format(archive.name, out.stderr.strip()[:300]))
        names, block = [], {}
        for line in out.stdout.splitlines() + [""]:
            if " = " in line:
                k, v = line.split(" = ", 1)
                block[k] = v
            elif block:
                is_dir = block.get("Folder") == "+" or block.get("Attributes", "").startswith("D")
                if "Path" in block and not is_dir:
                    names.append(block["Path"].replace("\\", "/"))
                block = {}
        return names
    tar = _system_tar()
    if tar:
        out = subprocess.run([tar, "-tf", str(archive)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if out.returncode:
            raise ProjectError(tr("tar could not read {0}: {1}").format(archive.name, out.stderr.strip()[:300]))
        return [n for n in out.stdout.splitlines() if n and not n.endswith("/")]
    raise ProjectError(tr("to open {0}, install 7-Zip (or use Windows 10+ tar.exe)").format(archive.suffix))


def _extract_all(archive: Path, dest: Path) -> None:
    names = list_entries(archive)
    bad = [n for n in names if not _safe_name(n)]
    if bad:
        raise ProjectError(tr("the archive has unsafe paths (absolute or with '..'): {0}").format(bad[:3]))
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return
    sz = _seven_zip()
    if sz:
        cmd = [sz, "x", "-y", f"-o{dest}", str(archive)]
    else:
        cmd = [_system_tar(), "-xf", str(archive), "-C", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise ProjectError(tr("failed to extract {0}: {1}").format(archive.name, (r.stderr or r.stdout).strip()[:300]))


def character_defs(folder: Path) -> list[Path]:
    """Os .def de personagem dentro de uma pasta: os que têm [Files] com sprite e anim."""
    out = []
    for d in sorted(folder.rglob("*.def")):
        try:
            doc = mtext.load(d)
        except Exception:  # noqa: BLE001 — .def ilegível não é personagem
            continue
        if doc.get("files", "sprite") and doc.get("files", "anim"):
            out.append(d)
    return out


def default_destination(archive: Path, mugen_root: Path | None) -> Path:
    root = mugen_root or find_mugen_root(archive.parent)
    return (root / "chars" if root else archive.parent) / archive.stem


def extract(archive: str | Path, dest: str | Path | None = None,
            mugen_root: str | Path | None = None, overwrite: bool = False) -> dict:
    """Extrai um personagem compactado e devolve {'folder', 'defs'}.

    dest: pasta final (padrão: chars/<nome do arquivo> do MUGEN, ou ao lado do arquivo).
    Se o compactado tiver uma única pasta na raiz, o conteúdo dela vai direto para dest."""
    archive = Path(archive).resolve()
    if not archive.exists():
        raise ProjectError(tr("file not found: {0}").format(archive))
    if archive.suffix.lower() not in ARCHIVE_SUFFIXES:
        raise ProjectError(tr("format not supported: {0} (use {1})").format(archive.suffix, ', '.join(ARCHIVE_SUFFIXES)))
    dest = Path(dest) if dest else default_destination(archive, Path(mugen_root) if mugen_root else None)
    if dest.exists() and any(dest.iterdir()) and not overwrite:
        raise ProjectError(tr("the destination folder already exists and is not empty: {0}").format(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".charfactory-extract-") as tmp:
        tmp = Path(tmp)
        _extract_all(archive, tmp)
        top = list(tmp.iterdir())
        src = top[0] if len(top) == 1 and top[0].is_dir() else tmp
        dest.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = dest / item.name
            if target.exists():
                (shutil.rmtree if target.is_dir() else os.remove)(target)
            shutil.move(str(item), str(target))
    defs = character_defs(dest)
    if not defs:
        raise ProjectError(tr("no character .def (with sprite and anim in [Files]) in {0}").format(archive.name))
    return {"folder": str(dest), "defs": [str(d) for d in defs]}


def pack(char_dir: str | Path, zip_path: str | Path, include_backups: bool = False) -> dict:
    """Empacota a pasta do personagem em .zip, dentro de uma pasta com o nome dela
    (o formato comum de distribuição: kfm.zip → kfm/kfm.def …). Ignora .bak e _backups/."""
    char_dir = Path(char_dir).resolve()
    if char_dir.is_file():
        char_dir = char_dir.parent
    zip_path = Path(zip_path).resolve()
    files = []
    for f in sorted(char_dir.rglob("*")):
        if not f.is_file() or f == zip_path:
            continue
        rel = f.relative_to(char_dir)
        if not include_backups and (f.suffix.lower() == ".bak" or rel.parts[0] == "_backups"):
            continue
        files.append((f, f"{char_dir.name}/{rel.as_posix()}"))
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f, name in files:
            z.write(f, name)
    return {"zip": str(zip_path), "files": len(files), "bytes": zip_path.stat().st_size}
