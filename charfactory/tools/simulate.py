"""Simulador de golpes: roda os estados do personagem tick a tick, sem o MUGEN.

Para que serve: ver e medir posição, tamanho e alinhamento de tudo que um golpe faz — o
personagem andando (VelSet/PosAdd/PosSet/Turn), as fases encadeadas (ChangeState), os
Explods presos a quem os criou (p1/p2/tela, bindtime, removetime, ângulo, espelhamento),
os helpers rodando o próprio estado (BindToRoot, size.xscale), PalFX (silhueta branca),
SuperPause e um **oponente boneco** que trava no acerto, é empurrado pela velocidade do
HitDef e cai. Muito mais barato e preciso que gravar o MUGEN, e dá número para auditar
(efeito abaixo do chão, efeito gigante, halo escuro, golpe que não acerta, oponente jogado
para o lado errado). O MUGEN continua sendo o teste final das regras do motor (controle,
comandos, teclas, custo de energia, travamento).

Semântica conferida no Ikemen GO (src/char.go, src/bytecode.go; MIT):
- Explod `postype = p2`: herda o facing do oponente e o `pos x` é multiplicado por ele;
- velocidade do acerto no mundo: `-ground.velocity.x × facing do atacante`;
- helper: desenhado na escala `size.xscale/yscale`;
- o oponente segue o common1: tremor, recuo e HitOver (hittime + 1 em char MUGEN), voo com o
  yaccel do HitDef, recuperação no ar (5040), queda, quique (5100/5101), deitado liedown.time
  (5110) e levantar (5120); juggle pelo airjuggle; projéteis (`Projectile`) com projhits,
  projmisstime, hitpause próprio e bordas da tela.
- câmera e bordas da tela (camera.go sem zoom: tension 50, screenleft/right 15, P1 e P2 presos
  à tela); regras de versão pelo ikemenversion do .def (um ZSS sem ele segue as de char MUGEN).
Aproximações (anotadas no relatório quando usadas): o oponente não ataca nem anda sozinho,
palco sem fim (sem canto nem cornerpush), comandos sempre falsos (o golpe é forçado).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import simexpr
from .charkit import _Canvas, _frame_img, _trans, frame_at, sprite_rgba
from ..i18n import tr

# --------------------------------------------------------------------- leitura do CNS

P1_ID, P2_ID = 56, 57    # PlayerID com dois jogadores (MUGEN/Ikemen); helpers a partir de 58


def _blocks(text: str) -> list[dict]:
    """Controladores de um statedef: cabeçalho, tipo e TODOS os gatilhos e parâmetros."""
    out = []
    for blk in re.split(r"(?im)^\s*\[state ", text)[1:]:
        lines = blk.splitlines()
        head = lines[0].split("]")[0]
        ctl = {"label": head.strip(), "index": len(out), "triggerall": [], "triggers": {}, "params": {}}
        for ln in lines[1:]:
            ln = ln.split(";")[0].strip()
            m = re.match(r"([\w.()]+)\s*=\s*(.*)$", ln)
            if not m:
                continue
            k, v = m.group(1).lower(), m.group(2).strip()
            if k == "triggerall":
                ctl["triggerall"].append(v)
            elif re.fullmatch(r"trigger\d+", k):
                ctl["triggers"].setdefault(int(k[7:]), []).append(v)
            elif k == "type":
                ctl["type"] = v.lower()
            else:
                ctl["params"].setdefault(k, v)
        out.append(ctl)
    return out


def _header(text: str) -> dict:
    head = re.split(r"(?im)^\s*\[state ", text)[0]
    kv = {}
    for k, v in re.findall(r"(?m)^\s*([\w.]+)\s*=\s*(.*?)\s*(?:;.*)?$", head):
        kv.setdefault(k.lower(), v)
    return kv


def _split_args(v: str) -> list[str]:
    """Separa `a, b` respeitando parênteses (pos = p2dist x, -30 + random % 10)."""
    out, depth, cur = [], 0, ""
    for ch in v:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    # a vírgula de um redirecionamento não separa argumentos: `.2*Helper(999999),Fvar(16)` é
    # um valor só (escala dos efeitos lida de um helper)
    merged = []
    for part in out:
        if merged and _REDIR_END.search(merged[-1]):
            merged[-1] += "," + part
        else:
            merged.append(part)
    return [p.strip() for p in merged]


_REDIR_END = re.compile(r"(?i)(?<![\w.])(helper|enemynear|enemy|target|playerid|partner|root|parent)"
                        r"\s*(\(\s*[^()]*\))?\s*$")


# --------------------------------------------------------------------- entidades

def _ikemen_version(char) -> bool:
    """O .def tem ikemenversion (maior ou menor diferente de zero), como o gi.ikemenver."""
    v = str(char.info("ikemenversion", "") or "").split(";")[0].strip()
    nums = [int(m) for m in re.findall(r"\d+", v)[:2]]
    return any(nums)


@dataclass
class Entity:
    sim: "Sim"
    name: str
    stateno: int
    pos: list = field(default_factory=lambda: [0.0, 0.0])
    vel: list = field(default_factory=lambda: [0.0, 0.0])
    facing: int = 1
    time: int = 0
    anim: int = 0
    anim_t: int = 0                      # ticks desde o ChangeAnim
    ctrl: int = 0
    statetype: str = "S"
    movetype: str = "I"
    physics: str = "N"
    vars: dict = field(default_factory=dict)
    fvars: dict = field(default_factory=dict)
    sysvars: dict = field(default_factory=dict)
    sysfvars: dict = field(default_factory=dict)
    locals: dict = field(default_factory=dict)  # locais do ZSS (let/$x), zerados a cada tick
    hitpause: int = 0
    movehit: int = 0
    movecontact: int = 0
    moveguarded: int = 0
    hitcount: int = 0
    hitdef: dict | None = None
    hitdef_hit: bool = False
    sprpriority: int = 0
    palfx: dict | None = None
    invisible: bool = False
    is_helper: bool = False
    helper_id: int = 0
    pid: int = P1_ID                      # PlayerID (o gatilho id e o redirecionamento playerid)
    parent: "Entity | None" = None
    root: "Entity | None" = None
    scale: tuple = (1.0, 1.0)
    trans: str | None = None             # Trans: vale só no tick em que roda
    alpha: tuple | None = None
    size: dict = field(default_factory=dict)   # size.* do Helper: o const() do helper usa estes
    angle: float = 0.0                   # AngleSet (fica)
    draw_angle: float | None = None      # AngleDraw: só no tick em que roda
    draw_scale: tuple | None = None
    bind_root: tuple | None = None       # (x, y) do BindToRoot neste tick
    alive: bool = True
    persist: dict = field(default_factory=dict)
    pausemove: int = 0
    screen: bool = False                 # helper de tela (cut-in): fora da folha do palco
    started: bool = False                # já passou pelo 1º tick (o tempo avança no começo dos seguintes)
    frozen: bool = False                 # pausa do acerto neste tick
    hit_now: bool = False                # acertou neste tick (movehit vira 1 no fim do tick)
    # trocou de estado/anim durante a pausa do acerto: o Ikemen roda o Time = 0 (e o 1º tick da
    # anim) no 1º tick fora da pausa, e só então o tempo sobe
    hold_time: bool = False
    hold_anim: bool = False
    juggle: int = 0                      # custo de juggle do estado (Statedef juggle)
    proj: dict | None = None             # projétil (controlador Projectile): estado dele
    proj_contact: dict | None = None     # último contato de um projétil deste dono
    prevstateno: int = 0
    power: int = 3000
    screenbound: tuple = (True, True)    # ScreenBound (preso à tela, move a câmera em x) deste tick

    # ---- contexto do avaliador
    @property
    def lenient_redirect(self) -> bool:
        return self.sim.ikemen

    def set_var(self, name, idx, value):
        """Atribuição `var(n) := v` dentro de expressão (comum em ZSS)."""
        d = {"var": self.vars, "fvar": self.fvars, "sysvar": self.sysvars,
             "sysfvar": self.sysfvars, "zsslocal": self.locals}[name]
        d[int(idx)] = int(value) if name in ("var", "sysvar") else value
        return d[int(idx)]

    def trigger(self, name, arg):
        s = self.sim
        if name == "time":
            return self.time
        if name == "gametime":
            return s.tick
        if name in ("anim",):
            return self.anim
        if name == "animtime":
            return self.animtime()
        if name == "animelemno":
            return self.elem_at(self.anim_t + (arg or 0))
        if name == "animelemtime":
            return self.animelem_time(int(arg)) if self.animelem_time(int(arg)) is not None else -1
        if name == "stateno":
            return self.stateno
        if name == "prevstateno":
            return self.prevstateno
        if name == "pos x":
            return self.pos[0] - s.origin_x
        if name == "pos y":
            return self.pos[1]
        if name == "vel x":
            return self.vel[0]
        if name == "vel y":
            return self.vel[1]
        if name == "p2dist x":
            return s.dist_round((s.p2.pos[0] - self.pos[0]) * self.facing)
        if name == "p2dist y":
            return s.dist_round(s.p2.pos[1] - self.pos[1])
        if name == "p2bodydist x":
            return s.dist_round(s.body_dist_x(self))
        if name == "p2bodydist y":
            return s.dist_round(s.p2.pos[1] - self.pos[1])
        if name == "facing":
            return self.facing
        if name == "var":
            return self.vars.get(int(arg), 0)
        if name == "fvar":
            return self.fvars.get(int(arg), 0.0)
        if name == "sysvar":
            return self.sysvars.get(int(arg), 0)
        if name == "sysfvar":
            return self.sysfvars.get(int(arg), 0.0)
        if name == "zsslocal":
            return self.locals.get(int(arg), 0)
        if name == "gethitvar":                   # o P1 simulado não apanha
            return 0
        if name in ("hitshakeover", "hitover"):   # sem acerto: o tremor e o acerto já acabaram
            return 1
        if name in ("hitfall", "canrecover"):
            return 0
        if name == "random":
            return s.random()
        if name in ("movehit", "movecontact"):    # ticks desde o acerto (1 no 1º; parado na pausa)
            return getattr(self, name)
        if name == "moveguarded":
            return self.moveguarded
        if name == "movereversed":
            return 0
        if name == "hitcount" or name == "uniqhitcount":
            return self.hitcount
        if name in ("projhittime", "projcontacttime", "projguardedtime"):
            pc = self.proj_contact
            want = {"projhittime": ("hit",), "projguardedtime": ("guard",),
                    "projcontacttime": ("hit", "guard")}[name]
            if pc is None or pc["type"] not in want or (arg and pc["id"] != arg):
                return -1
            return s.tick - pc["t"] - 1
        if name in ("numproj", "numprojid"):
            return sum(1 for q in s.projs if q.alive and q.parent is self and q.proj["status"] == "active"
                       and (name == "numproj" or q.proj["id"] == arg))
        if name == "numtarget":
            if arg is not None and not s._target_id_ok(int(arg)):
                return 0
            return 1 if (self.movehit or (self.root and self.root.movehit)) and s.p2.state != "stand" else 0
        if name == "numhelper":
            return sum(1 for h in s.helpers if h.alive and (arg is None or h.helper_id == arg))
        if name == "numexplod":
            return sum(1 for e in s.live_explods if e.alive and (arg is None or e.id == arg))
        if name == "numenemy":
            return 1
        if name == "ctrl":
            return self.ctrl
        if name == "ailevel":
            return 0
        if name == "roundstate":
            return 2
        if name in ("roundno", "roundsexisted"):   # 1º round da luta
            return 1 if name == "roundno" else 0
        if name == "alive":
            return 1
        if name == "ishelper":
            return 1 if self.is_helper else 0
        if name in ("statetype", "movetype", "physics"):
            return getattr(self, name)
        if name == "p2statetype":
            return s.p2.stype()
        if name == "p2movetype":
            return "H" if s.p2.movetype_h() else "I"
        if name == "p2stateno":
            return s.p2.state_no()
        if name == "power":
            return (self.root or self).power
        if name == "powermax":
            return 3000
        if name in ("life", "lifemax"):
            return 1000
        if name in ("hitpausetime",):
            return self.hitpause
        if name in EDGE_TRIGGERS:
            return s.edge_trigger(self, name)
        if name == "screenpos y":
            return 200
        if name in ("animexist", "selfanimexist"):
            return 1 if s.char.anims.get(int(arg)) is not None else 0
        if name in ("const240p", "const480p", "const720p"):   # localcoord 320x240
            return arg * {"const240p": 1, "const480p": 0.5, "const720p": 0.25}[name]
        if name in ("screenwidth", "gamewidth"):
            return 320
        if name in ("screenheight", "gameheight"):
            return 240
        # constantes e medidas que os chars usam para decidir quando o efeito acaba: sem elas a
        # condição falhava, o helper não se destruía e o golpe não terminava (como na issue #6)
        if name == "pi":
            return math.pi
        if name == "e":
            return math.e
        if name == "camerapos y":
            return 0.0
        if name in ("topedge", "bottomedge"):
            return -200 if name == "topedge" else 40
        if name in ("parentdist x", "rootdist x"):
            o = (self.parent if name[0] == "p" else self.root) or self
            return (o.pos[0] - self.pos[0]) * self.facing
        if name in ("parentdist y", "rootdist y"):
            o = (self.parent if name[0] == "p" else self.root) or self
            return o.pos[1] - self.pos[1]
        if name == "palno":
            return 1
        if name == "tickspersecond":
            return 60
        if name == "p2life":
            return s.p2.life
        if name == "winko":
            return 0
        if name == "teamside":
            return 1
        if name == "inguarddist":
            return 0
        if name == "id":
            return self.pid
        if name == "playeridexist":
            return 1 if s.by_pid(int(arg or 0)) is not None else 0
        if name == "teammode":
            return "single"
        if name == "gamemode":
            return "versus"
        if name in ("name", "p1name", "displayname"):
            return s.char.info("name", "").strip('"')
        if name == "authorname":
            return s.char.info("author", "").strip('"')
        if name in ("p2name", "p3name", "p4name"):
            return ""
        if name == "matchover":
            return 0
        if name == "win" or name == "lose":
            return 0
        if name == "const":
            return self.const(arg)
        raise simexpr.Unsupported(tr("trigger {0}").format(name))

    def const(self, key):
        """Constante do char; num helper, o size.* que o Helper deu (size.xscale = ...)."""
        if self.size:
            k = key.lower()
            parts = k.split(".")
            for kk, idx in ((k, 0), (".".join(parts[:-1]), {"x": 0, "y": 1, "z": 2}.get(parts[-1], 0))):
                if kk in self.size:
                    vals = self.size[kk]
                    return vals[min(idx, len(vals) - 1)]
        return self.sim.const(key)

    def command(self, name):
        return name in self.sim.commands

    def redirect(self, kind, arg):
        s = self.sim
        if kind in ("enemynear", "enemy", "p2", "target"):
            return s.p2
        if kind == "root":
            return self.root or self
        if kind == "parent":
            return self.parent or self
        if kind == "helper":
            for h in s.helpers:
                if h.alive and (arg is None or h.helper_id == arg):
                    return h
            return s.standing_helper(arg)
        if kind == "playerid":
            return s.by_pid(int(arg or 0))
        return None

    # ---- animação
    def frames(self):
        a = self.sim.char.anims.get(self.anim)
        return a.frames if a else []

    def starts(self):
        out, t = [], 0
        for f in self.frames():
            out.append(t)
            t += f.time if f.time > 0 else 10 ** 6
        return out, t

    def _age(self, t=None, clock=False):
        """Idade na anim considerando o laço. clock=True: o relógio do AnimElem/AnimElemTime."""
        t = self.anim_t if t is None else t
        a = self.sim.char.anims.get(self.anim)
        if not a or not a.frames:
            return t
        total = a.total_time
        if clock:
            # Ikemen (anim.go, Action): no tick em que AnimTime = 0 o quadro já voltou ao laço,
            # mas o curtime (o relógio do AnimElem) só volta no passo seguinte; por isso
            # AnimElem = 1 não dispara de novo na volta (medido com o log por tick). O anim.go
            # não olha a versão do char: vale para char MUGEN também (o TargetBind do quadro 1
            # do arremesso do kfm puxava o P2 de volta no tick do AnimTime = 0)
            if total > 0 and t > total and all(f.time > 0 for f in a.frames):
                ls = a.loopstart or 0
                pre = sum(f.time for f in a.frames[:ls])
                loop = max(1, total - pre)
                t = pre + (t - pre - 1) % loop + 1
            return t
        if total > 0 and t >= total and all(f.time > 0 for f in a.frames):
            ls = a.loopstart or 0
            pre = sum(f.time for f in a.frames[:ls])
            loop = max(1, total - pre)
            t = pre + (t - pre) % loop
        return t

    def elem_at(self, t):
        st, _ = self.starts()
        age = self._age(t)
        e = 0
        for k, s0 in enumerate(st):
            if age >= s0:
                e = k + 1
        return e

    def animelem_start(self, n):
        st, _ = self.starts()
        if n < 1 or n > len(st):
            return False
        return self._age(clock=True) == st[n - 1]   # MUGEN: vale nas voltas; Ikemen: ver _age

    def animelem_time(self, n):
        st, _ = self.starts()
        if n < 1 or n > len(st):
            return None
        return self._age(clock=True) - st[n - 1]

    def animtime(self):
        """0 no tick em que a anim termina (e a cada volta), negativo antes — como o MUGEN."""
        a = self.sim.char.anims.get(self.anim)
        if not a or not a.frames:
            return 0
        if any(f.time < 0 for f in a.frames):
            return -1
        total, t = a.total_time, self.anim_t
        if t <= total:
            return t - total
        pre = sum(f.time for f in a.frames[:a.loopstart or 0])
        loop = max(1, total - pre)
        k = (t - pre) % loop
        return 0 if k == 0 else (pre + k) - total

    def frame(self):
        """Quadro atual; a anim repete (o MUGEN repete toda anim sem -1 no fim)."""
        fr = self.frames()
        if not fr:
            return None
        return fr[max(0, self.elem_at(self.anim_t) - 1)]


@dataclass
class Explod:
    anim: int
    pos: list
    facing: int
    vfacing: int
    scale: tuple
    angle: float
    spr: int
    trans: str | None
    removetime: int
    bindtime: int
    bind_to: Entity | None
    offset: tuple
    vel: tuple
    accel: tuple
    id: int
    owner: Entity
    space: str = "stage"
    t: int = 0
    alive: bool = True
    label: str = ""
    removeongethit: bool = False
    born: int = 0
    pausemove: int = 0
    on_hit: bool = False
    alpha: tuple | None = None           # alpha = s,d do Explod (com trans = addalpha)
    move_facing: int = 1                 # lado em que a velocidade anda (o do dono, sem o facing do parâmetro)
    anim_t0: int = 0                     # tick (e.t) em que a anim atual começou (ModifyExplod anim)
    friction: tuple = (1.0, 1.0)


class P2Runner(Entity):
    """O P2 rodando um estado do atacante (HitDef p2stateno, TargetState): os controladores
    rodam como num char, com o P1 como oponente; a posição e a velocidade são as do boneco."""

    def trigger(self, name, arg):
        s, p1, d = self.sim, self.sim.p1, self.sim.p2
        if name == "p2dist x":
            return s.dist_round((p1.pos[0] - self.pos[0]) * self.facing)
        if name in ("p2dist y", "p2bodydist y"):
            return s.dist_round(p1.pos[1] - self.pos[1])
        if name == "p2bodydist x":
            f, _, _ = s._size(None, False)
            return s.dist_round((p1.pos[0] - self.pos[0]) * self.facing - 2 * f)
        if name == "p2statetype":
            return p1.statetype
        if name == "p2movetype":
            return p1.movetype
        if name == "p2stateno":
            return p1.stateno
        if name in ("life", "p2life"):
            return d.life if name == "life" else 1000
        if name == "gethitvar":
            return s.p2_gethitvar(arg)
        if name in ("hitshakeover", "hitover", "hitfall"):
            return d.trigger(name, arg)
        if name == "hitvel x":
            return d.hitvel[0] * self.facing
        if name == "hitvel y":
            return d.hitvel[1]
        if name == "canrecover":
            return 0
        if name in ("numtarget", "movehit", "movecontact", "moveguarded", "hitcount"):
            return 0
        if name == "id":
            return P2_ID
        return super().trigger(name, arg)

    def redirect(self, kind, arg):
        if kind in ("enemynear", "enemy", "p2", "target"):
            return self.sim.p1
        if kind in ("root", "parent"):
            return self
        return super().redirect(kind, arg)


@dataclass
class Dummy:
    """Oponente boneco: caixa do tamanho do char, reage ao acerto."""
    pos: list
    facing: int = -1
    vel: list = field(default_factory=lambda: [0.0, 0.0])
    state: str = "stand"
    shake: int = 0
    hittime: int = 0
    ctrltime: int = 0        # defesa: ticks até o P2 voltar a ter ctrl (guard.ctrltime)
    fall: bool = False
    t: int = 0
    hits: int = 0
    stateno: int = 0
    vars: dict = field(default_factory=dict)
    custom: int = 0          # estado do atacante em que o boneco está (HitDef p2stateno, TargetState)
    airguard: bool = False   # defendendo no ar (154/155)
    pose: str = "stand"      # postura parada: "stand", "crouch" ou "air" (paira no alto até apanhar)
    guard: bool = False      # defende o que o guardflag deixa
    life: int = 1000         # vida do boneco (lifemax padrão do MUGEN), para o dano do golpe
    # acerto e defesa como o Ikemen (common1 5000/5001 e 150/151): tremor ("shake") e depois o
    # recuo ("slide"), com o tempo do recuo (st) contado à parte; HitOver = hittime < 0
    sub: str = ""
    st: int = 0
    slidetime: int = 0
    hitvel: tuple = (0.0, 0.0)
    combo: int = 0           # acertos seguidos sem o P2 sair do acerto
    hitid: int = -1          # id do último HitDef que o pegou (chainid/nochainid)
    ctrl: bool = True
    # no ar e no chão (common1 5020/5030/5040/5050, 5100/5101/5110/5120): gravidade do HitDef
    # (yaccel), quique (fall.yvelocity) e o tempo deitado (liedown.time)
    yaccel: float = 0.35
    fallvel: tuple = (None, -4.5)
    down_rt: int = 60
    juggle: dict = field(default_factory=dict)   # pontos de juggle que sobram por atacante (PlayerID)
    bind: dict | None = None                     # TargetBind/BindToTarget: preso a quem, onde, quanto tempo
    def_mul: float = 1.0                         # defesa extra depois de cair (fall.defence_up)
    animtype: int = 0        # GetHitVar(animtype): 0 light, 1 medium, 2 hard, 3 back, 4 up, 5 diagup
    groundtype: int = 1      # GetHitVar(groundtype/airtype): 1 high, 2 low, 3 trip
    airtype: int = 1
    hit_damage: int = 0

    def stype(self) -> str:
        """StateType do P2 como o motor vê (S, C, A ou L): decide o hitflag e o guardflag."""
        if self.state == "down":
            return "L"
        if self.state in ("air", "fall") or self.pos[1] < 0:
            return "A"
        return "C" if self.pose == "crouch" else "S"

    def movetype_h(self) -> bool:
        """MoveType = H: apanhando ou defendendo (some no HitOver no ar e ao levantar)."""
        if self.custom:
            return True
        if self.state == "air":
            return not self.ctrl
        if self.state == "down":
            return self.sub != "getup"
        return self.state in ("hit", "guardhit", "fall")

    def state_no(self) -> int:
        if self.custom:
            return self.custom
        if self.state == "stand":
            return {"stand": 0, "crouch": 11, "air": 50}[self.pose]
        if self.state == "guardhit":
            return 152 if self.pose == "crouch" else 150
        if self.state == "hit":
            return (5010 if self.pose == "crouch" else 5000) + (self.sub == "slide")
        if self.state in ("air", "fall"):
            if self.sub == "shake":
                return 5020 if self.pos[1] < 0 else (5010 if self.pose == "crouch" else 5000)
            return {"fly": 5030, "recover": 5040, "falling": 5050}.get(self.sub, 5030)
        return {"bounce": 5100, "bounce2": 5101, "getup": 5120}.get(self.sub, 5110)

    def anim_no(self) -> int:
        """Anim do P2 como o common1 escolhe: 5000 (alto) / 5010 (baixo) / 5020 (agachado) +
        animtype no chão, 5030/5040/5050 no ar, 5100/5110/5120 no chão, 150/152/154 na defesa."""
        sim = self._sim
        if self.custom and sim.p2_run is not None:
            return sim.p2_run.anim
        if self.state == "stand":
            return {"stand": 0, "crouch": 11, "air": 41}[self.pose]
        if self.state == "guardhit":
            return 154 if self.airguard else 152 if self.pose == "crouch" else 150
        at = min(self.animtype, 2)
        if self.state == "hit":
            base = 5020 if self.pose == "crouch" else 5010 if self.groundtype == 2 else 5000
            return base + at
        if self.state in ("air", "fall"):
            if self.sub == "shake":
                return 5030 if self.animtype >= 3 or self.pos[1] < 0 else 5000 + at
            return {"fly": 5030, "recover": 5040, "falling": 5050}.get(self.sub, 5030)
        return {"bounce": 5100, "bounce2": 5101, "getup": 5120}.get(self.sub, 5110)

    # o boneco também responde a gatilhos redirecionados (enemynear, pos x)
    def trigger(self, name, arg):
        if name == "pos x":
            return self.pos[0] - self._sim.origin_x
        if name == "pos y":
            return self.pos[1]
        if name == "vel x":                      # relativa ao lado do P2, como a do P1
            return self.vel[0] * self.facing
        if name == "vel y":
            return self.vel[1]
        if name == "facing":
            return self.facing
        if name == "anim":
            return self.anim_no()
        if name == "gethitvar":
            return self._sim.p2_gethitvar(arg)
        if name in EDGE_TRIGGERS:
            return self._sim.edge_trigger(self, name)
        if name == "stateno":
            return self.state_no()
        if name == "statetype":
            return self.stype()
        if name == "movetype":
            return "H" if self.movetype_h() else "I"
        if name == "life":
            return self.life
        if name == "lifemax":
            return 1000
        if name == "time":
            return self.t
        if name == "hitshakeover":
            return int(self.state == "stand" or self.shake <= 0)
        if name == "hitover":
            return int(self.state == "stand" or self.hittime < 0)
        if name == "hitfall":
            return int(self.fall)
        if name == "ctrl":
            return int(self.state == "stand" or self.ctrl)
        if name == "hitcount":
            return self.combo
        if name == "alive":
            return 1
        if name == "id":
            return P2_ID
        if name in ("name", "displayname", "authorname"):
            return ""
        raise simexpr.Unsupported(tr("opponent trigger {0}").format(name))

    def redirect(self, kind, arg):
        return None

    def const(self, key):
        return self._sim.const(key)

    def command(self, name):
        return False

    def animelem_start(self, n):
        return False

    def animelem_time(self, n):
        return None


# --------------------------------------------------------------------- simulador

EDGE_TRIGGERS = {"backedgedist", "frontedgedist", "backedgebodydist", "frontedgebodydist",
                 "leftedge", "rightedge", "screenpos x", "camerapos x"}

CTRL_IGNORED = {"null", "playsnd", "stopsnd", "nothitby", "hitby",
                "afterimage", "afterimagetime", "envshake", "envcolor", "assertspecial_",
                "makedust", "sndpan", "defencemulset", "hitoverride",
                "lifeset", "displaytoclipboard", "appendtoclipboard",
                "clearclipboard", "victoryquote", "forcefeedback", "gamemakeanim", "bgpalfx",
                "allpalfx", "remappal", "hitfalldamage", "hitfallvel",
                "hitfallset", "fallenvshake", "targetpoweradd",
                "targetdrop", "reversaldef", "attackdist", "offset",
                "zoom", "camera"}
# controladores registrados como evento "ctrl" (som, tremor, cor, rastro, sumir, prender)
REVERSAL_T = 8           # tick da janela do ReversalDef em que o boneco "ataca" o contra
HELPER_MAX = 56          # helpers vivos por jogador (MUGEN 1.1)
EXPLOD_MAX = 512         # explods vivos (ExplodMax do mugen.cfg)
# trocas de estado num tick: há laços finitos longos (voltar ao próprio estado andando 1 px por
# volta até tocar o chão, contar sysvar até 5); passar disso é laço sem fim
LOOP_MAX = 2500
CTRL_LOGGED = {"playsnd", "envshake", "palfx", "bgpalfx", "allpalfx", "envcolor", "afterimage",
               "assertspecial", "targetbind", "bindtotarget", "targetstate", "posadd", "posset",
               "turn", "superpause", "pause", "projectile", "angledraw", "makedust",
               "reversaldef", "hitoverride", "screenbound"}
# controladores que agem nos alvos (o id escolhe o alvo pelo id do HitDef que o acertou)
TARGET_CTRLS = {"targetbind", "bindtotarget", "targetstate", "targetvelset", "targetveladd",
                "targetfacing", "targetlifeadd", "targetpoweradd"}


class Sim:
    def __init__(self, char, state: int, p2_dist: float = 50.0, seed: int = 7,
                 max_ticks: int = 900, commands: tuple = (), p2_clsn: list | None = None,
                 p2_pose: str = "stand", p2_guard: bool = False, chain: tuple = (),
                 from_ground: bool = False):
        self.char = char
        self.p2_clsn = p2_clsn                    # Clsn2 do P2 (oponente real); sem = boneco do tamanho do char
        self.defs = char.all_statedefs()
        self.cache: dict[int, tuple[dict, list]] = {}
        self.tick = 0
        self.seed = seed
        self._rng = np.random.default_rng(seed)
        self.commands = set(commands)
        self.origin_x = 0.0
        self.notes: list[str] = []
        self.unsupported: dict[str, int] = {}
        self.events: list[dict] = []
        self.helpers: list[Entity] = []
        self.projs: list[Entity] = []
        self.p2_run: P2Runner | None = None       # o P2 num estado do atacante
        self.explods: list[Explod] = []
        self.live_explods: list[Explod] = []       # só as vivas (o passo tira as que saíram)
        self.pause = 0
        self.pause_owner: Entity | None = None
        self._reversed = False                    # o boneco já "atacou" o ReversalDef
        self.max_ticks = max_ticks
        # regras de versão do char: o Ikemen olha o ikemenversion do .def, não o formato dos
        # estados (um ZSS sem ikemenversion segue as regras de char MUGEN, como o kfm_zss)
        self.ikemenver = _ikemen_version(char)
        # motor: char ZSS ou com ikemenversion só roda no Ikemen GO (expressões, física)
        self.ikemen = bool(getattr(char, "is_zss", False)) or self.ikemenver
        try:                                       # localcoord do char / 320 (escala dos limites)
            self.lc_ratio = float(char.info("localcoord", "320,240").split(",")[0]) / 320 or 1.0
        except ValueError:
            self.lc_ratio = 1.0
        self.consts = self._load_consts()
        # versão do MUGEN do char (.def mugenversion): WinMUGEN arredonda distâncias e pausa 1 a menos
        self.mugen1 = self.ikemen or str(char.info("mugenversion", "1.0")).strip().startswith("1")
        self.air_juggle = int(self.const("data.airjuggle"))
        self.stand_h = self._stand_height()
        self.body_w = 14.0
        self.p1 = Entity(self, "P1", state)
        self.p1.root = None
        self.p1.ctrl = 0
        self.p2 = Dummy(pos=[p2_dist, 0.0])
        self.p2._sim = self
        # câmera do Ikemen (camera.go, Fighting_View) sem zoom e sem palco: 320 de largura na
        # escala do char, tension 50, screenleft/screenright 15; começa no meio dos dois
        lc = self.lc_ratio
        self.cam_half, self.cam_tension, self.screen_lr = 160 * lc, 50 * lc, 15 * lc
        self.stage_left, self.stage_right = -1e6, 1e6
        span = 2 * (self.cam_half - self.screen_lr)   # o mais longe que os dois cabem na tela
        if abs(p2_dist) > span:
            p2_dist = math.copysign(span, p2_dist)
            self.p2.pos[0] = p2_dist
            self.note(tr("the dummy starts at most {0:.0f} px away (the width of the screen)").format(span))
        self.cam_x = (self.p1.pos[0] + p2_dist) / 2
        self.xmin = self.cam_x - self.cam_half + self.screen_lr
        self.xmax = self.cam_x + self.cam_half - self.screen_lr
        self.track: list[dict] = []
        self.enter(self.p1, state, first=True)
        self.p1.started = False
        # golpe aéreo: começa no alto do pulo, caindo; from_ground = sai do chão (pulo com ataque)
        if self.p1.statetype == "A" and not from_ground:
            self.p1.pos[1] = -1.3 * self.stand_h
            self.p1.vel = [self.p1.vel[0] or 2.0, self.p1.vel[1] or 0.5]
            self.p2.pos[1] = 0.0
        self.p2.pose, self.p2.guard = p2_pose, p2_guard
        # cadeia de golpes (combo): os estados seguintes entram assim que podem
        self.chain, self.chain_at = tuple(chain), 0
        if len(self.chain) > 1:
            self.cancels = combo_graph(char, char.all_statedefs(include_common=False))
        if p2_pose == "air":                        # subindo no pulo, à altura do peito do P1
            self.p2.pos[1] = -0.5 * self.stand_h
        if p2_pose != "stand" and self.p2_clsn is None:
            # agachado e no ar o boneco genérico erra demais: usa as caixas do próprio char
            # (como num espelho); em pé fica o boneco de sempre, calibrado para isso
            self.p2_clsn = opponent_clsn(char)

    # ---- constantes e utilidades
    def _load_consts(self):
        out = {}
        try:
            doc = self.char.constants
            for sec in doc.sections:
                if sec.name.lower() in ("data", "size", "velocity", "movement"):
                    for k, v in sec.as_dict().items():
                        out[f"{sec.name.lower()}.{k.lower()}"] = v
                elif sec.name.lower() == "constants":     # Ikemen: const(MeuValor), por cima do common.const
                    for k, v in sec.as_dict().items():
                        out[k.lower()] = v
        except Exception:
            pass
        return out

    def const(self, key):
        key = key.lower()
        parts = key.split(".")
        base, comp = ".".join(parts[:-1]), parts[-1]
        for k, idx in ((key, 0), (base, {"x": 0, "y": 1, "z": 2}.get(comp, 0))):
            if k in self.consts:
                vals = [float(x) for x in re.findall(r"-?\d*\.?\d+", self.consts[k])]
                if vals:
                    return vals[min(idx, len(vals) - 1)]
        defaults = {"movement.yaccel": 0.44, "movement.stand.friction": 0.85,
                    "movement.crouch.friction": 0.82, "size.height": 60,
                    # valores padrão do Ikemen (char.go) para queda, quique e juggle
                    "movement.air.gethit.groundlevel": 25.0, "movement.down.bounce.offset.y": 20.0,
                    "movement.down.bounce.yaccel": 0.4, "movement.down.bounce.groundlevel": 12.0,
                    "movement.down.friction.threshold": 0.05, "data.liedown.time": 60,
                    "data.fall.defence_up": 50,
                    "data.airjuggle": 15, "size.ground.front": 16, "size.ground.back": 15,
                    "size.air.front": 12, "size.air.back": 12}
        if key in defaults:
            return defaults[key]
        if self.ikemen:              # common.const do Ikemen: StateStand, FxDizzyEffect…
            from ..formats.zssread import common_consts
            v = re.fullmatch(r"\s*(-?\d*\.?\d+)\s*", common_consts().get(key, ""))
            if v:
                return float(v.group(1))
        return 0

    def _stand_height(self):
        f = frame_at(self.char, 0, 0)
        if f is None:
            return 60.0
        im, ax, ay = _frame_img(self.char, f)
        if im is None:
            return 60.0
        bb = im.getchannel("A").getbbox()
        return float(ay - bb[1]) if bb else 60.0

    def random(self):
        return int(self._rng.integers(0, 1000))

    def note(self, msg):
        self.unsupported[msg] = self.unsupported.get(msg, 0) + 1

    def by_pid(self, n: int):
        """Quem tem esse PlayerID: o P1, o boneco (P2) ou um helper vivo."""
        if n == P2_ID:
            return self.p2
        return next((e for e in [self.p1, *self.helpers] if e.alive and e.pid == n), None)

    def standing_helper(self, hid):
        """Helper que o char cria no começo da luta e lê o tempo todo (ex.: Helper(999999) com
        Fvar(16) = escala dos efeitos, padrão comum em chars). O golpe simulado não o cria;
        sem ele, `Helper(999999),Fvar(16)` vale 0 e posições e escalas desabam. Aqui ele existe
        só para leitura: roda os VarSet/VarAdd do estado dele no tick 0, fora da lista de
        helpers (não desenha, não acerta, não conta como helper preso)."""
        if hid is None:
            return None
        cache = self.__dict__.setdefault("_standing", {})
        if hid in cache:
            return cache[hid]
        h = None
        sd = self.statedef(int(hid)) if int(hid) in self.defs else None
        if sd is not None:
            root = self.p1
            h = Entity(self, f"helper {int(hid)}", int(hid))
            h.is_helper = True
            h.helper_id = int(hid)
            h.parent = h.root = root
            h.pos = list(root.pos)
            h.facing = root.facing
            for c in sd[1]:
                if c.get("type") in ("varset", "varadd") and self._triggered(h, c):
                    self.exec(h, c)
        cache[hid] = h
        return h

    def statedef(self, n):
        if n not in self.cache:
            sd = self.defs.get(n)
            if not sd:
                return None
            text = sd[0].text()
            self.cache[n] = (_header(text), _blocks(text))
        return self.cache[n]

    def ev(self, ent, expr, default=0):
        if getattr(ent, "proj", None) is not None:   # projétil: as expressões são do dono
            ent = ent.parent
        try:
            return simexpr.evaluate(expr, ent)
        except simexpr.Unsupported as ex:
            self.note(str(ex))
            return default
        except Exception as ex:                       # expressão quebrada: anota
            self.note(tr("error in {0!r}: {1}").format(expr, type(ex).__name__))
            return default

    def _sound(self, ent, v: str, hitdef: bool = False):
        """[grupo, número, comum] de um PlaySnd value ou HitDef hitsound. No PlaySnd o som é do
        char e "F" pede o fight.snd do motor; no HitDef é o contrário: "S" pede o do char."""
        v = (v or "").strip()
        lead = v[:1].upper()
        common = lead == "F" or (hitdef and lead != "S")
        if lead in ("S", "F"):
            v = v[1:]
        if not v:
            return None
        g, n = self.evpair(ent, v)
        return [int(g), int(n), common]

    def evpair(self, ent, v, default=(0, 0)):
        parts = _split_args(v) if v else []
        vals = [self.ev(ent, p, 0) if p else d for p, d in zip(parts + [""] * 2, default)]
        return vals[0], vals[1]

    def set_facing(self, ent, f):
        """Muda o lado. No Ikemen (setFacing) a velocidade x vira junto: o movimento no mundo
        continua para o mesmo lado (medido com o log por tick)."""
        if f and (ent.facing < 0) != (f < 0):
            ent.facing = -ent.facing
            if self.ikemen:
                ent.vel[0] = -ent.vel[0]

    # ---- estados
    def enter(self, ent: Entity, n: int, first=False):
        sd = self.statedef(n)
        ent.prevstateno = ent.stateno
        ent.stateno = n
        ent.time = 0
        ent.persist = {}
        ent.hold_time = ent.hold_anim = bool(ent.frozen)
        h = sd[0] if sd is not None else {}
        # hitdefpersist, movehitpersist e hitcountpersist (padrão 0): o que o Statedef não
        # manda guardar zera ao entrar (Ikemen stateDef.Run)
        on = lambda k: bool(self.ev(ent, h[k], 0)) if k in h else False
        if first or not on("hitdefpersist"):
            ent.hitdef = None
            ent.hitdef_hit = False
        if not first and not on("movehitpersist"):
            ent.movehit = ent.movecontact = ent.moveguarded = 0
        if not first and not on("hitcountpersist"):
            ent.hitcount = 0
        if sd is None:
            self.note(tr("statedef {0} does not exist").format(n))
            return
        if "type" in h:
            ent.statetype = h["type"].strip().upper()[:1]
        if "movetype" in h:
            ent.movetype = h["movetype"].strip().upper()[:1]
        if "physics" in h:
            ent.physics = h["physics"].strip().upper()[:1]
        if "anim" in h:                                  # o MUGEN reinicia a anim mesmo se for a mesma
            a = int(self.ev(ent, h["anim"], ent.anim))
            if a != -1:                                  # anim = -1: não muda
                ent.anim, ent.anim_t = a, 0
        if "velset" in h:                                # só o y que vier escrito
            parts = _split_args(h["velset"])
            ent.vel[0] = float(self.ev(ent, parts[0], 0)) if parts and parts[0] else 0.0
            if len(parts) > 1 and parts[1]:
                ent.vel[1] = float(self.ev(ent, parts[1], 0))
        if "ctrl" in h:
            ent.ctrl = int(self.ev(ent, h["ctrl"], 0))
        if "sprpriority" in h:
            ent.sprpriority = int(self.ev(ent, h["sprpriority"], 0))
        if "juggle" in h:                                # sem juggle o custo anterior fica
            ent.juggle = int(self.ev(ent, h["juggle"], 0))
        if "poweradd" in h:
            (ent.root or ent).power += int(self.ev(ent, h["poweradd"], 0))
        if "facep2" in h and self.ev(ent, h["facep2"], 0):
            self.set_facing(ent, 1 if self.p2.pos[0] >= ent.pos[0] else -1)
        self.events.append({"t": self.tick, "who": ent.name, "kind": "estado", "value": n})

    def run_entity(self, ent: Entity):
        frozen = ent.frozen
        if not frozen:                           # AngleDraw e Trans valem só no tick em que rodam
            ent.draw_angle, ent.draw_scale = None, None
            ent.trans, ent.alpha = None, None
        switches = 0
        while True:
            sd = self.statedef(ent.stateno)
            if sd is None:
                return
            changed = False
            ent.locals = {}
            for idx, c in enumerate(sd[1]):
                p = c["params"]
                if frozen and not self.ev(ent, p.get("ignorehitpause", "0"), 0):
                    continue
                if not self._triggered(ent, c):
                    continue
                pers = p.get("persistent")
                if pers is not None:
                    pv = int(self.ev(ent, pers, 1))
                    cnt = ent.persist.get(idx, 0)
                    ent.persist[idx] = cnt + 1
                    if pv == 0 and cnt > 0:
                        continue
                    if pv > 1 and cnt % pv:
                        continue
                res = self.exec(ent, c)
                if res == "changed":
                    changed = True
                    break
                if not ent.alive:
                    return
            if not changed:
                return
            switches += 1
            if switches > LOOP_MAX:
                # laço sem fim: a simulação para aqui (repetir o laço todo tick travava a tela)
                self.note(tr("ChangeState loop on the same tick"))
                self.max_ticks = min(self.max_ticks, self.tick + 1)
                return

    def _triggered(self, ent, c):
        for t in c["triggerall"]:
            if not self.ev(ent, t, 0):
                return False
        if not c["triggers"]:
            return False
        for k in sorted(c["triggers"]):
            if all(self.ev(ent, t, 0) for t in c["triggers"][k]):
                return True
        return False

    # ---- controladores
    def exec(self, ent: Entity, c: dict):
        """Roda um controlador; os eventos que ele gerar levam o estado e a posição dele no
        statedef (a linha do tempo do golpe volta ao código por aí)."""
        n0, st = len(self.events), ent.stateno
        res = self._exec(ent, c)
        for e in self.events[n0:]:
            if e["kind"] == "estado":
                e["src_state"] = st            # o ChangeState que levou ao estado novo
            else:
                e.setdefault("state", st)
            e.setdefault("ci", c.get("index"))
        return res

    def _target_id_ok(self, i: int) -> bool:
        """Filtro de id dos controladores de alvo e do NumTarget(id), como o getTarget do
        Ikemen: id < 0 vale para todos; senão só o alvo acertado por um HitDef com esse id."""
        return i < 0 or self.p2.hitid == i

    def _exec(self, ent: Entity, c: dict):
        typ = c.get("type", "")
        p = c["params"]
        g = lambda k, d=0: self.ev(ent, p[k], d) if k in p else d
        if isinstance(ent, P2Runner):
            r = self._p2_exec(ent, typ, p, g)
            if r is not None:
                return r
        if typ in TARGET_CTRLS and "id" in p and not self._target_id_ok(int(g("id", -1))):
            return                                # Ikemen getTarget(id): só alvos do HitDef de id
        if typ in CTRL_LOGGED:     # o que dá peso e leitura ao golpe (nota de qualidade)
            self.events.append({"t": self.tick, "who": ent.name, "kind": "ctrl", "value": typ,
                                "label": c.get("label", ""), "state": ent.stateno,
                                "root_state": (ent.root or ent).stateno,
                                "arg": p.get("value", "").strip() if typ == "playsnd" else
                                " ".join(p.get(k, "").strip().lower() for k in ("flag", "flag2", "flag3"))
                                if typ == "assertspecial" else ""})
            if typ == "playsnd":
                self.events[-1]["snd"] = self._sound(ent, p.get("value", ""))
                self.events[-1]["channel"] = int(g("channel", -1))
        if typ == "assertspecial" and (ent.root or ent) is self.p1 and "nobg" in " ".join(
                p.get(k, "").lower() for k in ("flag", "flag2", "flag3")):
            self._nobg_tick = self.tick       # cena: o cenário some (fundo preto) neste tick
        if typ == "reversaldef" and ent is self.p1 and "p1stateno" in p and ent.time >= REVERSAL_T                 and self.p2.state == "stand" and not self._reversed and self._reversal_touch(ent):
            # o boneco não ataca: no contra-ataque ele "ataca" no tick REVERSAL_T da janela, perto,
            # e o contra dispara (sem isso a simulação via só a pose de guarda e nunca o contra). No
            # MUGEN o ReversalDef só pega golpe cuja Clsn1 toca uma Clsn1 de quem contra-ataca:
            # guarda sem Clsn1 nunca dispara
            self.events.append({"t": self.tick, "who": ent.name, "kind": "ctrl", "value": "reversal",
                                "label": tr("dummy attacked: counter triggered"), "state": ent.stateno,
                                "root_state": ent.stateno, "arg": ""})
            pt = [x.strip() for x in p.get("pausetime", "0,0").split(",")]
            self.p2.shake = int(self.ev(ent, pt[-1], 0)) if pt[-1] else 0
            # uma vez só: com p1stateno = o próprio estado o tempo voltava a 0 e o boneco
            # "atacava" de novo a cada REVERSAL_T ticks, sem o golpe nunca acabar
            self._reversed = True
            self.enter(ent, int(g("p1stateno")))
            return "changed"
        if typ in ("changestate", "selfstate"):
            v = int(g("value", ent.stateno))
            if "ctrl" in p:
                ent.ctrl = int(g("ctrl"))
            self.enter(ent, v)
            if "anim" in p:
                ent.anim, ent.anim_t = int(g("anim")), 0
            return "changed"
        if typ in ("changeanim", "changeanim2"):   # ChangeAnim2: anim do dono do estado (o mesmo char aqui)
            new = int(g("value", ent.anim))
            if ent.frozen and (new != ent.anim or "elem" in p):
                ent.hold_anim = True
            ent.anim, ent.anim_t = new, int(g("elem", 1)) - 1
            if ent.anim_t > 0:
                st, _ = ent.starts()
                ent.anim_t = st[min(len(st) - 1, ent.anim_t)]
            return
        if typ == "velset":
            if "x" in p:
                ent.vel[0] = float(g("x"))
            if "y" in p:
                ent.vel[1] = float(g("y"))
            return
        if typ == "veladd":
            ent.vel[0] += float(g("x"))
            ent.vel[1] += float(g("y"))
            return
        if typ == "velmul":
            ent.vel[0] *= float(g("x", 1))
            ent.vel[1] *= float(g("y", 1))
            return
        if typ == "posadd":
            ent.pos[0] += float(g("x")) * ent.facing
            ent.pos[1] += float(g("y"))
            return
        if typ == "posset":
            if "x" in p:
                ent.pos[0] = float(g("x")) + self.origin_x
            if "y" in p:
                ent.pos[1] = float(g("y"))
            return
        if typ == "turn":
            self.set_facing(ent, -ent.facing)
            return
        if typ == "ctrlset":
            ent.ctrl = int(g("value"))
            return
        if typ == "posfreeze":                    # só neste tick: a posição não anda (a velocidade sim)
            ent.pos_frozen = bool(self.ev(ent, p.get("value", "1"), 1))
            return
        if typ == "attackmulset":
            (ent.root or ent).attackmul = float(g("value", 1))
            return
        if typ in ("targetlifeadd", "lifeadd"):
            if typ == "lifeadd":
                return                            # a vida do P1 não entra na conta
            # char MUGEN: curar (valor positivo) é sempre absolute (Ikemen, targetLifeAdd)
            v = int(g("value"))
            dealt = self._deal(-v, absolute=bool(g("absolute", 0)) or v > 0 and not self.ikemenver)
            v = -dealt
            self.p2.life = max(0, self.p2.life + v)
            self.events.append({"t": self.tick, "who": ent.name, "kind": "vida", "label": c["label"],
                                "state": ent.stateno, "root_state": (ent.root or ent).stateno,
                                "dealt": -v, "life": self.p2.life, "p1power": 0, "p2power": 0})
            return
        if typ == "screenbound":                  # vale só neste tick (o motor religa a cada tick)
            mc = self.evpair(ent, p.get("movecamera", "0,0"))
            ent.screenbound = (bool(self.ev(ent, p.get("value", "0"), 0)), bool(mc[0]))
            return
        if typ == "playerpush":                   # vale só neste tick (o motor religa a cada tick)
            ent.push_off = not self.ev(ent, p.get("value", "1"), 1)
            return
        if typ == "statetypeset":                 # sai do chão / fica no ar (física e tipo)
            for key, attr in (("statetype", "statetype"), ("movetype", "movetype"),
                              ("physics", "physics")):
                v = p.get(key, "").strip().upper()[:1]
                if v:
                    setattr(ent, attr, v)
            return
        if typ in ("parentvarset", "parentvaradd"):
            # mesma conta do VarSet/VarAdd, nas variáveis do pai (sem pai: o MUGEN ignora)
            if ent.parent is not None:
                self._set_vars(ent, ent.parent, p, typ == "parentvaradd")
            return
        if typ == "varrangeset":
            # var(first)..var(last) = value, ou fvar com fvalue (padrões 0 e 59 / 39)
            fl = "fvalue" in p
            d = ent.fvars if fl else ent.vars
            val = g("fvalue" if fl else "value")
            lo, hi = int(g("first", 0)), int(g("last", 39 if fl else 59))
            for n in range(lo, hi + 1):
                d[n] = val if fl else int(val)
            return
        if typ in ("varset", "varadd"):
            self._set_vars(ent, ent, p, typ == "varadd")
            return
        if typ == "varrandom":
            n = int(g("v"))
            lo, hi = self.evpair(ent, p.get("range", "0,1000"), (0, 1000))
            ent.vars[n] = int(self._rng.integers(int(lo), int(hi) + 1))
            return
        if typ == "sprpriority":
            ent.sprpriority = int(g("value"))
            return
        if typ == "assertspecial":
            flags = " ".join(p.get(k, "") for k in ("flag", "flag2", "flag3")).lower()
            if "invisible" in flags:
                ent.invisible_until = self.tick
            return
        if typ == "palfx":
            add = self.ev_triple(ent, p.get("add", "0,0,0"))
            mul = self.ev_triple(ent, p.get("mul", "256,256,256"), 256)
            ent.palfx = {"add": add, "mul": mul, "until": self.tick + int(g("time", 1))}
            return
        if typ in ("trans",):
            ent.trans = p.get("trans", "none").lower()
            ent.alpha = self.evpair(ent, p["alpha"], (256, 256)) if "alpha" in p else None
            return
        if typ == "angleset":
            ent.angle = float(g("value", ent.angle))
            return
        if typ == "angledraw":
            ent.draw_angle = float(g("value")) if "value" in p else ent.angle
            if "scale" in p:
                sx, sy = self.evpair(ent, p["scale"], (1, 1))
                ent.draw_scale = (sx, sy)
            return
        if typ == "explod":
            self.make_explod(ent, c)
            return
        if typ == "helper":
            self.make_helper(ent, c)
            return
        if typ == "destroyself":
            ent.alive = False
            return
        if typ == "modifyexplod":
            self.modify_explod(ent, c)
            return
        if typ == "explodbindtime":
            i, v = int(g("id", -1)), int(g("time", g("value", 0)))
            owner = ent.root or ent
            for e in self.live_explods:
                if e.alive and (e.owner.root or e.owner) is owner and (i < 0 or e.id == i):
                    e.bindtime = v if v < 0 else e.t + v
            return
        if typ == "width":                        # só neste tick: soma à caixa de tamanho (empurrão)
            if "player" in p or "value" in p:
                f, b = self.evpair(ent, p.get("player", p.get("value")), (0, 0))
                ent.width_player = (float(f), float(b))
            return
        if typ == "hitadd":
            (ent.root or ent).hitcount += int(g("value"))
            return
        if typ == "angleadd":
            ent.angle += float(g("value"))
            return
        if typ == "anglemul":
            ent.angle *= float(g("value", 1))
            return
        if typ == "powerset":
            (ent.root or ent).power = int(g("value"))
            return
        if typ == "removeexplod":
            i = int(g("id", -1))
            for e in self.live_explods:
                if e.owner is ent and (i < 0 or e.id == i):
                    e.alive = False
            return
        if typ in ("bindtoroot", "bindtoparent"):
            tgt = ent.root if typ == "bindtoroot" else ent.parent
            if tgt is not None:
                ox, oy = self.evpair(ent, p.get("pos", "0,0"))
                ent.pos = [tgt.pos[0] + ox * tgt.facing, tgt.pos[1] + oy]
                f = int(g("facing", 0))
                if f:
                    ent.facing = tgt.facing * (1 if f > 0 else -1)
            return
        if typ == "hitdef":
            ent.hitdef = {"params": p, "label": c["label"], "t": self.tick, "index": c.get("index")}
            if self.ikemenver and "air.juggle" in p:   # Ikemen: air.juggle troca o juggle do estado
                ent.juggle = int(g("air.juggle"))
            ent.hitdef_hit = False
            return
        if typ in ("superpause", "pause"):
            self.pause = int(g("time", 30))
            self.pause_owner = ent
            mt = int(g("movetime", 0))
            ent.pausemove = mt
            # helpers, explods e projéteis andam na pausa pelo supermovetime (SuperPause) ou
            # pelo pausemovetime (Pause), cada um na sua (Ikemen pauseStatus)
            self.pause_kind = "super" if typ == "superpause" else "pause"
            for o in [*self.helpers, *self.live_explods, *self.projs]:
                mts = getattr(o, "movetimes", None)
                if o is not ent and mts:
                    v = mts.get(self.pause_kind, 0)
                    o.pausemove = 10 ** 6 if v < 0 else v
            self.events.append({"t": self.tick, "who": ent.name, "kind": "pausa", "value": self.pause})
            if "poweradd" in p:
                (ent.root or ent).power += int(g("poweradd"))
            return
        if typ == "poweradd":
            (ent.root or ent).power += int(g("value"))
            return
        if typ == "gravity":
            ent.vel[1] += self.const("movement.yaccel")
            return
        if typ == "targetbind":
            # Ikemen targetBind: o alvo fica em pos do atacante + pos x pelo lado dele, a cada
            # tick por `time` ticks (padrão 1), já com o atacante na posição nova do tick
            ox, oy = self.evpair(ent, p.get("pos", "0,0"))
            self.p2.bind = {"to": ent, "x": float(ox), "y": float(oy), "time": int(g("time", 1))}
            return
        if typ == "bindtotarget":
            # o atacante vai ao alvo (pos x pelo lado do alvo) e o prende onde está
            d = self.p2
            ox, oy = self.evpair(ent, p.get("pos", "0,0"))
            ent.pos = [d.pos[0] + float(ox) * d.facing, d.pos[1] + float(oy)]
            d.bind = {"to": ent, "x": (d.pos[0] - ent.pos[0]) * ent.facing,
                      "y": d.pos[1] - ent.pos[1], "time": int(g("time", 1))}
            return
        if typ == "targetstate":
            # o P2 vai para um estado do atacante (roda já neste tick, depois do P1) ou volta
            # aos dele (5000-5199, 0...)
            self.p2_enter(int(g("value", 0)), now=True)
            return
        if typ in ("targetvelset", "targetveladd"):
            # Ikemen: t.vel[0] = x na velocidade local do alvo (positivo = para a frente dele)
            d, add = self.p2, typ == "targetveladd"
            if "x" in p:
                d.vel[0] = d.vel[0] * add + float(g("x")) * d.facing
            if "y" in p:
                d.vel[1] = d.vel[1] * add + float(g("y"))
            return
        if typ == "targetfacing":                 # value > 0: olha para o mesmo lado do atacante
            v = int(g("value", 0))
            if v:
                self.p2.facing = ent.facing * (1 if v > 0 else -1)
            return
        if typ == "projectile":
            self.make_projectile(ent, c)
            return
        if typ == "zssloopcut":                   # marcador do ZSS rebaixado: o laço passou do limite
            self.note(tr("ZSS loop cut at {0} turns").format(p.get("turns", "?")))
            return
        if typ in CTRL_IGNORED or typ.startswith("snd"):
            return
        self.note(tr("controller {0} not simulated").format(typ))

    # ---- o P2 num estado do atacante
    GETHIT = {5000: ("hit", "shake"), 5001: ("hit", "slide"), 5010: ("hit", "shake"),
              5011: ("hit", "slide"), 5020: ("air", "fly"), 5030: ("air", "fly"),
              5035: ("air", "fly"), 5040: ("air", "recover"), 5050: ("fall", "falling"),
              5070: ("fall", "falling"), 5071: ("fall", "falling"), 5080: ("down", "lie"),
              5081: ("down", "lie"), 5100: ("down", "bounce"), 5101: ("down", "bounce2"),
              5110: ("down", "lie"), 5120: ("down", "getup"), 5150: ("down", "lie")}

    def p2_enter(self, v: int, now: bool) -> None:
        """Põe o P2 no estado v do atacante; os estados comuns (parado e os de apanhar) voltam
        ao boneco. `now`: roda já no passo do P2 deste tick (TargetState, que vem do P1)."""
        d = self.p2
        if v in self.GETHIT or v < 200 or self.statedef(v) is None:
            if d.custom or self.p2_run is not None:
                self.p2_leave(v)
            return
        r = self.p2_run
        if r is None:
            r = self.p2_run = P2Runner(self, "P2", v)
            r.pid, r.root, r.parent = P2_ID, None, None
            r.pos, r.facing = list(d.pos), d.facing
            r.vel = [d.vel[0] * d.facing, d.vel[1]]
            r.anim = 0
        r.frozen = False
        # o estado começa (Statedef: velset, anim...) no passo do P2, depois do bind que o P1
        # aplica no fim do tick dele: já neste tick (TargetState vem antes dele) ou no seguinte
        # (p2stateno: o acerto vem depois)
        r.pending = v
        r.stateno = v
        d.custom = v
        d.ctrl = False

    def p2_leave(self, v: int, ctrl: bool = False) -> None:
        """SelfState/ChangeState para um estado comum: o boneco segue dali (5050 cai, 5110
        deitado, 0 parado...) com a posição e a velocidade que o estado deixou."""
        d = self.p2
        d.custom = 0
        self.p2_run = None
        state, sub = self.GETHIT.get(v, ("stand", ""))
        if state == "stand":
            d.state, d.sub, d.ctrl = "stand", "", True
            self._recover(d)
            if d.pos[1] < 0:                       # no ar: cai até o chão como num pulo
                d.state, d.sub, d.ctrl = "air", "recover", True
            return
        d.state, d.sub, d.st = state, sub, (1 if sub in ("fly", "falling", "recover", "slide") else 0)
        if state == "fall":
            d.fall = True
        if sub == "shake" and d.shake <= 0:
            d.sub = "slide"
        d.ctrl = False

    def p2_gethitvar(self, key) -> float:
        d = self.p2
        key = str(key or "").lower()
        # Ikemen: GetHitVar(xvel) é o ghv.xvel no mundo (velocidade x × -facing do atacante);
        # o HitVel X é que vem relativo ao lado do P2
        return {"xvel": d.hitvel[0], "yvel": d.hitvel[1], "yaccel": d.yaccel,
                "animtype": d.animtype, "groundtype": d.groundtype, "airtype": d.airtype,
                "type": d.airtype if d.stype() == "A" else d.groundtype,
                "damage": d.hit_damage, "id": d.hitid, "chainid": d.hitid,
                "fall": int(d.fall), "fall.yvel": d.fallvel[1],
                "fall.xvel": d.fallvel[0] if d.fallvel[0] is not None else 0,
                "hittime": d.hittime, "hitshaketime": d.shake, "slidetime": d.slidetime,
                "ctrltime": d.ctrltime, "isbound": int(bool(d.bind)), "hitcount": d.combo,
                "fall.recover": 0}.get(key, 0)

    def _p2_exec(self, r: "P2Runner", typ: str, p: dict, g):
        """Controladores que só valem para quem apanha, e as trocas de estado do P2."""
        d = self.p2
        if typ in ("selfstate", "changestate"):
            v = int(g("value", r.stateno))
            if typ == "selfstate" or v in self.GETHIT or v < 200:
                self._p2_sync_back(r)
                self.p2_leave(v, ctrl=bool(g("ctrl", 0)))
                r.alive = False                    # para o resto do estado neste tick
                return "left"
            return None
        if typ == "hitfallvel":
            if d.fallvel[0] is not None:
                r.vel[0] = d.fallvel[0]
            r.vel[1] = d.fallvel[1]
            return False
        if typ == "hitfallset":
            v = int(g("value", -1))
            if v >= 0:
                d.fall = bool(v)
            if "xvel" in p or "yvel" in p:
                d.fallvel = (float(g("xvel")) if "xvel" in p else d.fallvel[0],
                             float(g("yvel")) if "yvel" in p else d.fallvel[1])
            return False
        if typ == "hitvelset":
            if int(g("x", 1)):
                r.vel[0] = d.hitvel[0] * d.facing
            if int(g("y", 1)):
                r.vel[1] = d.hitvel[1]
            return False
        if typ in ("hitfalldamage", "fallenvshake"):
            return False
        if typ in ("hitdef", "helper", "projectile", "superpause", "pause", "targetbind",
                   "targetstate", "bindtotarget"):
            return False                       # o P2 não ataca nem segura ninguém aqui
        return None

    def _p2_sync_back(self, r: "P2Runner") -> None:
        d = self.p2
        d.pos, d.facing = list(r.pos), r.facing
        d.vel = [r.vel[0] * r.facing, r.vel[1]]

    def step_p2_run(self) -> None:
        """Um tick do P2 no estado do atacante: roda os controladores (o P1 é o oponente) e
        anda pela física do Statedef; os tempos do acerto continuam correndo."""
        r, d = self.p2_run, self.p2
        if r.started and getattr(r, "pending", None) is None:
            r.time += 1
            r.anim_t += 1
        r.started = True
        r.frozen, r.push_off, r.pos_frozen = False, False, False
        r.screenbound = (True, True)
        r.pos, r.facing = list(d.pos), d.facing
        r.vel = [d.vel[0] * d.facing, d.vel[1]]
        if getattr(r, "pending", None) is not None:
            v, r.pending = r.pending, None
            self.enter(r, v)
            r.hold_time = r.hold_anim = False
        bound = d.bind is not None and d.bind["time"] != 0
        self.run_entity(r)
        if self.p2_run is not r:
            return
        if not bound and not r.pos_frozen:
            r.pos[0] += r.vel[0] * r.facing
            r.pos[1] += r.vel[1]
        if r.physics == "A":
            r.vel[1] += self.const("movement.yaccel") or 0.44
            if r.pos[1] >= 0 and r.vel[1] > 0:
                self._p2_sync_back(r)
                self.p2_leave(52)
                d.pos[1], d.vel = 0.0, [0.0, 0.0]
                return
        elif r.physics in ("S", "C"):
            r.vel[0] *= self.const("movement.stand.friction") or 0.85
            if abs(r.vel[0]) < self.lc_ratio:
                r.vel[0] = 0.0
        self._p2_sync_back(r)
        self._tick_hit_timers(d)

    def _apply_bind(self) -> None:
        """TargetBind/BindToTarget: o P2 vai para perto de quem o prende (e copia a velocidade
        dele), antes de andar; o tempo do bind desce no fim do tick."""
        d, b = self.p2, self.p2.bind
        if b is None or b["time"] == 0:
            return
        to = b["to"]
        d.pos = [to.pos[0] + to.facing * b["x"], to.pos[1] + b["y"]]
        d.vel = [to.vel[0] * to.facing, to.vel[1]]

    def _set_vars(self, ent: Entity, dest: Entity, p: dict, add: bool) -> None:
        """VarSet/VarAdd (e ParentVar*): as expressões valem para `ent`, o resultado vai para as
        variáveis de `dest`. var e sysvar guardam inteiro."""
        def put(d, n, val, whole):
            val = int(val) if whole else val
            d[n] = d.get(n, 0) + val if add else val
        for k, v in p.items():
            m = re.fullmatch(r"((?:sys)?f?var|zsslocal)\((\d+)\)", k)
            if m:
                # sysvar(n) também: o laço finito "ChangeState para si mesmo até sysvar(3) = 5"
                # nunca acabava sem ele (issue #6)
                d = {"fvar": dest.fvars, "var": dest.vars, "sysvar": dest.sysvars,
                     "sysfvar": dest.sysfvars, "zsslocal": dest.locals}[m.group(1)]
                put(d, int(m.group(2)), self.ev(ent, v, 0), m.group(1) in ("var", "sysvar"))
        if "v" in p or "fv" in p:
            fl = "fv" in p
            n = int(self.ev(ent, p["fv" if fl else "v"], 0))
            put(dest.fvars if fl else dest.vars, n, self.ev(ent, p.get("value", "0"), 0), not fl)

    def ev_triple(self, ent, v, default=0):
        parts = _split_args(v)
        out = [self.ev(ent, x, default) if x else default for x in parts[:3]]
        return tuple(out + [default] * (3 - len(out)))

    def make_explod(self, ent: Entity, c):
        p = c["params"]
        g = lambda k, d=0: self.ev(ent, p[k], d) if k in p else d
        anim = int(g("anim", -1)) if not p.get("anim", "").upper().startswith("F") else -1
        if anim < 0:
            self.note(tr("Explod with a fightfx anim (F...) not drawn"))
            return
        if len(self.live_explods) >= EXPLOD_MAX:
            self.note(tr("Explod limit reached ({0})").format(EXPLOD_MAX))
            return
        x, y, facing, move_f, bind, space, ox, oy = self._explod_place(ent, p, g)
        sx, sy = self.evpair(ent, p.get("scale", "1,1"), (1, 1))
        vx, vy = self.evpair(ent, p.get("vel", "0,0"))
        axx, ayy = self.evpair(ent, p.get("accel", "0,0"))
        e = Explod(anim=anim, pos=[x, y], facing=facing, vfacing=int(g("vfacing", 1)) or 1,
                   scale=(sx, sy), angle=float(g("angle", 0)), spr=int(g("sprpriority", 0)),
                   trans=p.get("trans", "").lower() or None, removetime=int(g("removetime", -2)),
                   bindtime=int(g("bindtime", 0)), bind_to=bind, offset=(ox, oy), vel=(vx, vy),
                   accel=(axx, ayy), id=int(g("id", -1)), owner=ent, space=space,
                   label=c["label"], removeongethit=bool(g("removeongethit", 0)), born=self.tick,
                   pausemove=self._movetime(g),
                   # faísca: nasce do acerto (movehit/movecontact, ou o contador de acertos que
                   # os modelos do repertório usam: hitcount > var(1))
                   on_hit=any(re.search(r"(?i)move(hit|contact)|\bhitcount\b", t)
                              for ts in [c["triggerall"], *c["triggers"].values()] for t in ts),
                   alpha=self.evpair(ent, p["alpha"], (256, 256)) if "alpha" in p else None)
        e.movetimes = {"super": int(g("supermovetime", 0)), "pause": int(g("pausemovetime", 0))}
        e.move_facing = move_f
        if "friction" in p:
            fx, fy = self.evpair(ent, p["friction"], (1, 1))
            e.friction = (float(fx), float(fy))
        self.explods.append(e)
        self.live_explods.append(e)
        self.events.append({"t": self.tick, "who": ent.name, "kind": "explod", "value": anim,
                            "label": c["label"]})

    # parâmetros que um char MUGEN só muda no ModifyExplod quando também dá o postype
    MODIFY_LOCKED = {"pos", "random", "vel", "velocity", "accel", "facing", "vfacing", "space"}

    def modify_explod(self, ent: Entity, c) -> None:
        """ModifyExplod como o Ikemen: num char MUGEN, pos/vel/accel/facing só mudam com postype
        (e aí o explod é reposicionado do zero: velocidade e aceleração zeram, bindtime 0
        vira 1); scale, removetime, bindtime, sprpriority, trans, angle e anim valem sempre."""
        p = c["params"]
        g = lambda k, d=0: self.ev(ent, p[k], d) if k in p else d
        eid = int(g("id", -1))
        owner = ent.root or ent
        exps = [e for e in self.live_explods if e.alive and (e.owner.root or e.owner) is owner
                and (eid < 0 or e.id == eid)]
        has_pt = "postype" in p
        locked = not self.ikemenver and not has_pt
        for e in exps:
            if has_pt:
                x, y, facing, move_f, bind, space, ox, oy = self._explod_place(ent, p, g)
                e.pos, e.facing, e.move_facing, e.bind_to, e.space = [x, y], facing, move_f, bind, space
                e.offset = (ox, oy)
                e.vel, e.accel = (0.0, 0.0), (0.0, 0.0)
                if e.bindtime == 0:
                    e.bindtime = e.t + 1
            elif not locked and "pos" in p:            # char do Ikemen: pos relativa, sem virar
                ox, oy = self.evpair(ent, p["pos"], (0, 0))
                e.pos = [e.pos[0] - e.offset[0] + ox, e.pos[1] - e.offset[1] + oy]
                e.offset = (ox, oy)
            if not locked:
                if "vel" in p or "velocity" in p:
                    vx, vy = self.evpair(ent, p.get("vel", p.get("velocity")), (0, 0))
                    e.vel = (float(vx), float(vy))
                if "accel" in p:
                    ax, ay = self.evpair(ent, p["accel"], (0, 0))
                    e.accel = (float(ax), float(ay))
                if "facing" in p:
                    e.facing = e.move_facing * (-1 if int(g("facing", 1)) < 0 else 1)
                if "vfacing" in p:
                    e.vfacing = -1 if int(g("vfacing", 1)) < 0 else 1
            if "scale" in p:
                e.scale = tuple(float(v) for v in self.evpair(ent, p["scale"], (1, 1)))
            if "removetime" in p:                       # conta desde o nascimento (e.time)
                e.removetime = int(g("removetime", -2))
            if "bindtime" in p:                         # relógio que desce a partir de agora
                v = int(g("bindtime", 0))
                e.bindtime = v if v < 0 else e.t + v
            if "sprpriority" in p:
                e.spr = int(g("sprpriority", 0))
            if "trans" in p:
                e.trans = p["trans"].strip().lower() or None
            if "alpha" in p:
                e.alpha = self.evpair(ent, p["alpha"], (256, 256))
            if "angle" in p:
                e.angle = float(g("angle", 0))
            if "friction" in p:
                fx, fy = self.evpair(ent, p["friction"], (1, 1))
                e.friction = (float(fx), float(fy))
            if "anim" in p and not p["anim"].strip().upper().startswith("F"):
                a = int(g("anim", e.anim))
                if a != e.anim:
                    e.anim, e.anim_t0 = a, e.t

    def _explod_place(self, ent: Entity, p: dict, g) -> tuple:
        """(x, y, facing, lado do movimento, preso a, space, offset x, offset y) do Explod pelo
        postype, como o Explod.setPos do Ikemen."""
        ox, oy = self.evpair(ent, p.get("pos", "0,0"))
        postype = p.get("postype", "p1").lower()
        rel = -1 if int(g("facing", 1)) < 0 else 1
        space = p.get("space", "").strip().lower()
        if space not in ("screen", "stage"):          # sem space: front/back/left/right são da tela
            space = "screen" if postype in ("left", "right", "front", "back") else "stage"
        # Ikemen Explod.setPos: p1/p2 partem do char e o x vira pelo lado dele (guardado assim:
        # preso, não acompanha uma virada depois); front/back viram left/right pelo lado do
        # dono (back também inverte o x e herda o lado dele); o lado do movimento é o do char
        # (p1/p2/back) ou 1, e o desenho soma o facing do parâmetro
        move_f, bind = 1, None
        if postype in ("p1", "p2"):
            base = self.p2 if postype == "p2" else ent
            move_f = base.facing
            ox *= move_f
            x, y = base.pos[0] + ox, base.pos[1] + oy
            if space == "stage":
                bind = base
        else:
            pt = postype
            if pt in ("front", "back"):
                if pt == "back":
                    move_f = ent.facing
                right = ent.facing > 0 and pt == "front" or ent.facing < 0 and pt == "back"
                if right and pt == "back":
                    ox = -ox
                pt = "right" if right else "left"
            edge = {"left": -160, "right": 160}.get(pt, -160 if space == "screen" else None)
            if edge is None:                          # none no palco: posição absoluta
                x, y = self.origin_x + ox, oy
            else:
                # tela: x a partir da borda; o simulador usa a câmera centrada no começo
                x, y = self.origin_x + edge + ox, oy - 240 + 200
        return x, y, move_f * rel, move_f, bind, space, ox, oy

    def _movetime(self, g) -> int:
        """Ticks que anda na pausa em curso (ou na próxima, se não há): supermovetime na
        SuperPause, pausemovetime na Pause; -1 = a pausa toda."""
        kind = getattr(self, "pause_kind", "super")
        v = int(g("supermovetime" if kind == "super" else "pausemovetime", 0))
        return 10 ** 6 if v < 0 else v

    def make_projectile(self, ent: Entity, c):
        """Controlador Projectile como o Ikemen (Projectile.update/tick): anda depois dos
        personagens e antes da colisão, congela no próprio hitpause, acerta `projhits` vezes
        com `projmisstime` entre um e outro e sai pela animação de acerto/remoção, pelo
        `projremovetime` ou pelas bordas da tela."""
        p = c["params"]
        g = lambda k, d=0: self.ev(ent, p[k], d) if k in p else d
        pair = lambda k, d: tuple(float(x) for x in self.evpair(ent, p[k], d)) if k in p else d
        anim = int(g("projanim", 0))
        ox, oy = self.evpair(ent, p.get("offset", "0,0"))
        postype = p.get("postype", "p1").lower()
        qx, qy, qf = self.spawn_pos(ent, postype, float(ox), float(oy), float(g("facing", 1)), True)
        q = Entity(self, f"proj {int(g('projid', 0))}", 0)
        q.parent, q.root = ent, None          # o acerto do projétil não dá MoveHit ao dono
        q.pid = ent.pid
        q.facing = qf
        q.pos = [qx, qy]
        q.vel = list(pair("velocity", (0.0, 0.0)))
        q.anim, q.anim_t = anim, 0
        sc = pair("projscale", (1.0, 1.0))
        q.scale = (sc[0], sc[1])
        q.sprpriority = int(g("projsprpriority", 3))
        q.started = True
        hit_anim = int(g("projhitanim", -1))
        q.proj = {"id": int(g("projid", 0)), "hits": int(g("projhits", 1)),
                  "misstime": int(g("projmisstime", 0)), "miss": 0,
                  "removetime": int(g("projremovetime", -1)), "remove": bool(g("projremove", 1)),
                  "hitanim": hit_anim, "remanim": int(g("projremanim", hit_anim)),
                  "accel": pair("accel", (0.0, 0.0)), "velmul": pair("velmul", (1.0, 1.0)),
                  "remvel": pair("remvelocity", (0.0, 0.0)),
                  "edge": float(g("projedgebound", 40)), "stage": float(g("projstagebound", 40)),
                  "height": pair("projheightbound", (-240.0, 1.0)),
                  "status": "active", "contact": False, "hitpause": 0,
}
        q.pausemove = self._movetime(g)
        q.movetimes = {"super": int(g("supermovetime", 0)), "pause": int(g("pausemovetime", 0))}
        q.hitdef = {"params": p, "label": c["label"], "t": self.tick, "index": c.get("index")}
        q.hitdef_hit = False
        self.projs.append(q)

    def _proj_leaves(self, q: Entity) -> bool:
        """Saiu pelas bordas: da tela (projedgebound), do palco (projstagebound) ou da altura."""
        pr = q.proj
        x, vx = q.pos[0], q.vel[0] * q.facing
        if not self.xmin - self.screen_lr - pr["edge"] <= x <= self.xmax + self.screen_lr + pr["edge"]:
            return True
        # sem palco: as bordas do palco ficam longe (o projstagebound não corta antes da tela)
        if vx < 0 and x < self.stage_left - self.cam_half - pr["stage"] or \
                vx > 0 and x > self.stage_right + self.cam_half + pr["stage"]:
            return True
        lo, hi = pr["height"]
        return q.vel[1] > 0 and q.pos[1] > hi or q.vel[1] < 0 and q.pos[1] < lo

    def step_projectiles(self, paused: bool) -> None:
        """Projectile.update: remoção, troca para a animação de acerto/remoção e o movimento."""
        for q in self.projs:
            if not q.alive:
                continue
            pr = q.proj
            q.frozen = paused and q.pausemove <= 0 or pr["hitpause"] > 0
            if q.frozen:
                continue
            if pr["status"] == "active" and (pr["removetime"] == 0 or self._proj_leaves(q)):
                pr["status"] = "rem"
            if pr["status"] != "active" and not pr.get("done"):
                pr["done"] = True
                anim = pr["hitanim"] if pr["status"] == "hit" else pr["remanim"]
                if anim < 0 or self.char.anims.get(anim) is None:
                    q.alive = False
                    continue
                q.anim, q.anim_t = anim, 0
                q.vel, pr["accel"], pr["velmul"] = list(pr["remvel"]), (0.0, 0.0), (1.0, 1.0)
            if pr["status"] != "active" and q.animtime() == 0:
                q.alive = False
                continue
            q.pos[0] += q.vel[0] * q.facing
            q.pos[1] += q.vel[1]
            q.vel = [(q.vel[0] + pr["accel"][0]) * pr["velmul"][0],
                     (q.vel[1] + pr["accel"][1]) * pr["velmul"][1]]

    def tick_projectiles(self, paused: bool) -> None:
        """Projectile.tick: o contato gasta um acerto (sem acertos: animação de acerto), o
        projmisstime começa e os relógios andam fora da pausa e do hitpause."""
        for q in self.projs:
            if not q.alive:
                continue
            pr = q.proj
            if pr["contact"]:
                pr["contact"] = False
                pr["miss"] = max(0, pr["misstime"])
                pr["hits"] -= 1
                if pr["hits"] <= 0 and pr["remove"]:
                    pr["status"] = "hit"
            if paused and q.pausemove <= 0:
                continue
            if paused:
                q.pausemove -= 1
            if pr["hitpause"] > 0:
                pr["hitpause"] -= 1
                continue
            q.anim_t += 1
            if pr["removetime"] > 0:
                pr["removetime"] -= 1
            if pr["miss"] > 0:
                pr["miss"] -= 1
            # volta a poder acertar quando o misstime acaba e ainda tem acertos
            q.hitdef_hit = not (pr["status"] == "active" and pr["hits"] > 0 and pr["miss"] <= 0)
        self.projs = [q for q in self.projs if q.alive]

    def _proj_contact(self, ent: Entity, kind: str) -> None:
        """Contato de um projétil: congela o projétil (hitpause dele) e marca o dono
        (ProjHitTime/ProjContactTime/ProjGuardedTime)."""
        pr = ent.proj
        pr["contact"] = True
        pr["hitpause"], ent.hitpause = ent.hitpause, 0
        ent.parent.proj_contact = {"type": kind, "t": self.tick, "id": pr["id"]}

    def spawn_pos(self, ent: Entity, postype: str, ox: float, oy: float, facing_param: float,
                  proj: bool) -> tuple:
        """(x, y, facing) de um Helper ou Projectile como o Ikemen (helperPos): p1/p2 partem do
        char com o x pelo lado dele; front/back saem da borda da tela do lado do dono (x para
        dentro), left/right da borda; front/back/left/right/none usam o y absoluto. O facing
        do parâmetro vezes o do char (p1, p2, front/back; o projétil sempre pelo dono)."""
        f = -1 if facing_param < 0 else 1
        left, right = self.origin_x - 160, self.origin_x + 160
        if postype == "p2":
            d = self.p2
            return d.pos[0] + ox * d.facing, d.pos[1] + oy, f * (ent.facing if proj else d.facing)
        if postype in ("front", "back"):
            edge = right if (ent.facing > 0) == (postype == "front") else left
            return edge + (ox if ent.facing > 0 else -ox), oy, f * ent.facing
        if postype in ("left", "right"):
            return (left if postype == "left" else right) + ox, oy, f * (ent.facing if proj else 1)
        if postype == "none":
            return self.origin_x + ox, oy, f * (ent.facing if proj else 1)
        return ent.pos[0] + ox * ent.facing, ent.pos[1] + oy, f * ent.facing

    def make_helper(self, ent: Entity, c):
        p = c["params"]
        g = lambda k, d=0: self.ev(ent, p[k], d) if k in p else d
        ox, oy = self.evpair(ent, p.get("pos", "0,0"))
        postype = p.get("postype", "p1").lower()
        hx, hy, hf = self.spawn_pos(ent, postype, float(ox), float(oy), float(g("facing", 1)), False)
        root = ent.root or ent
        # o motor tem um teto de helpers vivos (MUGEN: 56 por jogador); sem ele, helper que cria
        # helper no 1º tick vira laço infinito (Kashimo 3100)
        if sum(1 for x in self.helpers if x.alive) >= HELPER_MAX:
            return
        h = Entity(self, f"helper {int(g('id', 0))}", int(g("stateno", 0)))
        h.is_helper = True
        h.screenbound = (False, False)
        h.helper_id = int(g("id", 0))
        self._next_pid = getattr(self, "_next_pid", P2_ID) + 1
        h.pid = self._next_pid
        h.parent, h.root = ent, root
        h.facing, h.pos = hf, [hx, hy]
        h.scale = (float(g("size.xscale", 1)), float(g("size.yscale", 1)))
        # o resto do size.* do Helper (size.head.pos, size.height…) vale no const() do helper
        h.size = {k: [float(v) for v in self.evpair(ent, p[k], (0, 0))[:len(_split_args(p[k]))]]
                  for k in p if k.startswith("size.")}
        h.size["size.xscale"], h.size["size.yscale"] = [h.scale[0]], [h.scale[1]]
        h.pausemove = self._movetime(g)
        h.movetimes = {"super": int(g("supermovetime", 0)), "pause": int(g("pausemovetime", 0))}
        h.anim = ent.anim
        h.screen = postype in ("left", "right", "front", "back")
        self.helpers.append(h)
        self.enter(h, h.stateno, first=True)
        if self.pause > 0 and h.pausemove <= 0:
            # nasceu parado na pausa (sem supermovetime/pausemovetime): o Ikemen não roda o
            # estado de quem está parado, o Time = 0 dele roda no 1º tick depois da pausa (rodar
            # já aqui repetia o Time = 0 e o VarAdd que conta os acertos contava dobrado)
            h.born_run = -1
        else:
            self.run_entity(h)                   # o helper roda o 1º tick ao nascer (BindToRoot já vale)
            h.born_run = self.tick               # ... e não roda de novo no mesmo tick (step)
        self.events.append({"t": self.tick, "who": ent.name, "kind": "helper", "value": h.stateno,
                            "label": c["label"]})

    def _reversal_touch(self, ent: Entity) -> bool:
        """A Clsn1 de `ent` alcança o soco do boneco (do eixo dele até 0,8 da altura para a frente)?"""
        reach = 0.8 * (self.stand_h or 60)
        x2 = self.p2.pos[0]
        side = 1 if x2 >= ent.pos[0] else -1
        lo, hi = sorted((x2, x2 - side * reach))
        return any(b[0] <= hi and b[2] >= lo for b in self.boxes(ent, "1"))

    # ---- colisão e oponente
    def boxes(self, ent: Entity, which: str):
        f = ent.frame()
        if f is None:
            return []
        out = []
        for b in (f.clsn1 if which == "1" else f.clsn2):
            x0, x1 = sorted((b[0] * ent.facing, b[2] * ent.facing))
            y0, y1 = sorted((b[1], b[3]))             # a caixa pode vir escrita de baixo para cima
            sx, sy = ent.scale
            out.append((ent.pos[0] + x0 * sx, ent.pos[1] + y0 * sy, ent.pos[0] + x1 * sx, ent.pos[1] + y1 * sy))
        return out

    def p2_box(self):
        if self.p2_run is not None:               # no estado do atacante: as Clsn2 do quadro dele
            box = self.boxes(self.p2_run, "2")
            if box:
                return box
        d = self.p2
        clsn = self.p2_clsn
        if isinstance(clsn, dict):                # caixas por pose do oponente de verdade
            clsn = next((clsn[k] for k in p2_keys(d.state, d.pose) if clsn.get(k)), None)
        if d.state == "down" and not (isinstance(self.p2_clsn, dict) and self.p2_clsn.get("down")):
            return [(d.pos[0] - 26, d.pos[1] - 14, d.pos[0] + 26, d.pos[1])]
        if clsn:                                  # caixas de um oponente de verdade
            out = []
            for b in clsn:
                x0, x1 = sorted((b[0] * d.facing, b[2] * d.facing))
                y0, y1 = sorted((b[1], b[3]))
                out.append((d.pos[0] + x0, d.pos[1] + y0, d.pos[0] + x1, d.pos[1] + y1))
            return out
        h = self.stand_h * (0.6 if d.pose == "crouch" and d.pos[1] >= 0 else 0.95)
        return [(d.pos[0] - 14, d.pos[1] - h, d.pos[0] + 14, d.pos[1])]

    def dist_round(self, v: float) -> float:
        """WinMUGEN (mugenversion antes da 1.0) trunca as distâncias para inteiro."""
        return v if self.mugen1 else float(int(v))

    def body_dist_x(self, ent: Entity) -> float:
        """P2BodyDist X do Ikemen (bodyDistX): a distância menos a frente da caixa de tamanho
        de quem pergunta e a frente do P2 (a de trás se ele está de costas)."""
        d = self.p2
        dist = (d.pos[0] - ent.pos[0]) * ent.facing
        f1, _, _ = self._size(None, ent.statetype == "A")
        size2 = (self.p2_clsn or {}).get("size") if isinstance(self.p2_clsn, dict) else None
        f2, b2, _ = self._size(size2 or None, d.stype() == "A")
        facing_each_other = (dist >= 0) == (ent.facing != d.facing)
        return dist - f1 - (f2 if facing_each_other else b2)

    def _size(self, src, air: bool):
        """(front, back, height) da caixa de tamanho: constantes [Size] do char ou do oponente."""
        get = (lambda k, d: float(src.get(k, d))) if isinstance(src, dict) else (
            lambda k, d: float(self.const(f"size.{k}") or d))
        h = get("height", 60)
        if air:
            return get("air.front", 12), get("air.back", 12), h
        return get("ground.front", 16), get("ground.back", 15), h

    # ---- câmera e bordas da tela
    def _cam_tracked(self) -> list:
        """(x, velocidade x no mundo) de quem move a câmera: P1 e P2 (helpers não, como no
        Ikemen: só os jogadores ganham screenbound/movecamera por padrão)."""
        out = []
        p1 = self.p1
        if p1.screenbound[1]:
            out.append((p1.pos[0], 0.0 if p1.frozen else p1.vel[0] * p1.facing))
        r = self.p2_run
        if r is not None and r.alive and self.p2.custom:
            if r.screenbound[1]:
                out.append((r.pos[0], 0.0 if r.frozen else r.vel[0] * r.facing))
        else:
            out.append((self.p2.pos[0], self.p2.vel[0]))
        return out

    def step_camera(self) -> None:
        """Camera.action do Ikemen (sem zoom, sem palco): a tela segue quem passa da tensão
        (50 px da borda), andando aos poucos (5% por passo, 3 passos, ou a velocidade de quem
        puxa); depois xmin/xmax = tela - screenleft/right e os jogadores ficam presos nela
        (xScreenBound)."""
        tr_ = self._cam_tracked()
        if tr_:
            lx = min(tr_, key=lambda v: v[0])
            rx = max(tr_, key=lambda v: v[0])
            leftest, leftvel = lx
            rightest, rightvel = rx
            half, ten = self.cam_half, self.cam_tension
            old_l, old_r = self.cam_x - half, self.cam_x + half
            tl, tr = old_l, old_r
            if leftest < tl + ten:
                diff = tl - max(leftest - ten, self.stage_left)
                tl = max(leftest - ten, self.stage_left)
                tr = max(old_r - diff, min(rightest + ten, self.stage_right))
            elif rightest > tr - ten:
                diff = tr - min(rightest + ten, self.stage_right)
                tr = min(rightest + ten, self.stage_right)
                tl = min(old_l - diff, max(leftest - ten, self.stage_left))
            if tr - tl > 2 * half:                 # não cabe sem zoom: fica no meio dos dois
                r_left = max(tl + ten - leftest, 0)
                r_right = max(rightest - (tr - ten), 0)
                diff = (tr - tl) - 2 * half
                if r_left > r_right:
                    d2 = min(r_left - r_right, diff)
                    tr -= d2
                    diff -= d2
                elif r_right > r_left:
                    d2 = min(r_right - r_left, diff)
                    tl += d2
                    diff -= d2
                tl += diff / 2
                tr -= diff / 2
                sl = sr = self.screen_lr
                if leftest - tl < sl:
                    diff = min(sl - (leftest - tl), tl - self.stage_left)
                    if tr - rightest < sr:
                        diff -= min(sr - (tr - rightest), self.stage_right - tr)
                    tl, tr = tl - diff, tr - diff
                elif tr - rightest < sr:
                    diff = min(sr - (tr - rightest), self.stage_right - tr)
                    tl, tr = tl + diff, tr + diff
            new_l, new_r = old_l, old_r
            step = 0.1 * self.lc_ratio             # gameWidth / 3200
            for _ in range(3):
                new_l += (tl - new_l) * 0.05
                new_r += (tr - new_r) * 0.05
                dl, dr = tl - new_l, tr - new_r
                new_l = tl if abs(dl) <= step else new_l + (step if dl > 0 else -step)
                if 0 < new_l - old_l < rightvel:
                    new_l = min(old_l + rightvel, tl)
                elif leftvel < new_l - old_l < 0:
                    new_l = max(old_l + leftvel, tl)
                new_r = tr if abs(dr) <= step else new_r + (step if dr > 0 else -step)
                if 0 < new_r - old_r < rightvel:
                    new_r = min(old_r + rightvel, tr)
                elif leftvel < new_r - old_r < 0:
                    new_r = max(old_r + leftvel, tr)
            x = (new_l + new_r) / 2
            self.cam_x = math.ceil(x * 4 - 0.5) / 4    # sem zoom o Ikemen arredonda a 1/4 px
        self.xmin = self.cam_x - self.cam_half + self.screen_lr
        self.xmax = self.cam_x + self.cam_half - self.screen_lr
        self.screen_clamp()

    def screen_clamp(self) -> None:
        """xScreenBound: P1 e P2 dentro de xmin/xmax (o Ikemen faz no update de cada char, logo
        depois de andar, no empurrão e depois da câmera)."""
        lo, hi = self.xmin, self.xmax
        p1 = self.p1
        if p1.screenbound[0]:
            p1.pos[0] = min(max(p1.pos[0], lo), hi)
        for h in self.helpers:                    # helper com ScreenBound value = 1 neste tick
            if h.alive and h.screenbound[0]:
                h.pos[0] = min(max(h.pos[0], lo), hi)
        r = self.p2_run
        if r is not None and r.alive and self.p2.custom:
            if r.screenbound[0]:
                r.pos[0] = min(max(r.pos[0], lo), hi)
                self.p2.pos[0] = r.pos[0]
        else:
            self.p2.pos[0] = min(max(self.p2.pos[0], lo), hi)

    def edge_trigger(self, who, name: str) -> float:
        """BackEdgeDist, FrontEdgeDist (e Body), LeftEdge, RightEdge, ScreenPos X e CameraPos X
        pela câmera simulada (Ikemen: distância a xmin/xmax; Body tira o width edge e, em char
        MUGEN, 0,5 no ar e 1 deitado)."""
        left = self.cam_x - self.cam_half
        if name == "leftedge":
            return left - self.origin_x
        if name == "rightedge":
            return left + 2 * self.cam_half - self.origin_x
        if name == "camerapos x":
            return self.cam_x - self.origin_x
        if name == "screenpos x":
            return who.pos[0] - left
        back = name.startswith("back")
        ahead = (who.facing > 0) != back            # olhando para a borda direita?
        d = self.xmax - who.pos[0] if ahead else who.pos[0] - self.xmin
        if "body" in name and not self.ikemenver:
            st = who.stype() if isinstance(who, Dummy) else who.statetype
            d -= {"A": 0.5, "L": 1.0}.get(st, 0.0)
        return d

    def push_players(self):
        """Corpos não se atravessam (Ikemen pushDetection): se as caixas de tamanho ([Size],
        mais o Width) se sobrepõem, cada um anda metade da sobreposição para o seu lado (pesos
        iguais, pushfactor 1). O Ikemen só olha as caixas de tamanho (não as Clsn2), e o P2
        deitado tem a mesma caixa de em pé. PlayerPush{value: 0} desliga no tick. Só P1 × P2."""
        p1, d = self.p1, self.p2
        # preso (TargetBind): o Ikemen devolve o P2 à posição do bind depois do empurrão
        if getattr(p1, "push_off", False) or d.bind is not None and d.bind["time"] != 0:
            return
        f1, b1, h1 = self._size(None, p1.statetype == "A")
        wp = getattr(p1, "width_player", (0.0, 0.0))        # Width: soma à frente e às costas
        f1, b1 = f1 + wp[0], b1 + wp[1]
        size2 = (self.p2_clsn or {}).get("size") if isinstance(self.p2_clsn, dict) else None
        f2, b2, h2 = self._size(size2 or None, d.pos[1] < 0)
        # caixas no mundo (x à frente de cada um pelo lado em que olha)
        l1, r1 = sorted((p1.pos[0] - b1 * p1.facing, p1.pos[0] + f1 * p1.facing))
        l2, r2 = sorted((d.pos[0] - b2 * d.facing, d.pos[0] + f2 * d.facing))
        overlap = min(r1, r2) - max(l1, l2)
        if overlap <= 0 or p1.pos[1] - h1 > d.pos[1] or d.pos[1] - h2 > p1.pos[1]:
            return
        side = 1 if d.pos[0] >= p1.pos[0] else -1             # de que lado o P2 está
        p1.pos[0] -= side * overlap / 2
        d.pos[0] += side * overlap / 2

    def check_hits(self, ent: Entity):
        hd = ent.hitdef
        if not hd or ent.hitdef_hit or ent.hitpause:
            return
        if ent.proj is not None and (ent.proj["status"] != "active" or ent.proj["hits"] <= 0
                                     or ent.proj["miss"] > 0 or ent.proj["hitpause"] > 0):
            return
        p = hd["params"]
        flag = p.get("hitflag", "MAF").upper().replace("M", "HL")
        d = self.p2
        if d.state == "down" and d.sub == "getup":     # 5120: NotHitBy SCA
            return
        moving_h = d.movetype_h()
        in_hit = moving_h and d.state != "guardhit"
        # hitflag como o Ikemen (hittableByChar): a letra do StateType do P2 (H em pé, L
        # agachado, A no ar, D deitado), F se ele está caindo, "-" só quem não apanha, "+" só
        # quem apanha (fora da defesa)
        if {"S": "H", "C": "L", "A": "A", "L": "D"}[d.stype()] not in flag:
            return
        if in_hit and d.fall and "F" not in flag:
            return
        if "-" in flag and moving_h or "+" in flag and not in_hit:
            return
        if not self._chain_ok(ent, p):
            return
        if not self._juggle_ok(ent, p, in_hit and d.fall):
            return
        mine = self.boxes(ent, "1")
        if not mine:
            return
        theirs = self.p2_box()
        inter = [(max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
                 for a in mine for b in theirs
                 if a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]]
        if not inter:
            return
        contact = ((min(i[0] for i in inter) + max(i[2] for i in inter)) / 2,
                   (min(i[1] for i in inter) + max(i[3] for i in inter)) / 2)
        g = lambda k, dft=(0, 0): self.evpair(ent, p[k], dft) if k in p else dft
        if self._guards(d, p):
            return self._guarded(ent, hd, contact, g)
        dmg = self._damage(ent, p, g)
        d.life = max(0, d.life - dmg["dealt"])
        ent.hitdef_hit = True
        ent.movehit = ent.movecontact = 1
        ent.hit_now = True
        numhits = max(0, int(self.ev(ent, p.get("numhits", "1"), 1)))
        ent.hitcount += numhits
        # combo do Ikemen (getterInCombo): o P2 ainda em acerto (movetype H) e não defendendo
        d.combo = d.combo + numhits if in_hit else numhits
        d.hitid = int(self.ev(ent, p.get("id", "0"), 0))
        d.hit_damage = dmg["dealt"]
        d.animtype, d.groundtype, d.airtype = self._hit_types(ent, p, d.stype() == "A")
        if ent.root is not None:
            ent.root.movehit = ent.root.movecontact = 1
            ent.root.hit_now = True
        pt = g("pausetime", (0, 0))
        ent.hitpause = self._attacker_pause(int(pt[0]))
        air = d.stype() == "A"
        gv = g("air.velocity" if air else "ground.velocity", (0, 0))
        was_falling = d.fall and in_hit
        if not in_hit:
            d.fall = False                            # o fallflag só acumula dentro do combo
        flag_of = lambda k, dft: bool(self.ev(ent, p[k], 0)) if k in p else dft
        ground_fall = flag_of("fall", False)
        d.fall = d.fall or (flag_of("air.fall", ground_fall) if air else ground_fall)
        # velocidade no mundo (Ikemen: ghv.xvel = velocity.x * -facing do atacante)
        vel = [float(gv[0]) * -ent.facing, float(gv[1]) if len(gv) > 1 else 0.0]
        if not air and d.fall and vel[1] == 0:
            vel[1] = -0.001                           # o MUGEN faz isso para cair do chão
        d.shake = int(pt[1])
        # hittime do Ikemen: no ar (ou lançado) air.hittime (padrão 20), no chão ground.hittime
        # (padrão 0); char MUGEN ganha 1 tick (o Ikemen compensa a defesa um quadro mais cedo)
        key, dft = ("air.hittime", 20) if air or vel[1] else ("ground.hittime", 0)
        d.hittime = max(0, int(self.ev(ent, p[key], dft)) if key in p else dft)
        if not self.ikemenver and d.hittime > 0:
            d.hittime += 1
        d.slidetime = int(self.ev(ent, p["ground.slidetime"], 0)) if "ground.slidetime" in p else 0
        d.yaccel = float(self.ev(ent, p["yaccel"], 0.35)) if "yaccel" in p else 0.35 * self.lc_ratio
        fx = float(self.ev(ent, p["fall.xvelocity"], 0)) if "fall.xvelocity" in p else None
        d.fallvel = (fx, float(self.ev(ent, p["fall.yvelocity"], -4.5)) if "fall.yvelocity" in p
                     else -4.5 * self.lc_ratio)
        d.down_rt = int(self.const("data.liedown.time"))
        # 5000/5010/5020: treme parado; depois 5001 (recuo no chão) ou 5030 (voa com o hitvelset)
        d.hitvel, d.vel, d.sub, d.st = tuple(vel), [0.0, 0.0], "shake", 0
        d.state = "hit" if not air and not vel[1] and not d.fall else ("fall" if d.fall else "air")
        d.ctrl = False
        self._juggle_spend(ent, p, was_falling or d.fall)
        # p2facing: 1 vira o P2 para o atacante, -1 de costas; sem ele o acerto o vira de frente
        p2f = int(self.ev(ent, p["p2facing"], 0)) if "p2facing" in p else 0
        d.facing = ent.facing if p2f < 0 else -ent.facing
        if "p1facing" in p and int(self.ev(ent, p["p1facing"], 0)) < 0 and ent.proj is None:
            ent.facing = -ent.facing
        d.t = 0
        d.hits += numhits
        # p2stateno: o oponente passa a rodar o estado do atacante (o Time = 0 dele roda no
        # tick seguinte: o P2 já rodou este tick quando o acerto acontece)
        if p.get("p2stateno", "").strip():
            v = int(self.ev(ent, p["p2stateno"], -1))
            if v >= 0:
                self.p2_enter(v, now=False)
        elif d.custom:
            self.p2_leave(5000)
        self.events.append({"t": self.tick, "who": ent.name, "kind": "acerto", "label": hd["label"],
                            "ci": hd.get("index"),
                            "value": [round(d.vel[0], 2), round(d.vel[1], 2)], "contact": contact,
                            "engine_spark": p.get("sparkno", "0").strip() not in ("-1",),
                            "state": ent.stateno, "root_state": (ent.root or ent).stateno,
                            # peso do acerto (nota de qualidade: o final tem de pesar mais)
                            "damage": int(g("damage", (0, 0))[0]), "pause": [int(pt[0]), int(pt[1])],
                            "dealt": dmg["dealt"], "guard_damage": dmg["guard"], "p1power": dmg["p1power"],
                            "p2power": dmg["p2power"], "life": d.life, "attr": p.get("attr", "").strip(),
                            "animtype": p.get("animtype", "Light").strip(),
                            "fall": d.fall, "shake": int(self.ev(ent, p.get("envshake.time", "0"), 0)) > 0,
                            "sound": bool(p.get("hitsound", "").strip()),
                            "snd": self._sound(ent, p.get("hitsound", ""), hitdef=True),
                            "hittime": d.hittime, "numhits": numhits, "combo": d.combo,
                            "juggle": d.juggle.get(ent.pid),
                            # o oponente vai para um estado do atacante (agarrão cinematográfico)
                            "p2state": bool(p.get("p2stateno", "").strip())})
        if ent.proj is not None:
            self._proj_contact(ent, "hit")
        elif p.get("p1stateno", "").strip():
            # p1stateno: o atacante troca de estado no acerto (arremessos); o Time = 0 do estado
            # novo roda no tick seguinte (stateChange1 no acerto, stateChange2 no próximo)
            v = int(self.ev(ent, p["p1stateno"], -1))
            if v >= 0:
                self.enter(ent, v)
                ent.ctrl = 0
                ent.hold_time = ent.hold_anim = True
                ent.hitdef_hit = True

    # Default.Attack.LifeToPowerMul e Default.GetHit.LifeToPowerMul do mugen.cfg padrão
    POWER_HIT, POWER_GETHIT = 0.7, 0.6

    def _damage(self, ent, p: dict, g) -> dict:
        """Dano e energia de um contato do HitDef, como o MUGEN conta: o dano vezes o attack do
        [Data] do char (o boneco tem defence 100); sem getpower/givepower, a energia vem do dano."""
        pair = lambda v: (int(v[0]) if len(v) > 0 else 0, int(v[1]) if len(v) > 1 else 0)
        hit, guard = pair(g("damage", (0, 0)))
        attack = (self.const("data.attack") or 100) / 100 * getattr(ent.root or ent, "attackmul", 1.0)
        # sem o 2º valor (p1gpower, p2gpower) a energia na defesa é a metade, como no MUGEN
        given = lambda k: pair(g(k)) if len(_split_args(p[k])) > 1 else (pair(g(k))[0], None)
        p1 = given("getpower") if "getpower" in p else (round(hit * self.POWER_HIT), None)
        p2 = given("givepower") if "givepower" in p else (round(hit * self.POWER_GETHIT), None)
        return {"dealt": self._deal(hit * attack), "guard": self._deal(guard * attack),
                "p1power": p1[0], "p1gpower": p1[1] if p1[1] is not None else p1[0] // 2,
                "p2power": p2[0], "p2gpower": p2[1] if p2[1] is not None else p2[0] // 2}

    def _hit_types(self, ent: Entity, p: dict, air: bool) -> tuple:
        """GetHitVar(animtype/groundtype/airtype) do HitDef: animtype light/medium/hard/back/up/
        diagup (air.animtype no ar), ground.type high/low/trip (air.type padrão = ground.type)."""
        def word(k, table, dft):
            v = p.get(k, "").strip().lower()
            return next((n for w, n in table if v.startswith(w)), dft) if v else dft
        at_tab = (("l", 0), ("m", 1), ("h", 2), ("b", 3), ("u", 4), ("d", 5))
        gt_tab = (("n", 0), ("h", 1), ("l", 2), ("t", 3))
        at = word("animtype", at_tab, 0)
        if air:
            at = word("air.animtype", at_tab, at)
        gt = word("ground.type", gt_tab, 1)
        return at, gt, word("air.type", gt_tab, gt)

    def _attacker_pause(self, p0: int) -> int:
        """Ticks parados do atacante: o pausetime no MUGEN 1.x/Ikemen; um a menos num char do
        WinMUGEN (o Ikemen soma 1 só para os outros: hitPauseTime = pausetime + hpFix)."""
        return p0 if self.mugen1 else max(0, p0 - 1)

    def _juggle_ok(self, ent: Entity, p: dict, falling: bool) -> bool:
        """Pontos de juggle (Ikemen canJuggle): o custo do golpe (`juggle` do Statedef, ou
        `air.juggle` do projétil) cabe no que sobra ao atacante (o [Data] airjuggle se ainda não
        é alvo). Char com ikemenversion pega de graça quem não está caindo; char MUGEN pega de
        graça quem ainda não é alvo dele, mas o projétil dele paga sempre."""
        cost = self._juggle_cost(ent, p)
        left = self.p2.juggle.get(ent.pid)
        proj = getattr(ent, "proj", None) is not None
        if cost <= (self.air_juggle if left is None else left):
            return True
        if self.ikemenver and not falling or not self.ikemenver and left is None and not proj:
            return True
        if left is not None:
            if not getattr(self, "_juggle_noted", False):
                self._juggle_noted = True
                self.note(tr("juggle points ran out: the opponent could not be hit again"))
        return False

    def _juggle_cost(self, ent: Entity, p: dict) -> int:
        if getattr(ent, "proj", None) is not None:
            return int(self.ev(ent, p["air.juggle"], 0)) if "air.juggle" in p else 0
        return ent.juggle

    def _juggle_spend(self, ent: Entity, p: dict, falling: bool) -> None:
        """No acerto o atacante vira alvo (com o máximo do [Data] airjuggle); se o P2 caía antes
        ou depois do acerto, o custo sai e o do golpe zera (um golpe de vários acertos paga uma
        vez só)."""
        d = self.p2
        d.juggle.setdefault(ent.pid, self.air_juggle)
        if falling:
            d.juggle[ent.pid] -= self._juggle_cost(ent, p)
            if getattr(ent, "proj", None) is None:
                ent.juggle = 0

    def _chain_ok(self, ent: Entity, p: dict) -> bool:
        """chainid/nochainid como o Ikemen (attrCheck): chainid só pega o P2 cujo último acerto
        veio do HitDef com esse id; nochainid não pega enquanto o P2 está no acerto daquele id."""
        d = self.p2
        if "chainid" in p:
            cid = int(self.ev(ent, p["chainid"], -1))
            if cid >= 0 and d.hitid != cid:
                return False
        if "nochainid" in p and d.state in ("hit", "air", "fall", "down", "guardhit"):
            for v in _split_args(p["nochainid"]):
                if v.strip() and int(self.ev(ent, v, -1)) == d.hitid >= 0:
                    return False
        return True

    def _deal(self, dmg: float, absolute: bool = False) -> int:
        """Dano que sai da vida como o Ikemen (computeDamage): dividido pela defesa do P2 (sobe
        depois de cair), ao menos 1 se houve dano, arredondado e no máximo a vida que sobra."""
        if dmg == 0:
            return 0
        if not absolute:
            dmg /= self.p2.def_mul
        if 0 < dmg < 1:
            dmg = 1
        return int(min(round(dmg), max(0, self.p2.life)))

    def _guards(self, d: Dummy, p: dict) -> bool:
        """O boneco que defende segura o golpe se está livre e o guardflag cobre a postura
        (H em pé, L agachado, A no ar; M = H e L)."""
        if not d.guard or d.custom or d.state not in ("stand", "guardhit"):
            return False
        gf = p.get("guardflag", "").upper()
        need = "A" if d.pos[1] < 0 else ("L" if d.pose == "crouch" else "H")
        return need in gf or (need != "A" and "M" in gf)

    def _guarded(self, ent: Entity, hd: dict, contact, g) -> None:
        # MUGEN: guard.pausetime, guard.velocity e guard.hittime caem nos valores do acerto
        d, p = self.p2, hd["params"]
        dmg = self._damage(ent, p, g)
        d.life = max(0, d.life - dmg["guard"])
        ent.hitdef_hit = True
        ent.movecontact = ent.moveguarded = 1
        ent.hit_now = True
        if ent.root is not None:
            ent.root.movecontact = ent.root.moveguarded = 1
            ent.root.hit_now = True
        pt = g("guard.pausetime", g("pausetime", (0, 0)))
        ent.hitpause = self._attacker_pause(int(pt[0]))
        d.shake = int(pt[1])
        gv = self.ev(ent, p["guard.velocity"], 0) if "guard.velocity" in p else g("ground.velocity", (0, 0))[0]
        d.hitvel, d.vel = (float(gv) * -ent.facing, 0.0), [0.0, 0.0]
        # guard.hittime: char MUGEN cai no ground.slidetime (o Ikemen segue o MUGEN, não a
        # documentação), char do Ikemen no ground.hittime
        base = "ground.hittime" if self.ikemenver else "ground.slidetime"
        d.hittime = max(0, int(self.ev(ent, p["guard.hittime"], 0)) if "guard.hittime" in p
                        else int(self.ev(ent, p[base], 0)) if base in p else 0)
        # common1.cns (151/153): CtrlSet em Time = GetHitVar(ctrltime) e 130 no HitOver; o
        # ctrltime cai no guard.slidetime, que cai no guard.hittime
        slide = int(self.ev(ent, p["guard.slidetime"], d.hittime)) if "guard.slidetime" in p else d.hittime
        d.ctrltime = int(self.ev(ent, p["guard.ctrltime"], slide)) if "guard.ctrltime" in p else slide
        d.slidetime = slide
        d.airguard = d.stype() == "A"
        if d.airguard:
            # 154/155: airguard.velocity (padrão air.velocity x 1,5 e y 0,5) e airguard.ctrltime
            av = g("air.velocity", (0, 0))
            dx, dy = float(av[0]) * 1.5, float(av[1]) * 0.5
            if "airguard.velocity" in p:
                parts = _split_args(p["airguard.velocity"])
                dx = float(self.ev(ent, parts[0], dx)) if parts and parts[0] else dx
                dy = float(self.ev(ent, parts[1], dy)) if len(parts) > 1 and parts[1] else dy
            d.hitvel = (dx * -ent.facing, dy)
            if "airguard.ctrltime" in p:
                d.ctrltime = int(self.ev(ent, p["airguard.ctrltime"], d.ctrltime))
        d.state, d.t, d.sub, d.st = "guardhit", 0, "shake", 0
        d.ctrl, d.combo = False, 0
        d.facing = -ent.facing
        self.events.append({"t": self.tick, "who": ent.name, "kind": "defesa", "label": hd["label"],
                            "ci": hd.get("index"),
                            "contact": contact, "state": ent.stateno, "root_state": (ent.root or ent).stateno,
                            "pause": [int(pt[0]), int(pt[1])], "hittime": d.hittime,
                            "ctrltime": d.ctrltime, "dealt": dmg["guard"], "p1power": dmg["p1gpower"], "p2power": dmg["p2gpower"],
                            "life": d.life, "attr": p.get("attr", "").strip(),
                            "snd": self._sound(ent, p.get("guardsound", "6,0"), hitdef=True)})
        if ent.proj is not None:
            self._proj_contact(ent, "guard")

    def step_dummy(self):
        """Um tick do boneco na ordem do Ikemen (actionRun): o estado roda (HitShakeOver passa
        do tremor ao recuo, HitOver devolve o parado), o corpo anda com o atrito do chão e só
        depois o hitshaketime e o hittime descem. Roda antes da colisão do tick."""
        d = self.p2
        self._apply_bind()
        if self.p2_run is not None:
            self.step_p2_run()
            if self.p2_run is not None:
                d.t += 1
                return
            self._apply_bind()
            d.t += 1
            return
        bound = d.bind is not None and d.bind["time"] != 0
        if bound:
            pos = list(d.pos)
        if d.state in ("hit", "guardhit"):
            self._step_stun(d)
        elif d.state in ("air", "fall"):
            self._step_air(d)
        elif d.state == "down":
            self._step_down(d)
        if bound:                                  # preso: o corpo não anda pela velocidade
            d.pos = pos
        d.t += 1

    def _step_airguard(self, d: Dummy) -> None:
        """155: HitVelSet x e y, gravidade do char a cada tick, ctrl no airguard.ctrltime e
        pouso (52) quando toca o chão; sem HitOver."""
        if d.sub == "shake" and d.shake <= 0:
            d.sub, d.st = "slide", 0
        if d.sub == "slide":
            if d.st == 0:
                d.vel = list(d.hitvel)
            if d.st == d.ctrltime:
                d.ctrl = True
            d.vel[1] += self.const("movement.yaccel") or 0.44
            if d.pos[1] >= 0 and d.vel[1] > 0:
                d.state, d.sub, d.vel, d.pos[1], d.ctrl = "stand", "", [0.0, 0.0], 0.0, True
                d.airguard = False
                return
            d.pos[0] += d.vel[0]
            d.pos[1] += d.vel[1]
            d.st += 1
        self._tick_hit_timers(d)

    def _recover(self, d: Dummy) -> None:
        """O P2 sai do acerto (MoveType deixa de ser H): o combo, o juggle e o fallflag zeram."""
        d.combo, d.hitid, d.fall = 0, -1, False
        d.def_mul = 1.0
        d.juggle.clear()

    def _tick_hit_timers(self, d: Dummy) -> None:
        if d.shake > 0:
            d.shake -= 1
        if d.shake <= 0 and d.hittime >= 0:
            d.hittime -= 1

    def _step_air(self, d: Dummy) -> None:
        """common1 5020/5030/5035 (voa com o hitvelset e a gravidade do HitDef), 5040 (sem
        fall: recupera no ar no HitOver e pousa) e 5050 (fall: cai até o chão)."""
        gl = self.const("movement.air.gethit.groundlevel")
        if d.sub == "shake" and d.shake <= 0:          # HitShakeOver
            d.sub, d.st = "fly", 0
        if d.sub == "fly":
            if d.st == 0:
                d.vel = list(d.hitvel)
            else:
                d.vel[1] += d.yaccel
            if d.hittime < 0 or d.vel[1] > 0 and d.pos[1] >= gl:
                d.sub, d.st = ("falling" if d.fall else "recover"), 0
                if not d.fall:
                    d.state = "air"
        elif d.sub in ("falling", "recover") and d.st > 0:
            d.vel[1] += d.yaccel
        if d.sub == "recover":
            if d.hittime < 0 and not d.ctrl:           # HitOver: ctrl e MoveType I no ar
                d.ctrl = True
                self._recover(d)
            if d.vel[1] > 0 and d.pos[1] >= 0:         # pousa (52)
                d.state, d.sub, d.vel, d.pos[1], d.ctrl = "stand", "", [0.0, 0.0], 0.0, True
                if d.combo:
                    self._recover(d)
                return
        elif d.sub == "falling" and d.vel[1] > 0 and d.pos[1] >= gl:
            d.state, d.sub, d.st = "down", "bounce", 0  # 5100: bate no chão
            self._step_down(d, landed=True)
            return
        if d.sub != "shake":
            d.pos[0] += d.vel[0]
            d.pos[1] += d.vel[1]
            d.st += 1
        self._tick_hit_timers(d)

    def _anim_len(self, n: int, dft: int) -> int:
        a = self.char.anims.get(n)
        return a.total_time if a is not None and a.total_time > 0 else dft

    def _step_down(self, d: Dummy, landed: bool = False) -> None:
        """5100 (bate e para, posfreeze), 5101 (quica com fall.yvelocity), 5110 (deitado
        liedown.time ticks) e 5120 (levanta, já sem MoveType H e sem poder apanhar)."""
        if d.sub == "bounce":
            if d.st == 0:
                d.pos[1], d.vel = 0.0, [d.vel[0] * 0.75, 0.0]
                if not d.fallvel[1]:
                    d.sub, d.st = "lie", 0
            if d.sub == "bounce":
                if d.st == 1:                          # 5100, time = 1: fall.defence_up
                    d.def_mul *= (100 + self.const("data.fall.defence_up")) / 100
                if d.st >= self._anim_len(5100, 3) - 1:   # AnimTime = 0
                    d.sub, d.st = "bounce2", 0
                else:
                    d.st += 1
                    return self._tick_hit_timers(d)
        if d.sub == "bounce2":
            if d.st == 0:
                # HitFallVel: fall.xvelocity é local do P2 (positivo = para a frente dele)
                d.vel = [d.vel[0] if d.fallvel[0] is None else d.fallvel[0] * d.facing, d.fallvel[1]]
                d.pos[1] = self.const("movement.down.bounce.offset.y")
            else:
                d.vel[1] += self.const("movement.down.bounce.yaccel")
            if d.vel[1] > 0 and d.pos[1] >= self.const("movement.down.bounce.groundlevel"):
                d.sub, d.st = "lie", 0
            else:
                d.pos[0] += d.vel[0]
                d.pos[1] += d.vel[1]
                d.st += 1
                return self._tick_hit_timers(d)
        if d.sub == "lie":
            if d.st == 0:
                d.pos[1], d.vel[1] = 0.0, 0.0
            d.vel[0] *= 0.85
            if abs(d.vel[0]) < self.const("movement.down.friction.threshold"):
                d.vel[0] = 0.0
            if d.st >= 1 and d.down_rt <= 0:          # 5120: levanta
                d.sub, d.st, d.vel = "getup", 0, [0.0, 0.0]
                self._recover(d)
            else:
                d.pos[0] += d.vel[0]
                d.st += 1
                d.down_rt -= 1
                return self._tick_hit_timers(d)
        if d.sub == "getup":
            if d.st >= self._anim_len(5120, 20):
                d.state, d.sub, d.ctrl = "stand", "", True
                return
            d.st += 1

    def _step_stun(self, d: Dummy) -> None:
        guard = d.state == "guardhit"
        if guard and getattr(d, "airguard", False):
            return self._step_airguard(d)
        if d.sub == "shake" and d.shake <= 0:          # 5000/150: HitShakeOver
            d.sub, d.st = "slide", 0
        if d.sub == "slide":
            if d.st == 0:                               # 5001/151: HitVelSet
                d.vel = [d.hitvel[0], 0.0]
            if guard:
                if d.st == d.slidetime or d.hittime < 0:
                    d.vel = [0.0, 0.0]
                if d.st == d.ctrltime:
                    d.ctrl = True
            elif d.st >= d.slidetime:
                d.vel[0] *= 0.6
            if d.hittime < 0:                           # HitOver: volta ao parado com ctrl
                d.state, d.vel, d.sub, d.ctrl = "stand", [0.0, 0.0], "", True
                d.shake = 0
                if not guard:
                    d.combo, d.hitid = 0, -1
                return
            d.pos[0] += d.vel[0]
            fr = self.const("movement.stand.friction") or 0.85
            d.vel[0] *= fr                              # physics S (o boneco é um char em pé)
            if abs(d.vel[0]) < self.lc_ratio:
                d.vel[0] = 0.0
            d.st += 1
        if d.shake > 0:
            d.shake -= 1
        if d.shake <= 0 and d.hittime >= 0:
            d.hittime -= 1

    # ---- passo
    def step(self):
        """Um tick: o tempo e a anim avançam (ou a pausa do acerto conta), rodam os
        controladores, o personagem anda e as caixas são testadas; o registro do tick mostra o
        quadro com os efeitos que os controladores criaram nele (como o MUGEN desenha)."""
        ents = [self.p1] + [h for h in self.helpers if h.alive]
        paused = self.pause > 0
        moving = [e for e in ents if not paused or e.pausemove > 0]
        for ent in moving:
            ent.frozen = False
            ent.push_off = False
            ent.pos_frozen = False
            ent.width_player = (0.0, 0.0)
            if not ent.hitpause:                  # Ikemen: o padrão volta fora da pausa do acerto
                # só os jogadores ficam presos à tela por padrão; helper só com ScreenBound
                ent.screenbound = (True, True) if ent is self.p1 else (False, False)
            if ent.started:
                if ent.hitpause > 0:
                    ent.hitpause -= 1
                    ent.frozen = True
                else:
                    if ent.hold_time:
                        ent.hold_time = False
                    else:
                        ent.time += 1
                    if ent.hold_anim:
                        ent.hold_anim = False
                    else:
                        ent.anim_t += 1
            ent.started = True
        for ent in moving:
            self.run_entity(ent)
        # helper criado neste tick roda já neste tick: estado, movimento e acerto (o Ikemen põe
        # o helper novo na fila do quadro; medido com o log por tick)
        seen = {id(e) for e in ents}
        for _ in range(8):
            new = [h for h in self.helpers if h.alive and id(h) not in seen
                   and not (self.pause > 0 and h.pausemove <= 0)]
            if not new:
                break
            for h in new:
                seen.add(id(h))
                h.frozen, h.started = False, True
                # o 1º tick já rodou ao nascer (make_helper): rodar de novo duplicava tudo do
                # tick 0 (um helper que solta helpers soltava dois)
                if getattr(h, "born_run", None) != self.tick:
                    self.run_entity(h)
                moving.append(h)
        # a pausa começou neste tick (SuperPause/Pause): quem não tem movetime não anda já
        # neste tick, mas o atrito vale (medido no Ikemen com o log por tick)
        starts = not paused and self.pause > 0
        for ent in moving:
            if not ent.alive or ent.frozen:
                continue
            if not (starts and ent.pausemove <= 0) and not getattr(ent, "pos_frozen", False):
                ent.pos[0] += ent.vel[0] * ent.facing
                ent.pos[1] += ent.vel[1]
            if ent.physics == "A":
                ent.vel[1] += self.const("movement.yaccel") or 0.44
                # o Ikemen pousa qualquer char com physics A, helper também (estado 52 do dono)
                if ent.pos[1] >= 0 and ent.vel[1] > 0 and ent.stateno != 105 \
                        and self.statedef(52) is not None:
                    ent.pos[1] = 0.0
                    ent.vel = [ent.vel[0], 0.0]
                    self.enter(ent, 52)
            elif ent.physics in ("S", "C"):
                kind = "stand" if ent.physics == "S" else "crouch"
                ent.vel[0] *= self.const(f"movement.{kind}.friction") or 0.85
                if self.ikemen:
                    # Ikemen (char.go, physics S): zera abaixo de 1 px/tick na escala do char e
                    # ignora friction.threshold; agachado não zera (medido com o log por tick)
                    if kind == "stand" and abs(ent.vel[0]) < self.lc_ratio:
                        ent.vel[0] = 0.0
                elif abs(ent.vel[0]) < (self.const(f"movement.{kind}.friction.threshold") or 0):
                    ent.vel[0] = 0.0
            # Ikemen: mctime sobe no fim do tick fora da pausa; o acerto deste tick vira 1
            if ent.movecontact and not ent.hit_now:
                ent.movecontact += 1
                ent.movehit += bool(ent.movehit)
                ent.moveguarded += bool(ent.moveguarded)
        # o P2 roda o tick dele (actionRun) antes da colisão, como no Ikemen: o HitOver deste
        # tick já vale para o acerto deste tick
        if not paused:
            self.step_dummy()
        self.screen_clamp()                       # update(): preso à tela antes da câmera
        self.step_projectiles(paused)             # charUpdate: os projéteis andam antes da colisão
        # como no Ikemen (collisionDetection): primeiro os corpos se empurram, depois os acertos
        if not self.p1.frozen and self.p1 in moving:
            self.push_players()
            self.screen_clamp()
        for ent in moving:
            if ent.alive and not ent.frozen:
                self.check_hits(ent)
        for q in self.projs:
            if q.alive and not q.frozen:
                self.check_hits(q)
        self.tick_projectiles(paused)
        self.step_camera()
        b = self.p2.bind
        if b is not None and not paused and b["time"] > 0:
            b["time"] -= 1
            if b["time"] == 0:
                self.p2.bind = None
        for ent in moving:
            ent.hit_now = False
            if paused:
                ent.pausemove -= 1
        ends_at: dict[int, int | None] = {}      # anim → tick em que a explod sai (None = nunca)
        for e in self.live_explods:
            if not e.alive:
                continue
            if paused:
                if e.pausemove <= 0:
                    continue
                e.pausemove -= 1
            if e.bind_to is not None and (e.bindtime < 0 or e.t < e.bindtime):
                b = e.bind_to                      # o x já veio virado pelo lado do nascimento
                e.pos = [b.pos[0] + e.offset[0], b.pos[1] + e.offset[1]]
            elif e.t > 0:
                e.pos[0] += e.vel[0] * e.move_facing
                e.pos[1] += e.vel[1]
                # Ikemen: vel = vel * friction + accel; com atrito, abaixo de 0,1 para
                fr = e.friction
                v = [e.vel[0] * fr[0] + e.accel[0], e.vel[1] * fr[1] + e.accel[1]]
                e.vel = tuple(0.0 if abs(v[i]) < 0.1 and abs(fr[i]) < 1 else v[i] for i in (0, 1))
            if e.anim not in ends_at:
                a = self.char.anims.get(e.anim)
                # só um quadro de tempo -1 segura a anim para sempre; tempo 0 (comum no último
                # quadro das faíscas) não: com ele a explod nunca saía e o helper que espera
                # !NumExplod para se destruir ficava na tela até o fim
                tt = a.total_time if a is not None else 0
                ends_at[e.anim] = (0 if a is None else
                                   tt if tt > 0 and all(f.time >= 0 for f in a.frames) else None)
            end = ends_at[e.anim]
            if e.removetime == -2 and end is not None and e.t - e.anim_t0 >= end:
                e.alive = False
            elif e.removetime >= 0 and e.t > e.removetime:
                e.alive = False
        if paused:
            self.pause -= 1
        self._paused_at_start = paused            # o que o Ikemen registra como pausa do tick
        self.snapshot()
        self.live_explods = [e for e in self.live_explods if e.alive]
        for e in self.live_explods:
            if not paused or e.pausemove > 0:
                e.t += 1
        self.tick += 1

    def chain_step(self) -> None:
        """Passa ao próximo golpe da cadeia: cancela (há ChangeState com movecontact/movehit do
        estado atual para ele e o golpe encostou) ou liga (o P1 já pode agir de novo)."""
        if self.chain_at + 1 >= len(self.chain):
            return
        p1, nxt = self.p1, self.chain[self.chain_at + 1]
        if p1.hitpause:
            return
        if nxt in self.cancels.get(p1.stateno, ()) and (p1.movecontact or p1.movehit):
            how = "cancela"
        elif p1.ctrl or p1.stateno in IDLE_STATES:
            how = "liga"
        else:
            return
        self.chain_at += 1
        self.events.append({"t": self.tick, "who": "P1", "kind": "cadeia", "value": nxt, "how": how,
                            "from": p1.stateno, "p2": self.p2.state})
        self.enter(p1, nxt)
        p1.started = False                     # o tick 0 do golpe novo roda no próximo passo

    def run(self, until_idle: bool = True):
        if self.statedef(self.p1.stateno) is None:   # estado que não existe: um tick só, com a nota
            self.step()
            return self
        while self.tick < self.max_ticks:
            self.step()
            if self.chain:
                self.chain_step()
            if until_idle and self.p1.stateno in (0, 52, 11, 20) and self.tick > 5 \
                    and not any(h.alive for h in self.helpers) and not self.live_explods \
                    and not self.projs and (self.p2.state == "stand" or
                                            self.p2.state == "down" and self.p2.sub == "lie"):
                break
        return self

    # ---- registro de cada tick (para desenhar e medir)
    def snapshot(self):
        ents = []
        for ent in [self.p1] + self.helpers + self.projs:
            if not ent.alive or ent.screen:
                continue
            f = ent.frame()
            pal = ent.palfx if ent.palfx and ent.palfx["until"] > self.tick else None
            inv = getattr(ent, "invisible_until", -9) >= self.tick - 1
            ents.append(dict(name=ent.name, pos=tuple(ent.pos), facing=ent.facing, frame=f, vel=tuple(ent.vel),
                             anim=ent.anim, elem=ent.elem_at(ent.anim_t), state=ent.stateno,
                             time=ent.time, spr=ent.sprpriority, pal=pal, ctrl=ent.ctrl,
                             frozen=ent.frozen,
                             scale=(ent.scale[0] * (ent.draw_scale or (1, 1))[0],
                                    ent.scale[1] * (ent.draw_scale or (1, 1))[1]),
                             angle=ent.draw_angle or 0.0,
                             inv=inv, trans=ent.trans, alpha=ent.alpha, clsn1=self.boxes(ent, "1"),
                             clsn2=self.boxes(ent, "2"), helper=ent.is_helper or ent.proj is not None,
                             stype=ent.statetype, hd=bool(ent.hitdef) and not ent.hitdef_hit,
                             # o projétil não entra no preparo/ativo do P1 (frame data do corpo)
                             atk=bool(ent.hitdef) and ent.proj is None, proj=ent.proj is not None,
                             guardflag=(ent.hitdef or {}).get("params", {}).get("guardflag", "").upper()))
        exps, scr = [], []
        for e in self.live_explods:
            if e.alive and e.space == "screen":
                # cut-in, camadas da cena: fora das medidas do palco, mas a folha tick a tick
                # desenha (em coordenadas da tela 320x240)
                f = frame_at(self.char, e.anim, e.t - e.anim_t0)
                if f is not None:
                    scr.append(dict(anim=e.anim, frame=f, pos=(e.pos[0] - self.origin_x + 160, e.pos[1] + 40),
                                    facing=e.facing, scale=e.scale, angle=e.angle, spr=e.spr,
                                    trans=e.trans, alpha=e.alpha, label=e.label))
            if not e.alive or e.space == "screen":       # cut-in e linhas na tela: fora da folha do palco
                continue
            f = frame_at(self.char, e.anim, e.t - e.anim_t0)
            if f is None:
                continue
            exps.append(dict(anim=e.anim, frame=f, pos=tuple(e.pos), facing=e.facing,
                             vfacing=e.vfacing, scale=e.scale, angle=e.angle, spr=e.spr,
                             trans=e.trans, alpha=e.alpha, label=e.label, owner=e.owner.name, age=e.t,
                             on_hit=e.on_hit, born=e.born, key=id(e)))
        self.track.append(dict(t=self.tick, ents=ents, explods=exps,
                               helper_states=sorted({h.stateno for h in self.helpers if h.alive}),
                               p2=dict(pos=tuple(self.p2.pos), state=self.p2.state,
                                       facing=self.p2.facing, shake=self.p2.shake,
                                       pose=self.p2.pose, guard=self.p2.guard,
                                       # pode agir: parado, ou na defesa depois do guard.ctrltime
                                       # (ou no ar depois do HitOver: 5040 devolve o ctrl)
                                       ctrl=self.p2.state == "stand" or (
                                           self.p2.state in ("guardhit", "air") and self.p2.ctrl
                                           and not self.p2.custom),
                                       sub=self.p2.sub, hittime=self.p2.hittime,
                                       frame=self.p2_run.frame() if self.p2_run else None,
                                       custom=self.p2.custom,
                                       combo=self.p2.combo),
                               p2box=self.p2_box(),
                               pause=self.pause > 0,
                               paused=getattr(self, "_paused_at_start", False),
                               nobg=getattr(self, "_nobg_tick", -1) == self.tick, screen=scr))


def simulate(char, state: int, p2_dist: float = 50.0, seed: int = 7, max_ticks: int = 900,
             commands: tuple = (), p2_clsn: list | None = None, p2_pose: str = "stand",
             p2_guard: bool = False, chain: tuple = (), from_ground: bool = False) -> Sim:
    """Roda o estado `state` (e os que ele chama) até voltar ao parado. p2_clsn: as Clsn2 do
    oponente (ex.: `opponent_clsn(kfm)`); sem elas o P2 é um boneco do tamanho do char.
    p2_pose ("stand", "crouch", "air") e p2_guard dizem como o P2 espera o golpe; chain é uma
    cadeia de estados (o 1º é `state`) que entram em seguida, para testar combos. from_ground:
    um estado aéreo que começa no chão (o pulo faz parte do golpe), em vez de no alto do pulo."""
    return Sim(char, state, p2_dist, seed, max_ticks, commands, p2_clsn, p2_pose, p2_guard,
               chain, from_ground).run()


def starts_on_ground(char, state: int) -> bool:
    """Um estado aéreo que o [Statedef -1] chama do chão (statetype != A, = S ou = C): o pulo é
    parte do golpe, então a simulação sai do chão em vez do alto de um pulo."""
    st = char.states_file_for(-1)
    sd = st.statedefs.get(-1) if st is not None else None
    for ctl in (sd.controllers if sd else []):
        if not hasattr(ctl, "triggers") or ctl.type.lower() != "changestate":
            continue
        m = re.match(r"^\s*(-?\d+)", ctl.section.get("value") or "")
        if not m or int(m[1]) != state:
            continue
        text = " ".join(e for ex in ctl.triggers().values() for e in ex).lower().replace(" ", "")
        if "statetype!=a" in text or "statetype=s" in text or "statetype=c" in text:
            return True
    return False


# pose do boneco -> anims do oponente de verdade (Clsn2 de todos os quadros, somadas)
P2_POSE_ANIMS = {"stand": (0,), "hit": (5000, 5001, 5005), "air": (5030, 5035),
                 "fall": (5030, 5035, 5050), "down": (5110,), "crouch": (11,),
                 "crouchhit": (5020, 5021, 5025), "jump": (41,), "guard": (150, 151),
                 "crouchguard": (152, 153)}


def p2_keys(state: str, pose: str) -> tuple:
    """Poses do P2 da mais exata à mais genérica (o oponente pode não ter a anim agachado)."""
    if state == "stand":
        return {"crouch": ("crouch", "stand"), "air": ("jump", "air", "stand")}.get(pose, ("stand",))
    if state == "hit":
        return ("crouchhit", "hit") if pose == "crouch" else ("hit",)
    if state == "guardhit":
        return ("crouchguard", "guard", "crouch", "stand") if pose == "crouch" else ("guard", "stand")
    return (state, "stand")


def opponent_clsn(opponent) -> dict:
    """Clsn2 de um personagem por pose do P2 da simulação: {"stand": [...], "hit": [...], ...}.
    Cada pose junta as caixas de todos os quadros das anims dela (o dano se inclina para trás)."""
    out = {}
    for pose, anims in P2_POSE_ANIMS.items():
        boxes = []
        for n in anims:
            act = opponent.anims.actions.get(n)
            for f in (act.frames if act else []):
                for b in (f.clsn2 or act.clsn2_default):
                    if tuple(b) not in boxes:
                        boxes.append(tuple(b))
        if boxes:
            out[pose] = boxes
    try:                                          # caixa de tamanho do oponente (empurrão)
        size = next(sec for sec in opponent.constants.sections if sec.name.lower() == "size")
        out["size"] = {k.lower(): v for k, v in size.as_dict().items()
                       if k.lower() in ("ground.front", "ground.back", "air.front", "air.back", "height")}
    except Exception:
        pass
    return out


# --------------------------------------------------------------------- desenho

def _palfx(im: Image.Image, pal):
    if not pal:
        return im
    a = np.asarray(im).astype(np.float32)
    add, mul = pal["add"], pal["mul"]
    for k in range(3):
        a[..., k] = np.clip((a[..., k] + add[k]) * mul[k] / 256.0, 0, 255)
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def _gray(im: Image.Image, alpha=0.75):
    a = np.asarray(im).astype(np.float32)
    lum = a[..., :3].mean(2, keepdims=True) * 0.6 + 40
    a[..., :3] = lum
    a[..., 3] *= alpha
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def _blend(frame, trans: str | None, alpha) -> tuple[str, float, float]:
    """(modo, fonte, fundo) de um sprite, como o MUGEN mistura: o `trans` do Trans/Explod manda
    (none = opaco; add; add1 = fundo pela metade; addalpha com alpha = fonte,fundo de 0 a 256;
    sub) e, sem ele (ou com default), vale a transparência do AIR (A, A1, S/Sub, AS<n>D<m>)."""
    t = (trans or "").lower()
    if t in ("", "default"):
        return _trans_air(frame)
    if t == "none":
        return "normal", 1.0, 1.0
    if t == "sub":
        return "sub", 1.0, 1.0
    if t == "add1":
        return "add", 1.0, 0.5
    if t == "addalpha" and alpha:
        s, d = (max(0.0, min(256.0, float(v))) / 256 for v in alpha)
        return ("normal", s, 1.0) if d == 0 and s >= 1 else ("add", s, d)
    return "add", 1.0, 1.0


def _trans_air(frame) -> tuple[str, float, float]:
    t = (getattr(frame, "trans", "") or "").strip().upper()
    if t in ("S", "SUB"):
        return "sub", 1.0, 1.0
    m = re.match(r"AS(\d+)D(\d+)", t)
    if m:
        s, d = int(m[1]) / 256, int(m[2]) / 256
        return ("normal", 1.0, 1.0) if d == 0 and s >= 1 else ("add", s, d)
    if t == "A1":
        return "add", 1.0, 0.5
    if t.startswith("A"):
        return "add", 1.0, 1.0
    return "normal", 1.0, 1.0


P2_ANIM = {"stand": 0, "hit": 5000, "fall": 5030, "air": 5030, "down": 5110, "crouch": 11,
           "crouchhit": 5020, "jump": 41, "guard": 150, "crouchguard": 152}


def _screen_layer(sim: Sim, rec: dict, size, front: bool):
    """Explods presos à tela (cut-in, camadas da cena) desenhados na tela 320x240 e esticados
    para o tamanho do quadro. front: os de sprpriority >= 0 (na frente dos chars)."""
    items = [x for x in rec.get("screen", []) if (x["spr"] >= 0) == front]
    if not items:
        return None
    cv = _Canvas((0, 320, 0, 240), 2)
    cv.im = Image.new("RGBA", cv.im.size, (0, 0, 0, 0))
    for x in sorted(items, key=lambda x: x["spr"]):
        im, ax, ay = _frame_img(sim.char, x["frame"])
        if im is None:
            continue
        mode, al, _ = _blend(x["frame"], x["trans"], x.get("alpha"))
        if mode == "add":
            # brilho aditivo sobre o que estiver atrás: vira alfa pelo brilho do pixel
            glow = sim.__dict__.setdefault("_glow_cache", {})
            k = (id(im), al)
            if k not in glow or glow[k][0] is not im:
                a = np.asarray(im.convert("RGBA"), np.float32)
                a[..., 3] = a[..., 3] * _rgb_max(a) / 255 * al
                glow[k] = (im, Image.fromarray(a.astype(np.uint8), "RGBA"))
            im = glow[k][1]
        cv.blit(im, ax, ay, x["pos"][0], x["pos"][1], x["scale"][0], x["scale"][1], x["facing"],
                x["angle"], "normal", 1.0)
    return cv.im.resize(size, Image.BILINEAR)


def render_tick(sim: Sim, rec: dict, window, zoom=2, boxes=True, show_p2=True):
    cv = _Canvas(window, zoom)
    if rec.get("nobg"):                       # cena: o char apagou o cenário
        cv.im = Image.new("RGBA", cv.im.size, (0, 0, 0, 255))
    back = _screen_layer(sim, rec, cv.im.size, front=False)
    if back is not None:
        cv.im.alpha_composite(back)
    d0 = ImageDraw.Draw(cv.im)
    gy = cv.P(0, 0)[1]
    d0.line([(0, gy), (cv.w, gy)], fill=(120, 150, 120, 255))
    items = []
    if show_p2:
        p2 = rec["p2"]
        f = p2.get("frame") or next((f for k in p2_keys(p2["state"], p2.get("pose", "stand"))
                                     if (f := frame_at(sim.char, P2_ANIM.get(k, 0), 0)) is not None), None)             or frame_at(sim.char, 0, 0)
        im, ax, ay = _frame_img(sim.char, f)
        if im is not None:
            items.append((-50, "p2", (_gray(im), ax, ay, p2["pos"][0] + (2 if p2["shake"] % 2 else 0),
                                      p2["pos"][1], 1, 1, p2["facing"], 0.0, "normal", 1.0)))
    for e in rec["ents"]:
        if e["inv"] or e["frame"] is None:
            continue
        im, ax, ay = _frame_img(sim.char, e["frame"])
        if im is None:
            continue
        im = _palfx(im, e["pal"])
        mode, al, dl = _blend(e["frame"], e["trans"], e.get("alpha"))
        items.append((e["spr"], e["name"], (im, ax, ay, e["pos"][0], e["pos"][1], e["scale"][0],
                                            e["scale"][1], e["facing"], e.get("angle", 0.0), mode, al, dl)))
    for x in rec["explods"]:
        im, ax, ay = _frame_img(sim.char, x["frame"])
        if im is None:
            continue
        if x["vfacing"] < 0:
            im = im.transpose(Image.FLIP_TOP_BOTTOM)
            ay = im.height - ay
        mode, al, dl = _blend(x["frame"], x["trans"], x.get("alpha"))
        items.append((x["spr"], "fx", (im, ax, ay, x["pos"][0], x["pos"][1], x["scale"][0],
                                       x["scale"][1], x["facing"], x["angle"], mode, al, dl)))
    items.sort(key=lambda t: t[0])
    for _, who, (im, ax, ay, x, y, sx, sy, fc, ang, mode, al, *dl) in items:
        cv.blit(im, ax, ay, x, y, sx, sy, fc, ang, mode, al, dl[0] if dl else 1.0)
    d = ImageDraw.Draw(cv.im)
    if boxes:
        for e in rec["ents"]:
            for bx in e["clsn1"]:
                (x0, y0), (x1, y1) = cv.P(bx[0], bx[1]), cv.P(bx[2], bx[3])
                d.rectangle([x0, y0, x1, y1], outline=(255, 60, 60, 255))
        for bx in rec["p2box"]:
            (x0, y0), (x1, y1) = cv.P(bx[0], bx[1]), cv.P(bx[2], bx[3])
            d.rectangle([x0, y0, x1, y1], outline=(80, 140, 255, 255))
    front = _screen_layer(sim, rec, cv.im.size, front=True)
    if front is not None:
        cv.im.alpha_composite(front)
        d = ImageDraw.Draw(cv.im)
    p1 = rec["ents"][0] if rec["ents"] else None
    if p1:
        x, y = cv.P(p1["pos"][0], 0)
        d.line([(x, y - 4), (x, y + 4)], fill=(255, 255, 0, 255))
    return cv.im


def tick_pages(sim: Sim, out_dir, name: str, every: int = 1, per_page: int = 48, columns: int = 8,
               zoom: int = 2, boxes: bool = False) -> list[str]:
    """Folhas do golpe inteiro tick a tick (ou a cada `every`), câmera na luta, com o que fica preso
    à tela (cut-in, camadas da cena) e o fundo preto quando o char apaga o cenário. É a folha que
    o usuário confere quadro a quadro; grava <name>_NN.png e devolve os caminhos."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ticks = list(range(0, len(sim.track), max(1, every)))
    paths = []
    for k in range(0, len(ticks), per_page):
        im = sheet(sim, zoom=zoom, columns=columns, boxes=boxes, ticks=ticks[k:k + per_page],
                   max_tiles=per_page, follow=True)
        p = out / f"{name}_{k // per_page + 1:02d}.png"
        im.save(p)
        paths.append(str(p))
    return paths


