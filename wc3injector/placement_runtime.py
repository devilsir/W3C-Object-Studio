from __future__ import annotations

"""Minimal runtime synchronization for preplaced-unit edits.

Warcraft does not instantiate ``war3mapUnits.doo`` directly when the map starts.
The World Editor compiles placement properties into ``war3map.j`` (for example
``SetHeroLevel`` and initial inventory calls).  Editing only the DOO therefore
makes the map look correct in an editor while the game can still execute stale
creation code.

This module deliberately does *not* regenerate the map script.  It owns one
small, marked JASS block and one marked call in ``main``.  Only placement fields
that have actually been changed are added to that block.  Existing user JASS,
GUI triggers and generated unit-creation functions remain byte-for-byte intact.
"""

from dataclasses import dataclass
import math
import re
from typing import Iterable, Mapping

from .errors import ValidationError
from .unitsdoo import UnitPlacement


BEGIN_MARKER = "// WC3 OBJECT STUDIO: PLACEMENT RUNTIME SYNC BEGIN"
END_MARKER = "// WC3 OBJECT STUDIO: PLACEMENT RUNTIME SYNC END"
CALL_MARKER = "// WC3 OBJECT STUDIO: PLACEMENT RUNTIME SYNC CALL"
APPLY_FUNCTION = "WC3OS_ApplyPlacementRuntimeSync"
FIND_FUNCTION = "WC3OS_FindPlacedUnit"

ADDED_BEGIN_MARKER = "// WC3 OBJECT STUDIO: ADDED PLACEMENTS BEGIN"
ADDED_END_MARKER = "// WC3 OBJECT STUDIO: ADDED PLACEMENTS END"
ADDED_CALL_MARKER = "// WC3 OBJECT STUDIO: ADDED PLACEMENTS CALL"
ADDED_FUNCTION = "WC3OS_CreateAddedPlacements"

REMOVED_BEGIN_MARKER = "// WC3 OBJECT STUDIO: REMOVED PLACEMENTS BEGIN"
REMOVED_END_MARKER = "// WC3 OBJECT STUDIO: REMOVED PLACEMENTS END"
REMOVED_CALL_MARKER = "// WC3 OBJECT STUDIO: REMOVED PLACEMENTS CALL"
REMOVED_FUNCTION = "WC3OS_RemoveDeletedPlacements"

# Fields we can reproduce deterministically after CreateAllUnits().  Abilities
# and drop tables are intentionally omitted: the World Editor can compile those
# into skill-point consumption/order strings and per-unit drop triggers, and
# blindly recreating them would be more dangerous than preserving the DOO only.
SUPPORTED_RUNTIME_FIELDS = {
    "position",
    "player",
    "angle",
    "hitpoints",
    "mana",
    "target_acquisition",
    "hero_level",
    "hero_attributes",
    "inventory",
}


@dataclass(frozen=True)
class PlacementRuntimeDefaults:
    acquisition: float | None = None
    hero_strength: int | None = None
    hero_agility: int | None = None
    hero_intelligence: int | None = None
    # Original DOO coordinates are used by the fallback resolver when a moved
    # placement does not have a gg_unit_* global in the compiled JASS.
    lookup_x: float | None = None
    lookup_y: float | None = None


@dataclass(frozen=True)
class RuntimePatchResult:
    data: bytes
    changed: bool
    override_fields: dict[int, set[str]]
    fallback_lookup_count: int


def _float_changed(a: float, b: float, *, tol: float = 1e-5) -> bool:
    return not math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol)


def _inventory_signature(unit: UnitPlacement) -> tuple[tuple[int, str], ...]:
    return tuple(sorted((int(x.slot), str(x.item_id)) for x in unit.inventory))


def placement_runtime_changed_fields(before: UnitPlacement, after: UnitPlacement) -> set[str]:
    """Return runtime-sensitive fields changed between two placements."""
    out: set[str] = set()
    if _float_changed(before.x, after.x) or _float_changed(before.y, after.y):
        out.add("position")
    if int(before.player) != int(after.player):
        out.add("player")
    if _float_changed(before.angle, after.angle):
        out.add("angle")
    if int(before.hitpoints) != int(after.hitpoints):
        out.add("hitpoints")
    if int(before.mana) != int(after.mana):
        out.add("mana")
    if _float_changed(before.target_acquisition, after.target_acquisition):
        out.add("target_acquisition")
    if int(before.hero_level) != int(after.hero_level):
        out.add("hero_level")
    if (
        int(before.hero_strength), int(before.hero_agility), int(before.hero_intelligence)
    ) != (
        int(after.hero_strength), int(after.hero_agility), int(after.hero_intelligence)
    ):
        out.add("hero_attributes")
    if _inventory_signature(before) != _inventory_signature(after):
        out.add("inventory")
    return out


