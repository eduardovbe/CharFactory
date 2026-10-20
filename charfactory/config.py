"""Configuração do charfactory gravada pelo setup (pasta do MUGEN etc.) em ~/.charfactory/config.json.

A variável de ambiente CHARFACTORY_MUGEN, quando definida, tem prioridade sobre o arquivo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def path() -> Path:
    return Path(os.environ.get("CHARFACTORY_CONFIG") or Path.home() / ".charfactory" / "config.json")


def load() -> dict:
    try:
        return json.loads(path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save(**values) -> dict:
    cfg = load()
    cfg.update({k: v for k, v in values.items() if v is not None})
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=1, ensure_ascii=False), "utf-8")
    return cfg


def mugen_root() -> Path | None:
    """Pasta do MUGEN: CHARFACTORY_MUGEN, senão a gravada pelo setup."""
    env = os.environ.get("CHARFACTORY_MUGEN")
    if env and Path(env).is_dir():
        return Path(env)
    saved = load().get("mugen")
    return Path(saved) if saved and Path(saved).is_dir() else None
