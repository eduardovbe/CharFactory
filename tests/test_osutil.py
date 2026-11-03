"""Diferenças entre Windows, macOS e Linux: pasta de configuração, executáveis do motor, Wine e
nomes de arquivo sem diferença de caixa."""
import os
import sys
from pathlib import Path

import pytest

from charfactory import osutil
from charfactory.tools import runner


def _as(monkeypatch, system):
    monkeypatch.setattr(osutil, "IS_WIN", system == "win")
    monkeypatch.setattr(osutil, "IS_MAC", system == "mac")
    monkeypatch.setattr(osutil, "IS_LINUX", system == "linux")


def test_config_dir_per_system(monkeypatch, tmp_path):
    monkeypatch.delenv("CHARFACTORY_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    _as(monkeypatch, "win")
    assert osutil.config_dir() == tmp_path / "roaming" / "charfactory"
    _as(monkeypatch, "mac")
    assert osutil.config_dir() == tmp_path / "Library" / "Application Support" / "charfactory"
    _as(monkeypatch, "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert osutil.config_dir() == tmp_path / ".config" / "charfactory"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert osutil.config_dir() == tmp_path / "xdg" / "charfactory"
    (tmp_path / "charfactory").mkdir()                     # quem já usava ~/charfactory continua nela
    (tmp_path / "charfactory" / "config.json").write_text("{}", "utf-8")
    assert osutil.config_dir() == tmp_path / "charfactory"
    monkeypatch.setenv("CHARFACTORY_CONFIG_HOME", str(tmp_path / "port"))
    assert osutil.config_dir() == tmp_path / "port" / "charfactory"


def test_native_ikemen_wins_over_the_exe(monkeypatch, tmp_path):
    for name in ("Ikemen_GO.exe", "Ikemen_GO_Linux", "Ikemen_GO_MacOS", "ikemen.dll"):
        (tmp_path / name).write_bytes(b"")
    app = tmp_path / "Ikemen_GO.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "Ikemen_GO").write_bytes(b"")
    _as(monkeypatch, "linux")
    assert runner.find_ikemen_exe(tmp_path) == tmp_path / "Ikemen_GO_Linux"
    _as(monkeypatch, "mac")
    assert runner.find_ikemen_exe(tmp_path) == app / "Ikemen_GO"
    _as(monkeypatch, "win")
    assert runner.find_ikemen_exe(tmp_path) == tmp_path / "Ikemen_GO.exe"


def test_linux_only_ikemen_folder_is_detected(monkeypatch, tmp_path):
    (tmp_path / "Ikemen_GO_Linux").write_bytes(b"")
    _as(monkeypatch, "linux")
    assert runner.is_ikemen(tmp_path)


def test_exe_goes_through_wine_outside_windows(monkeypatch, tmp_path):
    exe = tmp_path / "mugen.exe"
    exe.write_bytes(b"")
    _as(monkeypatch, "win")
    assert osutil.command([str(exe), "kfm"]) == [str(exe), "kfm"]
    _as(monkeypatch, "linux")
    monkeypatch.setattr(osutil, "wine", lambda: "/usr/bin/wine")
    assert osutil.command([str(exe), "kfm"]) == ["/usr/bin/wine", str(exe), "kfm"]
    monkeypatch.setattr(osutil, "wine", lambda: None)
    with pytest.raises(FileNotFoundError, match="Wine"):
        osutil.command([str(exe), "kfm"])


@pytest.mark.skipif(sys.platform == "win32", reason="permissão de execução do POSIX")
def test_native_binary_gets_its_exec_bit_back(tmp_path):
    exe = tmp_path / "Ikemen_GO_Linux"
    exe.write_bytes(b"")
    exe.chmod(0o644)
    osutil.command([str(exe)])
    assert os.access(exe, os.X_OK)


def test_file_names_ignore_case_outside_windows(monkeypatch, tmp_path):
    (tmp_path / "Sprites").mkdir()
    (tmp_path / "Sprites" / "KFM.sff").write_bytes(b"")
    _as(monkeypatch, "linux")
    found = osutil.find_case(tmp_path / "sprites" / "kfm.SFF")
    assert found.exists() and found.name.lower() == "kfm.sff"
    missing = tmp_path / "nada" / "x.sff"
    assert osutil.find_case(missing) == missing
