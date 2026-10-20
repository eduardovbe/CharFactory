"""Codecs de pixels indexados do SFF v2: RLE8, RLE5 e LZ5 (decodificar e codificar).
"""
from __future__ import annotations

import numpy as np
from ..i18n import tr


# ------------------------------------------------------------------ RLE8

def decode_rle8(src: bytes, size: int) -> bytearray:
    """Mesmo resultado de decode_rle8_py, vetorizado (~50x mais rápido: o editor, o simulador e
    as miniaturas decodificam sprites o tempo todo).

    01LLLLLL é marcador de run e o byte seguinte é a cor, mesmo que ele também pareça marcador.
    Numa sequência de bytes com cara de marcador, quem começa um pacote alterna a partir do
    primeiro: marcador, cor, marcador, cor... Fora dessas sequências todo byte começa um pacote."""
    a = np.frombuffer(src, np.uint8)
    n = len(a)
    if not n or size <= 0:
        return bytearray()
    idx = np.arange(n)
    looks = (a & 0xC0) == 0x40
    first = looks & np.concatenate(([True], ~looks[:-1]))     # início de cada sequência
    seq_start = np.maximum.accumulate(np.where(first, idx, 0))
    marker = looks & ((idx - seq_start) % 2 == 0)
    is_color = np.concatenate(([False], marker[:-1]))          # o byte depois de um marcador
    starts = np.flatnonzero(~is_color)
    if len(starts) and marker[starts[-1]] and starts[-1] == n - 1:
        starts = starts[:-1]                                    # marcador final sem a cor
    mk = marker[starts]
    lengths = np.where(mk, a[starts] & 0x3F, 1)
    colors = np.where(mk, a[np.minimum(starts + 1, n - 1)], a[starts])
    return bytearray(np.repeat(colors, lengths)[:size].tobytes())


def decode_rle8_py(src: bytes, size: int) -> bytearray:
    """Referência byte a byte do RLE8 (os testes comparam a versão vetorizada com ela)."""
    out = bytearray()
    i = 0
    while i < len(src) and len(out) < size:
        b = src[i]; i += 1
        if b & 0xC0 == 0x40:          # 01LLLLLL + cor: run de L pixels
            out += bytes([src[i]]) * (b & 0x3F); i += 1
        else:                          # pixel literal
            out.append(b)
    return out[:size]


def encode_rle8(pixels: bytes) -> bytes:
    out = bytearray()
    n, i = len(pixels), 0
    while i < n:
        c = pixels[i]
        run = 1
        while i + run < n and pixels[i + run] == c and run < 63:
            run += 1
        if run > 1 or c & 0xC0 == 0x40:  # literal que pareça marcador vira run de 1
            out += bytes((0x40 | run, c))
        else:
            out.append(c)
        i += run
    return bytes(out)


# ------------------------------------------------------------------ RLE5

def decode_rle5(src: bytes, size: int) -> bytearray:
    out = bytearray()
    i = 0
    while i + 1 < len(src) and len(out) < size:
        run_len, data_len = src[i], src[i + 1]; i += 2
        color = 0
        if data_len & 0x80:
            color = src[i] & 0x1F; i += 1
        out += bytes([color]) * (run_len + 1)
        for _ in range(data_len & 0x7F):
            b = src[i]; i += 1
            out += bytes([b & 0x1F]) * ((b >> 5) + 1)
    return out[:size]


# ------------------------------------------------------------------ LZ5

def decode_lz5(src: bytes, size: int) -> bytearray:
    out = bytearray()
    i = 0
    flags = bit = 0
    recycled = recycled_count = 0
    while i < len(src) and len(out) < size:
        if bit == 0:
            flags = src[i]; i += 1
        if not (flags >> bit) & 1:
            b = src[i]; i += 1           # pacote RLE: CCCPPPPP
            count, color = b >> 5, b & 0x1F
            if count == 0:
                count = src[i] + 8; i += 1
            out += bytes([color]) * count
        else:
            b = src[i]; i += 1           # pacote de cópia: HHLLLLLL
            hi, length = b >> 6, b & 0x3F
            if length == 0:              # cópia longa
                offset = (hi << 8) | src[i]; i += 1
                length = src[i] + 3; i += 1
            else:                        # cópia curta, bits HH reciclados
                length += 1
                if recycled_count == 3:
                    offset = recycled | hi
                    recycled = recycled_count = 0
                else:
                    recycled |= hi << ((3 - recycled_count) * 2)
                    recycled_count += 1
                    offset = src[i]; i += 1
            start = len(out) - (offset + 1)
            for k in range(length):      # a cópia pode sobrepor o destino
                out.append(out[start + k] if start + k >= 0 else 0)
        bit = (bit + 1) & 7
    return out[:size]


