"""Locate resource references in live text, retaining source locations.

Dynamic expressions are possible usages, never presented as exact matches.
ZSS is lowered using the existing parser and links back to its source statedef.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..formats import mtext, zssread
from .usage import _ANIM_KEYS, _SPARK_KEYS, _split_top


@dataclass(frozen=True)
class Usage:
    path: Path
    line: int
    text: str
    dynamic: bool = False


def _matches(value, target, prefix=False):
    value = value.strip()
    if prefix:
        value = re.sub(r"^[sS]\s*", "", value)
    if re.fullmatch(r"-?\d+", value):
        return int(value) == target, False
    # A computed expression can depend on runtime variables; do not infer that
    # its literals delimit all possible results.
    return bool(value), True


def find_usages(sources, kind, a, b=0, funcs=None):
    """Return (usages, unreadable ZSS paths). Sources: (path, text, is_zss)."""
    out, skipped, seen = [], [], set()
    for path, text, is_zss in sources:
        path = Path(path)
        if path in seen:
            continue
        seen.add(path)
        zss = None
        if is_zss:
            try:
                zss = zssread.ZssStates(text, path, dict(funcs or {}))
            except (zssread.ZssError, ValueError):
                skipped.append(path)
                continue
            doc = zss.doc
        else:
            doc = mtext.parse(text)
        offset = len(doc.preamble)
        state = None
        for section in doc.sections:
            sd = re.match(r"statedef\s+(-?\d+)", section.name, re.I)
            if sd:
                state = int(sd[1])
            ctype = (section.get("type") or "").lower()
            line_base = zss.zss_line(state) if zss and state is not None else None
            if kind == "sprite" and section.kind == "begin":
                for i, line in enumerate(section.lines[1:], 1):
                    m = re.match(r"\s*(-?\d+)\s*,\s*(-?\d+)\s*,", mtext.strip_comment(line))
                    if m and (int(m[1]), int(m[2])) == (a, b):
                        out.append(Usage(path, offset + i + 1, f"[{section.name}] {line.strip()}"))
            for entry in section.entries():
                if section.kind not in ("state", "statedef"):
                    continue
                key, value = entry.key.lower(), entry.value.strip()
                own_prefix = False
                if kind == "anim":
                    eligible = key in _ANIM_KEYS or (key == "value" and ctype in ("changeanim", "changeanim2"))
                    if key in _SPARK_KEYS:
                        eligible = value[:1].lower() == "s"
                        own_prefix = True
                    if value[:1].lower() == "f":
                        eligible = False
                    own_prefix = own_prefix or value[:1].lower() == "s"
                elif kind == "state":
                    eligible = key in ("stateno", "p1stateno", "p2stateno") or (
                        key == "value" and ctype in ("changestate", "selfstate", "targetstate"))
                elif kind == "sound":
                    eligible = (key == "value" and ctype == "playsnd" and value[:1].lower() != "f") or (
                        key in ("hitsound", "guardsound") and value[:1].lower() == "s")
                    own_prefix = True
                else:
                    eligible = False
                if not eligible:
                    continue
                if kind == "sound":
                    parts = _split_top(re.sub(r"^[sS]\s*", "", value))
                    if len(parts) != 2:
                        continue
                    gm, gd = _matches(parts[0], a)
                    nm, nd = _matches(parts[1], b)
                    match, dynamic = gm and nm, gd or nd
                else:
                    match, dynamic = _matches(value, a, own_prefix)
                if match:
                    # Lowered ZSS line numbers do not correspond to the source.
                    line = line_base if zss else offset + entry.line + 1
                    if line is not None:
                        out.append(Usage(path, line, f"[{section.name}] {entry.key} = {value}", dynamic))
            offset += len(section.lines)
    return out, skipped
