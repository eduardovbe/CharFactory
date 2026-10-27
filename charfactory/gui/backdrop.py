"""Fundo de stage atrás do sprite (editor de sprites e de animações), como o fundo do FFS: um
stage simples desenhado no localcoord do personagem, um stage de verdade da pasta do MUGEN ou
uma imagem PNG. Em todos, o chão do fundo fica no y = 0 do eixo, para ver onde o pé pisa.

Configuração em settings["backdrop"]: {"kind": "none"|"simple"|"stage"|"image", "path": …,
"x": …, "y": …} (x, y: o ponto do chão na imagem PNG)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QActionGroup, QImage
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu

from ..i18n import tr
from ..tools import render
from .canvas import pil_to_qimage

DEFAULT = {"kind": "simple"}
_cache: dict = {}


def config(ctx) -> dict:
    cfg = ctx.settings.get("backdrop")
    return dict(cfg) if isinstance(cfg, dict) and cfg.get("kind") else dict(DEFAULT)


def _stage(path: str, mugen_dir: str | None, local_w: int) -> dict:
    """Stage renderizado com a câmera no centro, mais largo que a tela, e onde fica o chão."""
    from ..tools.stage import Stage, _nums
    p = Path(path)
    key = (str(p), p.stat().st_mtime, local_w)
    if key not in _cache:
        st = Stage(p, mugen_dir)
        sw, _sh = st.localcoord
        left, right, _hi, _lo = st.camera_bounds()
        width = min(sw * 4, sw + 2 * max(abs(left), abs(right)))
        img = st.render(0, 0, 0, players=False, width=width)
        zoff = _nums(st.get("StageInfo", "zoffset", str(st.localcoord[1] - 40)), 1)[0]
        k = local_w / sw if sw else 1.0
        _cache.clear()          # um stage por vez basta
        _cache[key] = {"kind": "image", "image": pil_to_qimage(img), "pos": (-width / 2 * k, -zoff * k),
                       "scale": k}
    return _cache[key]


def build(ctx) -> tuple[dict | None, str]:
    """(fundo para o canvas, aviso) para a configuração atual e o personagem aberto."""
    cfg = config(ctx)
    kind = cfg.get("kind")
    local = render.localcoord(ctx.char) if ctx.char else (320, 240)
    if kind == "simple":
        return {"kind": "simple", "size": local}, ""
    if kind == "stage" and cfg.get("path"):
        try:
            return _stage(cfg["path"], ctx.settings.get("mugen_dir") or None, local[0]), ""
        except Exception as e:  # noqa: BLE001 — stage sem SFF, .def quebrado…
            return {"kind": "simple", "size": local}, tr("Stage background not loaded ({0}); using the simple stage").format(e)
    if kind == "image" and cfg.get("path"):
        img = QImage(cfg["path"])
        if img.isNull():
            return {"kind": "simple", "size": local}, tr("Could not open the background image {0}").format(cfg["path"])
        x, y = cfg.get("x", img.width() // 2), cfg.get("y", round(img.height() * 200 / 240))
        return {"kind": "image", "image": img, "pos": (-x, -y), "scale": 1.0}, ""
    return None, ""


def _stages(ctx) -> list[Path]:
    root = Path(ctx.settings.get("mugen_dir") or "")
    try:
        return sorted((root / "stages").glob("*.def"))[:60] if ctx.settings.get("mugen_dir") else []
    except OSError:
        return []


def menu(win, parent) -> QMenu:
    """Menu do botão Fundo: nenhum, stage simples, stages do MUGEN, outro .def ou uma imagem."""
    ctx = win.ctx
    m = QMenu(parent)

    def choose(**cfg):
        ctx.settings["backdrop"] = cfg
        win.apply_backdrop()

    def fill():
        m.clear()
        cur = config(ctx)
        group = QActionGroup(m)

        def radio(text, on, fn):
            a = m.addAction(text)
            a.setCheckable(True)
            a.setChecked(on)
            group.addAction(a)
            a.triggered.connect(fn)
            return a
        radio(tr("No background"), cur["kind"] == "none", lambda: choose(kind="none"))
        radio(tr("Simple stage (wall and floor)"), cur["kind"] == "simple", lambda: choose(kind="simple"))
        sub = m.addMenu(tr("MUGEN stage"))
        found = _stages(ctx)
        for p in found:
            a = sub.addAction(p.stem)
            a.setCheckable(True)
            a.setChecked(cur["kind"] == "stage" and Path(cur.get("path", "")) == p)
            a.triggered.connect(lambda _=False, p=p: choose(kind="stage", path=str(p)))
        if not found:
            sub.addAction(tr("(set the MUGEN folder in Options)")).setEnabled(False)
        sub.addSeparator()
        sub.addAction(tr("Other stage (.def)…"), pick_stage)
        radio(tr("Image (PNG)…"), cur["kind"] == "image", pick_image)
        if cur["kind"] == "image":
            m.addAction(tr("Ground point of the image…"), ground_point)

    def pick_stage():
        start = str(Path(ctx.settings.get("mugen_dir") or Path.home()) / "stages")
        path, _ = QFileDialog.getOpenFileName(win, tr("Stage for the background"), start, tr("Stage (*.def)"))
        if path:
            choose(kind="stage", path=path)

    def pick_image():
        path, _ = QFileDialog.getOpenFileName(win, tr("Background image"), "", tr("Images (*.png *.jpg *.bmp)"))
        if not path:
            return
        img = QImage(path)
        choose(kind="image", path=path, x=img.width() // 2, y=round(img.height() * 200 / 240))
        ground_point()

    def ground_point():
        cur = config(ctx)
        text, ok = QInputDialog.getText(
            win, tr("Ground point of the image"),
            tr("Pixel of the image that sits on the axis (x,y): x in the middle of the floor, y on the ground line"),
            text=f"{cur.get('x', 0)},{cur.get('y', 0)}")
        if ok:
            try:
                x, y = (int(v) for v in text.split(","))
            except ValueError:
                return
            choose(**{**cur, "x": x, "y": y})

    m.aboutToShow.connect(fill)
    return m
