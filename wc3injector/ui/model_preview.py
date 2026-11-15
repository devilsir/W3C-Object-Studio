from __future__ import annotations

import copy
import os
import re
import subprocess
import tempfile
import threading
import time
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from ..debuglog import configure_logging, latest_log_path, log_exception, log_resource_snapshot
from ..editor import EditSession
from ..fields import FIELD_SETS, FIELD_BY_ID, TYPE_NAMES, NAME_TYPES
from ..injector import auto_assets
from ..modelscan import scan_model, rewrite_texture_references
from ..objectmod import Modification, TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING
from ..storm import StormArchive, safe_archive_basename
from ..casc import CascStorage, CascError, detect_warcraft_install, remember_warcraft_install, get_shared_casc
from ..ability_native import profile_visual_bundle, resolve_ability_visual_bundle
from ..doodad_native import doodad_model_candidates
from ..unit_native import resolve_unit_visual, unit_model_candidates
from ..viewer_server import launch_viewer, prepare_viewer_url
from ..visuals import best_sibling_visual, infer_icon_import, related_icon_variants
from ..webview_profile import prepare_webview2_profile
from ..i18n import tr as i18n_tr, localize_field_def, describe_field as describe_field_i18n, translate_category
from ..paths import ASSETS_DIR
from .editor_values import TYPE_MAP, KIND_LABELS, ABILITY_EFFECT_FIELDS, DEFAULT_NEW, coerce, value_text, field_value_text, coerce_field
from .widgets import ScrollForm, AdaptiveFieldRow
from .dialogs import RawDialog, NewObjectDialog

LOGGER, _SESSION_LOG = configure_logging()