def placement_non_runtime_changed_fields(before: UnitPlacement, after: UnitPlacement) -> set[str]:
    """Known placement fields whose runtime compilation is not patched yet."""
    out: set[str] = set()
    before_abilities = tuple((x.ability_id, int(x.autocast), int(x.level)) for x in before.modified_abilities)
    after_abilities = tuple((x.ability_id, int(x.autocast), int(x.level)) for x in after.modified_abilities)
    if before_abilities != after_abilities:
        out.add("modified_abilities")

    def drops(u: UnitPlacement):
        return (
            int(u.dropped_item_table),
            tuple(tuple((i.item_id, int(i.chance)) for i in s.items) for s in u.dropped_item_sets),
        )
    if drops(before) != drops(after):
        out.add("dropped_items")
    return out



def studio_added_creation_numbers(original: bytes | str) -> set[int]:
    """Return placement creation numbers managed by the Studio's add-unit block."""
    text = original.decode("latin1") if isinstance(original, (bytes, bytearray)) else str(original)
    m = re.search(
        re.escape(ADDED_BEGIN_MARKER) + r"(?P<body>.*?)" + re.escape(ADDED_END_MARKER),
        text,
        flags=re.S,
    )
    if not m:
        return set()
    return {
        int(x.group(1))
        for x in re.finditer(r"^\s*//\s*WC3OS_ADDED\s+creation=(\d+)\s*$", m.group("body"), flags=re.M)
    }


def _remove_added_block_and_call(text: str) -> str:
    text = re.sub(
        r"\n?" + re.escape(ADDED_BEGIN_MARKER) + r".*?" + re.escape(ADDED_END_MARKER) + r"\r?\n?",
        "\n",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"(?m)^\s*call\s+" + re.escape(ADDED_FUNCTION) + r"\s*\(\s*\)\s*" + re.escape(ADDED_CALL_MARKER) + r"\s*\r?\n?",
        "",
        text,
    )
    return text

def _parse_existing_override_fields(text: str) -> dict[int, set[str]]:
    """Recover fields owned by a previous Studio-generated block."""
    block_match = re.search(
        re.escape(BEGIN_MARKER) + r"(?P<body>.*?)" + re.escape(END_MARKER),
        text,
        flags=re.S,
    )
    if not block_match:
        return {}
    result: dict[int, set[str]] = {}
    for m in re.finditer(
        r"^\s*//\s*WC3OS_OVERRIDE\s+creation=(\d+)\s+fields=([A-Za-z0-9_,.-]+)\s*$",
        block_match.group("body"),
        flags=re.M,
    ):
        creation = int(m.group(1))
        fields = {x for x in m.group(2).split(",") if x in SUPPORTED_RUNTIME_FIELDS}
        if fields:
            result.setdefault(creation, set()).update(fields)
    return result



def _parse_existing_lookup_positions(text: str) -> dict[int, tuple[float, float]]:
    """Recover the original coordinates used by fallback lookup entries.

    Once a placement is moved, its DOO baseline becomes the *new* position on
    the next application run while the World Editor's compiled creation call
    still spawns at the old position.  Preserve the first fallback lookup point
    from our existing block across later saves so owner/level/etc. edits can
    still find the unit before SetUnitX/SetUnitY runs.
    """
    block_match = re.search(
        re.escape(BEGIN_MARKER) + r"(?P<body>.*?)" + re.escape(END_MARKER),
        text, flags=re.S,
    )
    if not block_match:
        return {}
    body = block_match.group("body")
    out: dict[int, tuple[float, float]] = {}
    current: int | None = None
    for line in body.splitlines():
        m = re.match(r"\s*//\s*WC3OS_OVERRIDE\s+creation=(\d+)\s+fields=", line)
        if m:
            current = int(m.group(1))
            continue
        if current is None:
            continue
        m = re.search(
            re.escape(FIND_FUNCTION)
            + r"\s*\(\s*'[^']{4}'\s*,\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*\)",
            line,
        )
        if m:
            try:
                out[current] = (float(m.group(1)), float(m.group(2)))
            except Exception:
                pass
            current = None
    return out


def _remove_owned_block_and_call(text: str) -> str:
    text = re.sub(
        r"\n?" + re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER) + r"\r?\n?",
        "\n",
        text,
        flags=re.S,
    )
    # Remove only our marked call.  Never delete an unmarked function call with
    # a coincidentally similar name from user code.
    text = re.sub(
        r"(?m)^\s*call\s+" + re.escape(APPLY_FUNCTION) + r"\s*\(\s*\)\s*" + re.escape(CALL_MARKER) + r"\s*\r?\n?",
        "",
        text,
    )
    return text


def _jass_real(value: float) -> str:
    v = 0.0 if abs(float(value)) < 0.0000005 else float(value)
    text = f"{v:.6f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".0"
    return text


