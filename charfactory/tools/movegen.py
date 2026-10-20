"""Gerador de golpes estruturado (Move Generator / Move Wizard Backend).

Conecta o modelo de dados de um golpe às rotinas do movespec, autochar e formatos
do MUGEN, gerando blocos CNS, entradas no CMD e controladores no [Statedef -1].
"""
from __future__ import annotations

from typing import Any
from ..formats import mtext
from ..project import Character
from . import movespec, ops
from ..i18n import tr

MOVE_KINDS = {
    "normal": tr("Normal (Ground)"),
    "crouch": tr("Crouching Normal"),
    "air": tr("Air Normal"),
    "special": tr("Special (Ground)"),
    "air_special": tr("Air Special"),
    "super": tr("Super / Hyper"),
    "projectile": tr("Projectile Shot"),
    "throw": tr("Throw / Grab"),
}

KIND_DEFAULTS: dict[str, dict[str, Any]] = {
    "normal": {
        "statetype": "S",
        "physics": "S",
        "movetype": "A",
        "damage": (50, 6),
        "guardflag": "MA",
        "ground_type": "High",
        "pausetime": (8, 9),
        "ground_velocity": (-3.5, 0.0),
        "air_velocity": (-2.5, -3.0),
        "fall": False,
        "fall_recover": True,
        "power": 0,
        "superpause": None,
        "hitsound": "S5,0",
        "guardsound": "S6,0",
    },
    "crouch": {
        "statetype": "C",
        "physics": "C",
        "movetype": "A",
        "damage": (45, 5),
        "guardflag": "L",
        "ground_type": "Low",
        "pausetime": (8, 9),
        "ground_velocity": (-3.0, 0.0),
        "air_velocity": (-2.5, -3.0),
        "fall": False,
        "fall_recover": True,
        "power": 0,
        "superpause": None,
        "hitsound": "S5,0",
        "guardsound": "S6,0",
    },
    "air": {
        "statetype": "A",
        "physics": "A",
        "movetype": "A",
        "damage": (55, 6),
        "guardflag": "MA",
        "ground_type": "High",
        "pausetime": (8, 9),
        "ground_velocity": (-3.0, 0.0),
        "air_velocity": (-2.5, -3.0),
        "fall": False,
        "fall_recover": True,
        "power": 0,
        "superpause": None,
        "hitsound": "S5,0",
        "guardsound": "S6,0",
    },
    "special": {
        "statetype": "S",
        "physics": "S",
        "movetype": "A",
        "damage": (100, 15),
        "guardflag": "MA",
        "ground_type": "High",
        "pausetime": (10, 12),
        "ground_velocity": (-5.0, 0.0),
        "air_velocity": (-3.5, -3.5),
        "fall": True,
        "fall_recover": True,
        "power": 1000,
        "superpause": None,
        "hitsound": "S5,1",
        "guardsound": "S6,0",
    },
    "air_special": {
        "statetype": "A",
        "physics": "A",
        "movetype": "A",
        "damage": (100, 15),
        "guardflag": "MA",
        "ground_type": "High",
        "pausetime": (10, 12),
        "ground_velocity": (-4.5, 0.0),
        "air_velocity": (-3.5, -3.5),
        "fall": True,
        "fall_recover": True,
        "power": 1000,
        "superpause": None,
        "hitsound": "S5,1",
        "guardsound": "S6,0",
    },
    "super": {
        "statetype": "S",
        "physics": "S",
        "movetype": "A",
        "damage": (280, 40),
        "guardflag": "MA",
        "ground_type": "High",
        "pausetime": (14, 16),
        "ground_velocity": (-6.5, -2.5),
        "air_velocity": (-5.0, -4.0),
        "fall": True,
        "fall_recover": False,
        "power": 3000,
        "superpause": {"time": 30, "darken": 1, "unhittable": 1},
        "hitsound": "S5,2",
        "guardsound": "S6,0",
    },
    "projectile": {
        "statetype": "S",
        "physics": "S",
        "movetype": "A",
        "damage": (70, 10),
        "guardflag": "MA",
        "ground_type": "High",
        "pausetime": (6, 8),
        "ground_velocity": (-4.0, 0.0),
        "air_velocity": (-3.0, -3.0),
        "fall": False,
        "fall_recover": True,
        "power": 1000,
        "superpause": None,
        "hitsound": "S5,1",
        "guardsound": "S6,0",
    },
    "throw": {
        "statetype": "S",
        "physics": "S",
        "movetype": "A",
        "damage": (90, 0),
        "guardflag": "",
        "ground_type": "High",
        "pausetime": (8, 8),
        "ground_velocity": (-4.0, -4.0),
        "air_velocity": (-4.0, -4.0),
        "fall": True,
        "fall_recover": False,
        "power": 0,
        "superpause": None,
        "hitsound": "S5,0",
        "guardsound": "S6,0",
    },
}


