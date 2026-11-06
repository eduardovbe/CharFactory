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
    # o estado do P2 precisa existir: o motor não troca para um Statedef que não há
    ops.set_statedef_text(c, 9990, "[Statedef 9990]\ntype = S\nmovetype = H\nphysics = N\nanim = 0\n")
    sim = simulate.simulate(c, 9970, p2_dist=30, max_ticks=40)
    states = [e["value"] for e in sim.events if e["kind"] == "estado" and e["who"] == "P1"]
    assert states == [9970, 9971, 9972]     # viu o oponente no 9990 e depois solto pelo TargetState
    assert sim.p2.custom == 0


def test_finite_changestate_loop_on_the_same_tick(tmp_path):
    """Helper que volta para o próprio estado até sysvar(3) chegar a 5 e então se destrói, tudo
    no mesmo tick (issue #6): o VarAdd ignorava sysvar, o laço nunca acabava e o helper soltava
    explods até o fim da simulação."""
    from charfactory import project
    from charfactory.tools import generate, ops, simulate
    c = project.load(generate.new_character(tmp_path / "src", "S"))
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 0\nctrl = 0\n\n"
        "[State 9970, fx]\ntype = Helper\ntrigger1 = time = 2\nstateno = 9971\nID = 9971\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 10\nvalue = 0\nctrl = 1\n"))
    ops.set_statedef_text(c, 9971, (
        "[Statedef 9971]\ntype = A\nmovetype = I\nphysics = N\nanim = 0\n\n"
        "[State 9971, fx]\ntype = Explod\ntrigger1 = !time\nanim = 0\nremovetime = 60\n\n"
        "[State 9971, conta]\ntype = VarAdd\ntrigger1 = !time\nsysvar(3) = 1\n\n"
        "[State 9971, de novo]\ntype = ChangeState\ntrigger1 = sysvar(3) < 5\nvalue = stateno\n\n"
        "[State 9971, fim]\ntype = DestroySelf\ntrigger1 = 1\n"))
    sim = simulate.simulate(c, 9970, max_ticks=300)
    loops = [e for e in sim.events if e["kind"] == "estado" and e["who"] != "P1"]
    assert len(loops) == 5 and len({e["t"] for e in loops}) == 1
    assert not any(h.alive for h in sim.helpers)
    assert sum(e["kind"] == "explod" for e in sim.events) == 5
    assert sim.tick < 300 and not any("loop" in k for k in sim.unsupported)


def test_exit_conditions_the_simulator_used_to_miss(tmp_path):
    """Mesma família da issue #6: quando o gatilho ou controlador da saída não era simulado, o
    helper não se destruía e o golpe não acabava. Laço finito longo (andar 1 px por volta até o
    chão), pi, ParentVarAdd, VarRangeSet e o ReversalDef que volta ao próprio estado."""
    from charfactory import project
    from charfactory.tools import generate, ops, simulate
    c = project.load(generate.new_character(tmp_path / "src", "S"))
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = N\nanim = 0\nctrl = 0\n\n"
        "[State 9970, fx]\ntype = Helper\ntrigger1 = !time\nstateno = 9971\nID = 9971\n\n"
        "[State 9970, zera]\ntype = VarRangeSet\ntrigger1 = !time\nvalue = 3\nlast = 5\n\n"
        "[State 9970, cai]\ntype = PosSet\ntrigger1 = time = 2\ny = -40\n\n"
        "[State 9970, desce]\ntype = ChangeState\ntrigger1 = time = 3 && pos y < 0\nvalue = 9972\n"))
    ops.set_statedef_text(c, 9971, (
        "[Statedef 9971]\ntype = A\nmovetype = I\nphysics = N\nanim = 0\n\n"
        "[State 9971, conta]\ntype = ParentVarAdd\ntrigger1 = 1\nvar(7) = 1\n\n"
        "[State 9971, fim]\ntype = DestroySelf\ntrigger1 = time >= floor(pi)\n"))
    ops.set_statedef_text(c, 9972, (
        "[Statedef 9972]\ntype = A\nmovetype = A\nphysics = N\nanim = 0\n\n"
        "[State 9972, passo]\ntype = PosAdd\ntrigger1 = 1\ny = 1\n\n"
        "[State 9972, volta]\ntype = ChangeState\ntrigger1 = pos y < 0\nvalue = 9972\n\n"
        "[State 9972, chao]\ntype = ChangeState\ntrigger1 = 1\nvalue = 9973\n"))
    ops.set_statedef_text(c, 9973, (
        "[Statedef 9973]\ntype = S\nmovetype = I\nphysics = S\nanim = 0\n\n"
        "[State 9973, contra]\ntype = ReversalDef\ntrigger1 = 1\nreversal.attr = SCA, AA\n"
        "p1stateno = 9973\n\n"
        "[State 9973, fim]\ntype = ChangeState\ntrigger1 = time >= 20\nvalue = 0\nctrl = 1\n"))
    # o 0 do próprio char: sem o MUGEN (como no CI) não há common1.cns de onde ele viria
    ops.set_statedef_text(c, 0, "[Statedef 0]\ntype = S\nmovetype = I\nphysics = S\nanim = 0\nctrl = 1\n")
    sim = simulate.simulate(c, 9970, max_ticks=200)
    p1 = [e["value"] for e in sim.events if e["kind"] == "estado" and e["who"] == "P1"]
    assert p1.count(9972) == 40 and p1[-1] == 0                 # 40 px, uma volta por px
    assert sim.p1.vars[3] == sim.p1.vars[5] == 3 and sim.p1.vars[7] == 4
    assert not any(h.alive for h in sim.helpers)
    assert p1.count(9973) <= 2 and sim.tick < 200 and not sim.unsupported


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
    # sem guard.hittime o bloqueio dura o ground.slidetime (12), não o ground.hittime (17): é
    # assim no MUGEN e o Ikemen repete para chars do MUGEN
    assert fd["outcome"] == "defesa" and fd["advantage"] == -12 and not fd["knockdown"]
    assert "pausa" in fd["phases"]                             # a pausa do acerto fica fora da conta
    fd = SIM.frame_data(SIM.simulate(c, 1000))
    assert fd["outcome"] == "acerto" and fd["knockdown"] and fd["advantage"] is None
    # CtrlSet no meio dos quadros ativos: livre antes do fim da Clsn1, sem recuperação
    fd = SIM.frame_data(SIM.simulate(c, 400, p2_dist=200))
    assert (fd["startup"], fd["active"], fd["recovery"], fd["total"]) == (3, 6, 0, 6)


