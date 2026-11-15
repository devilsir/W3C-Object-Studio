from __future__ import annotations
import argparse
from pathlib import Path

from wc3injector.errors import InjectorError
from wc3injector.fields import NAME_TYPES, UNIT_FIELDS, DOODAD_FIELDS
from wc3injector.injector import AssetSpec, ObjectSpec, InjectionPlan, auto_assets, inject, report_to_json
from wc3injector.objectmod import Modification


def parse_mod(text: str) -> Modification:
    # field:type:value[:level:column]
    parts = text.split(":")
    if len(parts) < 3:
        raise argparse.ArgumentTypeError("formato: field:type:value[:level:column]")
    field, typ = parts[0], parts[1]
    if len(field) != 4:
        raise argparse.ArgumentTypeError("field id deve ter 4 caracteres")
    if typ not in NAME_TYPES:
        raise argparse.ArgumentTypeError("tipo deve ser int|real|unreal|string")

    # For numeric values, optional level/column are unambiguous. For strings,
    # colon is considered part of the value unless two trailing ints exist.
    level = column = 0
    value_parts = parts[2:]
    if len(value_parts) >= 3:
        try:
            maybe_level = int(value_parts[-2])
            maybe_column = int(value_parts[-1])
            level, column = maybe_level, maybe_column
            value_parts = value_parts[:-2]
        except ValueError:
            pass
    value = ":".join(value_parts)
    t = NAME_TYPES[typ]
    if typ == "int":
        value2 = int(value, 0)
    elif typ in ("real", "unreal"):
        value2 = float(value)
    else:
        value2 = value
    return Modification(field, t, value2, level, column)


def parse_named_field(text: str, object_type: str) -> Modification:
    if "=" not in text:
        raise argparse.ArgumentTypeError("formato de --field: nome=valor")
    name, raw = text.split("=", 1)
    defs = DOODAD_FIELDS if object_type == "doodad" else UNIT_FIELDS
    if name not in defs:
        raise argparse.ArgumentTypeError(f"preset desconhecido: {name}")
    fd = defs[name]
    if fd.type == NAME_TYPES["int"]:
        val = int(raw, 0)
    elif fd.type in (NAME_TYPES["real"], NAME_TYPES["unreal"]):
        val = float(raw)
    else:
        val = raw
    return Modification(fd.id, fd.type, val, 0, 0)


def parse_explicit_asset(text: str) -> tuple[Path, str]:
    if "=" not in text:
        raise argparse.ArgumentTypeError("formato de --asset: arquivo_local=caminho\\no\\MPQ")
    src, archive = text.split("=", 1)
    return Path(src), archive


def main():
    p = argparse.ArgumentParser(description="WC3 Custom Unit/Hero/Doodad Injector")
    p.add_argument("map", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--type", choices=["unit", "hero", "doodad"], required=True)
    p.add_argument("--base", required=True, help="rawcode base, ex: hfoo / Hpal / LTlt")
    p.add_argument("--id", required=True, help="novo rawcode, ex: u001 / H001 / D001")
    p.add_argument("--name", required=True)
    p.add_argument("--model", type=Path, required=True, help="modelo principal do objeto")
    p.add_argument("--aux-model", type=Path, action="append", default=[], help="MDX/MDL auxiliar/portrait; pode repetir")
    p.add_argument("--texture", type=Path, action="append", default=[])
    p.add_argument("--asset", action="append", default=[], help=r"mapeamento explícito: local.blp=Textures\\X.blp")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--field", action="append", default=[], help="preset conhecido: hp=5000, move_speed=320, hero_strength=40")
    p.add_argument("--mod", action="append", default=[], help="field:type:value[:level:column]")
    p.add_argument("--root", default=r"war3mapImported\Custom")
    p.add_argument("--compact", action="store_true")
    p.add_argument("--no-backup", action="store_true", help="não gerar .bak quando output == map")
    p.add_argument("--dll", default=None)
    args = p.parse_args()

    models = [args.model] + list(args.aux_model)
    assets, model_paths, warnings = auto_assets(models, args.texture, args.root)
    for raw in args.asset:
        src, archive = parse_explicit_asset(raw)
        assets.append(AssetSpec(src, archive))

    extra = [parse_named_field(x, args.type) for x in args.field]
    extra += [parse_mod(m) for m in args.mod]
    obj = ObjectSpec(args.type, args.base, args.id, args.name, model_paths[args.model], args.scale, extra)
    plan = InjectionPlan(
        args.map,
        args.output,
        [obj],
        assets,
        compact=args.compact,
        make_backup=not args.no_backup,
    )
    report = inject(plan, dll_path=args.dll)
    report.warnings[:0] = warnings
    print(report_to_json(report))


if __name__ == "__main__":
    main()
