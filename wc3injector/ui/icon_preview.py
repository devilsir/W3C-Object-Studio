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

from ..debuglog import configure_logging, latest_log_path, log_exception
from ..editor import EditSession
from ..fields import FIELD_SETS, FIELD_BY_ID, TYPE_NAMES, NAME_TYPES
from ..injector import auto_assets
from ..modelscan import scan_model
from ..objectmod import Modification, TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING
from ..storm import StormArchive
from ..casc import CascStorage, CascError, detect_warcraft_install, remember_warcraft_install, get_shared_casc
from ..ability_native import resolve_profile_field
from ..unit_native import resolve_unit_visual
from ..viewer_server import launch_viewer, prepare_viewer_url
from ..visuals import best_sibling_visual, infer_icon_import, related_icon_variants
from ..webview_profile import prepare_webview2_profile
from ..i18n import tr as i18n_tr, localize_field_def, describe_field as describe_field_i18n, translate_category
from ..paths import ASSETS_DIR
from .editor_values import TYPE_MAP, KIND_LABELS, ABILITY_EFFECT_FIELDS, DEFAULT_NEW, coerce, value_text, field_value_text, coerce_field
from .widgets import ScrollForm, AdaptiveFieldRow
from .dialogs import RawDialog, NewObjectDialog

LOGGER, _SESSION_LOG = configure_logging()