def test_guard_ctrltime_and_cancel_window(mugen_dir, tmp_path):
    """Na defesa o P2 volta a agir no guard.ctrltime, antes do fim do bloqueio (guard.hittime):
    a vantagem conta o controle e o bloqueio vem à parte. O cancelamento só vale no acerto ou
    na defesa conforme o gatilho (movehit, moveguarded, movecontact) (issue #4)."""
    import shutil
    from charfactory import project
    from charfactory.tools import ops, simulate as SIM
    src = tmp_path / "kfm"
    shutil.copytree(mugen_dir / "chars" / "kfm", src)
    c = project.load(src / "kfm.def", mugen_dir)
    base = ("[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 200\nctrl = 0\n\n"
            "[State 9970, hd]\ntype = HitDef\ntrigger1 = !time\nattr = S, NA\nguardflag = MA\n"
            "damage = 20\npausetime = 4, 4\nground.hittime = 12\nguard.hittime = 12\n{extra}\n"
            "[State 9970, hit]\ntype = ChangeState\ntrigger1 = movehit && command = \"a\"\nvalue = 210\n\n"
            "[State 9970, block]\ntype = ChangeState\ntrigger1 = moveguarded && command = \"a\"\nvalue = 230\n\n"
            "[State 9970, fim]\ntype = ChangeState\ntrigger1 = animtime = 0\nvalue = 0\nctrl = 1\n")
    ops.set_statedef_text(c, 9970, base.format(extra=""))
    plain = SIM.frame_data(SIM.simulate(c, 9970, p2_dist=30, p2_guard=True))
    ops.set_statedef_text(c, 9970, base.format(extra="guard.ctrltime = 5\n"))
    fd = SIM.frame_data(SIM.simulate(c, 9970, p2_dist=30, p2_guard=True))
    assert plain["outcome"] == fd["outcome"] == "defesa" and "advantage_stun" not in plain
    assert fd["advantage"] == plain["advantage"] - 7 and fd["advantage_stun"] == plain["advantage"]
    assert fd["cancel"]["into"] == [230]
    hit = SIM.frame_data(SIM.simulate(c, 9970, p2_dist=30))
    assert hit["outcome"] == "acerto" and hit["cancel"]["into"] == [210]
    assert hit["cancel"]["window"] > 0 and hit["cancel"]["advantage"] > hit["advantage"]
    assert SIM.cancel_targets(c, 9970) == {"acerto": {210}, "defesa": {230}}
    # movehit || moveguarded cancela nos dois; !movehit não conta como cancelamento
    ops.set_statedef_text(c, 9970, base.replace('= movehit && command = "a"', '= movehit || moveguarded')
                          .replace('= moveguarded && command = "a"', '= !movehit && command = "a"').format(extra=""))
    assert SIM.cancel_targets(c, 9970) == {"acerto": {210}, "defesa": {210}}


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
    assert r["hits"] == 3 and r["combo"] == 3 and r["broke"] is None
    r = SIM.chain_report(SIM.simulate(c, 200, p2_dist=40, chain=(200, 210)))
    assert r["complete"] and r["hits"] == 2 and r["combo"] == 1 and r["broke"]["state"] == 210
    fd = SIM.frame_data(SIM.simulate(c, 400, p2_dist=40, chain=(400, 400, 410)))
    assert fd["phases"].count("livre") < 10                  # livre só depois do último golpe


def _hit_char(tmp_path):
    from charfactory import project
    from charfactory.tools import generate, ops
    c = project.load(generate.new_character(tmp_path / "src", "S"))
    ops.set_action_text(c, "[Begin Action 9970]\nClsn1Default: 1\n Clsn1[0] = 0,-60,40,0\n0,0, 0,0, -1\n")
    return c


