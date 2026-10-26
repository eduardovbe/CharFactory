"""Perfis de execução (Depurar › Perfis de execução, Ctrl+Shift+R): cada perfil é um jeito de
rodar o char no MUGEN ou no Ikemen GO — oponente, palco, quem joga cada lado (humano ou IA e o
nível), a cor, a vida e a energia de cada um, rounds, tempo, som e as opções que só o Ikemen
tem. A linha de comando aparece embaixo enquanto se edita, e o que o motor escolhido não aceita
fica apagado com o motivo na dica."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QSpinBox,
                               QVBoxLayout)

from ..tools import runner
from ..tools.runner import RunProfile
from .context import AppContext
from .widgets import hint_label
from ..i18n import tr


def presets() -> list[tuple[str, dict]]:
    """Modelos para um perfil novo: os testes que mais se repetem ao fazer um char."""
    return [
        (tr("Blank"), {}),
        (tr("Test supers (full power, P2 standing still)"), {"max_power": True, "p2_ai": 0, "rounds": 1,
                                                             "time": -1, "engine": "ikemen"}),
        (tr("Against the AI (level 8)"), {"p2_ai": 8, "ai_level": 8}),
        (tr("AI vs AI (watch the character's AI)"), {"p1_ai": 8, "p2_ai": 8, "rounds": 2}),
        (tr("Quick fight (1 round, no music, window)"), {"rounds": 1, "no_music": True, "windowed": True}),
        (tr("Combo practice (P2 with a lot of life, no time)"), {"p2_life": 9999, "time": -1, "p2_ai": 0,
                                                                 "engine": "ikemen"}),
    ]


def _opt_spin(lo: int, hi: int, default_text: str, tip: str = "") -> QSpinBox:
    """Número opcional: o menor valor da faixa é 'padrão do motor' (vira None)."""
    sb = QSpinBox()
    sb.setRange(lo - 1, hi)
    sb.setSpecialValueText(default_text)
    sb.setValue(lo - 1)
    sb.setToolTip(tip)
    return sb


def _get(sb: QSpinBox):
    return None if sb.value() == sb.minimum() else sb.value()


def _set(sb: QSpinBox, v) -> None:
    sb.setValue(sb.minimum() if v is None else int(v))


class RunProfilesDialog(QDialog):
    """Edita a lista de perfis; Rodar agora roda o perfil escolhido (sinal run_requested)."""
    run_requested = Signal(object)

    def __init__(self, ctx: AppContext, parent=None, select: str | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(tr("Run profiles"))
        self.setMinimumSize(960, 640)
        self.profiles = [RunProfile.from_dict(p.to_dict()) for p in runner.load_profiles(ctx.settings)]
        self._quiet = False

        # ------------------------------------------------ lista
        self.list = QListWidget()
        self.list.setMinimumWidth(220)
        self.list.currentRowChanged.connect(self._show)
        self.list.itemDoubleClicked.connect(lambda _: self.rename())
        new = QPushButton(tr("New"))
        new.setToolTip(tr("New profile from a template"))
        menu = QMenu(new)
        for label, values in presets():
            menu.addAction(label, lambda v=values, n=label: self.add(n, v))
        new.setMenu(menu)
        dup = QPushButton(tr("Duplicate"))
        dup.clicked.connect(self.duplicate)
        ren = QPushButton(tr("Rename"))
        ren.clicked.connect(self.rename)
        self.del_btn = QPushButton(tr("Delete"))
        self.del_btn.clicked.connect(self.delete)
        lbtn = QHBoxLayout()
        for b in (new, dup, ren, self.del_btn):
            lbtn.addWidget(b)
        left = QVBoxLayout()
        left.addWidget(QLabel(tr("<b>Profiles</b>")))
        left.addWidget(self.list, 1)
        left.addLayout(lbtn)
        left.addWidget(hint_label(tr("Ctrl+R runs the active profile; the arrow next to Run in the toolbar "
                                     "picks another one.")))

        # ------------------------------------------------ motor e luta
        self.engine = QComboBox()
        self.engine.addItem(tr("Automatic"), "auto")
        self.engine.addItem("MUGEN", "mugen")
        self.engine.addItem("Ikemen GO", "ikemen")
        self.engine_note = QLabel()
        self.engine_note.setObjectName("dim")
        self.engine_note.setWordWrap(True)
        self.p2 = QComboBox()
        self.p2.setEditable(True)
        self.p2.lineEdit().setPlaceholderText(tr("empty = the character itself (e.g. kfm)"))
        self.stage = QComboBox()
        self.stage.setEditable(True)
        self.stage.lineEdit().setPlaceholderText(tr("empty = one that exists"))
        self.rounds = QSpinBox()
        self.rounds.setRange(0, 99)
        self.rounds.setSpecialValueText(tr("engine default"))
        self.rounds.setToolTip(tr("Plays this many rounds and closes the engine"))
        self.time = _opt_spin(-1, 999, tr("engine default"), tr("Round time; -1 = no time limit (Ikemen GO)"))
        match = QGroupBox(tr("Match"))
        mf = QFormLayout(match)
        mf.addRow(tr("Engine"), self.engine)
        mf.addRow(self.engine_note)
        mf.addRow(tr("Opponent (P2)"), self.p2)
        mf.addRow(tr("Stage"), self.stage)
        row = QHBoxLayout()
        row.addWidget(self.rounds)
        row.addWidget(QLabel(tr("Time")))
        row.addWidget(self.time)
        row.addStretch()
        mf.addRow(tr("Rounds"), row)

        # ------------------------------------------------ jogadores
        players = QGroupBox(tr("Players"))
        pg = QGridLayout(players)
        pg.addWidget(QLabel(""), 0, 0)
        pg.addWidget(QLabel(tr("<b>P1 (your character)</b>")), 0, 1)
        pg.addWidget(QLabel(tr("<b>P2 (opponent)</b>")), 0, 2)
        self.side: dict[int, dict] = {}
        rows = [(tr("Control"), "ai"), (tr("Palette"), "color"), (tr("Life"), "life"),
                (tr("Max life"), "life_max"), (tr("Power"), "power")]
        for r, (label, _) in enumerate(rows, 1):
            pg.addWidget(QLabel(label), r, 0)
        for n in (1, 2):
            ai = QComboBox()
            ai.addItem(tr("Human"), 0)
            for lvl in range(1, 9):
                ai.addItem(tr("AI level {0}").format(lvl), lvl)
            color = QComboBox()
            color.addItem(tr("Default"), 0)
            for k in range(1, 13):
                color.addItem(tr("Palette {0}").format(k), k)
            life = _opt_spin(0, 99999, tr("default"), tr("Starting life"))
            life_max = _opt_spin(1, 99999, tr("default"), tr("Maximum life (Ikemen GO)"))
            power = _opt_spin(0, 99999, tr("default"), tr("Starting power (1000 = one bar)"))
            self.side[n] = {"ai": ai, "color": color, "life": life, "life_max": life_max, "power": power}
            for r, (_, key) in enumerate(rows, 1):
                pg.addWidget(self.side[n][key], r, n)

        # ------------------------------------------------ opções
        self.ai_level = QComboBox()
        self.ai_level.addItem(tr("From the engine options"), 0)
        for lvl in range(1, 9):
            self.ai_level.addItem(str(lvl), lvl)
        self.ai_level.setToolTip(tr("Game difficulty for every AI (-ailevel)"))
        self.speed = QSpinBox()
        self.speed.setRange(-9, 9)
        self.speed.setSpecialValueText(tr("normal"))
        self.speed.setMinimum(-10)
        self.speed.setToolTip(tr("Game speed from -9 (slow, good for checking a move frame by frame) to 9 (Ikemen GO)"))
        self.windowed = QCheckBox(tr("Window (not full screen)"))
        self.max_power = QCheckBox(tr("Power always full (test supers)"))
        self.no_lifebars = QCheckBox(tr("Hide the life bars"))
        self.no_music = QCheckBox(tr("No music"))
        self.no_sound = QCheckBox(tr("No sound at all"))
        self.extra = QLineEdit()
        self.extra.setPlaceholderText(tr("other options, as on the command line (e.g. -log match.txt)"))
        opts = QGroupBox(tr("Options"))
        of = QFormLayout(opts)
        of.addRow(tr("AI difficulty"), self.ai_level)
        of.addRow(tr("Speed"), self.speed)
        checks = QGridLayout()
        for k, cb in enumerate((self.windowed, self.max_power, self.no_lifebars, self.no_music, self.no_sound)):
            checks.addWidget(cb, k // 2, k % 2)
        of.addRow(checks)
        of.addRow(tr("Extra"), self.extra)

        # ------------------------------------------------ linha de comando
        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        f = QFont("JetBrains Mono")
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.preview.setFont(f)
        self.warn = QLabel()
        self.warn.setObjectName("dim")
        self.warn.setWordWrap(True)

        right = QVBoxLayout()
        right.addWidget(match)
        right.addWidget(players)
        right.addWidget(opts)
        right.addWidget(QLabel(tr("Command line")))
        right.addWidget(self.preview)
        right.addWidget(self.warn)
        right.addStretch()
        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addLayout(right, 7)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.run_btn = bb.addButton(tr("Save and run"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.run_btn.setProperty("primary", True)
        self.run_btn.clicked.connect(self._run)
        bb.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self.save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(body, 1)
        lay.addWidget(bb)

        for w in (self.engine, self.ai_level, *(d["ai"] for d in self.side.values()),
                  *(d["color"] for d in self.side.values())):
            w.currentIndexChanged.connect(self._edited)
        for w in (self.rounds, self.time, self.speed, *(d[k] for d in self.side.values()
                                                         for k in ("life", "life_max", "power"))):
            w.valueChanged.connect(self._edited)
        for w in (self.windowed, self.max_power, self.no_lifebars, self.no_music, self.no_sound):
            w.toggled.connect(self._edited)
        for w in (self.p2, self.stage):
            w.editTextChanged.connect(self._edited)
        self.extra.textChanged.connect(self._edited)
        self.engine.currentIndexChanged.connect(self._fill_folders)

        self._fill_list(select or ctx.settings.get("run_profile"))

    # ------------------------------------------------------------ lista

    def _fill_list(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.profiles:
            self.list.addItem(QListWidgetItem(p.name))
        self.list.blockSignals(False)
        names = [p.name for p in self.profiles]
        self.list.setCurrentRow(names.index(select) if select in names else 0)
        self.del_btn.setEnabled(len(self.profiles) > 1)

    def current(self) -> RunProfile:
        return self.profiles[max(0, self.list.currentRow())]

    def _unique(self, name: str) -> str:
        names = {p.name for p in self.profiles}
        out, k = name, 2
        while out in names:
            out, k = f"{name} {k}", k + 1
        return out

    def add(self, name: str, values: dict) -> None:
        base = RunProfile.from_dict({**RunProfile().to_dict(), **values})
        base.name = self._unique(name if values else tr("New profile"))
        self.profiles.append(base)
        self._fill_list(base.name)

    def duplicate(self) -> None:
        p = RunProfile.from_dict(self.current().to_dict())
        p.name = self._unique(p.name)
        self.profiles.append(p)
        self._fill_list(p.name)

    def rename(self) -> None:
        p = self.current()
        name, ok = QInputDialog.getText(self, tr("Rename profile"), tr("Name"), text=p.name)
        name = name.strip()
        if ok and name and name != p.name:
            p.name = self._unique(name)
            self._fill_list(p.name)

    def delete(self) -> None:
        if len(self.profiles) <= 1:
            return
        del self.profiles[self.list.currentRow()]
        self._fill_list()

    # ------------------------------------------------------------ formulário

    def _root(self, engine: str) -> tuple[Path | None, str]:
        s, c = self.ctx.settings, self.ctx.char
        if c is not None:
            try:
                return runner.engine_root(c, engine, s.get("mugen_dir") or None, s.get("ikemen_dir") or None)
            except ValueError:
                pass
        if engine == "ikemen":
            return (Path(s["ikemen_dir"]) if s.get("ikemen_dir") else None), "ikemen"
        return (Path(s["mugen_dir"]) if s.get("mugen_dir") else None), "mugen"

    def _fill_folders(self) -> None:
        """Os chars e palcos da pasta do motor nas listas de oponente e palco."""
        root, engine = self._root(self.engine.currentData() or "auto")
        self.engine_note.setText(tr("{0} in {1}").format(runner.ENGINE_NAMES[engine], root) if root
                                 else tr("Set the {0} folder in Tools › Options.").format(runner.ENGINE_NAMES[engine]))
        for combo, what in ((self.p2, "chars"), (self.stage, "stages")):
            text = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("")
            combo.addItems(runner.list_dir_entries(root, what) if root else [])
            combo.setEditText(text)
            combo.blockSignals(False)
        ike = engine == "ikemen"
        why = tr("Ikemen GO only: MUGEN closes with an invalid option")
        for w in (self.time, self.speed, self.windowed, self.max_power, self.no_lifebars,
                  self.side[1]["life_max"], self.side[2]["life_max"]):
            w.setEnabled(ike)
            w.setToolTip("" if ike else why)

    def _show(self, row: int) -> None:
        if row < 0 or row >= len(self.profiles):
            return
        p = self.profiles[row]
        self._quiet = True
        self.engine.setCurrentIndex(max(0, self.engine.findData(p.engine)))
        self._fill_folders()
        self.p2.setEditText(p.p2)
        self.stage.setEditText(p.stage)
        self.rounds.setValue(p.rounds)
        _set(self.time, p.time)
        self.speed.setValue(p.speed if p.speed else self.speed.minimum())
        self.ai_level.setCurrentIndex(max(0, self.ai_level.findData(p.ai_level)))
        for n, d in self.side.items():
            d["ai"].setCurrentIndex(max(0, d["ai"].findData(getattr(p, f"p{n}_ai"))))
            d["color"].setCurrentIndex(max(0, d["color"].findData(getattr(p, f"p{n}_color"))))
            for key in ("life", "life_max", "power"):
                _set(d[key], getattr(p, f"p{n}_{key}"))
        for key in ("windowed", "max_power", "no_lifebars", "no_music", "no_sound"):
            getattr(self, key).setChecked(bool(getattr(p, key)))
        self.extra.setText(p.extra)
        self._quiet = False
        self._update_preview()

    def _edited(self, *_) -> None:
        if self._quiet or self.list.currentRow() < 0:
            return
        p = self.current()
        p.engine = self.engine.currentData() or "auto"
        p.p2, p.stage = self.p2.currentText().strip(), self.stage.currentText().strip()
        p.rounds, p.time = self.rounds.value(), _get(self.time)
        p.speed = None if self.speed.value() == self.speed.minimum() else self.speed.value()
        p.ai_level = self.ai_level.currentData() or 0
        for n, d in self.side.items():
            setattr(p, f"p{n}_ai", d["ai"].currentData() or 0)
            setattr(p, f"p{n}_color", d["color"].currentData() or 0)
            for key in ("life", "life_max", "power"):
                setattr(p, f"p{n}_{key}", _get(d[key]))
        for key in ("windowed", "max_power", "no_lifebars", "no_music", "no_sound"):
            setattr(p, key, getattr(self, key).isChecked())
        p.extra = self.extra.text()
        self._update_preview()

    def _update_preview(self) -> None:
        p = self.current()
        root, engine = self._root(p.engine)
        exe = Path("Ikemen_GO.exe" if engine == "ikemen" else "mugen.exe")
        if root:
            try:
                exe, engine = runner.engine_exe(root)
            except (FileNotFoundError, OSError):
                pass
        c = self.ctx.char
        p1 = "char/char.def"
        if c is not None and root:
            try:
                p1 = runner.char_argument(c, root, engine)
            except ValueError:
                p1 = f"{c.dir.name}/{c.def_path.name}"
        try:
            args = runner.profile_args(exe, engine, p1, p)
        except ValueError as e:
            self.preview.setText("")
            self.warn.setText(str(e))
            return
        self.preview.setText(" ".join([exe.name, *args[1:]]))
        skipped = p.unsupported(engine)
        self.warn.setText(tr("Left out in {0} (Ikemen GO only): {1}").format(
            runner.ENGINE_NAMES[engine], ", ".join(skipped)) if skipped else "")

    # ------------------------------------------------------------ gravar

    def save(self) -> None:
        s = self.ctx.settings
        s["run_profiles"] = [p.to_dict() for p in self.profiles]
        s["run_profile"] = self.current().name
        s["run_engine"] = self.current().engine        # o "Automático/MUGEN/Ikemen" das Opções
        s.save()
        self.ctx.settingsChanged.emit()
        self.accept()

    def _run(self) -> None:
        prof = RunProfile.from_dict(self.current().to_dict())
        self.save()
        self.run_requested.emit(prof)


def profile_menu(ctx: AppContext, menu: QMenu, run, manage) -> None:
    """Preenche um menu com os perfis (o ativo marcado): escolher roda com ele e o deixa ativo."""
    menu.clear()
    active = ctx.settings.get("run_profile")
    profs = runner.load_profiles(ctx.settings)
    for k, p in enumerate(profs):
        a = menu.addAction(p.name)
        a.setCheckable(True)
        a.setChecked(p.name == active or (active not in {q.name for q in profs} and k == 0))

        def pick(_=False, name=p.name):
            ctx.settings["run_profile"] = name
            ctx.settings.save()
            run(name)
        a.triggered.connect(pick)
    menu.addSeparator()
    menu.addAction(tr("Run profiles…"), manage)
