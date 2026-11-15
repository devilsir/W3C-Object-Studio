from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import os
import shutil
import tempfile
import time

from .errors import InjectorError, ValidationError
from .fields import UNIT_FIELDS, DOODAD_FIELDS, mod
from .imports import ImportFile
from .modelscan import scan_model, match_textures
from .objectmod import ObjectModFile, Modification, TYPE_REAL, TYPE_STRING
from .storm import StormArchive

PROTECTED = [
    # Script / trigger logic
    "war3map.j", "war3map.lua", "war3map.wtg", "war3map.wct", "war3map.wts",
    # Map/terrain/placements/pathing
    "war3map.w3i", "war3map.w3e", "war3mapUnits.doo", "war3map.doo", "war3map.wpm",
    # Other object-editor categories that this injector is not allowed to touch
    "war3map.w3a", "war3map.w3b", "war3map.w3h", "war3map.w3q", "war3map.w3t",
    # Misc map definitions
    "war3map.w3r", "war3map.w3c", "war3map.w3s",
]
OBJECT_PATHS = {"unit": "war3map.w3u", "hero": "war3map.w3u", "doodad": "war3map.w3d"}
OBJECT_KINDS = {"unit": "unit", "hero": "unit", "doodad": "doodad"}
SUPPORTED_ASSETS = {".mdx", ".mdl", ".blp", ".tga", ".dds"}


@dataclass
class ObjectSpec:
    object_type: str
    base_rawcode: str
    new_rawcode: str
    name: str
    model_archive_path: str
    scale: float = 1.0
    extra_mods: list[Modification] = field(default_factory=list)


@dataclass
class AssetSpec:
    source: Path
    archive_path: str


@dataclass
class InjectionPlan:
    map_path: Path
    output_path: Path
    objects: list[ObjectSpec]
    assets: list[AssetSpec]
    compact: bool = False
    make_backup: bool = True


@dataclass
class InjectionReport:
    output_path: Path
    backup_path: Path | None
    imported: list[str]
    objects: list[str]
    warnings: list[str]
    protected_hashes_ok: bool


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot_protected(archive: StormArchive) -> dict[str, str | None]:
    """Hash protected script/trigger payloads without ever rewriting them."""
    out: dict[str, str | None] = {}
    for p in PROTECTED:
        if not archive.has(p):
            out[p] = None
            continue
        try:
            out[p] = _sha(archive.read(p))
        except Exception as exc:
            # A protected file we cannot read is not safe to edit around: refuse.
            raise ValidationError(f"não foi possível validar arquivo protegido {p}: {exc}") from exc
    return out


def load_object_file(archive: StormArchive, path: str, kind: str) -> ObjectModFile:
    if archive.has(path):
        return ObjectModFile.from_bytes(archive.read(path), kind)
    return ObjectModFile.empty(kind)


def load_import_file(archive: StormArchive) -> ImportFile:
    if archive.has("war3map.imp"):
        return ImportFile.from_bytes(archive.read("war3map.imp"))
    return ImportFile.empty()


def build_object_mods(spec: ObjectSpec) -> list[Modification]:
    if spec.object_type in ("unit", "hero"):
        mods = [
            mod(UNIT_FIELDS["name"].id, TYPE_STRING, spec.name),
            mod(UNIT_FIELDS["model"].id, TYPE_STRING, spec.model_archive_path),
            mod(UNIT_FIELDS["scale"].id, TYPE_REAL, spec.scale),
        ]
    elif spec.object_type == "doodad":
        mods = [
            mod(DOODAD_FIELDS["name"].id, TYPE_STRING, spec.name, 0, 0),
            mod(DOODAD_FIELDS["model"].id, TYPE_STRING, spec.model_archive_path, 0, 0),
            mod(DOODAD_FIELDS["min_scale"].id, TYPE_REAL, spec.scale, 0, 0),
            mod(DOODAD_FIELDS["max_scale"].id, TYPE_REAL, spec.scale, 0, 0),
        ]
    else:
        raise InjectorError(f"tipo de objeto inválido: {spec.object_type}")
    return mods + list(spec.extra_mods)