def _rawcode_literal(rawcode: str) -> str:
    if len(rawcode) != 4:
        raise ValidationError(f"rawcode inválido para sync JASS: {rawcode!r}")
    # Rawcodes are four bytes.  A single quote would make an invalid JASS
    # integer literal, so refuse rather than corrupt the map script.
    if "'" in rawcode or "\n" in rawcode or "\r" in rawcode:
        raise ValidationError(f"rawcode não pode ser representado em JASS: {rawcode!r}")
    return "'" + rawcode + "'"


def _find_global_ref(script: str, unit: UnitPlacement) -> str | None:
    suffix = f"{int(unit.creation_number):04d}"
    candidate = f"gg_unit_{unit.unit_id}_{suffix}"
    # Only trust a generated global when it is actually declared/assigned.
    # A name occurring in a comment must never become emitted JASS.
    candidate_rx = re.escape(candidate)
    if (
        re.search(r"(?m)^\s*unit\s+" + candidate_rx + r"\s*=", script)
        or re.search(r"(?m)^\s*set\s+" + candidate_rx + r"\s*=", script)
    ):
        return candidate

    # Some tools sanitize the rawcode portion of the global name.  If the
    # creation-number suffix is unique and the assignment line contains the
    # expected rawcode, use that exact generated global.
    raw = re.escape(_rawcode_literal(unit.unit_id))
    pattern = re.compile(
        r"(?m)^\s*set\s+(gg_unit_[A-Za-z0-9_]+_" + re.escape(suffix) + r")\s*=\s*"
        r"(?:BlzCreateUnitWithSkin|CreateUnit)\s*\([^\n]*?" + raw
    )
    match = pattern.search(script)
    return match.group(1) if match else None


def _runtime_player_expr(player_id: int) -> str:
    """Return a version-safe JASS player expression for a Units.doo owner id.

    Reforged expanded playable slots from 12 to 24.  The four reserved neutral
    owners moved as a consequence, and Blizzard exposes natives/constants so
    map scripts do not have to hardcode their runtime indexes.  Units.doo uses
    the modern raw owner slots (24 Aggressive, 25 Victim, 26 Extra, 27 Passive),
    but writing ``Player(25)`` for what the UI called Neutral Passive is wrong
    and makes the unit attackable.  Emit semantic constants for all reserved
    neutral owners and numeric Player(n) only for ordinary player slots.
    """
    player_id = int(player_id)
    if player_id == 24:
        return "Player(PLAYER_NEUTRAL_AGGRESSIVE)"
    if player_id == 25:
        return "Player(bj_PLAYER_NEUTRAL_VICTIM)"
    if player_id == 26:
        return "Player(bj_PLAYER_NEUTRAL_EXTRA)"
    if player_id == 27:
        return "Player(PLAYER_NEUTRAL_PASSIVE)"
    return f"Player({player_id})"


def _render_field_lines(
    ref: str,
    unit: UnitPlacement,
    fields: set[str],
    defaults: PlacementRuntimeDefaults,
) -> list[str]:
    lines: list[str] = []
    if "position" in fields:
        lines.append(f"        call SetUnitX({ref}, {_jass_real(unit.x)})")
        lines.append(f"        call SetUnitY({ref}, {_jass_real(unit.y)})")
    if "player" in fields:
        lines.append(f"        call SetUnitOwner({ref}, {_runtime_player_expr(unit.player)}, true)")
    if "angle" in fields:
        degrees = math.degrees(float(unit.angle)) % 360.0
        lines.append(f"        call SetUnitFacing({ref}, {_jass_real(degrees)})")
    if "hitpoints" in fields:
        hp = int(unit.hitpoints)
        if hp < 0:
            lines.append(
                f"        call SetUnitState({ref}, UNIT_STATE_LIFE, GetUnitState({ref}, UNIT_STATE_MAX_LIFE))"
            )
        else:
            lines.append(
                f"        call SetUnitState({ref}, UNIT_STATE_LIFE, GetUnitState({ref}, UNIT_STATE_MAX_LIFE) * {_jass_real(hp / 100.0)})"
            )
    if "mana" in fields:
        mana = int(unit.mana)
        if mana < 0:
            lines.append(
                f"        call SetUnitState({ref}, UNIT_STATE_MANA, GetUnitState({ref}, UNIT_STATE_MAX_MANA))"
            )
        else:
            lines.append(
                f"        call SetUnitState({ref}, UNIT_STATE_MANA, GetUnitState({ref}, UNIT_STATE_MAX_MANA) * {_jass_real(mana / 100.0)})"
            )
    if "target_acquisition" in fields:
        acq = float(unit.target_acquisition)
        if acq >= 0.0:
            lines.append(f"        call SetUnitAcquireRange({ref}, {_jass_real(acq)})")
        elif acq == -1.0 and defaults.acquisition is not None:
            lines.append(f"        call SetUnitAcquireRange({ref}, {_jass_real(defaults.acquisition)})")
        elif acq == -2.0:
            # "Camp" is represented by a placement sentinel rather than a
            # numeric acquire range.  Creep guard is the closest native runtime
            # equivalent and is what we can apply without regenerating scripts.
            lines.append(f"        call SetUnitCreepGuard({ref}, true)")
    if "hero_level" in fields:
        # SetHeroLevel only raises levels.  The Blizzard wrapper also handles
        # lowering via UnitStripHeroLevel, so edits work in both directions.
        lines.append(f"        call SetHeroLevelBJ({ref}, {max(1, int(unit.hero_level))}, false)")
    if "hero_attributes" in fields:
        targets = (
            int(unit.hero_strength) if int(unit.hero_strength) > 0 else defaults.hero_strength,
            int(unit.hero_agility) if int(unit.hero_agility) > 0 else defaults.hero_agility,
            int(unit.hero_intelligence) if int(unit.hero_intelligence) > 0 else defaults.hero_intelligence,
        )
        names = ("SetHeroStr", "SetHeroAgi", "SetHeroInt")
        for name, value in zip(names, targets):
            if value is not None:
                lines.append(f"        call {name}({ref}, {int(value)}, true)")
    if "inventory" in fields:
        desired = {int(x.slot): str(x.item_id) for x in unit.inventory if 0 <= int(x.slot) <= 5 and str(x.item_id) != "\0\0\0\0"}
        for slot in range(6):
            lines.append(f"        if UnitItemInSlot({ref}, {slot}) != null then")
            lines.append(f"            call RemoveItem(UnitItemInSlot({ref}, {slot}))")
            lines.append("        endif")
            item_id = desired.get(slot)
            if item_id:
                lines.append(f"        call UnitAddItemToSlotById({ref}, {_rawcode_literal(item_id)}, {slot})")
    return lines