def get_kind_defaults(kind: str) -> dict[str, Any]:
    """Retorna uma cópia dos valores padrão para o tipo de golpe."""
    return dict(KIND_DEFAULTS.get(kind, KIND_DEFAULTS["normal"]))


def find_anim_hit_frame(char: Character | None, anim_num: int) -> int:
    """Detecta o primeiro quadro com Clsn1 na animação; retorna 2 caso não encontre."""
    if char and char.anims:
        action = char.anims.get(anim_num)
        if action:
            for k, f in enumerate(action.frames):
                if f.clsn1:
                    return k + 1
    return 2


def build_move_cns(char: Character | None, data: dict[str, Any]) -> str:
    """Gera o bloco completo [Statedef N] em formato CNS para o golpe especificado."""
    stateno = int(data.get("state") or data.get("stateno", 200))
    name = str(data.get("name", tr("Move")))
    kind = str(data.get("kind", "normal"))
    defs = get_kind_defaults(kind)

    anim = int(data.get("anim", stateno))
    st_type = str(data.get("statetype", defs["statetype"]))
    m_type = str(data.get("movetype", defs["movetype"]))
    physics = str(data.get("physics", defs["physics"]))

    move: dict[str, Any] = {
        "state": stateno,
        "name": name,
        "kind": kind,
        "type": st_type,
        "physics": physics,
        "anim": anim,
        "sprpriority": int(data.get("sprpriority", 2)),
    }

    if "velset" in data and data["velset"]:
        vx, vy = data["velset"]
        move["velset"] = [float(vx), float(vy)]

    power = int(data.get("power", defs["power"]))
    if power > 0:
        move["power"] = power

    if data.get("voice"):
        move["voice"] = str(data["voice"]).strip()

    if data.get("sounds"):
        move["sounds"] = list(data["sounds"])

    sp = data.get("superpause")
    if sp:
        move["superpause"] = sp

    # Projétil dedicado
    if kind == "projectile" and data.get("proj_anim"):
        proj_elem = int(data.get("hit_elem", 2))
        p_anim = int(data.get("proj_anim", 1005))
        p_hit = int(data.get("proj_hit_anim", p_anim))
        p_rem = int(data.get("proj_rem_anim", p_anim))
        p_vx, p_vy = data.get("proj_vel", (5.0, 0.0))
        p_ox, p_oy = data.get("proj_offset", (25, -35))
        dmg = data.get("damage", defs["damage"])
        dmg_s = f"{dmg[0]}, {dmg[1]}" if isinstance(dmg, (list, tuple)) else str(dmg)
        g_vel = data.get("ground_velocity", defs["ground_velocity"])
        g_vel_s = f"{g_vel[0]}, {g_vel[1]}"
        a_vel = data.get("air_velocity", defs["air_velocity"])
        a_vel_s = f"{a_vel[0]}, {a_vel[1]}"

        extra_proj = f"""[State {stateno}, Projectile]
type = Projectile
trigger1 = animelem = {proj_elem}
ProjAnim = {p_anim}
ProjHitAnim = {p_hit}
ProjRemAnim = {p_rem}
offset = {p_ox}, {p_oy}
velocity = {p_vx}, {p_vy}
attr = {st_type}, SP
damage = {dmg_s}
animtype = Medium
guardflag = {data.get('guardflag', defs['guardflag'])}
pausetime = {data.get('pausetime', defs['pausetime'])[0]}, {data.get('pausetime', defs['pausetime'])[1]}
sparkno = {data.get('sparkno', -1)}
guard.sparkno = -1
hitsound = {data.get('hitsound', defs['hitsound'])}
guardsound = {data.get('guardsound', defs['guardsound'])}
ground.type = {data.get('ground_type', defs['ground_type'])}
ground.slidetime = 14
ground.hittime = 16
ground.velocity = {g_vel_s}
air.velocity = {a_vel_s}
fall = {1 if data.get('fall', defs['fall']) else 0}
"""
        move["extra"] = extra_proj
    else:
        # Ataque com HitDef
        hit_elem = int(data.get("hit_elem", find_anim_hit_frame(char, anim)))
        attr = data.get("attr")
        if not attr:
            cat = ("SA" if "special" in kind else
                   "HA" if kind == "super" else
                   "NT" if kind == "throw" else "NA")
            attr = f"{st_type}, {cat}"

        hit_dict: dict[str, Any] = {
            "elem": hit_elem,
            "attr": attr,
            "animtype": data.get("animtype", "Medium"),
            "damage": data.get("damage", defs["damage"]),
            "guardflag": data.get("guardflag", defs["guardflag"]),
            "pausetime": data.get("pausetime", defs["pausetime"]),
            "hitsound": data.get("hitsound", defs["hitsound"]),
            "guardsound": data.get("guardsound", defs["guardsound"]),
            "sparkno": data.get("sparkno", -1),
            "guard_sparkno": -1,
            "sparkxy": data.get("sparkxy", [0, -30]),
            "ground_type": data.get("ground_type", defs["ground_type"]),
            "slidetime": int(data.get("slidetime", 12)),
            "hittime": int(data.get("hittime", 15)),
            "ground_velocity": data.get("ground_velocity", defs["ground_velocity"]),
            "air_velocity": data.get("air_velocity", defs["air_velocity"]),
            "fall": bool(data.get("fall", defs["fall"])),
            "fall_recover": bool(data.get("fall_recover", defs["fall_recover"])),
        }
        move["hit"] = [hit_dict]

    if data.get("cancels"):
        move["cancel"] = list(data["cancels"])

    return movespec.build_state(char, move, {})


