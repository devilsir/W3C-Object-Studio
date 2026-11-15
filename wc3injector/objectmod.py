from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import struct
from typing import Any, Iterable

from .errors import FormatError

TYPE_INT = 0
TYPE_REAL = 1
TYPE_UNREAL = 2
TYPE_STRING = 3

EXTENDED_KINDS = {"doodad", "ability", "upgrade"}
SUPPORTED_VERSIONS = {0, 1, 2, 3}
MAX_REASONABLE_COUNT = 1_000_000


def _rawcode(value: str) -> bytes:
    if len(value) != 4:
        raise ValueError(f"rawcode deve ter exatamente 4 caracteres: {value!r}")
    try:
        return value.encode("latin1")
    except UnicodeEncodeError as exc:
        raise ValueError("rawcodes devem ser Latin-1/ASCII") from exc


def _read_exact(bio: BytesIO, n: int) -> bytes:
    data = bio.read(n)
    if len(data) != n:
        raise FormatError("arquivo object-data truncado")
    return data


def _read_i32(bio: BytesIO) -> int:
    return struct.unpack("<i", _read_exact(bio, 4))[0]


def _write_i32(value: int) -> bytes:
    return struct.pack("<i", int(value))


def _read_u32(bio: BytesIO) -> int:
    return struct.unpack("<I", _read_exact(bio, 4))[0]


def _write_u32(value: int) -> bytes:
    value = int(value)
    if value < 0 or value > 0xFFFFFFFF:
        raise ValueError(f"valor uint32 fora do intervalo: {value}")
    return struct.pack("<I", value)


def _read_f32(bio: BytesIO) -> float:
    return struct.unpack("<f", _read_exact(bio, 4))[0]


def _write_f32(value: float) -> bytes:
    return struct.pack("<f", float(value))


def _read_cstr(bio: BytesIO) -> str:
    out = bytearray()
    while True:
        ch = bio.read(1)
        if not ch:
            raise FormatError("string sem terminador NUL em object-data")
        if ch == b"\0":
            break
        out += ch
    return out.decode("utf-8", errors="surrogateescape")


def _write_cstr(value: str) -> bytes:
    return value.encode("utf-8", errors="surrogateescape") + b"\0"


def _validate_count(value: int, label: str) -> int:
    if value < 0 or value > MAX_REASONABLE_COUNT:
        raise FormatError(f"{label} inválida: {value}")
    return value


@dataclass
class Modification:
    field_id: str
    value_type: int
    value: Any
    level: int = 0
    column: int = 0
    end_id: str = "\0\0\0\0"

    def key(self, extended: bool) -> tuple:
        if extended:
            return (self.field_id, self.level, self.column)
        return (self.field_id,)


@dataclass
class ModificationSet:
    """One Reforged object-data modification set.

    Format v3 added a sets_count + set_flag layer around each object's
    modifications. set_flag=0 is the normal/default set used by the Object
    Editor. Other flags can target alternate asset modes. We preserve every
    set byte-for-byte on parse/write, while the GUI edits only the default set.
    """

    flag: int = 0
    modifications: list[Modification] = field(default_factory=list)


@dataclass
class ObjectRecord:
    original_id: str
    custom_id: str
    modification_sets: list[ModificationSet] = field(default_factory=list)

    def default_set(self, create: bool = True) -> ModificationSet | None:
        for mod_set in self.modification_sets:
            if mod_set.flag == 0:
                return mod_set
        if not self.modification_sets:
            if not create:
                return None
            mod_set = ModificationSet(0, [])
            self.modification_sets.append(mod_set)
            return mod_set
        if create:
            # Reforged maps normally contain flag 0. If a third-party map only
            # has alternate sets, add a default one without destroying them.
            mod_set = ModificationSet(0, [])
            self.modification_sets.insert(0, mod_set)
            return mod_set
        return self.modification_sets[0]

    @property
    def modifications(self) -> list[Modification]:
        mod_set = self.default_set(create=True)
        assert mod_set is not None
        return mod_set.modifications

    @modifications.setter
    def modifications(self, value: list[Modification]) -> None:
        mod_set = self.default_set(create=True)
        assert mod_set is not None
        mod_set.modifications = list(value)