def _render_block(
    script: str,
    units_by_creation: Mapping[int, UnitPlacement],
    overrides: Mapping[int, set[str]],
    defaults_by_creation: Mapping[int, PlacementRuntimeDefaults],
) -> tuple[str, int]:
    fallback_needed = 0
    unit_entries: list[tuple[int, UnitPlacement, set[str], str | None]] = []
    for creation in sorted(overrides):
        unit = units_by_creation.get(int(creation))
        fields = set(overrides[creation]) & SUPPORTED_RUNTIME_FIELDS
        if unit is None or not fields:
            continue
        global_ref = _find_global_ref(script, unit)
        if global_ref is None:
            fallback_needed += 1
        unit_entries.append((creation, unit, fields, global_ref))

    lines: list[str] = [BEGIN_MARKER]
    lines.append("// Generated by WC3 Reforged Object Studio. Only this marked block is managed by the Studio.")
    if fallback_needed:
        lines.extend([
            f"function {FIND_FUNCTION} takes integer unitTypeId, real x, real y returns unit",
            "    local group g = CreateGroup()",
            "    local unit u = null",
            "    local unit best = null",
            "    local real dx = 0.0",
            "    local real dy = 0.0",
            "    local real d = 0.0",
            "    local real bestD = 999999999.0",
            "    call GroupEnumUnitsInRange(g, x, y, 128.0, null)",
            "    loop",
            "        set u = FirstOfGroup(g)",
            "        exitwhen u == null",
            "        call GroupRemoveUnit(g, u)",
            "        if GetUnitTypeId(u) == unitTypeId then",
            "            set dx = GetUnitX(u) - x",
            "            set dy = GetUnitY(u) - y",
            "            set d = dx * dx + dy * dy",
            "            if d < bestD then",
            "                set best = u",
            "                set bestD = d",
            "            endif",
            "        endif",
            "    endloop",
            "    call DestroyGroup(g)",
            "    set g = null",
            "    set u = null",
            "    return best",
            "endfunction",
            "",
        ])

    lines.extend([
        f"function {APPLY_FUNCTION} takes nothing returns nothing",
        "    local unit u = null",
    ])
    for creation, unit, fields, global_ref in unit_entries:
        field_csv = ",".join(sorted(fields))
        lines.append(f"    // WC3OS_OVERRIDE creation={creation} fields={field_csv}")
        if global_ref:
            lines.append(f"    set u = {global_ref}")
        else:
            defaults = defaults_by_creation.get(creation, PlacementRuntimeDefaults())
            lookup_x = unit.x if defaults.lookup_x is None else defaults.lookup_x
            lookup_y = unit.y if defaults.lookup_y is None else defaults.lookup_y
            lines.append(
                f"    set u = {FIND_FUNCTION}({_rawcode_literal(unit.unit_id)}, {_jass_real(lookup_x)}, {_jass_real(lookup_y)})"
            )
        lines.append("    if u != null then")
        lines.extend(_render_field_lines("u", unit, fields, defaults_by_creation.get(creation, PlacementRuntimeDefaults())))
        lines.append("    endif")
    lines.extend([
        "    set u = null",
        "endfunction",
        END_MARKER,
    ])
    return "\n".join(lines), fallback_needed


