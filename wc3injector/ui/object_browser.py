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

from ..debuglog import configure_logging, latest_log_path, log_exception
from ..editor import EditSession
from ..fields import FIELD_SETS, FIELD_BY_ID, TYPE_NAMES, NAME_TYPES
from ..injector import auto_assets
from ..modelscan import scan_model
from ..objectmod import Modification, TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING
from ..storm import StormArchive
from ..casc import CascStorage, CascError, detect_warcraft_install, remember_warcraft_install
from ..ability_native import resolve_profile_field
from ..unit_native import resolve_unit_visual
from ..viewer_server import launch_viewer, prepare_viewer_url
from ..visuals import best_sibling_visual, infer_icon_import, related_icon_variants
from ..unitsdoo import player_label
from ..webview_profile import prepare_webview2_profile
from ..i18n import tr as i18n_tr, localize_field_def, describe_field as describe_field_i18n, translate_category
from ..paths import ASSETS_DIR
from .editor_values import TYPE_MAP, KIND_LABELS, ABILITY_EFFECT_FIELDS, DEFAULT_NEW, coerce, value_text, field_value_text, coerce_field
from .widgets import ScrollForm, AdaptiveFieldRow
from .dialogs import RawDialog, NewObjectDialog

LOGGER, _SESSION_LOG = configure_logging()


