from __future__ import annotations

from dataclasses import dataclass, field
import io
import struct
from typing import Iterable

from .errors import ValidationError


_FILE_ID = b"W3do"


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def need(self, n: int) -> None:
        if n < 0 or self.pos + n > len(self.data):
            raise ValidationError(
                f"war3mapUnits.doo truncado em 0x{self.pos:X}: precisa de {n} bytes, restam {len(self.data)-self.pos}"
            )

    def read(self, n: int) -> bytes:
        self.need(n)
        out = self.data[self.pos:self.pos+n]
        self.pos += n
        return out

    def i32(self) -> int:
        self.need(4)
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u32(self) -> int:
        self.need(4)
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u16(self) -> int:
        self.need(2)
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def u8(self) -> int:
        self.need(1)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def f32(self) -> float:
        self.need(4)
        value = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return value

    def rawcode(self) -> str:
        return self.read(4).decode("latin1")


class _Writer:
    def __init__(self):
        self.buf = bytearray()

    def raw(self, b: bytes) -> None:
        self.buf.extend(b)

    def i32(self, value: int) -> None:
        self.buf.extend(struct.pack("<i", int(value)))

    def u32(self, value: int) -> None:
        self.buf.extend(struct.pack("<I", int(value) & 0xFFFFFFFF))

    def u16(self, value: int) -> None:
        self.buf.extend(struct.pack("<H", int(value) & 0xFFFF))

    def u8(self, value: int) -> None:
        self.buf.extend(struct.pack("<B", int(value) & 0xFF))

    def f32(self, value: float) -> None:
        self.buf.extend(struct.pack("<f", float(value)))

    def rawcode(self, value: str) -> None:
        raw = str(value).encode("latin1", errors="strict")
        if len(raw) != 4:
            raise ValidationError(f"rawcode de placement precisa ter 4 bytes: {value!r}")
        self.buf.extend(raw)

    def bytes(self) -> bytes:
        return bytes(self.buf)


@dataclass
class DroppedItem:
    item_id: str = "\0\0\0\0"
    chance: int = 100

    @classmethod
    def read_from(cls, r: _Reader) -> "DroppedItem":
        return cls(r.rawcode(), r.i32())

    def write_to(self, w: _Writer) -> None:
        w.rawcode(self.item_id)
        w.i32(self.chance)


@dataclass
class DroppedItemSet:
    items: list[DroppedItem] = field(default_factory=list)
    _count_raw: int = field(default=0, repr=False, compare=False)

    @classmethod
    def read_from(cls, r: _Reader) -> "DroppedItemSet":
        count_raw = r.i32()
        count = _decode_list_count(count_raw, "itens no drop set")
        return cls([DroppedItem.read_from(r) for _ in range(count)], count_raw)

    def write_to(self, w: _Writer) -> None:
        _write_list_count(w, len(self.items), self._count_raw)
        for item in self.items:
            item.write_to(w)


@dataclass
class InventoryItem:
    slot: int = 0
    item_id: str = "\0\0\0\0"

    @classmethod
    def read_from(cls, r: _Reader) -> "InventoryItem":
        return cls(r.i32(), r.rawcode())

    def write_to(self, w: _Writer) -> None:
        w.i32(self.slot)
        w.rawcode(self.item_id)


@dataclass
class ModifiedAbility:
    ability_id: str = "\0\0\0\0"
    autocast: int = 0
    level: int = 1

    @classmethod
    def read_from(cls, r: _Reader) -> "ModifiedAbility":
        return cls(r.rawcode(), r.i32(), r.i32())

    def write_to(self, w: _Writer) -> None:
        w.rawcode(self.ability_id)
        w.i32(self.autocast)
        w.i32(self.level)


@dataclass
class RandomUnitChoice:
    unit_id: str = "\0\0\0\0"
    chance: int = 100

    @classmethod
    def read_from(cls, r: _Reader) -> "RandomUnitChoice":
        return cls(r.rawcode(), r.i32())

    def write_to(self, w: _Writer) -> None:
        w.rawcode(self.unit_id)
        w.i32(self.chance)


