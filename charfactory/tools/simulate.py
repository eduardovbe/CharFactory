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
- helper: desenhado na escala `size.xscale/yscale`.
Aproximações (anotadas no relatório quando usadas): física de queda simples, sem juggle,
sem defesa, sem projéteis (`Projectile`), comandos sempre falsos (o golpe é forçado).
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
    parent: "Entity | None" = None
    root: "Entity | None" = None
    scale: tuple = (1.0, 1.0)
    trans: str | None = None
    alpha: tuple | None = None
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
    prevstateno: int = 0
    power: int = 3000

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
            return (s.p2.pos[0] - self.pos[0]) * self.facing
        if name == "p2dist y":
            return s.p2.pos[1] - self.pos[1]
        if name == "p2bodydist x":
            return (s.p2.pos[0] - self.pos[0]) * self.facing - s.body_w
        if name == "p2bodydist y":
            return s.p2.pos[1] - self.pos[1]
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
        if name == "numtarget":
            return 1 if (self.movehit or (self.root and self.root.movehit)) and s.p2.state != "stand" else 0
        if name == "numhelper":
            return sum(1 for h in s.helpers if h.alive and (arg is None or h.helper_id == arg))
        if name == "numexplod":
            return sum(1 for e in s.explods if e.alive and (arg is None or e.id == arg))
        if name == "numenemy":
            return 1
        if name == "ctrl":
            return self.ctrl
        if name == "ailevel":
            return 0
        if name == "roundstate":
            return 2
        if name == "alive":
            return 1
        if name == "ishelper":
            return 1 if self.is_helper else 0
        if name in ("statetype", "movetype", "physics"):
            return getattr(self, name)
        if name == "p2statetype":
            return "A" if s.p2.pos[1] < 0 else ("L" if s.p2.state == "down" else "S")
        if name == "p2movetype":
            return "H" if (s.p2.state != "stand" or s.p2.custom) else "I"
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
        if name in ("backedgedist", "frontedgedist", "backedgebodydist", "frontedgebodydist"):
            return 200
        if name == "screenpos x":
            return 160
        if name == "screenpos y":
            return 200
        if name in ("animexist", "selfanimexist"):
            return 1 if s.char.anims.get(int(arg)) is not None else 0
        if name in ("const240p", "const480p", "const720p"):   # localcoord 320x240
            return arg * {"const240p": 1, "const480p": 0.5, "const720p": 0.25}[name]
        if name == "screenwidth":
            return 320
        if name == "screenheight":
            return 240
        if name == "teamside":
            return 1
        if name == "inguarddist":
            return 0
        if name == "id":
            return id(self) % 10000
        if name == "matchover":
            return 0
        if name == "win" or name == "lose":
            return 0
        if name == "const":
            return self.const(arg)
        raise simexpr.Unsupported(tr("trigger {0}").format(name))

    def const(self, key):
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
        if clock and self.sim.ikemen:
            # Ikemen (anim.go, Action): no tick em que AnimTime = 0 o quadro já voltou ao laço,
            # mas o curtime (o relógio do AnimElem) só volta no passo seguinte; por isso
            # AnimElem = 1 não dispara de novo na volta (medido com o log por tick)
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


@dataclass
class Dummy:
    """Oponente boneco: caixa do tamanho do char, reage ao acerto."""
    pos: list
    facing: int = -1
    vel: list = field(default_factory=lambda: [0.0, 0.0])
    state: str = "stand"
    shake: int = 0
    hittime: int = 0
    fall: bool = False
    t: int = 0
    hits: int = 0
    stateno: int = 0
    vars: dict = field(default_factory=dict)
    custom: int = 0          # estado do atacante em que o boneco está (HitDef p2stateno, TargetState)
    pose: str = "stand"      # postura parada: "stand", "crouch" ou "air" (paira no alto até apanhar)
    guard: bool = False      # defende o que o guardflag deixa

    def state_no(self) -> int:
        if self.custom:
            return self.custom
        if self.state == "stand":
            return {"stand": 0, "crouch": 11, "air": 50}[self.pose]
        if self.state == "guardhit":
            return 152 if self.pose == "crouch" else 150
        return {"hit": 5000, "air": 5030, "fall": 5050, "down": 5110}[self.state]

    # o boneco também responde a gatilhos redirecionados (enemynear, pos x)
    def trigger(self, name, arg):
        if name == "pos x":
            return self.pos[0] - self._sim.origin_x
        if name == "pos y":
            return self.pos[1]
        if name == "vel x":
            return self.vel[0]
        if name == "vel y":
            return self.vel[1]
        if name == "facing":
            return self.facing
        if name == "stateno":
            return self.state_no()
        if name == "statetype":
            return "A" if self.pos[1] < 0 else ("L" if self.state == "down" else
                                                "C" if self.pose == "crouch" else "S")
        if name == "movetype":
            return "H" if (self.state != "stand" or self.custom) else "I"
        if name in ("life", "lifemax"):
            return 1000
        if name == "time":
            return self.t
        if name == "alive":
            return 1
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