def _insert_block_before_main(text: str, block: str) -> str:
    m = re.search(r"(?m)^\s*function\s+main\s+takes\s+nothing\s+returns\s+nothing\s*$", text)
    if not m:
        raise ValidationError("war3map.j não possui 'function main takes nothing returns nothing'")
    prefix = text[:m.start()].rstrip("\r\n")
    suffix = text[m.start():]
    return prefix + "\n\n" + block + "\n\n" + suffix


def _insert_call_in_main(text: str) -> str:
    main_match = re.search(r"(?m)^\s*function\s+main\s+takes\s+nothing\s+returns\s+nothing\s*$", text)
    if not main_match:
        raise ValidationError("war3map.j não possui função main")
    end_match = re.search(r"(?m)^\s*endfunction\s*$", text[main_match.end():])
    if not end_match:
        raise ValidationError("função main do war3map.j não possui endfunction")
    main_end = main_match.end() + end_match.end()
    body = text[main_match.end():main_end]
    call_line = f"    call {APPLY_FUNCTION}() {CALL_MARKER}"

    # Preferred location: immediately after World Editor unit creation.
    create_matches = list(re.finditer(r"(?m)^(?P<indent>\s*)call\s+CreateAllUnits\s*\(\s*\)\s*$", body))
    if create_matches:
        m = create_matches[-1]
        indent = m.group("indent")
        insertion = "\n" + indent + f"call {APPLY_FUNCTION}() {CALL_MARKER}"
        pos = main_match.end() + m.end()
        return text[:pos] + insertion + text[pos:]

    # Standard generated maps initialize custom triggers only after every
    # preplaced unit has been created.  Put our sync immediately before that.
    trigger_match = re.search(r"(?m)^(?P<indent>\s*)call\s+InitCustomTriggers\s*\(\s*\)\s*$", body)
    if trigger_match:
        indent = trigger_match.group("indent")
        insertion = indent + f"call {APPLY_FUNCTION}() {CALL_MARKER}\n"
        pos = main_match.end() + trigger_match.start()
        return text[:pos] + insertion + text[pos:]

    # Last-resort safe-ish fallback: end of main.  At that point every unit
    # created from main already exists.  This keeps support for custom scripts
    # without trying to parse/format unrelated JASS.
    end_start = main_match.end() + end_match.start()
    insertion = "    " + f"call {APPLY_FUNCTION}() {CALL_MARKER}\n"
    return text[:end_start] + insertion + text[end_start:]



def _added_runtime_fields(unit: UnitPlacement) -> set[str]:
    fields: set[str] = set()
    if int(unit.hitpoints) >= 0:
        fields.add("hitpoints")
    if int(unit.mana) >= 0:
        fields.add("mana")
    if float(unit.target_acquisition) != -1.0:
        fields.add("target_acquisition")
    if int(unit.hero_level) > 1:
        fields.add("hero_level")
    if any(int(v) > 0 for v in (unit.hero_strength, unit.hero_agility, unit.hero_intelligence)):
        fields.add("hero_attributes")
    if unit.inventory:
        fields.add("inventory")
    return fields


def _render_added_block(
    units_by_creation: Mapping[int, UnitPlacement],
    creation_numbers: Iterable[int],
    defaults_by_creation: Mapping[int, PlacementRuntimeDefaults],
) -> str:
    lines = [
        ADDED_BEGIN_MARKER,
        "// Generated by WC3 Reforged Object Studio. Only this marked block is managed by the Studio.",
        f"function {ADDED_FUNCTION} takes nothing returns nothing",
        "    local unit u = null",
    ]
    for creation in sorted({int(x) for x in creation_numbers}):
        unit = units_by_creation.get(creation)
        if unit is None:
            continue
        skin = str(unit.skin_id or unit.unit_id)
        if len(skin) != 4 or skin == "\0\0\0\0":
            skin = unit.unit_id
        degrees = math.degrees(float(unit.angle)) % 360.0
        lines.append(f"    // WC3OS_ADDED creation={creation}")
        lines.append(
            "    set u = BlzCreateUnitWithSkin("
            f"{_runtime_player_expr(unit.player)}, {_rawcode_literal(unit.unit_id)}, "
            f"{_jass_real(unit.x)}, {_jass_real(unit.y)}, {_jass_real(degrees)}, {_rawcode_literal(skin)})"
        )
        if not (
            math.isclose(float(unit.scale_x), 1.0, abs_tol=1e-6)
            and math.isclose(float(unit.scale_y), 1.0, abs_tol=1e-6)
            and math.isclose(float(unit.scale_z), 1.0, abs_tol=1e-6)
        ):
            lines.append(
                f"    call SetUnitScale(u, {_jass_real(unit.scale_x)}, {_jass_real(unit.scale_y)}, {_jass_real(unit.scale_z)})"
            )
        fields = _added_runtime_fields(unit)
        if fields:
            # _render_field_lines is indented for the guarded override block;
            # strip four spaces so it fits directly in this creation function.
            for line in _render_field_lines("u", unit, fields, defaults_by_creation.get(creation, PlacementRuntimeDefaults())):
                lines.append(line[4:] if line.startswith("    ") else line)
    lines.extend([
        "    set u = null",
        "endfunction",
        ADDED_END_MARKER,
    ])
    return "\n".join(lines)


