from __future__ import annotations

import json
import re
import tempfile
import threading
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox

from ..casc import detect_warcraft_install, get_shared_casc
from ..ability_native import resolve_ability_profile
from ..unit_native import resolve_unit_visual
from ..debuglog import configure_logging, log_exception
from ..modelscan import scan_model
from ..storm import StormArchive
from .editor_values import ABILITY_EFFECT_FIELDS

LOGGER, _SESSION_LOG = configure_logging()


class AssetExportMixin:
    """Export the selected object's model art plus all referenced textures.

    The archive keeps Warcraft-relative folders instead of flattening everything.
    This makes custom MPQ models portable and keeps native HD/DE/SD assets grouped
    exactly like their CASC source tree. A small manifest records any CASC texture
    extension substitution (for example a model referencing .tif while the current
    game ships the payload as .dds).
    """

    @staticmethod
    def _export_zip_path(path: str) -> str:
        text = str(path or "").replace("/", "\\")
        # CASC paths are archive chains such as war3.w3mod:_hd.w3mod:Units\...
        if ":" in text:
            text = text.rsplit(":", 1)[-1]
        text = re.sub(r"\\+", r"\\", text).lstrip("\\")
        parts = [p for p in text.split("\\") if p not in {"", ".", ".."}]
        return "/".join(parts)

    @staticmethod
    def _clean_export_filename(text: str) -> str:
        text = re.sub(r"[<>:\"/\\|?*]+", "_", str(text or "")).strip(" ._")
        return text or "wc3_model_export"

    def _visible_object_field_value(self, field_id: str, *, level: int = 0) -> str:
        """Return the selected object's visible/draft value for one raw field."""
        extended = self.current_kind in {"ability", "doodad"}
        for mod in reversed(getattr(self, "working_mods", [])):
            if mod.field_id != field_id:
                continue
            if extended and (int(getattr(mod, "level", 0)) != int(level) or int(getattr(mod, "column", 0)) != 0):
                continue
            return str(mod.value or "").strip()
        if self.session and self.current_id:
            try:
                mod, _source, _explicit = self.session.resolve_map_value(
                    self.current_kind, self.current_table, self.current_id,
                    field_id, level=level, column=0,
                )
                if mod is not None:
                    return str(mod.value or "").strip()
            except Exception:
                LOGGER.debug("Export field lookup failed %s:%s", self.current_id, field_id, exc_info=True)
        return ""

    def _current_export_model_refs(self) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()

        def add(value: str) -> None:
            for ref in str(value or "").replace(";", ",").split(","):
                ref = ref.strip().strip('"')
                if not ref or ref in {"-", "_"}:
                    continue
                key = ref.replace("/", "\\").casefold()
                if key not in seen:
                    seen.add(key)
                    refs.append(ref)

        if self.current_kind in {"unit", "doodad"}:
            ref, _source = self._current_model_reference(allow_icon_inference=False)
            if ref:
                add(ref)
        elif self.current_kind == "ability":
            level = max(1, int(getattr(self, "current_level", 1) or 1))
            for field_id, _label, _placement in ABILITY_EFFECT_FIELDS:
                add(self._visible_object_field_value(field_id, level=level))
        return refs

    def export_current_art_bundle(self) -> None:
        if not self.session or not self.current_id:
            messagebox.showinfo(
                self._msg("Exportar arte", "Export art"),
                self._msg("Selecione um objeto primeiro.", "Select an object first."),
                parent=self,
            )
            return
        refs = self._current_export_model_refs()
        if not refs:
            messagebox.showinfo(
                self._msg("Sem modelo 3D", "No 3D model"),
                self._msg(
                    "Nenhum MDX/MDL foi resolvido nos campos de Arte deste objeto.",
                    "No MDX/MDL was resolved from this object's Art fields.",
                ), parent=self,
            )
            return

        mode = self._model_asset_preference() if hasattr(self, "_model_asset_preference") else "hd"
        title = self.title_var.get().strip() if hasattr(self, "title_var") else self.current_id
        default_name = self._clean_export_filename(f"{self.current_id}_{title}_{mode}") + ".zip"
        target = filedialog.asksaveasfilename(
            parent=self,
            title=self._msg("Exportar modelo + texturas", "Export model + textures"),
            defaultextension=".zip",
            filetypes=[("ZIP", "*.zip")],
            initialfile=default_name,
        )
        if not target:
            return
        target_path = Path(target)
        self.status.set(self._msg(
            f"Exportando {len(refs)} modelo(s) e texturas…",
            f"Exporting {len(refs)} model(s) and textures…",
        ))
        map_path = Path(self.session.map_path)
        object_id = self.current_id
        object_kind = self.current_kind

        def worker():
            try:
                result = self._build_art_export_zip(
                    target_path, refs, mode=mode, map_path=map_path,
                    object_id=object_id, object_kind=object_kind,
                )
                self.after(0, lambda: self._art_export_done(target_path, result, object_kind, object_id))
            except Exception as exc:
                log_exception(LOGGER, f"export art bundle {object_kind}:{object_id}", exc)
                self.after(0, lambda e=exc: self._error_box(
                    self._msg("Falha ao exportar arte", "Art export failed"), "export_current_art_bundle", e
                ))

        threading.Thread(target=worker, daemon=True, name="wc3-art-export").start()

    def _build_art_export_zip(self, target: Path, refs: list[str], *, mode: str, map_path: Path, object_id: str, object_kind: str) -> dict:
        install = detect_warcraft_install()
        casc = None
        if install:
            try:
                casc = get_shared_casc(install)
            except Exception:
                LOGGER.warning("CASC unavailable during art export", exc_info=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        written: set[str] = set()
        manifest = {
            "object": object_id,
            "kind": object_kind,
            "graphics_mode": mode,
            "models": [],
            "textures": [],
            "missing": [],
        }

        def write_entry(zf: zipfile.ZipFile, path: str, data: bytes) -> str:
            arc_path = self._export_zip_path(path)
            if not arc_path:
                arc_path = "exported_asset.bin"
            key = arc_path.casefold()
            if key in written:
                return arc_path
            written.add(key)
            zf.writestr(arc_path, data)
            return arc_path

        with tempfile.TemporaryDirectory(prefix="wc3_export_scan_") as tmp_name, \
             StormArchive(map_path, read_only=True) as arc, \
             zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            tmp = Path(tmp_name)
            for index, requested in enumerate(refs, 1):
                actual = None
                data = None
                source = ""
                for candidate in self._model_asset_refs(requested):
                    found = self._resolve_archive_path(arc, candidate)
                    if found:
                        try:
                            actual, data, source = found, arc.read(found), "MPQ"
                            break
                        except Exception:
                            LOGGER.debug("MPQ export read failed: %s", found, exc_info=True)
                if data is None and casc is not None:
                    for candidate in self._model_asset_refs(requested):
                        try:
                            actual, data = casc.read_with_path(candidate, preference=mode)
                            source = "CASC"
                            break
                        except Exception:
                            data = None
                if data is None or actual is None:
                    manifest["missing"].append({"type": "model", "requested": requested})
                    continue

                model_zip = write_entry(zf, actual, data)
                model_item = {
                    "requested": requested,
                    "resolved": str(actual),
                    "zip_path": model_zip,
                    "source": source,
                    "textures": [],
                }
                manifest["models"].append(model_item)

                scan_path = tmp / f"model_{index}{Path(model_zip).suffix or '.mdx'}"
                scan_path.write_bytes(data)
                try:
                    info = scan_model(scan_path)
                except Exception as exc:
                    manifest["missing"].append({
                        "type": "texture_scan", "model": requested, "error": f"{type(exc).__name__}: {exc}"
                    })
                    continue

                for tex_ref in info.textures:
                    tex_actual = None
                    tex_data = None
                    tex_source = ""
                    found = self._resolve_archive_path(arc, tex_ref, near=actual if source == "MPQ" else None)
                    if found:
                        try:
                            tex_actual, tex_data, tex_source = found, arc.read(found), "MPQ"
                        except Exception:
                            tex_data = None
                    if tex_data is None and casc is not None:
                        try:
                            tex_actual, tex_data = casc.read_with_path(tex_ref, near=actual, preference=mode)
                            tex_source = "CASC"
                        except Exception:
                            tex_data = None
                    if tex_data is None or tex_actual is None:
                        missing = {"type": "texture", "model": requested, "requested": tex_ref}
                        manifest["missing"].append(missing)
                        model_item["textures"].append({**missing, "missing": True})
                        continue

                    tex_zip = write_entry(zf, tex_actual, tex_data)
                    tex_item = {
                        "model": requested,
                        "requested": tex_ref,
                        "resolved": str(tex_actual),
                        "zip_path": tex_zip,
                        "source": tex_source,
                    }
                    manifest["textures"].append(tex_item)
                    model_item["textures"].append(tex_item)

                    # For custom MPQ assets, also mirror the payload at the exact
                    # texture path stored in the model when the file extension is
                    # the same. This is what makes war3mapImported packs open as a
                    # standalone folder without manual texture relinking.
                    requested_ext = Path(str(tex_ref).replace("\\", "/")).suffix.casefold()
                    actual_ext = Path(self._export_zip_path(str(tex_actual))).suffix.casefold()
                    if tex_source == "MPQ" and requested_ext and requested_ext == actual_ext:
                        alias = self._export_zip_path(tex_ref)
                        if alias and alias.casefold() not in written:
                            write_entry(zf, alias, tex_data)
                            tex_item["reference_alias"] = alias

            readme = (
                "WC3 Object Studio — model export\n"
                f"Object: {object_id} ({object_kind})\n"
                f"Graphics set: {mode.upper()}\n\n"
                "The ZIP preserves Warcraft-relative asset folders. Native Reforged/Definitive\n"
                "models may reference .tif names while the installed CASC stores the current\n"
                "texture payload as .dds; export_manifest.json records every resolved mapping.\n"
            )
            zf.writestr("README_EXPORT.txt", readme)
            zf.writestr("export_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        return {
            "models": len(manifest["models"]),
            "textures": len(manifest["textures"]),
            "missing": len(manifest["missing"]),
        }

    @staticmethod
    def _icon_kind_and_core(path: str) -> tuple[str, str, str, str]:
        """Return (kind, folder, core_stem, extension) for a WC3 command icon."""
        text = str(path or "").strip().strip('"').replace('/', '\\')
        folder, _, name = text.rpartition('\\')
        stem = Path(name).stem
        ext = Path(name).suffix or ".blp"
        low = stem.casefold()
        prefixes = (
            ("disabled_passive", "dispasbtn"),
            ("disabled_passive", "dispas"),
            ("disabled_autocast", "disatc"),
            ("disabled", "disbtn"),
            ("passive", "pasbtn"),
            ("passive", "pas"),
            ("autocast", "atc"),
            ("normal", "btn"),
        )
        for kind, prefix in prefixes:
            if low.startswith(prefix):
                return kind, folder, stem[len(prefix):], ext
        return "normal", folder, stem, ext

    @classmethod
    def _icon_variant_candidates(cls, primary_ref: str, import_paths: list[str]) -> dict[str, list[str]]:
        """Build every useful BTN/PAS/ATC sibling candidate without assuming it exists."""
        primary = str(primary_ref or "").strip().strip('"').replace('/', '\\')
        if not primary:
            return {}
        _kind, folder, core, ext = cls._icon_kind_and_core(primary)
        if not core:
            return {"normal": [primary]}

        def unique(values):
            out=[]; seen=set()
            for value in values:
                value=str(value or "").replace('/', '\\')
                key=value.casefold()
                if value and key not in seen:
                    seen.add(key); out.append(value)
            return out

        # Match imported siblings first. Icon packs often use ATC/DISATC or
        # DISPASBTN files that are not referenced by Object Editor fields.
        imported: dict[str, list[str]] = {}
        target_core = re.sub(r"[^a-z0-9]+", "", core.casefold())
        for path in import_paths:
            kind, _ifolder, icore, _iext = cls._icon_kind_and_core(path)
            if re.sub(r"[^a-z0-9]+", "", icore.casefold()) != target_core:
                continue
            imported.setdefault(kind, []).append(path)

        normal_folder = folder.replace("CommandButtonsDisabled", "CommandButtons")
        disabled_folder = normal_folder.replace("CommandButtons", "CommandButtonsDisabled")
        passive_folder = normal_folder.replace("CommandButtons", "PassiveButtons")
        if not normal_folder:
            normal_folder = "ReplaceableTextures\\CommandButtons"
            disabled_folder = "ReplaceableTextures\\CommandButtonsDisabled"
            passive_folder = "ReplaceableTextures\\PassiveButtons"

        def at(base_folder: str, filename: str) -> str:
            return (base_folder.rstrip('\\') + '\\' if base_folder else '') + filename

        derived = {
            "normal": [at(normal_folder, f"BTN{core}{ext}")],
            "disabled": [at(disabled_folder, f"DISBTN{core}{ext}")],
            "passive": [at(passive_folder, f"PASBTN{core}{ext}"), at(normal_folder, f"PASBTN{core}{ext}"), at(normal_folder, f"PAS{core}{ext}")],
            "disabled_passive": [at(disabled_folder, f"DISPASBTN{core}{ext}"), at(disabled_folder, f"DISPAS{core}{ext}")],
            "autocast": [at(normal_folder, f"ATC{core}{ext}")],
            "disabled_autocast": [at(disabled_folder, f"DISATC{core}{ext}")],
        }
        original_kind, *_ = cls._icon_kind_and_core(primary)
        derived.setdefault(original_kind, []).insert(0, primary)
        return {kind: unique(imported.get(kind, []) + values) for kind, values in derived.items()}

    def _ability_icon_seed_refs(self, rawcode: str) -> list[tuple[str, str]]:
        """Return explicit icon refs (Art/Unart/Researchart) for one ability."""
        if not self.session:
            return []
        rawcode = str(rawcode or "").strip()
        seeds: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(label: str, value: str) -> None:
            for ref in str(value or "").replace(';', ',').split(','):
                ref = ref.strip().strip('"')
                if not ref:
                    continue
                low = ref.casefold()
                # Ignore model/effect art: this exporter is intentionally only
                # for command-card/research/autocast icons.
                name = Path(ref.replace('\\', '/')).name.casefold()
                if not ("commandbutton" in low or "passivebutton" in low or name.startswith(("btn", "disbtn", "pas", "dispas", "atc", "disatc"))):
                    continue
                key = ref.replace('/', '\\').casefold()
                if key not in seen:
                    seen.add(key); seeds.append((label, ref))

        table = None; record = None
        for candidate in ("custom", "original"):
            record = self.session.get_record("ability", candidate, rawcode)
            if record is not None:
                table = candidate; break
        base = str(getattr(record, "original_id", "") or rawcode)

        try:
            if table is not None:
                mod, _source, _explicit = self.session.resolve_map_value("ability", table, rawcode, "aart", level=0, column=0)
                if mod is None or not str(mod.value or "").strip():
                    mod, _source, _explicit = self.session.resolve_inherited_value("ability", table, rawcode, "aart", level=0, column=0)
            elif self.session.is_native_object("ability", rawcode):
                mod, _source, _explicit = self.session.resolve_inherited_value("ability", "native", rawcode, "aart", level=0, column=0)
            else:
                mod = None
            if mod is not None:
                add("Art", str(mod.value or ""))
        except Exception:
            LOGGER.debug("Ability icon map lookup failed for %s", rawcode, exc_info=True)

        install = detect_warcraft_install()
        if install:
            try:
                casc = get_shared_casc(install)
                # Prefer the actual rawcode profile when native, then its base.
                for profile_id in dict.fromkeys((rawcode, base)):
                    if len(profile_id) != 4:
                        continue
                    profile = resolve_ability_profile(casc, profile_id)
                    if not profile:
                        continue
                    folded = {str(k).casefold(): str(v) for k, v in profile.items()}
                    for label, keys in (
                        ("Art", ("art", "art:hd", "art_hd", "arthd", "art:sd", "art_sd", "artsd")),
                        ("Unart / Autocast", ("unart", "unart:hd", "unart_hd", "unarthd", "unart:sd", "unart_sd", "unartsd")),
                        ("Researchart", ("researchart", "researchart:hd", "researchart_hd", "researcharthd", "researchart:sd", "researchart_sd", "researchartsd")),
                    ):
                        for key in keys:
                            value = folded.get(key, "").strip()
                            if value:
                                add(f"{profile_id}:{label}", value)
                                break
            except Exception:
                LOGGER.debug("Ability profile icon lookup failed for %s", rawcode, exc_info=True)
        return seeds

    def _unit_icon_seed_refs(self, rawcode: str, *, placed=False) -> list[tuple[str, str]]:
        if not self.session:
            return []
        rawcode = str(rawcode or "").strip()
        seeds: list[tuple[str, str]] = []
        seen: set[str] = set()
        def add(label, ref):
            ref = str(ref or "").strip().strip('"')
            if ref and ref.casefold() not in seen:
                seen.add(ref.casefold()); seeds.append((label, ref))

        table = "native"
        if self.session.get_record("unit", "custom", rawcode) is not None:
            table = "custom"
        elif self.session.get_record("unit", "original", rawcode) is not None:
            table = "original"
        try:
            mod, _source, _explicit = self.session.resolve_map_value("unit", table, rawcode, "uico", level=0, column=0)
            if mod is None or not str(mod.value or "").strip():
                mod, _source, _explicit = self.session.resolve_inherited_value("unit", table, rawcode, "uico", level=0, column=0)
            if mod is not None:
                add("Game interface", mod.value)
        except Exception:
            LOGGER.debug("Unit icon lookup failed for %s", rawcode, exc_info=True)

        # Placement skin IDs may select a different native icon than the type.
        if placed and getattr(self, "_placed_index", None) is not None:
            try:
                unit = self.session.placed_unit_at(self._placed_index)
                skin = str(getattr(unit, "skin_id", "") or "").strip("\0 ") if unit else ""
                install = detect_warcraft_install()
                if install:
                    casc = get_shared_casc(install)
                    mode = self._placed_asset_preference() if hasattr(self, "_placed_asset_preference") else "hd"
                    for visual_raw in (skin, self._placed_native_base(rawcode) if hasattr(self, "_placed_native_base") else "", rawcode):
                        if len(visual_raw) != 4:
                            continue
                        visual = resolve_unit_visual(casc, visual_raw, preference=mode)
                        if visual.get("icon"):
                            add(f"Skin {visual_raw}", visual.get("icon")); break
            except Exception:
                LOGGER.debug("Placed unit icon lookup failed for %s", rawcode, exc_info=True)
        return seeds

    def export_current_icon_bundle(self) -> None:
        if not self.session or not self.current_id:
            messagebox.showinfo(self._msg("Exportar ícones", "Export icons"), self._msg("Selecione um objeto primeiro.", "Select an object first."), parent=self)
            return
        if self.current_kind == "ability":
            self.export_ability_icon_bundle(self.current_id)
            return
        if self.current_kind != "unit":
            messagebox.showinfo(self._msg("Exportar ícones", "Export icons"), self._msg("Este tipo de objeto não possui command-button para exportar.", "This object type has no command-button icon to export."), parent=self)
            return
        self._export_icon_bundle_dialog(self.current_id, self._unit_icon_seed_refs(self.current_id), kind="unit")

    def export_placed_icon_bundle(self) -> None:
        if not self.session or getattr(self, "_placed_index", None) is None:
            messagebox.showinfo(self._msg("Exportar ícones", "Export icons"), self._msg("Selecione uma unidade colocada primeiro.", "Select a placed unit first."), parent=self)
            return
        unit = self.session.placed_unit_at(self._placed_index)
        if unit is None:
            return
        self._export_icon_bundle_dialog(unit.unit_id, self._unit_icon_seed_refs(unit.unit_id, placed=True), kind="placed_unit")

    def export_ability_icon_bundle(self, rawcode: str) -> None:
        rawcode = str(rawcode or "").strip()
        self._export_icon_bundle_dialog(rawcode, self._ability_icon_seed_refs(rawcode), kind="ability")

    def _export_icon_bundle_dialog(self, rawcode: str, seeds: list[tuple[str, str]], *, kind: str) -> None:
        if not self.session:
            return
        if not seeds:
            messagebox.showinfo(self._msg("Sem ícones", "No icons"), self._msg("Nenhum caminho de ícone foi resolvido para este objeto.", "No icon path was resolved for this object."), parent=self)
            return
        mode = (self._placed_asset_preference() if kind == "placed_unit" and hasattr(self, "_placed_asset_preference") else self._model_asset_preference()) if hasattr(self, "_model_asset_preference") else "hd"
        title = rawcode
        try:
            if kind == "ability":
                title = self.session.native_name("ability", rawcode) or rawcode
            elif kind in {"unit", "placed_unit"}:
                title = self.session.native_name("unit", rawcode) or rawcode
        except Exception:
            pass
        initial = self._clean_export_filename(f"{rawcode}_{title}_icons_{mode}") + ".zip"
        target = filedialog.asksaveasfilename(parent=self, title=self._msg("Exportar ícones", "Export icons"), defaultextension=".zip", filetypes=[("ZIP", "*.zip")], initialfile=initial)
        if not target:
            return
        target_path = Path(target)
        map_path = Path(self.session.map_path)
        imports = [entry.archive_path for entry in self.session.imports.entries]
        self.status.set(self._msg(f"Exportando ícones de {rawcode}…", f"Exporting {rawcode} icons…"))

        def worker():
            try:
                result = self._build_icon_export_zip(target_path, rawcode=rawcode, kind=kind, seeds=seeds, imports=imports, mode=mode, map_path=map_path)
                self.after(0, lambda: self._icon_export_done(target_path, rawcode, result))
            except Exception as exc:
                log_exception(LOGGER, f"export icons {kind}:{rawcode}", exc)
                self.after(0, lambda e=exc: self._error_box(self._msg("Falha ao exportar ícones", "Icon export failed"), "export_icon_bundle", e))
        threading.Thread(target=worker, daemon=True, name="wc3-icon-export").start()

    def _build_icon_export_zip(self, target: Path, *, rawcode: str, kind: str, seeds: list[tuple[str, str]], imports: list[str], mode: str, map_path: Path) -> dict:
        install = detect_warcraft_install()
        casc = None
        if install:
            try:
                casc = get_shared_casc(install)
            except Exception:
                LOGGER.debug("CASC unavailable during icon export", exc_info=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest = {"object": rawcode, "kind": kind, "graphics_mode": mode, "seed_refs": [], "icons": [], "missing_explicit": []}
        written: set[str] = set()
        found_paths: set[str] = set()

        def write(zf, actual, data, label, variant, requested):
            zpath = self._export_zip_path(actual or requested)
            if not zpath:
                zpath = Path(str(actual or requested).replace('\\', '/')).name or f"{variant}.blp"
            if zpath.casefold() not in written:
                written.add(zpath.casefold()); zf.writestr(zpath, data)
            manifest["icons"].append({"label": label, "variant": variant, "requested": requested, "resolved": str(actual or requested), "zip_path": zpath})

        with StormArchive(map_path, read_only=True) as arc, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for label, seed in seeds:
                manifest["seed_refs"].append({"label": label, "path": seed})
                variants = self._icon_variant_candidates(seed, imports)
                explicit_found = False
                for variant, refs in variants.items():
                    for ref in refs:
                        key = ref.replace('/', '\\').casefold()
                        if key in found_paths:
                            continue
                        data = None; actual = None
                        found = self._resolve_archive_path(arc, ref)
                        if found:
                            try:
                                actual, data = found, arc.read(found)
                            except Exception:
                                data = None
                        if data is None and casc is not None:
                            try:
                                actual, data = casc.read_with_path(ref, preference=mode)
                            except Exception:
                                data = None
                        if data is None:
                            continue
                        found_paths.add(key)
                        if ref.replace('/', '\\').casefold() == seed.replace('/', '\\').casefold():
                            explicit_found = True
                        write(zf, actual, data, label, variant, ref)
                if not explicit_found:
                    # Seed might have resolved through a differently-cased/aliased
                    # CASC path. Try it directly once before calling it missing.
                    ref = seed
                    data = None; actual = None
                    found = self._resolve_archive_path(arc, ref)
                    if found:
                        try: actual, data = found, arc.read(found)
                        except Exception: data = None
                    if data is None and casc is not None:
                        try: actual, data = casc.read_with_path(ref, preference=mode)
                        except Exception: data = None
                    if data is not None:
                        write(zf, actual, data, label, "explicit", ref)
                    else:
                        manifest["missing_explicit"].append({"label": label, "path": seed})
            zf.writestr("icons_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.writestr("README_ICONS.txt", self._msg(
                "WC3 Object Studio — exportação de ícones\nInclui todas as variantes encontradas (BTN, DISBTN, PAS/DISPAS e ATC/DISATC), além de Art/Unart/Researchart quando a habilidade fornece esses caminhos.\n",
                "WC3 Object Studio — icon export\nIncludes every variant found (BTN, DISBTN, PAS/DISPAS and ATC/DISATC), plus Art/Unart/Researchart when the ability provides those paths.\n",
            ))
        return {"icons": len(manifest["icons"]), "missing": len(manifest["missing_explicit"])}

    def _icon_export_done(self, target: Path, rawcode: str, result: dict) -> None:
        self.status.set(self._msg(f"{rawcode}: {result['icons']} ícone(s) exportado(s).", f"{rawcode}: {result['icons']} icon(s) exported."))
        messagebox.showinfo(self._msg("Exportação concluída", "Export complete"), self._msg(
            f"Exportados {result['icons']} ícone(s)" + (f" · {result['missing']} referência(s) explícita(s) não encontrada(s)" if result['missing'] else "") + f".\n\n{target}",
            f"Exported {result['icons']} icon(s)" + (f" · {result['missing']} explicit reference(s) not found" if result['missing'] else "") + f".\n\n{target}",
        ), parent=self)

    def _art_export_done(self, target: Path, result: dict, kind: str, object_id: str) -> None:
        text = self._msg(
            f"Export concluído: {result['models']} modelo(s), {result['textures']} textura(s)"
            + (f", {result['missing']} referência(s) ausente(s)" if result['missing'] else "")
            + f".\n\n{target}",
            f"Export complete: {result['models']} model(s), {result['textures']} texture(s)"
            + (f", {result['missing']} missing reference(s)" if result['missing'] else "")
            + f".\n\n{target}",
        )
        self.status.set(self._msg(f"{object_id}: ZIP de arte exportado.", f"{object_id}: art ZIP exported."))
        messagebox.showinfo(self._msg("Exportação concluída", "Export complete"), text, parent=self)