def test_p2_hitstun_lasts_like_ikemen(tmp_path):
    """Issue #11 (SAIROU KYUSHUU): dois acertos 30 ticks um do outro com pausetime 10,12 e
    ground.hittime 18 são combo no MUGEN/Ikemen. O P2 só volta ao parado depois de tremor +
    hittime + 2 ticks (char MUGEN ganha 1 no hittime e o HitOver pede hittime < 0); o boneco
    voltava 3 ticks antes e o 2º acerto pegava o P2 já livre."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    sd = ("[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
          "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\ntrigger2 = time = {gap}\n"
          "attr = S, NA\ndamage = 10\npausetime = 10, 12\nground.hittime = 18\nsparkno = -1\n\n"
          "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 60\nvalue = 0\nctrl = 1\n")
    for gap, combo in ((20, 2), (21, 2), (22, 1)):
        ops.set_statedef_text(c, 9970, sd.format(gap=gap))
        sim = simulate.simulate(c, 9970, p2_dist=30)
        hits = [e for e in sim.events if e["kind"] == "acerto"]
        assert len(hits) == 2 and hits[1]["t"] - hits[0]["t"] == gap + 10
        assert hits[1]["combo"] == combo and simulate.chain_report(sim)["combo"] == combo


def test_helper_rehits_and_counts_like_ikemen(tmp_path):
    """Issue #11 (Shin Hadouken): o projétil-helper nasce na SuperPause, volta ao próprio estado
    a cada MoveContact (na pausa do acerto) e conta os acertos num VarAdd com !Time. O Time = 0
    do estado novo roda no 1º tick fora da pausa (o simulador pulava e o projétil acertava sem
    parar) e o helper nascido parado na pausa não roda o Time = 0 duas vezes."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 0\nctrl = 0\n\n"
        "[State 9970, sp]\ntype = SuperPause\ntrigger1 = time = 0\ntime = 20\nmovetime = 20\n\n"
        "[State 9970, proj]\ntype = Helper\ntrigger1 = time = 2\nstateno = 9971\nID = 9971\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 90\nvalue = 0\nctrl = 1\n"))
    ops.set_statedef_text(c, 9971, (
        "[Statedef 9971]\ntype = S\nmovetype = A\nphysics = N\nanim = 9970\nctrl = 0\n\n"
        "[State 9971, conta]\ntype = VarAdd\ntrigger1 = !time\nvar(2) = 1\n\n"
        "[State 9971, hd]\ntype = HitDef\ntrigger1 = 1\nattr = S, SP\ndamage = 10\n"
        "pausetime = 4, 8\nground.hittime = 16\nsparkno = -1\n\n"
        "[State 9971, de novo]\ntype = ChangeState\ntrigger1 = movecontact = 1\n"
        "value = ifelse(var(2) < 5, 9971, 9972)\nignorehitpause = 1\n"))
    ops.set_statedef_text(c, 9972, "[Statedef 9972]\ntype = S\nmovetype = I\nphysics = N\nanim = 0\n\n"
                          "[State 9972, fim]\ntype = DestroySelf\ntrigger1 = 1\n")
    sim = simulate.simulate(c, 9970, p2_dist=30, max_ticks=200)
    hits = [e for e in sim.events if e["kind"] == "acerto"]
    assert [e["combo"] for e in hits] == [1, 2, 3, 4, 5]


