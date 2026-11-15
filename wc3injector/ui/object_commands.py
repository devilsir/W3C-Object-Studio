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


class ObjectCommandsMixin:
    def refresh_raw(self):
            self.raw_tree.delete(*self.raw_tree.get_children())
            for i,m in enumerate(self.working_mods):
                self.raw_tree.insert("","end",iid=str(i),values=(m.field_id,TYPE_NAMES.get(m.value_type,m.value_type),m.level,m.column,value_text(m.value)))

    def raw_add(self):
            dlg=RawDialog(self,None,self.current_kind in {"ability","doodad"})
            if dlg.result:self.working_mods.append(dlg.result); self.refresh_raw(); self.load_form(); self.refresh_visuals()

    def raw_edit(self):
            sel=self.raw_tree.selection()
            if not sel:return
            idx=int(sel[0]); dlg=RawDialog(self,self.working_mods[idx],self.current_kind in {"ability","doodad"})
            if dlg.result:self.working_mods[idx]=dlg.result; self.refresh_raw(); self.load_form(); self.refresh_visuals()

    def raw_delete(self):
            sel=self.raw_tree.selection()
            if not sel:return
            del self.working_mods[int(sel[0])]; self.refresh_raw(); self.load_form(); self.refresh_visuals()

    def new_object(self):
            if not self.session:
                messagebox.showinfo(self._msg("Novo objeto", "New object"),self._msg("Abra um mapa primeiro.", "Open a map first."),parent=self); return
            hint=self.kind_var.get(); dlg=NewObjectDialog(self,hint)
            if not dlg.result:return
            kind,base,raw,name=dlg.result
            name_field={"unit":"unam","ability":"anam","doodad":"dnam"}[kind]
            mods=[Modification(name_field,TYPE_STRING,name,0,0)]
            try:
                self.session.new_custom(kind,base,raw,mods); self.kind_var.set(kind); self.refresh_tree(raw)
                self.status.set(self._msg(f"{raw} criado em memória a partir de {base}.", f"{raw} created in memory from {base}."))
            except Exception as exc:self._error_box(self._msg("Falha", "Error"), "new_object", exc)

    def edit_base_rawcode(self):
            if not self.session:
                messagebox.showinfo(self._msg("Editar base", "Edit base"), self._msg("Abra um mapa primeiro.", "Open a map first."),parent=self); return
            kind=self.kind_var.get()
            raw=simpledialog.askstring(self._msg("Editar base", "Edit base"), self._msg(f"Rawcode base de {self._t('kind_'+kind)} (4 caracteres):", f"Base rawcode for {self._t('kind_'+kind)} (4 characters):"), parent=self)
            if not raw:return
            if len(raw)!=4:
                messagebox.showerror("Rawcode",self._msg("Precisa ter exatamente 4 caracteres", "It must be exactly 4 characters"),parent=self); return
            try:
                existing=self.session.get_record(kind,"original",raw)
                mods=copy.deepcopy(existing.modifications) if existing else []
                self.session.set_record(kind,"original",raw,mods,raw)
                self.refresh_tree(raw)
                self.status.set(self._msg(f"Override de {raw} aberto. Campos vazios continuam herdando o dado nativo do jogo.", f"Override for {raw} opened. Empty fields continue to inherit native game data."))
            except Exception as exc: self._error_box(self._msg("Falha", "Error"), "edit_base_rawcode", exc)

    def duplicate_object(self):
            if not self.session or not self.current_id:return
            raw=simpledialog.askstring(self._msg("Duplicar", "Duplicate"),self._msg("Novo rawcode (4 caracteres):", "New rawcode (4 characters):"),parent=self)
            if not raw:return
            if len(raw)!=4:messagebox.showerror("Rawcode",self._msg("Precisa ter 4 caracteres", "It must be 4 characters"),parent=self);return
            try:
                self.save_form_to_working(); self.session.new_custom(self.current_kind,self.current_base,raw,copy.deepcopy(self.working_mods)); self.refresh_tree(raw)
            except Exception as exc:self._error_box(self._msg("Falha", "Error"), "duplicate_object", exc)

    def delete_object(self):
            if not self.session or not self.current_id:return
            if self.current_table=="native":
                messagebox.showinfo(self._msg("Objeto padrão do Warcraft", "Standard Warcraft object"),self._msg("Esse objeto não está gravado no mapa, então não há override para excluir. Edite e aplique para criar um override primeiro.", "This object is not stored in the map, so there is no override to delete. Edit and apply it first to create an override."),parent=self)
                return
            if not messagebox.askyesno(self._msg("Excluir", "Delete"),self._msg(f"Remover {self.current_id} da tabela {self.current_table}?\nIsso remove apenas o override/custom object, não o objeto base do jogo.", f"Remove {self.current_id} from the {self.current_table} table?\nThis removes only the override/custom object, not the base game object."),parent=self):return
            self.session.delete_record(self.current_kind,self.current_table,self.current_id); self.current_id=None; self.refresh_tree(); self.title_var.set(self._t("select_object"))

    def import_model(self,field_key):
            if not self.session:return
            model=filedialog.askopenfilename(filetypes=[("Warcraft model","*.mdx *.mdl")])
            if not model:return
            textures=filedialog.askopenfilenames(title=self._msg("Selecione as texturas do modelo (opcional)", "Select model textures (optional)"),filetypes=[("Warcraft textures","*.blp *.tga *.dds")])
            try:
                m=Path(model); ts=[Path(x) for x in textures]
                assets,paths,warnings=auto_assets([m],ts,r"war3mapImported\ObjectStudio")
                for a in assets:self.session.stage_asset(a.source,a.archive_path)
                ap=paths[m]; self.form_vars[field_key].set(ap); self.local_models[ap.casefold()]=(m,ts)
                if warnings: messagebox.showwarning(self._msg("Modelo importado", "Imported model"),"\n".join(warnings[:12]),parent=self)
                self.status.set(self._msg(f"Modelo staged: {ap}. A escala pode ser editada na mesma aba.", f"Model staged: {ap}. Scale can be edited in the same tab."))
                self.refresh_visuals()
            except Exception as exc:self._error_box(self._msg("Importação", "Import"), "import_model", exc)

    def view_model_field(self,field_key):
            ap=self.form_vars.get(field_key,tk.StringVar()).get().strip()
            if not ap:
                self.open_viewer_picker(); return
            local=self.local_models.get(ap.casefold())
            if local:
                launch_viewer([local[0],*local[1]],local[0]); return
            if not self.session:
                self.open_viewer_picker(); return
            try:
                tmp=Path(tempfile.mkdtemp(prefix="wc3_extract_view_")); self.temp_dirs.append(tmp)
                with StormArchive(self.session.map_path, read_only=True) as arc:
                    files=[]; missing=[]
                    actual,model_path=self._extract_model_bundle_from_arc(arc,ap,tmp,files,missing)
                    if not model_path:
                        raise FileNotFoundError(self._msg(f"{ap} não foi encontrado no MPQ/CASC", f"{ap} was not found in MPQ/CASC"))
                launch_viewer(files,model_path)
            except Exception as exc:
                log_exception(LOGGER, "view_model_field", exc)
                if messagebox.askyesno("Viewer",self._msg(f"Não consegui extrair esse modelo do mapa:\n{exc}\n\nTraceback: {latest_log_path()}\n\nQuer escolher um MDX/MDL local?", f"Could not extract this model from the map:\n{exc}\n\nTraceback: {latest_log_path()}\n\nChoose a local MDX/MDL instead?"),parent=self): self.open_viewer_picker()

    def open_viewer_picker(self):
            files=filedialog.askopenfilenames(title=self._msg("Modelo + texturas", "Model + textures"),filetypes=[("Warcraft model/assets","*.mdx *.mdl *.blp *.tga *.dds"),(self._msg("Todos","All files"),"*.*")])
            if not files:
                launch_viewer(); return
            paths=[Path(x) for x in files]; main=next((p for p in paths if p.suffix.lower() in {".mdx",".mdl"}),None)
            launch_viewer(paths,main)

    def save(self,save_as=False):
            if not self.session:return
            # Placement edits live in a dedicated workspace now, so they can be
            # dirty even while the Object Editor tab is active. Apply that draft
            # before serializing the map.
            try:
                if self._placed_unit_ui_dirty():
                    if not self.apply_placed_hero():
                        return
            except Exception as exc:
                self._error_box(self._msg("Não foi possível aplicar a unidade colocada", "Could not apply placed unit"), "save / placed unit draft", exc)
                return
            if self.current_id and not self.apply_current():
                return
            output=None
            if save_as:
                output=filedialog.asksaveasfilename(defaultextension=self.session.map_path.suffix,filetypes=[("Warcraft III map","*.w3m *.w3x")])
                if not output:return
            LOGGER.info("SAVE requested: save_as=%s output=%s dirty=%s placed_units_dirty=%s staged_assets=%d", save_as, output, sorted(self.session.dirty), self.session.placed_units_dirty, len(self.session.staged_assets))
            self._set_status("Gravando em cópia transacional + validando hashes… veja o CMD", "Writing a transactional copy + validating hashes… see the console")
            def worker():
                try:
                    LOGGER.info("Save worker started")
                    rep=self.session.save(output_path=output,make_backup=True)
                    LOGGER.info("Save SUCCESS output=%s changed=%s backup=%s", rep.output_path, rep.changed_files, rep.backup_path)
                    msg=self._msg(f"Salvo: {rep.output_path}\nArquivos alterados: {', '.join(rep.changed_files)}", f"Saved: {rep.output_path}\nChanged files: {', '.join(rep.changed_files)}")
                    if rep.backup_path:msg+=f"\nBackup: {rep.backup_path}"
                    self.after(0,lambda:self._save_done(True,msg))
                except Exception as exc:
                    log_exception(LOGGER, "save worker", exc)
                    self.after(0,lambda e=exc:self._save_done(False,self._msg(f"{type(e).__name__}: {e}\n\nTraceback: {latest_log_path()}", f"{type(e).__name__}: {e}\n\nTraceback: {latest_log_path()}")))
            threading.Thread(target=worker,daemon=True).start()

    def _save_done(self,ok,msg):
            self.status.set(self._msg("Salvo e validado.", "Saved and validated.") if ok else self._msg("Falha: nenhum commit foi concluído.", "Failed: no commit was completed."))
            if ok:
                self.map_label.set(self.session.map_path.name); messagebox.showinfo(self._msg("Concluído", "Done"),msg,parent=self)
            else:messagebox.showerror(self._msg("Falha ao salvar", "Save failed"),msg,parent=self)