def validate_plan(plan: InjectionPlan):
    if plan.map_path.suffix.lower() not in {".w3m", ".w3x"}:
        raise InjectorError("o arquivo de entrada precisa ser .w3m ou .w3x")
    if not plan.map_path.exists() or not plan.map_path.is_file():
        raise InjectorError(f"mapa não encontrado: {plan.map_path}")
    if plan.output_path.suffix.lower() not in {".w3m", ".w3x"}:
        raise InjectorError("o arquivo de saída precisa ser .w3m ou .w3x")
    if plan.output_path.suffix.lower() != plan.map_path.suffix.lower():
        raise InjectorError("entrada e saída devem manter a mesma extensão (.w3m ou .w3x)")
    if not plan.objects:
        raise InjectorError("o plano não contém nenhum objeto custom")

    seen_raw: set[tuple[str, str]] = set()
    for obj in plan.objects:
        if obj.object_type not in OBJECT_PATHS:
            raise InjectorError(f"tipo inválido: {obj.object_type}")
        if len(obj.base_rawcode) != 4 or len(obj.new_rawcode) != 4:
            raise InjectorError("base_rawcode e new_rawcode precisam ter 4 caracteres")
        try:
            obj.base_rawcode.encode("latin1")
            obj.new_rawcode.encode("latin1")
        except UnicodeEncodeError as exc:
            raise InjectorError("rawcodes precisam ser ASCII/Latin-1") from exc
        if obj.base_rawcode == obj.new_rawcode:
            raise InjectorError(f"rawcode base e custom são iguais: {obj.new_rawcode}")
        key = (OBJECT_PATHS[obj.object_type], obj.new_rawcode)
        if key in seen_raw:
            raise InjectorError(f"rawcode duplicado no lote: {obj.new_rawcode}")
        seen_raw.add(key)
        if not obj.name.strip():
            raise InjectorError(f"nome vazio para {obj.new_rawcode}")
        if obj.scale <= 0:
            raise InjectorError(f"escala precisa ser > 0 para {obj.new_rawcode}")

    seen_assets: dict[str, Path] = {}
    for asset in plan.assets:
        if not asset.source.exists() or not asset.source.is_file():
            raise InjectorError(f"asset não encontrado: {asset.source}")
        if asset.source.suffix.lower() not in SUPPORTED_ASSETS:
            raise InjectorError(f"asset não suportado: {asset.source.name}")
        ap = asset.archive_path.replace("/", "\\").strip()
        if not ap or ap.startswith("\\") or ap.endswith("\\") or "..\\" in (ap + "\\"):
            raise InjectorError(f"caminho interno inválido: {asset.archive_path}")
        k = ap.casefold()
        if k in seen_assets and seen_assets[k].resolve() != asset.source.resolve():
            raise InjectorError(
                f"colisão de import: duas fontes tentam ocupar '{ap}': "
                f"{seen_assets[k]} e {asset.source}"
            )
        seen_assets[k] = asset.source


def _validate_written_archive(
    target: Path,
    dll_path: str | None,
    protected_before: dict[str, str | None],
    imported: list[str],
    expected_objects: dict[str, tuple[str, set[str]]],
) -> None:
    """Reopen the MPQ after all writes; validation happens on fresh handles."""
    with StormArchive(target, dll_path=dll_path, read_only=True) as arc:
        protected_after = snapshot_protected(arc)
        if protected_before != protected_after:
            raise ValidationError("arquivo protegido mudou durante a injeção; commit recusado")

        if not arc.has("war3map.imp"):
            raise ValidationError("war3map.imp não existe após a injeção")
        imp2 = ImportFile.from_bytes(arc.read("war3map.imp"))
        imported_set = {e.archive_path.casefold() for e in imp2.entries}
        for p in imported:
            if p.casefold() not in imported_set:
                raise ValidationError(f"import ausente de war3map.imp após gravação: {p}")
            if not arc.has(p):
                raise ValidationError(f"asset listado mas ausente do MPQ após gravação: {p}")

        for obj_path, (kind, expected_ids) in expected_objects.items():
            if not arc.has(obj_path):
                raise ValidationError(f"object-data ausente após gravação: {obj_path}")
            reread = ObjectModFile.from_bytes(arc.read(obj_path), kind)
            # Byte-codec must remain stable after a fresh parse.
            encoded = reread.to_bytes()
            reparsed = ObjectModFile.from_bytes(encoded, kind)
            if reparsed.to_bytes() != encoded:
                raise ValidationError(f"round-trip pós-gravação falhou para {obj_path}")
            actual_ids = {r.custom_id for r in reread.custom}
            missing = expected_ids - actual_ids
            if missing:
                raise ValidationError(f"objetos não encontrados em {obj_path}: {sorted(missing)}")