def test_hitdef_numhits_hitflag_and_chainid(tmp_path):
    """numhits soma no combo; hitflag "-" não pega quem já está apanhando (o HitDef fica ativo
    e pega o P2 no tick em que ele volta ao parado: combo novo) e "+" só pega quem está;
    chainid só pega o P2 que apanhou por último do HitDef com aquele id."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    sd = ("[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
          "[State 9970, a]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\nid = 7\nnumhits = 3\n"
          "pausetime = 2, 2\nground.hittime = 20\nsparkno = -1\n\n"
          "[State 9970, b]\ntype = HitDef\ntrigger1 = time = 6\nattr = S, NA\n{extra}\n"
          "pausetime = 2, 2\nground.hittime = 20\nsparkno = -1\n\n"
          "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 40\nvalue = 0\nctrl = 1\n")
    combos = {}
    for extra in ("hitflag = MAF", "hitflag = MAF-", "hitflag = MAF+", "chainid = 7", "chainid = 8"):
        ops.set_statedef_text(c, 9970, sd.format(extra=extra))
        sim = simulate.simulate(c, 9970, p2_dist=30)
        combos[extra] = [e["combo"] for e in sim.events if e["kind"] == "acerto"]
    assert combos == {"hitflag = MAF": [3, 4], "hitflag = MAF-": [3, 1], "hitflag = MAF+": [3, 4],
                      "chainid = 7": [3, 4], "chainid = 8": [3]}


def _p2_path(sim):
    """[(estado, sub, tick em que entrou)] do boneco."""
    out = []
    for r in sim.track:
        k = (r["p2"]["state"], r["p2"].get("sub"))
        if not out or out[-1][:2] != k:
            out.append((*k, r["t"]))
    return out


def test_fall_bounce_and_liedown_like_common1(tmp_path):
    """Queda como o common1 do Ikemen: treme, voa com a gravidade do HitDef (yaccel), bate no
    chão (5100), quica com fall.yvelocity (5101), fica deitado liedown.time ticks (5110) e
    levanta (5120) já sem poder apanhar. Antes caía com a gravidade do char e levantava 60
    ticks depois de tocar o chão, sem quique."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\npausetime = 0, 4\n"
        "ground.velocity = -2, -6\nyaccel = .5\nfall = 1\nsparkno = -1\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 5\nvalue = 0\nctrl = 1\n"))
    sim = simulate.Sim(c, 9970, p2_dist=30, max_ticks=200).run(until_idle=False)
    path = _p2_path(sim)
    subs = [s for _, s, _ in path]
    assert subs[:6] == ["shake", "fly", "falling", "bounce", "bounce2", "lie"]
    at = {s: t for _, s, t in path}
    # tremor nos ticks 1-4, HitShakeOver no 5 (5030); no HitOver (air.hittime 20, +1 de char
    # MUGEN) vai a 5050 ainda no ar, e bate no chão 25 px abaixo dele
    assert at["fly"] == 5 and at["falling"] == 5 + 21
    land = next(r for r in sim.track if r["t"] == at["bounce"] - 1)["p2"]["pos"][1]
    assert land >= 25 - 0.5 * 21 and at["bounce"] > at["falling"]
    # deitado liedown.time (60) ticks antes de levantar
    lie_to_getup = next(t for _, s, t in path if s == "getup") - at["lie"]
    assert lie_to_getup == 60
    assert simulate.frame_data(sim)["knockdown"]


def test_air_hit_without_fall_recovers_in_the_air(tmp_path):
    """Acerto no ar sem fall: 5030 -> 5040, o P2 volta a ter ctrl no HitOver ainda no ar
    (o combo acaba ali) e pousa; antes caía e ficava deitado 60 ticks."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\npausetime = 0, 4\n"
        "air.velocity = -2, -2\nair.hittime = 10\nsparkno = -1\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 5\nvalue = 0\nctrl = 1\n"))
    sim = simulate.simulate(c, 9970, p2_dist=30, p2_pose="air")
    path = _p2_path(sim)
    assert [s for st, s, _ in path if st != "stand"] == ["shake", "fly", "recover"]
    assert not any(st == "down" for st, _, _ in path)
    ctrl_at = next(r["t"] for r in sim.track if r["t"] > 0 and r["p2"]["ctrl"])
    # tremor 4 + air.hittime 10 (+1 de char MUGEN) + 1 do HitOver
    assert ctrl_at == 4 + 11 + 1
    assert not simulate.frame_data(sim)["knockdown"]


def test_juggle_points_limit_air_hits(tmp_path):
    """Juggle: cada golpe que pega o P2 caindo gasta o `juggle` do Statedef dos 15 pontos do
    [Data] airjuggle; sem pontos o golpe não pega (antes o simulador não tinha juggle)."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\njuggle = 6\n\n"
        "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\npausetime = 0, 0\n"
        "ground.velocity = 0, -4\nair.velocity = 0, -4\nfall = 1\nyaccel = .1\nsparkno = -1\n\n"
        "[State 9970, de novo]\ntype = ChangeState\ntrigger1 = time = 6\nvalue = 9970\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 6\nvalue = 0\nctrl = 1\n"))
    sim = simulate.simulate(c, 9970, p2_dist=30, max_ticks=60)
    hits = [e for e in sim.events if e["kind"] == "acerto"]
    assert [e["juggle"] for e in hits] == [9, 3]           # 15 - 6 - 6; o 3º (6 > 3) não pega
    assert any("juggle" in n for n in sim.unsupported)


def test_p2bodydist_uses_the_size_boxes(tmp_path):
    """P2BodyDist X = distância - frente do P1 - frente do P2 ([Size] ground.front), como o
    Ikemen; antes tirava 14 px fixos."""
    from charfactory.tools import simulate
    c = _hit_char(tmp_path)
    sim = simulate.Sim(c, 0, p2_dist=100)
    front = sim.const("size.ground.front")
    assert sim.p1.trigger("p2bodydist x", None) == 100 - 2 * front