CTRL_IGNORED = {"playsnd", "stopsnd", "screenbound", "nothitby", "hitby",
                "afterimage", "afterimagetime", "envshake", "envcolor", "assertspecial_",
                "makedust", "sndpan", "width", "attackmulset", "defencemulset", "hitoverride",
                "posfreeze", "lifeadd", "lifeset", "displaytoclipboard", "appendtoclipboard",
                "clearclipboard", "victoryquote", "forcefeedback", "gamemakeanim", "bgpalfx",
                "allpalfx", "remappal", "hitfalldamage", "hitfallvel",
                "hitfallset", "fallenvshake", "targetfacing", "targetlifeadd", "targetpoweradd",
                "targetdrop", "reversaldef", "attackdist", "offset", "modifyexplod",
                "explodbindtime", "zoom", "camera", "powerset"}
# controladores registrados como evento "ctrl" (som, tremor, cor, rastro, sumir, prender)
REVERSAL_T = 8           # tick da janela do ReversalDef em que o boneco "ataca" o contra
HELPER_MAX = 56          # helpers vivos por jogador (MUGEN 1.1)
CTRL_LOGGED = {"playsnd", "envshake", "palfx", "bgpalfx", "allpalfx", "envcolor", "afterimage",
               "assertspecial", "targetbind", "bindtotarget", "targetstate", "posadd", "posset",
               "turn", "superpause", "pause", "projectile", "angledraw", "makedust",
               "reversaldef", "hitoverride", "screenbound"}


