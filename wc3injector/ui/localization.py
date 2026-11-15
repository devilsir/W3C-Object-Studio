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


class LocalizationMixin:
    def _t(self, key: str, default: str | None = None) -> str:
            return i18n_tr(self.language, key, default)

    def _msg(self, pt: str, en: str) -> str:
            """Return one short UI message in the active language."""
            return en if self.language == "en" else pt

    def _set_status(self, pt: str, en: str) -> None:
            if hasattr(self, "status"):
                self.status.set(self._msg(pt, en))

    def _localize_source_text(self, text: str) -> str:
            """Translate source/provenance labels emitted by the core catalogs.

            The core deliberately stays language-neutral-ish and some legacy
            provenance strings are Portuguese.  Translate them at the UI edge so
            English mode never leaks Portuguese under the field rows.
            """
            value = str(text or "")
            if self.language != "en" or not value:
                return value
            # Specific phrases must come before their generic suffixes.
            # Example: translating "herdado do Warcraft" first would leave
            # "ícone inherited from Warcraft" half-translated.
            pairs = (
                ("ícone inferido dos imports", "icon inferred from imports"),
                ("ícone herdado do Warcraft", "icon inherited from Warcraft"),
                ("ícone inferido", "inferred icon"),
                ("modelo custom ausente", "custom model missing"),
                ("visual inferido de", "visual inferred from"),
                ("fallback visual da base", "visual fallback from base"),
                ("skin do mapa", "map skin"),
                ("base nativa", "native base"),
                ("campo sem metadata", "field without metadata"),
                ("sem valor", "no value"),
                ("herdado do Warcraft", "inherited from Warcraft"),
                ("herdado do jogo", "inherited from game"),
                ("herdado de", "inherited from"),
                ("inferido por", "inferred by"),
                ("mesma base", "same base"),
                ("mesma escala", "same scale"),
                ("família ", "family "),
                ("nome ", "name "),
                (" sem registro", " missing record"),
                ("CASC sem ", "CASC missing "),
                ("nativa do Warcraft", "native Warcraft"),
                (" no mapa", " in map"),
                ("inválido", "invalid"),
            )
            for a, b in pairs:
                value = value.replace(a, b)
            return value

    def _localize_error_text(self, text: object) -> str:
            """Translate common core/MPQ/CASC exceptions at the UI boundary.

            Core exceptions intentionally stay stable for logs/tests; English mode
            should not expose their legacy Portuguese wording to the user.
            """
            value = str(text or "")
            if self.language != "en" or not value:
                return value
            pairs = (
                ("Instalação do Warcraft III não encontrada", "Warcraft III installation not found"),
                ("StormLib.dll não encontrado", "StormLib.dll not found"),
                ("CascLib.dll não encontrada", "CascLib.dll not found"),
                ("não há alterações para salvar", "there are no changes to save"),
                ("o mapa de origem mudou desde que foi aberto; recarregue antes de salvar", "the source map changed since it was opened; reload it before saving"),
                ("mudaram externamente desde a abertura; recarregue o mapa", "changed externally since the map was opened; reload the map"),
                ("arquivo protegido mudou; commit recusado", "a protected file changed; commit refused"),
                ("arquivo protegido mudou durante a injeção; commit recusado", "a protected file changed during injection; commit refused"),
                ("o mapa precisa ser .w3m ou .w3x", "the map must be .w3m or .w3x"),
                ("mapa não encontrado", "map not found"),
                ("modelo não encontrado", "model not found"),
                ("asset não encontrado", "asset not found"),
                ("arquivo não encontrado", "file not found"),
                ("não foi possível validar arquivo protegido", "could not validate protected file"),
                ("não foi possível", "could not"),
                ("não encontrado", "not found"),
                ("não encontrada", "not found"),
                ("não contém", "does not contain"),
                ("não está aberto", "is not open"),
                ("não está importado", "is not imported"),
                ("não suportada", "unsupported"),
                ("não suportado", "unsupported"),
                ("inválida", "invalid"),
                ("inválido", "invalid"),
                ("ausente após gravação", "missing after write"),
                ("ausente após importação", "missing after import"),
                ("round-trip pós-gravação falhou", "post-write round-trip failed"),
                ("round-trip pré-gravação falhou", "pre-write round-trip failed"),
                ("round-trip falhou antes da gravação", "round-trip failed before write"),
                ("tentativa de gravar", "attempt to write"),
                ("modo somente leitura", "read-only mode"),
                ("arquivo interno grande demais para leitura segura", "internal file is too large for safe reading"),
                ("o caminho não é um arquivo", "the path is not a file"),
                ("a extensão de saída deve ser igual à do mapa de origem", "the output extension must match the source map extension"),
                ("rawcode precisa ter exatamente 4 caracteres", "rawcode must be exactly 4 characters"),
                ("objeto custom precisa de rawcode base com 4 caracteres", "custom object needs a 4-character base rawcode"),
                ("tipo de objeto inválido", "invalid object type"),
                ("caminho interno inválido", "invalid internal path"),
                ("asset não suportado", "unsupported asset"),
                ("arquivo object-data truncado", "truncated object-data file"),
                ("versão object-data não suportada", "unsupported object-data version"),
                ("tipo de modificação desconhecido", "unknown modification type"),
                ("base_id é obrigatório para novo objeto custom", "base_id is required for a new custom object"),
            )
            for a, b in pairs:
                value = value.replace(a, b)
            return self._localize_source_text(value)

    def _load_flag_images(self):
            self.flag_images = {}
            root = ASSETS_DIR
            for code, filename in (("pt", "br.png"), ("en", "us.png")):
                path = root / filename
                try:
                    self.flag_images[code] = tk.PhotoImage(file=str(path))
                except Exception:
                    LOGGER.debug("Could not load language flag %s", path, exc_info=True)
                    self.flag_images[code] = None

    def _kind_values(self):
            return [self._t("kind_unit"), self._t("kind_ability"), self._t("kind_doodad")]

    def _scope_values(self):
            return [self._t("scope_all"), self._t("scope_map"), self._t("scope_warcraft")]

    def _kind_selected(self, _event=None):
            idx = self.kind_combo.current() if getattr(self, "kind_combo", None) else 0
            keys = ("unit", "ability", "doodad")
            if 0 <= idx < len(keys):
                self.kind_var.set(keys[idx])
            self.scope_combo.configure(state="readonly")
            self.refresh_tree()

    def _scope_selected(self, _event=None):
            idx = self.scope_combo.current() if getattr(self, "scope_combo", None) else 0
            keys = ("all", "map", "warcraft")
            if 0 <= idx < len(keys):
                self.scope_var.set(keys[idx])
            self.refresh_tree()

    def _set_language(self, language: str):
            if language not in {"pt", "en"}:
                return
            draft = {k: v.get() for k, v in self.form_vars.items()} if self.current_id else {}
            current_tab = self.notebook.index(self.notebook.select()) if self.notebook.tabs() else 0
            selected = None
            sel = self.tree.selection() if getattr(self, "tree", None) else ()
            if sel:
                selected = sel[0]
            self.language = language
            self._apply_language()
            if self.current_id:
                self.build_form(self.current_kind)
                self.load_form()
                for key, value in draft.items():
                    if key in self.form_vars:
                        self.form_vars[key].set(value)
                self.refresh_raw()
                self.refresh_ability_list()
                try:
                    tabs = self.notebook.tabs()
                    if tabs:
                        self.notebook.select(tabs[min(current_tab, len(tabs)-1)])
                except Exception:
                    pass
            self.refresh_tree(select_id=self.current_id if self.current_id else None, trigger_on_select=False)
            if selected and self.tree.exists(selected):
                self.tree.selection_set(selected)
                self.tree.see(selected)

    def _apply_language(self):
            if not hasattr(self, "open_map_btn"):
                return
            try:
                self.title(self._msg(
                    "WC3 Reforged Object Studio v1.0 — Object Editor completo • MPQ + CASC",
                    "WC3 Reforged Object Studio v1.0 — Full Object Editor • MPQ + CASC",
                ))
            except Exception:
                pass
            widget_texts = (
                (self.open_map_btn, "open_map"), (self.save_btn, "save"), (self.save_as_btn, "save_as"),
                (self.new_object_btn, "new_object"), (self.edit_base_btn, "edit_base"), (self.open_log_btn, "open_log"), (self.credits_btn, "credits"),
                (self.duplicate_btn, "duplicate"), (self.delete_override_btn, "delete_override"),
                (self.apply_btn, "apply_object"), (self.spell_level_label, "spell_level"),
                (self.raw_add_btn, "add_raw"), (self.raw_edit_btn, "edit"), (self.raw_remove_btn, "remove"),
                (self.viewer_title_label, "viewer"), (self.open_file_btn, "open_file"),
                (self.casc_btn, "casc"),
                (self.test_hero_label, "test_hero"),
            )
            for widget, key in widget_texts:
                try:
                    widget.configure(text=self._t(key, widget.cget("text")))
                except Exception:
                    pass
            try:
                self.asset_import_btn.configure(text=self._msg("IMPORTAR ASSETS…", "IMPORT ASSETS…"))
            except Exception:
                pass
            self.kind_combo.configure(values=self._kind_values())
            kind_idx = {"unit":0, "ability":1, "doodad":2}.get(self.kind_var.get(), 0)
            self.kind_combo.current(kind_idx)
            self.scope_combo.configure(values=self._scope_values())
            scope_idx = {"all":0, "map":1, "warcraft":2}.get(self.scope_var.get(), 0)
            self.scope_combo.current(scope_idx)
            try:
                self.scope_combo.configure(state="readonly")
            except Exception:
                pass
            try:
                if getattr(self, "main_tabs", None) is not None:
                    self.main_tabs.tab(self.object_editor_page, text=self._msg("OBJECT EDITOR", "OBJECT EDITOR"))
                    self.main_tabs.tab(self.placed_units_page, text=self._msg("UNIDADES NO MAPA", "UNITS ON MAP"))
                self.refresh_placed_units_language()
            except Exception:
                LOGGER.debug("Placed-units language refresh failed", exc_info=True)
            self._update_tree_headings()
            for col, key in (("field","field"),("type","type"),("level","level"),("column","column"),("value","value")):
                try: self.raw_tree.heading(col, text=self._t(key))
                except Exception: pass
            try:
                if not self.session:
                    self.map_label.set(self._t("no_map"))
                if not self.current_id:
                    self.title_var.set(self._t("select_object"))
                self.ability_test_card.configure(text=self._t("ability_test"))
                self.model_card.configure(text=self._t("model_spell"))
                if getattr(self, "models_label", None) is not None:
                    self.models_label.configure(text=self._msg("Modelos:", "Models:"))
                if getattr(self, "model_asset_mode_combo", None) is not None:
                    pref = self._model_asset_preference()
                    values = ("Definitive DE", "Reforged HD", self._msg("Clássico / SD", "Classic / SD"))
                    self.model_asset_mode_combo.configure(values=values)
                    self.model_asset_mode_var.set(values[0] if pref == "de" else (values[2] if pref == "classic" else values[1]))
                if self.current_kind == "ability":
                    self.icon_card.configure(text=self._t("icons_ability"))
                elif self.current_kind == "unit":
                    self.icon_card.configure(text=self._t("icons_unit"))
                else:
                    self.icon_card.configure(text=self._t("icon_object"))
                self.ability_test_help_label.configure(text=self._t("ability_test_help"))
            except Exception:
                pass
            try:
                if not self.current_id:
                    self.preview_status_var.set(self._msg("Selecione uma unidade/doodad com modelo importado", "Select a unit/doodad with a model"))
                    self.icon_status_var.set(self._msg("Ícones: selecione um objeto", "Icons: select an object"))
                if getattr(self, "preview_fallback", None) is not None and not getattr(self, "embedded_view", None):
                    self.preview_fallback.configure(text=self._msg("Inicializando WebView2…", "Initializing WebView2…"))
                self.lang_pt_btn.state(["disabled"] if self.language == "pt" else ["!disabled"])
                self.lang_en_btn.state(["disabled"] if self.language == "en" else ["!disabled"])
            except Exception:
                pass

    def _update_tree_headings(self):
            if not getattr(self, "tree", None):
                return
            labels={"name":self._t("object"), "base":self._t("base"), "table":self._t("table")}
            for col, label in labels.items():
                arrow = ""
                if col == self.tree_sort_column:
                    arrow = " ▼" if self.tree_sort_reverse else " ▲"
                self.tree.heading(col, text=label + arrow, command=lambda c=col:self._sort_tree(c))

    def _sort_tree(self, column: str):
            if column == self.tree_sort_column:
                self.tree_sort_reverse = not self.tree_sort_reverse
            else:
                self.tree_sort_column = column
                self.tree_sort_reverse = False
            selected_id = self.current_id
            self.refresh_tree(select_id=selected_id, trigger_on_select=False)