def test_projectile_hits_misstime_and_hit_anim(tmp_path):
    """Projectile: anda com a velocidade, acerta projhits vezes com projmisstime entre os
    acertos, congela no próprio pausetime e some pela projhitanim; o dono não ganha MoveHit
    mas vê ProjHitTime. Antes o controlador era ignorado."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_action_text(c, "[Begin Action 9971]\nClsn1Default: 1\n Clsn1[0] = -10,-50,10,-30\n0,0, 0,0, -1\n")
    ops.set_action_text(c, "[Begin Action 9972]\n0,0, 0,0, 4\n")
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 0\nctrl = 0\n\n"
        "[State 9970, bola]\ntype = Projectile\ntrigger1 = time = 0\nprojid = 5\nprojanim = 9971\n"
        "projhitanim = 9972\nvelocity = 6, 0\nprojhits = 2\nprojmisstime = 6\nattr = S, SP\n"
        "damage = 20\npausetime = 3, 8\nground.hittime = 20\nsparkno = -1\n\n"
        "[State 9970, viu]\ntype = VarSet\ntrigger1 = projhittime(5) = 0\nvar(1) = var(1) + 1\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 80\nvalue = 0\nctrl = 1\n"))
    sim = simulate.simulate(c, 9970, p2_dist=80, max_ticks=200)
    hits = [e for e in sim.events if e["kind"] == "acerto"]
    assert [e["who"] for e in hits] == ["proj 5", "proj 5"] and [e["combo"] for e in hits] == [1, 2]
    # 2º acerto: o pausetime (3) do projétil já desce no tick do contato, depois o
    # projmisstime (6) corre e ele pode acertar de novo (Projectile.tick do Ikemen)
    assert hits[1]["t"] - hits[0]["t"] == 3 + 6
    assert sim.p1.movehit == 0 and sim.p1.vars.get(1) == 2
    assert not sim.projs                                   # saiu pela projhitanim


def test_throw_runs_the_p2_custom_state(tmp_path):
    """Arremesso: p1stateno leva o P1 ao estado do arremesso, p2stateno põe o P2 num estado
    do atacante que roda de verdade (preso pelo TargetBind até o SelfState o soltar), o
    TargetLifeAdd entra no dano e o TargetState o joga (velset do Statedef) até cair."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, pega]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NT\nhitflag = M-\n"
        "p1stateno = 9971\np2stateno = 9980\nfall = 1\nsparkno = -1\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 30\nvalue = 0\nctrl = 1\n"))
    ops.set_statedef_text(c, 9971, (
        "[Statedef 9971]\ntype = S\nmovetype = A\nphysics = N\nanim = 0\nctrl = 0\n\n"
        "[State 9971, segura]\ntype = TargetBind\ntrigger1 = time < 10\npos = 20, -30\n\n"
        "[State 9971, dano]\ntype = TargetLifeAdd\ntrigger1 = time = 10\nvalue = -50\n\n"
        "[State 9971, joga]\ntype = TargetState\ntrigger1 = time = 10\nvalue = 9981\n\n"
        "[State 9971, fim]\ntype = ChangeState\ntrigger1 = time >= 20\nvalue = 0\nctrl = 1\n"))
    ops.set_statedef_text(c, 9980, (
        "[Statedef 9980]\ntype = A\nmovetype = H\nphysics = N\nvelset = 0,0\n\n"
        "[State 9980, solto]\ntype = SelfState\ntrigger1 = !gethitvar(isbound)\nvalue = 5050\n"))
    ops.set_statedef_text(c, 9981, (
        "[Statedef 9981]\ntype = A\nmovetype = H\nphysics = N\nvelset = 3, -6\n\n"
        "[State 9981, g]\ntype = VelAdd\ntrigger1 = 1\ny = .5\n\n"
        "[State 9981, ch]\ntype = SelfState\ntrigger1 = vel y > 0 && pos y >= 0\nvalue = 5100\n"))
    sim = simulate.Sim(c, 9970, p2_dist=25, max_ticks=80).run(until_idle=False)
    states = [(e["who"], e["value"]) for e in sim.events if e["kind"] == "estado"]
    assert ("P1", 9971) in states and ("P2", 9980) in states and ("P2", 9981) in states
    # preso: 20 px à frente do P1 e 30 acima
    r = next(r for r in sim.track if r["t"] == 5)
    p1 = next(e for e in r["ents"] if e["name"] == "P1")
    assert r["p2"]["pos"] == (p1["pos"][0] + 20 * p1["facing"], -30.0)
    # jogado: cai até bater no chão (5100) e o dano do TargetLifeAdd conta
    assert ("down", "bounce") in [(s, b) for s, b, _ in _p2_path(sim)]
    assert simulate.damage_report(sim)["total"] == 50


