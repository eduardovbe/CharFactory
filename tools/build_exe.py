"""Gera o executável do Char Factory com o PyInstaller, no sistema em que roda.

    python tools/build_exe.py            # Windows: dist/Char Factory/Char Factory.exe
                                         # macOS:   dist/Char Factory.app
                                         # Linux:   dist/Char Factory/Char Factory
    python tools/build_exe.py dist/novo  # outra pasta (o exe antigo aberto trava a pasta padrão)

Precisa do PyInstaller no mesmo Python do projeto (pip install pyinstaller). O ícone vem de
charfactory/gui/assets/charfactory.ico no Windows e do .png no macOS (o PyInstaller converte com o
Pillow); tools/build_app_icon.py gera os dois. As ferramentas do charfactory são importadas sob
demanda, então todos os submódulos e arquivos de dados do pacote vão junto.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "Char Factory"
# módulos do Qt que o app não usa (deixam a pasta bem menor)
EXCLUDE = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
           "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
           "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
           "PySide6.QtMultimedia", "PySide6.QtBluetooth", "PySide6.QtSensors", "PySide6.QtSerialPort",
           "tkinter", "matplotlib", "pytest"]


def main() -> int:
    import PyInstaller.__main__
    dist = (ROOT / sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    work = ROOT / "build" / "pyinstaller"
    entry = work / "charfactory_gui_main.py"
    work.mkdir(parents=True, exist_ok=True)
    entry.write_text("from charfactory.gui.launch import main\nraise SystemExit(main())\n", "utf-8")
    assets = ROOT / "charfactory" / "gui" / "assets"
    args = [str(entry), "--name", NAME, "--windowed", "--noconfirm", "--clean",
            "--paths", str(ROOT),
            "--collect-submodules", "charfactory", "--collect-data", "charfactory",
            "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work)]
    if sys.platform == "win32":
        args += ["--icon", str(assets / "charfactory.ico")]
        exe = dist / NAME / f"{NAME}.exe"
    elif sys.platform == "darwin":
        args += ["--icon", str(assets / "charfactory.png"), "--osx-bundle-identifier", "io.github.eduardovbe.charfactory"]
        exe = dist / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    else:
        exe = dist / NAME / NAME
    for m in EXCLUDE:
        args += ["--exclude-module", m]
    PyInstaller.__main__.run(args)
    print("executable:", exe if exe.exists() else "not built")
    return 0 if exe.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
