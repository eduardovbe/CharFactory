"""Leitor de SND (Elecbyte sound file) v1.0 — cada entrada é um WAV completo."""
from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from ..i18n import tr

SIGNATURE = b"ElecbyteSnd\0"


class SNDError(Exception):
    pass


@dataclass
class Sound:
    group: int
    number: int
    wav: bytes = field(repr=False)

    def info(self) -> dict:
        try:
            with wave.open(io.BytesIO(self.wav)) as w:
                return {"channels": w.getnchannels(), "rate": w.getframerate(),
                        "bits": w.getsampwidth() * 8,
                        "seconds": round(w.getnframes() / w.getframerate(), 3)}
        except (wave.Error, EOFError, ZeroDivisionError):
            return {"format": tr("not PCM or invalid"), "bytes": len(self.wav)}


@dataclass
class SND:
    version: int  # campo cru (amostras do MUGEN trazem 0x00000004)
    sounds: list[Sound]

    def find(self, group: int, number: int) -> Sound | None:
        return next((s for s in self.sounds if s.group == group and s.number == number), None)


def load(path: str | Path) -> SND:
    data = Path(path).read_bytes()
    if data[:12] != SIGNATURE:
        raise SNDError(tr("invalid signature (not ElecbyteSnd)"))
    ver = struct.unpack_from("<I", data, 12)[0]
    count, off = struct.unpack_from("<II", data, 16)
    sounds = []
    for _ in range(count):
        if off == 0 or off + 16 > len(data):
            break
        nxt, length, group, number = struct.unpack_from("<IIII", data, off)
        sounds.append(Sound(group, number, data[off + 16: off + 16 + length]))
        off = nxt
    return SND(ver, sounds)


def new() -> SND:
    return SND(0x00000004, [])


def set_sound(snd: SND, group: int, number: int, wav: bytes) -> Sound:
    """Cria ou substitui um som (o conteúdo precisa ser um arquivo WAV completo)."""
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise SNDError(tr("the sound must be a WAV file (RIFF/WAVE)"))
    existing = snd.find(group, number)
    if existing:
        existing.wav = wav
        return existing
    s = Sound(group, number, wav)
    snd.sounds.append(s)
    return s


def to_bytes(snd: SND) -> bytes:
    out = bytearray(512)
    out[0:12] = SIGNATURE
    struct.pack_into("<III", out, 12, snd.version, len(snd.sounds), 512 if snd.sounds else 0)
    pos = 512
    for i, s in enumerate(snd.sounds):
        nxt = pos + 16 + len(s.wav)  # o último aponta para o fim do arquivo, como no MUGEN
        out += struct.pack("<IIII", nxt, len(s.wav), s.group, s.number) + s.wav
        pos += 16 + len(s.wav)
    return bytes(out)


def save(snd: SND, path: str | Path) -> None:
    Path(path).write_bytes(to_bytes(snd))
