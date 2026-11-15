from __future__ import annotations
from dataclasses import dataclass, field
from io import BytesIO
import struct
from .errors import FormatError

CUSTOM_PATH_FLAGS = {10, 13}
STANDARD_PATH_FLAGS = {5, 8}
DEFAULT_CUSTOM_FLAG = 13


def _read_i32(bio: BytesIO) -> int:
    data = bio.read(4)
    if len(data) != 4:
        raise FormatError("war3map.imp truncado")
    return struct.unpack("<i", data)[0]


def _read_cstr(bio: BytesIO) -> str:
    out = bytearray()
    while True:
        b = bio.read(1)
        if not b:
            raise FormatError("string truncada em war3map.imp")
        if b == b"\0":
            return out.decode("utf-8", errors="surrogateescape")
        out += b


@dataclass
class ImportEntry:
    flag: int
    path: str

    @property
    def archive_path(self) -> str:
        if self.flag in STANDARD_PATH_FLAGS and "\\" not in self.path and "/" not in self.path:
            return "war3mapImported\\" + self.path
        return self.path.replace("/", "\\")


@dataclass
class ImportFile:
    version: int = 1
    entries: list[ImportEntry] = field(default_factory=list)

    @classmethod
    def from_bytes(cls, data: bytes) -> "ImportFile":
        bio = BytesIO(data)
        version = _read_i32(bio)
        if version != 1:
            raise FormatError(f"versão war3map.imp não suportada: {version}")
        count = _read_i32(bio)
        if count < 0 or count > 1_000_000:
            raise FormatError(f"contagem de imports inválida: {count}")
        entries = []
        for _ in range(count):
            flag_b = bio.read(1)
            if len(flag_b) != 1:
                raise FormatError("war3map.imp truncado")
            entries.append(ImportEntry(flag_b[0], _read_cstr(bio)))
        return cls(version, entries)

    @classmethod
    def empty(cls) -> "ImportFile":
        return cls(1, [])

    def add_custom(self, archive_path: str) -> None:
        archive_path = archive_path.replace("/", "\\")
        lower = archive_path.casefold()
        for ent in self.entries:
            if ent.archive_path.casefold() == lower:
                ent.flag = DEFAULT_CUSTOM_FLAG
                ent.path = archive_path
                return
        self.entries.append(ImportEntry(DEFAULT_CUSTOM_FLAG, archive_path))

    def to_bytes(self) -> bytes:
        out = bytearray(struct.pack("<ii", self.version, len(self.entries)))
        for ent in self.entries:
            out.append(ent.flag & 0xFF)
            out += ent.path.encode("utf-8", errors="surrogateescape") + b"\0"
        return bytes(out)
