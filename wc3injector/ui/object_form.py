from __future__ import annotations

import copy
import os
import re
import subprocess
import tempfile
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from ..debuglog import configure_logging, latest_log_path, log_exception, log_resource_snapshot
from ..editor import EditSession
from ..fields import FIELD_SETS, FIELD_BY_ID, TYPE_NAMES, NAME_TYPES
from ..injector import auto_assets
from ..modelscan import scan_model
from ..objectmod import Modification, TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING
from ..storm import StormArchive
from ..casc import CascStorage, CascError, detect_warcraft_install, remember_warcraft_install
from ..ability_native import resolve_profile_field
from ..unit_native import resolve_unit_visual
from ..doodad_native import build_doodad_field_defs
from ..viewer_server import launch_viewer, prepare_viewer_url
from ..visuals import best_sibling_visual, infer_icon_import, related_icon_variants
from ..webview_profile import prepare_webview2_profile
from ..i18n import tr as i18n_tr, localize_field_def, describe_field as describe_field_i18n, translate_category
from ..paths import ASSETS_DIR
from .editor_values import TYPE_MAP, KIND_LABELS, ABILITY_EFFECT_FIELDS, DEFAULT_NEW, coerce, value_text, field_value_text, coerce_field
from .widgets import ScrollForm, AdaptiveFieldRow
from .dialogs import RawDialog, NewObjectDialog

LOGGER, _SESSION_LOG = configure_logging()


