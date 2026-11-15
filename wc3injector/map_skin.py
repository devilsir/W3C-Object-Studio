from __future__ import annotations

from dataclasses import dataclass, field
import copy

from .debuglog import configure_logging
from .objectmod import ObjectModFile, ObjectRecord, Modification

LOGGER, _SESSION_LOG = configure_logging()

SKIN_OBJECT_FILES: dict[str, tuple[str, str]] = {
    "unit": ("war3mapSkin.w3u", "unit"),
    "ability": ("war3mapSkin.w3a", "ability"),
    "doodad": ("war3mapSkin.w3d", "doodad"),
}


@dataclass
class MapSkinCatalog:
    """Reforged map-skin object overlay.

    Modern Warcraft maps can split art/text fields into ``war3mapSkin.w3*``.
    The main ``war3map.w3*`` table may therefore contain gameplay overrides
    while names, icons, models and other localizable/visual fields live in the
    skin table.  The Studio reads this layer for display and patches only the
    skin fields explicitly changed by the user, preserving all unrelated data.
    """

    tables: dict[str, ObjectModFile] = field(default_factory=dict)
    _index: dict[str, dict[tuple[str, str], ObjectRecord]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        for kind, obj in self.tables.items():
            rows: dict[tuple[str, str], ObjectRecord] = {}
            for rec in obj.original:
                rows[("original", rec.original_id)] = rec
            for rec in obj.custom:
                rows[("custom", rec.custom_id)] = rec
            self._index[kind] = rows

    @classmethod
    def empty(cls) -> "MapSkinCatalog":
        return cls({})

    @classmethod
    def load(cls, archive) -> "MapSkinCatalog":
        tables: dict[str, ObjectModFile] = {}
        for kind, (path, codec_kind) in SKIN_OBJECT_FILES.items():
            if not archive.has(path):
                continue
            raw = archive.read(path)
            obj = ObjectModFile.from_bytes(raw, codec_kind)
            tables[kind] = obj
            LOGGER.info(
                "Map skin loaded %s: original=%d custom=%d version=%s",
                path, len(obj.original), len(obj.custom), obj.version,
            )
        return cls(tables)

    def get_record(self, kind: str, table: str, object_id: str) -> ObjectRecord | None:
        if table == "native":
            return None
        return self._index.get(kind, {}).get((table, object_id))

    def count(self, kind: str) -> int:
        obj = self.tables.get(kind)
        if obj is None:
            return 0
        return len(obj.original) + len(obj.custom)

    def field_ids(self, kind: str) -> set[str]:
        """Return field ids already classified into this map's Reforged skin table.

        Blizzard's split is metadata-driven and maps made by different editor
        versions do not always contain exactly the same field set.  Learning the
        skin field ids from the map itself is therefore safer than maintaining a
        brittle hard-coded list.
        """
        obj = self.tables.get(kind)
        if obj is None:
            return set()
        ids: set[str] = set()
        for rec in [*obj.original, *obj.custom]:
            for mod_set in rec.modification_sets:
                ids.update(m.field_id for m in mod_set.modifications)
        return ids

    def patch_record_fields(
        self,
        kind: str,
        table: str,
        object_id: str,
        *,
        base_id: str | None,
        upserts: list[Modification],
        remove_keys: set[tuple],
    ) -> bool:
        """Patch only selected fields in a skin record, preserving all others.

        The main object table and the skin table are two halves of one Reforged
        Object Editor record.  We never replace a whole skin row from the main
        row because doing so could copy gameplay fields into the localization/art
        layer.
        """
        obj = self.tables.get(kind)
        if obj is None:
            return False
        if table == "native":
            table = "original"
        extended = obj.extended
        rec = self.get_record(kind, table, object_id)
        changed = False

        if rec is not None and remove_keys:
            current = rec.modifications
            filtered = [m for m in current if m.key(extended) not in remove_keys]
            if len(filtered) != len(current):
                rec.modifications = filtered
                changed = True

        if upserts:
            mods = [copy.deepcopy(m) for m in upserts]
            if table == "custom":
                if not base_id:
                    raise ValueError("base_id é obrigatório ao criar/editar skin de objeto custom")
                before = self.get_record(kind, table, object_id)
                before_values = {m.key(extended): (m.value_type, m.value) for m in before.modifications} if before else {}
                rec = obj.upsert_custom(base_id, object_id, mods)
            elif table == "original":
                before = self.get_record(kind, table, object_id)
                before_values = {m.key(extended): (m.value_type, m.value) for m in before.modifications} if before else {}
                rec = obj.upsert_original(object_id, mods)
            else:
                raise ValueError(f"tabela inválida: {table}")
            after_values = {m.key(extended): (m.value_type, m.value) for m in rec.modifications}
            if before_values != after_values:
                changed = True

        if changed:
            rows: dict[tuple[str, str], ObjectRecord] = {}
            for row in obj.original:
                rows[("original", row.original_id)] = row
            for row in obj.custom:
                rows[("custom", row.custom_id)] = row
            self._index[kind] = rows
        return changed
