"""Assistente Visual de Golpes (Move Wizard / Visual Move Builder).

Interface gráfica para projetar, ajustar propriedades de combate (HitDef, física,
áudio, cancelamentos e comandos) com prévia em tempo real de CNS e aplicação direta.
"""
from __future__ import annotations

from typing import Any
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
    QTabWidget, QVBoxLayout, QWidget
)

from ..tools import movegen
from ..project import Character
from .context import AppContext
from .fonts import code_font
from .editors.code import MugenHighlighter
from ..i18n import tr


class MoveWizardDialog(QDialog):
    """Diálogo completo do Assistente Visual de Golpes."""

    def __init__(self, ctx: AppContext, parent=None, initial_anim: int | None = None):
        super().__init__(parent)
        self._initializing = True
        self.ctx = ctx
        self.char: Character | None = ctx.char if ctx else None
        self.setWindowTitle(tr("Visual Move Wizard"))
        self.resize(1020, 680)

        # Configurações do layout principal dividido (Splitter Esquerda / Direita)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # --------------------------------------------------- Painel Esquerdo: Configurações
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)

        self.tabs = QTabWidget()

        # Tab 1: Geral & Física
        self.tab_general = QWidget()
        self._init_general_tab()
        self.tabs.addTab(self.tab_general, tr("General and physics"))

        # Tab 2: Animação & Áudio
        self.tab_anim_audio = QWidget()
        self._init_anim_audio_tab(initial_anim)
        self.tabs.addTab(self.tab_anim_audio, tr("Animation and sound"))

        # Tab 3: Acerto & Combate (HitDef)
        self.tab_hitdef = QWidget()
        self._init_hitdef_tab()
        self.tabs.addTab(self.tab_hitdef, tr("Hit (HitDef)"))

        # Tab 4: Comando & Cancelamentos
        self.tab_cmd = QWidget()
        self._init_command_tab()
        self.tabs.addTab(self.tab_cmd, tr("Command and input (-1)"))

        left_layout.addWidget(self.tabs, 1)

        # --------------------------------------------------- Painel Direito: Preview ao vivo
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)

        right_layout.addWidget(QLabel(tr("<b>Generated code</b> · updates while you edit")))

        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setReadOnly(True)
        self.preview_edit.setFont(code_font())
        self.preview_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        # Syntax highlighter com tema ativo
        if self.ctx and self.ctx.theme:
            self.highlighter = MugenHighlighter(self.preview_edit.document(), self.ctx.theme)

        right_layout.addWidget(self.preview_edit, 1)

        # Botões de Ação
        btn_bar = QHBoxLayout()

        btn_copy = QPushButton(tr("Copy code"))
        btn_copy.setToolTip(tr("Copies the generated code to the clipboard"))
        btn_copy.clicked.connect(self.copy_code)

        btn_insert = QPushButton(tr("Insert into editor"))
        btn_insert.setToolTip(tr("Inserts the generated text at the cursor position in the states editor"))
        btn_insert.clicked.connect(self.insert_in_editor)

        btn_apply = QPushButton(tr("Generate and apply to the character"))
        btn_apply.setProperty("primary", True)
        btn_apply.clicked.connect(self.apply_to_character)

        btn_close = QPushButton(tr("Close"))
        btn_close.clicked.connect(self.reject)

        btn_bar.addWidget(btn_copy)
        btn_bar.addWidget(btn_insert)
        btn_bar.addWidget(btn_apply)
        btn_bar.addWidget(btn_close)

        right_layout.addLayout(btn_bar)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([480, 540])

        main_lay = QVBoxLayout(self)
        main_lay.addWidget(splitter)

        # Carrega preset inicial e gera preview
        self._initializing = False
        self.on_kind_changed()
        self.update_preview()

    # ======================================================= Inicialização das Abas

    def _init_general_tab(self) -> None:
        f = QFormLayout(self.tab_general)

        self.spin_state = QSpinBox()
        self.spin_state.setRange(0, 999999)
        self.spin_state.setValue(200)
        self.spin_state.valueChanged.connect(self._on_state_num_changed)

        self.edit_name = QLineEdit(tr("Strong Punch"))
        self.edit_name.textChanged.connect(self.update_preview)

        self.combo_kind = QComboBox()
        for k, label in movegen.MOVE_KINDS.items():
            self.combo_kind.addItem(label, k)
        self.combo_kind.currentIndexChanged.connect(self.on_kind_changed)

        self.combo_statetype = QComboBox()
        self.combo_statetype.addItems([tr("S (Standing)"), tr("C (Crouching)"), tr("A (Air)")])
        self.combo_statetype.currentIndexChanged.connect(self.update_preview)

        self.combo_movetype = QComboBox()
        self.combo_movetype.addItems([tr("A (Attack)"), tr("I (Idle)")])
        self.combo_movetype.currentIndexChanged.connect(self.update_preview)

        self.combo_physics = QComboBox()
        self.combo_physics.addItems([tr("S (Stand)"), tr("C (Crouch)"), tr("A (Air)"), tr("N (None / Manual)")])
        self.combo_physics.currentIndexChanged.connect(self.update_preview)

        # VelSet inicial
        vel_box = QHBoxLayout()
        self.spin_vel_x = QDoubleSpinBox()
        self.spin_vel_x.setRange(-50.0, 50.0)
        self.spin_vel_x.setValue(0.0)
        self.spin_vel_x.setSingleStep(0.5)
        self.spin_vel_x.valueChanged.connect(self.update_preview)

        self.spin_vel_y = QDoubleSpinBox()
        self.spin_vel_y.setRange(-50.0, 50.0)
        self.spin_vel_y.setValue(0.0)
        self.spin_vel_y.setSingleStep(0.5)
        self.spin_vel_y.valueChanged.connect(self.update_preview)

        vel_box.addWidget(QLabel("X"))
        vel_box.addWidget(self.spin_vel_x, 1)
        vel_box.addWidget(QLabel("Y"))
        vel_box.addWidget(self.spin_vel_y, 1)

        # Power / Custo
        self.spin_power = QSpinBox()
        self.spin_power.setRange(0, 5000)
        self.spin_power.setSingleStep(500)
        self.spin_power.setValue(0)
        self.spin_power.valueChanged.connect(self.update_preview)

        # SuperPause
        self.chk_superpause = QCheckBox(tr("Enable SuperPause (screen freeze and darkening)"))
        self.chk_superpause.toggled.connect(self.update_preview)
        # a duração só vale com a SuperPause ligada
        self.chk_superpause.toggled.connect(lambda on: self.spin_pause_time.setEnabled(on))

        self.spin_pause_time = QSpinBox()
        self.spin_pause_time.setRange(5, 120)
        self.spin_pause_time.setValue(30)
        self.spin_pause_time.valueChanged.connect(self.update_preview)
        self.spin_pause_time.setEnabled(self.chk_superpause.isChecked())

        f.addRow(tr("Statedef number"), self.spin_state)
        f.addRow(tr("Move name"), self.edit_name)
        f.addRow(tr("Category"), self.combo_kind)
        f.addRow(tr("Posture (statetype)"), self.combo_statetype)
        f.addRow(tr("Type (movetype)"), self.combo_movetype)
        f.addRow(tr("Physics (physics)"), self.combo_physics)
        f.addRow(tr("Initial velocity (VelSet)"), vel_box)
        f.addRow(tr("Power cost (power)"), self.spin_power)
        f.addRow(self.chk_superpause)
        f.addRow(tr("Pause length (ticks)"), self.spin_pause_time)

    def _init_anim_audio_tab(self, initial_anim: int | None) -> None:
        f = QFormLayout(self.tab_anim_audio)

        self.spin_hit_elem = QSpinBox()
        self.spin_hit_elem.setRange(1, 99)
        self.spin_hit_elem.setValue(2)
        self.spin_hit_elem.valueChanged.connect(self.update_preview)

        self.combo_anim = QComboBox()
        self.combo_anim.setEditable(True)
        if self.char and self.char.anims:
            for act in sorted(self.char.anims.actions.values(), key=lambda a: a.number):
                frames_cnt = len(act.frames)
                act_name = self.char.anims.name_of(act.number)
                lbl = tr("{0}: {1} ({2} frames)").format(act.number, act_name, frames_cnt) if act_name else tr("{0} ({1} frames)").format(act.number, frames_cnt)
                self.combo_anim.addItem(lbl, act.number)
        else:
            self.combo_anim.addItem(tr("200 (Default)"), 200)

        self.combo_anim.currentTextChanged.connect(self._on_anim_changed)

        if initial_anim is not None:
            idx = self.combo_anim.findData(initial_anim)
            if idx >= 0:
                self.combo_anim.setCurrentIndex(idx)
            else:
                self.combo_anim.setEditText(str(initial_anim))
        self._on_anim_changed(self.combo_anim.currentText())

        # Voz
        self.edit_voice = QLineEdit()
        self.edit_voice.setPlaceholderText(tr("e.g. S0, 1 (or empty)"))
        self.edit_voice.textChanged.connect(self.update_preview)

        # Som de ataque (PlaySnd)
        snd_box = QHBoxLayout()
        self.edit_sound = QLineEdit("S5, 0")
        self.edit_sound.setPlaceholderText(tr("e.g. S5, 0"))
        self.edit_sound.textChanged.connect(self.update_preview)

        self.spin_sound_elem = QSpinBox()
        self.spin_sound_elem.setRange(1, 99)
        self.spin_sound_elem.setValue(1)
        self.spin_sound_elem.valueChanged.connect(self.update_preview)

        snd_box.addWidget(self.edit_sound, 1)
        snd_box.addWidget(QLabel(tr("on frame")))
        snd_box.addWidget(self.spin_sound_elem)

        f.addRow(tr("Animation"), self.combo_anim)
        f.addRow(tr("Hit frame"), self.spin_hit_elem)
        f.addRow(tr("Voice at the start"), self.edit_voice)
        f.addRow(tr("Sound (PlaySnd)"), snd_box)

    def _init_hitdef_tab(self) -> None:
        f = QFormLayout(self.tab_hitdef)

        # Dano
        dmg_box = QHBoxLayout()
        self.spin_dmg_hit = QSpinBox()
        self.spin_dmg_hit.setRange(0, 9999)
        self.spin_dmg_hit.setValue(50)
        self.spin_dmg_hit.valueChanged.connect(self.update_preview)

        self.spin_dmg_guard = QSpinBox()
        self.spin_dmg_guard.setRange(0, 9999)
        self.spin_dmg_guard.setValue(6)
        self.spin_dmg_guard.valueChanged.connect(self.update_preview)

        dmg_box.addWidget(QLabel(tr("Hit")))
        dmg_box.addWidget(self.spin_dmg_hit, 1)
        dmg_box.addWidget(QLabel(tr("Guard")))
        dmg_box.addWidget(self.spin_dmg_guard, 1)

        # Guarda
        self.combo_guardflag = QComboBox()
        self.combo_guardflag.addItem(tr("MA (Mid and Air - Default)"), "MA")
        self.combo_guardflag.addItem(tr("M (Ground only)"), "M")
        self.combo_guardflag.addItem(tr("H (High / Overhead - beats crouching)"), "H")
        self.combo_guardflag.addItem(tr("L (Low / Sweep - beats standing)"), "L")
        self.combo_guardflag.addItem(tr("A (Unblockable in the air)"), "A")
        self.combo_guardflag.addItem(tr("None (Fully unblockable)"), "")
        self.combo_guardflag.currentIndexChanged.connect(self.update_preview)

        # Ground Type
        self.combo_ground_type = QComboBox()
        self.combo_ground_type.addItems(["High", "Low", "Trip"])
        self.combo_ground_type.currentIndexChanged.connect(self.update_preview)

        # Efeito de Queda
        self.combo_fall = QComboBox()
        self.combo_fall.addItem(tr("Normal (No fall)"), "none")
        self.combo_fall.addItem(tr("Knockdown (with recovery)"), "fall_recover")
        self.combo_fall.addItem(tr("Hard knockdown (no recovery)"), "fall_hard")
        self.combo_fall.currentIndexChanged.connect(self.update_preview)

        # Pausetime
        pause_box = QHBoxLayout()
        self.spin_p1_pause = QSpinBox()
        self.spin_p1_pause.setRange(0, 60)
        self.spin_p1_pause.setValue(8)
        self.spin_p1_pause.valueChanged.connect(self.update_preview)

        self.spin_p2_pause = QSpinBox()
        self.spin_p2_pause.setRange(0, 60)
        self.spin_p2_pause.setValue(9)
        self.spin_p2_pause.valueChanged.connect(self.update_preview)

        pause_box.addWidget(QLabel("P1"))
        pause_box.addWidget(self.spin_p1_pause, 1)
        pause_box.addWidget(QLabel("P2"))
        pause_box.addWidget(self.spin_p2_pause, 1)

        # Recuo Solo (ground.velocity)
        gvel_box = QHBoxLayout()
        self.spin_gvel_x = QDoubleSpinBox()
        self.spin_gvel_x.setRange(-50.0, 50.0)
        self.spin_gvel_x.setValue(-3.5)
        self.spin_gvel_x.setSingleStep(0.5)
        self.spin_gvel_x.valueChanged.connect(self.update_preview)

        self.spin_gvel_y = QDoubleSpinBox()
        self.spin_gvel_y.setRange(-50.0, 50.0)
        self.spin_gvel_y.setValue(0.0)
        self.spin_gvel_y.setSingleStep(0.5)
        self.spin_gvel_y.valueChanged.connect(self.update_preview)

        gvel_box.addWidget(QLabel("X"))
        gvel_box.addWidget(self.spin_gvel_x, 1)
        gvel_box.addWidget(QLabel("Y"))
        gvel_box.addWidget(self.spin_gvel_y, 1)

        # Recuo Aéreo (air.velocity)
        avel_box = QHBoxLayout()
        self.spin_avel_x = QDoubleSpinBox()
        self.spin_avel_x.setRange(-50.0, 50.0)
        self.spin_avel_x.setValue(-2.5)
        self.spin_avel_x.setSingleStep(0.5)
        self.spin_avel_x.valueChanged.connect(self.update_preview)

        self.spin_avel_y = QDoubleSpinBox()
        self.spin_avel_y.setRange(-50.0, 50.0)
        self.spin_avel_y.setValue(-3.0)
        self.spin_avel_y.setSingleStep(0.5)
        self.spin_avel_y.valueChanged.connect(self.update_preview)

        avel_box.addWidget(QLabel("X"))
        avel_box.addWidget(self.spin_avel_x, 1)
        avel_box.addWidget(QLabel("Y"))
        avel_box.addWidget(self.spin_avel_y, 1)

        # Áudio de impacto
        self.edit_hitsound = QLineEdit("S5, 0")
        self.edit_hitsound.textChanged.connect(self.update_preview)

        self.edit_guardsound = QLineEdit("S6, 0")
        self.edit_guardsound.textChanged.connect(self.update_preview)

        # Faísca
        self.spin_sparkno = QSpinBox()
        self.spin_sparkno.setRange(-1, 99999)
        self.spin_sparkno.setValue(-1)
        self.spin_sparkno.valueChanged.connect(self.update_preview)

        f.addRow(tr("Damage"), dmg_box)
        f.addRow(tr("Possible guard (guardflag)"), self.combo_guardflag)
        f.addRow(tr("Impact height (ground.type)"), self.combo_ground_type)
        f.addRow(tr("Opponent reaction"), self.combo_fall)
        f.addRow(tr("Hit pause (pausetime)"), pause_box)
        f.addRow(tr("Ground pushback (ground.velocity)"), gvel_box)
        f.addRow(tr("Air velocity (air.velocity)"), avel_box)
        f.addRow(tr("Hit sound (hitsound)"), self.edit_hitsound)
        f.addRow(tr("Guard sound (guardsound)"), self.edit_guardsound)
        f.addRow(tr("Spark (sparkno)"), self.spin_sparkno)

    def _init_command_tab(self) -> None:
        f = QFormLayout(self.tab_cmd)

        self.chk_add_cmd = QCheckBox(tr("Add command to the CMD and trigger in [Statedef -1]"))
        self.chk_add_cmd.setChecked(True)
        self.chk_add_cmd.toggled.connect(self.update_preview)

        self.edit_cmd_name = QLineEdit("a")
        self.edit_cmd_name.textChanged.connect(self.update_preview)

        self.edit_cmd_seq = QLineEdit("a")
        self.edit_cmd_seq.setPlaceholderText(tr("e.g. a, ~D, DF, F, a, ~D, DB, B, b"))
        self.edit_cmd_seq.textChanged.connect(self.update_preview)

        self.spin_cmd_time = QSpinBox()
        self.spin_cmd_time.setRange(1, 60)
        self.spin_cmd_time.setValue(15)
        self.spin_cmd_time.valueChanged.connect(self.update_preview)

        self.chk_combo_cancel = QCheckBox(tr("Allow canceling normals into this move on hit (Combo Cancel)"))
        self.chk_combo_cancel.setChecked(True)
        self.chk_combo_cancel.toggled.connect(self.update_preview)

        self.edit_extra_cond = QLineEdit()
        self.edit_extra_cond.setPlaceholderText(tr("e.g. var(5) == 0 (optional condition in the triggerall)"))
        self.edit_extra_cond.textChanged.connect(self.update_preview)

        f.addRow(self.chk_add_cmd)
        f.addRow(tr("Command name"), self.edit_cmd_name)
        f.addRow(tr("Key sequence"), self.edit_cmd_seq)
        f.addRow(tr("Input window (ticks)"), self.spin_cmd_time)
        f.addRow(self.chk_combo_cancel)
        f.addRow(tr("Extra condition (triggerall)"), self.edit_extra_cond)

    # ======================================================= Eventos e Presets

    def on_kind_changed(self) -> None:
        """Aplica valores recomendados quando a categoria do golpe é trocada."""
        kind = self.combo_kind.currentData() or "normal"
        d = movegen.get_kind_defaults(kind)

        # Tipo, física
        idx_st = {"S": 0, "C": 1, "A": 2}.get(d["statetype"], 0)
        self.combo_statetype.setCurrentIndex(idx_st)

        idx_ph = {"S": 0, "C": 1, "A": 2, "N": 3}.get(d["physics"], 0)
        self.combo_physics.setCurrentIndex(idx_ph)

        # Dano e recuo
        self.spin_dmg_hit.setValue(d["damage"][0])
        self.spin_dmg_guard.setValue(d["damage"][1])
        self.spin_p1_pause.setValue(d["pausetime"][0])
        self.spin_p2_pause.setValue(d["pausetime"][1])
        self.spin_gvel_x.setValue(d["ground_velocity"][0])
        self.spin_gvel_y.setValue(d["ground_velocity"][1])
        self.spin_avel_x.setValue(d["air_velocity"][0])
        self.spin_avel_y.setValue(d["air_velocity"][1])

        # Guarda
        gf_idx = self.combo_guardflag.findData(d["guardflag"])
        if gf_idx >= 0:
            self.combo_guardflag.setCurrentIndex(gf_idx)

        # Queda
        if d.get("fall"):
            if not d.get("fall_recover"):
                self.combo_fall.setCurrentIndex(2)  # Hard knockdown
            else:
                self.combo_fall.setCurrentIndex(1)  # Fall
        else:
            self.combo_fall.setCurrentIndex(0)

        # Power
        self.spin_power.setValue(d.get("power", 0))

        # SuperPause
        sp = d.get("superpause")
        self.chk_superpause.setChecked(sp is not None)
        if sp:
            self.spin_pause_time.setValue(sp.get("time", 30))

        # Sugestão de Statedef e Comando
        st = self.spin_state.value()
        if kind == "normal" and st not in range(200, 300):
            self.spin_state.setValue(200)
            self.edit_cmd_name.setText("a")
            self.edit_cmd_seq.setText("a")
        elif kind == "crouch" and st not in range(400, 500):
            self.spin_state.setValue(400)
            self.edit_cmd_name.setText("crouch_a")
            self.edit_cmd_seq.setText("a")
        elif kind == "air" and st not in range(600, 700):
            self.spin_state.setValue(600)
            self.edit_cmd_name.setText("air_a")
            self.edit_cmd_seq.setText("a")
        elif kind in ("special", "air_special") and st not in range(1000, 2000):
            self.spin_state.setValue(1000)
            self.edit_cmd_name.setText("qcf_a")
            self.edit_cmd_seq.setText("~D, DF, F, a")
        elif kind == "super" and st not in range(3000, 4000):
            self.spin_state.setValue(3000)
            self.edit_cmd_name.setText("qcf_x2_a")
            self.edit_cmd_seq.setText("~D, DF, F, D, DF, F, a")

        self.update_preview()

    def _on_state_num_changed(self, val: int) -> None:
        # Se a animação tiver o mesmo número do estado anterior, sincroniza
        if self.combo_anim.currentData() is not None:
            anim_txt = self.combo_anim.currentText().split(":")[0].strip()
            if anim_txt.isdigit() and int(anim_txt) == val:
                return
        self.update_preview()

    def _on_anim_changed(self, txt: str) -> None:
        num = None
        data_val = self.combo_anim.currentData()
        if data_val is not None:
            num = int(data_val)
        else:
            first_word = txt.split(":")[0].strip()
            if first_word.isdigit():
                num = int(first_word)
        if num is not None and self.char:
            hit_elem = movegen.find_anim_hit_frame(self.char, num)
            self.spin_hit_elem.setValue(hit_elem)
        self.update_preview()

    # ======================================================= Coleta de Dados & Preview

    def collect_move_data(self) -> dict[str, Any]:
        """Extrai todos os parâmetros configurados na interface em um dicionário estruturado."""
        kind = self.combo_kind.currentData() or "normal"

        # Extrai número da animação
        anim = self.spin_state.value()
        anim_data = self.combo_anim.currentData()
        if anim_data is not None:
            anim = int(anim_data)
        else:
            txt = self.combo_anim.currentText().split(":")[0].strip()
            if txt.isdigit():
                anim = int(txt)

        fall_mode = self.combo_fall.currentData()
        fall = fall_mode in ("fall_recover", "fall_hard")
        fall_recover = fall_mode == "fall_recover"

        st_type = ["S", "C", "A"][self.combo_statetype.currentIndex()]
        m_type = ["A", "I"][self.combo_movetype.currentIndex()]
        ph_type = ["S", "C", "A", "N"][self.combo_physics.currentIndex()]

        sounds = []
        snd_val = self.edit_sound.text().strip()
        if snd_val:
            sounds.append({"elem": self.spin_sound_elem.value(), "value": snd_val})

        superpause = None
        if self.chk_superpause.isChecked():
            superpause = {
                "time": self.spin_pause_time.value(),
                "darken": 1,
                "unhittable": 1,
            }

        return {
            "stateno": self.spin_state.value(),
            "name": self.edit_name.text().strip() or f"Move_{self.spin_state.value()}",
            "kind": kind,
            "statetype": st_type,
            "movetype": m_type,
            "physics": ph_type,
            "anim": anim,
            "hit_elem": self.spin_hit_elem.value(),
            "velset": (self.spin_vel_x.value(), self.spin_vel_y.value()),
            "power": self.spin_power.value(),
            "superpause": superpause,
            "voice": self.edit_voice.text().strip() or None,
            "sounds": sounds,
            "damage": (self.spin_dmg_hit.value(), self.spin_dmg_guard.value()),
            "guardflag": self.combo_guardflag.currentData(),
            "ground_type": self.combo_ground_type.currentText(),
            "pausetime": (self.spin_p1_pause.value(), self.spin_p2_pause.value()),
            "ground_velocity": (self.spin_gvel_x.value(), self.spin_gvel_y.value()),
            "air_velocity": (self.spin_avel_x.value(), self.spin_avel_y.value()),
            "fall": fall,
            "fall_recover": fall_recover,
            "hitsound": self.edit_hitsound.text().strip() or "S5,0",
            "guardsound": self.edit_guardsound.text().strip() or "S6,0",
            "sparkno": self.spin_sparkno.value(),
            "add_command": self.chk_add_cmd.isChecked(),
            "command_name": self.edit_cmd_name.text().strip(),
            "command_seq": self.edit_cmd_seq.text().strip(),
            "command_time": self.spin_cmd_time.value(),
            "combo_cancel": self.chk_combo_cancel.isChecked(),
            "extra_condition": self.edit_extra_cond.text().strip() or None,
        }

    def update_preview(self) -> None:
        """Gera o código CNS em tempo real e exibe no editor da direita."""
        if getattr(self, "_initializing", False):
            return
        data = self.collect_move_data()
        cns_code = movegen.build_move_cns(self.char, data)

        full_preview = [cns_code]
        if data.get("add_command"):
            cmd_info, minus1 = movegen.build_command_cns(data)
            full_preview.append(f"; --- Definition for the .CMD file ---\n; [Command]\n; name = \"{cmd_info['name']}\"\n; command = {cmd_info['command']}\n; time = {cmd_info['time']}\n")
            full_preview.append(f"; --- Trigger for [Statedef -1] in the .CMD ---\n{minus1}")

        self.preview_edit.setPlainText("\n".join(full_preview))

    # ======================================================= Ações dos Botões

    def copy_code(self) -> None:
        """Copia o código gerado para a área de transferência."""
        text = self.preview_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            if self.ctx:
                self.ctx.status.emit(tr("Move code copied to the clipboard."))

    def insert_in_editor(self) -> None:
        """Insere o código no editor de código atualmente em foco."""
        text = movegen.build_move_cns(self.char, self.collect_move_data())
        parent_win = self.parent()
        if hasattr(parent_win, "insert_code"):
            parent_win.insert_code(text)
            self.accept()
        else:
            QMessageBox.information(self, tr("Insert Code"), tr("No code editor that accepts it was found. The code was copied to the clipboard."))
            QApplication.clipboard().setText(text)

    def apply_to_character(self) -> None:
        """Aplica o golpe diretamente aos arquivos do personagem aberto."""
        if not self.char:
            QMessageBox.warning(self, tr("Warning"), tr("No character open to apply the move to."))
            return

        data = self.collect_move_data()
        try:
            res = movegen.apply_move_to_character(self.char, data)
            msg = tr("Statedef {0} ('{1}') created successfully!").format(res['stateno'], res['name'])
            if res.get("command_added"):
                msg += tr(" Command added to the .CMD.")
            if res.get("minus1_added"):
                msg += tr(" Trigger added to [Statedef -1].")

            if self.ctx:
                self.ctx.changed("states")
                if res.get("command_added") or res.get("minus1_added"):
                    self.ctx.changed("commands")
                self.ctx.status.emit(msg)

            QMessageBox.information(self, tr("Move Applied"), msg)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, tr("Error Applying"), tr("Failed to apply the move to the character:\n{0}").format(e))