def test_targetvelset_is_local_to_the_target(tmp_path):
    """TargetVelSet x vale na velocidade do alvo (positivo = para a frente dele, como o
    Ikemen: t.vel[0] = x); o simulador virava o sinal e zerava o y que não vinha escrito."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\npausetime = 0, 0\n"
        "ground.velocity = 0, -3\nfall = 1\nsparkno = -1\n\n"
        "[State 9970, v]\ntype = TargetVelSet\ntrigger1 = time = 3\nx = -4\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 5\nvalue = 0\nctrl = 1\n"))
    # no tick 3 o P2 já voa (o 5030 começa no tick 1 e o HitVelSet dele sobrescreveria)
    sim = simulate.Sim(c, 9970, p2_dist=30, max_ticks=4).run(until_idle=False)
    d = sim.p2
    assert d.facing == -1 and d.vel[0] == 4.0      # -4 para a frente do P2 (que olha para a esquerda)
    assert d.vel[1] != 0                           # o y do acerto continua


def test_statedef_persist_flags_and_posfreeze(tmp_path):
    """movehitpersist mantém o MoveHit na troca de estado (sem ele zera); PosFreeze segura a
    posição só nos ticks em que roda."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\npausetime = 0, 0\n"
        "ground.hittime = 20\nsparkno = -1\n\n"
        "[State 9970, vai]\ntype = ChangeState\ntrigger1 = time = 2\nvalue = 9971\n"))
    for persist, want in (("1", True), ("0", False)):
        ops.set_statedef_text(c, 9971, (
            "[Statedef 9971]\ntype = S\nmovetype = A\nphysics = N\nanim = 0\nctrl = 0\n"
            f"movehitpersist = {persist}\nvelset = 2, 0\n\n"
            "[State 9971, para]\ntype = PosFreeze\ntrigger1 = time < 3\n\n"
            "[State 9971, fim]\ntype = ChangeState\ntrigger1 = time >= 6\nvalue = 0\nctrl = 1\n"))
        # a 38 px o golpe alcança e as caixas de tamanho (16 + 16) não se empurram
        sim = simulate.Sim(c, 9970, p2_dist=38, max_ticks=8).run(until_idle=False)
        assert bool(sim.p1.movehit) is want
    xs = [next(e for e in r["ents"] if e["name"] == "P1")["pos"][0] for r in sim.track]
    assert xs[2] == xs[3] == xs[4] and xs[5] == xs[4] + 2


def test_movetime_matches_the_pause_kind(tmp_path):
    """Helper com supermovetime anda na SuperPause e fica parado na Pause (pausemovetime é o
    da Pause); o simulador usava um ou outro para as duas."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    for kind, moves in (("SuperPause", True), ("Pause", False)):
        ops.set_statedef_text(c, 9970, (
            "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 0\nctrl = 0\n\n"
            "[State 9970, h]\ntype = Helper\ntrigger1 = time = 0\nstateno = 9971\nID = 1\n"
            "supermovetime = 30\n\n"
            f"[State 9970, p]\ntype = {kind}\ntrigger1 = time = 1\ntime = 20\nmovetime = 20\n\n"
            "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 30\nvalue = 0\nctrl = 1\n"))
        ops.set_statedef_text(c, 9971, "[Statedef 9971]\ntype = A\nmovetype = I\nphysics = N\n"
                              "anim = 0\nvelset = 2, 0\n")
        sim = simulate.Sim(c, 9970, p2_dist=200, max_ticks=15).run(until_idle=False)
        h = next(e for e in sim.track[12]["ents"] if e["helper"])
        h0 = next(e for e in sim.track[5]["ents"] if e["helper"])
        assert (h["pos"][0] != h0["pos"][0]) is moves


def test_postype_of_explods_and_helpers_like_ikemen(tmp_path):
    """postype como o Ikemen (Explod.setPos e helperPos): front/back saem da borda do lado em
    que o dono olha; p2 prende o Explod ao P2 (bindtime) e o Helper herda o lado do P2; a
    velocidade do Explod anda pelo lado do dono, não pelo facing do parâmetro."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 0\nctrl = 0\n\n"
        "[State 9970, frente]\ntype = Explod\ntrigger1 = time = 0\nanim = 0\npostype = front\n"
        "pos = 10, 50\nremovetime = 20\n\n"
        "[State 9970, costas]\ntype = Explod\ntrigger1 = time = 0\nanim = 0\npostype = back\n"
        "pos = 10, 50\nremovetime = 20\n\n"
        "[State 9970, no p2]\ntype = Explod\ntrigger1 = time = 0\nanim = 0\npostype = p2\n"
        "pos = 5, 0\nbindtime = -1\nremovetime = 20\n\n"
        "[State 9970, vira]\ntype = Explod\ntrigger1 = time = 0\nanim = 0\nfacing = -1\n"
        "vel = 3, 0\nremovetime = 20\n\n"
        "[State 9970, h]\ntype = Helper\ntrigger1 = time = 0\nstateno = 9971\nID = 1\npostype = p2\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 30\nvalue = 0\nctrl = 1\n"))
    ops.set_statedef_text(c, 9971, "[Statedef 9971]\ntype = S\nmovetype = I\nphysics = N\nanim = 0\n")
    sim = simulate.Sim(c, 9970, p2_dist=60, max_ticks=10).run(until_idle=False)
    front, back, on_p2, turned = sim.explods[:4]
    # o P1 olha para a direita: front na borda direita, back na esquerda
    assert front.pos[0] == sim.origin_x + 160 + 10 and back.pos[0] == sim.origin_x - 160 + 10
    assert back.facing == sim.p1.facing and front.facing == 1
    # p2: x pelo lado do P2 e preso a ele
    assert on_p2.bind_to is sim.p2 and on_p2.pos[0] == sim.p2.pos[0] + 5 * sim.p2.facing
    # facing = -1 só desenha virado: a velocidade anda para a frente do P1
    assert turned.facing == -1 and turned.pos[0] > sim.p1.pos[0]
    helper = next(h for h in sim.helpers if h.helper_id == 1)
    assert helper.facing == sim.p2.facing