class Sim:
    def __init__(self, char, state: int, p2_dist: float = 50.0, seed: int = 7,
                 max_ticks: int = 900, commands: tuple = (), p2_clsn: list | None = None,
                 p2_pose: str = "stand", p2_guard: bool = False, chain: tuple = ()):
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
        self.explods: list[Explod] = []
        self.pause = 0
        self.pause_owner: Entity | None = None
        self.max_ticks = max_ticks
        self.ikemen = bool(getattr(char, "is_zss", False))   # regras do Ikemen (expressões, física)
        try:                                       # localcoord do char / 320 (escala dos limites)
            self.lc_ratio = float(char.info("localcoord", "320,240").split(",")[0]) / 320 or 1.0
        except ValueError:
            self.lc_ratio = 1.0
        self.consts = self._load_consts()
        self.stand_h = self._stand_height()
        self.body_w = 14.0
        self.p1 = Entity(self, "P1", state)
        self.p1.root = None
        self.p1.ctrl = 0
        self.p2 = Dummy(pos=[p2_dist, 0.0])
        self.p2._sim = self
        self.track: list[dict] = []
        self.enter(self.p1, state, first=True)
        self.p1.started = False
        if self.p1.statetype == "A":                 # golpe aéreo: começa no alto do pulo, caindo
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
                    "movement.crouch.friction": 0.82, "size.height": 60}
        return defaults.get(key, 0)

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
        ent.hitdef = None
        ent.hitdef_hit = False
        if not first:
            ent.movehit = ent.movecontact = ent.moveguarded = 0
            ent.hitcount = 0
        if sd is None:
            self.note(tr("statedef {0} does not exist").format(n))
            return
        h = sd[0]
        if "type" in h:
            ent.statetype = h["type"].strip().upper()[:1]
        if "movetype" in h:
            ent.movetype = h["movetype"].strip().upper()[:1]
        if "physics" in h:
            ent.physics = h["physics"].strip().upper()[:1]
        if "anim" in h:                                  # o MUGEN reinicia a anim mesmo se for a mesma
            ent.anim, ent.anim_t = int(self.ev(ent, h["anim"], ent.anim)), 0
        if "velset" in h:
            ent.vel = list(self.evpair(ent, h["velset"]))
        if "ctrl" in h:
            ent.ctrl = int(self.ev(ent, h["ctrl"], 0))
        if "sprpriority" in h:
            ent.sprpriority = int(self.ev(ent, h["sprpriority"], 0))
        if "poweradd" in h:
            (ent.root or ent).power += int(self.ev(ent, h["poweradd"], 0))
        if "facep2" in h and self.ev(ent, h["facep2"], 0):
            self.set_facing(ent, 1 if self.p2.pos[0] >= ent.pos[0] else -1)
        self.events.append({"t": self.tick, "who": ent.name, "kind": "estado", "value": n})

    def run_entity(self, ent: Entity):
        frozen = ent.frozen
        if not frozen:                           # AngleDraw vale só no tick em que roda
            ent.draw_angle, ent.draw_scale = None, None
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
            if switches > 8:
                self.note(tr("ChangeState loop on the same tick"))
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

    def _exec(self, ent: Entity, c: dict):
        typ = c.get("type", "")
        p = c["params"]
        g = lambda k, d=0: self.ev(ent, p[k], d) if k in p else d
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
        if typ == "reversaldef" and ent is self.p1 and "p1stateno" in p and ent.time >= REVERSAL_T                 and self.p2.state == "stand" and self._reversal_touch(ent):
            # o boneco não ataca: no contra-ataque ele "ataca" no tick REVERSAL_T da janela, perto,
            # e o contra dispara (sem isso a simulação via só a pose de guarda e nunca o contra). No
            # MUGEN o ReversalDef só pega golpe cuja Clsn1 toca uma Clsn1 de quem contra-ataca:
            # guarda sem Clsn1 nunca dispara
            self.events.append({"t": self.tick, "who": ent.name, "kind": "ctrl", "value": "reversal",
                                "label": tr("dummy attacked: counter triggered"), "state": ent.stateno,
                                "root_state": ent.stateno, "arg": ""})
            pt = [x.strip() for x in p.get("pausetime", "0,0").split(",")]
            self.p2.shake = int(self.ev(ent, pt[-1], 0)) if pt[-1] else 0
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
        if typ == "changeanim":
            ent.anim, ent.anim_t = int(g("value", ent.anim)), int(g("elem", 1)) - 1
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
        if typ in ("varset", "varadd"):
            for k, v in p.items():
                m = re.fullmatch(r"(f?var|zsslocal)\((\d+)\)", k)
                if m:
                    d = {"fvar": ent.fvars, "var": ent.vars, "zsslocal": ent.locals}[m.group(1)]
                    val = self.ev(ent, v, 0)
                    d[int(m.group(2))] = (d.get(int(m.group(2)), 0) + val) if typ == "varadd" else val
            if "v" in p or "fv" in p:
                d = ent.fvars if "fv" in p else ent.vars
                n = int(g("fv" if "fv" in p else "v"))
                val = g("value")
                d[n] = (d.get(n, 0) + val) if typ == "varadd" else val
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
        if typ == "removeexplod":
            i = int(g("id", -1))
            for e in self.explods:
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
            ent.hitdef_hit = False
            return
        if typ in ("superpause", "pause"):
            self.pause = int(g("time", 30))
            self.pause_owner = ent
            mt = int(g("movetime", 0))
            ent.pausemove = mt
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
            ox, oy = self.evpair(ent, p.get("pos", "0,0"))
            self.p2.pos = [ent.pos[0] + ox * ent.facing, ent.pos[1] + oy]
            return
        if typ == "targetstate":
            # o boneco vai para um estado do atacante (ou volta aos dele: 5000-5199 e 0)
            v = int(g("value", 0))
            self.p2.custom = 0 if (v == 0 or 5000 <= v < 5200) else v
            return
        if typ == "targetvelset":
            self.p2.vel = [float(g("x")) * -self.p2.facing, float(g("y"))]
            return
        if typ == "projectile":
            self.note(tr("Projectile is not simulated"))
            return
        if typ in CTRL_IGNORED or typ.startswith("snd"):
            return
        self.note(tr("controller {0} not simulated").format(typ))

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
        ox, oy = self.evpair(ent, p.get("pos", "0,0"))
        postype = p.get("postype", "p1").lower()
        rel = int(g("facing", 1)) or 1
        if postype == "p2":
            base = self.p2
            bf = self.p2.facing
            x, y = base.pos[0] + ox * bf, base.pos[1] + oy
            facing = bf * rel
            bind = None                                 # preso ao oponente: só no nascimento
        elif postype in ("left", "right", "front", "back", "none"):
            # tela: x a partir da borda; o simulador usa a câmera centrada no começo
            x = self.origin_x + (-160 if postype in ("left", "back") else 160 if postype in ("right", "front") else 0) + ox
            y = oy - 240 + 200 if postype != "none" else oy
            facing = rel
            bind = None
        else:
            x, y = ent.pos[0] + ox * ent.facing, ent.pos[1] + oy
            facing = ent.facing * rel
            bind = ent
        sx, sy = self.evpair(ent, p.get("scale", "1,1"), (1, 1))
        vx, vy = self.evpair(ent, p.get("vel", "0,0"))
        axx, ayy = self.evpair(ent, p.get("accel", "0,0"))
        e = Explod(anim=anim, pos=[x, y], facing=facing, vfacing=int(g("vfacing", 1)) or 1,
                   scale=(sx, sy), angle=float(g("angle", 0)), spr=int(g("sprpriority", 0)),
                   trans=p.get("trans", "").lower() or None, removetime=int(g("removetime", -2)),
                   bindtime=int(g("bindtime", 0)), bind_to=bind, offset=(ox, oy), vel=(vx, vy),
                   accel=(axx, ayy), id=int(g("id", -1)), owner=ent,
                   space=("screen" if p.get("space", "").lower() == "screen"
                          or postype in ("left", "right", "front", "back") else "stage"),
                   label=c["label"], removeongethit=bool(g("removeongethit", 0)), born=self.tick,
                   pausemove=int(g("supermovetime", 0)) or int(g("pausemovetime", 0)),
                   # faísca: nasce do acerto (movehit/movecontact, ou o contador de acertos que
                   # os modelos do repertório usam: hitcount > var(1))
                   on_hit=any(re.search(r"(?i)move(hit|contact)|\bhitcount\b", t)
                              for ts in [c["triggerall"], *c["triggers"].values()] for t in ts))
        self.explods.append(e)
        self.events.append({"t": self.tick, "who": ent.name, "kind": "explod", "value": anim,
                            "label": c["label"]})

    def make_helper(self, ent: Entity, c):
        p = c["params"]
        g = lambda k, d=0: self.ev(ent, p[k], d) if k in p else d
        ox, oy = self.evpair(ent, p.get("pos", "0,0"))
        postype = p.get("postype", "p1").lower()
        if postype == "p2":
            bx, by, bf = self.p2.pos[0], self.p2.pos[1], self.p2.facing
        elif postype in ("left", "right", "front", "back"):
            bx, by, bf = self.origin_x + (-160 if postype in ("left", "back") else 160), 0.0, 1
        else:
            bx, by, bf = ent.pos[0], ent.pos[1], ent.facing
        root = ent.root or ent
        # o motor tem um teto de helpers vivos (MUGEN: 56 por jogador); sem ele, helper que cria
        # helper no 1º tick vira laço infinito (Kashimo 3100)
        if sum(1 for x in self.helpers if x.alive) >= HELPER_MAX:
            return
        h = Entity(self, f"helper {int(g('id', 0))}", int(g("stateno", 0)))
        h.is_helper = True
        h.helper_id = int(g("id", 0))
        h.parent, h.root = ent, root
        h.facing = ent.facing * (1 if int(g("facing", 1)) >= 0 else -1)
        h.pos = [bx + ox * (bf if postype != "p2" else bf), by + oy]
        h.scale = (float(g("size.xscale", 1)), float(g("size.yscale", 1)))
        h.pausemove = int(g("pausemovetime", 0)) or int(g("supermovetime", 0))
        h.anim = ent.anim
        h.screen = postype in ("left", "right", "front", "back")
        self.helpers.append(h)
        self.enter(h, h.stateno, first=True)
        self.run_entity(h)                       # o helper roda o 1º tick ao nascer (BindToRoot já vale)
        h.born_run = self.tick                   # ... e não roda de novo no mesmo tick (step)
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

    def _size(self, src, air: bool):
        """(front, back, height) da caixa de tamanho: constantes [Size] do char ou do oponente."""
        get = (lambda k, d: float(src.get(k, d))) if isinstance(src, dict) else (
            lambda k, d: float(self.const(f"size.{k}") or d))
        h = get("height", 60)
        if air:
            return get("air.front", 12), get("air.back", 12), h
        return get("ground.front", 16), get("ground.back", 15), h

    def push_players(self):
        """Corpos não se atravessam (Ikemen pushDetection): se as caixas de tamanho e as Clsn2
        se sobrepõem, cada um anda metade da sobreposição para o seu lado (pesos iguais,
        pushfactor 1). PlayerPush{value: 0} desliga no tick. Só P1 × P2 (o boneco)."""
        p1, d = self.p1, self.p2
        if getattr(p1, "push_off", False) or d.state == "down":
            return
        f1, b1, h1 = self._size(None, p1.statetype == "A")
        size2 = (self.p2_clsn or {}).get("size") if isinstance(self.p2_clsn, dict) else None
        f2, b2, h2 = self._size(size2 or None, d.pos[1] < 0)
        # caixas no mundo (x à frente de cada um pelo lado em que olha)
        l1, r1 = sorted((p1.pos[0] - b1 * p1.facing, p1.pos[0] + f1 * p1.facing))
        l2, r2 = sorted((d.pos[0] - b2 * d.facing, d.pos[0] + f2 * d.facing))
        overlap = min(r1, r2) - max(l1, l2)
        if overlap <= 0 or p1.pos[1] - h1 > d.pos[1] or d.pos[1] - h2 > p1.pos[1]:
            return
        mine, theirs = self.boxes(p1, "2"), self.p2_box()   # Clsn2 também precisam se tocar
        if not any(a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]
                   for a in mine for b in theirs):
            return
        side = 1 if d.pos[0] >= p1.pos[0] else -1             # de que lado o P2 está
        p1.pos[0] -= side * overlap / 2
        d.pos[0] += side * overlap / 2

    def check_hits(self, ent: Entity):
        hd = ent.hitdef
        if not hd or ent.hitdef_hit or ent.hitpause:
            return
        p = hd["params"]
        flag = p.get("hitflag", "MAF").upper()
        d = self.p2
        if d.state == "down" and "D" not in flag:
            return
        if d.pos[1] < 0 and "A" not in flag and "F" not in flag:
            return
        if d.state == "fall" and "F" not in flag:
            return
        # no chão: H pega em pé, L agachado, M os dois
        if d.pos[1] >= 0 and d.state not in ("down", "fall") and "M" not in flag \
                and ("L" if d.pose == "crouch" else "H") not in flag:
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
        ent.hitdef_hit = True
        ent.movehit = ent.movecontact = 1
        ent.hit_now = True
        ent.hitcount += 1
        if ent.root is not None:
            ent.root.movehit = ent.root.movecontact = 1
            ent.root.hit_now = True
        pt = g("pausetime", (0, 0))
        ent.hitpause = int(pt[0])
        air = d.pos[1] < 0 or d.state in ("air", "fall")
        gv = g("air.velocity" if air else "ground.velocity", (0, 0))
        fall = bool(self.ev(ent, p.get("fall", "0"), 0)) or air and bool(self.ev(ent, p.get("air.fall", p.get("fall", "0")), 0))
        # velocidade no mundo (Ikemen: ghv.xvel = velocity.x * -facing do atacante)
        d.vel = [float(gv[0]) * -ent.facing, float(gv[1])]
        d.shake = int(pt[1])
        d.hittime = int(self.ev(ent, p.get("ground.hittime", "15"), 15))
        d.fall = fall or d.fall
        d.state = "fall" if (fall or air or d.vel[1] < 0) else "hit"
        d.facing = -ent.facing if d.state != "down" else d.facing
        d.t = 0
        d.hits += 1
        # p2stateno: o oponente passa a rodar o estado do atacante (o gatilho p2stateno o vê)
        d.custom = int(self.ev(ent, p["p2stateno"], 0)) if p.get("p2stateno", "").strip() else 0
        self.events.append({"t": self.tick, "who": ent.name, "kind": "acerto", "label": hd["label"],
                            "ci": hd.get("index"),
                            "value": [round(d.vel[0], 2), round(d.vel[1], 2)], "contact": contact,
                            "engine_spark": p.get("sparkno", "0").strip() not in ("-1",),
                            "state": ent.stateno, "root_state": (ent.root or ent).stateno,
                            # peso do acerto (nota de qualidade: o final tem de pesar mais)
                            "damage": int(g("damage", (0, 0))[0]), "pause": [int(pt[0]), int(pt[1])],
                            "fall": d.fall, "shake": int(self.ev(ent, p.get("envshake.time", "0"), 0)) > 0,
                            "sound": bool(p.get("hitsound", "").strip()),
                            "snd": self._sound(ent, p.get("hitsound", ""), hitdef=True),
                            "hittime": d.hittime,
                            # o oponente vai para um estado do atacante (agarrão cinematográfico)
                            "p2state": bool(p.get("p2stateno", "").strip())})

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
        ent.hitdef_hit = True
        ent.movecontact = ent.moveguarded = 1
        ent.hit_now = True
        if ent.root is not None:
            ent.root.movecontact = ent.root.moveguarded = 1
            ent.root.hit_now = True
        pt = g("guard.pausetime", g("pausetime", (0, 0)))
        ent.hitpause = int(pt[0])
        d.shake = int(pt[1])
        gv = self.ev(ent, p["guard.velocity"], 0) if "guard.velocity" in p else g("ground.velocity", (0, 0))[0]
        d.vel = [float(gv) * -ent.facing, 0.0]
        d.hittime = int(self.ev(ent, p.get("guard.hittime", p.get("ground.hittime", "15")), 15))
        d.state, d.t = "guardhit", 0
        d.facing = -ent.facing
        self.events.append({"t": self.tick, "who": ent.name, "kind": "defesa", "label": hd["label"],
                            "ci": hd.get("index"),
                            "contact": contact, "state": ent.stateno, "root_state": (ent.root or ent).stateno,
                            "pause": [int(pt[0]), int(pt[1])], "hittime": d.hittime,
                            "snd": self._sound(ent, p.get("guardsound", "6,0"), hitdef=True)})

    def step_dummy(self):
        d = self.p2
        d.t += 1
        if d.shake > 0:
            d.shake -= 1
            return
        if d.state in ("hit", "guardhit"):
            d.pos[0] += d.vel[0]
            d.vel[0] *= 0.88
            d.hittime -= 1
            if d.hittime <= 0:
                d.state, d.vel = "stand", [0.0, 0.0]
        elif d.state in ("fall", "air"):
            d.pos[0] += d.vel[0]
            d.pos[1] += d.vel[1]
            d.vel[1] += self.const("movement.yaccel") or 0.44
            if d.pos[1] >= 0 and d.vel[1] > 0:
                d.pos[1] = 0.0
                d.state, d.vel, d.t = "down", [0.0, 0.0], 0
        elif d.state == "down" and d.t > 60:
            d.state = "stand"

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
            if ent.started:
                if ent.hitpause > 0:
                    ent.hitpause -= 1
                    ent.frozen = True
                else:
                    ent.time += 1
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
            if not (starts and ent.pausemove <= 0):
                ent.pos[0] += ent.vel[0] * ent.facing
                ent.pos[1] += ent.vel[1]
            if ent.physics == "A":
                ent.vel[1] += self.const("movement.yaccel") or 0.44
                if ent.pos[1] >= 0 and ent.vel[1] > 0 and not ent.is_helper:
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
        # como no Ikemen (collisionDetection): primeiro os corpos se empurram, depois os acertos
        if not self.p1.frozen and self.p1 in moving:
            self.push_players()
        for ent in moving:
            if ent.alive and not ent.frozen:
                self.check_hits(ent)
        for ent in moving:
            ent.hit_now = False
            if paused:
                ent.pausemove -= 1
        if not paused:
            self.step_dummy()
        for e in self.explods:
            if not e.alive:
                continue
            if paused:
                if e.pausemove <= 0:
                    continue
                e.pausemove -= 1
            if e.bind_to is not None and (e.bindtime < 0 or e.t < e.bindtime):
                b = e.bind_to
                e.pos = [b.pos[0] + e.offset[0] * b.facing, b.pos[1] + e.offset[1]]
            elif e.t > 0:
                e.pos[0] += e.vel[0] * e.facing
                e.pos[1] += e.vel[1]
                e.vel = (e.vel[0] + e.accel[0], e.vel[1] + e.accel[1])
            a = self.char.anims.get(e.anim)
            ends = a is not None and a.total_time > 0 and all(f.time > 0 for f in a.frames)
            if e.removetime == -2 and (a is None or (ends and e.t >= a.total_time)):
                e.alive = False
            elif e.removetime >= 0 and e.t > e.removetime:
                e.alive = False
        if paused:
            self.pause -= 1
        self._paused_at_start = paused            # o que o Ikemen registra como pausa do tick
        self.snapshot()
        for e in self.explods:
            if e.alive and (not paused or e.pausemove > 0):
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
        while self.tick < self.max_ticks:
            self.step()
            if self.chain:
                self.chain_step()
            if until_idle and self.p1.stateno in (0, 52, 11, 20) and self.tick > 5 \
                    and not any(h.alive for h in self.helpers) and not any(e.alive for e in self.explods) \
                    and self.p2.state in ("stand", "down"):
                break
        return self

    # ---- registro de cada tick (para desenhar e medir)
    def snapshot(self):
        ents = []
        for ent in [self.p1] + self.helpers:
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
                             inv=inv, trans=ent.trans, clsn1=self.boxes(ent, "1"),
                             clsn2=self.boxes(ent, "2"), helper=ent.is_helper,
                             stype=ent.statetype, hd=bool(ent.hitdef) and not ent.hitdef_hit,
                             atk=bool(ent.hitdef),
                             guardflag=(ent.hitdef or {}).get("params", {}).get("guardflag", "").upper()))
        exps, scr = [], []
        for e in self.explods:
            if e.alive and e.space == "screen":
                # cut-in, camadas da cena: fora das medidas do palco, mas a folha tick a tick
                # desenha (em coordenadas da tela 320x240)
                f = frame_at(self.char, e.anim, e.t)
                if f is not None:
                    scr.append(dict(anim=e.anim, frame=f, pos=(e.pos[0] - self.origin_x + 160, e.pos[1] + 40),
                                    facing=e.facing, scale=e.scale, angle=e.angle, spr=e.spr,
                                    trans=e.trans, label=e.label))
            if not e.alive or e.space == "screen":       # cut-in e linhas na tela: fora da folha do palco
                continue
            f = frame_at(self.char, e.anim, e.t)
            if f is None:
                continue
            exps.append(dict(anim=e.anim, frame=f, pos=tuple(e.pos), facing=e.facing,
                             vfacing=e.vfacing, scale=e.scale, angle=e.angle, spr=e.spr,
                             trans=e.trans, label=e.label, owner=e.owner.name, age=e.t,
                             on_hit=e.on_hit, born=e.born, key=id(e)))
        self.track.append(dict(t=self.tick, ents=ents, explods=exps,
                               helper_states=sorted({h.stateno for h in self.helpers if h.alive}),
                               p2=dict(pos=tuple(self.p2.pos), state=self.p2.state,
                                       facing=self.p2.facing, shake=self.p2.shake,
                                       pose=self.p2.pose, guard=self.p2.guard),
                               p2box=self.p2_box(),
                               pause=self.pause > 0,
                               paused=getattr(self, "_paused_at_start", False),
                               nobg=getattr(self, "_nobg_tick", -1) == self.tick, screen=scr))


