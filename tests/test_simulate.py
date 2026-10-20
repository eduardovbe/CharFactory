import pytest

from charfactory.tools import simexpr


class Ctx:
    """Contexto mínimo para o avaliador: gatilhos fixos e um 'inimigo' redirecionável."""

    def __init__(self, **trig):
        self.t = {"time": 10, "facing": 1, "pos x": 5, "var": {30: 7}, **trig}
        self.enemy = None

    def trigger(self, name, arg):
        if name == "var":
            return self.t["var"].get(arg, 0)
        if name == "random":
            return 500
        if name not in self.t:
            raise simexpr.Unsupported(name)
        return self.t[name]

    def redirect(self, kind, arg):
        return self.enemy

    def const(self, key):
        return {"velocity.run.fwd.x": 4.6}.get(key, 0)

    def command(self, name):
        return name == "x"

    def animelem_start(self, n):
        return n == 3

    def animelem_time(self, n):
        return {3: 0, 2: 4}.get(n)


def ev(text, ctx=None):
    return simexpr.evaluate(text, ctx or Ctx())


def test_arithmetic_and_mugen_rules():
    assert ev("7/2") == 3 and ev("-7/2") == -3          # divisão inteira trunca para zero
    assert ev("7.0/2") == 3.5
    assert ev("time % 4") == 2
    assert ev("2 ** 3 ** 2") == 512                      # ** à direita
    assert ev("1 + 2 * 3 = 7") == 1
    assert ev("!0 && 1 || 0") == 1
    assert ev("10 / 0") == 0                             # MUGEN: divisão por zero dá 0


def test_triggers_forms():
    assert ev("time = [5, 10]") == 1 and ev("time = [5, 10)") == 0
    assert ev("time != (0, 3]") == 1
    assert ev("ifelse(facing = 1, 24, -24)") == 24
    assert ev("animelem = 3") == 1 and ev("animelem = 2") == 0
    assert ev("animelem = 2, >= 4") == 1
    assert ev("timemod = 4, 2") == 1
    assert ev('command = "x"') == 1 and ev('command != "y"') == 1
    assert ev("const(velocity.run.fwd.x)") == 4.6
    assert ev("var(30) >= 7") == 1
    assert abs(ev("p2dist x - 30 * cos(time * .314)", Ctx(**{"p2dist x": 40})) - (40 - 30 * __import__("math").cos(3.14))) < 1e-9


def test_redirect_and_unsupported():
    c = Ctx()
    c.enemy = Ctx(**{"pos x": 80})
    assert ev("(enemynear, pos x) + ifelse(facing = 1, 24, -24)", c) == 104
    assert ev("enemynear, pos x > 50", c) == 1
    with pytest.raises(simexpr.Unsupported):
        ev("gatilhoinexistente", c)


def test_simulate_kfm_punch_hits_dummy(mugen_dir):
    from charfactory.project import Character
    from charfactory.tools import simulate as SIM
    c = Character(mugen_dir / "chars" / "kfm" / "kfm.def")
    sim = SIM.simulate(c, 200, p2_dist=30)
    rep = SIM.summary(sim)
    assert rep["hits"] >= 1
    assert sim.p1.stateno == 0                            # o golpe termina e volta ao parado
    img = SIM.sheet(sim, columns=4)
    assert img.width > 100 and img.height > 100


def test_simulate_hit_pushes_away_from_attacker(mugen_dir):
    """Velocidade do acerto no mundo = -ground.velocity.x * facing do atacante (Ikemen)."""
    from charfactory.project import Character
    from charfactory.tools import simulate as SIM
    c = Character(mugen_dir / "chars" / "kfm" / "kfm.def")
    sim = SIM.simulate(c, 210, p2_dist=34)
    hit = [e for e in sim.events if e["kind"] == "acerto"]
    assert hit and hit[0]["value"][0] >= 0                # KFM empurra para trás: vx no mundo >= 0