def test_modifyexplod_needs_postype_in_mugen_chars(tmp_path):
    """ModifyExplod como o Ikemen: num char MUGEN pos/vel só mudam com postype (e aí o explod
    é reposicionado do zero, velocidade zerada); scale e removetime valem sempre; uma anim
    nova começa do quadro 1."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 0\nctrl = 0\n\n"
        "[State 9970, a]\ntype = Explod\ntrigger1 = time = 0\nanim = 0\nid = 1\npos = 10, 0\n"
        "vel = 2, 0\nremovetime = 50\n\n"
        "[State 9970, b]\ntype = Explod\ntrigger1 = time = 0\nanim = 0\nid = 2\npos = 10, 0\n"
        "vel = 2, 0\nremovetime = 50\n\n"
        "[State 9970, sem postype]\ntype = ModifyExplod\ntrigger1 = time = 3\nid = 1\n"
        "pos = 80, 0\nscale = 2, 2\n\n"
        "[State 9970, com postype]\ntype = ModifyExplod\ntrigger1 = time = 3\nid = 2\n"
        "postype = p1\npos = 80, 0\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 30\nvalue = 0\nctrl = 1\n"))
    sim = simulate.Sim(c, 9970, p2_dist=200, max_ticks=6).run(until_idle=False)
    a, b = sim.explods[:2]
    assert a.scale == (2.0, 2.0) and a.vel == (2, 0) and a.pos[0] < 40   # pos travada sem postype
    assert b.pos[0] == sim.p1.pos[0] + 80 and b.vel == (0.0, 0.0)      # reposicionado, parado


def test_damage_after_a_knockdown_and_minimum(tmp_path):
    """Dano como o Ikemen (computeDamage): depois de bater no chão (5100) a defesa sobe pelo
    fall.defence_up (50 -> dano / 1,5); dano entre 0 e 1 vira 1."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, derruba]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\ndamage = 30\n"
        "pausetime = 0, 0\nground.velocity = 0, -2\nfall = 1\nyaccel = 1\nsparkno = -1\n\n"
        "[State 9970, no chão]\ntype = HitDef\ntrigger1 = time = 20\nattr = S, NA\nhitflag = FD\n"
        "damage = 30\npausetime = 0, 0\nsparkno = -1\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 40\nvalue = 0\nctrl = 1\n"))
    sim = simulate.Sim(c, 9970, p2_dist=20, max_ticks=40).run(until_idle=False)
    dealt = [e["dealt"] for e in sim.events if e["kind"] == "acerto"]
    assert dealt == [30, 20]


def test_air_guard_falls_and_lands(tmp_path):
    """Defesa no ar (154/155): airguard.velocity padrão = air.velocity x 1,5 e y 0,5, cai
    com a gravidade do char e pousa; antes flutuava com a velocidade da defesa no chão."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\nguardflag = MA\n"
        "pausetime = 0, 2\nair.velocity = -2, -4\nsparkno = -1\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 5\nvalue = 0\nctrl = 1\n"))
    sim = simulate.Sim(c, 9970, p2_dist=30, p2_pose="air", p2_guard=True, max_ticks=80).run(until_idle=False)
    assert [e["kind"] for e in sim.events if e["kind"] in ("acerto", "defesa")] == ["defesa"]
    first = next(r for r in sim.track if r["p2"]["state"] == "guardhit" and r["p2"].get("sub") == "slide")
    i = sim.track.index(first)
    # primeiro tick do 155: hitvelset (3, -2) + gravidade
    assert abs(sim.track[i]["p2"]["pos"][0] - sim.track[i - 1]["p2"]["pos"][0] - 3) < 1e-6
    assert sim.track[-1]["p2"]["state"] == "stand" and sim.track[-1]["p2"]["pos"][1] == 0


def test_width_and_hitadd(tmp_path):
    """Width player soma à caixa de tamanho no empurrão (só no tick); HitAdd soma ao HitCount."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    sd = ("[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 0\nctrl = 0\n\n"
          "{w}[State 9970, conta]\ntype = HitAdd\ntrigger1 = time = 1\nvalue = 3\n\n"
          "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 4\nvalue = 0\nctrl = 1\n")
    ops.set_statedef_text(c, 9970, sd.format(w=""))
    plain = simulate.Sim(c, 9970, p2_dist=40, max_ticks=3).run(until_idle=False)
    ops.set_statedef_text(c, 9970, sd.format(w="[State 9970, larga]\ntype = Width\ntrigger1 = 1\n"
                                               "player = 30, 0\n\n"))
    wide = simulate.Sim(c, 9970, p2_dist=40, max_ticks=3).run(until_idle=False)
    assert plain.p2.pos[0] == 40 and wide.p2.pos[0] > 40
    assert wide.p1.hitcount == 3


