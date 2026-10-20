"""Testes para o gerador de golpes (charfactory.tools.movegen)."""
from __future__ import annotations

import pytest
from charfactory import project
from charfactory.tools import movegen


def test_kind_defaults():
    norm = movegen.get_kind_defaults("normal")
    assert norm["damage"] == (50, 6)
    assert norm["guardflag"] == "MA"
    assert norm["fall"] is False

    sup = movegen.get_kind_defaults("super")
    assert sup["power"] == 3000
    assert sup["fall"] is True
    assert sup["superpause"] is not None


def test_build_normal_move_cns():
    data = {
        "stateno": 210,
        "name": "Chute Forte",
        "kind": "normal",
        "anim": 210,
        "hit_elem": 3,
        "damage": (60, 8),
        "ground_velocity": (-4.0, 0.0),
        "fall": False,
    }
    cns = movegen.build_move_cns(None, data)
    assert "[Statedef 210]" in cns
    assert "; Chute Forte" in cns
    assert "type = S" in cns
    assert "anim = 210" in cns
    assert "type = HitDef" in cns
    assert "trigger1 = animelem = 3" in cns
    assert "damage = 60, 8" in cns
    assert "ground.velocity = -4.0, 0.0" in cns
    assert "fall = 0" in cns
    assert "type = ChangeState" in cns


def test_build_super_move_cns():
    data = {
        "stateno": 3000,
        "name": "Super Explosão",
        "kind": "super",
        "anim": 3000,
        "power": 3000,
        "damage": (300, 50),
        "fall": True,
        "fall_recover": False,
        "voice": "S0, 1",
    }
    cns = movegen.build_move_cns(None, data)
    assert "[Statedef 3000]" in cns
    assert "poweradd = -3000" in cns
    assert "type = SuperPause" in cns
    assert "time = 30" in cns
    assert "value = S0, 1" in cns
    assert "fall = 1" in cns
    assert "fall.recover = 0" in cns


def test_build_projectile_move_cns():
    data = {
        "stateno": 1000,
        "name": "Magia",
        "kind": "projectile",
        "anim": 1000,
        "proj_anim": 1005,
        "proj_vel": (6.0, 0.0),
        "damage": (80, 10),
    }
    cns = movegen.build_move_cns(None, data)
    assert "[Statedef 1000]" in cns
    assert "type = Projectile" in cns
    assert "ProjAnim = 1005" in cns
    assert "velocity = 6.0, 0.0" in cns
    assert "damage = 80, 10" in cns


def test_build_command_cns():
    data = {
        "stateno": 1000,
        "name": "Magia",
        "kind": "special",
        "command_name": "qcf_a",
        "command_seq": "~D, DF, F, a",
        "command_time": 15,
        "power": 1000,
        "combo_cancel": True,
    }
    cmd_info, minus1 = movegen.build_command_cns(data)
    assert cmd_info["name"] == "qcf_a"
    assert cmd_info["command"] == "~D, DF, F, a"
    assert cmd_info["time"] == 15

    assert "[State -1, Magia]" in minus1
    assert "value = 1000" in minus1
    assert "triggerall = (roundstate = 2)" in minus1
    assert "triggerall = (statetype != A)" in minus1
    assert "power >= 1000" in minus1
    assert 'trigger1 = command = "qcf_a"' in minus1


def test_apply_move_to_character(tmp_path, mugen_dir):
    import shutil
    from pathlib import Path
    src_dir = mugen_dir / "chars" / "kfm"

    dest_dir = tmp_path / "char"
    shutil.copytree(src_dir, dest_dir, ignore=shutil.ignore_patterns("*.sff", "*.snd", "_backups", "review"))
    def_file = dest_dir / "kfm.def"
    char = project.load(def_file)

    data = {
        "stateno": 2500,
        "name": "Golpe Teste",
        "kind": "special",
        "anim": 200,
        "damage": (95, 12),
        "command_name": "teste_special",
        "command_seq": "~D, DF, F, b",
        "command_time": 15,
        "add_command": True,
    }

    res = movegen.apply_move_to_character(char, data)
    assert res["stateno"] == 2500
    assert 2500 in char.all_statedefs(include_common=False)
    assert char.commands.find("teste_special")
    assert any(c.section.get("value") == "2500" for c in char.commands.states.statedefs[-1].controllers)
