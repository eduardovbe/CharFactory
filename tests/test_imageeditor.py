"""Funções de pixel do editor de imagem: elipse, região, contorno, pixels soltos, tela e cores."""
import numpy as np

from charfactory.gui import imageeditor as ie


def test_ellipse_is_symmetric_and_closed():
    pts = set(ie.ellipse_points(0, 0, 10, 6, filled=False))
    assert (0, 3) in pts and (10, 3) in pts and (5, 0) in pts and (5, 6) in pts
    assert all((10 - x, y) in pts and (x, 6 - y) in pts for x, y in pts)     # simétrica
    m = np.zeros((7, 11), np.uint8)
    for x, y in pts:
        m[y, x] = 1
    ie.flood_fill(m, 0, 0, 2)                  # o contorno fecha: o balde de fora não entra
    assert m[3, 5] == 0
    filled = set(ie.ellipse_points(10, 6, 0, 0, filled=True))     # cantos trocados dão o mesmo
    assert pts <= filled and (5, 3) in filled
    assert set(ie.ellipse_points(2, 2, 3, 2, filled=False)) == {(2, 2), (3, 2)}


def test_region_contiguous_or_whole_image_and_fill_limit():
    a = np.zeros((4, 7), np.uint8)
    a[:, 3] = 5                                 # parede no meio
    assert ie.region(a, 0, 0).sum() == 12
    assert ie.region(a, 0, 0, contiguous=False).sum() == 24
    lim = np.zeros_like(a, bool)
    lim[:2] = True
    assert ie.flood_fill(a, 0, 0, 9, contiguous=False, limit=lim)
    assert (a[:2, :3] == 9).all() and (a[:2, 4:] == 9).all() and (a[2:] != 9).all()


def test_outline_inside_outside_and_isolated_pixels():
    a = np.zeros((7, 7), np.uint8)
    a[2:5, 2:5] = 3
    a[0, 6] = 4                                  # pixel solto
    out = ie.outline_mask(a)
    assert out[1, 3] and out[3, 1] and not out[1, 1] and not out[3, 3]
    assert ie.outline_mask(a, diagonal=True)[1, 1]
    inner = ie.outline_mask(a, inside=True)
    assert inner[2, 2] and inner[2, 3] and not inner[3, 3]
    assert ie.isolated_mask(a).sum() == 1 and ie.isolated_mask(a)[0, 6]
    sel = np.zeros_like(a, bool)
    sel[2:5, 2:3] = True                         # só a coluna da esquerda da figura
    lim = ie.outline_mask(a, within=sel)
    assert lim[3, 1] and not lim[3, 5]


def test_resize_canvas_shifted_and_bbox():
    a = np.arange(12, dtype=np.uint8).reshape(3, 4)
    b = ie.resize_canvas(a, 2, 1, 0, -1)         # 2 à esquerda, 1 em cima, corta 1 embaixo
    assert b.shape == (3, 6) and b[1, 2] == a[0, 0] and (b[0] == 0).all() and b[2, 5] == a[1, 3]
    assert ie.bbox(b != 0) == (2, 1, 5, 2)
    s = ie.shifted(a, 1, -1)
    assert s[0, 1] == a[1, 0] and (s[2] == 0).all()
    rgba = np.zeros((2, 2, 4), np.uint8)
    rgba[0, 0] = (250, 10, 10, 255)
    rgba[0, 1] = (0, 0, 240, 255)
    rgba[1, 1] = (0, 0, 240, 0)                  # transparente vira 0
    pal = [(0, 0, 0), (255, 0, 0), (0, 0, 255)]
    assert ie.nearest_indices(rgba, pal).tolist() == [[1, 2], [0, 0]]
