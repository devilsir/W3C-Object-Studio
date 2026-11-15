from __future__ import annotations

from ..objectmod import Modification, TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING

TYPE_MAP = {"int": TYPE_INT, "real": TYPE_REAL, "unreal": TYPE_UNREAL, "string": TYPE_STRING}
KIND_LABELS = {"unit": "Units / Heroes", "placed_hero": "Heroes on Map", "ability": "Spells / Abilities", "doodad": "Doodads"}
ABILITY_EFFECT_FIELDS = (
    ("acat", "Caster Art", "caster"),
    ("atat", "Target Art", "target"),
    ("asat", "Special Art", "caster"),
    ("aeat", "Effect Art", "target"),
    ("aaea", "Area Effect Art", "area"),
    ("amat", "Missile Art", "missile"),
)
DEFAULT_NEW = {
    "unit": ("hfoo", "u001", "Custom Unit"),
    "hero": ("Hpal", "H001", "Custom Hero"),
    "ability": ("AHbz", "A001", "Custom Spell"),
    "doodad": ("LTlt", "D001", "Custom Doodad"),
}

def coerce(value_type: int, raw: str):
    if value_type == TYPE_INT:
        return int(raw, 0)
    if value_type in (TYPE_REAL, TYPE_UNREAL):
        return float(raw.replace(",", "."))
    return raw

def value_text(value) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)

def field_value_text(fd, value) -> str:
    if getattr(fd, "editor_type", "") == "bool":
        try:
            return "True" if int(float(value)) else "False"
        except Exception:
            return str(value)
    return value_text(value)

def coerce_field(fd, raw: str):
    if getattr(fd, "editor_type", "") == "bool":
        value = raw.strip().casefold()
        if value in {"true", "yes", "sim", "1"}:
            return 1
        if value in {"false", "no", "não", "nao", "0"}:
            return 0
        raise ValueError(f"{fd.label}: use True/False")
    return coerce(fd.type, raw)
