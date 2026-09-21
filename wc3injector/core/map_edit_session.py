from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import copy
import math
import os
import shutil
import tempfile
import time

from ..errors import InjectorError, ValidationError
from ..debuglog import configure_logging
from ..imports import ImportFile
from ..injector import AssetSpec, SUPPORTED_ASSETS
from ..objectmod import ObjectModFile, Modification, ObjectRecord
from ..storm import StormArchive
from ..native_data import NativeDataCatalog
from ..ability_native import profile_field_value, profile_display_name
from ..doodad_native import doodad_field_value, doodad_display_name
from ..editor_strings import EditorStringCatalog
from ..map_skin import MapSkinCatalog, SKIN_OBJECT_FILES
from ..map_strings import MapStringTable
from ..unitsdoo import UnitPlacementFile, UnitPlacement
from ..placement_runtime import (
    SUPPORTED_RUNTIME_FIELDS, PlacementRuntimeDefaults, patch_jass_placement_runtime,
    patch_jass_added_placements, patch_jass_removed_placements, studio_added_creation_numbers,
    placement_runtime_changed_fields, placement_non_runtime_changed_fields,
)
from .native_catalog_loader import load_native_catalogs
from .map_integrity import (
    OBJECT_FILES, EDITOR_PROTECTED,
    _snapshot, _snapshot_editable, _load_object, sha256_bytes,
)

LOGGER, _SESSION_LOG = configure_logging()

# Fields that are unambiguously visual/localizable even if a particular map's
# skin table happens not to contain an example of them yet.  The primary
# classifier is still learned dynamically from the existing war3mapSkin.w3*
# tables; this is only the safety net for newly-created objects/fields.
SKIN_FALLBACK_FIELDS: dict[str, set[str]] = {
    "unit": {
        "unam", "unsf", "upro", "umdl", "usca", "ussc", "uico",
        "uclr", "uclg", "uclb", "ua1m",
    },
    "ability": {
        "anam", "ansf", "aart", "abpx", "abpy", "ahky",
        "atp1", "aub1", "aret", "arut", "arhk",
    },
    "doodad": {"dnam", "dfil", "dmis", "dmas", "dvar"},
}

@dataclass
class EditorSaveReport:
    output_path: Path
    backup_path: Path | None
    changed_files: list[str]
    imported: list[str]
    protected_hashes_ok: bool