def _insert_added_call_in_main(text: str) -> str:
    main_match = re.search(r"(?m)^\s*function\s+main\s+takes\s+nothing\s+returns\s+nothing\s*$", text)
    if not main_match:
        raise ValidationError("war3map.j não possui função main")
    end_match = re.search(r"(?m)^\s*endfunction\s*$", text[main_match.end():])
    if not end_match:
        raise ValidationError("função main do war3map.j não possui endfunction")
    main_end = main_match.end() + end_match.end()
    body = text[main_match.end():main_end]

    create_matches = list(re.finditer(r"(?m)^(?P<indent>\s*)call\s+CreateAllUnits\s*\(\s*\)\s*$", body))
    if create_matches:
        m = create_matches[-1]
        indent = m.group("indent")
        pos = main_match.end() + m.end()
        return text[:pos] + "\n" + indent + f"call {ADDED_FUNCTION}() {ADDED_CALL_MARKER}" + text[pos:]

    trigger_match = re.search(r"(?m)^(?P<indent>\s*)call\s+InitCustomTriggers\s*\(\s*\)\s*$", body)
    if trigger_match:
        indent = trigger_match.group("indent")
        pos = main_match.end() + trigger_match.start()
        return text[:pos] + indent + f"call {ADDED_FUNCTION}() {ADDED_CALL_MARKER}\n" + text[pos:]

    end_start = main_match.end() + end_match.start()
    return text[:end_start] + f"    call {ADDED_FUNCTION}() {ADDED_CALL_MARKER}\n" + text[end_start:]


def patch_jass_added_placements(
    original: bytes,
    *,
    units: Iterable[UnitPlacement],
    creation_numbers: Iterable[int],
    defaults_by_creation: Mapping[int, PlacementRuntimeDefaults] | None = None,
) -> RuntimePatchResult:
    """Synchronize units created by the Studio without regenerating map JASS."""
    text = original.decode("latin1")
    requested = {int(x) for x in creation_numbers}
    clean = _remove_added_block_and_call(text)
    units_by_creation = {int(u.creation_number): u for u in units}
    requested = {x for x in requested if x in units_by_creation}

    if not requested:
        data = clean.encode("latin1")
        return RuntimePatchResult(data, data != original, {}, 0)

    if ADDED_BEGIN_MARKER not in text and re.search(
        r"(?m)^\s*function\s+" + re.escape(ADDED_FUNCTION) + r"\s+takes\b", text
    ):
        raise ValidationError(
            f"war3map.j já possui uma função chamada {ADDED_FUNCTION}; criação de placement recusada para não sobrescrever JASS do mapa"
        )

    block = _render_added_block(units_by_creation, requested, defaults_by_creation or {})
    patched = _insert_block_before_main(clean, block)
    patched = _insert_added_call_in_main(patched)
    data = patched.encode("latin1")
    return RuntimePatchResult(data, data != original, {x: {"created"} for x in requested}, 0)




def _parse_existing_removed_entries(text: str) -> dict[int, tuple[str, float, float]]:
    """Recover deletions already owned by a previous Studio save."""
    m = re.search(
        re.escape(REMOVED_BEGIN_MARKER) + r"(?P<body>.*?)" + re.escape(REMOVED_END_MARKER),
        text, flags=re.S,
    )
    if not m:
        return {}
    out: dict[int, tuple[str, float, float]] = {}
    rx = re.compile(
        r"^\s*//\s*WC3OS_REMOVED\s+creation=(\d+)\s+raw=([^\s]+)\s+x=([-+0-9.eE]+)\s+y=([-+0-9.eE]+)\s*$",
        flags=re.M,
    )
    for hit in rx.finditer(m.group("body")):
        try:
            out[int(hit.group(1))] = (hit.group(2), float(hit.group(3)), float(hit.group(4)))
        except Exception:
            continue
    return out


