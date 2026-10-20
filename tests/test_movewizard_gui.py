"""Testes de interface gráfica para o Assistente Visual de Golpes (MoveWizardDialog)."""
from __future__ import annotations

import os
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

from charfactory import project
from charfactory.gui.context import AppContext
from charfactory.gui.movewizard import MoveWizardDialog


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_move_wizard_dialog_instantiation(qapp):
    ctx = AppContext()
    dlg = MoveWizardDialog(ctx)
    assert dlg.windowTitle() == "Visual Move Wizard"
    assert dlg.tabs.count() == 4
    # Preview inicial deve conter [Statedef 200]
    preview = dlg.preview_edit.toPlainText()
    assert "[Statedef 200]" in preview
    assert "type = HitDef" in preview
    dlg.close()


def test_move_wizard_presets_change(qapp):
    ctx = AppContext()
    dlg = MoveWizardDialog(ctx)

    # Troca para Super / Hiper
    idx = dlg.combo_kind.findData("super")
    assert idx >= 0
    dlg.combo_kind.setCurrentIndex(idx)

    data = dlg.collect_move_data()
    assert data["kind"] == "super"
    assert data["power"] == 3000
    assert data["stateno"] == 3000
    assert data["fall"] is True
    assert data["superpause"] is not None

    preview = dlg.preview_edit.toPlainText()
    assert "[Statedef 3000]" in preview
    assert "poweradd = -3000" in preview
    assert "type = SuperPause" in preview
    dlg.close()


def test_move_wizard_with_character(qapp, tmp_path, mugen_dir):
    import shutil
    src_dir = mugen_dir / "chars" / "kfm"

    dest_dir = tmp_path / "char"
    shutil.copytree(src_dir, dest_dir, ignore=shutil.ignore_patterns("*.sff", "*.snd", "_backups", "review"))
    char = project.load(dest_dir / "kfm.def")

    ctx = AppContext()
    ctx.char = char

    dlg = MoveWizardDialog(ctx, initial_anim=200)
    # Deve detectar a animação 200 e selecionar
    assert dlg.combo_anim.currentData() == 200
    # o quadro de acerto é o primeiro com Clsn1 na anim 200
    hit = next(i for i, f in enumerate(char.anims.get(200).frames, 1) if f.clsn1)
    assert dlg.spin_hit_elem.value() == hit

    # Altera nome e dano
    dlg.edit_name.setText("Chicote Super Rápido")
    dlg.spin_dmg_hit.setValue(120)

    data = dlg.collect_move_data()
    assert data["name"] == "Chicote Super Rápido"
    assert data["damage"][0] == 120

    preview = dlg.preview_edit.toPlainText()
    assert "; Chicote Super Rápido" in preview
    assert "damage = 120" in preview

    dlg.close()
