from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .casc import CascStorage

LOGGER = logging.getLogger("wc3studio")

_TOKEN_RE = re.compile(r"^\$?(WESTRING_[A-Za-z0-9_]+)$", re.IGNORECASE)


def parse_worldedit_strings(text: str) -> dict[str, str]:
    """Parse Blizzard WorldEditStrings.txt into a case-insensitive token map.

    The file is an INI-like ``[WorldEditStrings]`` table.  Reforged locale
    packs occasionally leave values blank; blank values are intentionally
    ignored so an enUS baseline can be overlaid by the user's locale safely.
    """
    out: dict[str, str] = {}
    active = False
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith(";"):
            continue
        if line.startswith("[") and "]" in line:
            active = line[1:line.index("]")].strip().casefold() == "worldeditstrings"
            continue
        if not active or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        value = value.replace(r"\n", " ").replace("|n", " ")
        value = " ".join(value.split())
        if key and value:
            out[key.casefold()] = value
    return out


@dataclass
class EditorStringCatalog:
    values: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "EditorStringCatalog":
        return cls()

    def __len__(self) -> int:
        return len(self.values)

    def resolve(self, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return text
        current = text
        seen: set[str] = set()
        # Some Blizzard tables use $WESTRING_FOO while others use WESTRING_FOO.
        # Values may also alias another WESTRING, so follow a short chain.
        for _ in range(12):
            match = _TOKEN_RE.match(current.strip())
            if not match:
                break
            key = match.group(1).casefold()
            if key in seen:
                break
            seen.add(key)
            replacement = self.values.get(key)
            if not replacement:
                break
            current = replacement.strip()
        return current


def _locale_refs(locale: str) -> tuple[str, ...]:
    locale = locale.casefold()
    return (
        rf"war3.w3mod:_locales\{locale}.w3mod:ui\WorldEditStrings.txt",
        rf"war3.w3mod:_locales\{locale}.w3mod:UI\WorldEditStrings.txt",
        rf"war3.w3mod:_locales\{locale}.w3mod:ui/WorldEditStrings.txt",
        rf"war3.w3mod:_locales\{locale}.w3mod:UI/WorldEditStrings.txt",
        rf"{locale}.w3mod:ui\WorldEditStrings.txt",
    )


def load_editor_strings(casc: CascStorage) -> EditorStringCatalog:
    """Load World Editor localization from CASC.

    enUS is loaded first as a complete baseline; ptBR is then overlaid when the
    installed locale exposes translated values.  A neutral UI path is tried as
    a final compatibility source.  Missing sources are non-fatal.
    """
    merged: dict[str, str] = {}
    sources: list[str] = []

    def merge_first(refs: tuple[str, ...], label: str) -> None:
        try:
            actual, data = casc.read_first(refs)
        except Exception:
            LOGGER.debug("WorldEditStrings source unavailable: %s", label)
            return
        try:
            parsed = parse_worldedit_strings(data.decode("utf-8-sig", "replace"))
        except Exception:
            LOGGER.warning("Failed parsing WorldEditStrings: %s", actual, exc_info=True)
            return
        if parsed:
            merged.update(parsed)
            sources.append(actual)
            LOGGER.info("WorldEditStrings loaded: %s entries=%d", actual, len(parsed))

    # Neutral/base first, then English fallback, then the user's PT-BR locale.
    merge_first((
        r"war3.w3mod:ui\WorldEditStrings.txt",
        r"war3.w3mod:UI\WorldEditStrings.txt",
        r"UI\WorldEditStrings.txt",
        r"ui\WorldEditStrings.txt",
    ), "base")
    merge_first(_locale_refs("enus"), "enUS")
    merge_first(_locale_refs("ptbr"), "ptBR")

    return EditorStringCatalog(merged, sources)