def _remove_removed_block_and_call(text: str) -> str:
    text = re.sub(
        r"\n?" + re.escape(REMOVED_BEGIN_MARKER) + r".*?" + re.escape(REMOVED_END_MARKER) + r"\r?\n?",
        "\n", text, flags=re.S,
    )
    text = re.sub(
        r"(?m)^\s*call\s+" + re.escape(REMOVED_FUNCTION) + r"\s*\(\s*\)\s*" + re.escape(REMOVED_CALL_MARKER) + r"\s*\r?\n?",
        "", text,
    )
    return text


def _insert_removed_call_in_main(text: str) -> str:
    """Run removals after map unit creation and after Studio-added units."""
    main_match = re.search(r"(?m)^\s*function\s+main\s+takes\s+nothing\s+returns\s+nothing\s*$", text)
    if not main_match:
        raise ValidationError("war3map.j não possui função main")
    end_match = re.search(r"(?m)^\s*endfunction\s*$", text[main_match.end():])
    if not end_match:
        raise ValidationError("função main do war3map.j não possui endfunction")
    main_end = main_match.end() + end_match.end()
    body = text[main_match.end():main_end]

    # Prefer immediately after our added-placement call, otherwise after
    # CreateAllUnits.  This keeps deletion deterministic without touching the
    # World Editor-generated creation functions themselves.
    added = re.search(
        r"(?m)^(?P<indent>\s*)call\s+" + re.escape(ADDED_FUNCTION) + r"\s*\(\s*\).*?$",
        body,
    )
    if added:
        pos = main_match.end() + added.end()
        indent = added.group("indent")
        return text[:pos] + "\n" + indent + f"call {REMOVED_FUNCTION}() {REMOVED_CALL_MARKER}" + text[pos:]

    create_matches = list(re.finditer(r"(?m)^(?P<indent>\s*)call\s+CreateAllUnits\s*\(\s*\)\s*$", body))
    if create_matches:
        m = create_matches[-1]
        pos = main_match.end() + m.end()
        indent = m.group("indent")
        return text[:pos] + "\n" + indent + f"call {REMOVED_FUNCTION}() {REMOVED_CALL_MARKER}" + text[pos:]

    end_start = main_match.end() + end_match.start()
    return text[:end_start] + f"    call {REMOVED_FUNCTION}() {REMOVED_CALL_MARKER}\n" + text[end_start:]


def patch_jass_removed_placements(
    original: bytes,
    *,
    removed_units: Iterable[UnitPlacement],
) -> RuntimePatchResult:
    """Keep deleted preplaced units absent at runtime without regenerating JASS.

    The unit is removed from war3mapUnits.doo.  For a World Editor-created
    placement its original creation statement can safely remain untouched: this
    marked block runs after CreateAllUnits and removes the matching instance.
    Whenever possible it uses the generated ``gg_unit_*`` global; otherwise it
    resolves the unit by rawcode + its *original* coordinates.
    """
    text = original.decode("latin1")
    existing = _parse_existing_removed_entries(text)
    clean = _remove_removed_block_and_call(text)

    snapshots: dict[int, UnitPlacement] = {}
    for u in removed_units:
        snapshots[int(u.creation_number)] = u
        existing[int(u.creation_number)] = (str(u.unit_id), float(u.x), float(u.y))

    if not existing:
        data = clean.encode("latin1")
        return RuntimePatchResult(data, data != original, {}, 0)

    if REMOVED_BEGIN_MARKER not in text and re.search(
        r"(?m)^\s*function\s+" + re.escape(REMOVED_FUNCTION) + r"\s+takes\b", text
    ):
        raise ValidationError(
            f"war3map.j já possui uma função chamada {REMOVED_FUNCTION}; remoção recusada para não sobrescrever JASS do mapa"
        )

    need_finder = False
    rendered: list[tuple[int, str, float, float, str | None]] = []
    for creation in sorted(existing):
        raw, x, y = existing[creation]
        snap = snapshots.get(creation)
        if snap is None:
            snap = UnitPlacement(unit_id=raw, x=x, y=y, creation_number=creation)
        ref = _find_global_ref(clean, snap)
        if ref is None:
            need_finder = True
        rendered.append((creation, raw, x, y, ref))

    lines = [REMOVED_BEGIN_MARKER]
    lines.append("// Generated by WC3 Reforged Object Studio. Only this marked block is managed by the Studio.")
    # Reuse the resolver owned by the runtime-sync block when present.  If the
    # map has no runtime-sync block, include a private finder in this block.
    finder_name = FIND_FUNCTION if BEGIN_MARKER in clean else "WC3OS_FindRemovedPlacement"
    if need_finder and BEGIN_MARKER not in clean:
        lines.extend([
            f"function {finder_name} takes integer unitTypeId, real x, real y returns unit",
            "    local group g = CreateGroup()",
            "    local unit u = null",
            "    local unit best = null",
            "    local real dx = 0.0",
            "    local real dy = 0.0",
            "    local real d = 0.0",
            "    local real bestD = 999999999.0",
            "    call GroupEnumUnitsInRange(g, x, y, 128.0, null)",
            "    loop",
            "        set u = FirstOfGroup(g)",
            "        exitwhen u == null",
            "        call GroupRemoveUnit(g, u)",
            "        if GetUnitTypeId(u) == unitTypeId then",
            "            set dx = GetUnitX(u) - x",
            "            set dy = GetUnitY(u) - y",
            "            set d = dx * dx + dy * dy",
            "            if d < bestD then",
            "                set best = u",
            "                set bestD = d",
            "            endif",
            "        endif",
            "    endloop",
            "    call DestroyGroup(g)",
            "    set g = null",
            "    set u = null",
            "    return best",
            "endfunction",
            "",
        ])

    lines.extend([f"function {REMOVED_FUNCTION} takes nothing returns nothing", "    local unit u = null"])
    fallback = 0
    for creation, raw, x, y, ref in rendered:
        lines.append(f"    // WC3OS_REMOVED creation={creation} raw={raw} x={x:.6f} y={y:.6f}")
        if ref:
            lines.append(f"    set u = {ref}")
        else:
            fallback += 1
            lines.append(f"    set u = {finder_name}({_rawcode_literal(raw)}, {_jass_real(x)}, {_jass_real(y)})")
        lines.append("    if u != null then")
        lines.append("        call RemoveUnit(u)")
        lines.append("    endif")
    lines.extend(["    set u = null", "endfunction", REMOVED_END_MARKER])
    block = "\n".join(lines)
    patched = _insert_block_before_main(clean, block)
    patched = _insert_removed_call_in_main(patched)
    data = patched.encode("latin1")
    return RuntimePatchResult(data, data != original, {k: {"removed"} for k in existing}, fallback)