@dataclass
class EditSession:
    map_path: Path
    objects: dict[str, ObjectModFile]
    imports: ImportFile
    protected_hashes: dict[str, str | None]
    source_hashes: dict[str, str | None] = field(default_factory=dict)
    placed_units: UnitPlacementFile | None = None
    placed_units_baseline: UnitPlacementFile | None = None
    placed_units_error: str = ""
    placed_units_dirty: bool = False
    placed_runtime_forced: dict[int, set[str]] = field(default_factory=dict)
    studio_added_placements: set[int] = field(default_factory=set)
    # Original World Editor placements removed during this session.  Their DOO
    # records disappear immediately in memory; snapshots are retained only so
    # save() can emit the minimal post-CreateAllUnits RemoveUnit runtime block.
    removed_placements: dict[int, UnitPlacement] = field(default_factory=dict)
    dirty: set[str] = field(default_factory=set)
    skin_dirty: set[str] = field(default_factory=set)
    staged_assets: list[AssetSpec] = field(default_factory=list)
    native_data: NativeDataCatalog | None = None
    native_abilities: dict[str, dict[str, str]] = field(default_factory=dict)
    native_doodads: dict[str, dict[str, object]] = field(default_factory=dict)
    native_doodad_metadata: dict[str, dict[str, object]] = field(default_factory=dict)
    map_skin: MapSkinCatalog = field(default_factory=MapSkinCatalog.empty)
    map_strings: MapStringTable = field(default_factory=MapStringTable.empty)
    editor_strings: EditorStringCatalog = field(default_factory=EditorStringCatalog.empty)

    @classmethod
    def load(cls, map_path: str | Path, dll_path: str | None = None) -> "EditSession":
        path = Path(map_path).resolve()
        LOGGER.info("EditSession.load START path=%s dll=%s", path, dll_path)
        if path.suffix.lower() not in {".w3m", ".w3x"}:
            raise InjectorError("o mapa precisa ser .w3m ou .w3x")
        if not path.is_file():
            raise InjectorError(f"mapa não encontrado: {path}")
        with StormArchive(path, dll_path=dll_path, read_only=True) as arc:
            LOGGER.info("MPQ open succeeded; loading editable object tables")
            objects: dict[str, ObjectModFile] = {}
            present_kinds: set[str] = set()
            for kind, (object_path, _codec_kind) in OBJECT_FILES.items():
                exists = arc.has(object_path)
                if exists:
                    present_kinds.add(kind)
                objects[kind] = _load_object(arc, kind)

            # If this is a Reforged map using object-data v3, keep that format
            # for any editable table that does not exist yet. This avoids
            # creating a legacy v2 w3a/w3d next to an existing v3 w3u.
            present_versions = [objects[k].version for k in present_kinds]
            preferred_version = max(present_versions, default=2)
            for kind in OBJECT_FILES:
                if kind not in present_kinds:
                    objects[kind].version = preferred_version
                    LOGGER.info(
                        "Missing %s will be created as object-data v%d",
                        OBJECT_FILES[kind][0],
                        preferred_version,
                    )

            if arc.has("war3map.imp"):
                raw_imp=arc.read("war3map.imp")
                LOGGER.info("Parsing war3map.imp (%d bytes)", len(raw_imp))
                imp=ImportFile.from_bytes(raw_imp)
                LOGGER.info("Parsed imports: %d entries", len(imp.entries))
            else:
                LOGGER.info("war3map.imp not present -> empty import table")
                imp=ImportFile.empty()

            # Reforged maps can split art/text fields into war3mapSkin.w3*.
            # Load them as a read-only preview/display overlay.  This is crucial
            # for maps where umdl/uico/unam exist only in the skin object file.
            map_skin = MapSkinCatalog.load(arc)
            if arc.has("war3map.wts"):
                try:
                    map_strings = MapStringTable.from_bytes(arc.read("war3map.wts"))
                    LOGGER.info("Map string table loaded: %d TRIGSTR entries", len(map_strings))
                except Exception:
                    LOGGER.warning("Failed parsing war3map.wts; TRIGSTR values will stay raw", exc_info=True)
                    map_strings = MapStringTable.empty()
            else:
                map_strings = MapStringTable.empty()

            placed_units = None
            placed_units_error = ""
            if arc.has("war3mapUnits.doo"):
                try:
                    raw_units = arc.read("war3mapUnits.doo")
                    placed_units = UnitPlacementFile.from_bytes(raw_units)
                    LOGGER.info(
                        "Placed units loaded: version=%d subversion=%d skin=%s units=%d heroes=%d bytes=%d",
                        placed_units.version, placed_units.subversion, placed_units.has_skin,
                        len(placed_units.units), len(placed_units.heroes()), len(raw_units),
                    )
                except Exception as exc:
                    placed_units_error = str(exc)
                    LOGGER.warning("Could not parse war3mapUnits.doo; placed-hero editing disabled: %s", exc, exc_info=True)
            else:
                LOGGER.info("war3mapUnits.doo not present -> placed-hero editor unavailable")

            studio_added: set[int] = set()
            if arc.has("war3map.j"):
                try:
                    studio_added = studio_added_creation_numbers(arc.read("war3map.j"))
                    if studio_added:
                        LOGGER.info("Recovered %d Studio-added placement marker(s): %s", len(studio_added), sorted(studio_added))
                except Exception:
                    LOGGER.warning("Could not inspect Studio-added placement markers", exc_info=True)

            LOGGER.info("Snapshotting protected files")
            protected = _snapshot(arc)
            LOGGER.info("Snapshotting editable files")
            editable = _snapshot_editable(arc)
        catalogs = load_native_catalogs()
        native = catalogs.units
        native_abilities = catalogs.abilities
        native_doodads = catalogs.doodads
        native_doodad_metadata = catalogs.doodad_metadata
        session=cls(
            path, objects, imp, protected, editable,
            placed_units=placed_units, placed_units_baseline=copy.deepcopy(placed_units) if placed_units is not None else None, placed_units_error=placed_units_error,
            studio_added_placements=studio_added,
            native_data=native, native_abilities=native_abilities,
            native_doodads=native_doodads, native_doodad_metadata=native_doodad_metadata,
            map_skin=map_skin, map_strings=map_strings, editor_strings=catalogs.editor_strings,
        )
        LOGGER.info(
            "EditSession.load DONE units=%d abilities=%d doodads=%d imports=%d "
            "skin_units=%d skin_abilities=%d skin_doodads=%d map_strings=%d "
            "native_fields=%d native_units=%d native_abilities=%d native_ability_profiles=%d native_doodads=%d native_doodad_fields=%d editor_strings=%d placed_units=%d placed_heroes=%d native_ready=%s",
            len(session.list_records('unit')), len(session.list_records('ability')), len(session.list_records('doodad')), len(session.imports.entries),
            session.map_skin.count('unit'), session.map_skin.count('ability'), session.map_skin.count('doodad'), len(session.map_strings),
            len(native.metadata), len(session.native_unit_ids()), len(session.native_ids('ability')), len(native_abilities), len(native_doodads), len(native_doodad_metadata), len(session.editor_strings), len(session.placed_units_list()), len(session.placed_heroes()), native.ready
        )
        return session

    def placed_units_list(self) -> list[UnitPlacement]:
        """All preplaced units/items from war3mapUnits.doo in file order."""
        return list(self.placed_units.units) if self.placed_units is not None else []

    def placed_unit(self, creation_number: int) -> UnitPlacement | None:
        return self.placed_units.by_creation_number(int(creation_number)) if self.placed_units is not None else None

    def placed_unit_at(self, index: int) -> UnitPlacement | None:
        """Return one placement by its stable file-order index.

        ``creation_number`` is normally unique in World Editor maps, but maps
        modified by third-party tools can legally contain duplicates (often 0).
        UI code must therefore never use creation_number as its sole identity.
        """
        if self.placed_units is None:
            return None
        try:
            idx = int(index)
        except Exception:
            return None
        if idx < 0 or idx >= len(self.placed_units.units):
            return None
        return self.placed_units.units[idx]

    def placed_unit_index(self, unit: UnitPlacement | None) -> int | None:
        """Return the file-order index for an exact placement object."""
        if self.placed_units is None or unit is None:
            return None
        for idx, candidate in enumerate(self.placed_units.units):
            if candidate is unit:
                return idx
        return None

    def placement_creation_count(self, creation_number: int) -> int:
        if self.placed_units is None:
            return 0
        creation = int(creation_number)
        return sum(1 for u in self.placed_units.units if int(u.creation_number) == creation)

    def placed_heroes(self) -> list[UnitPlacement]:
        return [u for u in self.placed_units_list() if u.is_hero]

    def placed_hero(self, creation_number: int) -> UnitPlacement | None:
        unit = self.placed_unit(creation_number)
        return unit if unit is not None and unit.is_hero else None

    def mark_placed_units_dirty(self) -> None:
        if self.placed_units is None:
            raise InjectorError(self.placed_units_error or "war3mapUnits.doo não está disponível para edição")
        self.placed_units_dirty = True

    def next_placement_creation_number(self) -> int:
        if self.placed_units is None:
            raise InjectorError(self.placed_units_error or "war3mapUnits.doo não está disponível para edição")
        used = {int(u.creation_number) for u in self.placed_units.units}
        candidate = max(used, default=-1) + 1
        while candidate in used:
            candidate += 1
        if candidate > 0x7FFFFFFF:
            raise InjectorError("não há creation_number disponível para uma nova unidade")
        return candidate

    def add_placed_unit(
        self,
        unit_id: str,
        *,
        x: float = 0.0, y: float = 0.0, z: float = 0.0,
        angle_degrees: float = 270.0, player: int = 0,
        scale: float = 1.0, hero_level: int = 1,
    ) -> UnitPlacement:
        """Add one placement to Units.doo and mark it for minimal JASS creation."""
        if self.placed_units is None:
            # Empty maps may legitimately have no war3mapUnits.doo yet. In that
            # case create a modern empty placement table on first add. If parsing
            # failed for an existing DOO, keep refusing rather than overwriting it.
            if self.placed_units_error:
                raise InjectorError(self.placed_units_error)
            self.placed_units = UnitPlacementFile.empty()
            self.placed_units_baseline = copy.deepcopy(self.placed_units)
            LOGGER.info("Created empty war3mapUnits.doo model for first Studio-added placement")
        raw = str(unit_id)
        if len(raw) != 4:
            raise InjectorError("rawcode da unidade precisa ter exatamente 4 caracteres")
        raw.encode("latin1", errors="strict")
        if not (0 <= int(player) <= 27):
            raise InjectorError("owner/player inválido para a nova unidade")
        if float(scale) <= 0:
            raise InjectorError("escala precisa ser maior que zero")
        creation = self.next_placement_creation_number()
        unit = UnitPlacement(
            unit_id=raw,
            x=float(x), y=float(y), z=float(z),
            angle=math.radians(float(angle_degrees) % 360.0),
            scale_x=float(scale), scale_y=float(scale), scale_z=float(scale),
            skin_id=raw if self.placed_units.has_skin else None,
            player=int(player), hero_level=max(1, int(hero_level)),
            creation_number=creation,
        )
        self.placed_units.units.append(unit)
        self.studio_added_placements.add(creation)
        self.placed_units_dirty = True
        LOGGER.info(
            "Added placement creation=%d unit=%s player=%d pos=(%.3f,%.3f,%.3f) facing=%.3f scale=%.3f level=%d",
            creation, raw, int(player), float(x), float(y), float(z), float(angle_degrees), float(scale), max(1, int(hero_level)),
        )
        return unit

    def move_placed_unit(self, creation_number: int, x: float, y: float) -> UnitPlacement:
        """Move one placement in memory; save() syncs original placements in JASS."""
        unit = self.placed_unit(int(creation_number))
        if unit is None:
            raise InjectorError("a instância selecionada não existe em war3mapUnits.doo")
        unit.x = float(x)
        unit.y = float(y)
        self.placed_units_dirty = True
        LOGGER.info("Moved placement creation=%d pos=(%.3f,%.3f)", int(creation_number), float(x), float(y))
        return unit

    def move_placed_unit_at(self, index: int, x: float, y: float) -> UnitPlacement:
        """Move exactly one placement even when creation_number is duplicated."""
        unit = self.placed_unit_at(index)
        if unit is None:
            raise InjectorError("a instância selecionada não existe em war3mapUnits.doo")
        unit.x = float(x)
        unit.y = float(y)
        self.placed_units_dirty = True
        LOGGER.info("Moved placement index=%d creation=%d pos=(%.3f,%.3f)", int(index), int(unit.creation_number), float(x), float(y))
        return unit

    def rotate_placed_unit(self, creation_number: int, angle_degrees: float) -> UnitPlacement:
        """Rotate one placement in memory using Warcraft's 0°=east convention."""
        unit = self.placed_unit(int(creation_number))
        if unit is None:
            raise InjectorError("a instância selecionada não existe em war3mapUnits.doo")
        unit.angle = math.radians(float(angle_degrees) % 360.0)
        self.placed_units_dirty = True
        LOGGER.info("Rotated placement creation=%d facing=%.3f", int(creation_number), float(angle_degrees) % 360.0)
        return unit

    def rotate_placed_unit_at(self, index: int, angle_degrees: float) -> UnitPlacement:
        """Rotate exactly one placement even when creation_number is duplicated."""
        unit = self.placed_unit_at(index)
        if unit is None:
            raise InjectorError("a instância selecionada não existe em war3mapUnits.doo")
        unit.angle = math.radians(float(angle_degrees) % 360.0)
        self.placed_units_dirty = True
        LOGGER.info("Rotated placement index=%d creation=%d facing=%.3f", int(index), int(unit.creation_number), float(angle_degrees) % 360.0)
        return unit

    def remove_placed_unit_at(self, index: int) -> bool:
        """Remove one exact placement without collapsing duplicate IDs.

        Original placements with a duplicated creation number cannot be mapped
        safely to a single generated JASS handle, so destructive removal is
        refused instead of silently removing the wrong/all instances.
        """
        unit = self.placed_unit_at(index)
        if unit is None:
            return False
        creation = int(unit.creation_number)
        if self.placement_creation_count(creation) > 1 and creation not in self.studio_added_placements:
            raise InjectorError(
                f"a instância #{creation} usa um creation_number duplicado; "
                "a remoção foi bloqueada para não apagar/sincronizar a unidade errada"
            )
        return self.remove_placed_unit(creation)

    def remove_placed_unit(self, creation_number: int) -> bool:
        """Remove any placement while preserving runtime correctness.

        Studio-added placements are deleted from the Studio creation block.
        Original World Editor placements are removed from DOO and remembered so
        save() can add a tiny post-CreateAllUnits ``RemoveUnit`` block instead
        of rewriting the map's generated creation functions.
        """
        if self.placed_units is None:
            raise InjectorError(self.placed_units_error or "war3mapUnits.doo não está disponível para edição")
        creation = int(creation_number)
        unit = self.placed_unit(creation)
        if unit is None:
            return False
        snapshot = copy.deepcopy(unit)
        self.placed_units.units = [u for u in self.placed_units.units if int(u.creation_number) != creation]
        if creation in self.studio_added_placements:
            self.studio_added_placements.discard(creation)
            LOGGER.info("Removed Studio-added placement creation=%d", creation)
        else:
            self.removed_placements[creation] = snapshot
            LOGGER.info("Removed original placement creation=%d unit=%s pos=(%.3f,%.3f)", creation, snapshot.unit_id, snapshot.x, snapshot.y)
        self.placed_runtime_forced.pop(creation, None)
        self.placed_units_dirty = True
        return True

    def remove_studio_placed_unit(self, creation_number: int) -> bool:
        """Backwards-compatible alias; removal is now safe for all placements."""
        return self.remove_placed_unit(creation_number)

    def force_placed_runtime_sync(self, creation_number: int, fields: set[str] | None = None) -> None:
        """Explicitly repair runtime state for a placement already correct in the DOO.

        This is intentionally opt-in. Normal saves still patch only fields that
        changed since load/last save. It exists for maps previously saved by an
        older Studio version where war3mapUnits.doo already contains the desired
        value but compiled war3map.j is stale.
        """
        if self.placed_units is None or self.placed_unit(int(creation_number)) is None:
            raise InjectorError("a instância selecionada não existe em war3mapUnits.doo")
        if self.placement_creation_count(int(creation_number)) != 1:
            raise InjectorError(
                f"não é possível sincronizar runtime da instância #{int(creation_number)}: "
                "creation_number duplicado no war3mapUnits.doo"
            )
        selected = set(fields or SUPPORTED_RUNTIME_FIELDS) & SUPPORTED_RUNTIME_FIELDS
        if not selected:
            return
        self.placed_runtime_forced.setdefault(int(creation_number), set()).update(selected)
        self.placed_units_dirty = True
        LOGGER.info("Forced placement runtime sync creation=%d fields=%s", int(creation_number), sorted(selected))

    def placed_runtime_change_sets(self) -> tuple[dict[int, set[str]], dict[int, set[str]]]:
        """Return (runtime-supported, placement-only) changes since load/save.

        Runtime JASS patches are keyed by World Editor creation_number.  Some
        third-party maps contain duplicate creation numbers; those records are
        still editable/savable in war3mapUnits.doo, but a single JASS target
        cannot be chosen safely.  Duplicate IDs are therefore excluded from
        runtime patch generation instead of being compared against the wrong
        baseline record (which used to create false changes for every duplicate).
        """
        if self.placed_units is None or self.placed_units_baseline is None:
            return {}, {}

        def group_by_creation(units):
            grouped: dict[int, list[UnitPlacement]] = {}
            for unit in units:
                grouped.setdefault(int(unit.creation_number), []).append(unit)
            return grouped

        before_groups = group_by_creation(self.placed_units_baseline.units)
        current_groups = group_by_creation(self.placed_units.units)
        duplicate_ids = {
            creation for creation in (set(before_groups) | set(current_groups))
            if len(before_groups.get(creation, ())) > 1 or len(current_groups.get(creation, ())) > 1
        }
        if duplicate_ids:
            LOGGER.warning(
                "Duplicate placement creation_number values detected; runtime sync disabled for ids=%s (DOO editing remains available)",
                sorted(duplicate_ids)[:64],
            )

        runtime: dict[int, set[str]] = {}
        unsupported: dict[int, set[str]] = {}
        for creation, current_list in current_groups.items():
            if creation in duplicate_ids or creation in self.studio_added_placements:
                continue
            old_list = before_groups.get(creation, [])
            if len(current_list) != 1 or len(old_list) != 1:
                continue
            current = current_list[0]
            old = old_list[0]
            fields = placement_runtime_changed_fields(old, current)
            if fields:
                runtime[creation] = fields
            other = placement_non_runtime_changed_fields(old, current)
            if other:
                unsupported[creation] = other

        for creation, fields in self.placed_runtime_forced.items():
            creation = int(creation)
            if creation in duplicate_ids or creation in self.studio_added_placements:
                continue
            if len(current_groups.get(creation, ())) == 1:
                runtime.setdefault(creation, set()).update(set(fields) & SUPPORTED_RUNTIME_FIELDS)
        return runtime, unsupported

    def _unit_numeric_runtime_value(self, rawcode: str, field_id: str) -> float | None:
        """Resolve one numeric unit-type field through map overrides + native data."""
        try:
            if self.get_record("unit", "custom", rawcode) is not None:
                mod, _source, _explicit = self.resolve_map_value("unit", "custom", rawcode, field_id)
            elif self.get_record("unit", "original", rawcode) is not None:
                mod, _source, _explicit = self.resolve_map_value("unit", "original", rawcode, field_id)
            else:
                mod, _source = self._native_value("unit", rawcode, field_id)
            if mod is None:
                return None
            return float(mod.value)
        except Exception:
            LOGGER.debug("Could not resolve runtime unit field %s.%s", rawcode, field_id, exc_info=True)
            return None

    def placement_runtime_defaults(self, unit: UnitPlacement) -> PlacementRuntimeDefaults:
        """Values needed when a placement switches back to World Editor defaults."""
        acq = self._unit_numeric_runtime_value(unit.unit_id, "uacq")
        level = max(1, int(unit.hero_level))

        def hero_stat(base_field: str, gain_field: str) -> int | None:
            base = self._unit_numeric_runtime_value(unit.unit_id, base_field)
            gain = self._unit_numeric_runtime_value(unit.unit_id, gain_field)
            if base is None:
                return None
            if gain is None:
                gain = 0.0
            # Warcraft truncates fractional hero attributes toward zero.
            return int(float(base) + (level - 1) * float(gain))

        baseline = None
        if self.placed_units_baseline is not None:
            matches = [u for u in self.placed_units_baseline.units if int(u.creation_number) == int(unit.creation_number)]
            # Duplicate creation numbers cannot be mapped to one generated JASS
            # variable reliably.  Fall back to the current position rather than
            # borrowing another duplicate's baseline coordinates.
            baseline = matches[0] if len(matches) == 1 else None
        return PlacementRuntimeDefaults(
            acquisition=acq,
            hero_strength=hero_stat("ustr", "ustp"),
            hero_agility=hero_stat("uagi", "uagp"),
            hero_intelligence=hero_stat("uint", "uinp"),
            lookup_x=float(baseline.x) if baseline is not None else float(unit.x),
            lookup_y=float(baseline.y) if baseline is not None else float(unit.y),
        )

    def resolve_string(self, value: object) -> str:
        """Resolve map TRIGSTR and Blizzard WESTRING tokens for display."""
        text = self.map_strings.resolve(value) if self.map_strings is not None else str(value or "")
        return self.editor_strings.resolve(text) if self.editor_strings is not None else text

    def skin_record(self, kind: str, table: str, object_id: str):
        return self.map_skin.get_record(kind, table, object_id) if self.map_skin is not None else None

    def skin_value(
        self, kind: str, table: str, object_id: str, field_id: str, *, level: int = 0, column: int = 0
    ):
        """Return a read-only field from ``war3mapSkin.w3*`` for display/preview."""
        rec = self.skin_record(kind, table, object_id)
        if rec is None:
            return None, ""
        obj = self.map_skin.tables.get(kind)
        extended = bool(obj.extended) if obj is not None else kind in {"ability", "doodad"}
        mod = self._find_mod(rec, field_id, extended=extended, level=level, column=column)
        if mod is None:
            return None, ""
        value = mod.value
        if mod.value_type == 3:
            value = self.resolve_string(value)
        return Modification(mod.field_id, mod.value_type, value, mod.level, mod.column, mod.end_id), f"skin do mapa · war3mapSkin.{ {'unit':'w3u','ability':'w3a','doodad':'w3d'}.get(kind,'w3?') }"

    def native_unit_ids(self) -> set[str]:
        if self.native_data is None:
            return set()
        ids: set[str] = set(self.native_data.profiles)
        for table in self.native_data.tables.values():
            ids.update(table)
        return {x for x in ids if isinstance(x, str) and len(x) == 4}

    def native_ids(self, kind: str) -> set[str]:
        if kind == "unit":
            return self.native_unit_ids()
        if kind == "ability":
            # ability_native keeps Bxxx/Xxxx auxiliary Buff/Effect profiles in
            # the same dictionary because Spell Lab needs their Art fields.
            # Only rows sourced from AbilityData.slk are real Ability objects
            # and should appear in the Warcraft browser.
            marked = {
                raw for raw, row in self.native_abilities.items()
                if isinstance(raw, str) and len(raw) == 4
                and isinstance(row, dict) and row.get("__ability_object__") == "1"
            }
            if marked:
                return marked
            # Compatibility with tests/user-supplied legacy profile dictionaries.
            return {x for x in self.native_abilities if isinstance(x, str) and len(x) == 4}
        if kind == "doodad":
            return {x for x in self.native_doodads if isinstance(x, str) and len(x) == 4}
        return set()

    def is_native_object(self, kind: str, rawcode: str) -> bool:
        return rawcode in self.native_ids(kind)

    def native_name(self, kind: str, rawcode: str) -> str:
        if kind == "unit" and self.native_data is not None:
            mod, _source = self.native_data.resolve_unit(rawcode, "unam")
            if mod is not None and str(mod.value).strip():
                value=str(mod.value).strip()
                if not value.upper().startswith("WESTRING_"):
                    return value
            row=self.native_data.profiles.get(rawcode,{})
            for key in ("Name", "Tip"):
                value=next((str(v).strip() for k,v in row.items() if k.casefold()==key.casefold() and str(v).strip()),"")
                if value and not value.upper().startswith("WESTRING_"):
                    return value.split(",")[0]
        if kind == "ability":
            row = self.native_abilities.get(rawcode,{})
            name = profile_display_name(row, rawcode, resolver=self.resolve_string)
            if name != rawcode:
                return name
            # Some internal/editor variants carry no own localized Name but do
            # point at a named base ability through AbilityData.code/base.
            folded = {str(k).casefold(): str(v).strip() for k,v in row.items()}
            for key in ("code", "base", "parent", "baseid"):
                base = folded.get(key, "")
                if len(base) == 4 and base != rawcode and base in self.native_abilities:
                    inherited = profile_display_name(
                        self.native_abilities.get(base,{}), base, resolver=self.resolve_string
                    )
                    if inherited != base:
                        return inherited
            return rawcode
        if kind == "doodad":
            row=self.native_doodads.get(rawcode,{})
            return doodad_display_name(row, rawcode, resolver=self.resolve_string)
        return rawcode

    def list_records(self, kind: str, include_native: bool = False) -> list[tuple[str, str, str, object]]:
        """Return (table, object_id, base_id, record).

        ``include_native`` also exposes standard Warcraft objects that are not
        stored in the map's w3u/w3a tables. They are virtual/read-only until the
        user edits them; applying creates a normal original-table override.
        """
        data = self.objects[kind]
        rows: list[tuple[str, str, str, object]] = []
        present: set[str] = set()
        for rec in data.custom:
            rows.append(("custom", rec.custom_id, rec.original_id, rec)); present.add(rec.custom_id)
        for rec in data.original:
            rows.append(("original", rec.original_id, rec.original_id, rec)); present.add(rec.original_id)
        if include_native:
            for rawcode in sorted(self.native_ids(kind)):
                if rawcode in present:
                    continue
                rows.append(("native", rawcode, rawcode, ObjectRecord(rawcode, "\0\0\0\0", [])))
        return rows

    def get_record(self, kind: str, table: str, object_id: str):
        if table == "native":
            if self.is_native_object(kind, object_id):
                return ObjectRecord(object_id, "\0\0\0\0", [])
            return None
        data = self.objects[kind]
        rows = data.custom if table == "custom" else data.original
        for rec in rows:
            rid = rec.custom_id if table == "custom" else rec.original_id
            if rid == object_id:
                return rec
        return None

    @staticmethod
    def _find_mod(rec, field_id: str, *, extended: bool, level: int = 0, column: int = 0):
        if rec is None:
            return None
        for mod in rec.modifications:
            if mod.field_id != field_id:
                continue
            if not extended or (mod.level == level and mod.column == column):
                return mod
        return None

    def _native_value(self, kind: str, rawcode: str, field_id: str, *, level: int = 0, column: int = 0):
        if kind == "unit" and self.native_data is not None:
            mod, source = self.native_data.resolve_unit(rawcode, field_id)
            if mod is not None and mod.value_type == 3:
                mod = Modification(mod.field_id, mod.value_type, self.resolve_string(mod.value), mod.level, mod.column, mod.end_id)
            return mod, source
        if kind == "ability":
            row=self.native_abilities.get(rawcode,{})
            value,key=profile_field_value(row, field_id, level)
            if value == "":
                return None, f"base nativa {rawcode} · CASC sem {key or field_id}"
            # Reuse the GUI field definition when possible to preserve numeric types.
            from ..fields import FIELD_BY_ID
            fd_pair=FIELD_BY_ID.get("ability",{}).get(field_id)
            value_type=fd_pair[1].type if fd_pair else 3
            try:
                if value_type == 0:
                    low=str(value).strip().casefold()
                    if low in {"true","false"}: parsed=1 if low=="true" else 0
                    else: parsed=int(float(str(value).strip()))
                elif value_type in (1,2): parsed=float(str(value).strip())
                else: parsed=self.resolve_string(value)
            except Exception:
                parsed=self.resolve_string(value); value_type=3
            return Modification(field_id,value_type,parsed,level,column), f"base nativa {rawcode} · CASC.{key or field_id}"
        if kind == "doodad":
            row=self.native_doodads.get(rawcode,{})
            value, used, value_type = doodad_field_value(
                row, self.native_doodad_metadata, field_id, variation=max(1, int(level or 1))
            )
            if value is None:
                return None, f"base nativa {rawcode} · CASC.DoodadData sem {used}"
            if value_type == 3:
                value = self.resolve_string(value)
                if field_id == "dnam" and (not value or str(value).upper().startswith("WESTRING_")):
                    value = doodad_display_name(row, rawcode, resolver=self.resolve_string)
            return Modification(field_id,value_type,value,level,column), f"base nativa {rawcode} · CASC.DoodadData.{used}"
        return None, f"herdado do Warcraft ({rawcode})"

    def resolve_map_value(
        self,
        kind: str,
        table: str,
        object_id: str,
        field_id: str,
        *,
        level: int = 0,
        column: int = 0,
    ):
        """Resolve a field through object-data stored *inside the map*.

        Returns ``(modification, source_label, explicit)``.  This intentionally
        does not pretend to know Blizzard's native SLK/profile value: if the
        chain reaches a native rawcode and the map has no original-table
        override for that field, ``modification`` is ``None`` and the source
        label says that the value is inherited from Warcraft.

        This distinction is important for the GUI: inherited values displayed
        from map overrides must not be written back as new custom modifications
        merely because the user selected the object.
        """
        if kind not in self.objects:
            raise InjectorError(f"tipo de objeto inválido: {kind}")
        extended = self.objects[kind].extended
        visited: set[tuple[str, str]] = set()

        def walk(cur_table: str, cur_id: str, *, first: bool):
            key = (cur_table, cur_id)
            if key in visited:
                return None, f"herança circular detectada em {cur_id}", False
            visited.add(key)
            rec = self.get_record(kind, cur_table, cur_id)
            if rec is None:
                native, source = self._native_value(kind, cur_id, field_id, level=level, column=column)
                return native, source, False

            mod = self._find_mod(rec, field_id, extended=extended, level=level, column=column)
            if mod is not None:
                if first:
                    return mod, "override neste objeto", True
                return mod, f"herdado de {cur_id} no mapa", False

            # Reforged may keep art/text only in war3mapSkin.w3*. Treat the skin
            # row as a read-only display/preview overlay before falling through
            # to the Blizzard/native parent.
            skin_mod, skin_source = self.skin_value(
                kind, cur_table, cur_id, field_id, level=level, column=column
            )
            if skin_mod is not None:
                if first:
                    return skin_mod, skin_source, False
                return skin_mod, f"{skin_source} · herdado de {cur_id}", False

            if cur_table == "original":
                native, source = self._native_value(kind, cur_id, field_id, level=level, column=column)
                return native, source, False

            base = rec.original_id
            # A custom object may theoretically derive from another custom ID.
            # Prefer such a record when it exists; otherwise look for an
            # original-table override of the native base rawcode.
            if self.get_record(kind, "custom", base) is not None:
                return walk("custom", base, first=False)
            if self.get_record(kind, "original", base) is not None:
                return walk("original", base, first=False)
            native, source = self._native_value(kind, base, field_id, level=level, column=column)
            return native, source, False

        return walk(table, object_id, first=True)

    def resolve_inherited_value(
        self,
        kind: str,
        table: str,
        object_id: str,
        field_id: str,
        *,
        level: int = 0,
        column: int = 0,
    ):
        """Resolve only the parent/base side of an object, skipping its own mod."""
        rec = self.get_record(kind, table, object_id)
        if rec is None:
            return None, f"objeto {object_id} não encontrado", False
        if table == "native":
            native, source = self._native_value(kind, object_id, field_id, level=level, column=column)
            return native, source, False
        if table == "original":
            native, source = self._native_value(kind, object_id, field_id, level=level, column=column)
            return native, source, False
        base = rec.original_id
        if self.get_record(kind, "custom", base) is not None:
            mod, _source, _explicit = self.resolve_map_value(kind, "custom", base, field_id, level=level, column=column)
            if mod is not None:
                return mod, f"herdado de {base} no mapa", False
            native, source = self._native_value(kind, base, field_id, level=level, column=column)
            return native, source, False
        if self.get_record(kind, "original", base) is not None:
            mod, _source, _explicit = self.resolve_map_value(kind, "original", base, field_id, level=level, column=column)
            if mod is not None:
                return mod, f"herdado de {base} no mapa", False
            native, source = self._native_value(kind, base, field_id, level=level, column=column)
            return native, source, False
        native, source = self._native_value(kind, base, field_id, level=level, column=column)
        return native, source, False

    @staticmethod
    def _mod_value_signature(mod: Modification) -> tuple:
        return (mod.value_type, mod.value, mod.level, mod.column)

    def _sync_reforged_skin_changes(
        self,
        kind: str,
        table: str,
        object_id: str,
        base_id: str | None,
        before_modifications: list[Modification],
        after_modifications: list[Modification],
    ) -> bool:
        """Mirror changed Reforged skin fields into war3mapSkin.w3*.

        Reforged splits one logical Object Editor object into two files.  The
        runtime overlays art/text from war3mapSkin.w3* on top of war3map.w3*.
        Writing only the latter can therefore look correct in an editor while
        the game keeps using the old model/icon/scale.

        Only fields whose values actually changed in this edit are mirrored,
        and only when the map already has a skin table for this object kind.
        This avoids copying unrelated gameplay data into the skin layer.
        """
        skin_obj = self.map_skin.tables.get(kind) if self.map_skin is not None else None
        if skin_obj is None:
            return False
        extended = self.objects[kind].extended
        before = {m.key(extended): m for m in before_modifications}
        after = {m.key(extended): m for m in after_modifications}
        changed_keys = {
            key for key in (set(before) | set(after))
            if (key not in before or key not in after or
                self._mod_value_signature(before[key]) != self._mod_value_signature(after[key]))
        }
        if not changed_keys:
            return False

        skin_fields = self.map_skin.field_ids(kind) | SKIN_FALLBACK_FIELDS.get(kind, set())
        skin_keys = {key for key in changed_keys if key and key[0] in skin_fields}
        if not skin_keys:
            return False

        upserts = [after[key] for key in skin_keys if key in after]
        removals = {key for key in skin_keys if key not in after}
        changed = self.map_skin.patch_record_fields(
            kind, table, object_id, base_id=base_id,
            upserts=upserts, remove_keys=removals,
        )
        if changed:
            self.skin_dirty.add(kind)
            LOGGER.info(
                "Synced Reforged skin kind=%s table=%s id=%s fields=%s removed=%s",
                kind, table, object_id, sorted({m.field_id for m in upserts}),
                sorted({key[0] for key in removals}),
            )
        return changed

    def set_record(
        self,
        kind: str,
        table: str,
        object_id: str,
        modifications: list[Modification],
        base_id: str | None = None,
    ):
        if kind not in OBJECT_FILES:
            raise InjectorError(f"tipo de objeto inválido: {kind}")
        if table == "native":
            table = "original"
        if len(object_id) != 4:
            raise InjectorError("rawcode precisa ter exatamente 4 caracteres")
        if table == "custom" and (not base_id or len(base_id) != 4):
            raise InjectorError("objeto custom precisa de rawcode base com 4 caracteres")
        LOGGER.info("Set record kind=%s table=%s id=%s base=%s modifications=%d", kind, table, object_id, base_id, len(modifications))
        previous = self.get_record(kind, table, object_id)
        before_modifications = list(previous.modifications) if previous is not None else []
        rec = self.objects[kind].replace_record_modifications(table, object_id, modifications, base_id)
        self.dirty.add(kind)
        self._sync_reforged_skin_changes(
            kind, table, object_id, base_id, before_modifications, list(rec.modifications)
        )
        return rec

    def new_custom(self, kind: str, base_id: str, object_id: str, modifications: list[Modification]):
        return self.set_record(kind, "custom", object_id, modifications, base_id)

    def delete_record(self, kind: str, table: str, object_id: str) -> bool:
        changed = self.objects[kind].delete_record(table, object_id)
        if changed:
            self.dirty.add(kind)
        return changed

    def stage_asset(self, source: str | Path, archive_path: str) -> None:
        src = Path(source).resolve()
        if not src.is_file():
            raise InjectorError(f"asset não encontrado: {src}")
        if src.suffix.lower() not in SUPPORTED_ASSETS:
            raise InjectorError(f"asset não suportado: {src.name}")
        ap = archive_path.replace("/", "\\").strip()
        if not ap or ap.startswith("\\") or ap.endswith("\\") or "..\\" in (ap + "\\"):
            raise InjectorError(f"caminho interno inválido: {archive_path}")
        # Last explicit staging wins for the same archive path.
        self.staged_assets = [a for a in self.staged_assets if a.archive_path.casefold() != ap.casefold()]
        self.staged_assets.append(AssetSpec(src, ap))
        self.imports.add_custom(ap)
        LOGGER.info("Staged asset source=%s archive=%s size=%d", src, ap, src.stat().st_size)

    def save(
        self,
        output_path: str | Path | None = None,
        dll_path: str | None = None,
        compact: bool = False,
        make_backup: bool = True,
    ) -> EditorSaveReport:
        LOGGER.info("EditSession.save START output=%s compact=%s backup=%s dirty=%s skin_dirty=%s placed_units_dirty=%s staged_assets=%d", output_path, compact, make_backup, sorted(self.dirty), sorted(self.skin_dirty), self.placed_units_dirty, len(self.staged_assets))
        if not self.dirty and not self.skin_dirty and not self.placed_units_dirty and not self.staged_assets:
            raise InjectorError("não há alterações para salvar")

        source = self.map_path.resolve()
        final = Path(output_path).resolve() if output_path else source
        if final.suffix.lower() != source.suffix.lower():
            raise InjectorError("a extensão de saída deve ser igual à do mapa de origem")
        final.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{final.stem}.wc3edit_", suffix=final.suffix, dir=str(final.parent)
        )
        os.close(fd)
        work = Path(tmp_name)
        backup: Path | None = None
        changed_files: list[str] = []
        imported: list[str] = []
        desired_written: dict[str, tuple[bytes, str]] = {}
        desired_units_raw: bytes | None = None
        desired_jass_raw: bytes | None = None
        expected_protected: dict[str, str | None] = dict(self.protected_hashes)

        try:
            LOGGER.info("Transaction copy: %s -> %s", source, work)
            shutil.copy2(source, work)
            LOGGER.info("Transaction copy size=%d", work.stat().st_size)
            with StormArchive(work, dll_path=dll_path) as arc:
                before = _snapshot(arc)
                editable_before = _snapshot_editable(arc)
                if before != self.protected_hashes:
                    raise ValidationError(
                        "o mapa de origem mudou desde que foi aberto; recarregue antes de salvar"
                    )
                if self.source_hashes and editable_before != self.source_hashes:
                    raise ValidationError(
                        "w3u/w3a/w3d/imports mudaram externamente desde a abertura; recarregue o mapa"
                    )

                for kind in sorted(self.dirty):
                    path, codec_kind = OBJECT_FILES[kind]
                    encoded = self.objects[kind].to_bytes()
                    LOGGER.info("Encoding/writing kind=%s path=%s bytes=%d", kind, path, len(encoded))
                    # Refuse to write malformed object-data produced by ourselves.
                    parsed = ObjectModFile.from_bytes(encoded, codec_kind)
                    if parsed.to_bytes() != encoded:
                        raise ValidationError(f"round-trip falhou antes da gravação: {path}")
                    arc.write(path, encoded)
                    desired_written[path] = (encoded, codec_kind)
                    changed_files.append(path)

                # Reforged runtime art/text layer.  These files are not optional
                # when the source map already uses them: stale values here would
                # override freshly-written model/icon/scale values from war3map.w3*.
                for kind in sorted(self.skin_dirty):
                    path, codec_kind = SKIN_OBJECT_FILES[kind]
                    skin_obj = self.map_skin.tables.get(kind)
                    if skin_obj is None:
                        raise ValidationError(f"skin marcada como alterada mas tabela ausente: {path}")
                    encoded = skin_obj.to_bytes()
                    LOGGER.info("Encoding/writing Reforged skin kind=%s path=%s bytes=%d", kind, path, len(encoded))
                    parsed = ObjectModFile.from_bytes(encoded, codec_kind)
                    if parsed.to_bytes() != encoded:
                        raise ValidationError(f"round-trip falhou antes da gravação: {path}")
                    arc.write(path, encoded)
                    desired_written[path] = (encoded, codec_kind)
                    changed_files.append(path)

                if self.placed_units_dirty:
                    if self.placed_units is None:
                        raise ValidationError("war3mapUnits.doo marcado como alterado sem parser carregado")

                    runtime_changes, placement_only_changes = self.placed_runtime_change_sets()
                    if placement_only_changes:
                        LOGGER.warning(
                            "Placed fields changed without safe runtime JASS compiler yet: %s",
                            {k: sorted(v) for k, v in placement_only_changes.items()},
                        )

                    # New placements need an explicit creation call in runtime JASS.
                    # Existing placements continue to use the smaller override-only block.
                    needs_jass = bool(runtime_changes or self.studio_added_placements or self.removed_placements)
                    has_jass = arc.has("war3map.j")
                    if needs_jass and not has_jass:
                        if arc.has("war3map.lua"):
                            raise ValidationError(
                                "este mapa usa war3map.lua; adicionar unidades e sincronizar propriedades de instância "
                                "está habilitado somente para mapas JASS por enquanto"
                            )
                        raise ValidationError(
                            "o mapa não possui war3map.j; não é seguro adicionar/sincronizar placements porque o jogo "
                            "poderia continuar executando dados antigos"
                        )

                    if has_jass:
                        jass_work = arc.read("war3map.j")
                        added_defaults = {
                            int(u.creation_number): self.placement_runtime_defaults(u)
                            for u in self.placed_units.units
                            if int(u.creation_number) in self.studio_added_placements
                        }
                        added_patch = patch_jass_added_placements(
                            jass_work,
                            units=self.placed_units.units,
                            creation_numbers=self.studio_added_placements,
                            defaults_by_creation=added_defaults,
                        )
                        jass_work = added_patch.data
                        if added_patch.changed:
                            LOGGER.info(
                                "Patched Studio-added placement creation JASS: placements=%s bytes=%d",
                                sorted(self.studio_added_placements), len(jass_work),
                            )

                        if runtime_changes or self.removed_placements:
                            defaults = {
                                int(u.creation_number): self.placement_runtime_defaults(u)
                                for u in self.placed_units.units
                                if int(u.creation_number) in runtime_changes
                            }
                            runtime_patch = patch_jass_placement_runtime(
                                jass_work,
                                units=self.placed_units.units,
                                changed_fields=runtime_changes,
                                defaults_by_creation=defaults,
                            )
                            jass_work = runtime_patch.data
                            LOGGER.info(
                                "Placement runtime sync: overrides=%s fallbackLookups=%d bytes=%d",
                                {k: sorted(v) for k, v in runtime_patch.override_fields.items()},
                                runtime_patch.fallback_lookup_count, len(jass_work),
                            )

                        if self.removed_placements:
                            removed_patch = patch_jass_removed_placements(
                                jass_work, removed_units=self.removed_placements.values()
                            )
                            jass_work = removed_patch.data
                            LOGGER.info(
                                "Placement removal sync: removed=%s fallbackLookups=%d bytes=%d",
                                sorted(self.removed_placements), removed_patch.fallback_lookup_count, len(jass_work),
                            )

                        original_jass = arc.read("war3map.j")
                        if jass_work != original_jass:
                            desired_jass_raw = jass_work
                            arc.write("war3map.j", desired_jass_raw)
                            expected_protected["war3map.j"] = sha256_bytes(desired_jass_raw)
                            if "war3map.j" not in changed_files:
                                changed_files.append("war3map.j")

                    encoded_units = self.placed_units.to_bytes()
                    parsed_units = UnitPlacementFile.from_bytes(encoded_units, has_skin=self.placed_units.has_skin)
                    if parsed_units.to_bytes() != encoded_units:
                        raise ValidationError("round-trip falhou antes da gravação: war3mapUnits.doo")
                    LOGGER.info(
                        "Encoding/writing placed units path=war3mapUnits.doo bytes=%d heroes=%d",
                        len(encoded_units), len(parsed_units.heroes()),
                    )
                    arc.write("war3mapUnits.doo", encoded_units)
                    desired_units_raw = encoded_units
                    changed_files.append("war3mapUnits.doo")

                if self.staged_assets:
                    for asset in self.staged_assets:
                        arc.add_file(asset.source, asset.archive_path, compress=True)
                        imported.append(asset.archive_path)
                    arc.write("war3map.imp", self.imports.to_bytes())
                    changed_files.append("war3map.imp")

                if compact:
                    arc.compact()

            # Fresh-handle post-write validation.
            LOGGER.info("Starting post-write validation with fresh MPQ handle")
            with StormArchive(work, dll_path=dll_path, read_only=True) as arc:
                after = _snapshot(arc)
                if after != expected_protected:
                    changed_protected = {
                        key: (expected_protected.get(key), after.get(key))
                        for key in set(expected_protected) | set(after)
                        if expected_protected.get(key) != after.get(key)
                    }
                    raise ValidationError(f"arquivo protegido mudou fora do patch permitido; commit recusado: {sorted(changed_protected)}")
                if desired_jass_raw is not None:
                    if not arc.has("war3map.j") or arc.read("war3map.j") != desired_jass_raw:
                        raise ValidationError("war3map.j gravado diverge do patch runtime esperado")
                for path, (expected_raw, codec_kind) in desired_written.items():
                    if not arc.has(path):
                        raise ValidationError(f"object-data ausente após gravação: {path}")
                    raw = arc.read(path)
                    if raw != expected_raw:
                        raise ValidationError(f"bytes gravados divergem do estado em memória: {path}")
                    parsed = ObjectModFile.from_bytes(raw, codec_kind)
                    if parsed.to_bytes() != raw:
                        raise ValidationError(f"round-trip pós-gravação falhou: {path}")
                if desired_units_raw is not None:
                    if not arc.has("war3mapUnits.doo"):
                        raise ValidationError("war3mapUnits.doo ausente após gravação")
                    raw_units = arc.read("war3mapUnits.doo")
                    if raw_units != desired_units_raw:
                        raise ValidationError("bytes gravados divergem do estado em memória: war3mapUnits.doo")
                    parsed_units = UnitPlacementFile.from_bytes(raw_units, has_skin=self.placed_units.has_skin if self.placed_units else None)
                    if parsed_units.to_bytes() != raw_units:
                        raise ValidationError("round-trip pós-gravação falhou: war3mapUnits.doo")

                if self.staged_assets:
                    if not arc.has("war3map.imp"):
                        raise ValidationError("war3map.imp ausente após importação")
                    reread_imp = ImportFile.from_bytes(arc.read("war3map.imp"))
                    listed = {e.archive_path.casefold() for e in reread_imp.entries}
                    for ap in imported:
                        if ap.casefold() not in listed or not arc.has(ap):
                            raise ValidationError(f"asset importado não validou: {ap}")

            if make_backup and final == source:
                stamp = time.strftime("%Y%m%d_%H%M%S")
                backup = source.with_name(f"{source.stem}.backup_{stamp}{source.suffix}")
                shutil.copy2(source, backup)

            LOGGER.info("Committing transaction atomically: %s -> %s", work, final)
            os.replace(work, final)
            LOGGER.info("Commit complete; final size=%d", final.stat().st_size)
            self.map_path = final
            self.dirty.clear()
            self.skin_dirty.clear()
            self.placed_units_dirty = False
            self.placed_runtime_forced.clear()
            self.removed_placements.clear()
            self.staged_assets.clear()
            with StormArchive(final, dll_path=dll_path, read_only=True) as arc:
                self.protected_hashes = _snapshot(arc)
                self.source_hashes = _snapshot_editable(arc)
                self.map_skin = MapSkinCatalog.load(arc)
                if arc.has("war3map.j"):
                    try:
                        self.studio_added_placements = studio_added_creation_numbers(arc.read("war3map.j"))
                    except Exception:
                        LOGGER.warning("Could not refresh Studio-added placement markers after save", exc_info=True)
                else:
                    self.studio_added_placements.clear()
                if arc.has("war3mapUnits.doo"):
                    # Do not make an unrelated object-data save fail merely because
                    # a third-party map uses a placement encoding we cannot edit yet.
                    # If placements were loaded/edited, refresh them; otherwise keep
                    # the original bytes untouched and preserve the diagnostic.
                    if desired_units_raw is not None or self.placed_units is not None:
                        try:
                            self.placed_units = UnitPlacementFile.from_bytes(arc.read("war3mapUnits.doo"))
                            self.placed_units_baseline = copy.deepcopy(self.placed_units)
                            self.placed_units_error = ""
                        except Exception as exc:
                            if desired_units_raw is not None:
                                raise
                            self.placed_units = None
                            self.placed_units_baseline = None
                            self.placed_units_error = str(exc)
                            LOGGER.warning("Placed units remain unavailable after save; file preserved unchanged: %s", exc)
            LOGGER.info("EditSession.save DONE changed=%s imported=%d backup=%s", changed_files, len(imported), backup)
            return EditorSaveReport(final, backup, changed_files, imported, True)
        except Exception:
            LOGGER.exception("EditSession.save FAILED; deleting transaction copy %s", work)
            try:
                work.unlink(missing_ok=True)
            except OSError:
                pass
            raise
