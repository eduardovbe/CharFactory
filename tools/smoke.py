"""Teste de fumaça: confere que o Char Factory roda de verdade no sistema atual (usado no CI em
Windows, macOS e Linux).

    python tools/smoke.py                      # linha de comando e janela (Qt offscreen)
    python tools/smoke.py "dist/Char Factory"  # também abre o executável empacotado

Cria um personagem numa pasta temporária, roda a linha de comando nele, abre a janela com ele e
passa por todos os editores; com um executável, abre o app empacotado e confere que ele continua
aberto alguns segundos (sem fechar com erro de biblioteca ou de import)."""
from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def step(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


def check_cli(work: Path) -> Path:
    from charfactory.tools import generate
    d = generate.new_character(work / "chars" / "Smoke", "Smoke")
    for args in (["info", str(d)], ["validate", str(d)]):
        r = subprocess.run([sys.executable, "-m", "charfactory.cli", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT)
        if r.returncode not in (0, 1):            # validate devolve 1 quando há avisos
            raise SystemExit(f"cli {args[0]} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
        step(f"cli {args[0]}: ok")
    return d


def check_case_insensitive(d: Path) -> None:
    """Um .def escrito no Windows pode citar o arquivo com outra caixa (KFM.SFF para kfm.sff)."""
    from charfactory import project
    text = d.read_text("utf-8")
    sff = project.load(d).path_of("sprite")
    upper = text.replace(sff.name, sff.name.upper())
    d.write_text(upper, "utf-8")
    found = project.load(d).path_of("sprite")
    d.write_text(text, "utf-8")
    if not found.exists():
        raise SystemExit(f"case-insensitive lookup failed: {sff.name.upper()}")
    step(f"case-insensitive file names: ok ({sff.name.upper()} -> {found.name})")


def check_gui(d: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from charfactory.gui.app import MainWindow
    from charfactory.gui.context import AppContext
    ctx = AppContext()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        ctx.settings["ai_mcp_port"] = sock.getsockname()[1]
    win = MainWindow(ctx)
    win.show()
    win.open_path(str(d))
    app.processEvents()
    if ctx.char is None:
        raise SystemExit("gui: the character did not open")
    from PySide6.QtWidgets import QTabWidget
    for tw in win.findChildren(QTabWidget):
        for i in range(tw.count()):
            tw.setCurrentIndex(i)
            app.processEvents()
    step(f"gui: window opened {ctx.char.name!r} and went through every tab")
    ctx.char.dirty.clear()
    win.close()
    app.processEvents()


def check_frozen(dist: Path, config_home: Path) -> None:
    if sys.platform == "win32":
        exe = dist / "Char Factory.exe"
    elif sys.platform == "darwin":
        exe = dist.parent / "Char Factory.app" / "Contents" / "MacOS" / "Char Factory"
        if not exe.exists():
            exe = dist / "Contents" / "MacOS" / "Char Factory"
    else:
        exe = dist / "Char Factory"
    if not exe.exists():
        raise SystemExit(f"packaged executable not found: {exe}")
    # idioma já escolhido: abre direto a janela principal, sem a tela de boas-vindas
    cfg = config_home / "charfactory" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"language": "en", "theme": "Default"}), "utf-8")
    env = dict(os.environ, CHARFACTORY_CONFIG_HOME=str(config_home), QT_QPA_PLATFORM="offscreen")
    log = config_home / "frozen.log"
    with open(log, "w", encoding="utf-8") as out:
        proc = subprocess.Popen([str(exe)], env=env, stdout=out, stderr=subprocess.STDOUT)
        time.sleep(15)
        code = proc.poll()
        proc.kill()
        proc.wait(10)
    text = log.read_text("utf-8", errors="replace")
    if code is not None:
        raise SystemExit(f"packaged app closed by itself (code {code}):\n{text[-3000:]}")
    step(f"packaged app: still running after 15 s ({exe.name})")


def main() -> int:
    step(f"{platform.system()} {platform.release()} {platform.machine()}, Python {platform.python_version()}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        os.environ["CHARFACTORY_CONFIG_HOME"] = str(tmp / "config")
        from charfactory import osutil
        step(f"config dir: {osutil.config_dir()}")
        d = check_cli(tmp)
        check_case_insensitive(d)
        check_gui(d)
        if len(sys.argv) > 1:
            check_frozen(Path(sys.argv[1]).resolve(), tmp / "frozen-config")
    step("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