@dataclass
class UnitPlacement:
    unit_id: str = "hfoo"
    variation: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    angle: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    scale_z: float = 1.0
    skin_id: str | None = None
    flags: int = 0
    player: int = 0
    unknown: int = 0
    hitpoints: int = -1
    mana: int = -1
    dropped_item_table: int = -1
    dropped_item_sets: list[DroppedItemSet] = field(default_factory=list)
    gold_amount: int = 12500
    target_acquisition: float = -1.0
    hero_level: int = 1
    hero_strength: int = 0
    hero_agility: int = 0
    hero_intelligence: int = 0
    inventory: list[InventoryItem] = field(default_factory=list)
    modified_abilities: list[ModifiedAbility] = field(default_factory=list)
    random_flag: int = 0
    random_level: bytes = b"\x00\x00\x00"
    item_class: int = 0
    unit_group: int = 0
    position_in_group: int = 0
    random_units: list[RandomUnitChoice] = field(default_factory=list)
    _drop_set_count_raw: int = field(default=0, repr=False, compare=False)
    _inventory_count_raw: int = field(default=0, repr=False, compare=False)
    _ability_count_raw: int = field(default=0, repr=False, compare=False)
    _random_units_count_raw: int = field(default=0, repr=False, compare=False)
    # Placement format v13 (Warcraft III patch 3.0+) added 16 opaque bytes
    # around the legacy payload: one i32 immediately after skin_id and 12
    # bytes after creation_number. Their semantics are not documented yet,
    # so preserve them verbatim while continuing to edit the known fields.
    _v13_pre_flags: int = field(default=-1, repr=False, compare=False)
    _v13_tail: bytes = field(default=b"\x00" * 12, repr=False, compare=False)
    custom_team_color: int = -1
    waygate: int = -1
    creation_number: int = 0

    @property
    def is_hero(self) -> bool:
        if not self.unit_id:
            return False
        if self.unit_id.startswith("YY"):
            return False
        c = self.unit_id[0]
        return "A" <= c <= "Z"

    @classmethod
    def read_from(cls, r: _Reader, *, version: int, subversion: int, has_skin: bool) -> "UnitPlacement":
        u = cls()
        u.unit_id = r.rawcode()
        u.variation = r.i32()
        u.x, u.y, u.z = r.f32(), r.f32(), r.f32()
        u.angle = r.f32()
        u.scale_x, u.scale_y, u.scale_z = r.f32(), r.f32(), r.f32()
        u.skin_id = r.rawcode() if has_skin else None
        if version >= 13:
            u._v13_pre_flags = r.i32()
        u.flags = r.u8()
        u.player = r.i32()
        u.unknown = r.u16()
        u.hitpoints = r.i32()
        u.mana = r.i32()
        if subversion > 10:
            u.dropped_item_table = r.i32()
        else:
            u.dropped_item_table = -1
        set_count_raw = r.i32()
        set_count = _decode_list_count(set_count_raw, "drop sets")
        u._drop_set_count_raw = set_count_raw
        u.dropped_item_sets = [DroppedItemSet.read_from(r) for _ in range(set_count)]
        u.gold_amount = r.i32()
        u.target_acquisition = r.f32()
        u.hero_level = r.i32()
        if subversion > 10:
            u.hero_strength = r.i32()
            u.hero_agility = r.i32()
            u.hero_intelligence = r.i32()
        else:
            u.hero_strength = u.hero_agility = u.hero_intelligence = 0
        inv_count_raw = r.i32()
        inv_count = _decode_list_count(inv_count_raw, "itens no inventário")
        u._inventory_count_raw = inv_count_raw
        u.inventory = [InventoryItem.read_from(r) for _ in range(inv_count)]
        ability_count_raw = r.i32()
        ability_count = _decode_list_count(ability_count_raw, "habilidades modificadas")
        u._ability_count_raw = ability_count_raw
        u.modified_abilities = [ModifiedAbility.read_from(r) for _ in range(ability_count)]
        u.random_flag = r.i32()
        if u.random_flag == 0:
            u.random_level = r.read(3)
            u.item_class = r.u8()
        elif u.random_flag == 1:
            u.unit_group = r.u32()
            u.position_in_group = r.u32()
        elif u.random_flag == 2:
            count_raw = r.i32()
            count = _decode_list_count(count_raw, "unidades aleatórias")
            u._random_units_count_raw = count_raw
            u.random_units = [RandomUnitChoice.read_from(r) for _ in range(count)]
        else:
            # Some third-party map writers preserve -1/other sentinel values here.
            # Warcraft's readers treat unknown flags as having no extra random payload.
            # Keep the raw value byte-stable instead of rejecting the whole placement file.
            pass
        u.custom_team_color = r.i32()
        u.waygate = r.i32()
        u.creation_number = r.i32()
        if version >= 13:
            u._v13_tail = r.read(12)
        return u

    def write_to(self, w: _Writer, *, version: int, subversion: int, has_skin: bool) -> None:
        w.rawcode(self.unit_id)
        w.i32(self.variation)
        w.f32(self.x); w.f32(self.y); w.f32(self.z)
        w.f32(self.angle)
        w.f32(self.scale_x); w.f32(self.scale_y); w.f32(self.scale_z)
        if has_skin:
            w.rawcode(self.skin_id if self.skin_id is not None else self.unit_id)
        if version >= 13:
            w.i32(self._v13_pre_flags)
        w.u8(self.flags)
        w.i32(self.player)
        w.u16(self.unknown)
        w.i32(self.hitpoints)
        w.i32(self.mana)
        if subversion > 10:
            w.i32(self.dropped_item_table)
        _write_list_count(w, len(self.dropped_item_sets), self._drop_set_count_raw)
        for s in self.dropped_item_sets:
            s.write_to(w)
        w.i32(self.gold_amount)
        w.f32(self.target_acquisition)
        w.i32(self.hero_level)
        if subversion > 10:
            w.i32(self.hero_strength)
            w.i32(self.hero_agility)
            w.i32(self.hero_intelligence)
        _write_list_count(w, len(self.inventory), self._inventory_count_raw)
        for item in self.inventory:
            item.write_to(w)
        _write_list_count(w, len(self.modified_abilities), self._ability_count_raw)
        for ability in self.modified_abilities:
            ability.write_to(w)
        w.i32(self.random_flag)
        if self.random_flag == 0:
            raw = bytes(self.random_level or b"\x00\x00\x00")
            if len(raw) != 3:
                raise ValidationError("random_level precisa ter exatamente 3 bytes")
            w.raw(raw)
            w.u8(self.item_class)
        elif self.random_flag == 1:
            w.u32(self.unit_group)
            w.u32(self.position_in_group)
        elif self.random_flag == 2:
            _write_list_count(w, len(self.random_units), self._random_units_count_raw)
            for ru in self.random_units:
                ru.write_to(w)
        else:
            # Preserve non-standard sentinel values exactly; they carry no payload.
            pass
        w.i32(self.custom_team_color)
        w.i32(self.waygate)
        w.i32(self.creation_number)
        if version >= 13:
            tail = bytes(self._v13_tail or b"\x00" * 12)
            if len(tail) != 12:
                raise ValidationError("payload extra v13 de placement precisa ter exatamente 12 bytes")
            w.raw(tail)


