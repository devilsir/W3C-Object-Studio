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
from ..webview_profile import prepare_webview2_profile
from ..i18n import tr as i18n_tr, localize_field_def, describe_field as describe_field_i18n, translate_category
from ..paths import ASSETS_DIR
from .editor_values import TYPE_MAP, KIND_LABELS, ABILITY_EFFECT_FIELDS, DEFAULT_NEW, coerce, value_text, field_value_text, coerce_field
from .widgets import ScrollForm, AdaptiveFieldRow
from .dialogs import RawDialog, NewObjectDialog

LOGGER, _SESSION_LOG = configure_logging()


class ArchiveAssetResolverMixin:
    @staticmethod
    def _norm_archive_path(path: str) -> str:
            return re.sub(r"\\+", r"\\", (path or "").replace("/","\\")).lstrip("\\")

    def _candidate_import_paths(self, requested: str, near: str | None = None) -> list[str]:
            """Return likely MPQ paths, ordered from strongest to weakest match.
    
            Texture packs often contain repeated generic basenames such as
            ``body.blp``.  When the exact MDX reference is missing, prefer an import
            that shares the model's archive directory before falling back to plain
            basename matching.
            """
            req=self._norm_archive_path(requested)
            near=self._norm_archive_path(near or "")
            out=[]
            seen=set()
            def add(x):
                x=self._norm_archive_path(x)
                cf=x.casefold()
                if x and cf not in seen:
                    seen.add(cf); out.append(x)
    
            add(req)
            req_name=Path(req.replace("\\","/")).name
            req_base=req_name.casefold()
            near_dir=near.rsplit("\\",1)[0] if "\\" in near else ""
            if near_dir and req_name:
                add(near_dir+"\\"+req_name)
                # Some MDX files use a relative subdirectory such as Textures\\x.blp.
                if "\\" in req:
                    add(near_dir+"\\"+req)
            if req_name:
                add("war3mapImported\\"+req_name)
    
            ranked=[]
            if self.session:
                near_cf=near_dir.casefold()
                req_cf=req.casefold()
                for idx,ent in enumerate(self.session.imports.entries):
                    ap=self._norm_archive_path(ent.archive_path)
                    ap_cf=ap.casefold()
                    name_cf=Path(ap.replace("\\","/")).name.casefold()
                    score=-idx/1000000.0
                    if ap_cf==req_cf:
                        score+=10000
                    elif req_cf and ap_cf.endswith("\\"+req_cf):
                        score+=9000
                    elif req_base and name_cf==req_base:
                        score+=1000
                        if near_cf:
                            # Reward a long shared directory prefix with the model.
                            a=ap_cf.split("\\")[:-1]; b=near_cf.split("\\")
                            common=0
                            for x,y in zip(a,b):
                                if x!=y: break
                                common+=1
                            score+=common*100
                    else:
                        continue
                    ranked.append((score,ap))
            for _score,ap in sorted(ranked,key=lambda x:x[0],reverse=True):
                add(ap)
            return out

    def _resolve_archive_path(self,arc: StormArchive,requested: str,near: str | None = None) -> str | None:
            for candidate in self._candidate_import_paths(requested,near=near):
                try:
                    if arc.has(candidate):
                        return candidate
                except Exception:
                    LOGGER.debug("arc.has failed while resolving %s",candidate,exc_info=True)
            return None

    def _current_record(self):
            if not self.session or not self.current_id:
                return None
            return self.session.get_record(self.current_kind,self.current_table,self.current_id)

    def _sibling_visual_reference(self, field_id: str):
            if not self.session or self.current_kind != "unit" or not self.current_id or not self.current_base:
                return None, ""
            match=best_sibling_visual(
                self.session.list_records("unit"),
                self.current_id,
                self.current_base,
                self.working_mods,
                field_id,
                self.title_var.get(),
            )
            if not match:
                return None, ""
            source=f"visual inferido de {match.object_id} · {match.reason}"
            LOGGER.info("Visual fallback %s for %s <- %s score=%.1f (%s)",field_id,self.current_id,match.object_id,match.score,match.reason)
            return match.value,source

    @staticmethod
    def _model_asset_refs(model_ref: str) -> list[str]:
            """Return model path candidates, including extension fallbacks for CASC."""
            ref=(model_ref or '').strip().strip('"').replace('/', '\\')
            while '\\\\' in ref:
                ref=ref.replace('\\\\','\\')
            if not ref:
                return []
            low=ref.casefold(); out=[]
            def add(v):
                if v and v.casefold() not in {x.casefold() for x in out}: out.append(v)
            add(ref)
            if low.endswith('.mdl'):
                add(ref[:-4]+'.mdx')
            elif low.endswith('.mdx'):
                add(ref[:-4]+'.mdl')
            else:
                add(ref+'.mdx'); add(ref+'.mdl')
            return out

    def _is_native_visual_source(self, source: str = '') -> bool:
            return bool(
                self.current_table == 'native'
                or (source or '').startswith('base nativa')
                or 'CASC' in (source or '')
            )
