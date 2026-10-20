"""Gera os temas embutidos em charfactory/gui/themes/*.json (cores da paleta do Tailwind CSS)."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "charfactory" / "gui" / "themes"


def cs(back_start, back_end, bg, fg, comment, block, number, string, keyword, kw_value,
       block_type, function, const, line, block_range, paren="#f43f5e", paren_range=None,
       attack="#ef4444", collision="#3b82f6", wave="#818cf8"):
    def c(h, bold=False, italic=False):
        h = h.lstrip("#")
        return {"rgb": [int(h[i:i + 2], 16) for i in (0, 2, 4)], "bold": bold, "italic": italic}
    return {
        "back_start": c(back_start), "back_end": c(back_end), "attackbox": c(attack),
        "collisionbox": c(collision), "sound_wave": c(wave), "block_range": c(block_range),
        "line": c(line), "paren": c(paren), "paren_range": c(paren_range or block_range),
        "comment": c(comment, italic=True), "block": c(block, bold=True), "number": c(number),
        "string": c(string), "keyword": c(keyword), "keyword_value": c(kw_value),
        "block_type": c(block_type, bold=True), "function": c(function), "const": c(const),
        "background": c(bg), "foreground": c(fg),
    }


THEMES = {
    # zinc-950/900/800 + indigo
    "Dark": {
        "colorset": cs("#27272a", "#18181b", "#111113", "#e4e4e7", "#71717a", "#a5b4fc", "#fcd34d", "#6ee7b7",
                       "#7dd3fc", "#c4b5fd", "#f472b6", "#5eead4", "#fdba74", "#1c1c20", "#161619"),
        "ui": {"window": "#09090b", "panel": "#18181b", "panel_alt": "#27272a", "input": "#111113",
               "text": "#e4e4e7", "text_dim": "#a1a1aa", "border": "#27272a", "accent": "#6366f1",
               "accent_soft": "#1e1b4b", "selection": "#312e81", "button": "#18181b", "button_hover": "#3f3f46",
               "icon": "#a1a1aa", "error": "#f87171", "warning": "#fbbf24"}},
    # white/slate + indigo
    "Light": {
        "colorset": cs("#f1f5f9", "#e2e8f0", "#ffffff", "#1e293b", "#94a3b8", "#4f46e5", "#d97706", "#059669",
                       "#0369a1", "#7c3aed", "#db2777", "#0d9488", "#ea580c", "#f8fafc", "#f1f5f9",
                       wave="#6366f1"),
        "ui": {"window": "#f8fafc", "panel": "#ffffff", "panel_alt": "#f1f5f9", "input": "#ffffff",
               "text": "#0f172a", "text_dim": "#64748b", "border": "#e2e8f0", "accent": "#4f46e5",
               "accent_soft": "#eef2ff", "selection": "#e0e7ff", "button": "#ffffff", "button_hover": "#f1f5f9",
               "icon": "#475569", "error": "#dc2626", "warning": "#d97706"}},
    # slate-950 + sky
    "Midnight": {
        "colorset": cs("#1e293b", "#0f172a", "#0b1120", "#e2e8f0", "#64748b", "#7dd3fc", "#fcd34d", "#86efac",
                       "#93c5fd", "#c4b5fd", "#f9a8d4", "#67e8f9", "#fdba74", "#111a2e", "#0f1729",
                       wave="#38bdf8"),
        "ui": {"window": "#020617", "panel": "#0f172a", "panel_alt": "#1e293b", "input": "#0b1120",
               "text": "#e2e8f0", "text_dim": "#94a3b8", "border": "#1e293b", "accent": "#38bdf8",
               "accent_soft": "#082f49", "selection": "#0c4a6e", "button": "#0f172a", "button_hover": "#334155",
               "icon": "#94a3b8", "accent_text": "#082f49"}},
    # stone + emerald (claro e quente)
    "Emerald": {
        "colorset": cs("#f5f5f4", "#e7e5e4", "#fffffe", "#1c1917", "#a8a29e", "#047857", "#b45309", "#0f766e",
                       "#1d4ed8", "#7e22ce", "#be123c", "#047857", "#c2410c", "#fafaf9", "#f5f5f4",
                       wave="#10b981"),
        "ui": {"window": "#fafaf9", "panel": "#ffffff", "panel_alt": "#f5f5f4", "input": "#ffffff",
               "text": "#1c1917", "text_dim": "#78716c", "border": "#e7e5e4", "accent": "#059669",
               "accent_soft": "#ecfdf5", "selection": "#d1fae5", "button": "#ffffff", "button_hover": "#f5f5f4",
               "icon": "#57534e"}},
    # violet/fuchsia
    "Arcade": {
        "colorset": cs("#2e1065", "#160430", "#12081f", "#f5f3ff", "#8b7aa8", "#e879f9", "#fde047", "#86efac",
                       "#a5f3fc", "#f0abfc", "#fb923c", "#22d3ee", "#fb7185", "#1d0f33", "#170b29",
                       attack="#f43f5e", collision="#22d3ee", wave="#e879f9"),
        "ui": {"window": "#0c0616", "panel": "#160a26", "panel_alt": "#24123c", "input": "#12081f",
               "text": "#f5f3ff", "text_dim": "#a78bfa", "border": "#2e1a4a", "accent": "#d946ef",
               "accent_soft": "#3b0b4a", "selection": "#581c87", "button": "#160a26", "button_hover": "#3b1d5e",
               "icon": "#c4b5fd"}},
    "Default": {
        "colorset": cs("#3e3d32", "#272822", "#272822", "#f8f8f2", "#75715e", "#a6e22e", "#ae81ff", "#e6db74",
                       "#f8f8f2", "#66d9ef", "#fd971f", "#a6e22e", "#f92672", "#3e3d32", "#2f302a", wave="#e6db74"),
        "ui": {"window": "#1e1f1a", "panel": "#272822", "panel_alt": "#34352d", "input": "#22231e",
               "text": "#f8f8f2", "text_dim": "#a59f85", "border": "#3a3b33", "accent": "#a6e22e",
               "accent_soft": "#2f3a1c", "selection": "#49483e", "button": "#272822", "button_hover": "#49483e",
               "icon": "#cfcfc2", "accent_text": "#1e1f1a"}},
    "Classic": {
        "colorset": cs("#c0c0c0", "#909090", "#ffffff", "#000000", "#808080", "#000080", "#800000", "#008080",
                       "#000000", "#800080", "#804000", "#0000ff", "#ff00ff", "#ffffe0", "#f0f0f0",
                       attack="#ff0000", collision="#0000ff", wave="#008000"),
        "ui": {"window": "#ece9d8", "panel": "#f4f3ee", "panel_alt": "#e3e0d0", "input": "#ffffff",
               "text": "#000000", "text_dim": "#5a5a5a", "border": "#c4c1b2", "accent": "#316ac5",
               "accent_soft": "#d8e3f5", "selection": "#c1d2ee", "button": "#f4f3ee", "button_hover": "#ffffff",
               "icon": "#333333"}},
    "High contrast": {
        "colorset": cs("#000000", "#000000", "#000000", "#ffffff", "#00ff00", "#ffff00", "#00ffff", "#00ff00",
                       "#ffffff", "#ff80ff", "#ffff00", "#00ffff", "#ff8080", "#202020", "#101010",
                       attack="#ff0000", collision="#00a0ff", wave="#ffff00"),
        "ui": {"window": "#000000", "panel": "#000000", "panel_alt": "#1a1a1a", "input": "#000000",
               "text": "#ffffff", "text_dim": "#e0e0e0", "border": "#ffffff", "accent": "#ffff00",
               "accent_soft": "#333300", "selection": "#0050a0", "button": "#000000", "button_hover": "#333333",
               "icon": "#ffffff", "accent_text": "#000000"}},
}


# Paletas de código refinadas (sobrepõem as do cs()): tons Tailwind 300/400 nos escuros e 600/700 nos claros.
CODE = {
    "Dark": dict(background="#111113", foreground="#d4d4d8", comment="#71717a", block="#a5b4fc",
                   number="#fdba74", string="#86efac", keyword="#93c5fd", key_trigger="#f9a8d4",
                   keyword_value="#c4b5fd", block_type="#fbbf24", function="#67e8f9", const="#5eead4",
                   operator="#a1a1aa", label="#a1a1aa", paren="#f472b6", line="#18181b", block_range="#141416"),
    "Light": dict(background="#ffffff", foreground="#1e293b", comment="#94a3b8", block="#4338ca",
                  number="#c2410c", string="#15803d", keyword="#1d4ed8", key_trigger="#be185d",
                  keyword_value="#7c3aed", block_type="#b45309", function="#0e7490", const="#0f766e",
                  operator="#64748b", label="#64748b", paren="#db2777", line="#f8fafc", block_range="#f1f5f9"),
    "Midnight": dict(background="#0b1120", foreground="#cbd5e1", comment="#64748b", block="#7dd3fc",
                       number="#fda4af", string="#a7f3d0", keyword="#93c5fd", key_trigger="#f0abfc",
                       keyword_value="#c4b5fd", block_type="#fcd34d", function="#5eead4", const="#fdba74",
                       operator="#94a3b8", label="#94a3b8", paren="#f472b6", line="#111a2e", block_range="#0f1729"),
    "Emerald": dict(background="#fffffe", foreground="#292524", comment="#a8a29e", block="#047857",
                      number="#b45309", string="#0f766e", keyword="#1d4ed8", key_trigger="#be123c",
                      keyword_value="#7e22ce", block_type="#c2410c", function="#0369a1", const="#047857",
                      operator="#78716c", label="#78716c", paren="#e11d48", line="#fafaf9", block_range="#f5f5f4"),
    "Arcade": dict(background="#12081f", foreground="#ede9fe", comment="#8b7aa8", block="#f0abfc",
                   number="#fde047", string="#86efac", keyword="#a5f3fc", key_trigger="#fb7185",
                   keyword_value="#c4b5fd", block_type="#fb923c", function="#22d3ee", const="#f9a8d4",
                   operator="#a78bfa", label="#a78bfa", paren="#facc15", line="#1d0f33", block_range="#170b29"),
    "Default": dict(background="#272822", foreground="#f8f8f2", comment="#75715e", block="#a6e22e",
                    number="#ae81ff", string="#e6db74", keyword="#66d9ef", key_trigger="#f92672",
                    keyword_value="#ae81ff", block_type="#66d9ef", function="#a6e22e", const="#fd971f",
                    operator="#f92672", label="#cfcfc2", paren="#fd971f", line="#3e3d32", block_range="#2f302a"),
    "Classic": dict(operator="#555555", key_trigger="#800000", label="#555555"),
    "High contrast": dict(operator="#ffffff", key_trigger="#ff80ff", label="#e0e0e0"),
}
STYLE = {"comment": (False, True), "block": (True, False), "block_type": (True, False),
         "key_trigger": (True, False), "label": (False, True)}


def apply_code(themes):
    for name, colors in CODE.items():
        cset = themes[name]["colorset"]
        for key, hexv in colors.items():
            h = hexv.lstrip("#")
            bold, italic = STYLE.get(key, (False, False))
            cset[key] = {"rgb": [int(h[i:i + 2], 16) for i in (0, 2, 4)], "bold": bold, "italic": italic}


apply_code(THEMES)

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for name, t in THEMES.items():
        slug = (name.lower().replace(" ", "-").replace("á", "a").replace("ã", "a"))
        (OUT / f"{slug}.json").write_text(json.dumps({"name": name, **t}, indent=1, ensure_ascii=False), "utf-8")
    print(len(THEMES), "temas ->", OUT)