class IconPreviewMixin:
    def _current_icon_reference(self):
            if self.current_kind not in {"unit","ability"} or not self.current_id:
                return None,""
            key="icon"
            native_fallback=None
            if key in self.form_vars and self.form_vars[key].get().strip():
                source=self.form_state.get(key,{}).get("source","")
                value=self.form_vars[key].get().strip()
                # Custom transformation/quest forms often inherit their *data* from
                # a Blizzard hero but intentionally use visuals copied from a sibling
                # custom form.  Keep the inherited native value visible in the Art
                # tab, but do not let Mountain King art beat a Fujitora sibling for
                # preview purposes.
                if self.current_kind=="unit" and self.current_table=="custom" and source.startswith("base nativa"):
                    native_fallback=(value,source)
                else:
                    return value,source or "override"
            fd=self._fields_for_kind(self.current_kind).get(key)
            if fd and native_fallback is None:
                inherited,source=self.inherited_mod_for(fd)
                if inherited is not None and str(inherited.value).strip():
                    if self.current_kind=="unit" and self.current_table=="custom" and source.startswith("base nativa"):
                        native_fallback=(str(inherited.value).strip(),source)
                    else:
                        return str(inherited.value).strip(),source
            if self.current_kind=="unit":
                sibling,source=self._sibling_visual_reference("uico")
                if sibling:
                    return sibling,source
    
            # Last-resort display inference: custom maps often import a BTN icon but
            # leave uico on a quest/transformation form blank.  Use name/model hints
            # only for the preview; never write this inferred path into w3u.
            if self.session:
                model_ref=""
                if self.current_kind in {"unit","doodad"}:
                    model_ref,_model_source=self._current_model_reference(allow_icon_inference=False)
                hints=[self.title_var.get(), self.current_id or "", model_ref or ""]
                paths=[e.archive_path for e in self.session.imports.entries]
                guessed=infer_icon_import(paths,hints,purpose="unit" if self.current_kind=="unit" else "ability")
                if guessed:
                    path,score,reason=guessed
                    LOGGER.info("Icon import inference for %s -> %s score=%.1f (%s)",self.current_id,path,score,reason)
                    return path,self._msg(f"ícone inferido dos imports · {reason}", f"icon inferred from imports · {reason}")
            if native_fallback is not None:
                return native_fallback
            return None, self._msg(f"herdado do Warcraft ({self.current_base or self.current_id})", f"inherited from Warcraft ({self.current_base or self.current_id})")

    @staticmethod
    def _native_icon_variant_refs(icon_ref: str) -> dict[str, str]:
            """Build likely Warcraft command-button variants for a CASC icon path."""
            ref=(icon_ref or "").strip().strip('"').replace('/', '\\')
            if not ref:
                return {}
            # Native profile paths occasionally omit an extension.
            if not Path(ref.replace('\\','/')).suffix:
                ref += '.blp'
            folder,_,name=ref.rpartition('\\')
            low_name=name.casefold()
            out={}
            if low_name.startswith('disbtn'):
                out['disabled']=ref
                normal_name='BTN'+name[6:]
                normal_folder=folder.replace('CommandButtonsDisabled','CommandButtons')
                out['normal']=(normal_folder+'\\' if normal_folder else '')+normal_name
            elif low_name.startswith('pasbtn'):
                out['passive']=ref
                normal_name='BTN'+name[6:]
                out['normal']=(folder+'\\' if folder else '')+normal_name
                disabled_folder=folder.replace('CommandButtons','CommandButtonsDisabled')
                out['disabled']=(disabled_folder+'\\' if disabled_folder else '')+'DISBTN'+name[6:]
            else:
                out['normal']=ref
                stem=name[3:] if low_name.startswith('btn') else name
                disabled_folder=folder.replace('CommandButtons','CommandButtonsDisabled')
                out['disabled']=(disabled_folder+'\\' if disabled_folder else '')+'DISBTN'+stem
                out['passive']=(folder+'\\' if folder else '')+'PASBTN'+stem
            return out

    def change_icon_from_preview(self, variant: str = "normal"):
            """Replace BTN/DISBTN/PASBTN by clicking the visible preview slot."""
            if not self.session or not self.current_id:
                messagebox.showinfo(self._msg("Ícone", "Icon"), self._msg("Abra um mapa e selecione uma unidade ou habilidade primeiro.", "Open a map and select a unit or ability first."), parent=self)
                return
            if self.current_kind not in {"unit", "ability"}:
                messagebox.showinfo(self._msg("Ícone", "Icon"), self._msg("Doodads não possuem o command-button de unidades/habilidades.", "Doodads do not have a unit/ability command-button icon."), parent=self)
                return
            if "icon" not in self.form_vars:
                messagebox.showerror(self._msg("Ícone", "Icon"), self._msg("O campo Art - Icon não está disponível neste objeto.", "The Art - Icon field is not available on this object."), parent=self)
                return

            variant=(variant or "normal").casefold()
            labels={"normal":"Normal BTN","disabled":"Disabled","passive":"Passive"}
            chosen=filedialog.askopenfilename(
                title=self._msg(f"Substituir ícone — {labels.get(variant,variant)}", f"Replace icon — {labels.get(variant,variant)}"),
                filetypes=[("Warcraft icon", "*.blp *.tga *.dds"), (self._msg("Todos", "All files"), "*.*")],
            )
            if not chosen:
                return
            src=Path(chosen)
            if src.suffix.casefold() not in {".blp", ".tga", ".dds"}:
                messagebox.showerror(self._msg("Ícone", "Icon"), self._msg("Use BLP, TGA ou DDS.", "Use BLP, TGA, or DDS."), parent=self)
                return

            try:
                current_ref=self.form_vars["icon"].get().strip()
                if not current_ref:
                    inherited,_source=self._current_icon_reference()
                    current_ref=str(inherited or "").strip()

                if variant == "normal":
                    stem=src.stem
                    low=stem.casefold()
                    if low.startswith("disbtn"):
                        stem="BTN"+stem[6:]
                    elif low.startswith("pasbtn"):
                        stem="BTN"+stem[6:]
                    elif not low.startswith("btn"):
                        stem="BTN"+stem
                    archive=f"war3mapImported\\ObjectStudio\\Icons\\{stem}{src.suffix.lower()}"
                    self.session.stage_asset(src,archive)
                    self.form_vars["icon"].set(archive)
                    self.status.set(self._msg(f"Ícone Normal staged: {archive}. Clique Aplicar/Salvar para gravar o campo.", f"Normal icon staged: {archive}. Click Apply/Save to write the field."))
                else:
                    if not current_ref:
                        messagebox.showinfo(self._msg("Ícone", "Icon"), self._msg("Defina primeiro o ícone Normal; depois clique em Disabled/Passive.", "Set the Normal icon first, then click Disabled/Passive."), parent=self)
                        return
                    refs=self._native_icon_variant_refs(current_ref)
                    target=refs.get(variant)
                    if not target:
                        raise ValueError(self._msg(f"não consegui derivar a variante {variant} de {current_ref}", f"could not derive the {variant} variant from {current_ref}"))
                    target_ext=Path(target.replace("\\","/")).suffix.casefold()
                    if target_ext and target_ext != src.suffix.casefold():
                        messagebox.showerror(
                            self._msg("Ícone", "Icon"),
                            self._msg(f"A variante precisa ter a mesma extensão do ícone principal ({target_ext}).\nArquivo escolhido: {src.suffix.lower()}", f"The variant must use the same extension as the main icon ({target_ext}).\nSelected file: {src.suffix.lower()}"),
                        )
                        return
                    self.session.stage_asset(src,target)
                    self.status.set(self._msg(f"Ícone {labels.get(variant,variant)} staged: {target}.", f"{labels.get(variant,variant)} icon staged: {target}."))

                LOGGER.info("Icon click replace id=%s kind=%s variant=%s source=%s", self.current_id, self.current_kind, variant, src)
                self.refresh_icon()
            except Exception as exc:
                self._error_box(self._msg("Ícone", "Icon"), f"change_icon_from_preview:{variant}", exc)

    def refresh_visuals(self):
            if self.embedded_view is None:
                # The right-panel refresh button also doubles as a viewer restart.
                self.after(10, lambda: self._init_embedded_viewer(force=True))
            # v3.9.7: keep the right panel focused on the actual visual assets.
            # Ability previews are FX-only; the old hero/caster test card is no longer
            # shown because it duplicated the 3D preview and made spell inspection noisy.
            if self.current_kind == "ability":
                self.icon_card.configure(text=self._t("icons_ability"))
            else:
                self.icon_card.configure(text=self._t("icons_unit") if self.current_kind == "unit" else self._t("icon_object"))
            if self.ability_test_card.winfo_manager():
                self.ability_test_card.pack_forget()
            # Start the 3D path first; icons are secondary and must not delay the
            # model by grabbing MPQ/CASC locks a few milliseconds earlier.
            self.refresh_preview()
            self.refresh_icon()

    def _clear_icon(self, message: str, source: str = ""):
            self.icon_photo=None
            self.icon_archive_path=None
            self.icon_variant_photos={}
            for variant,label in self.icon_variant_labels.items():
                try:
                    label.configure(image="",text="—")
                except Exception:
                    pass
                var=self.icon_variant_path_vars.get(variant)
                if var is not None:
                    var.set("")
            self.icon_status_var.set(message)
            self.visual_source_var.set(self._localize_source_text(source))

    @staticmethod
    def _decode_icon_photo(data: bytes, size: int):
            from io import BytesIO
            from PIL import Image, ImageTk
            img=Image.open(BytesIO(data)).convert("RGBA")
            img.thumbnail((size,size),Image.Resampling.LANCZOS)
            bg=Image.new("RGBA",(size,size),(20,24,32,255))
            x=(size-img.width)//2; y=(size-img.height)//2
            bg.alpha_composite(img,(x,y))
            return ImageTk.PhotoImage(bg)

    def refresh_icon(self):
            """Load icon variants, preferring map imports and falling back to WC3 CASC."""
            self.icon_serial += 1
            serial=self.icon_serial
            if not self.current_id:
                self._clear_icon(self._msg("Ícones: selecione um objeto", "Icons: select an object"))
                return
            icon_ref,source=self._current_icon_reference()
            native_raw=None
            if self._is_native_visual_source(source):
                if self.current_kind=="unit":
                    native_raw=self.current_id
                elif self.current_kind=="ability":
                    native_raw=self.current_base or self.current_id
            # A native unit can have its icon only in UnitFunc/UnitSkin inside CASC,
            # so a missing cached profile value is not a terminal condition anymore.
            if not icon_ref and not native_raw:
                self._clear_icon(
                    self._msg(f"Sem ícone custom no mapa; usa o ícone nativo de {self.current_base or self.current_id}.", f"No custom icon in the map; using the native icon for {self.current_base or self.current_id}."),
                    source,
                )
                return
            if not self.session:
                self._clear_icon(icon_ref or self._msg("Ícone nativo", "Native icon"),source)
                return
    
            imports=[e.archive_path for e in self.session.imports.entries]
            self._clear_icon(
                self._msg("Buscando ícone no MPQ/CASC…", "Looking up icon in MPQ/CASC…") if native_raw or self.current_table=="native" else self._msg("Carregando ícone…", "Loading icon…"),
                source,
            )
            map_path=self.session.map_path
            current_kind=self.current_kind
            current_table=self.current_table
            current_id=self.current_id
            native_visual=bool(current_table=="native" or native_raw or (source or "").startswith("base nativa") or "CASC" in (source or ""))
            asset_preference=self._model_asset_preference() if hasattr(self, "_model_asset_preference") else "hd"
    
            def worker():
                blobs={}; actuals={}; failures={}; casc=None
                resolved_ref=icon_ref or ""; resolved_source=source
                started=time.perf_counter()
                try:
                    if serial!=self.icon_serial:
                        return
                    # Open CASC early for native objects, or lazily if the MPQ misses.
                    if native_raw:
                        install=detect_warcraft_install()
                        if install:
                            try:
                                casc=get_shared_casc(install)
                                if current_kind=="unit":
                                    visual=resolve_unit_visual(casc,native_raw,preference=asset_preference)
                                    casc_icon=str(visual.get("icon","") or "").strip()
                                    icon_origin="UnitSkin/UnitFunc"
                                else:
                                    casc_icon,_field_source=resolve_profile_field(casc,native_raw,"aart")
                                    casc_icon=str(casc_icon or "").strip()
                                    icon_origin="AbilitySkin/AbilityFunc"
                                # Native rows should trust the user's installed CASC over
                                # the bundled/offline baseline. This matters when Blizzard
                                # changed a Reforged skin path between builds.
                                if casc_icon:
                                    resolved_ref=casc_icon
                                    resolved_source=f"CASC · {native_raw} · {icon_origin}"
                            except Exception:
                                LOGGER.warning("Native icon CASC lookup failed for %s",native_raw,exc_info=True)
                                casc=None
    
                    if not resolved_ref:
                        self.after(0,lambda:self._clear_icon(
                            self._msg(f"Ícone nativo de {current_id} não foi resolvido no CASC.", f"Native icon for {current_id} could not be resolved from CASC."),
                            resolved_source or f"Warcraft {current_id}",
                        ))
                        return
    
                    variants=related_icon_variants(imports,resolved_ref)
                    # For native Warcraft art the variants are not in war3map.imp.
                    # Derive BTN/DISBTN/PASBTN paths and let CASC tell us which exist.
                    if native_visual:
                        for key,value in self._native_icon_variant_refs(resolved_ref).items():
                            variants.setdefault(key,value)
                    if "normal" not in variants:
                        variants["normal"]=resolved_ref
    
                    with StormArchive(map_path, read_only=True) as arc:
                        for variant,ref in variants.items():
                            if serial!=self.icon_serial:
                                LOGGER.info("ICON[%s] cancelled before %s for %s",serial,variant,current_id)
                                return
                            data=None; actual=None
                            try:
                                actual=self._resolve_archive_path(arc,ref)
                                if actual:
                                    data=arc.read(actual)
                                else:
                                    if casc is None:
                                        install=detect_warcraft_install()
                                        if install:
                                            try:casc=get_shared_casc(install)
                                            except Exception:
                                                LOGGER.debug("CASC icon fallback unavailable",exc_info=True); casc=None
                                    if casc is not None:
                                        try:
                                            actual,data=casc.read_with_path(ref,preference=asset_preference)
                                        except Exception:
                                            data=None
                                if data is None:
                                    failures[variant]=self._msg(f"não encontrado no MPQ/CASC: {ref}", f"not found in MPQ/CASC: {ref}")
                                    continue
                                blobs[variant]=data; actuals[variant]=actual or ref
                            except Exception as exc:
                                failures[variant]=f"{type(exc).__name__}: {self._localize_error_text(exc)}"
                    LOGGER.debug("ICON[%s] ready id=%s variants=%d failures=%d total=%.1f ms",
                                 serial,current_id,len(blobs),len(failures),(time.perf_counter()-started)*1000.0)
                    self.after(0,lambda:self._show_icon_variants(serial,blobs,actuals,resolved_source,failures))
                except Exception as exc:
                    log_exception(LOGGER,f"icon variants extraction {resolved_ref or current_id}",exc)
                    self.after(0,lambda e=exc:self._icon_failed(serial,resolved_ref or current_id,resolved_source,e))
            self.after(70,lambda: threading.Thread(target=worker,daemon=True,name="wc3-icon-extract").start())

    def _show_icon_variants(self,serial: int,blobs: dict,actuals: dict,source: str,failures: dict):
            if serial!=self.icon_serial:return
            shown=[]
            self.icon_variant_photos={}
            for variant,label in self.icon_variant_labels.items():
                data=blobs.get(variant)
                if not data:
                    label.configure(image="",text="—")
                    self.icon_variant_path_vars[variant].set(self._msg("não encontrado", "not found") if variant in failures else "—")
                    continue
                try:
                    photo=self._decode_icon_photo(data,72)
                    self.icon_variant_photos[variant]=photo
                    label.configure(image=photo,text="")
                    actual=actuals.get(variant,"")
                    self.icon_variant_path_vars[variant].set(Path(actual.replace("\\","/")).name if actual else variant)
                    shown.append(variant)
                    if variant=="normal":
                        self.icon_photo=photo
                        self.icon_archive_path=actual
                except Exception as exc:
                    log_exception(LOGGER,f"decode {variant} icon",exc)
                    label.configure(image="",text="!")
                    self.icon_variant_path_vars[variant].set(type(exc).__name__)
            if shown:
                labels={"normal":"Normal","disabled":"Disabled","passive":"Passive"}
                self.icon_status_var.set(self._msg("Encontrados: " + ", ".join(labels.get(x,x) for x in shown) + ". Clique no ícone para substituir.", "Found: " + ", ".join(labels.get(x,x) for x in shown) + ". Click an icon to replace it."))
            else:
                self.icon_status_var.set(self._msg("O caminho de ícone foi resolvido, mas nenhuma variante pôde ser exibida.", "The icon path was resolved, but no variant could be displayed."))
            self.visual_source_var.set(self._localize_source_text(source))

    def _icon_failed(self,serial,icon_ref,source,exc):
            if serial!=self.icon_serial:return
            self._clear_icon(self._msg(
                f"Ícone: {icon_ref} — {type(exc).__name__}: {exc}",
                f"Icon: {icon_ref} — {type(exc).__name__}: {self._localize_error_text(exc)}",
            ),source)
