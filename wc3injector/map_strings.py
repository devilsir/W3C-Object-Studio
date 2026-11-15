from __future__ import annotations

import re
from dataclasses import dataclass, field

_TRIGSTR_RE = re.compile(r"^TRIGSTR_(\d+)$", re.IGNORECASE)


@dataclass
class MapStringTable:
    """Parsed ``war3map.wts`` string table.

    Warcraft object skin files frequently keep ``TRIGSTR_xxx`` references
    instead of literal text.  Keeping this parser separate from object-data
    makes names/tooltips reusable by the browser without touching JASS/triggers.
    """

    strings: dict[int, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "MapStringTable":
        return cls({})

    @classmethod
    def from_bytes(cls, data: bytes) -> "MapStringTable":
        text = data.decode("utf-8-sig", "replace")
        lines = text.splitlines()
        out: dict[int, str] = {}
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            m = re.match(r"^STRING\s+(\d+)\b", line, re.IGNORECASE)
            if not m:
                i += 1
                continue
            key = int(m.group(1))
            i += 1
            while i < len(lines) and lines[i].strip() != "{":
                i += 1
            if i >= len(lines):
                break
            i += 1
            body: list[str] = []
            while i < len(lines) and lines[i].strip() != "}":
                body.append(lines[i])
                i += 1
            out[key] = "\n".join(body)
            if i < len(lines):
                i += 1
        return cls(out)

    def resolve(self, value: object) -> str:
        raw = str(value if value is not None else "")
        match = _TRIGSTR_RE.match(raw.strip())
        if not match:
            return raw
        return self.strings.get(int(match.group(1)), raw)

    def __len__(self) -> int:
        return len(self.strings)
