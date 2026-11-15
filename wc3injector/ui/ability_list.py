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


class AbilityListMixin:
    @staticmethod
    def _split_rawcodes(value: str) -> list[str]:
            out=[]; seen=set()
            for item in (value or "").replace(";",",").split(","):
                raw=item.strip()
                if len(raw)!=4 or raw in seen:
                    continue
                seen.add(raw); out.append(raw)
            return out

    def _ability_record_info(self, rawcode: str) -> tuple[str, str | None, str]:
            """Return (display name, icon ref, source) for a unit ability rawcode."""
            if not self.session:
                return rawcode,None,""
            table=None; rec=None
            for candidate in ("custom","original"):
                rec=self.session.get_record("ability",candidate,rawcode)
                if rec is not None:
                    table=candidate; break
            name=rawcode
            icon_ref=None
            source=self._msg("nativa do Warcraft", "native Warcraft")
            if rec is not None and table is not None:
                nmod,nsource,_=self.session.resolve_map_value("ability",table,rawcode,"anam",level=0,column=0)
                if nmod is None or not str(nmod.value).strip():
                    nmod,nsource,_=self.session.resolve_inherited_value("ability",table,rawcode,"anam",level=0,column=0)
                if nmod is not None and str(nmod.value).strip():
                    name=str(nmod.value).strip()
                else:
                    # Some custom spells store their human label only in tooltip.
                    for fid in ("atp1","aret"):
                        lvl=1 if fid=="atp1" else 0
                        tmod,_,_=self.session.resolve_map_value("ability",table,rawcode,fid,level=lvl,column=0)
                        if tmod is None or not str(tmod.value).strip():
                            tmod,_,_=self.session.resolve_inherited_value("ability",table,rawcode,fid,level=lvl,column=0)
                        if tmod is not None and str(tmod.value).strip():
                            name=str(tmod.value).strip(); break
                imod,isource,_=self.session.resolve_map_value("ability",table,rawcode,"aart",level=0,column=0)
                if imod is None or not str(imod.value).strip():
                    imod,isource,_=self.session.resolve_inherited_value("ability",table,rawcode,"aart",level=0,column=0)
                if imod is not None and str(imod.value).strip():
                    icon_ref=str(imod.value).strip(); source=isource
                else:
                    source=self._msg(f"ícone herdado do Warcraft ({rec.original_id})", f"icon inherited from Warcraft ({rec.original_id})")
            elif self.session.is_native_object("ability",rawcode):
                # Native spells are virtual rows and therefore have no w3a record.
                # Resolve their name/icon from the local CASC catalog instead.
                name=self.session.native_name("ability",rawcode)
                try:
                    imod,isource,_=self.session.resolve_inherited_value("ability","native",rawcode,"aart",level=0,column=0)
                except Exception:
                    imod=None; isource=f"CASC Warcraft ({rawcode})"
                if imod is not None and str(imod.value).strip():
                    icon_ref=str(imod.value).strip(); source=isource
                else:
                    source=isource
    
            if not icon_ref:
                paths=[e.archive_path for e in self.session.imports.entries]
                guessed=infer_icon_import(paths,[name,rawcode],purpose="ability")
                if guessed:
                    icon_ref,score,reason=guessed
                    source=self._msg(f"ícone inferido · {reason}", f"inferred icon · {reason}")
                    LOGGER.info("Ability icon inference %s (%s) -> %s score=%.1f",rawcode,name,icon_ref,score)
            return name,icon_ref,source

    def refresh_ability_list(self):
            """Render the selected unit's uabi/uhab as visual spell rows."""
            self.ability_icon_serial += 1
            serial=self.ability_icon_serial
            host=self.ability_list_host
            if host is None:
                return
            for child in host.winfo_children():
                child.destroy()
            self.ability_rows=[]
            self.ability_icon_photos={}
            if self.current_kind!="unit" or not self.current_id:
                ttk.Label(host,text=("Selecione uma unidade." if self.language=="pt" else "Select a unit."),style="Source.TLabel").pack(anchor="w")
                return
            normal=self._split_rawcodes(self.form_vars.get("normal_abilities",tk.StringVar()).get())
            hero=self._split_rawcodes(self.form_vars.get("hero_abilities",tk.StringVar()).get())
            merged=[]; pos={}
            for raw,kind_label in [(x,"Normal") for x in normal]+[(x,("Herói" if self.language=="pt" else "Hero")) for x in hero]:
                if raw in pos:
                    merged[pos[raw]][1]="Normal + Herói" if self.language=="pt" else "Normal + Hero"
                else:
                    pos[raw]=len(merged); merged.append([raw,kind_label])
            if not merged:
                ttk.Label(host,text=("Nenhuma habilidade gravada neste objeto (ou o campo é herdado apenas do Warcraft)." if self.language=="pt" else "No ability is stored on this object (or the field is inherited only from Warcraft)."),style="Source.TLabel").pack(anchor="w")
                return
    
            infos=[]
            for idx,(raw,kind_label) in enumerate(merged):
                name,icon_ref,source=self._ability_record_info(raw)
                row=ttk.Frame(host,padding=(2,4)); row.pack(fill="x")
                icon_host=tk.Frame(row,width=50,height=50,bg="#11151d",highlightthickness=1,highlightbackground="#343a46")
                icon_host.pack(side="left"); icon_host.pack_propagate(False)
                icon=tk.Label(icon_host,text="—",fg="#8f9bad",bg="#11151d",font=("Segoe UI",13,"bold"))
                icon.pack(fill="both",expand=True)
                body=ttk.Frame(row,padding=(8,0,0,0)); body.pack(side="left",fill="x",expand=True)
                ttk.Label(body,text=name,font=("Segoe UI",9,"bold")).pack(anchor="w")
                ttk.Label(body,text=f"{raw} · {kind_label}",style="Source.TLabel").pack(anchor="w")
                ttk.Label(body,text=self._localize_source_text(source),style="Source.TLabel",wraplength=430).pack(anchor="w")
                actions=ttk.Frame(row); actions.pack(side="right",padx=(6,0))
                ttk.Button(actions,text=self._msg("Editar spell", "Edit ability"),command=lambda r=raw:self._open_ability_from_unit(r)).pack(side="left")
                ttk.Button(actions,text=self._msg("Exportar ícones", "Export icons"),command=lambda r=raw:self.export_ability_icon_bundle(r)).pack(side="left",padx=(6,0))
                item={"raw":raw,"icon_ref":icon_ref,"widget":icon,"name":name,"source":source,"key":f"{idx}:{raw}"}
                self.ability_rows.append(item); infos.append(item)
                if idx < len(merged)-1:
                    ttk.Separator(host,orient="horizontal").pack(fill="x")
    
            if not self.session:
                return
            map_path=self.session.map_path
            def worker():
                loaded={}; casc=None; started=time.perf_counter()
                try:
                    if serial!=self.ability_icon_serial:
                        return
                    with StormArchive(map_path,read_only=True) as arc:
                        for item in infos:
                            if serial!=self.ability_icon_serial:
                                LOGGER.info("ABILITY_ICONS[%s] cancelled",serial)
                                return
                            ref=item["icon_ref"]
                            if not ref: continue
                            try:
                                actual=self._resolve_archive_path(arc,ref)
                                if actual:
                                    loaded[item["key"]]=(arc.read(actual),actual)
                                    continue
                                # Standard Warcraft abilities normally have no icon in
                                # the map MPQ. Fall back to the user's local CASC.
                                if casc is None:
                                    install=detect_warcraft_install()
                                    if install:
                                        try:casc=get_shared_casc(install)
                                        except Exception:
                                            LOGGER.debug("Ability icon CASC fallback unavailable",exc_info=True); casc=None
                                if casc is not None:
                                    try:
                                        native_actual,data=casc.read_with_path(ref)
                                        loaded[item["key"]]=(data,native_actual)
                                    except Exception:
                                        pass
                            except Exception:
                                LOGGER.debug("Ability icon read failed for %s",item["raw"],exc_info=True)
                    LOGGER.debug("ABILITY_ICONS[%s] ready loaded=%d total=%.1f ms",serial,len(loaded),(time.perf_counter()-started)*1000.0)
                    self.after(0,lambda:self._show_ability_icons(serial,loaded))
                except Exception as exc:
                    log_exception(LOGGER,"ability icon batch extraction",exc)
            # The 3D model is the primary interaction. Give its worker a small
            # head start so spell-icon I/O cannot stall every character click.
            self.after(120,lambda: threading.Thread(target=worker,daemon=True,name="wc3-ability-icons").start())

    def _show_ability_icons(self,serial: int,loaded: dict):
            if serial!=self.ability_icon_serial:return
            for item in self.ability_rows:
                pair=loaded.get(item["key"])
                if not pair: continue
                try:
                    photo=self._decode_icon_photo(pair[0],44)
                    self.ability_icon_photos[item["key"]]=photo
                    item["widget"].configure(image=photo,text="")
                except Exception:
                    LOGGER.debug("Ability icon decode failed %s",item["raw"],exc_info=True)

    def _open_ability_from_unit(self,rawcode: str):
            if not self.session:return
            self.kind_var.set("ability")
            self.refresh_tree(rawcode)