def test_effect_scale_helper_of_game_chars(tmp_path):
    """Muitos chars escalam efeitos por `Helper(999999),Fvar(16)` (helper criado no começo da
    luta). A vírgula do redirecionamento não separa argumentos e o helper existe para leitura."""
    from charfactory import project
    from charfactory.tools import generate, ops, simulate
    from charfactory.tools.simulate import _split_args
    assert _split_args(".2*Helper(999999),Fvar(16),.3*Helper(999999),Fvar(16)") == \
        [".2*Helper(999999),Fvar(16)", ".3*Helper(999999),Fvar(16)"]
    assert _split_args("p2dist x, -30 + random % 10") == ["p2dist x", "-30 + random % 10"]
    c = project.load(generate.new_character(tmp_path / "src", "J"))
    ops.set_action_text(c, "[Begin Action 1000]\n0,0, 0,0, 20\n")
    ops.set_statedef_text(c, 999999, """[Statedef 999999]
type = S
physics = N
anim = 1000

[State 0, escala]
type = VarSet
trigger1 = 1
fv = 16
value = 1.05
""")
    ops.set_statedef_text(c, 1000, """[Statedef 1000]
type = S
movetype = A
physics = S
anim = 1000
ctrl = 0

[State 1000, fx]
type = Explod
trigger1 = time = 1
anim = 1000
pos = 2*Helper(999999),Fvar(16), -20*Helper(999999),Fvar(16)
scale = .2*Helper(999999),Fvar(16), .2*Helper(999999),Fvar(16)
removetime = 5

[State 1000, fim]
type = ChangeState
trigger1 = time >= 10
value = 0
ctrl = 1
""")
    sim = simulate.simulate(c, 1000, p2_dist=50, max_ticks=40)
    e = sim.explods[0]
    assert abs(e.scale[0] - 0.21) < 1e-6 and abs(e.pos[1] + 21) < 1e-6
    assert not [h for h in sim.helpers if h.helper_id == 999999]     # só leitura


def test_p2stateno_trigger_follows_the_custom_state(tmp_path):
    """HitDef com p2stateno põe o oponente num estado do atacante: o gatilho p2stateno e o
    redirecionamento enemynear, stateno o veem (com `p2stateno = 3090` o simulador
    devolvia 5000). TargetState também o move."""
    from charfactory import project
    from charfactory.tools import generate, ops, simulate
    c = project.load(generate.new_character(tmp_path / "src", "S"))
    ops.set_action_text(c, "[Begin Action 9970]\nClsn1Default: 1\n Clsn1[0] = 0,-60,40,0\n0,0, 0,0, 40\n")
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, prende]\ntype = HitDef\ntrigger1 = time = 2\ndamage = 30\npausetime = 4, 4\n"
        "p2stateno = 9990\np2getp1state = 1\nsparkno = -1\n\n"
        "[State 9970, viu]\ntype = ChangeState\ntrigger1 = p2stateno = 9990\n"
        "trigger1 = enemynear, stateno = 9990\nvalue = 9971\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 30\nvalue = 0\nctrl = 1\n"))
    ops.set_statedef_text(c, 9971, (
        "[Statedef 9971]\ntype = S\nmovetype = A\nphysics = S\nanim = 0\nctrl = 0\n\n"
        "[State 9971, solta]\ntype = TargetState\ntrigger1 = time = 3\nvalue = 5050\n\n"
        "[State 9971, solto]\ntype = ChangeState\ntrigger1 = (time >= 5) && (p2stateno != 9990)\n"
        "value = 9972\n"))
    ops.set_statedef_text(c, 9972, "[Statedef 9972]\ntype = S\nmovetype = I\nphysics = S\nanim = 0\n")
    sim = simulate.simulate(c, 9970, p2_dist=30, max_ticks=40)
    states = [e["value"] for e in sim.events if e["kind"] == "estado" and e["who"] == "P1"]
    assert states == [9970, 9971, 9972]     # viu o oponente no 9990 e depois solto pelo TargetState
    assert sim.p2.custom == 0


