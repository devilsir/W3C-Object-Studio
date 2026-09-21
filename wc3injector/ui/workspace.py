from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
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
from ..casc import CascStorage, CascError, detect_warcraft_install, remember_warcraft_install, get_shared_casc, reset_shared_casc
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


class WorkspaceMixin:
    def _build(self):
            style=ttk.Style(self)
            try: style.theme_use("vista")
            except tk.TclError: pass
            try:
                style.configure("Source.TLabel", foreground="#6b7280", font=("Segoe UI",8))
            except tk.TclError:
                pass
            top=ttk.Frame(self,padding=(12,10)); top.pack(fill="x")
            self.map_label=tk.StringVar(value=self._t("no_map"))
            self.open_map_btn=ttk.Button(top,text=self._t("open_map"),command=self.pick_map); self.open_map_btn.pack(side="left")
            self.save_btn=ttk.Button(top,text=self._t("save"),command=lambda:self.save(False)); self.save_btn.pack(side="left",padx=6)
            self.save_as_btn=ttk.Button(top,text=self._t("save_as"),command=lambda:self.save(True)); self.save_as_btn.pack(side="left")
            ttk.Separator(top,orient="vertical").pack(side="left",fill="y",padx=12)
            self.new_object_btn=ttk.Button(top,text=self._t("new_object"),command=self.new_object); self.new_object_btn.pack(side="left")
            self.edit_base_btn=ttk.Button(top,text=self._t("edit_base"),command=self.edit_base_rawcode); self.edit_base_btn.pack(side="left",padx=6)
            self.asset_import_btn=ttk.Button(top,text=self._msg("IMPORTAR ASSETS…","IMPORT ASSETS…"),command=self.open_asset_importer); self.asset_import_btn.pack(side="left",padx=(0,6))
            self.open_log_btn=ttk.Button(top,text=self._t("open_log"),command=self.open_log); self.open_log_btn.pack(side="left",padx=(0,6))
            self.credits_btn=ttk.Button(top,text=self._t("credits"),command=self.show_credits); self.credits_btn.pack(side="left",padx=(0,0))
            ttk.Label(top,textvariable=self.map_label).pack(side="right")
            lang_box=ttk.Frame(top); lang_box.pack(side="right",padx=(0,12))
            br=self.flag_images.get("pt"); us=self.flag_images.get("en")
            self.lang_pt_btn=ttk.Button(lang_box,text="PT",image=br,compound="left",command=lambda:self._set_language("pt"),width=5)
            self.lang_pt_btn.pack(side="left",padx=(0,4))
            self.lang_en_btn=ttk.Button(lang_box,text="EN",image=us,compound="left",command=lambda:self._set_language("en"),width=5)
            self.lang_en_btn.pack(side="left")

            # Main work areas are deliberately separated. Object definitions
            # (war3map.w3*) and placed instances (war3mapUnits.doo) are different
            # data layers in Warcraft III and mixing them in one type combobox was
            # confusing and made the center form jump between unrelated editors.
            self.main_tabs=ttk.Notebook(self)
            self.main_tabs.pack(fill="both",expand=True,padx=12,pady=(0,12))
            self.object_editor_page=ttk.Frame(self.main_tabs)
            self.placed_units_page=ttk.Frame(self.main_tabs)
            self.main_tabs.add(self.object_editor_page,text=self._msg("OBJECT EDITOR","OBJECT EDITOR"))
            self.main_tabs.add(self.placed_units_page,text=self._msg("UNIDADES NO MAPA","UNITS ON MAP"))
            self.main_tabs.bind("<<NotebookTabChanged>>",self._main_workspace_changed)

            pan=ttk.Panedwindow(self.object_editor_page,orient="horizontal"); pan.pack(fill="both",expand=True)
            left=ttk.Frame(pan,width=350); center=ttk.Frame(pan); visual=ttk.Frame(pan,width=410)
            pan.add(left,weight=0); pan.add(center,weight=1); pan.add(visual,weight=0)

            # LEFT: object browser only.  The preview was moved out of this pane in
            # v2.7 so the list stays tall and the visual workspace gets a dedicated
            # column on the right.
            toolbar=ttk.Frame(left,padding=(0,0,8,8)); toolbar.pack(fill="x")
            self.kind_var=tk.StringVar(value="unit")
            self.kind_display_var=tk.StringVar(value=self._t("kind_unit"))
            self.kind_combo=ttk.Combobox(toolbar,textvariable=self.kind_display_var,values=self._kind_values(),state="readonly",width=20)
            self.kind_combo.pack(side="left"); self.kind_combo.current(0); self.kind_combo.bind("<<ComboboxSelected>>",self._kind_selected)
            self.scope_var=tk.StringVar(value="all")
            self.scope_display_var=tk.StringVar(value=self._t("scope_all"))
            self.scope_combo=ttk.Combobox(toolbar,textvariable=self.scope_display_var,values=self._scope_values(),state="readonly",width=10)
            self.scope_combo.pack(side="left",padx=(6,0)); self.scope_combo.current(0); self.scope_combo.bind("<<ComboboxSelected>>",self._scope_selected)
            self.search_var=tk.StringVar(); search=ttk.Entry(toolbar,textvariable=self.search_var); search.pack(side="left",fill="x",expand=True,padx=(6,0)); search.bind("<KeyRelease>",lambda e:self.refresh_tree())

            tree_host=ttk.Frame(left); tree_host.pack(fill="both",expand=True,padx=(0,8))
            tree_host.rowconfigure(0,weight=1); tree_host.columnconfigure(0,weight=1)
            self.tree=ttk.Treeview(tree_host,columns=("name","base","table"),show="headings",selectmode="browse")
            tree_v=ttk.Scrollbar(tree_host,orient="vertical",command=self.tree.yview)
            tree_h=ttk.Scrollbar(tree_host,orient="horizontal",command=self.tree.xview)
            self.tree.configure(yscrollcommand=tree_v.set,xscrollcommand=tree_h.set)
            self.tree.grid(row=0,column=0,sticky="nsew"); tree_v.grid(row=0,column=1,sticky="ns"); tree_h.grid(row=1,column=0,sticky="ew")
            self.tree.column("name",width=225,minwidth=150); self.tree.column("base",width=58,minwidth=50,anchor="center"); self.tree.column("table",width=78,minwidth=68,anchor="center")
            self._update_tree_headings()
            self.tree.bind("<<TreeviewSelect>>",self.on_tree)
            left_actions=ttk.Frame(left,padding=(0,8,8,4)); left_actions.pack(fill="x")
            self.duplicate_btn=ttk.Button(left_actions,text=self._t("duplicate"),command=self.duplicate_object); self.duplicate_btn.pack(side="left")
            self.delete_override_btn=ttk.Button(left_actions,text=self._t("delete_override"),command=self.delete_object); self.delete_override_btn.pack(side="left",padx=6)

            # CENTER: object editor.
            header=ttk.Frame(center,padding=(10,0,8,8)); header.pack(fill="x")
            header_top=ttk.Frame(header); header_top.pack(fill="x")
            header_actions=ttk.Frame(header); header_actions.pack(fill="x",pady=(4,0))
            self.title_var=tk.StringVar(value=self._t("select_object"))
            self.meta_var=tk.StringVar(value="")
            ttk.Label(header_top,textvariable=self.title_var,font=("Segoe UI",15,"bold")).pack(side="left")
            ttk.Label(header_top,textvariable=self.meta_var).pack(side="left",padx=14,fill="x",expand=True)
            # Keep Spell level on its own action row. Long object names/meta text no
            # longer squeeze the label down to "Spell le..." on narrow windows.
            self.level_box=ttk.Frame(header_actions)
            self.spell_level_label=ttk.Label(self.level_box,text=self._t("spell_level")); self.spell_level_label.pack(side="left")
            self.level_var=tk.IntVar(value=1)
            sp=ttk.Spinbox(self.level_box,from_=1,to=100,textvariable=self.level_var,width=5,command=self.change_level)
            sp.pack(side="left",padx=6); sp.bind("<FocusOut>",lambda e:self.change_level()); sp.bind("<Return>",lambda e:self.change_level())
            self.apply_btn=ttk.Button(header_actions,text=self._t("apply_object"),command=self.apply_current); self.apply_btn.pack(side="right")

            self.notebook=ttk.Notebook(center); self.notebook.pack(fill="both",expand=True,padx=(10,8))
            self.raw_frame=ttk.Frame(self.notebook,padding=10)
            self.raw_tree=ttk.Treeview(self.raw_frame,columns=("field","type","level","column","value"),show="headings")
            for col,key,width in [("field","field",70),("type","type",80),("level","level",55),("column","column",55),("value","value",420)]:
                self.raw_tree.heading(col,text=self._t(key)); self.raw_tree.column(col,width=width,anchor="w")
            self.raw_tree.pack(fill="both",expand=True)
            rb=ttk.Frame(self.raw_frame); rb.pack(fill="x",pady=(8,0))
            self.raw_add_btn=ttk.Button(rb,text=self._t("add_raw"),command=self.raw_add); self.raw_add_btn.pack(side="left")
            self.raw_edit_btn=ttk.Button(rb,text=self._t("edit"),command=self.raw_edit); self.raw_edit_btn.pack(side="left",padx=6)
            self.raw_remove_btn=ttk.Button(rb,text=self._t("remove"),command=self.raw_delete); self.raw_remove_btn.pack(side="left")
            self.raw_tree.bind("<Double-1>",lambda e:self.raw_edit())

            # RIGHT: visual workspace (icon + model preview).  All viewer controls
            # live here now; there is no separate preview section under the list.
            visual.configure(padding=(8,0,0,0))
            visual_head=ttk.Frame(visual,padding=(0,0,0,6)); visual_head.pack(fill="x")
            self.viewer_title_label=ttk.Label(visual_head,text=self._t("viewer"),font=("Segoe UI",11,"bold")); self.viewer_title_label.pack(side="left")
            ttk.Button(visual_head,text="↻",width=3,command=self.refresh_visuals).pack(side="right")
            self.open_file_btn=ttk.Button(visual_head,text=self._t("open_file"),command=self.open_viewer_picker); self.open_file_btn.pack(side="right",padx=(0,5))
            self.casc_btn=ttk.Button(visual_head,text="CASC…",command=self.configure_casc); self.casc_btn.pack(side="right",padx=(0,5))


            # Keep a concrete LabelFrame instead of textvariable here. Some Windows/Tk builds
            # do not expose -textvariable on ttk::labelframe, which can make startup fail.
            self.icon_card=ttk.LabelFrame(visual,text=self._t("icons_unit"),padding=8)
            self.icon_card.pack(fill="x",pady=(0,8))
            icon_card=self.icon_card
            icon_gallery=ttk.Frame(icon_card); icon_gallery.pack(fill="x")
            self.icon_variant_labels={}
            self.icon_variant_path_vars={}
            for col,(variant,label) in enumerate((("normal",self._t("normal_btn")),("disabled",self._t("disabled")),("passive",self._t("passive")))):
                slot=ttk.Frame(icon_gallery)
                slot.grid(row=0,column=col,sticky="n",padx=(0 if col==0 else 6,0))
                ttk.Label(slot,text=label,style="Source.TLabel").pack(anchor="center")
                host=tk.Frame(slot,width=82,height=82,bg="#11151d",highlightthickness=1,highlightbackground="#343a46")
                host.pack(pady=(3,2)); host.pack_propagate(False)
                img=tk.Label(host,text="—",fg="#8f9bad",bg="#11151d",font=("Segoe UI",18,"bold"),cursor="hand2")
                img.pack(fill="both",expand=True)
                img.bind("<Button-1>",lambda _event,v=variant:self.change_icon_from_preview(v))
                host.configure(cursor="hand2")
                host.bind("<Button-1>",lambda _event,v=variant:self.change_icon_from_preview(v))
                self.icon_variant_labels[variant]=img
                pv=tk.StringVar(value="")
                self.icon_variant_path_vars[variant]=pv
                ttk.Label(slot,textvariable=pv,style="Source.TLabel",justify="center",wraplength=90).pack(fill="x")
            icon_gallery.columnconfigure(0,weight=1); icon_gallery.columnconfigure(1,weight=1); icon_gallery.columnconfigure(2,weight=1)
            self.icon_label=self.icon_variant_labels["normal"]
            icon_actions=ttk.Frame(icon_card); icon_actions.pack(fill="x",pady=(6,0))
            self.export_icons_btn=ttk.Button(
                icon_actions,text=self._msg("Exportar ícones…", "Export icons…"),
                command=self.export_current_icon_bundle,
            )
            self.export_icons_btn.pack(side="left")
            self.icon_status_label=ttk.Label(icon_card,textvariable=self.icon_status_var,justify="left",anchor="w",wraplength=285)
            self.icon_status_label.pack(anchor="w",fill="x",pady=(6,0))
            self.visual_source_label=ttk.Label(icon_card,textvariable=self.visual_source_var,style="Source.TLabel",justify="left",anchor="w",wraplength=285)
            self.visual_source_label.pack(anchor="w",fill="x",pady=(3,0))
            def _resize_icon_text(event):
                safe=max(150,int(event.width)-24)
                self.icon_status_label.configure(wraplength=safe)
                self.visual_source_label.configure(wraplength=safe)
            icon_card.bind("<Configure>",_resize_icon_text,add="+")

            self.ability_test_card=ttk.LabelFrame(visual,text=self._t("ability_test"),padding=8)
            hero_row=ttk.Frame(self.ability_test_card); hero_row.pack(fill="x")
            self.test_hero_label=ttk.Label(hero_row,text=self._t("test_hero")); self.test_hero_label.pack(side="left")
            self.ability_test_hero_combo=ttk.Combobox(hero_row,textvariable=self.ability_test_hero_var,state="readonly",width=25)
            self.ability_test_hero_combo.pack(side="left",fill="x",expand=True,padx=(8,0))
            self.ability_test_hero_combo.bind("<<ComboboxSelected>>",lambda e:self.refresh_preview())
            ttk.Label(self.ability_test_card,textvariable=self.ability_test_info_var,style="Source.TLabel",justify="left",wraplength=365).pack(anchor="w",fill="x",pady=(6,0))
            self.ability_test_help_label=ttk.Label(self.ability_test_card,text=self._t("ability_test_help"),style="Source.TLabel",justify="left",wraplength=365); self.ability_test_help_label.pack(anchor="w",fill="x",pady=(3,0))

            self.model_card=ttk.LabelFrame(visual,text=self._t("model_spell"),padding=8); self.model_card.pack(fill="both",expand=True)
            model_card=self.model_card
            preview_head=ttk.Frame(model_card,padding=(0,0,0,5)); preview_head.pack(fill="x")
            # Warcraft III patch 3.0 exposes three native art sets: Definitive DE,
            # Reforged HD and Classic SD. This selector is preview-only and never
            # writes a graphics mode into the map.
            # Legacy selector reference for old static tests: values=("Reforged HD", "Clássico / SD")
            # Current patch adds Definitive DE as the third native art set: "Definitive DE", "Reforged HD", "Clássico / SD"
            self.models_label=ttk.Label(preview_head,text=self._msg("Modelos:", "Models:")); self.models_label.pack(side="left")
            self.model_asset_mode_combo=ttk.Combobox(
                preview_head,textvariable=self.model_asset_mode_var,state="readonly",width=24,
                values=("Definitive DE", "Reforged HD", self._msg("Clássico / SD", "Classic / SD"))
            )
            self.model_asset_mode_combo.pack(side="left",padx=(7,0),fill="x",expand=True)
            self.model_asset_mode_combo.bind("<<ComboboxSelected>>",lambda e:self.change_model_asset_mode())
            # v3.9.5: the embedded viewer is the primary/only preview surface.
            # Keeping a second "Abrir externo" action made failures ambiguous and
            # duplicated the exact same viewer in another browser window.
            self.open_external_btn=None
            ttk.Label(model_card,textvariable=self.preview_status_var,anchor="w",justify="left",wraplength=365).pack(fill="x",pady=(0,6))
            self.preview_host=tk.Frame(model_card,bg="#0d0f14",height=430,highlightthickness=1,highlightbackground="#2b2f38")
            self.preview_host.pack(fill="both",expand=True)
            self.preview_fallback=tk.Label(self.preview_host,text=self._msg("Inicializando WebView2…", "Initializing WebView2…"),fg="#9aa3b5",bg="#0d0f14",justify="center")
            self.preview_fallback.place(relx=.5,rely=.5,anchor="center")

            # Dedicated editor for concrete placements in war3mapUnits.doo.
            self._build_placed_units_workspace(self.placed_units_page)

            self.status=tk.StringVar(value=self._msg(f"Log: {latest_log_path()} · Abra um .w3m/.w3x.", f"Log: {latest_log_path()} · Open a .w3m/.w3x."))
            ttk.Label(self,textvariable=self.status,anchor="w",padding=(12,0,12,10)).pack(fill="x")
            self.build_form("unit")
            self.after_idle(self._apply_language)
            self.after(350,self._init_embedded_viewer)

    def _main_workspace_changed(self, _event=None):
            """Keep Object-Editor-only commands out of the placement workspace."""
            try:
                current = self.main_tabs.select()
                on_placements = current == str(self.placed_units_page)
                for widget in (self.new_object_btn, self.edit_base_btn):
                    widget.state(["disabled"] if on_placements else ["!disabled"])
                if on_placements:
                    self.refresh_placed_units_tree(select_id=getattr(self, "_placed_index", None))
                    try:
                        if self.session:
                            self.refresh_placed_map_view()
                    except Exception:
                        LOGGER.debug("Placement minimap refresh failed", exc_info=True)
                    self.after_idle(self._init_placed_embedded_viewer)
                    if getattr(self, "_placed_index", None) is not None:
                        self.after(80, self.refresh_placed_visuals)
            except Exception:
                LOGGER.debug("Main workspace tab update failed", exc_info=True)

    def _tk_callback_exception(self, exc_type, exc_value, exc_tb):
            text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            LOGGER.critical("TK CALLBACK EXCEPTION\n%s", text)
            messagebox.showerror(
                self._msg("Erro interno — traceback salvo", "Internal error — traceback saved"),
                self._msg(
                    f"{exc_type.__name__}: {exc_value}\n\nLog completo:\n{latest_log_path()}",
                    f"{exc_type.__name__}: {self._localize_error_text(exc_value)}\n\nFull log:\n{latest_log_path()}",
                ),
                parent=self,
            )


    def _model_asset_preference(self) -> str:
            value=str(self.model_asset_mode_var.get() if hasattr(self, "model_asset_mode_var") else "Reforged HD").casefold()
            if "definitive" in value or value.strip().startswith("de"):
                return "de"
            return "classic" if ("cláss" in value or "classic" in value or "sd" in value) else "hd"

    def change_model_asset_mode(self):
            mode=self._model_asset_preference()
            # preview_cache is already mode-aware for native Warcraft art. Keeping
            # both HD and SD bundles makes toggling back instantaneous. Imported
            # map models use a mode-independent cache because the file is identical.
            if mode=="de":
                label="Definitive DE"
            elif mode=="classic":
                label=self._msg("Clássico / SD (pré-Reforged)", "Classic / SD (pre-Reforged)")
            else:
                label="Reforged HD"
            self.status.set(self._msg(f"Modo de modelos: {label} · trocando preview · apenas visualização; o mapa não é alterado.", f"Model mode: {label} · switching preview · preview only; the map is not modified."))
            try:self._show_preview_loading(self._msg(f"Trocando para {label}…", f"Switching to {label}…"))
            except Exception:pass
            LOGGER.info("Preview model asset mode changed: %s (cache entries=%d)", mode, len(self.preview_cache))
            if self.current_id:
                self.refresh_preview()

    def configure_casc(self):
            current=detect_warcraft_install()
            start=str(current) if current else str(Path(os.environ.get("PROGRAMFILES(X86)","C:/Program Files (x86)"))/"Warcraft III")
            folder=filedialog.askdirectory(title=self._msg("Pasta do Warcraft III (CASC)", "Warcraft III folder (CASC)"),initialdir=start)
            if not folder:return
            try:
                remember_warcraft_install(folder)
                # Validate and keep the storage warm. Preview/icon workers reuse this
                # process-wide CASC handle instead of reopening Reforged on every click.
                reset_shared_casc()
                get_shared_casc(folder)
                self.status.set(self._msg(f"CASC configurado e aquecido: {folder}", f"CASC configured and warmed up: {folder}"))
                LOGGER.info("User configured Warcraft III CASC: %s",folder)
                if self.current_kind=="ability": self.refresh_preview()
            except Exception as exc:
                self._error_box(self._msg("Falha no CASC", "CASC error"),"configure_casc",exc)

    def open_log(self):
            path = latest_log_path()
            LOGGER.info("User requested log open: %s", path)
            try:
                if os.name == "nt":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(path)])
                else:
                    subprocess.Popen(["xdg-open", str(path)])
            except Exception as exc:
                log_exception(LOGGER, "open_log", exc)
                messagebox.showinfo("Log", f"Log: {path}", parent=self)

    def show_credits(self):
            """Show project and third-party attribution in a compact scrollable dialog."""
            win=tk.Toplevel(self)
            win.title(self._msg("Créditos", "Credits"))
            win.transient(self)
            win.geometry("760x640")
            win.minsize(620,480)
            try:
                if getattr(self, "_app_icon_photo", None) is not None:
                    win.iconphoto(True, self._app_icon_photo)
            except Exception:
                pass

            outer=ttk.Frame(win,padding=14); outer.pack(fill="both",expand=True)
            ttk.Label(
                outer,
                text="WC3 Reforged Object Studio v1.0",
                font=("Segoe UI",15,"bold"),
            ).pack(anchor="w")
            ttk.Label(
                outer,
                text=self._msg(
                    "Editor de Object Data, assets e placements para Warcraft III.",
                    "Object Data, assets and placements editor for Warcraft III.",
                ),
            ).pack(anchor="w",pady=(2,10))

            host=ttk.Frame(outer); host.pack(fill="both",expand=True)
            text=tk.Text(host,wrap="word",font=("Segoe UI",10),relief="solid",borderwidth=1,padx=12,pady=10,cursor="arrow")
            scroll=ttk.Scrollbar(host,orient="vertical",command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            text.pack(side="left",fill="both",expand=True); scroll.pack(side="right",fill="y")

            heading_font=("Segoe UI",10,"bold")
            text.tag_configure("heading",font=heading_font,spacing1=8,spacing3=3)
            text.tag_configure("link",foreground="#0563C1",underline=True)
            text.tag_configure("muted",foreground="#6b7280")

            def add(value="", tag=None):
                text.insert("end", value, tag or ())
            def section(title):
                add(title+"\n","heading")
            links=[]
            def link(label,url):
                tag=f"link_{len(links)}"
                text.tag_configure(tag,foreground="#0563C1",underline=True)
                text.tag_bind(tag,"<Button-1>",lambda _e,u=url:webbrowser.open_new_tab(u))
                text.tag_bind(tag,"<Enter>",lambda _e:text.configure(cursor="hand2"))
                text.tag_bind(tag,"<Leave>",lambda _e:text.configure(cursor="arrow"))
                add(label,tag)
                links.append((tag,url))

            section(self._msg("Projeto", "Project"))
            add(self._msg("Criado por: ", "Created by: ")); add("DarkSir#1620\n")
            add(self._msg(
                "WC3 Reforged Object Studio é um projeto independente e não oficial.\n\n",
                "WC3 Reforged Object Studio is an independent, unofficial project.\n\n",
            ))

            section(self._msg("Projetos e bibliotecas de terceiros", "Third-party projects and libraries"))
            entries=[
                ("War3AssetsImporter — Jaccouille", "MIT", "https://github.com/Jaccouille/war3-assets-importer",
                 self._msg("Referência para o fluxo integrado de importação em lote, criação de unidades e posicionamento no mapa.",
                           "Reference for the integrated batch import, unit creation and map placement workflow.")),
                ("StormLib 9.40 — Ladislav Zezula", "MIT", "https://github.com/ladislav-zezula/StormLib",
                 self._msg("Leitura e gravação de arquivos MPQ.", "MPQ archive reading and writing.")),
                ("CascLib — Ladislav Zezula", "MIT", "https://github.com/ladislav-zezula/CascLib",
                 self._msg("Leitura do CASC local do Warcraft III Reforged.", "Reading the local Warcraft III Reforged CASC storage.")),
                ("war3-model 4.0.1 — 4eb0da", "MIT", "https://github.com/4eb0da/war3-model",
                 self._msg("Parser MDX/MDL e base do visualizador WebGL 3D.", "MDX/MDL parser and base of the 3D WebGL viewer.")),
                ("gl-matrix 3.4.3 — Brandon Jones / Colin MacKenzie IV", "MIT", "https://github.com/toji/gl-matrix",
                 self._msg("Operações de matriz e vetor usadas pelo viewer.", "Matrix and vector operations used by the viewer.")),
                ("tkwry 0.1.9 — mashu3", "MIT", "https://github.com/mashu3/tkwry",
                 self._msg("Integração do WebView2 como child window do Tkinter.", "Embeds WebView2 as a Tkinter child window.")),
                ("Pillow", "MIT-CMU", "https://github.com/python-pillow/Pillow",
                 self._msg("Decodificação e preview de imagens/texturas compatíveis.", "Compatible image/texture decoding and preview.")),
                ("Microsoft Edge WebView2 Runtime", self._msg("componente externo", "external component"), "https://developer.microsoft.com/microsoft-edge/webview2/",
                 self._msg("Runtime usado para hospedar o visualizador WebGL no Windows; não é distribuído pelo Studio.",
                           "Runtime used to host the WebGL viewer on Windows; it is not distributed by the Studio.")),
            ]
            for name,license_name,url,usage in entries:
                add("• "); add(name); add(f" — {license_name}\n")
                add("  "); add(usage+"\n  ")
                link(url,url); add("\n\n")

            section(self._msg("Warcraft III / Blizzard", "Warcraft III / Blizzard"))
            add(self._msg(
                "Warcraft III, Warcraft III: Reforged e seus nomes, formatos, marcas e assets pertencem aos respectivos titulares, incluindo Blizzard Entertainment. O Studio não é afiliado, endossado ou publicado pela Blizzard. Assets nativos são lidos da instalação local do usuário quando necessário; o programa não reivindica propriedade sobre eles.\n\n",
                "Warcraft III, Warcraft III: Reforged and their names, formats, trademarks and assets belong to their respective rights holders, including Blizzard Entertainment. The Studio is not affiliated with, endorsed by, or published by Blizzard. Native assets are read from the user's local installation when needed; the program claims no ownership over them.\n\n",
            ))

            section(self._msg("Licenças", "Licenses"))
            add(self._msg(
                "Os textos de licença aplicáveis estão incluídos na pasta licenses/ e o resumo de componentes está em THIRD_PARTY.md.\n",
                "Applicable license texts are included in the licenses/ folder and the component summary is in THIRD_PARTY.md.\n",
            ))
            text.configure(state="disabled")

            buttons=ttk.Frame(outer); buttons.pack(fill="x",pady=(10,0))
            def open_third_party():
                path=Path(__file__).resolve().parents[2]/"THIRD_PARTY.md"
                try:
                    if os.name == "nt": os.startfile(path)
                    elif sys.platform == "darwin": subprocess.Popen(["open",str(path)])
                    else: subprocess.Popen(["xdg-open",str(path)])
                except Exception as exc:
                    LOGGER.debug("Could not open THIRD_PARTY.md",exc_info=True)
                    messagebox.showinfo(self._msg("Créditos","Credits"),str(path),parent=win)
            ttk.Button(buttons,text=self._msg("ABRIR THIRD_PARTY.md","OPEN THIRD_PARTY.md"),command=open_third_party).pack(side="left")
            ttk.Button(buttons,text=self._msg("FECHAR","CLOSE"),command=win.destroy).pack(side="right")
            try:
                win.grab_set()
                win.focus_set()
            except Exception:
                pass

    def _error_box(self, title: str, context: str, exc: BaseException):
            log_exception(LOGGER, context, exc)
            messagebox.showerror(
                title,
                self._msg(
                    f"{type(exc).__name__}: {exc}\n\nTraceback completo no CMD e em:\n{latest_log_path()}",
                    f"{type(exc).__name__}: {self._localize_error_text(exc)}\n\nFull traceback in the console and at:\n{latest_log_path()}",
                ),
                parent=self,
            )