_LZ5_WINDOW = 1024     # distância máxima da cópia longa (offset de 10 bits + 1)
_LZ5_SHORT_WINDOW = 256
_LZ5_MAX_COPY = 258    # cópia longa: byte + 3
_LZ5_MAX_SHORT = 64    # cópia curta: 6 bits + 1
_LZ5_MAX_RUN = 263     # RLE longo: byte + 8
_LZ5_CANDIDATES = 48   # posições anteriores testadas por busca


def encode_lz5(pixels: bytes) -> bytes:
    """Codifica pixels de 5 bits (0..31) em LZ5.

    Escolha gulosa por pixels cobertos / bytes gastos entre RLE (1-2 bytes),
    cópia curta (2 bytes; a cada 4 cópias curtas a 4ª custa 1 byte, porque seu
    offset viaja nos bits HH das três anteriores) e cópia longa (3 bytes).
    """
    if any(p > 31 for p in pixels):
        raise ValueError(tr("LZ5 only accepts color indices 0..31 (5-bit sprites)"))
    n = len(pixels)
    packets: list[tuple] = []  # ("rle", bytes) | ("long", bytes) | ("short", offset, length)
    heads: dict[bytes, list[int]] = {}
    i = 0
    while i < n:
        c = pixels[i]
        run = 1
        while i + run < n and pixels[i + run] == c and run < _LZ5_MAX_RUN:
            run += 1
        long_len = long_dist = short_len = short_dist = 0
        if i + 2 <= n:
            for j in reversed(heads.get(pixels[i:i + 2], [])[-_LZ5_CANDIDATES:]):
                dist = i - j
                if dist > _LZ5_WINDOW:
                    break
                length = 0
                while (i + length < n and length < _LZ5_MAX_COPY
                       and pixels[j + length] == pixels[i + length]):
                    length += 1
                if length > long_len:
                    long_len, long_dist = length, dist
                if dist <= _LZ5_SHORT_WINDOW and min(length, _LZ5_MAX_SHORT) > short_len:
                    short_len, short_dist = min(length, _LZ5_MAX_SHORT), dist
                if long_len == _LZ5_MAX_COPY and short_len == _LZ5_MAX_SHORT:
                    break
        options = [(run / (1 if run < 8 else 2), run, "rle")]
        if short_len >= 2:
            options.append((short_len / 1.75, short_len, "short"))
        if long_len >= 3:
            options.append((long_len / 3, long_len, "long"))
        _, step, kind = max(options, key=lambda o: (o[0], o[1]))
        if kind == "rle":
            body = bytes(((run << 5) | c,)) if run < 8 else bytes((c, run - 8))
            packets.append(("rle", body))
        elif kind == "short":
            packets.append(("short", short_dist - 1, short_len))
        else:
            off = long_dist - 1
            packets.append(("long", bytes(((off >> 8) << 6, off & 0xFF, long_len - 3))))
        for k in range(i, min(i + step, n - 1)):
            heads.setdefault(pixels[k:k + 2], []).append(k)
        i += step

    # cópias curtas em grupos de 4: os HH das 3 primeiras carregam o offset da 4ª
    shorts = [k for k, p in enumerate(packets) if p[0] == "short"]
    bodies: list[bytes] = [b""] * len(packets)
    for g in range(0, len(shorts), 4):
        grp = shorts[g:g + 4]
        if len(grp) == 4:
            off4 = packets[grp[3]][1]
            hh = [(off4 >> 6) & 3, (off4 >> 4) & 3, (off4 >> 2) & 3, off4 & 3]
        else:
            hh = [0] * len(grp)
        for pos, k in enumerate(grp):
            _, off, length = packets[k]
            first = (hh[pos] << 6) | (length - 1)
            bodies[k] = bytes((first,)) if pos == 3 else bytes((first, off))
    for k, p in enumerate(packets):
        if p[0] != "short":
            bodies[k] = p[1]

    out = bytearray()
    for g in range(0, len(packets), 8):
        out.append(sum(1 << b for b, p in enumerate(packets[g:g + 8]) if p[0] != "rle"))
        for body in bodies[g:g + 8]:
            out += body
    return bytes(out)
