from __future__ import annotations
from dataclasses import dataclass
from .objectmod import Modification, TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING


@dataclass(frozen=True)
class FieldDef:
    id: str
    label: str
    type: int
    category: str = "Geral"
    levelled: bool = False
    help: str = ""
    editor_type: str = ""
    native_table: str = ""
    native_column: str = ""
    sort: str = ""


# Common unit/object-editor fields. Values not listed here can still be edited
# through the Raw Fields tab, so the GUI never limits the binary format.
UNIT_FIELDS = {
    # General / text
    "name": FieldDef("unam", "Nome", TYPE_STRING, "Geral"),
    "editor_suffix": FieldDef("unsf", "Sufixo do editor", TYPE_STRING, "Geral"),
    "normal_abilities": FieldDef("uabi", "Habilidades normais", TYPE_STRING, "Habilidades", help="Rawcodes separados por vírgula"),
    "hero_abilities": FieldDef("uhab", "Habilidades de herói", TYPE_STRING, "Habilidades", help="Rawcodes separados por vírgula"),
    "classification": FieldDef("utyp", "Classificações", TYPE_STRING, "Geral"),

    # Art
    "model": FieldDef("umdl", "Modelo", TYPE_STRING, "Arte"),
    "scale": FieldDef("usca", "Art - Scaling Value", TYPE_REAL, "Arte"),
    "selection_scale": FieldDef("ussc", "Escala do círculo de seleção", TYPE_REAL, "Arte"),
    "icon": FieldDef("uico", "Ícone", TYPE_STRING, "Arte"),
    "red": FieldDef("uclr", "Tint R", TYPE_INT, "Arte"),
    "green": FieldDef("uclg", "Tint G", TYPE_INT, "Arte"),
    "blue": FieldDef("uclb", "Tint B", TYPE_INT, "Arte"),

    # Stats
    "hp": FieldDef("uhpm", "HP máximo", TYPE_INT, "Stats"),
    "mana": FieldDef("umpm", "Mana máxima", TYPE_INT, "Stats"),
    "mana_initial": FieldDef("umpi", "Mana inicial", TYPE_INT, "Stats"),
    "hp_regen": FieldDef("uhpr", "Regeneração de HP", TYPE_UNREAL, "Stats"),
    "mana_regen": FieldDef("umpr", "Regeneração de mana", TYPE_UNREAL, "Stats"),
    "sight_day": FieldDef("usid", "Visão diurna", TYPE_INT, "Stats"),
    "sight_night": FieldDef("usin", "Visão noturna", TYPE_INT, "Stats"),
    "acquisition": FieldDef("uacq", "Acquisition range", TYPE_UNREAL, "Stats"),
    "food_cost": FieldDef("ufoo", "Food cost", TYPE_INT, "Stats"),
    "food_made": FieldDef("ufma", "Food produced", TYPE_INT, "Stats"),
    "gold_cost": FieldDef("ugol", "Gold cost", TYPE_INT, "Stats"),
    "lumber_cost": FieldDef("ulum", "Lumber cost", TYPE_INT, "Stats"),
    "build_time": FieldDef("ubld", "Build time", TYPE_INT, "Stats"),

    # Movement
    "move_speed": FieldDef("umvs", "Velocidade de movimento", TYPE_INT, "Movimento"),
    "move_type": FieldDef("umvt", "Tipo de movimento", TYPE_STRING, "Movimento"),
    "fly_height": FieldDef("umvh", "Altura de movimento/voo", TYPE_UNREAL, "Movimento"),
    "turn_rate": FieldDef("umvr", "Turn rate", TYPE_UNREAL, "Movimento"),
    "collision_size": FieldDef("ucol", "Collision size", TYPE_UNREAL, "Movimento"),

    # Combat / defense
    "armor_base": FieldDef("udef", "Armadura base", TYPE_INT, "Combate"),
    "armor_type": FieldDef("uarm", "Tipo de armadura", TYPE_STRING, "Combate"),
    "defense_type": FieldDef("udty", "Tipo de defesa", TYPE_STRING, "Combate"),
    "attack1_base": FieldDef("ua1b", "Ataque 1 - dano base", TYPE_INT, "Combate"),
    "attack1_dice": FieldDef("ua1d", "Ataque 1 - nº de dados", TYPE_INT, "Combate"),
    "attack1_sides": FieldDef("ua1s", "Ataque 1 - lados por dado", TYPE_INT, "Combate"),
    "attack1_cooldown": FieldDef("ua1c", "Ataque 1 - cooldown", TYPE_UNREAL, "Combate"),
    "attack1_range": FieldDef("ua1r", "Ataque 1 - alcance", TYPE_INT, "Combate"),
    "attack1_projectile": FieldDef("ua1m", "Ataque 1 - projétil", TYPE_STRING, "Combate"),
    "attack1_type": FieldDef("ua1t", "Ataque 1 - tipo de ataque", TYPE_STRING, "Combate"),
    "attack1_weapon": FieldDef("ua1w", "Ataque 1 - tipo de arma", TYPE_STRING, "Combate"),
    "attack1_targets": FieldDef("ua1g", "Ataque 1 - alvos permitidos", TYPE_STRING, "Combate"),

    # Hero
    "proper_names": FieldDef("upro", "Nomes próprios", TYPE_STRING, "Herói"),
    "primary_attribute": FieldDef("upra", "Atributo primário", TYPE_STRING, "Herói"),
    "hero_level": FieldDef("ulev", "Nível do tipo (Stats - Level)", TYPE_INT, "Herói"),
    "hero_strength": FieldDef("ustr", "Força base", TYPE_INT, "Herói"),
    "hero_agility": FieldDef("uagi", "Agilidade base", TYPE_INT, "Herói"),
    "hero_intelligence": FieldDef("uint", "Inteligência base", TYPE_INT, "Herói"),
    "str_per_level": FieldDef("ustp", "Força por nível", TYPE_UNREAL, "Herói"),
    "agi_per_level": FieldDef("uagp", "Agilidade por nível", TYPE_UNREAL, "Herói"),
    "int_per_level": FieldDef("uinp", "Inteligência por nível", TYPE_UNREAL, "Herói"),
}