class ObjectBrowserMixin:
    @staticmethod
    def _mods_signature(mods):
            return tuple((m.field_id, m.value_type, m.level, m.column, m.end_id, repr(m.value)) for m in mods)

    def _current_object_has_unapplied_changes(self) -> bool:
            if not self.current_id:
                return False
            if self.current_kind == "placed_hero":
                return bool(self._placed_hero_ui_dirty())
            # Text-entry edits do not touch working_mods until Apply/level change.
            for key, state in getattr(self, "form_state", {}).items():
                var = getattr(self, "form_vars", {}).get(key)
                if var is not None and var.get() != state.get("initial", ""):
                    return True
            snap = getattr(self, "_selection_snapshot_mods", None)
            if snap is not None and self._mods_signature(getattr(self, "working_mods", [])) != self._mods_signature(snap):
                return True
            snap_assets = getattr(self, "_selection_snapshot_assets", None)
            if self.session is not None and snap_assets is not None:
                def akey(a):
                    return (str(getattr(a, "source", "")), str(getattr(a, "archive_path", "")).casefold())
                if tuple(map(akey, self.session.staged_assets)) != tuple(map(akey, snap_assets)):
                    return True
            return False

    def _discard_unapplied_current(self) -> None:
            if self.current_kind == "placed_hero" and self._placed_snapshot is not None:
                try:
                    self.load_placed_hero_form(copy.deepcopy(self._placed_snapshot))
                except Exception:
                    LOGGER.debug("Could not restore placed-hero draft", exc_info=True)
                return
            if self.session is not None and hasattr(self, "_selection_snapshot_assets"):
                self.session.staged_assets = copy.deepcopy(self._selection_snapshot_assets)
            self.working_mods = copy.deepcopy(getattr(self, "_selection_snapshot_mods", []))

    def _confirm_leave_current_object(self) -> bool:
            if not self._current_object_has_unapplied_changes():
                return True
            name = self.title_var.get().strip() if hasattr(self, "title_var") else (self.current_id or "")
            title = self._msg("Alterações não salvas", "Unsaved changes")
            body = self._msg(
                f"Existem alterações não salvas em {name or self.current_id}.\n\nDeseja sair e descartar essas alterações?",
                f"There are unsaved changes in {name or self.current_id}.\n\nLeave and discard these changes?",
            )
            leave = messagebox.askyesno(title, body, parent=self)
            if leave:
                LOGGER.info("Discarding unapplied edits before object switch: %s:%s", self.current_kind, self.current_id)
                self._discard_unapplied_current()
            return leave

    def pick_map(self):
            path=filedialog.askopenfilename(filetypes=[("Warcraft III map","*.w3m *.w3x")])
            if not path:
                LOGGER.info("Open map cancelled")
                return
            LOGGER.info("OPEN MAP requested: %s", path)
            try:
                pp=Path(path)
                LOGGER.info("Map metadata: exists=%s size=%s suffix=%s", pp.exists(), pp.stat().st_size if pp.exists() else None, pp.suffix)
            except Exception:
                LOGGER.debug("Could not stat map before opening", exc_info=True)
            self._set_status("Lendo MPQ e object-data… veja o CMD para log ao vivo", "Reading MPQ and object data… see the console for live logs")
            try:
                LOGGER.info("Calling EditSession.load")
                self.session=EditSession.load(path)
                self.preview_cache.clear()
                self._placed_item_catalog_cache = None
                LOGGER.info("EditSession.load SUCCESS: %s", self.session.map_path)
                self.map_label.set(Path(path).name)
                counts = {k: len(self.session.list_records(k)) for k in ("unit", "ability", "doodad")}
                native_counts = {k: len(self.session.native_ids(k)) for k in ("unit", "ability", "doodad")}
                placed_units = len(self.session.placed_units_list())
                placed_heroes = len(self.session.placed_heroes())
                if not any(counts.values()):
                    self.status.set(self._msg(
                        f"Mapa carregado sem overrides próprios · {placed_units} unidades/itens colocados ({placed_heroes} heróis) · catálogo Warcraft: {native_counts['unit']} units/heroes, {native_counts['ability']} abilities e {native_counts['doodad']} doodads. Use o filtro à esquerda.",
                        f"Map loaded with no custom overrides · {placed_units} placed units/items ({placed_heroes} heroes) · Warcraft catalog: {native_counts['unit']} units/heroes, {native_counts['ability']} abilities and {native_counts['doodad']} doodads. Use the filter on the left.",
                    ))
                else:
                    native_status=self._msg(
                        f"dados nativos OK ({len(self.session.native_data.metadata)} campos)" if self.session.native_data and self.session.native_data.ready else "dados nativos incompletos",
                        f"native data OK ({len(self.session.native_data.metadata)} fields)" if self.session.native_data and self.session.native_data.ready else "native data incomplete",
                    )
                    self.status.set(self._msg(
                        f"Mapa carregado: {counts['unit']} units/heroes, {counts['ability']} spells, {counts['doodad']} doodads do mapa · {placed_units} unidades/itens colocados ({placed_heroes} heróis) · Warcraft base: {native_counts['unit']} units/heroes, {native_counts['ability']} spells, {native_counts['doodad']} doodads · {native_status}. Scripts/triggers protegidos por hash.",
                        f"Map loaded: {counts['unit']} units/heroes, {counts['ability']} spells, {counts['doodad']} map doodads · {placed_units} placed units/items ({placed_heroes} heroes) · Warcraft base: {native_counts['unit']} units/heroes, {native_counts['ability']} spells, {native_counts['doodad']} doodads · {native_status}. Scripts/triggers protected by hash validation.",
                    ))
                self.refresh_tree()
                # Placement editor is a separate workspace. Reset any previous
                # draft and populate it from this map's war3mapUnits.doo.
                self._placed_snapshot = None
                self._placed_creation_number = None
                self._placed_index = None
                try:
                    self.placed_title_var.set(self._msg("Selecione uma unidade colocada", "Select a placed unit"))
                    self.placed_meta_var.set("")
                    self.placed_apply_btn.state(["disabled"])
                except Exception:
                    LOGGER.debug("Could not reset Units on Map workspace", exc_info=True)
                # Keep list and terrain refresh independent. A malformed/duplicate
                # placement must never prevent war3map.w3e from being rendered.
                try:
                    self.refresh_placed_units_tree()
                except Exception:
                    LOGGER.exception("Could not refresh Units on Map placement list")
                try:
                    self.refresh_placed_map_view()
                except Exception:
                    LOGGER.exception("Could not refresh Units on Map terrain surface")
            except Exception as exc:
                self.session=None
                self._error_box(self._msg("Falha ao abrir mapa", "Failed to open map"), "pick_map / EditSession.load", exc)
                self.status.set(self._msg(f"Falha ao abrir mapa · veja {latest_log_path()}", f"Failed to open map · see {latest_log_path()}"))

    def record_name(self,kind,rec):
            name_id={"unit":"unam","ability":"anam","doodad":"dnam"}[kind]
            object_id=rec.custom_id if rec.custom_id.strip("\0") else rec.original_id
            table="custom" if rec.custom_id.strip("\0") else "original"
            for m in rec.modifications:
                if m.field_id==name_id and str(m.value).strip():
                    value=str(m.value).strip()
                    shown=self.session.resolve_string(value) if self.session else value
                    if not (kind=="ability" and table=="original" and shown.strip().casefold()==object_id.casefold()):
                        return shown
            # Reforged Object Editor commonly stores the human-readable custom
            # name in war3mapSkin.w3* while war3map.w3* only keeps gameplay data.
            if self.session:
                try:
                    skin_mod,_skin_source=self.session.skin_value(kind,table,object_id,name_id,level=0,column=0)
                    if skin_mod is not None and str(skin_mod.value).strip():
                        shown=self.session.resolve_string(skin_mod.value).strip()
                        if not (kind=="ability" and table=="original" and shown.casefold()==object_id.casefold()):
                            return shown
                except Exception:
                    LOGGER.debug("Map-skin object name lookup failed for %s:%s",kind,object_id,exc_info=True)
                try:
                    inherited,_source,_explicit=self.session.resolve_inherited_value(
                        kind,table,object_id,name_id,level=0,column=0
                    )
                    if inherited is not None and str(inherited.value).strip():
                        shown=self.session.resolve_string(inherited.value).strip()
                        if not (kind=="ability" and table=="original" and shown.casefold()==object_id.casefold()):
                            return shown
                except Exception:
                    LOGGER.debug("Inherited object name lookup failed for %s:%s",kind,object_id,exc_info=True)
                # If a custom/original override does not store its own name, use
                # the human-readable native base name instead of the rawcode.
                base_id = rec.original_id if getattr(rec, "original_id", "") else object_id
                if base_id and base_id.strip("\0") and self.session.is_native_object(kind, base_id):
                    base_name = self.session.native_name(kind, base_id)
                    if base_name and base_name != base_id:
                        return base_name
            return object_id

    def refresh_tree(self,select_id=None,trigger_on_select=True):
            self.tree.delete(*self.tree.get_children())
            self._update_tree_headings()
            if not self.session:return
            kind=self.kind_var.get(); query=self.search_var.get().strip().casefold()
            scope=getattr(self,"scope_var",tk.StringVar(value="all")).get()
            if kind == "placed_hero":
                rows=[]
                for placement_index, unit in enumerate(self.session.placed_units_list()):
                    if not self._placed_unit_is_hero(unit):
                        continue
                    name=self._placed_unit_display_name(unit.unit_id)
                    owner=player_label(unit.player,self.language)
                    hay=f"{unit.unit_id} {unit.creation_number} {name} {owner} {unit.hero_level}".casefold()
                    if query and query not in hay:
                        continue
                    rows.append((placement_index,unit,name,owner))
                col=self.tree_sort_column
                if col=="base": rows.sort(key=lambda x:(x[3].casefold(),x[2].casefold(),x[1].creation_number,x[0]),reverse=self.tree_sort_reverse)
                elif col=="table": rows.sort(key=lambda x:(x[1].hero_level,x[2].casefold(),x[0]),reverse=self.tree_sort_reverse)
                else: rows.sort(key=lambda x:(x[2].casefold(),x[1].creation_number,x[0]),reverse=self.tree_sort_reverse)
                for placement_index,unit,name,owner in rows:
                    iid=f"placed:{placement_index}"
                    self.tree.insert("","end",iid=iid,values=(f"#{unit.creation_number}  {name}  [{unit.unit_id}]",owner,str(unit.hero_level)))
                if select_id is not None:
                    iid=f"placed:{select_id}"
                    if self.tree.exists(iid):
                        self.tree.selection_set(iid); self.tree.see(iid)
                        if trigger_on_select:self.on_tree()
                return
            include_native = scope in {"all","warcraft"}
            rows=self.session.list_records(kind, include_native=include_native)
            if scope == "map":
                rows=[row for row in rows if row[0] != "native"]
            elif scope == "warcraft":
                rows=[row for row in rows if row[0] == "native"]
            def display_name(row):
                table,oid,_base,rec=row
                return self.session.native_name(kind,oid) if table=="native" else self.record_name(kind,rec)
            def table_label(table):
                if table=="custom": return self._t("custom")
                if table=="original": return self._t("override")
                return self._t("warcraft")
            filtered=[]
            for row in rows:
                table,oid,base,rec=row
                name=display_name(row); label=table_label(table)
                hay=f"{oid} {base} {name} {label}".casefold()
                if query and query not in hay:
                    continue
                filtered.append((row,name,label))
            col=self.tree_sort_column
            def sort_key(item):
                (table,oid,base,_rec),name,label=item
                if col=="base": return ((base or "").casefold(), name.casefold(), oid.casefold())
                if col=="table": return (label.casefold(), name.casefold(), oid.casefold())
                return (name.casefold(), oid.casefold())
            filtered.sort(key=sort_key, reverse=self.tree_sort_reverse)
            for (table,oid,base,rec),name,label in filtered:
                iid=f"{table}:{oid}"; self.tree.insert("", "end", iid=iid, values=(f"{oid}  {name}",base,label))
            if select_id:
                # Prefer a map override/custom row if one now exists after editing a native object.
                candidates=[iid for iid in self.tree.get_children() if iid.endswith(":"+select_id)]
                candidates.sort(key=lambda iid: 1 if iid.startswith("native:") else 0)
                if candidates:
                    iid=candidates[0]; self.tree.selection_set(iid); self.tree.see(iid)
                    if trigger_on_select:
                        self.on_tree()

    def on_tree(self,_=None):
            if not self.session or getattr(self, "_selection_change_guard", False):return
            sel=self.tree.selection()
            if not sel:return
            target_iid=sel[0]
            table,oid=target_iid.split(":",1); kind=self.kind_var.get()
            previous_iid=getattr(self,"_selected_tree_iid",None)
            same_current=bool(self.current_id and (kind,table,oid)==(self.current_kind,self.current_table,self.current_id))
            if same_current and target_iid==previous_iid:
                # Restoring the tree selection after a cancelled switch must not
                # reload the record and wipe the user's still-unapplied draft.
                return
            changing=bool(self.current_id and not same_current)
            if changing and not self._confirm_leave_current_object():
                if previous_iid and self.tree.exists(previous_iid):
                    self._selection_change_guard=True
                    try:
                        self.tree.selection_set(previous_iid); self.tree.see(previous_iid)
                    finally:
                        self._selection_change_guard=False
                return
            if kind == "placed_hero":
                if table != "placed":
                    return
                try:
                    placement_index=int(oid)
                except Exception:
                    return
                unit=self.session.placed_unit_at(placement_index)
                if unit is None or not self._placed_unit_is_hero(unit):
                    return
                creation=int(unit.creation_number)
                self._placed_index=placement_index
                self.current_kind="placed_hero"; self.current_table="placement"; self.current_id=str(placement_index); self.current_base=unit.unit_id
                self.working_mods=[]; self.current_level=1; self.level_var.set(1)
                self._selection_snapshot_mods=[]
                self._selection_snapshot_assets=copy.deepcopy(self.session.staged_assets)
                self._selected_tree_iid=target_iid
                display_name=self._placed_unit_display_name(unit.unit_id)
                self.title_var.set(display_name)
                self.meta_var.set(self._msg(
                    f"{unit.unit_id} · instância #{unit.creation_number} · {player_label(unit.player,self.language)} · nível {unit.hero_level}",
                    f"{unit.unit_id} · instance #{unit.creation_number} · {player_label(unit.player,self.language)} · level {unit.hero_level}",
                ))
                self.build_placed_hero_form(unit)
                try:
                    self._clear_icon(self._msg("Herói colocado: edite as propriedades no painel central.","Placed hero: edit instance properties in the center panel."))
                    self.preview_status_var.set(self._msg("Preview do tipo do herói permanece no Object Editor de Units / Heroes.","Hero type preview remains in the Units / Heroes Object Editor."))
                except Exception:
                    pass
                return
            rec=self.session.get_record(kind,table,oid)
            if not rec:return
            self.current_kind=kind; self.current_table=table; self.current_id=oid; self.current_base=rec.original_id
            self.working_mods=copy.deepcopy(rec.modifications); self.current_level=1; self.level_var.set(1)
            self._selection_snapshot_mods=copy.deepcopy(self.working_mods)
            self._selection_snapshot_assets=copy.deepcopy(self.session.staged_assets)
            self._selected_tree_iid=target_iid
            active_ids={fd.id for fd in self._fields_for_kind(kind).values()}
            curated=sum(1 for m in self.working_mods if m.field_id in active_ids)
            display_name=self.session.native_name(kind,oid) if table=="native" else self.record_name(kind,rec)
            self.title_var.set(display_name)
            if table=="native":
                self.meta_var.set(f"{oid} · " + self._msg("objeto padrão do Warcraft · sem override no mapa", "standard Warcraft object · no map override"))
            else:
                self.meta_var.set(self._msg(
                    f"{oid}  · base {rec.original_id} · {table} · {len(self.working_mods)} overrides ({curated} campos da UI)",
                    f"{oid}  · base {rec.original_id} · {table} · {len(self.working_mods)} overrides ({curated} UI fields)",
                ))
            self.build_form(kind); self.load_form(); self.refresh_raw(); self.refresh_visuals(); self.refresh_ability_list()