def patch_jass_placement_runtime(
    original: bytes,
    *,
    units: Iterable[UnitPlacement],
    changed_fields: Mapping[int, set[str]],
    defaults_by_creation: Mapping[int, PlacementRuntimeDefaults] | None = None,
) -> RuntimePatchResult:
    """Patch only the Studio-owned placement-runtime block in ``war3map.j``.

    Existing generated overrides are retained and merged with this save's
    changed fields, so later saves never accidentally drop a previous runtime
    fix (e.g. changing owner after level was already patched in an earlier save).
    """
    try:
        text = original.decode("latin1")
    except Exception as exc:  # latin1 itself cannot fail; retained for clarity.
        raise ValidationError(f"não foi possível ler war3map.j: {exc}") from exc

    existing = _parse_existing_override_fields(text)
    existing_lookup = _parse_existing_lookup_positions(text)
    if BEGIN_MARKER not in text:
        # Reserved names are intentionally stable so later saves can replace
        # our block. Refuse a first insertion if user JASS already owns either
        # name rather than creating a duplicate function and breaking compile.
        for reserved in (APPLY_FUNCTION, FIND_FUNCTION):
            if re.search(r"(?m)^\s*function\s+" + re.escape(reserved) + r"\s+takes\b", text):
                raise ValidationError(
                    f"war3map.j já possui uma função chamada {reserved}; sync runtime recusado para não sobrescrever JASS do mapa"
                )
    merged: dict[int, set[str]] = {k: set(v) for k, v in existing.items()}
    for creation, fields in changed_fields.items():
        supported = set(fields) & SUPPORTED_RUNTIME_FIELDS
        if supported:
            merged.setdefault(int(creation), set()).update(supported)

    # Nothing to patch and no old Studio block: byte-exact no-op.
    if not merged and BEGIN_MARKER not in text:
        return RuntimePatchResult(original, False, {}, 0)

    clean = _remove_owned_block_and_call(text)
    units_by_creation = {int(u.creation_number): u for u in units}
    # Drop stale entries for placements that no longer exist.
    merged = {k: v for k, v in merged.items() if k in units_by_creation and v}
    if not merged:
        data = clean.encode("latin1")
        return RuntimePatchResult(data, data != original, {}, 0)

    render_defaults = dict(defaults_by_creation or {})
    for creation, (lookup_x, lookup_y) in existing_lookup.items():
        d = render_defaults.get(creation, PlacementRuntimeDefaults())
        render_defaults[creation] = PlacementRuntimeDefaults(
            acquisition=d.acquisition,
            hero_strength=d.hero_strength,
            hero_agility=d.hero_agility,
            hero_intelligence=d.hero_intelligence,
            lookup_x=lookup_x,
            lookup_y=lookup_y,
        )

    block, fallback_count = _render_block(
        clean,
        units_by_creation,
        merged,
        render_defaults,
    )
    patched = _insert_block_before_main(clean, block)
    patched = _insert_call_in_main(patched)
    data = patched.encode("latin1")
    return RuntimePatchResult(data, data != original, merged, fallback_count)