@dataclass
class UnitPlacementFile:
    version: int = 8
    subversion: int = 11
    units: list[UnitPlacement] = field(default_factory=list)
    has_skin: bool = True

    @classmethod
    def empty(cls) -> "UnitPlacementFile":
        return cls()

    @classmethod
    def from_bytes(cls, data: bytes, *, has_skin: bool | None = None) -> "UnitPlacementFile":
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("war3mapUnits.doo precisa ser bytes")
        raw = bytes(data)
        if len(raw) < 16:
            raise ValidationError("war3mapUnits.doo muito pequeno")
        if has_skin is not None:
            return cls._parse(raw, bool(has_skin))

        successes: list[UnitPlacementFile] = []
        errors: list[str] = []
        # Reforged (1.32+) inserted skinId without changing the file version.
        # Try both structural layouts and only accept parses that consume the
        # entire buffer. In real maps one of the two quickly becomes impossible
        # because the 4-byte shift corrupts the following counts/flags.
        for candidate in (True, False):
            try:
                parsed = cls._parse(raw, candidate)
                successes.append(parsed)
            except Exception as exc:
                errors.append(f"skin={candidate}: {exc}")
        if not successes:
            raise ValidationError("não foi possível interpretar war3mapUnits.doo; " + " | ".join(errors))
        if len(successes) == 1:
            return successes[0]

        # Extremely small/synthetic files can be ambiguous. Prefer the Reforged
        # layout only when its skin IDs look like rawcodes instead of arbitrary
        # numeric placement bytes; otherwise keep the legacy layout.
        reforged = next(x for x in successes if x.has_skin)
        legacy = next(x for x in successes if not x.has_skin)
        if all(_plausible_rawcode(u.skin_id or "") for u in reforged.units):
            return reforged
        return legacy

    @classmethod
    def _parse(cls, data: bytes, has_skin: bool) -> "UnitPlacementFile":
        r = _Reader(data)
        if r.read(4) != _FILE_ID:
            raise ValidationError("war3mapUnits.doo sem cabeçalho W3do")
        version = r.i32()
        subversion = r.u32()
        count = r.i32()
        _validate_count(count, "placements", max_count=1_000_000)
        units = [UnitPlacement.read_from(r, version=version, subversion=subversion, has_skin=has_skin) for _ in range(count)]
        if r.pos != len(data):
            raise ValidationError(f"{len(data)-r.pos} bytes sobraram após {count} placements")
        result = cls(version, subversion, units, has_skin)
        # A successful parse must be byte-stable before we trust it for editing.
        if result.to_bytes() != data:
            raise ValidationError("round-trip do war3mapUnits.doo divergiu")
        return result

    def to_bytes(self) -> bytes:
        w = _Writer()
        w.raw(_FILE_ID)
        w.i32(self.version)
        w.u32(self.subversion)
        w.i32(len(self.units))
        for unit in self.units:
            unit.write_to(w, version=self.version, subversion=self.subversion, has_skin=self.has_skin)
        return w.bytes()

    def heroes(self) -> list[UnitPlacement]:
        return [u for u in self.units if u.is_hero]

    def by_creation_number(self, creation_number: int) -> UnitPlacement | None:
        for unit in self.units:
            if unit.creation_number == creation_number:
                return unit
        return None