def test_old_forms_compare_without_comma():
    """Formas antigas como o compilador do Ikemen: a vírgula da 2ª comparação é opcional
    (`AnimElem = 9 = 21` compara o AnimElemTime(9) com 21; antes comparava o 0/1 do AnimElem
    com 21 e o arremesso do Seiya nunca soltava o P2); ProjHit/ProjContact[ID] = 1 vira
    ProjHitTime = 0, e com `, < t` exige contato (tempo >= 0)."""
    assert ev("animelem = 2 = 4") == 1 and ev("animelem = 2 = 3") == 0
    assert ev("timemod = 4 = 2") == 1
    assert ev("projhit = 1", Ctx(projhittime=0)) == 1
    assert ev("projhit = 1", Ctx(projhittime=3)) == 0
    assert ev("projhit1330 = 1, < 10", Ctx(projhittime=3)) == 1
    assert ev("projhit1330 = 1, < 10", Ctx(projhittime=-1)) == 0      # sem contato
    assert ev("projcontact = 0, < 10", Ctx(projcontacttime=-1)) == 1


def test_ikemenversion_decides_the_version_rules(tmp_path):
    """As regras de versão saem do ikemenversion do .def, não do formato (ZSS) dos estados:
    char MUGEN ganha 1 tick no hittime; com ikemenversion o P2 volta 1 tick antes."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\ndamage = 10\n"
        "pausetime = 0, 0\nground.hittime = 10\nsparkno = -1\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 40\nvalue = 0\nctrl = 1\n"))

    def back(ch):
        sim = simulate.Sim(ch, 9970, p2_dist=30, max_ticks=40).run(until_idle=False)
        return next(r["t"] for r in sim.track if r["t"] > 0 and r["p2"]["state"] == "stand"), sim
    t_mugen, s1 = back(c)
    c.definition.section("info").set("ikemenversion", "1.0")
    t_ikemen, s2 = back(c)
    assert not s1.ikemenver and s2.ikemenver and s2.ikemen
    assert t_ikemen == t_mugen - 1


def test_screen_edges_hold_the_players(tmp_path):
    """Sem palco, a tela (320 de largura, screenleft/right 15) segue os dois com a tensão do
    Ikemen e prende os jogadores: o P1 recuando para na borda de trás (BackEdgeDist 0) a 290 px
    do P2 em vez de sair da tela; BackEdgeDist no começo é a distância até a borda de trás."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = I\nphysics = N\nanim = 0\nctrl = 0\n\n"
        "[State 9970, borda]\ntype = VarSet\ntrigger1 = time = 0\nvar(1) = backedgedist\n\n"
        "[State 9970, recua]\ntype = VelSet\ntrigger1 = 1\nx = -12\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 80\nvalue = 0\nctrl = 1\n"))
    sim = simulate.Sim(c, 9970, p2_dist=30, max_ticks=60).run(until_idle=False)
    assert sim.p1.vars[1] == 160 - 15 - 15                 # câmera no meio (x 15): borda em -130
    assert all(r["p2"]["pos"][0] - r["ents"][0]["pos"][0] <= 290 + 1e-6 for r in sim.track)
    assert sim.p1.pos[0] == sim.p2.pos[0] - 290 and sim.p2.pos[0] == 30   # sem arrastar o P2
    assert abs(sim.p1.trigger("backedgedist", None)) < 1e-6
    # distância pedida maior que a tela: o boneco começa na borda, com uma nota
    wide = simulate.Sim(c, 9970, p2_dist=400, max_ticks=1).run(until_idle=False)
    assert wide.p2.pos[0] == 290 and wide.p1.pos[0] == 0 and wide.unsupported


def test_gethitvar_xvel_is_in_world_space(tmp_path):
    """GetHitVar(xvel) no Ikemen é a velocidade do acerto no mundo (velocity.x × -facing do
    atacante); `VelSet x = GetHitVar(xvel) * facing` num estado do atacante leva o P2 para
    longe. O simulador devolvia o valor já relativo e o P2 voava para o lado do P1."""
    from charfactory.tools import ops, simulate
    c = _hit_char(tmp_path)
    ops.set_statedef_text(c, 9970, (
        "[Statedef 9970]\ntype = S\nmovetype = A\nphysics = S\nanim = 9970\nctrl = 0\n\n"
        "[State 9970, hd]\ntype = HitDef\ntrigger1 = time = 0\nattr = S, NA\ndamage = 10\n"
        "pausetime = 0, 0\nground.velocity = -6\np2stateno = 9990\nsparkno = -1\n\n"
        "[State 9970, fim]\ntype = ChangeState\ntrigger1 = time >= 30\nvalue = 0\nctrl = 1\n"))
    ops.set_statedef_text(c, 9990, (
        "[Statedef 9990]\ntype = S\nmovetype = H\nphysics = N\nanim = 0\nvelset = 0, 0\n\n"
        "[State 9990, voa]\ntype = VelSet\ntrigger1 = time = 0\nx = gethitvar(xvel) * facing\n\n"
        "[State 9990, fim]\ntype = SelfState\ntrigger1 = time >= 10\nvalue = 0\n"))
    sim = simulate.Sim(c, 9970, p2_dist=30, max_ticks=12).run(until_idle=False)
    assert sim.p2.pos[0] > 30 + 40                         # 6 px/tick para longe do P1