def build_command_cns(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Gera a definição [Command] e o controlador para [Statedef -1].

    Retorna: (dict_do_comando, texto_do_controlador_minus1)
    """
    stateno = int(data.get("state") or data.get("stateno", 200))
    name = str(data.get("name", f"Move_{stateno}"))
    cmd_name = str(data.get("command_name") or f"cmd_{stateno}")
    seq = str(data.get("command_seq") or "a")
    time_buf = int(data.get("command_time", 15))
    kind = str(data.get("kind", "normal"))
    power = int(data.get("power", 0))

    cmd_info = {
        "name": cmd_name,
        "command": seq,
        "time": time_buf,
    }

    air_move = kind in ("air", "air_special")
    conds = ["(roundstate = 2)"]
    if air_move:
        conds.append("(statetype = A)")
    else:
        conds.append("(statetype != A)")

    # Condição de ativação (ctrl ou cancelamento)
    if data.get("combo_cancel", True) and kind in ("special", "air_special", "super"):
        conds.append("((ctrl) || ((movecontact) && (stateno = [200, 699])))")
    else:
        conds.append("(ctrl)")

    if power > 0:
        conds.append(f"(power >= {power})")

    extra_cond = data.get("extra_condition")
    if extra_cond:
        conds.append(f"({str(extra_cond).strip()})")

    triggeralls = "\n".join(f"triggerall = {c}" for c in conds)
    ctrl_minus1 = f"""[State -1, {name}]
type = ChangeState
value = {stateno}
{triggeralls}
trigger1 = command = "{cmd_name}"
"""
    return cmd_info, ctrl_minus1


def apply_move_to_character(char: Character, data: dict[str, Any]) -> dict[str, Any]:
    """Aplica o golpe diretamente no personagem aberto (StateDef + Command + Statedef -1).

    Retorna um relatório com o que foi atualizado.
    """
    stateno = int(data.get("state") or data.get("stateno", 200))
    cns_text = build_move_cns(char, data)

    # 1. Aplica o Statedef
    ops.set_statedef_text(char, stateno, cns_text)

    cmd_added = False
    minus1_added = False

    # 2. Adiciona o comando ao CMD se requisitado
    if data.get("add_command", True):
        cmd_info, minus1_ctrl = build_command_cns(data)
        cmd_name = cmd_info["name"]

        # Evita duplicar comando com mesmo nome
        existing = char.commands.find(cmd_name)
        if not existing:
            char.commands.add_command(cmd_name, cmd_info["command"], cmd_info["time"])
            char.mark("commands")
            cmd_added = True

        # Adiciona controlador ao [Statedef -1]
        if -1 in char.commands.states.statedefs:
            # Verifica se já existe chamada a este stateno para evitar duplicata exata
            sd_minus1 = char.commands.states.statedefs[-1]
            already_has = any(
                c.type.lower() == "changestate" and (c.section.get("value") or "").strip() == str(stateno)
                for c in sd_minus1.controllers
            )
            if not already_has:
                char.commands.states.add_controller(-1, minus1_ctrl)
                char.mark("commands")
                minus1_added = True

    char.mark("states")
    return {
        "stateno": stateno,
        "name": data.get("name", tr("Move")),
        "cns_text": cns_text,
        "command_added": cmd_added,
        "minus1_added": minus1_added,
    }