def frame_ticks(sim: Sim) -> list[int]:
    """Um tick por (estado, quadro da anim) do P1 — o do meio, fora das pausas: a folha que mostra
    cada pose uma vez (sprite virado, efeito no membro errado, rastro no sentido errado)."""
    seen: dict = {}
    for r in sim.track:
        p1 = next((e for e in r["ents"] if e["name"] == "P1"), None)
        if p1 and not r["pause"]:
            seen.setdefault((p1["state"], p1["elem"]), []).append(r["t"])
    return sorted(ts[len(ts) // 2] for ts in seen.values())


def sheet(sim: Sim, every: int | None = None, zoom: int = 2, columns: int = 6, boxes=True,
          max_tiles: int = 36, ticks: list | None = None, per_frame: bool = False,
          follow: bool = False) -> Image.Image:
    """Folha da simulação: quadros a cada `every` ticks (ou nos ticks de evento), câmera fixa.
    per_frame = um quadro por (estado, elem), com a câmera seguindo o golpe (mais perto)."""
    track = sim.track
    if per_frame:
        ticks = frame_ticks(sim)
    xs, ys = [], []
    for r in track:
        for e in r["ents"]:
            xs.append(e["pos"][0]); ys.append(e["pos"][1])
        xs.append(r["p2"]["pos"][0]); ys.append(r["p2"]["pos"][1])
    h = sim.stand_h
    x0, x1 = min(xs) - 1.6 * h, max(xs) + 1.6 * h
    y0, y1 = min(min(ys) - 1.9 * h, -2.2 * h), max(15, max(ys) + 15)
    window = (int(x0), int(x1), int(y0), int(y1))
    if ticks is None:
        ev_ticks = sorted({e["t"] for e in sim.events if e["kind"] in ("estado", "explod", "acerto", "helper")})
        if every is None:
            every = max(2, len(track) // max_tiles)
        ticks = sorted(set(range(0, len(track), every)) | set(t + 1 for t in ev_ticks if t + 1 < len(track)))
        if len(ticks) > max_tiles:
            step_ = len(ticks) / max_tiles
            ticks = [ticks[int(i * step_)] for i in range(max_tiles)]
    tiles = []
    for t in ticks:
        rec = track[min(t, len(track) - 1)]
        win = window
        p1 = next((e for e in rec["ents"] if e["name"] == "P1"), None)
        if (per_frame or follow) and p1:
            cx = (p1["pos"][0] + rec["p2"]["pos"][0]) / 2
            win = (int(cx - 2.6 * h), int(cx + 2.6 * h), int(min(y0, -2.4 * h)), 12)
        im = render_tick(sim, rec, win, zoom, boxes)
        d = ImageDraw.Draw(im)
        p1 = next((e for e in rec["ents"] if e["name"] == "P1"), None)
        lab = f"t{rec['t']}"
        if p1:
            lab += tr(" st {0} el {1}").format(p1['state'], p1['elem'])
            if p1["frame"] is not None:
                lab += f" spr {p1['frame'].group},{p1['frame'].item}"
        if rec["pause"]:
            lab += tr(" PAUSE")
        evs = [e for e in sim.events if e["t"] == rec["t"] - 1 and e["kind"] in ("acerto",)]
        if evs:
            lab += tr(" HIT")
        d.rectangle([0, 0, im.width, 14], fill=(0, 0, 0, 255))
        d.text((3, 2), lab, fill=(255, 220, 80, 255))
        tiles.append(im)
    tw, th = tiles[0].size
    rows = (len(tiles) + columns - 1) // columns
    out = Image.new("RGB", (columns * tw, rows * th), (10, 10, 10))
    for k, t in enumerate(tiles):
        out.paste(t.convert("RGB"), ((k % columns) * tw, (k // columns) * th))
    return out


def _window(sim: Sim):
    xs, ys = [], []
    for r in sim.track:
        for e in r["ents"]:
            # helper sem nada na tela (sprite -1, invisível) não entra: os que ficam estacionados
            # em 999,999 como controle abriam a janela e encolhiam os chars num canto
            f = e["frame"]
            if e["name"] != "P1" and (e["inv"] or f is None or f.group < 0):
                continue
            xs.append(e["pos"][0]); ys.append(e["pos"][1])
        xs.append(r["p2"]["pos"][0]); ys.append(r["p2"]["pos"][1])
    h = sim.stand_h
    return (int(min(xs) - 1.6 * h), int(max(xs) + 1.6 * h),
            int(min(min(ys) - 1.9 * h, -2.2 * h)), int(max(15, max(ys) + 15)))


def animation(sim: Sim, path, step: int = 1, zoom: int = 2, boxes: bool = False) -> str:
    """GIF do golpe simulado em tempo real (60 ticks = 1 s), câmera fixa, com o tick, o estado
    e o quadro escritos em cima."""
    window = _window(sim)
    frames = []
    for rec in sim.track[::step]:
        im = render_tick(sim, rec, window, zoom, boxes)
        d = ImageDraw.Draw(im)
        p1 = next((e for e in rec["ents"] if e["name"] == "P1"), None)
        lab = f"t{rec['t']}  {rec['t'] / 60:.2f}s"
        if p1:
            lab += tr("  state {0}  frame {1}").format(p1['state'], p1['elem'])
        if rec["pause"]:
            lab += tr("  PAUSE")
        d.rectangle([0, 0, im.width, 14], fill=(0, 0, 0, 255))
        d.text((4, 2), lab, fill=(255, 220, 80, 255))
        frames.append(im.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT))
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=int(1000 * step / 60),
                   loop=0, optimize=False, disposal=1)
    return str(path)


# --------------------------------------------------------------------- medições

def _frame_key(f) -> tuple:
    return (f.group, f.item, f.x, f.y, (f.flip or "").upper())


def _rgb_max(a):
    """Maior canal (R, G ou B) de cada pixel: elemento a elemento é ~10x mais rápido que
    a[..., :3].max(2), que reduz num eixo não contíguo."""
    return np.maximum(np.maximum(a[..., 0], a[..., 1]), a[..., 2])


def _sprite_measure(sim: Sim, f, what: str):
    """Medida de um quadro (pixels visíveis ou área apagada), guardada na simulação: um super
    repete os mesmos quadros em centenas de ticks e decodificar cada vez levava ~40 s. A medida
    é do sprite (sem o deslocamento do quadro), então quadros do mesmo sprite em lugares
    diferentes medem uma vez só."""
    cache = sim.__dict__.setdefault("_fx_cache", {})
    key = (f.group, f.item, (f.flip or "").upper(), what)
    if key not in cache:
        im, ax, ay = _frame_img(sim.char, f)
        out = None
        if im is not None:
            ax, ay = ax + f.x, ay + f.y
            a = np.asarray(im)
            mx = _rgb_max(a)
            if what == "dim":
                out = int(((a[..., 3] > 0) & (mx >= 20) & (mx < 90)).sum())
            else:
                ys, xs = np.nonzero((a[..., 3] > 0) & (mx >= (90 if what == "bright" else 40)))
                if xs.size:
                    if xs.size > 4000:
                        k = np.linspace(0, xs.size - 1, 4000).astype(int)
                        xs, ys = xs[k], ys[k]
                    out = (xs - ax, ys - ay)
        cache[key] = out
    out = cache[key]
    if out is None or what == "dim":
        return out
    return out[0] + f.x, out[1] + f.y


def _effect_points(sim: Sim, x: dict, bright: bool = False):
    """Pixels visíveis do efeito no mundo (escala, espelhamento e ângulo aplicados)."""
    if x["frame"] is None or x["frame"].group < 0:
        return None
    local = _sprite_measure(sim, x["frame"], "bright" if bright else "vis")
    if local is None:
        return None
    px = local[0] * abs(x["scale"][0])
    py = local[1] * abs(x["scale"][1])
    if x["vfacing"] < 0:
        py = -py
    if x["angle"]:
        r = math.radians(x["angle"])
        px, py = px * math.cos(r) + py * math.sin(r), -px * math.sin(r) + py * math.cos(r)
    if x["facing"] < 0:
        px = -px
    return x["pos"][0] + px, x["pos"][1] + py


def _effect_box(sim: Sim, x: dict, bright: bool = False):
    pts = _effect_points(sim, x, bright)
    if pts is None:
        return None
    return (float(pts[0].min()), float(pts[1].min()), float(pts[0].max()), float(pts[1].max()))


def _dim_area(sim: Sim, x: dict) -> float:
    """Área na tela (px²) da parte apagada (brilho 20-90) do efeito: somada ao cenário, vira
    um disco cinza em volta do char."""
    n = _sprite_measure(sim, x["frame"], "dim") if x["frame"] is not None and x["frame"].group >= 0 else None
    return float(n or 0) * abs(x["scale"][0] * x["scale"][1])


KIT_FIXED = {7400, 7405, 9022, 7020}         # efeitos comuns em lugar fixo (ativação, clarão, linhas)


def audit(sim: Sim) -> list[dict]:
    """Problemas medidos na simulação (posição/tamanho/alinhamento)."""
    h = sim.stand_h
    out = []
    seen = set()
    measured = set()

    def add(kind, msg, key):
        if key in seen:
            return
        seen.add(key)
        out.append({"kind": kind, "message": msg})

    for rec in sim.track:
        # helpers de efeito (aura, garras) entram como efeito: frame/pos/facing/escala do helper
        hx = [dict(frame=e["frame"], pos=e["pos"], facing=e["facing"], vfacing=1, scale=e["scale"],
                   angle=e.get("angle", 0.0), trans=e["trans"] or "add", label=f"helper {e['state']}", anim=-1,
                   on_hit=False) for e in rec["ents"] if e["helper"] and not e["inv"] and e["frame"]]
        for x in rec["explods"] + hx:
            # o mesmo efeito parado no mesmo lugar em vários ticks dá a mesma medida: mede uma vez
            f = x["frame"]
            k = (_frame_key(f) if f is not None else None, tuple(x["pos"]), tuple(x["scale"]), x["angle"],
                 x["facing"], x["vfacing"], x["trans"], x["on_hit"], x["label"], x["anim"], bool(rec.get("nobg")))
            if k in measured:
                continue
            measured.add(k)
            b = _effect_box(sim, x)
            if b is None:
                continue
            lab = x["label"] or f"anim {x['anim']}"
            kit = x["anim"] in KIT_FIXED
            pts = _effect_points(sim, x, bright=True)    # só a parte clara (o brilho fraco some)
            frac = float((pts[1] > 2).mean()) if pts is not None else 0.0
            # medido: aura certa 0,7%, aura afundada 11%, rastro do dash no chão 54%, raios 1-4%
            depth = float(pts[1].max()) if pts is not None else 0.0
            if frac > 0.05 and depth > 5 and not x["on_hit"] and not kit:  # faísca de acerto pode passar do chão
                add("below-ground", tr("{0}: {1:.0%} of the effect below the ground (up to {2:.0f} px)").format(lab, frac, float(pts[1].max())), ("chao", lab))
            if (b[3] - b[1] > 2.6 * h or b[2] - b[0] > 4.5 * h) and not kit:
                if x["anim"] < 30000 or x["anim"] >= 35000:
                    add("huge-effect", tr("{0}: {1:.0f}x{2:.0f} px (the character is {3:.0f})").format(lab, b[2]-b[0], b[3]-b[1], h), ("big", lab))
            w_, h_ = b[2] - b[0], b[3] - b[1]
            round_ = max(w_, h_) < 1.8 * max(1.0, min(w_, h_))
            # na cena (cenário apagado pelo próprio char) o fundo é preto: o halo não acinzenta nada
            if x["trans"] != "sub" and not x["on_hit"] and not kit and round_ and not rec.get("nobg"):
                area = _dim_area(sim, x)
                if area > 2.5 * h * h:
                    add("dark-halo", tr("{0}: dimmed area of {1:.0f} px² ({2:.1f}x the character's height²) — added to the stage it becomes a gray disc").format(lab, area, area / (h * h)), ("halo", lab))
    # golpes que não acertam
    acts = [e for e in sim.events if e["kind"] == "acerto"]
    if not acts and not any(e["kind"] == "defesa" for e in sim.events) and any(r for r in sim.track if any(e.get("clsn1") for e in r["ents"])):
        add("no-hit", tr("no hit on the dummy at {0:.0f} px").format(sim.p2.pos[0]), ("miss",))
    # oponente jogado para o lado do atacante no fim
    if acts and len(sim.track) > 2:
        p1x = sim.track[-1]["ents"][0]["pos"][0] if sim.track[-1]["ents"] else 0
        # acerto no último tick simulado: não há tick depois dele (StopIteration derrubava a auditoria)
        first = next((r for r in sim.track if r["t"] > acts[-1]["t"]), sim.track[-1])
        p2a = first["p2"]["pos"][0]
        p2b = sim.track[-1]["p2"]["pos"][0]
        # a velocidade do acerto no mundo aponta para o lado do atacante (helper que vem de frente
        # com ground.velocity negativo joga o oponente por cima dele: a distância no fim não acusa)
        hit = acts[-1]
        at = next((r for r in sim.track if r["t"] >= hit["t"] and r["ents"]), first)
        vx = float(hit["value"][0])
        side = at["p2"]["pos"][0] - at["ents"][0]["pos"][0]
        toward = abs(vx) >= 2 and vx * side < 0
        if toward or (abs(p2b - p2a) > 20 and abs(p2b - p1x) < abs(p2a - p1x) - 20):
            add("reversed-push", tr("the last hit pushes the opponent toward the attacker (check the sign of ground.velocity x)"), ("push",))
    return out


def summary(sim: Sim) -> dict:
    return {"ticks": sim.tick, "events": [e for e in sim.events if e["kind"] in ("estado", "acerto", "pausa")],
            "hits": sum(1 for e in sim.events if e["kind"] == "acerto"),
            "unsupported": sim.unsupported, "problems": audit(sim)}


# ------------------------------------------------------------------ frame data e som

IDLE_STATES = (0, 11, 20, 52)


def frame_data(sim: Sim) -> dict:
    """Preparo, ativo e recuperação do P1 contados nos ticks da simulação (pausas fora) e a
    vantagem: quantos ticks o P1 volta a agir antes do P2 sair do acerto ou da defesa.

    `phases` tem uma fase por tick do registro: "preparo", "ativo", "entre" (vão entre dois
    acertos), "recuperacao", "pausa" e "livre" (o P1 já pode agir)."""
    track = sim.track
    p1 = [next((e for e in r["ents"] if e["name"] == "P1"), None) for r in track]
    paused = [r["pause"] or bool(e and e.get("frozen")) for r, e in zip(track, p1)]
    # ativo: Clsn1 de quem tem HitDef (helper de aura com Clsn1 e sem HitDef não ataca)
    active = [any(e["clsn1"] and e.get("atk", True) for e in r["ents"]) for r in track]
    idx = [i for i, a in enumerate(active) if a]
    contacts = [e for e in sim.events if e["kind"] in ("acerto", "defesa")]
    out = {"phases": [], "startup": None, "active": None, "recovery": None, "total": len(track),
           "outcome": "defesa" if contacts and contacts[-1]["kind"] == "defesa" else
           "acerto" if contacts else "errou", "advantage": None, "knockdown": False}
    first, last = (idx[0], idx[-1]) if idx else (None, None)
    # livre: o P1 volta a ter ctrl (um CtrlSet pode devolvê-lo ainda nos quadros ativos)
    # numa cadeia o P1 fica livre entre um golpe e outro: conta até o fim do último
    begin = 1 if first is None else (last if len(sim.chain) > 1 else first) + 1
    free = next((i for i in range(begin, len(track))
                 if p1[i] and (p1[i]["ctrl"] or p1[i]["state"] in IDLE_STATES)), len(track))
    count = lambda a, b: sum(1 for i in range(a, b) if not paused[i])
    for i in range(len(track)):
        if i >= free and (last is None or i > last or not active[i]):
            ph = "livre"
        elif paused[i]:
            ph = "pausa"
        elif first is None:
            ph = "recuperacao"
        elif i < first:
            ph = "preparo"
        elif i <= last:
            ph = "ativo" if active[i] else "entre"
        else:
            ph = "recuperacao"
        out["phases"].append(ph)
    if first is None:
        return out
    out["startup"], out["active"] = count(0, first), count(first, last + 1)
    out["startup_pause"] = first - out["startup"]           # SuperPause antes do golpe sair
    out["recovery"], out["total"] = count(last + 1, free), count(0, free)
    if contacts:
        last_t = contacts[-1]["t"]
        after = [i for i, r in enumerate(track) if r["t"] > last_t]
        # na defesa o P2 volta a agir no guard.ctrltime, que pode vir antes do fim do bloqueio
        p2_ctrl = next((i for i in after if track[i]["p2"].get("ctrl", track[i]["p2"]["state"] == "stand")), None)
        p2_free = next((i for i in after if track[i]["p2"]["state"] == "stand"), None)
        down = any(track[i]["p2"]["state"] in ("fall", "down") for i in after)
        out["knockdown"] = down or any(e.get("fall") or e.get("p2state") for e in contacts[-1:])
        if p2_ctrl is not None and not out["knockdown"] and free < len(track):
            out["advantage"] = p2_ctrl - free
            if p2_free is not None and p2_free != p2_ctrl:
                out["advantage_stun"] = p2_free - free      # fim do bloqueio (HitOver)
        out["cancel"] = _cancel_data(sim, track, p1, contacts, p2_ctrl, out["outcome"])
    return out


def cancel_targets(char, state: int, sds=None) -> dict:
    """Para onde o golpe cancela ao encostar: {"acerto": {...}, "defesa": {...}}. ChangeState
    com movehit só cancela no acerto, com moveguarded só na defesa, com movecontact nos dois;
    vale o do próprio estado e o do [Statedef -1] com `stateno = ...` que o cobre."""
    sds = sds or char.all_statedefs(include_common=False)
    out = {"acerto": set(), "defesa": set()}
    for n, (sd, _) in sds.items():
        if n != state and n != -1:
            continue
        for c in sd.controllers:
            if c.type.lower() != "changestate":
                continue
            t = " ".join(x for v in c.triggers().values() for x in v).lower()
            v = _lit(c.section.get("value"))
            if v is None or v == state or not re.search(r"move(contact|hit|guarded)", t):
                continue
            if n == -1:
                covers = any(int(a) <= state <= int(b or a) for a, b in
                             re.findall(r"stateno\s*=\s*\[?\s*(\d+)\s*(?:,\s*(\d+))?", t))
                if not covers:
                    continue
            # o que o gatilho pede, sem contar os negados (!movehit não é "cancela no acerto")
            has = {w for w in ("movecontact", "movehit", "moveguarded")
                   if re.search(rf"(?<![!\w]){w}\b", t)}
            kinds = set()
            if "movecontact" in has or {"movehit", "moveguarded"} <= has:
                kinds = {"acerto", "defesa"}                  # movehit || moveguarded = os dois
            elif "movehit" in has:
                kinds = {"acerto"}
            elif "moveguarded" in has:
                kinds = {"defesa"}
            for k in kinds:
                out[k].add(v)
    return out


def _cancel_data(sim: Sim, track, p1, contacts, p2_ctrl, outcome) -> dict | None:
    """Janela de cancelamento do golpe simulado ("from" = quadro do golpe, pausas fora): do
    primeiro tick depois do 1º contato em que o
    P1 já anda (fora da pausa do acerto) até o último tick ainda no estado do golpe; e a
    vantagem de cancelar o mais cedo depois do último contato (quantos ticks o P2 ainda fica
    preso: o golpe seguinte precisa sair antes disso para emendar)."""
    if outcome not in ("acerto", "defesa") or len(sim.chain) > 1:
        return None
    state = p1[0]["state"] if p1 and p1[0] else None
    if state is None:
        return None
    into = sorted(cancel_targets(sim.char, state)[outcome])
    if not into:
        return None

    def first_free_after(t):
        return next((i for i, (r, e) in enumerate(zip(track, p1)) if r["t"] > t and e
                     and not e.get("frozen") and not r["pause"]), None)
    start, last = first_free_after(contacts[0]["t"]), first_free_after(contacts[-1]["t"])
    if start is None or p1[start]["state"] != state:
        return None
    end = start
    while end + 1 < len(track) and p1[end + 1] and p1[end + 1]["state"] == state:
        end += 1
    moving = sum(1 for i in range(start, end + 1) if not track[i]["pause"] and not p1[i].get("frozen"))
    adv = p2_ctrl - last if p2_ctrl is not None and last is not None and last <= end else None
    frame = 1 + sum(1 for i in range(start) if not track[i]["pause"] and not (p1[i] or {}).get("frozen"))
    return {"into": into, "from": frame, "window": moving, "advantage": adv}


_ENGINE_SND: dict[str, object] = {}


def _engine_sounds(char):
    """common.snd do motor (sons "F" do PlaySnd e os sem "S" do HitDef), se o MUGEN for achado."""
    root = Path(getattr(char, "mugen_root", None) or "")
    for name in ("data/common.snd", "data/fight.snd"):
        path = root / name
        if getattr(char, "mugen_root", None) and path.is_file():
            key = str(path)
            if key not in _ENGINE_SND:
                from ..formats import snd as sndfmt
                try:
                    _ENGINE_SND[key] = sndfmt.load(path)
                except Exception:  # noqa: BLE001
                    _ENGINE_SND[key] = None
            if _ENGINE_SND[key] is not None:
                return _ENGINE_SND[key]
    return None


def sound_cues(sim: Sim) -> list[dict]:
    """Sons do golpe por tick: PlaySnd, som do acerto e da defesa."""
    out = []
    for e in sim.events:
        snd = e.get("snd")
        if snd and (e["kind"] in ("acerto", "defesa") or e.get("value") == "playsnd"):
            out.append({"t": e["t"], "group": snd[0], "number": snd[1], "common": snd[2],
                        "channel": e.get("channel", -1)})
    return out


def sound_track(sim: Sim, start_tick: int = 0, rate: int = 44100) -> bytes | None:
    """WAV mono com os sons do golpe a partir de `start_tick` (60 ticks por segundo). Um som
    que começou antes e ainda toca entra cortado; PlaySnd no mesmo canal corta o anterior."""
    import io
    import wave
    from .edit import _wav_arrays
    cues = sound_cues(sim)
    if not cues:
        return None
    engine = _engine_sounds(sim.char) if any(c["common"] for c in cues) else None
    clips, cut = [], {}
    for c in cues:
        bank = engine if c["common"] else sim.char.sounds
        s = bank.find(c["group"], c["number"]) if bank is not None else None
        if s is None or not s.wav:
            continue
        try:
            params, data = _wav_arrays(s.wav)
        except Exception:  # noqa: BLE001
            continue
        full = {1: 128.0, 2: 32768.0, 4: 2147483648.0}[params.sampwidth]
        mono = data.mean(axis=1) / full
        if params.framerate != rate and len(mono) > 1:
            n = max(1, round(len(mono) * rate / params.framerate))
            mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono)
        at = round((c["t"] - start_tick) * rate / 60)
        clip = [at, mono]
        if c["channel"] >= 0:
            prev = cut.get(c["channel"])
            if prev is not None:              # o mesmo canal corta o som que ainda tocava
                prev[1] = prev[1][:max(0, at - prev[0])]
            cut[c["channel"]] = clip
        clips.append(clip)
    clips = [(at, m) for at, m in clips if at + len(m) > 0]
    if not clips:
        return None
    total = max(at + len(m) for at, m in clips)
    mix = np.zeros(total)
    for at, m in clips:
        if at < 0:
            m, at = m[-at:], 0
        mix[at:at + len(m)] += m
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def chain_report(sim: Sim) -> dict:
    """Como a cadeia andou: cada passo (tick, cancela/liga, se o P2 ainda estava preso), o
    maior combo (acertos seguidos sem o P2 voltar ao parado) e onde quebrou."""
    steps = [e for e in sim.events if e["kind"] == "cadeia"]
    hits = [e for e in sim.events if e["kind"] in ("acerto", "defesa")]
    free_at = {r["t"] for r in sim.track if r["p2"]["state"] == "stand"}
    best = run = 0
    broke = None
    prev = None
    for h in hits:
        # o acerto traz o combo do P2 (o P2 pode voltar ao parado e apanhar no mesmo tick)
        restarted = h.get("combo") is not None and h["combo"] <= h.get("numhits", 1)
        if prev is not None and (h["kind"] == "defesa" or restarted
                                 or any(prev["t"] < t < h["t"] for t in free_at)):
            if broke is None:
                broke = {"t": h["t"], "state": h.get("root_state", h.get("state"))}
            run = 0
        run = run + 1 if h["kind"] == "acerto" else 0
        best = max(best, run)
        prev = h
    done = len(steps) + 1 if sim.chain else 1
    return {"chain": list(sim.chain), "reached": done, "complete": done >= len(sim.chain),
            "steps": [{"t": e["t"], "from": e["from"], "to": e["value"], "how": e["how"],
                       "p2_stuck": e["p2"] != "stand"} for e in steps],
            "hits": sum(1 for h in hits if h["kind"] == "acerto"), "combo": best, "broke": broke}


def _lit(v) -> int | None:
    m = re.match(r"\s*(-?\d+)", str(v or ""))
    return int(m.group(1)) if m else None


def combo_graph(char, sds=None) -> dict:
    """Cancelamentos ao encostar entre golpes: {estado: {destinos}}, dos ChangeState com
    movecontact/movehit dentro do golpe e dos do [Statedef -1] com `stateno = ...`."""
    sds = sds or char.all_statedefs(include_common=False)
    edges: dict[int, set[int]] = {}

    def trig(c):
        return " ".join(x for v in c.triggers().values() for x in v).lower()
    for n, (sd, _) in sds.items():
        for c in sd.controllers:
            if c.type.lower() != "changestate":
                continue
            t = trig(c)
            v = _lit(c.section.get("value"))
            if v is None or not re.search(r"move(contact|hit)", t):
                continue
            if n >= 0:
                edges.setdefault(n, set()).add(v)
                continue
            for a, b in re.findall(r"stateno\s*=\s*\[?\s*(\d+)\s*(?:,\s*(\d+))?", t):
                lo, hi = int(a), int(b or a)
                for s_ in sds:
                    if lo <= s_ <= hi and s_ != v:
                        edges.setdefault(s_, set()).add(v)
    return edges


def move_states(char) -> list[int]:
    """Estados com comando no .cmd entre 200 e 3999 (menos o 500 de carregar)."""
    t = char.commands.doc.to_text()     # o texto em memória: um golpe novo ainda não salvo já conta
    found = {int(v) for v in re.findall(r"(?im)^\s*value\s*=\s*(\d+)\s*$", t)}
    # o [Statedef -1] também pode estar nos arquivos de estado (no ZSS, command.zss): o rebaixado
    # para CNS dá os ChangeState de lá
    try:
        neg = char.all_statedefs(include_common=False).get(-1)
    except Exception:  # noqa: BLE001 — texto com erro: fica com o .cmd
        neg = None
    if neg:
        for c in neg[0].controllers:
            v = _lit(c.section.get("value")) if (c.type or "").lower() == "changestate" else None
            if v is not None:
                found.add(v)
    return sorted(v for v in found if 200 <= v < 4000 and v != 500)


def damage_report(sim) -> dict:
    """Dano do golpe simulado: cada acerto e defesa (dano, energia, pausa, tempo de golpe,
    atributo) e os totais, com a vida que sobra ao boneco (de 1000)."""
    rows = [e for e in sim.events if e["kind"] in ("acerto", "defesa", "vida")]
    hits = [e for e in rows if e["kind"] == "acerto"]
    total = sum(e.get("dealt", 0) for e in rows)
    return {"rows": rows, "hits": len(hits), "guards": sum(e["kind"] == "defesa" for e in rows), "total": total,
            "life": max(0, 1000 - total), "percent": round(total / 10, 1),
            "p1power": sum(e.get("p1power", 0) for e in rows),
            "p2power": sum(e.get("p2power", 0) for e in rows)}


def life_at(sim, t: int) -> int:
    """Vida do boneco no tick t (antes de qualquer acerto, 1000)."""
    return 1000 - sum(e.get("dealt", 0) for e in sim.events
                      if e["kind"] in ("acerto", "defesa", "vida") and e["t"] <= t)
