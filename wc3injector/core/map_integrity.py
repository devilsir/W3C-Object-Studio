from __future__ import annotations

import hashlib

from ..debuglog import configure_logging
from ..errors import ValidationError
from ..objectmod import ObjectModFile
from ..storm import StormArchive

LOGGER, _SESSION_LOG = configure_logging()

OBJECT_FILES: dict[str, tuple[str, str]] = {
    "unit": ("war3map.w3u", "unit"),
    "ability": ("war3map.w3a", "ability"),
    "doodad": ("war3map.w3d", "doodad"),
}

PROTECTED_MAP_FILES = [
    "war3map.j", "war3map.lua", "war3map.wtg", "war3map.wct", "war3map.wts",
    "war3map.w3i", "war3map.w3e", "war3map.doo", "war3map.wpm",
    "war3map.w3b", "war3map.w3h", "war3map.w3q", "war3map.w3t",
    "war3map.w3r", "war3map.w3c", "war3map.w3s",
    # UI/game-interface skin text and unsupported object categories stay protected.
    # The editable Reforged object skin tables (w3u/w3a/w3d) are intentionally
    # excluded here because they are the runtime half of unit/ability/doodad art
    # data and must be kept in sync with edits made by this Studio.
    "war3mapSkin.txt",
    "war3mapSkin.w3t", "war3mapSkin.w3b", "war3mapSkin.w3h", "war3mapSkin.w3q", "war3mapSkin.w3r",
]

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def snapshot_protected_files(archive: StormArchive) -> dict[str, str | None]:
    LOGGER.debug("Protected snapshot start (%d paths)", len(PROTECTED_MAP_FILES))
    result: dict[str, str | None] = {}
    for path in PROTECTED_MAP_FILES:
        if not archive.has(path):
            result[path] = None
            continue
        try:
            result[path] = sha256_bytes(archive.read(path))
        except Exception as exc:
            raise ValidationError(f"não foi possível validar arquivo protegido {path}: {exc}") from exc
    LOGGER.debug(
        "Protected snapshot done: present=%d missing=%d",
        sum(v is not None for v in result.values()),
        sum(v is None for v in result.values()),
    )
    return result

def snapshot_editable_files(archive: StormArchive) -> dict[str, str | None]:
    # Reforged splits Object Editor data between war3map.w3* (gameplay/static)
    # and war3mapSkin.w3* (art/text/localizable). Track both halves so external
    # World Editor changes are detected before a transaction commits.
    paths = ["war3map.imp", "war3mapUnits.doo"] + [value[0] for value in OBJECT_FILES.values()] + [
        "war3mapSkin.w3u", "war3mapSkin.w3a", "war3mapSkin.w3d"
    ]
    result = {
        path: (sha256_bytes(archive.read(path)) if archive.has(path) else None)
        for path in dict.fromkeys(paths)
    }
    LOGGER.debug(
        "Editable snapshot: %s",
        {key: (value[:12] + "…" if value else None) for key, value in result.items()},
    )
    return result

def load_object_table(archive: StormArchive, kind: str) -> ObjectModFile:
    path, codec_kind = OBJECT_FILES[kind]
    LOGGER.info("Loading object-data kind=%s path=%s", kind, path)
    if archive.has(path):
        raw = archive.read(path)
        LOGGER.info("Parsing %s (%d bytes)", path, len(raw))
        obj = ObjectModFile.from_bytes(raw, codec_kind)
        LOGGER.info(
            "Parsed %s: original=%d custom=%d version=%s",
            path, len(obj.original), len(obj.custom), getattr(obj, "version", "?"),
        )
        return obj
    LOGGER.info("%s not present -> using empty %s object table", path, codec_kind)
    return ObjectModFile.empty(codec_kind)

# Compatibility names used by the existing session implementation.
EDITOR_PROTECTED = PROTECTED_MAP_FILES
_sha = sha256_bytes
_snapshot = snapshot_protected_files
_snapshot_editable = snapshot_editable_files
_load_object = load_object_table
