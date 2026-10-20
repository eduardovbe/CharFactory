"""Parser dos arquivos de texto do MUGEN (DEF, CNS/ST, CMD, AIR, stage, motif…).

Formato INI estendido: seções `[Nome]`, linhas `chave = valor`, comentários `;`.
O documento guarda as linhas originais, então `to_text()` devolve o arquivo idêntico
quando nada foi alterado, e edições mexem só nas linhas tocadas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SECTION = re.compile(r"^\s*\[([^\]]*)\]")
_ENTRY = re.compile(r"^\s*([^=;\[\]]+?)\s*=\s*(.*)$")


def strip_comment(text: str) -> str:
    """Remove o comentário `;` fora de aspas."""
    quoted = False
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif ch == ";" and not quoted:
            return text[:i].rstrip()
    return text.rstrip()


def read_text(path: str | Path) -> tuple[str, str]:
    """Lê um arquivo de texto do MUGEN e devolve (texto, encoding detectado)."""
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


@dataclass
class Entry:
    key: str
    value: str
    line: int  # índice da linha dentro da seção


@dataclass
class Section:
    name: str                       # texto entre colchetes, sem espaços nas pontas
    lines: list[str] = field(default_factory=list)  # linha do cabeçalho + corpo

    @property
    def header(self) -> str:
        return self.lines[0] if self.lines else f"[{self.name}]"

    @property
    def kind(self) -> str:
        """Primeira palavra do nome, em minúsculas (ex.: 'statedef', 'state', 'begin')."""
        return self.name.split()[0].lower() if self.name.split() else ""

    def entries(self) -> list[Entry]:
        # a validação e as telas perguntam type/trigger da mesma seção milhares de vezes: o
        # resultado vale enquanto as linhas forem as mesmas (comparar listas de strings que não
        # mudaram é quase de graça: o Python compara primeiro a identidade de cada item)
        cached = self.__dict__.get("_entries")
        if cached is not None and cached[0] == self.lines:
            return list(cached[1])
        out = []
        for i, line in enumerate(self.lines[1:], 1):
            body = strip_comment(line)
            m = _ENTRY.match(body)
            if m:
                out.append(Entry(m[1].strip(), m[2].strip(), i))
        self.__dict__["_entries"] = (list(self.lines), out)
        return list(out)

    def __getstate__(self):     # cópias (desfazer) e pickle não levam o cache de entries
        state = dict(self.__dict__)
        state.pop("_entries", None)
        return state

    def get(self, key: str, default: str | None = None) -> str | None:
        k = key.lower()
        for e in self.entries():
            if e.key.lower() == k:
                return e.value
        return default

    def get_all(self, key: str) -> list[str]:
        k = key.lower()
        return [e.value for e in self.entries() if e.key.lower() == k]

    def as_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for e in self.entries():
            out.setdefault(e.key.lower(), e.value)
        return out

    def set(self, key: str, value: str) -> None:
        """Altera a 1ª ocorrência da chave (preservando comentário) ou acrescenta."""
        k = key.lower()
        for e in self.entries():
            if e.key.lower() == k:
                line = self.lines[e.line]
                comment = line[len(strip_comment(line)):] if ";" in line else ""
                indent = line[:len(line) - len(line.lstrip())]
                self.lines[e.line] = f"{indent}{e.key} = {value}{comment}"
                return
        insert_at = len(self.lines)
        while insert_at > 1 and not self.lines[insert_at - 1].strip():
            insert_at -= 1  # mantém as linhas em branco do fim da seção
        self.lines.insert(insert_at, f"{key} = {value}")

    def remove(self, key: str) -> bool:
        k = key.lower()
        hits = [e.line for e in self.entries() if e.key.lower() == k]
        for i in reversed(hits):
            del self.lines[i]
        return bool(hits)

    def body_text(self) -> str:
        return "\n".join(self.lines[1:])

    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Document:
    preamble: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    encoding: str = "utf-8"
    newline: str = "\r\n"
    trailing_newline: bool = True

    # -------------------------------------------------------------- consulta

    def find(self, name: str) -> Section | None:
        n = _norm(name)
        return next((s for s in self.sections if _norm(s.name) == n), None)

    def find_all(self, name: str) -> list[Section]:
        n = _norm(name)
        return [s for s in self.sections if _norm(s.name) == n]

    def of_kind(self, kind: str) -> list[Section]:
        k = kind.lower()
        return [s for s in self.sections if s.kind == k]

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        s = self.find(section)
        return s.get(key, default) if s else default

    # -------------------------------------------------------------- edição

    def section(self, name: str) -> Section:
        """Devolve a seção, criando-a no fim se não existir."""
        s = self.find(name)
        if s is None:
            s = self.append_section(name)
        return s

    def append_section(self, name: str, body: list[str] | None = None,
                       index: int | None = None) -> Section:
        s = Section(name.strip(), [f"[{name.strip()}]", *(body or []), ""])
        if index is None:
            if self.sections and self.sections[-1].lines and self.sections[-1].lines[-1].strip():
                self.sections[-1].lines.append("")
            self.sections.append(s)
        else:
            self.sections.insert(index, s)
        return s

    def remove_section(self, section: Section) -> None:
        # por identidade: seções com o mesmo texto são iguais por valor
        self.sections[:] = [s for s in self.sections if s is not section]

    # -------------------------------------------------------------- texto

    def to_text(self) -> str:
        lines = list(self.preamble)
        for s in self.sections:
            lines.extend(s.lines)
        text = self.newline.join(lines)
        return text + (self.newline if self.trailing_newline else "")

    def save(self, path: str | Path) -> None:
        enc = "utf-8-sig" if self.encoding == "utf-8-sig" else self.encoding
        Path(path).write_bytes(self.to_text().encode(enc, errors="replace"))


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def parse(text: str, encoding: str = "utf-8") -> Document:
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r"))
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if trailing:
        lines = lines[:-1]
    doc = Document(encoding=encoding, newline=newline, trailing_newline=trailing)
    current: Section | None = None
    for line in lines:
        m = _SECTION.match(line)
        if m and not strip_comment(line).strip().startswith(";"):
            current = Section(m[1].strip(), [line])
            doc.sections.append(current)
        elif current is None:
            doc.preamble.append(line)
        else:
            current.lines.append(line)
    return doc


def load(path: str | Path) -> Document:
    text, enc = read_text(path)
    return parse(text, enc)