def _decode_list_count(value: int, what: str, *, max_count: int = 100_000) -> int:
    """Decode a WC3 variable-list count while preserving common empty sentinel -1.

    Blizzard's own data normally uses 0 for an empty list, but a number of
    World Editor-compatible tools emit -1. Native/JS readers effectively loop
    zero times for a negative count. Accept -1 as empty and preserve the raw
    encoding on write so untouched maps remain byte-stable.
    """
    if value == -1:
        return 0
    if value < -1 or value > max_count:
        raise ValidationError(f"war3mapUnits.doo: contagem inválida de {what}: {value}")
    return value


def _write_list_count(w: _Writer, length: int, raw_count: int) -> None:
    if length == 0 and raw_count == -1:
        w.i32(-1)
    else:
        w.i32(length)


def _validate_count(value: int, what: str, *, max_count: int = 100_000) -> None:
    if value < 0 or value > max_count:
        raise ValidationError(f"war3mapUnits.doo: contagem inválida de {what}: {value}")


def _plausible_rawcode(value: str) -> bool:
    if len(value) != 4:
        return False
    raw = value.encode("latin1", errors="ignore")
    # \0\0\0\0 is a legal 'no skin' style value in some tools.
    if raw == b"\0\0\0\0":
        return True
    return all(32 <= b <= 126 for b in raw)


PLAYER_LABELS_PT: dict[int, str] = {
    0: "Jogador 1 (Vermelho)", 1: "Jogador 2 (Azul)", 2: "Jogador 3 (Ciano)", 3: "Jogador 4 (Roxo)",
    4: "Jogador 5 (Amarelo)", 5: "Jogador 6 (Laranja)", 6: "Jogador 7 (Verde)", 7: "Jogador 8 (Rosa)",
    8: "Jogador 9 (Cinza)", 9: "Jogador 10 (Azul-claro)", 10: "Jogador 11 (Verde-escuro)", 11: "Jogador 12 (Marrom)",
    # Reforged/24-player maps keep four reserved neutral owner slots after
    # the 24 playable players: Aggressive, Victim, Extra, Passive.  Do not
    # swap Victim/Passive here: the raw owner id is persisted in Units.doo.
    24: "Neutro Hostil", 25: "Neutro Vítima", 26: "Neutro Extra", 27: "Neutro Passivo",
}
PLAYER_LABELS_EN: dict[int, str] = {
    0: "Player 1 (Red)", 1: "Player 2 (Blue)", 2: "Player 3 (Teal)", 3: "Player 4 (Purple)",
    4: "Player 5 (Yellow)", 5: "Player 6 (Orange)", 6: "Player 7 (Green)", 7: "Player 8 (Pink)",
    8: "Player 9 (Gray)", 9: "Player 10 (Light Blue)", 10: "Player 11 (Dark Green)", 11: "Player 12 (Brown)",
    24: "Neutral Hostile", 25: "Neutral Victim", 26: "Neutral Extra", 27: "Neutral Passive",
}


def player_label(player: int, lang: str = "pt") -> str:
    labels = PLAYER_LABELS_EN if lang == "en" else PLAYER_LABELS_PT
    if player in labels:
        return labels[player]
    if 0 <= player <= 23:
        return ("Player" if lang == "en" else "Jogador") + f" {player + 1}"
    return ("Owner" if lang == "en" else "Dono") + f" {player}"


def player_choices(lang: str = "pt") -> list[tuple[str, int]]:
    ids = list(range(24)) + [24, 25, 26, 27]
    return [(player_label(i, lang), i) for i in ids]
