"""Interface language: English in the code, translations in charfactory/locale/<code>.json.

Each catalog maps the English text exactly as written in the code to the translated text.
`tr("Open")` returns the translation for the current language, or the English text itself
when there is none. Texts with values use numbered placeholders, filled after the lookup:
`tr("Sprite {0},{1} edited").format(g, i)`.

The language is chosen once, when this module is first imported (before any interface module
builds its labels): the CHARFACTORY_LANG environment variable, else the "language" saved in the
settings file, else the system language when it is one of LANGUAGES, else English. Changing it
in the app saves the choice and asks for a restart.
"""
from __future__ import annotations

import json
import locale
import os
from pathlib import Path

LANGUAGES = {"en": "English", "pt_BR": "Português (Brasil)", "es": "Español"}
LOCALE_DIR = Path(__file__).parent / "locale"

_current = "en"
_catalog: dict[str, str] = {}


def settings_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "charfactory" / "config.json"


def saved_language() -> str | None:
    try:
        lang = json.loads(settings_path().read_text("utf-8")).get("language")
    except (OSError, ValueError, AttributeError):
        return None
    return lang if lang in LANGUAGES else None


def system_language() -> str:
    """The system language when it is one we have (pt → pt_BR, es → es), else English."""
    names = []
    try:
        if os.name == "nt":
            import ctypes
            from locale import windows_locale
            names.append(windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), ""))
    except (AttributeError, OSError, ImportError):
        pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        names.append(os.environ.get(var, ""))
    try:
        names.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    for name in names:
        low = name.lower()
        if low.startswith("pt") or low.startswith("portuguese"):
            return "pt_BR"
        if low.startswith("es") or low.startswith("spanish"):
            return "es"
    return "en"


def resolve_language() -> str:
    env = os.environ.get("CHARFACTORY_LANG")
    if env in LANGUAGES:
        return env
    return saved_language() or system_language()


def load_catalog(lang: str) -> dict[str, str]:
    if lang == "en":
        return {}
    try:
        return json.loads((LOCALE_DIR / f"{lang}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def set_language(lang: str) -> None:
    """Switches the catalog for texts translated from now on (labels already built stay)."""
    global _current, _catalog
    _current = lang if lang in LANGUAGES else "en"
    _catalog = load_catalog(_current)


def language() -> str:
    return _current


def tr(text: str) -> str:
    return _catalog.get(text, text)


def qt_locale_name() -> str | None:
    """Name of Qt's own translation (qtbase_<name>.qm) for the standard dialogs and buttons."""
    return {"pt_BR": "pt_BR", "es": "es"}.get(_current)


set_language(resolve_language())
