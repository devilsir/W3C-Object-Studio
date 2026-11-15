from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any

from wc3injector.errors import InjectorError
from wc3injector.fields import UNIT_FIELDS, DOODAD_FIELDS, NAME_TYPES
from wc3injector.injector import (
    AssetSpec,
    InjectionPlan,
    ObjectSpec,
    auto_assets,
    inject,
    report_to_json,
)
from wc3injector.objectmod import Modification


def _path(base: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def _value_for_type(value_type: int, value: Any):
    if value_type == NAME_TYPES["int"]:
        return int(value, 0) if isinstance(value, str) else int(value)
    if value_type in (NAME_TYPES["real"], NAME_TYPES["unreal"]):
        return float(value)
    return str(value)


def _named_mods(object_type: str, fields: dict[str, Any]) -> list[Modification]:
    defs = DOODAD_FIELDS if object_type == "doodad" else UNIT_FIELDS
    out: list[Modification] = []
    for name, raw in fields.items():
        if name not in defs:
            raise InjectorError(
                f"preset de campo desconhecido para {object_type}: {name}. "
                f"Use mods[] para field IDs arbitrários."
            )
        fd = defs[name]
        level = column = 0
        value = raw
        if isinstance(raw, dict):
            if "value" not in raw:
                raise InjectorError(f"campo {name}: objeto precisa de 'value'")
            value = raw["value"]
            level = int(raw.get("level", 0))
            column = int(raw.get("column", 0))
        out.append(Modification(fd.id, fd.type, _value_for_type(fd.type, value), level, column))
    return out


def _raw_mods(items: list[dict[str, Any]]) -> list[Modification]:
    out: list[Modification] = []
    for item in items:
        fid = str(item.get("field", ""))
        typ = str(item.get("type", ""))
        if len(fid) != 4:
            raise InjectorError(f"field id inválido em mods[]: {fid!r}")
        if typ not in NAME_TYPES:
            raise InjectorError(f"tipo inválido em mods[] para {fid}: {typ!r}")
        vt = NAME_TYPES[typ]
        out.append(
            Modification(
                fid,
                vt,
                _value_for_type(vt, item.get("value", "")),
                int(item.get("level", 0)),
                int(item.get("column", 0)),
            )
        )
    return out


def load_manifest(path: Path) -> tuple[InjectionPlan, str | None, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InjectorError("manifest precisa ser um objeto JSON")
    base = path.resolve().parent

    map_path = _path(base, data["map"])
    output_path = _path(base, data.get("output", map_path.with_name(map_path.stem + "_INJECTED" + map_path.suffix)))
    root = str(data.get("root", r"war3mapImported\Custom"))

    models = [_path(base, p) for p in data.get("models", [])]
    textures = [_path(base, p) for p in data.get("textures", [])]

    # Object-referenced models are automatically added to the asset scan.
    for obj in data.get("objects", []):
        mp = _path(base, obj["model"])
        if mp not in models:
            models.append(mp)

    assets, model_paths, warnings = auto_assets(models, textures, root)

    # Explicit archive-path mappings are useful for unusual models or ambiguous
    # texture basenames. validate_plan() will reject conflicting destinations.
    for item in data.get("extra_assets", []):
        assets.append(AssetSpec(_path(base, item["source"]), str(item["archive_path"])))

    objects: list[ObjectSpec] = []
    for item in data.get("objects", []):
        typ = str(item["type"])
        model_file = _path(base, item["model"])
        extra = _named_mods(typ, dict(item.get("fields", {})))
        extra += _raw_mods(list(item.get("mods", [])))
        objects.append(
            ObjectSpec(
                typ,
                str(item["base"]),
                str(item["id"]),
                str(item["name"]),
                model_paths[model_file],
                float(item.get("scale", 1.0)),
                extra,
            )
        )

    plan = InjectionPlan(
        map_path=map_path,
        output_path=output_path,
        objects=objects,
        assets=assets,
        compact=bool(data.get("compact", False)),
        make_backup=bool(data.get("make_backup", True)),
    )
    dll = data.get("stormlib_dll")
    if dll:
        dll = str(_path(base, dll))
    return plan, dll, warnings


def main():
    ap = argparse.ArgumentParser(description="Batch injector via JSON manifest")
    ap.add_argument("manifest", type=Path)
    args = ap.parse_args()
    plan, dll, warnings = load_manifest(args.manifest)
    report = inject(plan, dll_path=dll)
    report.warnings[:0] = warnings
    print(report_to_json(report))


if __name__ == "__main__":
    main()
