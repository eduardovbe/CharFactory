"""O que o assistente guarda entre uma conversa e outra, fora dos arquivos do personagem (na pasta
de configuração do app, uma subpasta por personagem):

- notas: decisões e preferências do usuário sobre o char ("golpes em 1000-1099", "dano baixo"),
  lidas no começo de cada conversa e anotadas pelo assistente com save_note;
- nomes das animações tirados das imagens (animindex), cada um com a assinatura dos quadros: se a
  animação muda, o nome antigo deixa de valer;
- a última conversa, para reabrir depois de fechar o app;
- e, para todos os chars, as avaliações (👍/👎) e os casos de teste salvos pelo usuário.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from ..osutil import config_dir

MAX_NOTES = 60
MAX_NOTE = 300


def root() -> Path:
    return config_dir() / "ai"


def char_dir(def_path) -> Path:
    """Pasta do personagem: nome + hash do caminho (dois chars com o mesmo nome não se misturam)."""
    return root() / "chars" / _char_key(str(def_path))


@functools.lru_cache(maxsize=64)
def _char_key(def_path: str) -> str:
    p = Path(def_path).resolve()
    return f"{p.stem[:40]}-{hashlib.sha1(str(p).lower().encode('utf-8')).hexdigest()[:10]}"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), "utf-8")
    os.replace(tmp, path)                     # nunca deixa o arquivo pela metade


def copy(src_def, dst_def) -> None:
    """Leva a memória de um char para outro caminho (a cópia que o eval usa)."""
    src, dst = char_dir(src_def), char_dir(dst_def)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)


# ======================================================================= notas

def notes(def_path) -> list[str]:
    return [str(n) for n in _read(char_dir(def_path) / "notes.json", [])]


def set_notes(def_path, items: list[str]) -> None:
    clean = [" ".join(str(n).split())[:MAX_NOTE] for n in items if str(n).strip()]
    _write(char_dir(def_path) / "notes.json", clean[-MAX_NOTES:])


def add_note(def_path, text: str) -> list[str]:
    items = notes(def_path)
    text = " ".join(text.split())[:MAX_NOTE]
    if text and text not in items:
        items.append(text)
    set_notes(def_path, items)
    return notes(def_path)


def remove_note(def_path, index: int) -> list[str]:
    items = notes(def_path)
    if not 1 <= index <= len(items):
        raise IndexError(f"there is no note {index} (the character has {len(items)})")
    del items[index - 1]
    set_notes(def_path, items)
    return items


# ======================================================================= nomes das animações

_labels_cache: dict[str, tuple[float, dict]] = {}


def signature(action) -> str:
    """Assinatura dos quadros de uma animação (sprites e tempos)."""
    raw = ";".join(f"{f.group},{f.item},{f.time}" for f in action.frames)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _labels_path(def_path) -> Path:
    return char_dir(def_path) / "anim_labels.json"


def labels(def_path) -> dict[int, dict]:
    """{ação: {"label", "sig"}} lidos do disco (com cache pela data do arquivo)."""
    path = _labels_path(def_path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    hit = _labels_cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    data = {int(k): v for k, v in _read(path, {}).items() if str(k).lstrip("-").isdigit() and isinstance(v, dict)}
    _labels_cache[str(path)] = (mtime, data)
    return data


def label_of(char, number: int) -> str:
    """Nome visto nas imagens, se ainda vale para os quadros atuais da animação."""
    try:
        entry = labels(char.def_path).get(number)
        if not entry:
            return ""
        act = char.anims.get(number)
        return entry.get("label", "") if act is not None and entry.get("sig") == signature(act) else ""
    except (OSError, AttributeError, TypeError):
        return ""


def save_labels(def_path, new: dict[int, tuple[str, str]]) -> None:
    """new: {ação: (nome, assinatura)}; junta com os que já estavam."""
    data = {str(k): v for k, v in labels(def_path).items()}
    for n, (label, sig) in new.items():
        data[str(n)] = {"label": label, "sig": sig}
    _write(_labels_path(def_path), data)
    _labels_cache.pop(str(_labels_path(def_path)), None)


# ======================================================================= conversa

def save_conversation(def_path, data: dict) -> None:
    # compacto: com imagens e muitas etapas o arquivo chega a megabytes
    _write(char_dir(def_path) / "conversation.json", data, indent=None)


def load_conversation(def_path) -> dict | None:
    data = _read(char_dir(def_path) / "conversation.json", None)
    return data if isinstance(data, dict) and data.get("messages") else None


def conversation_saved(def_path) -> float | None:
    """Quando a última conversa foi gravada, sem abrir o arquivo (que pode ser grande)."""
    try:
        return (char_dir(def_path) / "conversation.json").stat().st_mtime
    except OSError:
        return None


# ======================================================================= avaliações e casos

def log_feedback(entry: dict) -> None:
    path = root() / "feedback.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**entry, "time": time.time()}, ensure_ascii=False) + "\n")


def user_cases_path() -> Path:
    return root() / "eval_cases.json"


def add_case(case: dict) -> dict:
    """Grava um caso de teste do usuário (id único) para o tools/ai_eval.py --user-cases."""
    cases = _read(user_cases_path(), [])
    ids = {c.get("id") for c in cases}
    base = case.get("id") or "case"
    n, cid = 1, base
    while cid in ids:
        n += 1
        cid = f"{base}-{n}"
    case = {**case, "id": cid}
    cases.append(case)
    _write(user_cases_path(), cases)
    return case