class ObjectFormMixin:
    def _fields_for_kind(self, kind):
            """Return World-Editor-style fields, enriched from UnitMetaData.slk."""
            base=dict(FIELD_SETS[kind])
            if kind == "doodad" and self.session and self.session.native_doodad_metadata:
                dynamic=build_doodad_field_defs(self.session.native_doodad_metadata,self.language)
                # Preserve semantic keys used elsewhere (model/min_scale/etc.) while
                # replacing their definitions with the full DoodadMetaData schema.
                used_ids=set()
                for key,old in list(base.items()):
                    richer=dynamic.get(old.id)
                    if richer is not None:
                        base[key]=richer
                    used_ids.add(old.id)
                for fid,fd in dynamic.items():
                    if fid not in used_ids:
                        base[fid]=fd
                return base
            if kind != "unit" or not self.session or not self.session.native_data or not self.session.native_data.metadata:
                return {key: localize_field_def(fd, self.language) for key, fd in base.items()}
            hero=bool((self.current_base or self.current_id or "")[:1].isupper())
            dynamic=self.session.native_data.field_defs(hero=hero)
            # Preserve stable semantic keys used by model/icon code, but replace
            # their labels/types with metadata-backed definitions when available.
            used_ids=set()
            for key,old in list(base.items()):
                richer=dynamic.get(old.id)
                if richer is not None:
                    base[key]=richer
                used_ids.add(old.id)
            for fid,fd in dynamic.items():
                if fid not in used_ids:
                    base[fid]=fd
            return {key: localize_field_def(fd, self.language) for key, fd in base.items()}

    def build_form(self,kind):
            # Placed-hero editor temporarily reuses the shared Apply button.
            # Restore normal Object Editor semantics whenever a definition is selected.
            try:
                self.apply_btn.configure(text=self._t("apply_object"), command=self.apply_current)
            except Exception:
                pass
            fields=self._fields_for_kind(kind)
            signature=(
                kind,
                self.language,
                tuple(
                    (key, fd.id, fd.category, fd.type, bool(getattr(fd,"levelled",False)), getattr(fd,"editor_type",""), fd.label)
                    for key,fd in fields.items()
                ),
            )
            if getattr(self,"_form_signature",None)==signature and self.form_defs:
                LOGGER.debug(
                    "FORM reuse kind=%s object=%s fields=%d tabs=%d",
                    kind,self.current_id,len(self.form_defs),len(self.notebook.tabs())
                )
                log_resource_snapshot(LOGGER,f"form-reuse kind={kind} id={self.current_id}")
                return

            # ``Notebook.forget`` only hides a tab; it does NOT destroy the child
            # widget tree.  Rebuilding the 200+ field Object Editor form on every
            # selection therefore leaked thousands of Tk/USER objects until Windows
            # eventually aborted with ``Failed to create the menu window``.
            destroyed=0
            for tab in list(self.notebook.tabs()):
                try:
                    widget=self.nametowidget(tab)
                except Exception:
                    widget=None
                self.notebook.forget(tab)
                if widget is not None and widget is not self.raw_frame:
                    try:
                        widget.destroy(); destroyed+=1
                    except Exception:
                        LOGGER.debug("Could not destroy stale form tab %s",tab,exc_info=True)

            self.form_vars={}; self.form_defs=[]; self.form_state={}; self.form_source_vars={}
            self.ability_list_host=None
            self.ability_rows=[]
            self.ability_icon_photos={}
            categories=[]
            for fd in fields.values():
                if fd.category not in categories: categories.append(fd.category)
            for category in categories:
                sf=ScrollForm(self.notebook); self.notebook.add(sf,text=category)
                # Art exports are object-level rather than tied to one field. For
                # abilities this can include several Caster/Target/Effect/Missile
                # models, so keep one clear action at the top of the Art tab.
                if category == translate_category("Arte", self.language):
                    export_bar=ttk.Frame(sf.inner)
                    export_bar.pack(fill="x", pady=(2, 10))
                    ttk.Button(
                        export_bar,
                        text=self._msg("EXPORTAR MDX/MDL + TEXTURAS (.ZIP)", "EXPORT MDX/MDL + TEXTURES (.ZIP)"),
                        command=self.export_current_art_bundle,
                    ).pack(side="left")
                    ttk.Label(
                        export_bar,
                        text=self._msg(
                            "Mantém as pastas do Warcraft e inclui todas as texturas referenciadas.",
                            "Keeps Warcraft folders and includes all referenced textures.",
                        ),
                        style="Source.TLabel",
                    ).pack(side="left", padx=(10, 0))
                    ttk.Separator(sf.inner, orient="horizontal").pack(fill="x", pady=(0, 5))
                rows=[(key,fd) for key,fd in fields.items() if fd.category==category]
                for key,fd in rows:
                    var=tk.StringVar(); self.form_vars[key]=var; self.form_defs.append((key,fd))
                    source_var=tk.StringVar(value=""); self.form_source_vars[key]=source_var
                    actions=[]
                    if fd.id in {"umdl","dmod","dfil"}:
                        actions=[
                            (self._t("import_model"), lambda k=key:self.import_model(k)),
                            (self._t("preview"), lambda k=key:self.view_model_field(k)),
                        ]
                    row=AdaptiveFieldRow(
                        sf.inner, label=fd.label, variable=var, source_variable=source_var,
                        help_text=fd.help or "", popup_help=describe_field_i18n(fd, self.language), editor_type=getattr(fd,"editor_type",""), actions=actions,
                    )
                    row.pack(fill="x", expand=True)
                    ttk.Separator(sf.inner, orient="horizontal").pack(fill="x", pady=(1, 2))
                if kind=="unit" and category==translate_category("Habilidades", self.language):
                    card=ttk.LabelFrame(sf.inner,text=self._t("unit_abilities"),padding=8)
                    card.pack(fill="both",expand=True,pady=(12,4))
                    self.ability_list_host=ttk.Frame(card)
                    self.ability_list_host.pack(fill="both",expand=True)
                    ttk.Label(self.ability_list_host,text=self._t("select_unit_abilities"),style="Source.TLabel",justify="left",wraplength=640).pack(anchor="w",fill="x")
            self.notebook.add(self.raw_frame,text=self._t("raw_fields"))
            if kind=="ability": self.level_box.pack(side="left",padx=10)
            else: self.level_box.pack_forget()
            self._form_signature=signature
            LOGGER.info(
                "FORM rebuilt kind=%s object=%s fields=%d categories=%d destroyed_tabs=%d",
                kind,self.current_id,len(self.form_defs),len(categories),destroyed
            )
            log_resource_snapshot(LOGGER,f"form-rebuilt kind={kind} id={self.current_id}")

    def _field_level(self,fd):
            return self.current_level if (self.current_kind=="ability" and fd.levelled) else 0

    def mod_for(self,fd):
            """Return a modification explicitly stored on the selected object."""
            extended=self.current_kind in {"ability","doodad"}
            level=self._field_level(fd)
            for m in self.working_mods:
                if m.field_id!=fd.id: continue
                if not extended: return m
                if m.level==level and m.column==0: return m
            return None

    def inherited_mod_for(self,fd):
            if not self.session or not self.current_id:
                return None, ""
            level=self._field_level(fd)
            # The selected object's Reforged skin row is logically "own art/text"
            # even though it lives outside war3map.w3*. Surface it before the
            # parent/native chain so models/icons/names are not mistaken for stock.
            skin_mod,skin_source=self.session.skin_value(
                self.current_kind,self.current_table,self.current_id,fd.id,level=level,column=0
            )
            if skin_mod is not None:
                # Some Reforged skin tables serialize the rawcode itself as the
                # display Name for editor-only original abilities (ACnr -> ACnr).
                # Treat that as a placeholder so the actual Blizzard profile name
                # can surface in the UI instead of looking unnamed.
                if self.current_kind == "ability" and fd.id == "anam":
                    shown = self.session.resolve_string(skin_mod.value).strip()
                    if shown.casefold() != str(self.current_id).casefold():
                        return skin_mod,skin_source
                else:
                    return skin_mod,skin_source
            mod,source,_explicit=self.session.resolve_inherited_value(
                self.current_kind,self.current_table,self.current_id,fd.id,level=level,column=0
            )
            return mod,source

    def load_form(self):
            if not self.current_id:return
            self.form_state={}
            for key,fd in self.form_defs:
                own=self.mod_for(fd)
                if own is not None:
                    text=field_value_text(fd,own.value)
                    self.form_vars[key].set(text)
                    self.form_source_vars.get(key,tk.StringVar()).set(self._t("override"))
                    self.form_state[key]={"explicit":True,"initial":text,"source":"override","original_mod":copy.deepcopy(own)}
                    continue
                inherited,source=self.inherited_mod_for(fd)
                if inherited is not None:
                    text=field_value_text(fd,inherited.value)
                    self.form_vars[key].set(text)
                    self.form_source_vars.get(key,tk.StringVar()).set(self._localize_source_text(source))
                    self.form_state[key]={"explicit":False,"initial":text,"source":source,"inherited_value":True}
                else:
                    self.form_vars[key].set("")
                    self.form_source_vars.get(key,tk.StringVar()).set(self._localize_source_text(source) or self._msg("herdado do jogo", "inherited from game"))
                    self.form_state[key]={"explicit":False,"initial":"","source":source,"inherited_value":False}

    def save_form_to_working(self):
            if not self.current_id:return
            extended=self.current_kind in {"ability","doodad"}
            for key,fd in self.form_defs:
                raw=self.form_vars[key].get()
                level=self._field_level(fd)
                col=0
                state=self.form_state.get(key,{})
                def matches(m):
                    if m.field_id!=fd.id:return False
                    if not extended:return True
                    return m.level==level and m.column==col
                self.working_mods=[m for m in self.working_mods if not matches(m)]
    
                # Unchanged explicit mods are put back byte-semantically, including
                # meaningful empty strings such as uhab="".  Without this, merely
                # selecting/applying Fujitora could accidentally restore Hmkg hero
                # abilities by deleting the empty uhab override.
                if state.get("explicit") and raw == state.get("initial",""):
                    original=state.get("original_mod")
                    if original is not None:
                        self.working_mods.append(copy.deepcopy(original))
                    continue
    
                # A visible inherited value is display-only until the user changes
                # it. This prevents selection from creating redundant overrides.
                if not state.get("explicit") and raw == state.get("initial",""):
                    continue
    
                # Empty strings are valid object-data values; empty numeric fields
                # mean "remove this override / inherit". Raw Fields remains the
                # precise escape hatch for unusual cases.
                if raw.strip()!="" or fd.type==TYPE_STRING:
                    self.working_mods.append(Modification(fd.id,fd.type,coerce_field(fd,raw),level,col))

    def change_level(self):
            if self.current_kind!="ability" or not self.current_id:return
            try:new=max(1,int(self.level_var.get()))
            except Exception:new=1
            if new==self.current_level:return
            try:self.save_form_to_working()
            except Exception as exc:
                messagebox.showerror(
                    self._msg("Valor inválido", "Invalid value"),
                    self._localize_error_text(exc), parent=self
                ); self.level_var.set(self.current_level); return
            self.current_level=new; self.load_form(); self.refresh_raw()

    def apply_current(self):
            if not self.session or not self.current_id:
                return False
            try:
                self.save_form_to_working()
                was_native=(self.current_table=="native")
                self.session.set_record(self.current_kind,self.current_table,self.current_id,copy.deepcopy(self.working_mods),self.current_base)
                if was_native:
                    self.current_table="original"
                # This object is now safely stored in the in-memory EditSession.
                # Reset the per-object draft baseline so switching characters does
                # not warn about edits that have already been applied.
                self._selection_snapshot_mods=copy.deepcopy(self.working_mods)
                self._selection_snapshot_assets=copy.deepcopy(self.session.staged_assets)
                self._selected_tree_iid=f"{self.current_table}:{self.current_id}"
                self.load_form()
                self.refresh_raw()
                self.status.set(self._msg(
                    f"{self.current_id}: alterações aplicadas em memória. Clique SALVAR para gravar o mapa." + (" Foi criado um override do objeto padrão do Warcraft." if was_native else ""),
                    f"{self.current_id}: changes applied in memory. Click SAVE to write the map." + (" A standard Warcraft object override was created." if was_native else ""),
                ))
                self.refresh_tree(self.current_id)
                return True
            except Exception as exc:
                self._error_box(self._msg("Não foi possível aplicar", "Could not apply changes"), "apply_current", exc)
                return False