@dataclass
class ObjectModFile:
    version: int = 2
    original: list[ObjectRecord] = field(default_factory=list)
    custom: list[ObjectRecord] = field(default_factory=list)
    kind: str = "unit"

    @property
    def extended(self) -> bool:
        return self.kind in EXTENDED_KINDS

    @classmethod
    def empty(cls, kind: str, version: int = 2) -> "ObjectModFile":
        if version not in SUPPORTED_VERSIONS:
            raise ValueError(f"versão object-data não suportada: {version}")
        return cls(version=version, kind=kind)

    @classmethod
    def from_bytes(cls, data: bytes, kind: str) -> "ObjectModFile":
        bio = BytesIO(data)
        if len(data) < 4:
            raise FormatError("arquivo object-data pequeno demais")
        version = _read_u32(bio)
        if version not in SUPPORTED_VERSIONS:
            raise FormatError(f"versão object-data não suportada: {version}")

        obj = cls(version=version, kind=kind)
        obj.original = cls._read_table(bio, kind, version)

        # Real WC3 files normally carry both original and custom tables. A few
        # third-party tools omit the second empty table, so tolerate EOF here.
        remaining = len(data) - bio.tell()
        if remaining >= 4:
            obj.custom = cls._read_table(bio, kind, version)
        elif remaining == 0:
            obj.custom = []
        else:
            raise FormatError("arquivo object-data truncado após tabela original")

        trailing = bio.read()
        if trailing and any(b != 0 for b in trailing):
            raise FormatError(f"bytes extras inesperados no fim do object-data: {len(trailing)}")
        return obj

    @staticmethod
    def _read_table(bio: BytesIO, kind: str, version: int) -> list[ObjectRecord]:
        count = _validate_count(_read_u32(bio), "contagem de objetos")
        records: list[ObjectRecord] = []
        extended = kind in EXTENDED_KINDS

        for _ in range(count):
            old = _read_exact(bio, 4).decode("latin1")
            new = _read_exact(bio, 4).decode("latin1")

            sets_count = _read_u32(bio) if version >= 3 else 1
            _validate_count(sets_count, "contagem de conjuntos de modificações")
            mod_sets: list[ModificationSet] = []

            for _ in range(sets_count):
                set_flag = _read_u32(bio) if version >= 3 else 0
                mod_count = _validate_count(_read_u32(bio), "contagem de modificações")
                mods: list[Modification] = []

                for _ in range(mod_count):
                    field_id = _read_exact(bio, 4).decode("latin1")
                    value_type = _read_u32(bio)
                    level = column = 0
                    if extended:
                        level = _read_u32(bio)
                        column = _read_u32(bio)

                    if value_type == TYPE_INT:
                        value = _read_i32(bio)
                    elif value_type in (TYPE_REAL, TYPE_UNREAL):
                        value = _read_f32(bio)
                    elif value_type == TYPE_STRING:
                        value = _read_cstr(bio)
                    else:
                        raise FormatError(f"tipo de modificação desconhecido: {value_type}")

                    if version > 0:
                        end_id = _read_exact(bio, 4).decode("latin1")
                    else:
                        end_id = "\0\0\0\0"
                    mods.append(Modification(field_id, value_type, value, level, column, end_id))

                mod_sets.append(ModificationSet(set_flag, mods))

            records.append(ObjectRecord(old, new, mod_sets))
        return records

    def to_bytes(self) -> bytes:
        if self.version not in SUPPORTED_VERSIONS:
            raise FormatError(f"versão object-data não suportada: {self.version}")
        out = bytearray(_write_u32(self.version))
        out += self._write_table(self.original)
        out += self._write_table(self.custom)
        return bytes(out)

    def _write_table(self, records: Iterable[ObjectRecord]) -> bytes:
        records = list(records)
        out = bytearray(_write_u32(len(records)))

        for rec in records:
            out += _rawcode(rec.original_id)
            out += _rawcode(rec.custom_id)

            mod_sets = rec.modification_sets
            if self.version >= 3:
                out += _write_u32(len(mod_sets))
                sets_to_write = mod_sets
            else:
                if len(mod_sets) > 1:
                    raise FormatError(
                        "object-data v0/v1/v2 não pode armazenar múltiplos modification sets"
                    )
                # Old versions always imply exactly one set. Preserve an empty
                # record by emitting an empty implicit set when needed.
                sets_to_write = mod_sets or [ModificationSet(0, [])]

            for mod_set in sets_to_write:
                if self.version >= 3:
                    out += _write_u32(mod_set.flag)
                out += _write_u32(len(mod_set.modifications))

                for mod in mod_set.modifications:
                    out += _rawcode(mod.field_id)
                    out += _write_u32(mod.value_type)
                    if self.extended:
                        out += _write_u32(mod.level)
                        out += _write_u32(mod.column)

                    if mod.value_type == TYPE_INT:
                        out += _write_i32(mod.value)
                    elif mod.value_type in (TYPE_REAL, TYPE_UNREAL):
                        out += _write_f32(mod.value)
                    elif mod.value_type == TYPE_STRING:
                        out += _write_cstr(str(mod.value))
                    else:
                        raise FormatError(f"tipo de modificação desconhecido: {mod.value_type}")

                    if self.version > 0:
                        end = mod.end_id
                        if end == "\0\0\0\0":
                            out += b"\0\0\0\0"
                        else:
                            out += _rawcode(end)
        return bytes(out)

    def find_record(self, object_id: str) -> tuple[str, ObjectRecord] | None:
        """Return (table_name, record) for a custom or original object id."""
        for rec in self.custom:
            if rec.custom_id == object_id:
                return ("custom", rec)
        for rec in self.original:
            if rec.original_id == object_id:
                return ("original", rec)
        return None

    def _new_record(self, original_id: str, custom_id: str, modifications: list[Modification]) -> ObjectRecord:
        return ObjectRecord(original_id, custom_id, [ModificationSet(0, list(modifications))])

    def upsert_original(self, object_id: str, modifications: list[Modification]) -> ObjectRecord:
        """Edit an existing base object through the original-table override section."""
        _rawcode(object_id)
        existing = next((r for r in self.original if r.original_id == object_id), None)
        if existing is None:
            existing = self._new_record(object_id, "\0\0\0\0", [])
            self.original.append(existing)
        by_key = {m.key(self.extended): m for m in existing.modifications}
        for m in modifications:
            m.end_id = "\0\0\0\0"
            by_key[m.key(self.extended)] = m
        existing.modifications = list(by_key.values())
        return existing

    def replace_record_modifications(
        self,
        table: str,
        object_id: str,
        modifications: list[Modification],
        base_id: str | None = None,
    ) -> ObjectRecord:
        """Replace only the default (set_flag=0) modifications for one object.

        Reforged v3 may contain additional asset-mode sets. They are preserved
        exactly and are never flattened/dropped by normal GUI edits.
        """
        if table == "custom":
            existing = next((r for r in self.custom if r.custom_id == object_id), None)
            if existing is None:
                if not base_id:
                    raise ValueError("base_id é obrigatório para novo objeto custom")
                existing = self._new_record(base_id, object_id, [])
                self.custom.append(existing)
            elif base_id:
                existing.original_id = base_id
            for m in modifications:
                m.end_id = object_id
            existing.modifications = list(modifications)
            return existing

        if table == "original":
            existing = next((r for r in self.original if r.original_id == object_id), None)
            if existing is None:
                existing = self._new_record(object_id, "\0\0\0\0", [])
                self.original.append(existing)
            for m in modifications:
                m.end_id = "\0\0\0\0"
            existing.modifications = list(modifications)
            return existing

        raise ValueError(f"tabela inválida: {table}")

    def delete_record(self, table: str, object_id: str) -> bool:
        records = self.custom if table == "custom" else self.original if table == "original" else None
        if records is None:
            raise ValueError(f"tabela inválida: {table}")
        before = len(records)
        if table == "custom":
            records[:] = [r for r in records if r.custom_id != object_id]
        else:
            records[:] = [r for r in records if r.original_id != object_id]
        return len(records) != before

    def upsert_custom(self, base_id: str, custom_id: str, modifications: list[Modification]) -> ObjectRecord:
        _rawcode(base_id)
        _rawcode(custom_id)
        existing = next((r for r in self.custom if r.custom_id == custom_id), None)
        if existing is None:
            existing = self._new_record(base_id, custom_id, [])
            self.custom.append(existing)
        else:
            existing.original_id = base_id
        by_key = {m.key(self.extended): m for m in existing.modifications}
        for m in modifications:
            m.end_id = custom_id
            by_key[m.key(self.extended)] = m
        existing.modifications = list(by_key.values())
        return existing