# Ability object-data uses the extended modification layout. Many fields may be
# repeated by level. The raw editor supports any data field (DataA/DataB/etc.).
ABILITY_FIELDS = {
    "name": FieldDef("anam", "Nome", TYPE_STRING, "Geral"),
    "editor_suffix": FieldDef("ansf", "Sufixo do editor", TYPE_STRING, "Geral"),
    "icon": FieldDef("aart", "Ícone", TYPE_STRING, "Arte"),
    "levels": FieldDef("alev", "Número de níveis", TYPE_INT, "Geral"),
    "required_level": FieldDef("arlv", "Nível requerido", TYPE_INT, "Geral"),
    "level_skip": FieldDef("alsk", "Level skip requirement", TYPE_INT, "Geral"),
    "hero_ability": FieldDef("aher", "Hero ability", TYPE_INT, "Geral"),
    "item_ability": FieldDef("aite", "Item ability", TYPE_INT, "Geral"),
    "race": FieldDef("arac", "Race", TYPE_STRING, "Geral"),
    "button_x": FieldDef("abpx", "Button position X", TYPE_INT, "Arte"),
    "button_y": FieldDef("abpy", "Button position Y", TYPE_INT, "Arte"),
    "hotkey": FieldDef("ahky", "Hotkey", TYPE_STRING, "Texto"),
    "tooltip": FieldDef("atp1", "Tooltip", TYPE_STRING, "Texto", True),
    "ubertip": FieldDef("aub1", "Ubertip", TYPE_STRING, "Texto", True),
    "research_tooltip": FieldDef("aret", "Research tooltip", TYPE_STRING, "Texto"),
    "research_ubertip": FieldDef("arut", "Research ubertip", TYPE_STRING, "Texto"),
    "research_hotkey": FieldDef("arhk", "Research hotkey", TYPE_STRING, "Texto"),
    "cooldown": FieldDef("acdn", "Cooldown", TYPE_UNREAL, "Gameplay", True),
    "mana_cost": FieldDef("amcs", "Mana cost", TYPE_INT, "Gameplay", True),
    "cast_range": FieldDef("aran", "Cast range", TYPE_UNREAL, "Gameplay", True),
    "area": FieldDef("aare", "Area of effect", TYPE_UNREAL, "Gameplay", True),
    "duration": FieldDef("adur", "Duration - normal", TYPE_UNREAL, "Gameplay", True),
    "hero_duration": FieldDef("ahdu", "Duration - hero", TYPE_UNREAL, "Gameplay", True),
    "targets": FieldDef("atar", "Targets allowed", TYPE_STRING, "Gameplay", True),
    "order": FieldDef("aord", "Order string", TYPE_STRING, "Gameplay"),
}


# Doodad fields are extended object modifications (variation + column).
DOODAD_FIELDS = {
    "name": FieldDef("dnam", "Nome", TYPE_STRING, "Geral"),
    "model": FieldDef("dfil", "Modelo", TYPE_STRING, "Arte"),
    "min_scale": FieldDef("dmis", "Escala mínima", TYPE_REAL, "Arte"),
    "max_scale": FieldDef("dmas", "Escala máxima", TYPE_REAL, "Arte"),
    "variations": FieldDef("dvar", "Variações", TYPE_INT, "Arte"),
    "selectable": FieldDef("dsel", "Selecionável", TYPE_INT, "Geral"),
}

FIELD_SETS = {"unit": UNIT_FIELDS, "ability": ABILITY_FIELDS, "doodad": DOODAD_FIELDS}
FIELD_BY_ID = {
    kind: {field.id: (key, field) for key, field in fields.items()}
    for kind, fields in FIELD_SETS.items()
}

TYPE_NAMES = {TYPE_INT: "int", TYPE_REAL: "real", TYPE_UNREAL: "unreal", TYPE_STRING: "string"}
NAME_TYPES = {v: k for k, v in TYPE_NAMES.items()}


def mod(field_id: str, value_type: int, value, level: int = 0, column: int = 0) -> Modification:
    return Modification(field_id, value_type, value, level, column)