class ModelPreviewMixin:
    def _current_model_reference(self, allow_icon_inference: bool = True):
            if self.current_kind not in {"unit","doodad"} or not self.current_id:
                return None,""
            key="model"
            native_fallback=None
            if key in self.form_vars and self.form_vars[key].get().strip():
                source=self.form_state.get(key,{}).get("source","")
                value=self.form_vars[key].get().strip()
                if self.current_kind=="unit" and self.current_table=="custom" and source.startswith("base nativa"):
                    native_fallback=(value,source)
                else:
                    return value,source
            fd=self._fields_for_kind(self.current_kind).get(key)
            if fd and native_fallback is None:
                inherited,source=self.inherited_mod_for(fd)
                if inherited is not None and str(inherited.value).strip():
                    if self.current_kind=="unit" and self.current_table=="custom" and source.startswith("base nativa"):
                        native_fallback=(str(inherited.value).strip(),source)
                    else:
                        return str(inherited.value).strip(),source
            if self.current_kind=="unit":
                sibling,sibling_source=self._sibling_visual_reference("umdl")
                if sibling:
                    return sibling,sibling_source
            if native_fallback is not None:
                return native_fallback
            return None,source if 'source' in locals() else ""

    @staticmethod
    def _unique_target(tmp: Path, name: str) -> Path:
            target=tmp/name
            n=2
            while target.exists():
                target=tmp/f"{Path(name).stem}_{n}{Path(name).suffix}"; n+=1
            return target

    def _preview_stale(self, serial: int) -> bool:
            return serial != self.preview_serial

    def _extract_model_bundle_from_arc(
        self, arc: StormArchive, archive_ref: str, tmp: Path, files: list[Path], missing: list[str], *,
        near: str | None = None, casc: CascStorage | None = None, casc_preference: str = "hd", serial: int | None = None, casc_getter=None
    ):
            """Extract one model + textures, preferring MPQ and falling back to WC3 CASC.

            ``serial`` lets a superseded click abort between expensive reads instead
            of finishing a model the user no longer wants to see.
            """
            started=time.perf_counter(); actual=None; source="MPQ"; data=None
            active_casc=casc
            def ensure_casc():
                nonlocal active_casc
                if active_casc is None and casc_getter is not None:
                    active_casc=casc_getter()
                return active_casc
            refs=self._model_asset_refs(archive_ref)
            LOGGER.debug("PREVIEW[%s] resolve model ref=%s refs=%s pref=%s", serial, archive_ref, refs, casc_preference)
            for ref in refs:
                if serial is not None and self._preview_stale(serial):
                    raise InterruptedError(f"preview {serial} superseded while resolving model")
                actual=self._resolve_archive_path(arc,ref,near=near)
                if actual:
                    try:
                        data=arc.read(actual); source="MPQ"; break
                    except Exception:
                        LOGGER.debug("PREVIEW[%s] MPQ model read failed: %s",serial,actual,exc_info=True)
                        data=None
            if data is None:
                fallback_casc=ensure_casc()
            else:
                fallback_casc=active_casc
            if data is None and fallback_casc is not None:
                for ref in refs:
                    if serial is not None and self._preview_stale(serial):
                        raise InterruptedError(f"preview {serial} superseded before CASC model read")
                    try:
                        actual,data=fallback_casc.read_with_path(ref, preference=casc_preference); source="CASC"; break
                    except Exception:
                        data=None
            if data is None:
                LOGGER.debug("PREVIEW[%s] model unresolved ref=%s after %.1f ms",serial,archive_ref,(time.perf_counter()-started)*1000)
                return None,None

            if serial is not None and self._preview_stale(serial):
                raise InterruptedError(f"preview {serial} superseded after model read")
            actual_text=str(actual or archive_ref)
            model_ext=Path(actual_text.replace("\\","/").split(":")[-1]).suffix or ".mdx"
            model_name=safe_archive_basename(actual or archive_ref, fallback=f"model{model_ext}")
            model_path=self._unique_target(tmp,model_name)
            model_path.write_bytes(data); files.append(model_path)
            LOGGER.info("PREVIEW[%s] model %s from %s (%d bytes) -> %s",serial,actual,source,len(data),model_path.name)
            texture_bytes=0; texture_count=0
            try:
                info=scan_model(model_path)
                existing_names={p.name.casefold():p for p in files}
                texture_rewrites: dict[str, str] = {}
                for tex_ref in info.textures:
                    if serial is not None and self._preview_stale(serial):
                        raise InterruptedError(f"preview {serial} superseded while loading textures")
                    tex_data=None; found=self._resolve_archive_path(arc,tex_ref,near=actual if source=="MPQ" else None)
                    tex_actual=found
                    if found:
                        tex_data=arc.read(found)
                    else:
                        texture_casc=ensure_casc()
                        if texture_casc is not None:
                            try:
                                tex_actual,tex_data=texture_casc.read_with_path(tex_ref, near=actual or archive_ref, preference=casc_preference)
                            except Exception:
                                tex_data=None
                    if tex_data is None:
                        missing.append(f"{actual}: {tex_ref}"); continue
                    tex_text=str(tex_actual or tex_ref)
                    tex_ext=Path(tex_text.replace("\\","/").split(":")[-1]).suffix or ".bin"
                    tex_name=safe_archive_basename(tex_actual or tex_ref, fallback=f"texture_{texture_count+1}{tex_ext}")
                    target=tmp/tex_name
                    if tex_name.casefold() in existing_names:
                        # Two different Warcraft paths can share one basename.
                        # Keep both payloads and relink the preview copy instead
                        # of silently showing the first texture twice.
                        target=self._unique_target(tmp,tex_name)
                        tex_name=target.name
                    target.write_bytes(tex_data); files.append(target)
                    existing_names[tex_name.casefold()]=target
                    texture_rewrites[tex_ref.replace("/", "\\")]=tex_name
                    texture_count += 1; texture_bytes += len(tex_data)
                if texture_rewrites:
                    changed_refs=rewrite_texture_references(model_path,texture_rewrites)
                    if changed_refs:
                        LOGGER.debug("PREVIEW[%s] relinked %d texture reference(s) in %s",serial,changed_refs,model_path.name)
            except InterruptedError:
                raise
            except Exception as scan_exc:
                LOGGER.warning("Preview texture scan failed for %s: %s",actual,scan_exc,exc_info=True)
            elapsed=(time.perf_counter()-started)*1000.0
            LOGGER.info(
                "PREVIEW[%s] bundle ready ref=%s source=%s textures=%d bytes=%d missing=%d total=%.1f ms",
                serial,archive_ref,source,texture_count,texture_bytes,len(missing),elapsed
            )
            return actual,model_path

    def _show_ability_scene(self, serial: int, files: list[Path], hero_model: Path, scene: dict, label: str, missing: list[str]):
            if serial!=self.preview_serial:return
            self._show_preview_files(serial,files,hero_model,label,missing,scene=scene)

    def refresh_preview(self):
            self.preview_serial += 1
            serial=self.preview_serial
            if serial == 1 or serial % 5 == 0:
                log_resource_snapshot(LOGGER,f"preview-start serial={serial} kind={self.current_kind} id={self.current_id}")
            asset_preference=self._model_asset_preference() if hasattr(self, "_model_asset_preference") else "hd"
            try:
                self._show_preview_loading(self._msg("Carregando preview 3D…", "Loading 3D preview…"))
            except Exception:
                pass
            asset_mode_label=("Definitive DE" if asset_preference=="de" else (self._msg("Clássico / SD", "Classic / SD") if asset_preference=="classic" else "Reforged HD"))
            self.preview_last_files=[]; self.preview_last_model=None; self.preview_last_scene=None
            if not self.current_id:
                self._clear_preview_view()
                self._hide_preview_loading()
                self.preview_status_var.set(self._msg("Selecione uma unidade, doodad ou habilidade.", "Select a unit, doodad, or ability."))
                return
            if self.current_kind=="ability":
                if not self.session:
                    self._clear_preview_view()
                    self._hide_preview_loading()
                    self.preview_status_var.set(self._msg("Abra um mapa para visualizar os efeitos da habilidade.", "Open a map to preview ability effects."))
                    return
                # Ability preview is intentionally FX-only.  Earlier builds assembled a
                # fake caster/target scene, which made the panel heavy and obscured the
                # actual spell models.  Keep only the skill art resolved from w3a/CASC/
                # Buff/Effect profiles and script-local model references.
                ability_id=str(self.current_id)
                ability_name=str(self.title_var.get())
                ability_base=str(self.current_base or self.current_id)
                ability_level=int(self.current_level or 1)
                effect_states={fid:self._ability_map_field_state(fid,level=self.current_level) for fid,_,_ in ABILITY_EFFECT_FIELDS}
                inferred_imports=[dict(x) for x in self._ability_import_effect_candidates(None)]
                map_effect_count=sum(1 for value,_empty in effect_states.values() if value)
                self.preview_status_var.set(
                    self._msg(f"Carregando FX da habilidade {ability_id} · {map_effect_count} efeito(s) no mapa · procurando herança no CASC…", f"Loading ability FX {ability_id} · {map_effect_count} map effect(s) · checking CASC inheritance…")
                )
                map_path=self.session.map_path

                def ability_worker():
                    casc=None
                    worker_ability_name=ability_name
                    started=time.perf_counter()
                    try:
                        if self._preview_stale(serial):
                            return
                        tmp=Path(tempfile.mkdtemp(prefix="wc3_ability_fx_")); self.temp_dirs.append(tmp)
                        files=[]; missing=[]; scene_effects=[]; casc_label=self._msg("CASC indisponível", "CASC unavailable")
                        native_bundle=profile_visual_bundle(
                            self.session.native_abilities if self.session else {},
                            ability_base,level=ability_level,
                            resolver=self.session.resolve_string if self.session else None
                        )
                        install=detect_warcraft_install()
                        if install:
                            try:
                                casc=get_shared_casc(install); casc_label=self._msg(f"CASC compartilhado: {install}", f"Shared CASC: {install}")
                                LOGGER.info("Ability FX preview using local CASC for %s base=%s level=%s",ability_id,ability_base,ability_level)
                                native_bundle=resolve_ability_visual_bundle(casc,ability_base,level=ability_level)
                                LOGGER.info(
                                    "Ability native visual bundle %s: name=%s icon=%s related=%s effects=%d",
                                    ability_base,native_bundle.get("name"),native_bundle.get("icon"),
                                    native_bundle.get("related_ids"),len(native_bundle.get("effects",[]))
                                )
                            except Exception as casc_exc:
                                LOGGER.warning("CASC ability FX preview unavailable: %s",casc_exc,exc_info=True)
                                casc=None; casc_label=self._msg(f"CASC falhou: {casc_exc}", f"CASC failed: {casc_exc}")
                        else:
                            LOGGER.warning("Warcraft III install not detected; native spell assets cannot be extracted from CASC")

                        effects=[]
                        for fid,label_name,placement in ABILITY_EFFECT_FIELDS:
                            raw,explicit_empty=effect_states[fid]
                            for idx,ref in enumerate(self._split_model_refs(raw)):
                                effects.append({
                                    "field":fid,"label":label_name if idx==0 else f"{label_name} {idx+1}",
                                    "archive_ref":ref,"placement":placement,"source":self._msg("mapa", "map"),
                                })

                        for native_item in native_bundle.get("effects",[]):
                            fid=str(native_item.get("field", ""))
                            relation=str(native_item.get("relation", "ability"))
                            map_value,explicit_empty=effect_states.get(fid,("",False))
                            if relation=="ability" and (map_value or explicit_empty):
                                continue
                            ref=str(native_item.get("archive_ref", "") or "").strip()
                            for split_ref in self._split_model_refs(ref):
                                item=dict(native_item)
                                item["archive_ref"]=split_ref
                                effects.append(item)

                        if (not worker_ability_name or worker_ability_name==ability_id) and native_bundle.get("name"):
                            worker_ability_name=str(native_bundle.get("name"))

                        with StormArchive(map_path,read_only=True) as arc:
                            scripted=self._script_effect_references_from_arc(arc,ability_id)
                            if ability_base == "ANcl" and ability_id != ability_base and scripted and not map_effect_count:
                                effects=[]
                                LOGGER.info("Spell Lab %s: suppressing native ANcl placeholder art because script-local FX were found", ability_id)
                            seen_refs={x["archive_ref"].casefold() for x in effects}
                            for extra in [*scripted,*inferred_imports]:
                                key=extra["archive_ref"].casefold()
                                if key not in seen_refs:
                                    effects.append(extra); seen_refs.add(key)

                            # Native ability/profile overlays can expose the exact same
                            # Art model through multiple fields/related profiles.  In the
                            # FX-only viewer these copies overlap perfectly, while each
                            # duplicate still creates another WebGL renderer + texture set.
                            # X015 in the OPWU log, for example, loaded DeathPactTarget three
                            # times and spent ~5.5 s in the viewer for ~48 KB of assets.
                            unique_effects=[]; duplicate_effects=0; seen_effect_models=set()
                            for item in effects:
                                ref_key=str(item.get("archive_ref","") or "").replace("/","\\").casefold()
                                if not ref_key or ref_key in seen_effect_models:
                                    if ref_key:
                                        duplicate_effects+=1
                                    continue
                                seen_effect_models.add(ref_key); unique_effects.append(item)
                            if duplicate_effects:
                                LOGGER.info(
                                    "Spell FX %s: removed %d duplicate model reference(s) before extraction",
                                    ability_id,duplicate_effects
                                )
                            effects=unique_effects
                            LOGGER.info(
                                "Spell FX %s: script=%d import-inferred=%d candidates=%d",
                                ability_id,len(scripted),len(inferred_imports),len(effects)
                            )
                            for item in effects:
                                if self._preview_stale(serial):
                                    raise InterruptedError(f"preview {serial} superseded during ability FX")
                                actual,effect_model=self._extract_model_bundle_from_arc(
                                    arc,item["archive_ref"],tmp,files,missing,casc=casc,serial=serial,
                                    # Classic/SD spell FX use normal ParticleEmitter2/Ribbon
                                    # data and are substantially more compatible with war3-model
                                    # than Reforged Popcorn-emitter stubs.
                                    casc_preference="classic"
                                )
                                if not effect_model:
                                    missing.append(f"{item['label']}: {item['archive_ref']}")
                                    continue
                                scene_effects.append({
                                    "label":item["label"],"placement":item.get("placement","effect"),
                                    "model":effect_model,"archive_ref":actual or item["archive_ref"],
                                    "source":item.get("source","")
                                })

                        if not scene_effects:
                            LOGGER.info(
                                "PREVIEW[%s] ability FX empty %s: candidates=%d missing=%d; habilidade pode não usar Art 3D",
                                serial,ability_id,len(effects),len(missing)
                            )
                            self.after(0,lambda:self._ability_preview_empty(serial,ability_id,worker_ability_name,missing))
                            return
                        primary_model=Path(scene_effects[0]["model"])
                        scene={
                            "mode":"ability_fx",
                            "ability_id":ability_id,
                            "ability_name":worker_ability_name,
                            "ability_base":ability_base,
                            "effects":scene_effects,
                            "casc_status":casc_label,
                            "asset_mode":"classic_fx",
                        }
                        label=(f"SKILL {ability_id} · {worker_ability_name or ability_id} · " +
                               self._msg(f"{len(scene_effects)} FX carregado(s) · {casc_label}", f"{len(scene_effects)} FX loaded · {casc_label}"))
                        LOGGER.info("PREVIEW[%s] ability FX ready %s effects=%d total=%.1f ms",serial,ability_id,len(scene_effects),(time.perf_counter()-started)*1000.0)
                        self.after(0,lambda:self._show_ability_scene(serial,files,primary_model,scene,label,missing))
                    except InterruptedError as exc:
                        LOGGER.info("PREVIEW[%s] cancelled ability FX: %s",serial,exc)
                    except Exception as exc:
                        log_exception(LOGGER,f"ability FX preview {ability_id}",exc)
                        self.after(0,lambda e=exc:self._preview_failed(serial,ability_id,e))
                threading.Thread(target=ability_worker,daemon=True,name="wc3-ability-preview").start()
                return
            model_ref,source=self._current_model_reference()
            current_kind=str(self.current_kind)
            current_table=str(self.current_table or "")
            current_id=str(self.current_id or "")
            current_base=str(self.current_base or current_id)
            native_visual=bool(current_table=="native" or (source or "").startswith("base nativa") or "CASC" in (source or ""))

            doodad_refs=[]
            # Native doodad ``file`` is often only a base path. The real asset may
            # be Base0.mdx/Base1.mdx according to numVar, so keep all candidates
            # and probe them in the worker instead of assuming the literal path.
            if current_kind=="doodad" and self.session:
                native_rawcode=(current_base if (current_table=="custom" and current_base and current_base.strip("\0")) else current_id) or current_base
                native_row=self.session.native_doodads.get(native_rawcode,{})
                doodad_refs=doodad_model_candidates(native_row,native_rawcode,preference=asset_preference)
                if not model_ref and doodad_refs:
                    model_ref=doodad_refs[0]
                    source=f"CASC Doodads.slk + DoodadSkins.txt · {asset_mode_label}"
                    native_visual=True
                elif model_ref:
                    # Explicit map override first, native variants after it.
                    doodad_refs=[model_ref,*[x for x in doodad_refs if x.casefold()!=model_ref.casefold()]]
                if not doodad_refs:
                    LOGGER.warning(
                        "PREVIEW[%s] doodad %s has no model candidate; row_keys=%s row=%s",
                        serial, native_rawcode, sorted(map(str, native_row.keys())),
                        {str(k): native_row[k] for k in list(native_row)[:18]},
                    )

            local=self.local_models.get(model_ref.casefold()) if model_ref else None
            if local:
                files=[local[0],*local[1]]
                self._show_preview_files(serial,files,local[0],self._msg(f"{model_ref} · staged local", f"{model_ref} · staged locally") + (f" · {self._localize_source_text(source)}" if source else ""))
                return

            # Keep HD/SD separated for every object.  A valid imported map model is
            # physically identical in both modes, but a *missing* custom import can
            # fall back to the Warcraft base model.  the previous build cached that fallback
            # under a mode-agnostic ``map:`` key, so switching HD -> SD immediately
            # reused the HD Footman/hero bundle instead of resolving Classic art.
            preview_cache_key=f"{asset_preference}:{current_kind}:{current_table}:{current_id}:{(model_ref or '').casefold()}"
            cached=self.preview_cache.get(preview_cache_key)
            if cached and all(p.is_file() for p in cached[0]):
                files,model_path,label,missing=cached
                LOGGER.info("PREVIEW[%s] cache hit key=%s files=%d",serial,preview_cache_key,len(files))
                self._show_preview_files(serial,files,model_path,label + (f" · {self._localize_source_text(source)}" if source else ""),missing)
                return
            LOGGER.debug("PREVIEW[%s] cache miss key=%s kind=%s table=%s id=%s mode=%s ref=%s source=%s",
                         serial,preview_cache_key,current_kind,current_table,current_id,asset_preference,model_ref,source)
            if not self.session:
                self._clear_preview_view()
                self._hide_preview_loading()
                self.preview_status_var.set(self._msg("Abra um mapa para extrair o modelo selecionado.", "Open a map to extract the selected model."))
                return

            # Standard Warcraft objects are virtual rows: their model/icon files live
            # in the local game CASC, not in the map MPQ. Resolve/extract them there.
            if not model_ref and current_kind=="unit" and (current_table=="native" or native_visual):
                self._show_preview_loading(self._msg(f"Buscando modelo nativo de {current_id}…", f"Looking up native model for {current_id}…"))
                self.preview_status_var.set(self._msg(f"Buscando modelo nativo de {current_id} no CASC do Warcraft III · {asset_mode_label}…", f"Looking up native model for {current_id} in Warcraft III CASC · {asset_mode_label}…"))
            elif not model_ref:
                self._clear_preview_view()
                self._hide_preview_loading()
                base=current_base
                if current_kind == "doodad":
                    self.preview_status_var.set(
                        self._msg(f"Nenhum modelo foi resolvido para o doodad {base}. A linha nativa não trouxe Art - Model File; veja o log PREVIEW para os campos lidos.", f"No model was resolved for doodad {base}. The native row did not provide Art - Model File; see the PREVIEW log for the fields that were read.")
                    )
                else:
                    self.preview_status_var.set(
                        self._msg(f"Nenhum caminho de modelo foi resolvido para {base}. Se for um objeto padrão do Warcraft, configure o CASC pelo botão CASC….", f"No model path was resolved for {base}. If this is a standard Warcraft object, configure CASC with the CASC… button.")
                    )
                return
            else:
                origin="MPQ/CASC" if native_visual or current_table=="native" else self._msg("mapa (com fallback CASC)", "map (with CASC fallback)")
                self._show_preview_loading(self._msg(f"Carregando {model_ref}…", f"Loading {model_ref}…"))
                self.preview_status_var.set(self._msg(f"Extraindo {model_ref} + texturas de {origin} · {asset_mode_label}…", f"Extracting {model_ref} + textures from {origin} · {asset_mode_label}…"))

            map_path=self.session.map_path
            requested_ref=str(model_ref or "")
            def worker():
                started=time.perf_counter(); casc=None
                try:
                    if self._preview_stale(serial):
                        return
                    LOGGER.info(
                        "PREVIEW[%s] start kind=%s table=%s id=%s base=%s mode=%s ref=%s native=%s",
                        serial,current_kind,current_table,current_id,current_base,asset_preference,requested_ref,native_visual
                    )
                    tmp=Path(tempfile.mkdtemp(prefix="wc3_embedded_preview_")); self.temp_dirs.append(tmp)
                    files=[]; missing=[]; resolved_ref=requested_ref; resolved_source=source
                    install=detect_warcraft_install()

                    def shared_casc():
                        nonlocal casc
                        if casc is None and install:
                            casc=get_shared_casc(install)
                        return casc

                    unit_refs=[]
                    # Only native/inherited Warcraft units need CASC before the MPQ
                    # probe. Custom map models stay MPQ-only unless a model/texture
                    # actually misses and the lazy fallback below is needed.
                    if current_kind=="unit" and (not resolved_ref or native_visual):
                        if self._preview_stale(serial):
                            raise InterruptedError(f"preview {serial} superseded before native resolve")
                        native_rawcode=current_base if (current_table=="custom" and current_base) else (current_id or current_base)
                        native_casc=shared_casc()
                        if native_casc is not None:
                            visual=resolve_unit_visual(native_casc,native_rawcode,preference=asset_preference)
                            unit_refs=unit_model_candidates(native_casc,native_rawcode,preference=asset_preference)
                            native_ref=str(visual.get("model","") or "").strip()
                            if native_ref:
                                resolved_ref=native_ref
                                resolved_source=f"CASC · {native_rawcode} · UnitSkin/UnitFunc/UnitUI · {asset_mode_label}"
                                LOGGER.info("PREVIEW[%s] native unit resolved %s -> %s (%s)",serial,native_rawcode,resolved_ref,asset_preference)
                            if resolved_ref:
                                unit_refs=[resolved_ref,*[x for x in unit_refs if x.casefold()!=resolved_ref.casefold()]]
                            LOGGER.debug("PREVIEW[%s] unit candidates %s",serial,unit_refs[:12])

                    if not resolved_ref and not unit_refs:
                        raise FileNotFoundError(
                            self._msg(f"modelo nativo de {current_id or current_base} não encontrado nos profiles/skins do CASC", f"native model for {current_id or current_base} was not found in CASC profiles/skins")
                        )

                    if self._preview_stale(serial):
                        raise InterruptedError(f"preview {serial} superseded before MPQ open")
                    mpq_started=time.perf_counter()
                    with StormArchive(map_path, read_only=True) as arc:
                        LOGGER.debug("PREVIEW[%s] MPQ ready after %.1f ms",serial,(time.perf_counter()-mpq_started)*1000.0)
                        actual=model_path=None
                        if current_kind=="doodad" and doodad_refs:
                            refs_to_try=list(doodad_refs)
                        elif current_kind=="unit" and unit_refs:
                            refs_to_try=list(unit_refs)
                        else:
                            refs_to_try=[resolved_ref]
                        LOGGER.debug("PREVIEW[%s] candidates=%s",serial,refs_to_try[:12])
                        for candidate_ref in refs_to_try:
                            if self._preview_stale(serial):
                                raise InterruptedError(f"preview {serial} superseded before candidate {candidate_ref}")
                            actual,model_path=self._extract_model_bundle_from_arc(
                                arc,candidate_ref,tmp,files,missing,casc=casc,casc_preference=asset_preference,
                                serial=serial,casc_getter=shared_casc
                            )
                            if model_path:
                                resolved_ref=candidate_ref
                                break

                        # A stale/missing custom skin import should not leave the
                        # preview completely empty. Fall back to the object's
                        # Warcraft base art only after the explicit map model has
                        # genuinely failed. This is preview-only and never edits
                        # the object data inside the map.
                        if (
                            not model_path and current_kind == "unit" and current_table == "custom"
                            and current_base and current_base != current_id
                        ):
                            native_casc = shared_casc()
                            fallback_refs = unit_model_candidates(
                                native_casc, current_base, preference=asset_preference
                            ) if native_casc is not None else []
                            if fallback_refs:
                                LOGGER.warning(
                                    "PREVIEW[%s] custom model missing for %s (%s); falling back to base %s candidates=%s",
                                    serial, current_id, requested_ref, current_base, fallback_refs[:8],
                                )
                                for candidate_ref in fallback_refs:
                                    if self._preview_stale(serial):
                                        raise InterruptedError(f"preview {serial} superseded before base fallback {candidate_ref}")
                                    actual,model_path=self._extract_model_bundle_from_arc(
                                        arc,candidate_ref,tmp,files,missing,casc=casc,casc_preference=asset_preference,
                                        serial=serial,casc_getter=shared_casc
                                    )
                                    if model_path:
                                        resolved_ref=candidate_ref
                                        resolved_source=(
                                            self._msg(f"fallback visual da base {current_base} · modelo custom ausente · {asset_mode_label}", f"visual fallback from base {current_base} · custom model missing · {asset_mode_label}")
                                        )
                                        break
                    if not model_path:
                        attempted=", ".join(refs_to_try[:8])
                        raise FileNotFoundError(
                            self._msg(f"modelo não encontrado no MPQ nem no CASC: {attempted or resolved_ref}; kind={current_kind} table={current_table} id={current_id} source={source or '-'} mode={asset_preference}", f"model not found in MPQ or CASC: {attempted or resolved_ref}; kind={current_kind} table={current_table} id={current_id} source={source or '-'} mode={asset_preference}")
                        )
                    if self._preview_stale(serial):
                        raise InterruptedError(f"preview {serial} superseded after extraction")
                    self.preview_cache[preview_cache_key]=(list(files),model_path,actual or resolved_ref,list(missing))
                    display_label=(actual or resolved_ref) + f" · {asset_mode_label}" + (f" · {self._localize_source_text(resolved_source)}" if resolved_source else "")
                    elapsed=(time.perf_counter()-started)*1000.0
                    log_level=LOGGER.warning if elapsed>1800 else LOGGER.info
                    log_level(
                        "PREVIEW[%s] extracted id=%s files=%d missing=%d total=%.1f ms cache=%s",
                        serial,current_id,len(files),len(missing),elapsed,preview_cache_key
                    )
                    self.after(0,lambda:self._show_preview_files(serial,files,model_path,display_label,missing))
                except InterruptedError as exc:
                    LOGGER.info("PREVIEW[%s] cancelled: %s",serial,exc)
                except Exception as exc:
                    log_exception(LOGGER,f"preview extraction serial={serial} kind={current_kind} table={current_table} id={current_id} ref={requested_ref}",exc)
                    self.after(0,lambda e=exc:self._preview_failed(serial,requested_ref or current_id,e))
            threading.Thread(target=worker,daemon=True,name="wc3-preview-extract").start()

    def _ability_preview_empty(self,serial,ability_id,ability_name,missing=None):
            if serial!=self.preview_serial:return
            self._clear_preview_view()
            self._hide_preview_loading()
            self.preview_last_files=[]; self.preview_last_model=None; self.preview_last_scene=None
            suffix=self._msg(f" · {len(missing or [])} referência(s) tentada(s) sem modelo", f" · {len(missing or [])} attempted reference(s) had no model") if missing else ""
            self.preview_status_var.set(
                self._msg(f"SKILL {ability_id} · {ability_name or ability_id} · sem Art 3D detectável{suffix}", f"SKILL {ability_id} · {ability_name or ability_id} · no detectable 3D Art{suffix}")
            )

    def _preview_failed(self,serial,model_ref,exc):
            if serial!=self.preview_serial:return
            self._clear_preview_view()
            self._hide_preview_loading()
            self.preview_last_files=[]; self.preview_last_model=None; self.preview_last_scene=None
            err = self._localize_error_text(exc)
            self.preview_status_var.set(f"Preview: {model_ref} — {type(exc).__name__}: {err}")

    def _show_preview_files(self,serial,files,model_path,label,missing=None,scene=None):
            if serial!=self.preview_serial:return
            files=[Path(x) for x in files if Path(x).is_file()]
            if not files or not Path(model_path).is_file():
                self._hide_preview_loading()
                self.preview_status_var.set(self._msg("Preview sem arquivos válidos.", "Preview has no valid files."))
                return
            self.preview_last_files=files; self.preview_last_model=Path(model_path); self.preview_last_scene=scene
            viewer_started=time.perf_counter()
            url=prepare_viewer_url(files,self.preview_last_model,compact=True,scene=scene)
            session_ms=(time.perf_counter()-viewer_started)*1000.0
            nav_started=time.perf_counter()
            loaded=self._load_preview_url(url)
            nav_ms=(time.perf_counter()-nav_started)*1000.0
            LOGGER.info(
                "PREVIEW[%s] viewer handoff model=%s files=%d session=%.1f ms load_url=%.1f ms ok=%s",
                serial,Path(model_path).name,len(files),session_ms,nav_ms,loaded
            )
            self._hide_preview_loading()
            missing=missing or []
            tex=max(0,len(files)-1)
            suffix=self._msg(f" · {tex} textura(s)", f" · {tex} texture(s)") + (self._msg(f" · {len(missing)} referência(s) não encontrada(s)", f" · {len(missing)} missing reference(s)") if missing else "")
            self.preview_status_var.set(f"{label}{suffix}")

    def open_external_preview(self):
            if self.preview_last_model and self.preview_last_model.is_file():
                launch_viewer(self.preview_last_files,self.preview_last_model,scene=self.preview_last_scene)
                return
            if self.current_kind in {"unit","doodad"} and "model" in self.form_vars and self.form_vars["model"].get().strip():
                self.view_model_field("model")
                return
            self.open_viewer_picker()
