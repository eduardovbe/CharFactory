from charfactory import cli


def test_sim_command(mugen_dir, tmp_path, capsys):
    kfm = str(mugen_dir / "chars" / "kfm" / "kfm.def")
    assert cli.main(["sim", kfm, "200", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "=== 200" in out and "HIT" in out
    assert (tmp_path / "move_200.png").exists()


def test_move_states_picks_command_states(mugen_dir):
    from charfactory.project import Character
    from charfactory.tools import simulate
    c = Character(mugen_dir / "chars" / "kfm" / "kfm.def")
    states = simulate.move_states(c)
    assert states and 500 not in states and all(200 <= s < 4000 for s in states)


def test_view_command(mugen_dir, tmp_path):
    kfm = str(mugen_dir / "chars" / "kfm" / "kfm.def")
    png = tmp_path / "s.png"
    assert cli.main(["view", kfm, "0", "0", "--out", str(png)]) == 0 and png.exists()