def inject(plan: InjectionPlan, dll_path: str | None = None) -> InjectionReport:
    """
    Transactional injection.

    The source/final map is never edited directly. A sibling temporary copy is
    modified, closed, reopened, validated, and only then atomically committed.
    """
    validate_plan(plan)
    final = plan.output_path.resolve()
    source = plan.map_path.resolve()
    final.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{final.stem}.wc3inject_",
        suffix=final.suffix,
        dir=str(final.parent),
    )
    os.close(fd)
    work = Path(tmp_name)
    backup: Path | None = None
    warnings: list[str] = []
    imported: list[str] = []
    changed_object_files: dict[str, ObjectModFile] = {}
    protected_before: dict[str, str | None] = {}

    try:
        shutil.copy2(source, work)

        with StormArchive(work, dll_path=dll_path) as arc:
            protected_before = snapshot_protected(arc)
            imp = load_import_file(arc)

            for asset in plan.assets:
                ap = asset.archive_path.replace("/", "\\")
                arc.add_file(asset.source, ap, compress=True)
                imp.add_custom(ap)
                if ap.casefold() not in {x.casefold() for x in imported}:
                    imported.append(ap)

            arc.write("war3map.imp", imp.to_bytes())

            for obj in plan.objects:
                obj_path = OBJECT_PATHS[obj.object_type]
                kind = OBJECT_KINDS[obj.object_type]
                data = changed_object_files.get(obj_path)
                if data is None:
                    data = load_object_file(arc, obj_path, kind)
                    changed_object_files[obj_path] = data
                data.upsert_custom(obj.base_rawcode, obj.new_rawcode, build_object_mods(obj))

            for obj_path, data in changed_object_files.items():
                encoded = data.to_bytes()
                # Self-roundtrip before touching the MPQ entry.
                parsed = ObjectModFile.from_bytes(encoded, data.kind)
                if parsed.to_bytes() != encoded:
                    raise ValidationError(f"round-trip pré-gravação falhou para {obj_path}")
                arc.write(obj_path, encoded)

            # Compact only the disposable working copy. A compact failure is a
            # warning because the edits themselves can remain perfectly valid.
            if plan.compact:
                try:
                    arc.compact()
                except Exception as exc:
                    warnings.append(f"compactação ignorada: {exc}")

        expected_objects: dict[str, tuple[str, set[str]]] = {}
        for obj in plan.objects:
            op = OBJECT_PATHS[obj.object_type]
            kind = OBJECT_KINDS[obj.object_type]
            expected_objects.setdefault(op, (kind, set()))[1].add(obj.new_rawcode)

        _validate_written_archive(
            work,
            dll_path,
            protected_before,
            imported,
            expected_objects,
        )

        # Backup only immediately before a successful in-place commit.
        if final == source and plan.make_backup:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup = source.with_suffix(source.suffix + f".{stamp}.bak")
            shutil.copy2(source, backup)

        # os.replace is atomic on the same filesystem. Work lives beside final.
        os.replace(work, final)
        return InjectionReport(
            final,
            backup,
            imported,
            [f"{o.object_type}:{o.new_rawcode}" for o in plan.objects],
            warnings,
            True,
        )
    except Exception:
        try:
            if work.exists():
                work.unlink()
        except OSError:
            pass
        raise


def auto_assets(
    models: list[Path],
    textures: list[Path],
    root: str = "war3mapImported\\Custom",
) -> tuple[list[AssetSpec], dict[Path, str], list[str]]:
    if not models:
        raise InjectorError("adicione pelo menos um MDX/MDL")

    assets: list[AssetSpec] = []
    model_paths: dict[Path, str] = {}
    warnings: list[str] = []
    archive_owner: dict[str, Path] = {}
    texture_added: dict[Path, str] = {}

    def add_asset(source: Path, archive_path: str) -> None:
        archive_path = archive_path.replace("/", "\\")
        key = archive_path.casefold()
        old = archive_owner.get(key)
        if old is not None:
            if old.resolve() != source.resolve():
                raise InjectorError(
                    f"colisão de caminho interno '{archive_path}' entre {old.name} e {source.name}"
                )
            return
        archive_owner[key] = source
        assets.append(AssetSpec(source, archive_path))

    for model_path in models:
        model_path = Path(model_path)
        info = scan_model(model_path)
        model_archive = f"{root}\\Models\\{model_path.name}".replace("/", "\\")
        model_paths[model_path] = model_archive
        add_asset(model_path, model_archive)

        mapping, missing = match_textures(info, textures)
        for ref, src in mapping.items():
            archive_path = ref.replace("/", "\\")
            add_asset(src, archive_path)
            texture_added[src] = archive_path
        for ref in missing:
            warnings.append(f"{model_path.name}: textura referenciada não encontrada/ambígua: {ref}")
        warnings += [f"{model_path.name}: {w}" for w in info.warnings]

    # Extra textures not referenced by a scanned model are still imported.
    # Warcraft command-button textures have conventional archive paths; using
    # those paths means an imported BTN/DISBTN can be assigned immediately as
    # an icon without the user having to rename/re-import it.
    for tex in textures:
        tex = Path(tex)
        if tex in texture_added:
            continue
        lower = tex.name.casefold()
        if lower.startswith("disbtn"):
            archive_path = f"ReplaceableTextures\\CommandButtonsDisabled\\{tex.name}"
        elif lower.startswith("btn"):
            archive_path = f"ReplaceableTextures\\CommandButtons\\{tex.name}"
        else:
            archive_path = f"{root}\\Textures\\{tex.name}".replace("/", "\\")
        add_asset(tex, archive_path)

    return assets, model_paths, warnings


def report_to_json(report: InjectionReport) -> str:
    return json.dumps(
        {
            "output": str(report.output_path),
            "backup": str(report.backup_path) if report.backup_path else None,
            "imported": report.imported,
            "objects": report.objects,
            "warnings": report.warnings,
            "protected_hashes_ok": report.protected_hashes_ok,
        },
        indent=2,
        ensure_ascii=False,
    )
