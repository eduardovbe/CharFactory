"""Gera as imagens do README (docs/media) abrindo o app de verdade, sem mostrar a janela.

Capturas em PNG na escala da tela (2x numa tela HiDPI) e animações em WebP: o simulador
andando tick a tick, uma animação tocando no editor e a troca de temas. Usa o kfm de uma
instalação do MUGEN (arquivos oficiais da Elecbyte).

    python tools/build_screenshots.py C:\\caminho\\do\\mugen
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "media"
SIZE = (1440, 860)          # janela, em pixels lógicos
MOTION_WIDTH = 1600         # largura das animações (as capturas ficam na escala da tela)


def pil(pixmap) -> Image.Image:
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def with_dialog(base: Image.Image, dialog: Image.Image, top: float | None = None) -> Image.Image:
    """Diálogo por cima da janela escurecida, com sombra."""
    out = Image.alpha_composite(base, Image.new("RGBA", base.size, (0, 0, 0, 110)))
    x = (base.width - dialog.width) // 2
    y = int(base.height * top) if top is not None else (base.height - dialog.height) // 2
    pad = 60
    shadow = Image.new("RGBA", (dialog.width + 2 * pad, dialog.height + 2 * pad), (0, 0, 0, 0))
    mask = Image.new("L", shadow.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad + 12, pad + dialog.width, pad + dialog.height + 12),
                                           radius=24, fill=150)
    shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(28)))
    out.alpha_composite(shadow, (x - pad, y - pad))
    out.alpha_composite(dialog, (x, y))
    return out


def save_png(img: Image.Image, name: str) -> None:
    img.convert("RGB").save(OUT / name, optimize=True)
    print(f"{name}: {img.width}x{img.height}, {(OUT / name).stat().st_size // 1024} KB")


def save_motion(frames: list[Image.Image], durations: list[int], name: str) -> None:
    w = MOTION_WIDTH
    small = [f.convert("RGB").resize((w, round(f.height * w / f.width)), Image.Resampling.LANCZOS)
             for f in frames]
    small[0].save(OUT / name, save_all=True, append_images=small[1:], duration=durations, loop=0,
                  quality=90, method=6)
    print(f"{name}: {len(frames)} frames, {(OUT / name).stat().st_size // 1024} KB")


def font(names: list[str], size: int):
    from PIL import ImageFont
    dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", ROOT / "charfactory" / "gui" / "fonts"]
    for name in names:
        for d in dirs:
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), size)
    return ImageFont.load_default(size)


def rounded(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def banner(screen: Image.Image, ui: dict) -> Image.Image:
    """Capa 2560x1280 (reduzida a 1280x640 vira a prévia social do GitHub): ícone, nome, frase e a janela."""
    def rgb(h: str) -> tuple[int, int, int]:
        return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
    W, H, S = 1280, 640, 2                      # desenha em 2x e reduz no fim
    bg, accent, text, dim = rgb(ui["window"]), rgb(ui["accent"]), rgb(ui["text"]), rgb(ui["text_dim"])
    img = Image.new("RGBA", (W * S, H * S), bg + (255,))
    # brilho verde suave atrás da janela
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((W * S * 0.45, H * S * 0.05, W * S * 1.25, H * S * 1.2), fill=accent + (80,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(160)))
    # grade de pontos discreta, como o canvas do app
    dots = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(dots)
    for x in range(0, W * S, 48):
        fade = min(1.0, max(0.0, (x - W * S * 0.38) / (W * S * 0.12)))   # some atrás do texto
        for y in range(0, H * S, 48):
            if fade:
                d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=text + (int(40 * fade),))
    img.alpha_composite(dots)
    # janela do app à direita, cortada pela borda
    w = int(W * S * 0.62)
    win = rounded(screen.resize((w, round(screen.height * w / screen.width)), Image.Resampling.LANCZOS), 28)
    x, y = int(W * S * 0.47), int(H * S * 0.16)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x, y + 30, x + win.width, y + win.height + 30), radius=28,
                                             fill=(0, 0, 0, 170))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(40)))
    img.alpha_composite(win, (x, y))
    edge = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle((x, y, x + win.width - 1, y + win.height - 1), radius=28,
                                           outline=text + (50,), width=3)
    img.alpha_composite(edge)
    # texto à esquerda
    d = ImageDraw.Draw(img)
    icon = Image.open(ROOT / "charfactory" / "gui" / "assets" / "charfactory.png").convert("RGBA")
    icon = icon.resize((150, 150), Image.Resampling.LANCZOS)
    left = 110
    img.alpha_composite(icon, (left, 250))
    d.text((left, 450), "Char Factory", font=font(["segoeuib.ttf", "JetBrainsMono-Bold.ttf"], 128), fill=text)
    d.text((left + 4, 620), "M.U.G.E.N character editor", font=font(["seguisb.ttf", "JetBrainsMono-Medium.ttf"], 52),
           fill=accent)
    small = font(["segoeui.ttf", "JetBrainsMono-Regular.ttf"], 38)
    for i, line in enumerate(["Sprites, animations, Clsn boxes, sounds and code,",
                              "plus a tick-by-tick move simulator.", "Free and open source."]):
        d.text((left + 4, 730 + i * 54), line, font=small, fill=dim)
    chips, cx = ["SFF v1/v2", "AIR", "CNS", "CMD", "ZSS"], left
    chip_font = font(["seguisb.ttf", "JetBrainsMono-Medium.ttf"], 32)
    for c in chips:
        tw = d.textlength(c, font=chip_font)
        d.rounded_rectangle((cx, 940, cx + tw + 44, 1000), radius=30, fill=rgb(ui["accent_soft"]),
                            outline=rgb(ui["accent_soft"]), width=2)
        d.text((cx + 22 + tw / 2, 970), c, font=chip_font, fill=accent, anchor="mm")
        cx += tw + 60
    return img


def main() -> int:
    if len(sys.argv) < 2 or not (Path(sys.argv[1]) / "chars" / "kfm" / "kfm.def").exists():
        print("usage: python tools/build_screenshots.py <MUGEN folder with chars/kfm>")
        return 1
    mugen = Path(sys.argv[1])
    tmp = Path(tempfile.mkdtemp(prefix="charfactory-shots-"))
    os.environ["APPDATA"] = str(tmp / "appdata")          # configuração limpa, sem a do usuário
    os.environ["CHARFACTORY_LANG"] = "en"
    shutil.copytree(mugen / "chars" / "kfm", tmp / "chars" / "kfm")
    (tmp / "data").mkdir()
    shutil.copy(mugen / "data" / "common1.cns", tmp / "data")
    OUT.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication([])
    from charfactory.gui.app import APP_NAME, MainWindow, app_icon, install_translator
    from charfactory.gui.context import AppContext
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    install_translator(app)
    ctx = AppContext()
    win = MainWindow(ctx)
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    win.resize(*SIZE)
    win.show()

    def settle(n: int = 8) -> None:
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    def shot() -> Image.Image:
        settle()
        return pil(win.grab())

    captured: dict[str, Image.Image] = {}

    def grab_dialog(key: str):
        """exec() de diálogo que só mostra, captura e fecha."""
        def run(dlg, *_):
            dlg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            dlg.show()
            settle(12)
            prepare = getattr(dlg, "_shot_prepare", None)
            if prepare:
                prepare()
                settle(12)
            captured[key] = pil(dlg.grab())
            dlg.reject()
            return 0
        return run

    settle()
    win.open_path(str(tmp / "chars" / "kfm" / "kfm.def"))
    settle()

    # editor de animações: soco com caixa de ataque visível
    an = win.anims
    an.onion.setChecked(False)

    def pose(frame: int) -> None:
        win.show_page(an)
        an.select_action(210)
        settle()
        an.group_combo.setCurrentIndex(max(0, an.group_combo.findData(210)))
        an.canvas.set_zoom(4)
        an.frame_nav.set_index(frame)

    pose(4)
    hero = shot()
    save_png(hero, "animations.png")
    cover = banner(hero, ctx.theme.ui)
    save_png(cover, "banner.png")
    save_png(cover.resize((1280, 640), Image.Resampling.LANCZOS), "social-preview.png")

    # sprites
    win.show_page(win.sprites)
    win.sprites.select(5000, 0)
    win.sprites.canvas.set_zoom(4)
    save_png(shot(), "sprites.png")

    # código: estados com a árvore à esquerda
    win.goto_code("kfm.cns", "[Statedef 210]")
    save_png(shot(), "code.png")

    # sons
    win.show_page(win.sounds)
    win.sounds.select(5, 0)
    save_png(shot(), "sounds.png")

    # editor de imagem
    from charfactory.gui import imageeditor
    win.show_page(win.sprites)
    win.sprites.select(0, 0)
    settle()
    imageeditor.ImageEditor.exec = grab_dialog("image")
    win.sprites.edit_image()
    save_png(with_dialog(shot(), captured["image"]), "image-editor.png")

    # paleta de comandos
    from charfactory.gui import dialogs
    win.show_page(an)
    orig_exec = QDialog.exec

    def palette_exec(dlg, *a):
        dlg._shot_prepare = lambda: dlg.edit.setText("anim")
        return grab_dialog("palette")(dlg, *a)
    dialogs.CommandPalette.exec = palette_exec
    win.command_palette()
    save_png(with_dialog(shot(), captured["palette"], top=0.08), "command-palette.png")
    dialogs.CommandPalette.exec = orig_exec

    # simulador: um golpe contra o P2 parado, tick a tick
    sp = win.simulator
    win.show_page(sp)
    sp.run_state(210)
    t = time.perf_counter()
    while sp.sim is None and time.perf_counter() - t < 120:
        settle(1)
    if sp.sim is None:
        print("the simulation did not finish")
        return 1
    frames, durations = [], []
    for tick in range(sp.slider.maximum() + 1):
        sp.slider.setValue(tick)
        t = time.perf_counter()
        while (sp._rendering or sp._key(tick) not in sp.cache) and time.perf_counter() - t < 20:
            settle(1)
        frames.append(shot())
        durations.append(60)
    durations[-1] = 1200
    save_png(frames[min(len(frames) - 1, 8)], "simulator.png")
    save_motion(frames, durations, "simulator.webp")

    # animação tocando no editor (meia velocidade, para dar para ler a timeline)
    pose(0)
    frames, durations = [], []
    anim = ctx.char.anims.get(210)
    for i, fr in enumerate(anim.frames):
        an.frame_nav.set_index(i)
        frames.append(shot())
        durations.append(max(1, fr.time if fr.time > 0 else 30) * 33)
    durations[-1] += 600
    save_motion(frames, durations, "animation.webp")

    # temas, no quadro do golpe
    pose(4)
    frames = []
    names = list(ctx.themes)
    for name in names:
        ctx.set_theme(name)
        frames.append(shot())
    ctx.set_theme(names[0])
    save_motion(frames, [1400] * len(frames), "themes.webp")

    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