def simulate(char, state: int, p2_dist: float = 50.0, seed: int = 7, max_ticks: int = 900,
             commands: tuple = (), p2_clsn: list | None = None, p2_pose: str = "stand",
             p2_guard: bool = False, chain: tuple = ()) -> Sim:
    """Roda o estado `state` (e os que ele chama) até voltar ao parado. p2_clsn: as Clsn2 do
    oponente (ex.: `opponent_clsn(kfm)`); sem elas o P2 é um boneco do tamanho do char.
    p2_pose ("stand", "crouch", "air") e p2_guard dizem como o P2 espera o golpe; chain é uma
    cadeia de estados (o 1º é `state`) que entram em seguida, para testar combos."""
    return Sim(char, state, p2_dist, seed, max_ticks, commands, p2_clsn, p2_pose, p2_guard,
               chain).run()


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
        mode, al = _trans(x["frame"])
        if (x["trans"] or "") in ("add", "addalpha", "add1") or mode == "add":
            # brilho aditivo sobre o que estiver atrás: vira alfa pelo brilho do pixel
            a = np.asarray(im.convert("RGBA"), np.float32)
            a[..., 3] = a[..., 3] * a[..., :3].max(axis=2) / 255 * al
            im = Image.fromarray(a.astype(np.uint8), "RGBA")
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
        f = next((f for k in p2_keys(p2["state"], p2.get("pose", "stand"))
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
        mode, al = _trans(e["frame"])
        if e["trans"] in ("add", "addalpha", "add1"):
            mode = "add"
        items.append((e["spr"], e["name"], (im, ax, ay, e["pos"][0], e["pos"][1], e["scale"][0],
                                            e["scale"][1], e["facing"], e.get("angle", 0.0), mode, al)))
    for x in rec["explods"]:
        im, ax, ay = _frame_img(sim.char, x["frame"])
        if im is None:
            continue
        if x["vfacing"] < 0:
            im = im.transpose(Image.FLIP_TOP_BOTTOM)
            ay = im.height - ay
        mode, al = _trans(x["frame"])
        if x["trans"] in ("add", "addalpha", "add1"):
            mode = "add"
        elif x["trans"] == "sub":
            mode = "sub"
        items.append((x["spr"], "fx", (im, ax, ay, x["pos"][0], x["pos"][1], x["scale"][0],
                                       x["scale"][1], x["facing"], x["angle"], mode, al)))
    items.sort(key=lambda t: t[0])
    for _, who, (im, ax, ay, x, y, sx, sy, fc, ang, mode, al) in items:
        cv.blit(im, ax, ay, x, y, sx, sy, fc, ang, mode, al)
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


def _sprite_measure(sim: Sim, f, what: str):
    """Medida de um quadro (pixels visíveis ou área apagada), guardada na simulação: um super
    repete os mesmos quadros em centenas de ticks e decodificar cada vez levava ~40 s."""
    cache = sim.__dict__.setdefault("_fx_cache", {})
    key = (_frame_key(f), what)
    if key in cache:
        return cache[key]
    im, ax, ay = _frame_img(sim.char, f)
    out = None
    if im is not None:
        a = np.asarray(im)
        mx = a[..., :3].max(2)
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
    return out


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
                add("abaixo-do-chao", tr("{0}: {1:.0%} of the effect below the ground (up to {2:.0f} px)").format(lab, frac, float(pts[1].max())), ("chao", lab))
            if (b[3] - b[1] > 2.6 * h or b[2] - b[0] > 4.5 * h) and not kit:
                if x["anim"] < 30000 or x["anim"] >= 35000:
                    add("efeito-gigante", tr("{0}: {1:.0f}x{2:.0f} px (the character is {3:.0f})").format(lab, b[2]-b[0], b[3]-b[1], h), ("big", lab))
            w_, h_ = b[2] - b[0], b[3] - b[1]
            round_ = max(w_, h_) < 1.8 * max(1.0, min(w_, h_))
            # na cena (cenário apagado pelo próprio char) o fundo é preto: o halo não acinzenta nada
            if x["trans"] != "sub" and not x["on_hit"] and not kit and round_ and not rec.get("nobg"):
                area = _dim_area(sim, x)
                if area > 2.5 * h * h:
                    add("halo-escuro", tr("{0}: dimmed area of {1:.0f} px² ({2:.1f}x the character's height²) — added to the stage it becomes a gray disc").format(lab, area, area / (h * h)), ("halo", lab))
    # golpes que não acertam
    acts = [e for e in sim.events if e["kind"] == "acerto"]
    if not acts and not any(e["kind"] == "defesa" for e in sim.events) and any(r for r in sim.track if any(e.get("clsn1") for e in r["ents"])):
        add("sem-acerto", tr("no hit on the dummy at {0:.0f} px").format(sim.p2.pos[0]), ("miss",))
    # oponente jogado para o lado do atacante no fim
    if acts and len(sim.track) > 2:
        p1x = sim.track[-1]["ents"][0]["pos"][0] if sim.track[-1]["ents"] else 0
        first = next(r for r in sim.track if r["t"] > acts[-1]["t"])
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
            add("empurrao-invertido", tr("the last hit pushes the opponent toward the attacker (check the sign of ground.velocity x)"), ("push",))
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
        p2_free = next((i for i in after if track[i]["p2"]["state"] == "stand"), None)
        down = any(track[i]["p2"]["state"] in ("fall", "down") for i in after)
        out["knockdown"] = down or any(e.get("fall") or e.get("p2state") for e in contacts[-1:])
        if p2_free is not None and not out["knockdown"] and free < len(track):
            out["advantage"] = p2_free - free
    return out


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
        if prev is not None and (h["kind"] == "defesa" or any(prev["t"] < t < h["t"] for t in free_at)):
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
    t = char.path_of("cmd").read_text("latin-1")
    return sorted({int(v) for v in re.findall(r"(?im)^\s*value\s*=\s*(\d+)\s*$", t)
                   if 200 <= int(v) < 4000 and int(v) != 500})