def test_p2_guard_follows_the_guardflag(mugen_dir):
    """O P2 que defende segura o que o guardflag cobre na postura dele: o soco baixo do kfm
    (guardflag = L) passa pela defesa em pé e para na agachada."""
    from charfactory.project import Character
    from charfactory.tools import simulate as SIM
    c = Character(mugen_dir / "chars" / "kfm" / "kfm.def")
    kinds = lambda sim: [e["kind"] for e in sim.events if e["kind"] in ("acerto", "defesa")]
    assert kinds(SIM.simulate(c, 400, p2_guard=True)) == ["acerto"]
    sim = SIM.simulate(c, 400, p2_pose="crouch", p2_guard=True)
    assert kinds(sim) == ["defesa"] and sim.p2.state == "stand"
    guard = next(e for e in sim.events if e["kind"] == "defesa")
    assert guard["pause"] == [10, 11] and guard["snd"] == [6, 0, True]   # guardsound do motor
    assert kinds(SIM.simulate(c, 200, p2_guard=True)) == ["defesa"]
    # o soco alto do kfm passa por cima do agachado (Clsn2 da anim 11 do próprio char)
    assert kinds(SIM.simulate(c, 200, p2_pose="crouch")) == []
    assert kinds(SIM.simulate(c, 200, p2_pose="air")) == ["acerto"]


def test_frame_data_matches_the_air_and_counts_advantage(mugen_dir):
    from charfactory.project import Character
    from charfactory.tools import simulate as SIM
    c = Character(mugen_dir / "chars" / "kfm" / "kfm.def")
    fd = SIM.frame_data(SIM.simulate(c, 200, p2_dist=200))      # longe: só a anim conta
    assert (fd["startup"], fd["active"], fd["recovery"], fd["outcome"]) == (3, 4, 5, "errou")
    assert fd["phases"][:3] == ["preparo"] * 3 and fd["phases"][3] == "ativo"
    fd = SIM.frame_data(SIM.simulate(c, 1000, p2_guard=True))
    assert fd["outcome"] == "defesa" and fd["advantage"] == -9 and not fd["knockdown"]
    assert "pausa" in fd["phases"]                             # a pausa do acerto fica fora da conta
    fd = SIM.frame_data(SIM.simulate(c, 1000))
    assert fd["outcome"] == "acerto" and fd["knockdown"] and fd["advantage"] is None
    # CtrlSet no meio dos quadros ativos: livre antes do fim da Clsn1, sem recuperação
    fd = SIM.frame_data(SIM.simulate(c, 400, p2_dist=200))
    assert (fd["startup"], fd["active"], fd["recovery"], fd["total"]) == (3, 6, 0, 6)


def test_sound_track_mixes_the_move_sounds(mugen_dir):
    import io
    import wave
    from charfactory.project import Character
    from charfactory.tools import simulate as SIM
    c = Character(mugen_dir / "chars" / "kfm" / "kfm.def")
    sim = SIM.simulate(c, 200)
    cues = SIM.sound_cues(sim)
    assert [(q["t"], q["group"], q["number"], q["common"]) for q in cues] == [(1, 0, 0, False), (3, 5, 0, True)]
    with wave.open(io.BytesIO(SIM.sound_track(sim))) as w:
        full = w.getnframes() / w.getframerate()
    with wave.open(io.BytesIO(SIM.sound_track(sim, start_tick=3))) as w:
        later = w.getnframes() / w.getframerate()
    assert full > 0.1 and abs((full - later) - 3 / 60) < 0.002    # a trilha começa no tick 0: 3/60 s a menos
    assert SIM.sound_track(sim, start_tick=10_000) is None        # depois de tudo: silêncio


def test_chain_cancels_links_and_finds_where_the_combo_breaks(mugen_dir):
    """Cadeia de golpes: o soco baixo do kfm (400) liga nele mesmo (CtrlSet no tick 6) e cancela
    no 410 (ChangeState com movecontact no 400); soco fraco em pé -> forte não é combo (+1 no
    acerto e 8 de preparo: o P2 se recupera antes)."""
    from charfactory.project import Character
    from charfactory.tools import simulate as SIM
    c = Character(mugen_dir / "chars" / "kfm" / "kfm.def")
    r = SIM.chain_report(SIM.simulate(c, 400, p2_dist=40, chain=(400, 400, 410)))
    assert r["complete"] and [s["how"] for s in r["steps"]] == ["liga", "cancela"]
    assert r["combo"] == 2 and r["broke"] is None
    r = SIM.chain_report(SIM.simulate(c, 200, p2_dist=40, chain=(200, 210)))
    assert r["complete"] and r["hits"] == 2 and r["combo"] == 1 and r["broke"]["state"] == 210
    fd = SIM.frame_data(SIM.simulate(c, 400, p2_dist=40, chain=(400, 400, 410)))
    assert fd["phases"].count("livre") < 10                  # livre só depois do último golpe
