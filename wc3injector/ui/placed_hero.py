from __future__ import annotations

import copy
import math
import os
import tempfile
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from ..debuglog import configure_logging, log_exception
from ..casc import detect_warcraft_install, get_shared_casc
from ..storm import StormArchive
from ..unit_native import resolve_unit_visual
from ..viewer_server import prepare_viewer_url
from .map_placement import MapPlacementCanvas, load_map_placement_art
from .item_browser import choose_item
from ..item_catalog import load_item_catalog
from ..unitsdoo import (
    UnitPlacement, InventoryItem, ModifiedAbility, DroppedItem, DroppedItemSet,
    player_choices, player_label,
)

LOGGER, _SESSION_LOG = configure_logging()


class PlacedHeroMixin:
    """World-Editor-style editor for preplaced units/heroes in war3mapUnits.doo."""

    def _ensure_placed_state(self) -> None:
        if hasattr(self, "placed_owner_var"):
            return
        self.placed_owner_var = tk.StringVar(value="")
        self.placed_facing_var = tk.StringVar(value="0")
        self.placed_hp_var = tk.StringVar(value="-1")
        self.placed_mana_var = tk.StringVar(value="-1")
        self.placed_acq_var = tk.StringVar(value="-1")
        self.placed_level_var = tk.StringVar(value="1")
        self.placed_use_default_attrs = tk.BooleanVar(value=True)
        self.placed_str_var = tk.StringVar(value="0")
        self.placed_agi_var = tk.StringVar(value="0")
        self.placed_int_var = tk.StringVar(value="0")
        self.placed_inventory_vars = [tk.StringVar(value="") for _ in range(6)]
        self._placed_item_catalog_cache = None
        self._placed_inventory_icon_photos: dict[int, object] = {}
        self._placed_inventory_icon_labels = []
        self.placed_drop_mode_var = tk.StringVar(value="none")
        self.placed_drop_mode_display_var = tk.StringVar(value="")
        self.placed_drop_pointer_var = tk.StringVar(value="0")
        self.placed_ability_rows: list[dict] = []
        # Per-selected-ability editor. ``active`` is the UI-facing state that
        # controls whether a ModifiedAbility record is persisted for this
        # placement. Warcraft's war3mapUnits.doo stores two fields per modified
        # ability (activeForAutocast + heroLevel); the separate Active control
        # therefore maps to presence/absence of the instance override, while
        # Autocast maps to activeForAutocast and Level maps to heroLevel.
        self.placed_ability_active_var = tk.BooleanVar(value=False)
        self.placed_ability_autocast_var = tk.BooleanVar(value=False)
        self.placed_ability_level_edit_var = tk.StringVar(value="0")
        self._placed_ability_editor_guard = False
        self.placed_drop_rows: list[dict] = []
        self._placed_snapshot: UnitPlacement | None = None
        self._placed_creation_number: int | None = None
        # Unique UI identity. creation_number is not guaranteed to be unique in
        # maps modified by external tools, while file-order index always is.
        self._placed_index: int | None = None
        # UI-level baseline used to detect *actual* user edits. Comparing a
        # placement rebuilt from text fields against the binary snapshot is not
        # reliable because f32 -> decimal -> degrees/radians -> f32 introduces
        # harmless rounding differences even when the user touched nothing.
        self._placed_ui_initial_signature = None
        self._placed_owner_lookup: dict[str, int] = {}
        self.placed_search_var = tk.StringVar(value="")
        self.placed_filter_var = tk.StringVar(value="all")
        self.placed_filter_display_var = tk.StringVar(value="")
        self.placed_title_var = tk.StringVar(value=self._msg("Selecione uma unidade colocada", "Select a placed unit"))
        self.placed_meta_var = tk.StringVar(value="")
        self.placed_count_var = tk.StringVar(value="")
        self._placed_tree_guard = False
        self._placed_is_hero = False
        self.placed_visual_serial = 0
        self.placed_model_asset_mode_var = tk.StringVar(value="Reforged HD")
        self.placed_preview_status_var = tk.StringVar(value=self._msg("Selecione uma unidade colocada.", "Select a placed unit."))
        self.placed_visual_source_var = tk.StringVar(value="")
        self.placed_icon_status_var = tk.StringVar(value=self._msg("Ícone: aguardando seleção", "Icon: waiting for selection"))
        self.placed_icon_path_var = tk.StringVar(value="")
        self.placed_icon_photo = None
        self.placed_embedded_view = None
        self.placed_preview_last_url = None

    def _placed_unit_display_name(self, rawcode: str) -> str:
        if not self.session:
            return rawcode
        # Prefer a map object with the same rawcode, then the native catalog.
        for table in ("custom", "original"):
            rec = self.session.get_record("unit", table, rawcode)
            if rec is not None:
                try:
                    name_mod, _source, _explicit = self.session.resolve_map_value(
                        "unit", table, rawcode, "unam", level=0, column=0
                    )
                    if name_mod is not None and str(name_mod.value).strip():
                        return self.session.resolve_string(name_mod.value).strip()
                except Exception:
                    LOGGER.debug("Placed hero name lookup failed for %s:%s", table, rawcode, exc_info=True)
        try:
            name = self.session.native_name("unit", rawcode)
            return name if name else rawcode
        except Exception:
            return rawcode

    def _placed_unit_is_hero(self, unit: UnitPlacement) -> bool:
        if unit.is_hero:
            return True
        if not self.session:
            return False
        # Custom hero rawcodes do not have to begin with an uppercase letter.
        # Resolve the map object's native base before deciding whether hero-only
        # placement fields (level/stats) are meaningful.
        for table in ("custom", "original"):
            rec = self.session.get_record("unit", table, unit.unit_id)
            if rec is None:
                continue
            base = str(getattr(rec, "original_id", "") or "")
            if base and not base.startswith("YY") and base[:1].isupper():
                return True
        return False

    def _placed_ability_display_name(self, rawcode: str) -> str:
        if not self.session:
            return rawcode
        for table in ("custom", "original"):
            rec = self.session.get_record("ability", table, rawcode)
            if rec is not None:
                try:
                    mod, _source, _explicit = self.session.resolve_map_value(
                        "ability", table, rawcode, "anam", level=0, column=0
                    )
                    if mod is not None and str(mod.value).strip():
                        shown = self.session.resolve_string(mod.value).strip()
                        if not (table == "original" and shown.casefold() == rawcode.casefold()):
                            return shown
                except Exception:
                    LOGGER.debug("Placed ability name lookup failed for %s:%s", table, rawcode, exc_info=True)
        try:
            return self.session.native_name("ability", rawcode)
        except Exception:
            return rawcode

    def _placed_unit_table(self, rawcode: str) -> str:
        if not self.session:
            return "native"
        if self.session.get_record("unit", "custom", rawcode) is not None:
            return "custom"
        if self.session.get_record("unit", "original", rawcode) is not None:
            return "original"
        return "native"

    def _placed_default_abilities(self, rawcode: str) -> list[str]:
        if not self.session:
            return []
        table = self._placed_unit_table(rawcode)
        result: list[str] = []
        for fid in ("uhab", "uabi"):
            try:
                mod, _source, _explicit = self.session.resolve_map_value(
                    "unit", table, rawcode, fid, level=0, column=0
                )
                if mod is None:
                    continue
                text = str(mod.value or "")
                for token in text.replace(";", ",").split(","):
                    ability = token.strip()
                    if len(ability) == 4 and ability not in result:
                        result.append(ability)
            except Exception:
                LOGGER.debug("Default ability pool lookup failed %s %s", rawcode, fid, exc_info=True)
        return result

    def _placed_form_notebook(self):
        return getattr(self, "placed_notebook", None) or self.notebook

    def _destroy_object_form_tabs(self) -> None:
        """Clear the target placement form without touching Object Editor tabs.

        Before v3.10.12 placement editing reused the Object Editor notebook, so
        selecting a placed hero destroyed/rebuilt the normal object tabs.  The
        dedicated "Units on Map" workspace owns its own notebook now.
        """
        notebook = self._placed_form_notebook()
        for tab in list(notebook.tabs()):
            try:
                widget = self.nametowidget(tab)
            except Exception:
                widget = None
            notebook.forget(tab)
            if widget is not None and widget is not getattr(self, "raw_frame", None):
                try:
                    widget.destroy()
                except Exception:
                    LOGGER.debug("Could not destroy placement tab %s", tab, exc_info=True)
        # Only the legacy shared-notebook path needs to hide Spell level.
        if notebook is getattr(self, "notebook", None):
            self.form_vars = {}
            self.form_defs = []
            self.form_state = {}
            self.form_source_vars = {}
            self._form_signature = None
            try:
                self.level_box.pack_forget()
            except Exception:
                pass

    def _placed_filter_values(self) -> list[str]:
        return [
            self._msg("Todos", "All"),
            self._msg("Heróis", "Heroes"),
            self._msg("Outras unidades / itens", "Other units / items"),
        ]

    def _placed_filter_selected(self, _event=None) -> None:
        combo = getattr(self, "placed_filter_combo", None)
        idx = combo.current() if combo is not None else 0
        self.placed_filter_var.set(("all", "heroes", "other")[idx] if 0 <= idx <= 2 else "all")
        self.refresh_placed_units_tree()

    def _build_placed_units_workspace(self, parent) -> None:
        """Build the dedicated top-level workspace for map placements."""
        self._ensure_placed_state()
        root = ttk.Frame(parent, padding=(10, 8))
        root.pack(fill="both", expand=True)
        pan = ttk.Panedwindow(root, orient="horizontal")
        pan.pack(fill="both", expand=True)

        left = ttk.Frame(pan, width=350)
        center = ttk.Frame(pan)
        visual = ttk.Frame(pan, width=400)
        pan.add(left, weight=0)
        pan.add(center, weight=1)
        pan.add(visual, weight=0)

        toolbar = ttk.Frame(left, padding=(0, 0, 8, 8))
        toolbar.pack(fill="x")
        self.placed_filter_display_var.set(self._placed_filter_values()[0])
        self.placed_filter_combo = ttk.Combobox(
            toolbar, textvariable=self.placed_filter_display_var,
            values=self._placed_filter_values(), state="readonly", width=22,
        )
        self.placed_filter_combo.pack(side="left")
        self.placed_filter_combo.current(0)
        self.placed_filter_combo.bind("<<ComboboxSelected>>", self._placed_filter_selected)
        search = ttk.Entry(toolbar, textvariable=self.placed_search_var)
        search.pack(side="left", fill="x", expand=True, padx=(6, 0))
        search.bind("<KeyRelease>", lambda _e: self.refresh_placed_units_tree())

        host = ttk.Frame(left)
        host.pack(fill="both", expand=True, padx=(0, 8))
        host.rowconfigure(0, weight=1); host.columnconfigure(0, weight=1)
        self.placed_tree = ttk.Treeview(
            host, columns=("name", "owner", "kind", "level"), show="headings", selectmode="browse"
        )
        vs = ttk.Scrollbar(host, orient="vertical", command=self.placed_tree.yview)
        hs = ttk.Scrollbar(host, orient="horizontal", command=self.placed_tree.xview)
        self.placed_tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.placed_tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns"); hs.grid(row=1, column=0, sticky="ew")
        self.placed_tree.column("name", width=235, minwidth=150)
        self.placed_tree.column("owner", width=145, minwidth=100)
        self.placed_tree.column("kind", width=85, minwidth=70, anchor="center")
        self.placed_tree.column("level", width=55, minwidth=45, anchor="center")
        self._update_placed_tree_headings()
        self.placed_tree.bind("<<TreeviewSelect>>", self.on_placed_tree)
        ttk.Label(left, textvariable=self.placed_count_var, style="Source.TLabel").pack(anchor="w", pady=(6, 0))
        placement_actions = ttk.Frame(left, padding=(0, 7, 8, 0))
        placement_actions.pack(fill="x")
        self.placed_add_btn = ttk.Button(
            placement_actions, text=self._msg("+ ADICIONAR UNIDADE", "+ ADD UNIT"),
            command=self.open_add_unit_dialog,
        )
        self.placed_add_btn.pack(fill="x", pady=(0, 4))
        self.placed_import_assets_btn = ttk.Button(
            placement_actions, text=self._msg("IMPORTAR MODELOS / TEXTURAS…", "IMPORT MODELS / TEXTURES…"),
            command=lambda: self.open_asset_importer(default_mode="create_place"),
        )
        self.placed_import_assets_btn.pack(fill="x", pady=(0, 4))
        self.placed_remove_added_btn = ttk.Button(
            placement_actions, text=self._msg("REMOVER UNIDADE", "REMOVE UNIT"),
            command=self.remove_selected_studio_placement,
        )
        self.placed_remove_added_btn.pack(fill="x")

        head = ttk.Frame(center, padding=(10, 0, 8, 8))
        head.pack(fill="x")
        head_top = ttk.Frame(head); head_top.pack(fill="x")
        ttk.Label(head_top, textvariable=self.placed_title_var, font=("Segoe UI", 15, "bold")).pack(side="left")
        ttk.Label(head_top, textvariable=self.placed_meta_var).pack(side="left", padx=14, fill="x", expand=True)
        self.placed_apply_btn = ttk.Button(
            head_top, text=self._msg("APLICAR NA INSTÂNCIA", "APPLY TO INSTANCE"), command=self.apply_placed_hero
        )
        self.placed_apply_btn.pack(side="right")

        # The center workspace intentionally mirrors War3AssetsImporter: the
        # map is a first-class surface instead of hiding placement behind X/Y
        # text fields. Properties stay available in a sibling tab.
        self.placed_center_tabs = ttk.Notebook(center)
        self.placed_center_tabs.pack(fill="both", expand=True, padx=(10, 8))
        self.placed_map_page = ttk.Frame(self.placed_center_tabs)
        self.placed_properties_page = ttk.Frame(self.placed_center_tabs)
        self.placed_center_tabs.add(self.placed_map_page, text=self._msg("MAPA", "MAP"))
        self.placed_center_tabs.add(self.placed_properties_page, text=self._msg("PROPRIEDADES", "PROPERTIES"))

        self.placed_map_view = MapPlacementCanvas(
            self.placed_map_page,
            msg=self._msg,
            on_select=self._select_placement_from_map,
            on_move=self._move_placement_from_map,
            on_rotate=self._rotate_placement_from_map,
            on_add=self.open_add_unit_dialog,
            on_remove=self._remove_placement_from_map,
        )
        self.placed_map_view.pack(fill="both", expand=True)

        self.placed_notebook = ttk.Notebook(self.placed_properties_page)
        self.placed_notebook.pack(fill="both", expand=True)
        placeholder = ttk.Frame(self.placed_notebook, padding=24)
        self.placed_notebook.add(placeholder, text=self._msg("Propriedades", "Properties"))
        self.placed_placeholder_label = ttk.Label(
            placeholder,
            text=self._msg(
                "Abra um mapa e selecione uma unidade colocada na lista à esquerda.",
                "Open a map and select a placed unit from the list on the left.",
            ),
            justify="center",
        )
        self.placed_placeholder_label.pack(expand=True)
        self.placed_apply_btn.state(["disabled"])
        self._build_placed_visual_workspace(visual)

    def _build_placed_visual_workspace(self, visual) -> None:
        ttk.Label(visual, text=self._msg("VISUALIZADOR", "VIEWER"), font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=(8, 8), pady=(0, 5))
        icon_card = ttk.LabelFrame(visual, text=self._msg("ÍCONE DA UNIDADE / HERÓI", "UNIT / HERO ICON"), padding=8)
        icon_card.pack(fill="x", padx=(8, 8), pady=(0, 8))
        icon_host = tk.Frame(icon_card, width=100, height=100, bg="#11151d", highlightthickness=1, highlightbackground="#343a46")
        icon_host.pack(anchor="w"); icon_host.pack_propagate(False)
        self.placed_icon_label = tk.Label(icon_host, text="—", fg="#8f9bad", bg="#11151d", font=("Segoe UI", 22, "bold"))
        self.placed_icon_label.pack(fill="both", expand=True)
        ttk.Button(icon_card, text=self._msg("Exportar ícones…", "Export icons…"), command=self.export_placed_icon_bundle).pack(anchor="w", pady=(6, 0))
        self.placed_icon_path_label=ttk.Label(icon_card, textvariable=self.placed_icon_path_var, style="Source.TLabel", wraplength=285, justify="left", anchor="w")
        self.placed_icon_path_label.pack(anchor="w", fill="x", pady=(4, 0))
        self.placed_icon_status_label=ttk.Label(icon_card, textvariable=self.placed_icon_status_var, justify="left", wraplength=285, anchor="w")
        self.placed_icon_status_label.pack(anchor="w", fill="x", pady=(4, 0))
        self.placed_visual_source_label=ttk.Label(icon_card, textvariable=self.placed_visual_source_var, style="Source.TLabel", justify="left", wraplength=285, anchor="w")
        self.placed_visual_source_label.pack(anchor="w", fill="x", pady=(2, 0))
        def _resize_placed_icon_text(event):
            safe=max(150,int(event.width)-24)
            for label in (self.placed_icon_path_label,self.placed_icon_status_label,self.placed_visual_source_label):
                label.configure(wraplength=safe)
        icon_card.bind("<Configure>",_resize_placed_icon_text,add="+")

        model_card = ttk.LabelFrame(visual, text=self._msg("MODELO 3D / PREVIEW", "3D MODEL / PREVIEW"), padding=8)
        model_card.pack(fill="both", expand=True, padx=(8, 8))
        head = ttk.Frame(model_card); head.pack(fill="x", pady=(0, 5))
        ttk.Label(head, text=self._msg("Modelos:", "Models:")).pack(side="left")
        self.placed_model_asset_mode_combo = ttk.Combobox(
            head, textvariable=self.placed_model_asset_mode_var, state="readonly", width=22,
            values=("Definitive DE", "Reforged HD", self._msg("Clássico / SD", "Classic / SD")),
        )
        self.placed_model_asset_mode_combo.pack(side="left", fill="x", expand=True, padx=(7, 0))
        self.placed_model_asset_mode_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_placed_visuals())
        ttk.Label(model_card, textvariable=self.placed_preview_status_var, anchor="w", justify="left", wraplength=350).pack(fill="x", pady=(0, 6))
        self.placed_preview_host = tk.Frame(model_card, bg="#0d0f14", height=430, highlightthickness=1, highlightbackground="#2b2f38")
        self.placed_preview_host.pack(fill="both", expand=True)
        self.placed_preview_fallback = tk.Label(
            self.placed_preview_host, text=self._msg("Inicializando WebView2…", "Initializing WebView2…"),
            fg="#9aa3b5", bg="#0d0f14", justify="center",
        )
        self.placed_preview_fallback.place(relx=.5, rely=.5, anchor="center")

    def _update_placed_tree_headings(self) -> None:
        tree = getattr(self, "placed_tree", None)
        if tree is None:
            return
        labels = {
            "name": self._msg("Unidade colocada", "Placed unit"),
            "owner": self._msg("Dono", "Owner"),
            "kind": self._msg("Tipo", "Type"),
            "level": self._msg("Nível", "Level"),
        }
        for col, label in labels.items():
            tree.heading(col, text=label)

    def _placed_kind_label(self, unit: UnitPlacement) -> str:
        return self._msg("Herói", "Hero") if self._placed_unit_is_hero(unit) else self._msg("Unidade/Item", "Unit/Item")

    def _select_placement_from_map(self, placement_index: int) -> None:
        """Select a minimap marker by unique file-order placement index."""
        if not self.session:
            return
        index = int(placement_index)
        iid = f"placement:{index}"
        tree = getattr(self, "placed_tree", None)
        if tree is None:
            return
        if not tree.exists(iid):
            # A search/filter may hide the clicked marker. Clear only the
            # placement-list filter so the map always remains authoritative.
            self.placed_search_var.set("")
            self.placed_filter_var.set("all")
            try:
                self.placed_filter_combo.current(0)
                self.placed_filter_display_var.set(self._placed_filter_values()[0])
            except Exception:
                pass
            self.refresh_placed_units_tree(select_id=index)
        if tree.exists(iid):
            self._placed_tree_guard = False
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)
            self.on_placed_tree()

    def _move_placement_from_map(self, placement_index: int, x: float, y: float) -> None:
        if not self.session:
            return
        try:
            index = int(placement_index)
            unit = self.session.move_placed_unit_at(index, float(x), float(y))
            if self._placed_snapshot is not None and self._placed_index == index:
                self._placed_snapshot.x = float(x)
                self._placed_snapshot.y = float(y)
                try:
                    self.placed_position_label.configure(text=f"X {unit.x:.1f}  Y {unit.y:.1f}  Z {unit.z:.1f}")
                except Exception:
                    pass
            self.refresh_placed_units_tree(select_id=index)
            self.status.set(self._msg(
                f"Instância #{unit.creation_number} movida para X {unit.x:.1f}, Y {unit.y:.1f}. SALVAR grava o DOO; runtime só é sincronizado quando o creation_number é único.",
                f"Instance #{unit.creation_number} moved to X {unit.x:.1f}, Y {unit.y:.1f}. SAVE writes the DOO; runtime is synchronized only when creation_number is unique.",
            ))
        except Exception as exc:
            LOGGER.exception("Visual placement move failed")
            messagebox.showerror(self._msg("Mover unidade", "Move unit"), str(exc), parent=self)

    def _rotate_placement_from_map(self, placement_index: int, angle_degrees: float) -> None:
        if not self.session:
            return
        try:
            index = int(placement_index)
            unit = self.session.rotate_placed_unit_at(index, float(angle_degrees))
            if self._placed_snapshot is not None and self._placed_index == index:
                self._placed_snapshot.angle = float(unit.angle)
                try:
                    self.placed_facing_var.set(f"{float(angle_degrees) % 360.0:.3f}")
                except Exception:
                    pass
            self.refresh_placed_units_tree(select_id=index)
            self.status.set(self._msg(
                f"Instância #{unit.creation_number} rotacionada para {float(angle_degrees) % 360.0:.1f}°. SALVAR grava o DOO; runtime só é sincronizado quando o creation_number é único.",
                f"Instance #{unit.creation_number} rotated to {float(angle_degrees) % 360.0:.1f}°. SAVE writes the DOO; runtime is synchronized only when creation_number is unique.",
            ))
        except Exception as exc:
            LOGGER.exception("Visual placement rotation failed")
            messagebox.showerror(self._msg("Rotacionar unidade", "Rotate unit"), str(exc), parent=self)

    def _remove_placement_from_map(self, placement_index: int) -> None:
        try:
            self._select_placement_from_map(int(placement_index))
        except Exception:
            pass
        self.remove_selected_studio_placement()

    def refresh_placed_map_units(self, selected: int | None = None) -> None:
        view = getattr(self, "placed_map_view", None)
        if view is None:
            return
        units = self.session.placed_units_list() if self.session else []
        added = self.session.studio_added_placements if self.session else set()
        if selected is None:
            selected = getattr(self, "_placed_index", None)
        view.set_units(units, studio_added=added, selected=selected)

    def refresh_placed_map_view(self) -> None:
        """Load the map minimap + W3E bounds without blocking the Tk event loop."""
        view = getattr(self, "placed_map_view", None)
        if view is None:
            return
        if not self.session:
            view.set_map(None, view._bounds, "")
            view.set_units([])
            return
        map_path = Path(self.session.map_path)
        units = list(self.session.placed_units_list())
        token = getattr(self, "_placed_map_load_serial", 0) + 1
        self._placed_map_load_serial = token
        view.status_var.set(self._msg("Reconstruindo terreno e carregando placements…", "Rebuilding terrain and loading placements…"))

        def worker():
            try:
                image, bounds, source = load_map_placement_art(map_path, units)
                self.after(0, lambda: self._show_placed_map_result(token, image, bounds, source))
            except Exception as exc:
                LOGGER.warning("Could not load placement map surface", exc_info=True)
                self.after(0, lambda e=exc: self._show_placed_map_error(token, e))

        threading.Thread(target=worker, daemon=True, name="wc3-placement-map").start()

    def _show_placed_map_result(self, token, image, bounds, source) -> None:
        if token != getattr(self, "_placed_map_load_serial", None):
            return
        view = getattr(self, "placed_map_view", None)
        if view is None:
            return
        view.set_map(image, bounds, source)
        self.refresh_placed_map_units()
        LOGGER.info(
            "Placement map ready source=%s bounds=(%.1f,%.1f)-(%.1f,%.1f) units=%d",
            source or "grid", bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y,
            len(self.session.placed_units_list()) if self.session else 0,
        )

    def _show_placed_map_error(self, token, exc: BaseException) -> None:
        if token != getattr(self, "_placed_map_load_serial", None):
            return
        view = getattr(self, "placed_map_view", None)
        if view is None:
            return
        # Coordinate grid remains fully usable even when the minimap BLP is
        # absent/corrupt, so placement never depends on artwork decoding.
        try:
            from .map_placement import infer_bounds_from_units
            bounds = infer_bounds_from_units(self.session.placed_units_list() if self.session else [])
            view.set_map(None, bounds, "")
            self.refresh_placed_map_units()
            view.status_var.set(self._msg(
                f"Minimapa indisponível ({type(exc).__name__}); usando grade de coordenadas.",
                f"Minimap unavailable ({type(exc).__name__}); using coordinate grid.",
            ))
        except Exception:
            LOGGER.debug("Could not install placement-map fallback", exc_info=True)

    def refresh_placed_units_tree(self, select_id: int | str | None = None) -> None:
        tree = getattr(self, "placed_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        self._update_placed_tree_headings()
        if not self.session or self.session.placed_units is None:
            self.placed_count_var.set(self._msg("war3mapUnits.doo indisponível", "war3mapUnits.doo unavailable"))
            return
        query = self.placed_search_var.get().strip().casefold()
        mode = self.placed_filter_var.get()
        rows = []
        for placement_index, unit in enumerate(self.session.placed_units_list()):
            if mode == "heroes" and not self._placed_unit_is_hero(unit):
                continue
            if mode == "other" and self._placed_unit_is_hero(unit):
                continue
            name = self._placed_unit_display_name(unit.unit_id)
            owner = player_label(unit.player, self.language)
            kind = self._placed_kind_label(unit)
            hay = f"{unit.creation_number} {unit.unit_id} {name} {owner} {kind} {unit.hero_level}".casefold()
            if query and query not in hay:
                continue
            rows.append((placement_index, unit, name, owner, kind))
        rows.sort(key=lambda r: (r[2].casefold(), r[1].creation_number, r[0]))
        for placement_index, unit, name, owner, kind in rows:
            # Tk Treeview item IDs must be unique. creation_number is *not* a
            # safe IID because externally modified maps can contain duplicates.
            iid = f"placement:{placement_index}"
            level = str(unit.hero_level) if self._placed_unit_is_hero(unit) else "—"
            tree.insert("", "end", iid=iid, values=(f"#{unit.creation_number}  {name}  [{unit.unit_id}]", owner, kind, level))
        total = len(self.session.placed_units.units)
        self.placed_count_var.set(self._msg(f"{len(rows)} exibidos · {total} placements no mapa", f"{len(rows)} shown · {total} placements in map"))
        if select_id is None:
            select_id = getattr(self, "_placed_index", None)
        if select_id is not None:
            iid = f"placement:{int(select_id)}"
            if tree.exists(iid):
                self._placed_tree_guard = True
                try:
                    tree.selection_set(iid); tree.see(iid)
                finally:
                    self._placed_tree_guard = False
        try:
            self.refresh_placed_map_units(selected=int(select_id) if select_id is not None else None)
        except Exception:
            LOGGER.debug("Could not refresh placement map markers", exc_info=True)

    def _placed_ui_signature(self):
        """Return a stable signature of what is visibly editable in the placement UI.

        The previous implementation rebuilt a UnitPlacement and compared it with
        the binary snapshot. That produced false positives for untouched units,
        especially angle/target-acquisition floats read from war3mapUnits.doo.
        Here we compare the UI state exactly as loaded, so a prompt appears only
        after the user actually changes an editable control/list.
        """
        owner_label = self.placed_owner_var.get()
        owner = self._placed_owner_lookup.get(owner_label, owner_label)
        abilities = tuple(
            (
                str(row.get("id", "")),
                int(row.get("level", 0)),
                bool(row.get("autocast", False)),
                bool(row.get("persist", False)),
                bool(row.get("base", False)),
            )
            for row in self.placed_ability_rows
        )
        drops = tuple(
            (int(row.get("set", 0)), str(row.get("id", "")), int(row.get("chance", 0)))
            for row in self.placed_drop_rows
        )
        return (
            owner,
            self.placed_facing_var.get(),
            self.placed_hp_var.get(),
            self.placed_mana_var.get(),
            self.placed_acq_var.get(),
            bool(getattr(self, "_placed_is_hero", False)),
            self.placed_level_var.get(),
            bool(self.placed_use_default_attrs.get()),
            self.placed_str_var.get(),
            self.placed_agi_var.get(),
            self.placed_int_var.get(),
            tuple(var.get() for var in self.placed_inventory_vars),
            abilities,
            self.placed_drop_mode_var.get(),
            self.placed_drop_pointer_var.get(),
            drops,
        )

    def _placed_unit_ui_dirty(self) -> bool:
        if self._placed_snapshot is None or self._placed_index is None:
            return False
        baseline = getattr(self, "_placed_ui_initial_signature", None)
        if baseline is None:
            return False
        try:
            return self._placed_ui_signature() != baseline
        except Exception:
            LOGGER.debug("Could not compare placed-unit UI signature", exc_info=True)
            return False

    def on_placed_tree(self, _event=None) -> None:
        if self._placed_tree_guard or not self.session:
            return
        sel = self.placed_tree.selection() if getattr(self, "placed_tree", None) else ()
        if not sel:
            return
        try:
            placement_index = int(sel[0].split(":", 1)[1])
        except Exception:
            return
        if self._placed_index == placement_index:
            return
        if self._placed_unit_ui_dirty():
            leave = messagebox.askyesno(
                self._msg("Alterações não aplicadas", "Unapplied changes"),
                self._msg(
                    "Existem alterações não aplicadas nesta unidade colocada. Deseja descartar e trocar de instância?",
                    "There are unapplied changes in this placed unit. Discard them and switch instance?",
                ), parent=self,
            )
            if not leave:
                old = self._placed_index
                if old is not None:
                    self.refresh_placed_units_tree(select_id=old)
                return
        unit = self.session.placed_unit_at(placement_index)
        if unit is None:
            return
        self.placed_title_var.set(self._placed_unit_display_name(unit.unit_id))
        self.placed_meta_var.set(self._msg(
            f"{unit.unit_id} · instância #{unit.creation_number} · registro {placement_index} · {player_label(unit.player, self.language)}" + (f" · nível {unit.hero_level}" if self._placed_unit_is_hero(unit) else ""),
            f"{unit.unit_id} · instance #{unit.creation_number} · record {placement_index} · {player_label(unit.player, self.language)}" + (f" · level {unit.hero_level}" if self._placed_unit_is_hero(unit) else ""),
        ))
        self._placed_index = placement_index
        self.build_placed_hero_form(unit)
        self.refresh_placed_visuals(unit)
        try:
            self.placed_map_view.set_selected(placement_index)
        except Exception:
            LOGGER.debug("Could not mirror placement selection on map", exc_info=True)
        try:
            self.placed_apply_btn.state(["!disabled"])
        except Exception:
            pass

    def refresh_placed_units_language(self) -> None:
        if not hasattr(self, "placed_tree"):
            return
        draft = None
        snapshot = copy.deepcopy(self._placed_snapshot) if self._placed_snapshot is not None else None
        initial_ui_signature = getattr(self, "_placed_ui_initial_signature", None)
        if self._placed_snapshot is not None:
            try:
                draft = self._placed_collect_unit(self._placed_snapshot)
            except Exception:
                draft = copy.deepcopy(self._placed_snapshot)
        values = self._placed_filter_values()
        self.placed_filter_combo.configure(values=values)
        idx = {"all": 0, "heroes": 1, "other": 2}.get(self.placed_filter_var.get(), 0)
        self.placed_filter_display_var.set(values[idx])
        self._update_placed_tree_headings()
        for attr, pt, en in (
            ("placed_add_btn", "+ ADICIONAR UNIDADE", "+ ADD UNIT"),
            ("placed_import_assets_btn", "IMPORTAR MODELOS / TEXTURAS…", "IMPORT MODELS / TEXTURES…"),
            ("placed_remove_added_btn", "REMOVER UNIDADE", "REMOVE UNIT"),
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.configure(text=self._msg(pt, en))
                except Exception:
                    pass
        try:
            if hasattr(self, "placed_center_tabs"):
                self.placed_center_tabs.tab(self.placed_map_page, text=self._msg("MAPA", "MAP"))
                self.placed_center_tabs.tab(self.placed_properties_page, text=self._msg("PROPRIEDADES", "PROPERTIES"))
            if hasattr(self, "placed_map_view"):
                self.placed_map_view.apply_language()
        except Exception:
            LOGGER.debug("Could not refresh placement map language", exc_info=True)
        if hasattr(self, "placed_model_asset_mode_combo"):
            current_mode = self.placed_model_asset_mode_var.get()
            self.placed_model_asset_mode_combo.configure(values=(
                "Definitive DE", "Reforged HD", self._msg("Clássico / SD", "Classic / SD")
            ))
            if "classic" in current_mode.casefold() or "cláss" in current_mode.casefold() or "sd" in current_mode.casefold():
                self.placed_model_asset_mode_var.set(self._msg("Clássico / SD", "Classic / SD"))
        if draft is not None:
            self.build_placed_hero_form(draft)
            self._placed_snapshot = snapshot
            # Preserve whether the draft was dirty before the language rebuild.
            self._placed_ui_initial_signature = initial_ui_signature
        self.refresh_placed_units_tree(select_id=self._placed_index)

    def _placed_asset_preference(self) -> str:
        value = str(self.placed_model_asset_mode_var.get() if hasattr(self, "placed_model_asset_mode_var") else "Reforged HD").casefold()
        if "definitive" in value or value.strip().startswith("de"):
            return "de"
        if "cláss" in value or "classic" in value or "sd" in value:
            return "classic"
        return "hd"

    def _placed_native_base(self, rawcode: str) -> str:
        if not self.session:
            return rawcode
        rec = self.session.get_record("unit", "custom", rawcode)
        if rec is not None and getattr(rec, "original_id", ""):
            return str(rec.original_id)
        return rawcode

    def _init_placed_embedded_viewer(self) -> None:
        if getattr(self, "placed_embedded_view", None) is not None or not hasattr(self, "placed_preview_host"):
            return
        try:
            from tkwry import WebSession, WebView
            self.update_idletasks()
            if self.placed_preview_host.winfo_width() <= 1 or self.placed_preview_host.winfo_height() <= 1:
                self.after(220, self._init_placed_embedded_viewer)
                return
            if getattr(self, "web_session", None) is None:
                self.web_session = WebSession(data_directory=str(self.webview_data_dir))
            self.placed_preview_fallback.configure(text=self._msg("Inicializando visualizador 3D…", "Initializing 3D viewer…"))
            self.placed_preview_fallback.place(relx=.5, rely=.5, anchor="center")
            self.placed_embedded_view = WebView(
                self.placed_preview_host,
                url=prepare_viewer_url(compact=True),
                session=self.web_session,
                width=max(340, self.placed_preview_host.winfo_width()),
                height=max(360, self.placed_preview_host.winfo_height()),
                default_context_menus=False,
                hotkeys_zoom=False,
                focused=False,
            )
            self.placed_embedded_view.pack(fill="both", expand=True)
            self.placed_embedded_view.when_ready(self._placed_web_ready)
            self.placed_embedded_view.when_failed(self._placed_web_failed)
            LOGGER.info("Placed-units embedded WebView2 creation requested")
        except Exception as exc:
            log_exception(LOGGER, "placed units WebView2 init", exc)
            self.placed_embedded_view = None
            try:
                self.placed_preview_fallback.configure(text=self._msg("Viewer 3D indisponível. Veja o log.", "3D viewer unavailable. See log."))
            except Exception:
                pass

    def _placed_web_ready(self, *_):
        LOGGER.info("Placed-units embedded WebView2 ready")
        try:
            self.placed_preview_fallback.place_forget()
        except Exception:
            pass
        if getattr(self, "_placed_index", None) is not None:
            self.refresh_placed_visuals()

    def _placed_web_failed(self, exc):
        log_exception(LOGGER, "placed units WebView2 create", exc)
        self.placed_embedded_view = None
        self.placed_preview_status_var.set(self._msg(
            f"WebView2 falhou: {exc}",
            f"WebView2 failed: {self._localize_error_text(exc)}",
        ))
        try:
            self.placed_preview_fallback.configure(text=self._msg("ERRO 3D\nVeja o log do WebView2", "3D ERROR\nSee WebView2 log"))
            self.placed_preview_fallback.place(relx=.5, rely=.5, anchor="center")
        except Exception:
            pass

    def _placed_load_preview_url(self, url: str) -> bool:
        view = getattr(self, "placed_embedded_view", None)
        if view is None:
            self._init_placed_embedded_viewer()
            return False
        if getattr(self, "placed_preview_last_url", None) == url:
            return True
        try:
            view.load_url(url)
            self.placed_preview_last_url = url
            return True
        except Exception as exc:
            log_exception(LOGGER, "placed preview load_url", exc)
            return False

    def _clear_placed_visuals(self, message: str | None = None) -> None:
        if hasattr(self, "placed_icon_label"):
            self.placed_icon_label.configure(image="", text="—")
        self.placed_icon_photo = None
        self.placed_icon_path_var.set("")
        self.placed_icon_status_var.set(message or self._msg("Ícone não disponível", "Icon unavailable"))
        self.placed_visual_source_var.set("")
        self.placed_preview_status_var.set(message or self._msg("Sem modelo 3D", "No 3D model"))
        try:
            self._placed_load_preview_url(prepare_viewer_url(compact=True))
        except Exception:
            pass

    def refresh_placed_visuals(self, unit: UnitPlacement | None = None) -> None:
        if not self.session:
            self._clear_placed_visuals(self._msg("Abra um mapa primeiro.", "Open a map first."))
            return
        if unit is None and self._placed_index is not None:
            unit = self.session.placed_unit_at(self._placed_index)
        if unit is None:
            self._clear_placed_visuals(self._msg("Selecione uma unidade colocada.", "Select a placed unit."))
            return

        self.placed_visual_serial += 1
        serial = self.placed_visual_serial
        mode = self._placed_asset_preference()
        self.placed_preview_status_var.set(self._msg("Carregando modelo da instância…", "Loading instance model…"))
        self.placed_icon_status_var.set(self._msg("Carregando ícone…", "Loading icon…"))
        rawcode = unit.unit_id
        table = self._placed_unit_table(rawcode)
        native_base = self._placed_native_base(rawcode)
        skin_raw = str(unit.skin_id or "").strip("\0 ")
        map_path = Path(self.session.map_path)

        try:
            model_mod, model_source, _ = self.session.resolve_map_value("unit", table, rawcode, "umdl", level=0, column=0)
            icon_mod, icon_source, _ = self.session.resolve_map_value("unit", table, rawcode, "uico", level=0, column=0)
            map_model = str(model_mod.value).strip() if model_mod is not None else ""
            map_icon = str(icon_mod.value).strip() if icon_mod is not None else ""
        except Exception:
            LOGGER.debug("Placed visual map-value lookup failed for %s", rawcode, exc_info=True)
            map_model = map_icon = ""
            model_source = icon_source = ""

        def worker():
            started = time.perf_counter()
            casc = None
            files: list[Path] = []
            missing: list[str] = []
            model_path = None
            model_actual = None
            icon_data = None
            icon_actual = None
            resolved_source = model_source or icon_source or f"{rawcode}"
            tmp = Path(tempfile.mkdtemp(prefix="wc3_placed_preview_"))
            self.temp_dirs.append(tmp)
            try:
                install = detect_warcraft_install()
                if install:
                    try:
                        casc = get_shared_casc(install)
                    except Exception:
                        LOGGER.debug("Placed preview CASC unavailable", exc_info=True)

                # A Reforged per-placement skin ID is the strongest native visual
                # selector. Custom map model/icon overrides remain stronger than a
                # stock base profile because they are explicit object art.
                live_visual = {}
                if casc is not None:
                    for visual_raw in (skin_raw, native_base, rawcode):
                        if not visual_raw or len(visual_raw) != 4:
                            continue
                        try:
                            candidate = resolve_unit_visual(casc, visual_raw, preference=mode)
                        except Exception:
                            continue
                        if candidate.get("model") or candidate.get("icon"):
                            live_visual = candidate
                            resolved_source = candidate.get("source", resolved_source)
                            break

                native_model_source = ("base nativa" in (model_source or "").casefold()) or table == "native"
                native_icon_source = ("base nativa" in (icon_source or "").casefold()) or table == "native"
                model_ref = (str(live_visual.get("model") or "").strip() if native_model_source else map_model) or map_model or str(live_visual.get("model") or "").strip()
                icon_ref = (str(live_visual.get("icon") or "").strip() if native_icon_source else map_icon) or map_icon or str(live_visual.get("icon") or "").strip()

                with StormArchive(map_path, read_only=True) as arc:
                    model_refs = [model_ref] if model_ref else []
                    # If the live visual points at a cinematic/nonexistent alias,
                    # reuse the same candidate search used by the Object Editor.
                    if casc is not None and native_base:
                        try:
                            from ..unit_native import unit_model_candidates
                            for ref in unit_model_candidates(casc, skin_raw or native_base, preference=mode):
                                if ref and ref.casefold() not in {x.casefold() for x in model_refs}:
                                    model_refs.append(ref)
                        except Exception:
                            LOGGER.debug("Placed model candidate expansion failed", exc_info=True)
                    for ref in model_refs:
                        model_actual, model_path = self._extract_model_bundle_from_arc(
                            arc, ref, tmp, files, missing,
                            casc=casc, casc_preference=mode, serial=None,
                        )
                        if model_path is not None:
                            break

                    if icon_ref:
                        found = self._resolve_archive_path(arc, icon_ref)
                        if found:
                            try:
                                icon_actual, icon_data = found, arc.read(found)
                            except Exception:
                                icon_data = None
                        if icon_data is None and casc is not None:
                            try:
                                icon_actual, icon_data = casc.read_with_path(icon_ref, preference=mode)
                            except Exception:
                                icon_data = None

                LOGGER.info(
                    "PLACED_VISUAL[%s] ready id=%s raw=%s model=%s icon=%s files=%d missing=%d mode=%s total=%.1f ms",
                    serial, unit.creation_number, rawcode, model_actual, icon_actual,
                    len(files), len(missing), mode, (time.perf_counter()-started)*1000.0,
                )
                self.after(0, lambda: self._show_placed_visual_result(
                    serial, unit.creation_number, rawcode, files, model_path, model_actual,
                    missing, icon_data, icon_actual, resolved_source, mode,
                ))
            except Exception as exc:
                log_exception(LOGGER, f"placed visual {unit.creation_number}/{rawcode}", exc)
                self.after(0, lambda e=exc: self._placed_visual_failed(serial, rawcode, e))

        threading.Thread(target=worker, daemon=True, name="wc3-placed-visual").start()

    def _show_placed_visual_result(self, serial: int, creation: int, rawcode: str, files, model_path, model_actual, missing, icon_data, icon_actual, source: str, mode: str) -> None:
        if serial != self.placed_visual_serial:
            return
        if icon_data:
            try:
                photo = self._decode_icon_photo(icon_data, 92)
                self.placed_icon_photo = photo
                self.placed_icon_label.configure(image=photo, text="")
                self.placed_icon_path_var.set(Path(str(icon_actual or "").replace("\\", "/")).name)
                self.placed_icon_status_var.set(self._msg("Ícone carregado do MPQ/CASC", "Icon loaded from MPQ/CASC"))
            except Exception as exc:
                self.placed_icon_label.configure(image="", text="!")
                self.placed_icon_status_var.set(f"{type(exc).__name__}: {self._localize_error_text(exc)}")
        else:
            self.placed_icon_label.configure(image="", text="—")
            self.placed_icon_status_var.set(self._msg("Ícone não encontrado", "Icon not found"))
            self.placed_icon_path_var.set("")
        self.placed_visual_source_var.set(self._localize_source_text(source))

        if model_path is not None and Path(model_path).is_file():
            valid_files = [Path(x) for x in files if Path(x).is_file()]
            url = prepare_viewer_url(valid_files, Path(model_path), compact=True)
            loaded = self._placed_load_preview_url(url)
            tex = max(0, len(valid_files)-1)
            label = Path(str(model_actual or model_path).replace("\\", "/")).name
            self.placed_preview_status_var.set(self._msg(
                f"{label} · {tex} textura(s)" + (f" · {len(missing)} ausente(s)" if missing else "") + ("" if loaded else " · aguardando viewer"),
                f"{label} · {tex} texture(s)" + (f" · {len(missing)} missing" if missing else "") + ("" if loaded else " · waiting for viewer"),
            ))
        else:
            self._placed_load_preview_url(prepare_viewer_url(compact=True))
            self.placed_preview_status_var.set(self._msg(
                f"{rawcode}: modelo 3D não encontrado para {mode.upper()}",
                f"{rawcode}: no 3D model found for {mode.upper()}",
            ))

    def _placed_visual_failed(self, serial: int, rawcode: str, exc: BaseException) -> None:
        if serial != self.placed_visual_serial:
            return
        self.placed_preview_status_var.set(self._msg(
            f"{rawcode}: erro no preview — {type(exc).__name__}: {exc}",
            f"{rawcode}: preview error — {type(exc).__name__}: {self._localize_error_text(exc)}",
        ))

    @staticmethod
    def _grid_label(parent, text: str, row: int, column: int = 0):
        label = ttk.Label(parent, text=text)
        label.grid(row=row, column=column, sticky="w", padx=(0, 8), pady=5)
        return label

    def build_placed_hero_form(self, unit: UnitPlacement) -> None:
        self._ensure_placed_state()
        self._destroy_object_form_tabs()
        notebook = self._placed_form_notebook()
        apply_btn = getattr(self, "placed_apply_btn", None) or self.apply_btn
        apply_btn.configure(
            text=self._msg("APLICAR NA INSTÂNCIA", "APPLY TO INSTANCE"),
            command=self.apply_placed_hero,
        )

        # ---------- General ----------
        general = ttk.Frame(notebook, padding=14)
        notebook.add(general, text=self._msg("Geral", "General"))
        general.columnconfigure(1, weight=1)
        general.columnconfigure(3, weight=1)
        info = ttk.LabelFrame(general, text=self._msg("Instância no mapa", "Map instance"), padding=10)
        info.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        ttk.Label(info, text=f"Rawcode: {unit.unit_id}").pack(side="left")
        ttk.Label(info, text=f"ID: {unit.creation_number}").pack(side="left", padx=18)
        self.placed_position_label = ttk.Label(info, text=f"X {unit.x:.1f}  Y {unit.y:.1f}  Z {unit.z:.1f}")
        self.placed_position_label.pack(side="left")

        owner_choices = player_choices(self.language)
        self._placed_owner_lookup = {label: value for label, value in owner_choices}
        self._grid_label(general, self._msg("Dono / Player:", "Owner / Player:"), 1)
        self.placed_owner_combo = ttk.Combobox(
            general, textvariable=self.placed_owner_var,
            values=[label for label, _ in owner_choices], state="readonly", width=32,
        )
        self.placed_owner_combo.grid(row=1, column=1, sticky="ew", pady=5)
        self._grid_label(general, self._msg("Direção / Facing:", "Facing:"), 1, 2)
        ttk.Spinbox(general, from_=-360, to=360, increment=1, textvariable=self.placed_facing_var, width=10).grid(row=1, column=3, sticky="w", pady=5)

        self._grid_label(general, self._msg("Pontos de vida %:", "Hit Points %:"), 2)
        ttk.Spinbox(general, from_=-1, to=100000, textvariable=self.placed_hp_var, width=12).grid(row=2, column=1, sticky="w", pady=5)
        self._grid_label(general, self._msg("Pontos de mana %:", "Mana Points %:"), 2, 2)
        ttk.Spinbox(general, from_=-1, to=100000, textvariable=self.placed_mana_var, width=12).grid(row=2, column=3, sticky="w", pady=5)

        self.placed_level_label = self._grid_label(general, self._msg("Nível do herói:", "Hero level:"), 3)
        self.placed_level_spin = ttk.Spinbox(general, from_=1, to=9999, textvariable=self.placed_level_var, width=12)
        self.placed_level_spin.grid(row=3, column=1, sticky="w", pady=5)
        self._grid_label(general, self._msg("Aquisição de alvo:", "Target acquisition:"), 3, 2)
        acq = ttk.Combobox(
            general, textvariable=self.placed_acq_var, width=22,
            values=("-1", "-2", "0", "200", "500", "600", "800", "1000"),
        )
        acq.grid(row=3, column=3, sticky="ew", pady=5)
        ttk.Label(
            general,
            text=self._msg("-1 = normal · -2 = camp", "-1 = normal · -2 = camp"),
            style="Source.TLabel",
        ).grid(row=4, column=3, sticky="w")
        runtime_row = ttk.Frame(general)
        runtime_row.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        runtime_row.columnconfigure(0, weight=1)
        ttk.Label(
            runtime_row,
            text=self._msg(
                "Runtime sync: nível, dono, facing, HP/mana, aquisição, atributos e inventário alterados aqui recebem somente o patch JASS necessário.",
                "Runtime sync: level, owner, facing, HP/mana, acquisition, attributes and inventory changed here receive only the required JASS patch.",
            ),
            style="Source.TLabel", wraplength=720, justify="left",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            runtime_row,
            text=self._msg("SINCRONIZAR RUNTIME", "SYNC RUNTIME"),
            command=self.force_sync_placed_runtime,
        ).grid(row=0, column=1, sticky="e", padx=(12, 0))
        ttk.Label(
            runtime_row,
            text=self._msg(
                "Use o botão somente para reparar um mapa salvo por uma versão antiga quando o DOO já está certo, mas o jogo ainda usa o JASS antigo.",
                "Use the button only to repair a map saved by an older version when the DOO is already correct but the game still uses stale JASS.",
            ),
            style="Source.TLabel", wraplength=900, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(3, 0))

        attrs = ttk.LabelFrame(general, text=self._msg("Atributos do herói", "Hero attributes"), padding=10)
        self.placed_attrs_frame = attrs
        attrs.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        attrs.columnconfigure(5, weight=1)
        ttk.Checkbutton(
            attrs, text=self._msg("Usar atributos padrão", "Use Default Attributes"),
            variable=self.placed_use_default_attrs, command=self._placed_toggle_attrs,
        ).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 8))
        ttk.Label(attrs, text=self._msg("Força:", "Strength:")).grid(row=1, column=0, sticky="w")
        self.placed_str_spin = ttk.Spinbox(attrs, from_=0, to=99999, textvariable=self.placed_str_var, width=10)
        self.placed_str_spin.grid(row=1, column=1, sticky="w", padx=(5, 18))
        ttk.Label(attrs, text=self._msg("Agilidade:", "Agility:")).grid(row=1, column=2, sticky="w")
        self.placed_agi_spin = ttk.Spinbox(attrs, from_=0, to=99999, textvariable=self.placed_agi_var, width=10)
        self.placed_agi_spin.grid(row=1, column=3, sticky="w", padx=(5, 18))
        ttk.Label(attrs, text=self._msg("Inteligência:", "Intelligence:")).grid(row=1, column=4, sticky="w")
        self.placed_int_spin = ttk.Spinbox(attrs, from_=0, to=99999, textvariable=self.placed_int_var, width=10)
        self.placed_int_spin.grid(row=1, column=5, sticky="w", padx=(5, 0))

        # ---------- Abilities ----------
        abilities = ttk.Frame(notebook, padding=12)
        notebook.add(abilities, text=self._msg("Habilidades", "Abilities"))
        abilities.rowconfigure(1, weight=1); abilities.columnconfigure(0, weight=1)
        ttk.Label(
            abilities,
            text=self._msg(
                "Habilidades do tipo + overrides desta instância. Editar um rawcode cria/atualiza o bloco de habilidade do herói colocado.",
                "Type abilities + overrides for this instance. Editing a rawcode creates/updates the placed hero ability block.",
            ),
            style="Source.TLabel", wraplength=760, justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.placed_abilities_tree = ttk.Treeview(
            abilities, columns=("id", "name", "active", "autocast", "level", "source"), show="headings", selectmode="browse"
        )
        for col, text, width in (
            ("id", "ID", 76), ("name", self._msg("Habilidade", "Ability"), 260),
            ("active", self._msg("Ativa", "Active"), 70),
            ("autocast", self._msg("Autocast", "Autocast"), 82),
            ("level", self._msg("Nível", "Level"), 66),
            ("source", self._msg("Origem", "Source"), 120),
        ):
            self.placed_abilities_tree.heading(col, text=text)
            self.placed_abilities_tree.column(col, width=width, anchor="w")
        self.placed_abilities_tree.grid(row=1, column=0, sticky="nsew")
        ab_scroll = ttk.Scrollbar(abilities, orient="vertical", command=self.placed_abilities_tree.yview)
        ab_scroll.grid(row=1, column=1, sticky="ns"); self.placed_abilities_tree.configure(yscrollcommand=ab_scroll.set)

        # Keep the three per-instance controls visible instead of hiding them
        # behind a sequence of modal dialogs. This mirrors the World Editor
        # placement properties more closely: Active, Autocast and Level are
        # always editable for the selected row.
        ab_editor = ttk.LabelFrame(abilities, text=self._msg("Configuração da habilidade selecionada", "Selected ability settings"), padding=(10, 7))
        ab_editor.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.placed_ability_active_check = ttk.Checkbutton(
            ab_editor, text=self._msg("Ativa nesta instância", "Active on this instance"),
            variable=self.placed_ability_active_var, command=self._placed_apply_ability_controls,
        )
        self.placed_ability_active_check.pack(side="left")
        self.placed_ability_autocast_check = ttk.Checkbutton(
            ab_editor, text=self._msg("Autocast ativo", "Autocast active"),
            variable=self.placed_ability_autocast_var, command=self._placed_apply_ability_controls,
        )
        self.placed_ability_autocast_check.pack(side="left", padx=(16, 0))
        ttk.Label(ab_editor, text=self._msg("Nível:", "Level:")).pack(side="left", padx=(18, 5))
        self.placed_ability_level_spin = ttk.Spinbox(
            ab_editor, from_=0, to=999, width=7, textvariable=self.placed_ability_level_edit_var,
            command=self._placed_apply_ability_controls,
        )
        self.placed_ability_level_spin.pack(side="left")
        self.placed_ability_level_spin.bind("<FocusOut>", lambda _e: self._placed_apply_ability_controls())
        self.placed_ability_level_spin.bind("<Return>", lambda _e: self._placed_apply_ability_controls())
        ttk.Label(
            ab_editor,
            text=self._msg(
                "Ativa = grava/remove o override da instância; Autocast = estado inicial do autocast.",
                "Active = writes/removes the instance override; Autocast = initial autocast state.",
            ),
            style="Source.TLabel",
        ).pack(side="left", padx=(16, 0))

        ab_buttons = ttk.Frame(abilities); ab_buttons.grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(ab_buttons, text=self._msg("+ Adicionar", "+ Add"), command=self._placed_add_ability).pack(side="left")
        ttk.Button(ab_buttons, text=self._msg("Editar rawcode", "Edit rawcode"), command=self._placed_edit_ability).pack(side="left", padx=6)
        ttk.Button(ab_buttons, text=self._msg("Exportar ícones", "Export icons"), command=self._placed_export_selected_ability_icons).pack(side="left", padx=(0,6))
        ttk.Button(ab_buttons, text=self._msg("Remover override", "Remove override"), command=self._placed_remove_ability).pack(side="left")
        self.placed_abilities_tree.bind("<<TreeviewSelect>>", self._placed_ability_selection_changed)
        self.placed_abilities_tree.bind("<Double-1>", lambda _e: self._placed_edit_ability())

        # ---------- Inventory ----------
        inventory = ttk.Frame(notebook, padding=14)
        notebook.add(inventory, text=self._msg("Inventário", "Inventory"))
        inventory.columnconfigure(2, weight=1)
        ttk.Label(
            inventory,
            text=self._msg(
                "Itens iniciais do herói colocado. Escolha pelos itens nativos do Warcraft ou pelos itens custom do mapa — com nome, ícone, descrição e filtros.",
                "Starting items for this placed hero. Choose from native Warcraft items or map custom items — with name, icon, description and filters."
            ),
            style="Source.TLabel", wraplength=760, justify="left",
        ).grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 10))
        self.placed_inventory_name_labels = []
        self._placed_inventory_icon_labels = []
        for slot in range(6):
            ttk.Label(inventory, text=self._msg(f"Slot {slot+1}", f"Slot {slot+1}")).grid(row=slot+1, column=0, sticky="w", pady=5)
            icon_host=tk.Frame(inventory,width=42,height=42,bg="#11151d",highlightthickness=1,highlightbackground="#343a46")
            icon_host.grid(row=slot+1,column=1,sticky="w",padx=(10,10),pady=4); icon_host.grid_propagate(False)
            icon=tk.Label(icon_host,text="—",fg="#8f9bad",bg="#11151d")
            icon.place(relx=.5,rely=.5,anchor="center")
            self._placed_inventory_icon_labels.append(icon)
            name_var = tk.StringVar(value=self._msg("Vazio", "Empty"))
            self.placed_inventory_name_labels.append(name_var)
            info=ttk.Frame(inventory); info.grid(row=slot+1,column=2,sticky="ew",pady=5)
            ttk.Label(info,textvariable=name_var,font=("Segoe UI",9,"bold")).pack(anchor="w")
            raw_label=ttk.Label(info,textvariable=self.placed_inventory_vars[slot],style="Source.TLabel")
            raw_label.pack(anchor="w")
            ttk.Button(inventory,text=self._msg("Escolher…","Choose…"),command=lambda s=slot:self._placed_choose_inventory_item(s)).grid(row=slot+1,column=3,padx=(10,5),pady=5)
            ttk.Button(inventory,text=self._msg("Limpar","Clear"),command=lambda s=slot:self._placed_clear_inventory_item(s)).grid(row=slot+1,column=4,pady=5)

        # ---------- Items Dropped ----------
        drops = ttk.Frame(notebook, padding=12)
        notebook.add(drops, text=self._msg("Itens Dropados", "Items Dropped"))
        drops.columnconfigure(1, weight=1); drops.rowconfigure(4, weight=1)
        ttk.Label(drops, text=self._msg("Itens Dropados na Morte:", "Items Dropped On Death:")).grid(row=0, column=0, sticky="w")
        self._placed_drop_mode_labels = {
            "none": self._msg("Nenhum", "None"),
            "map": self._msg("Usar tabela de itens do mapa", "Use item table from map"),
            "custom": self._msg("Usar tabela de itens custom", "Use custom item table"),
        }
        self.placed_drop_mode_display_var.set(self._placed_drop_mode_labels["none"])
        self.placed_drop_mode_combo = ttk.Combobox(
            drops, textvariable=self.placed_drop_mode_display_var, state="readonly", width=32,
            values=tuple(self._placed_drop_mode_labels.values()),
        )
        self.placed_drop_mode_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.placed_drop_mode_combo.bind("<<ComboboxSelected>>", lambda _e:self._placed_drop_display_changed())
        ttk.Label(drops, text=self._msg("Escolha uma tabela do mapa ou monte uma tabela custom com os itens abaixo.", "Choose a map item table or build a custom table with the items below."), style="Source.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(3,8))
        ttk.Label(drops, text=self._msg("Índice da tabela do mapa:", "Map item table index:")).grid(row=2, column=0, sticky="w")
        ttk.Spinbox(drops, from_=0, to=99999, textvariable=self.placed_drop_pointer_var, width=10).grid(row=2, column=1, sticky="w", padx=(8,0))
        ttk.Label(drops,text=self._msg("Para tabelas custom, adicione itens pelo catálogo visual — não é necessário digitar rawcode.","For custom tables, add items through the visual catalog — no rawcode typing required."),style="Source.TLabel").grid(row=3,column=0,columnspan=2,sticky="w",pady=(2,4))
        self.placed_drop_tree = ttk.Treeview(drops, columns=("set", "name", "id", "chance"), show="headings", selectmode="browse")
        for col,text,width in (("set", self._msg("Conjunto", "Set"),70),("name",self._msg("Item","Item"),260),("id","ID",75),("chance","%",65)):
            self.placed_drop_tree.heading(col,text=text); self.placed_drop_tree.column(col,width=width,anchor="w")
        self.placed_drop_tree.grid(row=4,column=0,columnspan=2,sticky="nsew",pady=(6,0))
        drop_buttons=ttk.Frame(drops); drop_buttons.grid(row=5,column=0,columnspan=2,sticky="w",pady=(8,0))
        ttk.Button(drop_buttons,text=self._msg("+ Item", "+ Item"),command=self._placed_add_drop).pack(side="left")
        ttk.Button(drop_buttons,text=self._msg("Editar", "Edit"),command=self._placed_edit_drop).pack(side="left",padx=6)
        ttk.Button(drop_buttons,text=self._msg("Remover", "Remove"),command=self._placed_remove_drop).pack(side="left")
        self.placed_drop_tree.bind("<Double-1>",lambda _e:self._placed_edit_drop())

        self.load_placed_hero_form(unit)

    def build_placed_unit_form(self, unit: UnitPlacement) -> None:
        self.build_placed_hero_form(unit)

    def load_placed_hero_form(self, unit: UnitPlacement) -> None:
        self._ensure_placed_state()
        self._placed_creation_number = unit.creation_number
        if self.session is not None:
            resolved_index = self.session.placed_unit_index(unit)
            if resolved_index is not None:
                self._placed_index = resolved_index
        self._placed_snapshot = copy.deepcopy(unit)
        label = player_label(unit.player, self.language)
        if label not in self._placed_owner_lookup:
            self._placed_owner_lookup[label] = unit.player
            try:
                self.placed_owner_combo.configure(values=list(self._placed_owner_lookup))
            except Exception:
                pass
        self.placed_owner_var.set(label)
        self.placed_facing_var.set(f"{math.degrees(unit.angle):g}")
        self.placed_hp_var.set(str(unit.hitpoints))
        self.placed_mana_var.set(str(unit.mana))
        self.placed_acq_var.set(f"{unit.target_acquisition:g}")
        self._placed_is_hero = bool(self._placed_unit_is_hero(unit))
        self.placed_level_var.set(str(max(1, unit.hero_level)))
        try:
            if self._placed_is_hero:
                self.placed_level_spin.configure(state="normal")
                self.placed_level_label.configure(text=self._msg("Nível do herói:", "Hero level:"))
            else:
                self.placed_level_spin.configure(state="disabled")
                self.placed_level_label.configure(text=self._msg("Nível do herói (não aplicável):", "Hero level (not applicable):"))
        except Exception:
            pass
        default_attrs = unit.hero_strength == 0 and unit.hero_agility == 0 and unit.hero_intelligence == 0
        self.placed_use_default_attrs.set(default_attrs)
        self.placed_str_var.set(str(unit.hero_strength))
        self.placed_agi_var.set(str(unit.hero_agility))
        self.placed_int_var.set(str(unit.hero_intelligence))
        self._placed_toggle_attrs()

        for var in self.placed_inventory_vars:
            var.set("")
        for item in unit.inventory:
            if 0 <= item.slot < 6 and item.item_id.strip("\0"):
                self.placed_inventory_vars[item.slot].set(item.item_id)
        for slot in range(6):
            self._placed_update_inventory_name(slot)

        # Combine the hero's object-data pool with placement-specific modifications.
        base_ids = self._placed_default_abilities(unit.unit_id)
        modified = {m.ability_id: m for m in unit.modified_abilities if len(m.ability_id) == 4}
        rows: list[dict] = []
        for aid in base_ids:
            m = modified.pop(aid, None)
            rows.append({
                "id": aid, "level": int(m.level if m else 0), "autocast": bool(m.autocast if m else 0),
                "persist": bool(m is not None), "base": True,
            })
        for m in unit.modified_abilities:
            if m.ability_id in base_ids:
                continue
            rows.append({"id":m.ability_id,"level":int(m.level),"autocast":bool(m.autocast),"persist":True,"base":False})
        self.placed_ability_rows = rows
        self._placed_refresh_abilities_tree()
        if rows:
            try:
                self.placed_abilities_tree.selection_set("a0")
                self._placed_ability_selection_changed()
            except Exception:
                LOGGER.debug("Could not initialize placed ability editor selection", exc_info=True)

        if unit.dropped_item_table >= 0:
            self.placed_drop_mode_var.set("map")
            self.placed_drop_pointer_var.set(str(unit.dropped_item_table))
        elif unit.dropped_item_sets:
            self.placed_drop_mode_var.set("custom")
            self.placed_drop_pointer_var.set("0")
        else:
            self.placed_drop_mode_var.set("none")
            self.placed_drop_pointer_var.set("0")
        self._placed_sync_drop_display()
        self.placed_drop_rows=[]
        for set_index, item_set in enumerate(unit.dropped_item_sets, start=1):
            for item in item_set.items:
                self.placed_drop_rows.append({"set":set_index,"id":item.item_id,"chance":item.chance})
        self._placed_refresh_drop_tree()
        # Baseline only after every variable/list has been populated. This keeps
        # selection changes silent until the user actually edits something.
        self._placed_ui_initial_signature = self._placed_ui_signature()

    def _placed_toggle_attrs(self) -> None:
        state = "disabled" if (not getattr(self, "_placed_is_hero", False) or self.placed_use_default_attrs.get()) else "normal"
        for widget in (getattr(self,"placed_str_spin",None),getattr(self,"placed_agi_spin",None),getattr(self,"placed_int_spin",None)):
            if widget is not None:
                try: widget.configure(state=state)
                except Exception: pass

    def _placed_item_catalog(self):
        cache=getattr(self,"_placed_item_catalog_cache",None)
        if cache is None and self.session is not None:
            try:
                cache=load_item_catalog(self.session)
            except Exception:
                LOGGER.warning("Could not build placed-item catalog",exc_info=True); cache=[]
            self._placed_item_catalog_cache=cache
        return cache or []

    def _placed_item_info(self, raw: str):
        raw=str(raw or "").strip()
        if len(raw)!=4:return None
        return next((x for x in self._placed_item_catalog() if x.rawcode==raw),None)

    def _placed_update_inventory_name(self, slot: int) -> None:
        if not hasattr(self,"placed_inventory_name_labels") or not (0 <= slot < len(self.placed_inventory_name_labels)):
            return
        raw=self.placed_inventory_vars[slot].get().strip()
        info=self._placed_item_info(raw) if raw else None
        if not raw:
            self.placed_inventory_name_labels[slot].set(self._msg("Vazio","Empty"))
        elif info is not None:
            self.placed_inventory_name_labels[slot].set(info.name or raw)
        else:
            self.placed_inventory_name_labels[slot].set(self._msg(f"Item {raw}",f"Item {raw}"))
        self._placed_load_inventory_icon(slot,info)

    def _placed_clear_inventory_item(self, slot: int) -> None:
        if 0<=slot<len(self.placed_inventory_vars):
            self.placed_inventory_vars[slot].set(""); self._placed_update_inventory_name(slot)

    def _placed_choose_inventory_item(self, slot: int) -> None:
        if not (0<=slot<len(self.placed_inventory_vars)):return
        selected=choose_item(self,initial_raw=self.placed_inventory_vars[slot].get().strip(),title=self._msg(f"Escolher item — Slot {slot+1}",f"Choose item — Slot {slot+1}"))
        if selected is None:return
        self.placed_inventory_vars[slot].set(selected.rawcode)
        # Share the dialog-loaded catalog so subsequent slots do not have to scan CASC again.
        self._placed_inventory_name_labels_safe(slot, selected.name or selected.rawcode)
        self._placed_update_inventory_name(slot)

    def _placed_inventory_name_labels_safe(self, slot:int, value:str) -> None:
        try:self.placed_inventory_name_labels[slot].set(value)
        except Exception:pass

    def _placed_load_inventory_icon(self, slot:int, info) -> None:
        labels=getattr(self,"_placed_inventory_icon_labels",[])
        if not (0<=slot<len(labels)):return
        label=labels[slot]
        self._placed_inventory_icon_photos.pop(slot,None)
        if info is None or not getattr(info,"icon",""):
            label.configure(image="",text="—"); label.image=None; return
        raw=info.rawcode; ref=str(info.icon).strip(); session=self.session
        label.configure(image="",text="…")
        def worker():
            data=None
            try:
                with StormArchive(session.map_path,read_only=True) as arc:
                    resolver=getattr(self,"_resolve_archive_path",None)
                    actual=resolver(arc,ref) if callable(resolver) else (ref if arc.has(ref) else None)
                    if actual:data=arc.read(actual)
            except Exception:
                LOGGER.debug("Inventory icon MPQ lookup failed %s",ref,exc_info=True)
            if data is None:
                try:
                    install=detect_warcraft_install()
                    if install:
                        _actual,data=get_shared_casc(install).read_with_path(ref,preference="hd")
                except Exception:
                    LOGGER.debug("Inventory icon CASC lookup failed %s",ref,exc_info=True)
            def show():
                if self.placed_inventory_vars[slot].get().strip()!=raw:return
                if not data:
                    label.configure(image="",text="—"); return
                try:
                    decoder=getattr(self,"_decode_icon_photo",None)
                    photo=decoder(data,38) if callable(decoder) else None
                    if photo is None:
                        label.configure(image="",text="—"); return
                    self._placed_inventory_icon_photos[slot]=photo; label.configure(image=photo,text=""); label.image=photo
                except Exception:
                    label.configure(image="",text="!")
            self.after(0,show)
        threading.Thread(target=worker,daemon=True,name=f"wc3-inventory-icon-{slot}").start()

    def _placed_refresh_abilities_tree(self) -> None:
        tree=getattr(self,"placed_abilities_tree",None)
        if tree is None:return
        selected = self._placed_selected_ability_index()
        tree.delete(*tree.get_children())
        for i,row in enumerate(self.placed_ability_rows):
            active=bool(row.get("persist",False))
            if row.get("base"):
                source=self._msg("tipo + override" if active else "tipo da unidade", "type + override" if active else "unit type")
            else:
                source=self._msg("instância" if active else "instância (inativa)", "instance" if active else "instance (inactive)")
            tree.insert(
                "","end",iid=f"a{i}",
                values=(
                    row["id"], self._placed_ability_display_name(row["id"]),
                    self._msg("Sim","Yes") if active else self._msg("Não","No"),
                    self._msg("Sim","Yes") if row.get("autocast") else self._msg("Não","No"),
                    row.get("level",0), source,
                ),
            )
        if selected is not None and 0 <= selected < len(self.placed_ability_rows):
            try:
                tree.selection_set(f"a{selected}")
            except Exception:
                pass

    def _placed_ability_selection_changed(self, _event=None) -> None:
        if getattr(self, "_placed_ability_editor_guard", False):
            return
        idx=self._placed_selected_ability_index()
        enabled = idx is not None and 0 <= idx < len(self.placed_ability_rows)
        for widget in (
            getattr(self,"placed_ability_active_check",None),
            getattr(self,"placed_ability_autocast_check",None),
            getattr(self,"placed_ability_level_spin",None),
        ):
            if widget is not None:
                try: widget.configure(state="normal" if enabled else "disabled")
                except Exception: pass
        self._placed_ability_editor_guard = True
        try:
            if not enabled:
                self.placed_ability_active_var.set(False)
                self.placed_ability_autocast_var.set(False)
                self.placed_ability_level_edit_var.set("0")
                return
            row=self.placed_ability_rows[idx]
            self.placed_ability_active_var.set(bool(row.get("persist",False)))
            self.placed_ability_autocast_var.set(bool(row.get("autocast",False)))
            self.placed_ability_level_edit_var.set(str(max(0,int(row.get("level",0)))))
        finally:
            self._placed_ability_editor_guard = False

    def _placed_apply_ability_controls(self) -> None:
        if getattr(self, "_placed_ability_editor_guard", False):
            return
        idx=self._placed_selected_ability_index()
        if idx is None or not (0 <= idx < len(self.placed_ability_rows)):
            return
        try:
            level=max(0,min(999,int(str(self.placed_ability_level_edit_var.get()).strip() or "0")))
        except ValueError:
            level=max(0,int(self.placed_ability_rows[idx].get("level",0)))
            self.placed_ability_level_edit_var.set(str(level))
        row=self.placed_ability_rows[idx]
        row["persist"]=bool(self.placed_ability_active_var.get())
        row["autocast"]=bool(self.placed_ability_autocast_var.get())
        row["level"]=level
        self._placed_refresh_abilities_tree()
        try:
            self.placed_abilities_tree.selection_set(f"a{idx}")
        except Exception:
            pass

    def _placed_selected_ability_index(self) -> int | None:
        sel=getattr(self,"placed_abilities_tree",None).selection() if getattr(self,"placed_abilities_tree",None) else ()
        if not sel:return None
        try:return int(sel[0][1:])
        except Exception:return None

    def _placed_prompt_ability(self, existing: dict | None = None) -> dict | None:
        current=(existing or {}).get("id","")
        aid=simpledialog.askstring(self._msg("Habilidade","Ability"),self._msg("Rawcode da habilidade (4 caracteres):","Ability rawcode (4 characters):"),initialvalue=current,parent=self)
        if aid is None:return None
        aid=aid.strip()
        if len(aid)!=4:
            messagebox.showerror(self._msg("Habilidade","Ability"),self._msg("O rawcode precisa ter exatamente 4 caracteres.","The rawcode must be exactly 4 characters."),parent=self); return None
        level=simpledialog.askinteger(self._msg("Nível","Level"),self._msg("Nível da habilidade para este herói (0 = não aprendida):","Ability level for this hero (0 = unlearned):"),initialvalue=int((existing or {}).get("level",0)),minvalue=0,maxvalue=999,parent=self)
        if level is None:return None
        autocast=messagebox.askyesno(self._msg("Autocast","Autocast"),self._msg("Deixar autocast ativo?","Enable autocast?"),parent=self)
        # New rows start active. Existing rows keep their current Active state;
        # the visible Active checkbox can then enable/disable the override
        # without losing the configured level/autocast values.
        persist=True if existing is None else bool((existing or {}).get("persist",False))
        return {"id":aid,"level":level,"autocast":autocast,"persist":persist,"base":bool((existing or {}).get("base",False) and aid==current)}

    def _placed_export_selected_ability_icons(self) -> None:
        tree=getattr(self,"placed_abilities_tree",None)
        if tree is None:
            return
        sel=tree.selection()
        if not sel:
            messagebox.showinfo(self._msg("Exportar ícones","Export icons"),self._msg("Selecione uma habilidade primeiro.","Select an ability first."),parent=self)
            return
        try:
            idx=int(sel[0][1:])
            raw=str(self.placed_ability_rows[idx].get("id","")).strip()
        except Exception:
            return
        if raw:
            self.export_ability_icon_bundle(raw)

    def _placed_add_ability(self) -> None:
        row=self._placed_prompt_ability()
        if row:
            self.placed_ability_rows.append(row); self._placed_refresh_abilities_tree()
            idx=len(self.placed_ability_rows)-1
            try:
                self.placed_abilities_tree.selection_set(f"a{idx}"); self.placed_abilities_tree.see(f"a{idx}"); self._placed_ability_selection_changed()
            except Exception:
                pass

    def _placed_edit_ability(self) -> None:
        idx=self._placed_selected_ability_index()
        if idx is None or not (0<=idx<len(self.placed_ability_rows)):return
        row=self._placed_prompt_ability(self.placed_ability_rows[idx])
        if row:
            self.placed_ability_rows[idx]=row; self._placed_refresh_abilities_tree(); self.placed_abilities_tree.selection_set(f"a{idx}"); self._placed_ability_selection_changed()

    def _placed_remove_ability(self) -> None:
        idx=self._placed_selected_ability_index()
        if idx is None or not (0<=idx<len(self.placed_ability_rows)):return
        row=self.placed_ability_rows[idx]
        if row.get("base"):
            row["persist"]=False; row["level"]=0; row["autocast"]=False
        else:
            self.placed_ability_rows.pop(idx)
        self._placed_refresh_abilities_tree()
        if self.placed_ability_rows:
            next_idx=min(idx,len(self.placed_ability_rows)-1)
            try:
                self.placed_abilities_tree.selection_set(f"a{next_idx}"); self._placed_ability_selection_changed()
            except Exception:
                pass
        else:
            self._placed_ability_selection_changed()

    def _placed_sync_drop_display(self) -> None:
        labels=getattr(self,"_placed_drop_mode_labels",{})
        key=self.placed_drop_mode_var.get() if hasattr(self,"placed_drop_mode_var") else "none"
        if labels and hasattr(self,"placed_drop_mode_display_var"):
            self.placed_drop_mode_display_var.set(labels.get(key,labels.get("none","None")))

    def _placed_drop_display_changed(self) -> None:
        labels=getattr(self,"_placed_drop_mode_labels",{})
        current=self.placed_drop_mode_display_var.get() if hasattr(self,"placed_drop_mode_display_var") else ""
        key=next((k for k,v in labels.items() if v==current),"none")
        self.placed_drop_mode_var.set(key)

    def _placed_refresh_drop_tree(self) -> None:
        tree=getattr(self,"placed_drop_tree",None)
        if tree is None:return
        tree.delete(*tree.get_children())
        for i,row in enumerate(self.placed_drop_rows):
            info=self._placed_item_info(row.get("id",""))
            name=(info.name if info is not None else row.get("id",""))
            tree.insert("","end",iid=f"d{i}",values=(row["set"],name,row["id"],row["chance"]))

    def _placed_selected_drop_index(self) -> int | None:
        sel=getattr(self,"placed_drop_tree",None).selection() if getattr(self,"placed_drop_tree",None) else ()
        if not sel:return None
        try:return int(sel[0][1:])
        except Exception:return None

    def _placed_prompt_drop(self, existing: dict | None = None) -> dict | None:
        set_no=simpledialog.askinteger(self._msg("Conjunto de drop","Drop set"),self._msg("Número do conjunto (1, 2, 3...):","Set number (1, 2, 3...):"),initialvalue=int((existing or {}).get("set",1)),minvalue=1,maxvalue=999,parent=self)
        if set_no is None:return None
        selected=choose_item(self,initial_raw=str((existing or {}).get("id","") or ""),title=self._msg("Escolher item para drop","Choose dropped item"))
        if selected is None:return None
        chance=simpledialog.askinteger(self._msg("Chance","Chance"),self._msg(f"Chance de drop de {selected.name} (%):",f"Drop chance for {selected.name} (%):"),initialvalue=int((existing or {}).get("chance",100)),minvalue=0,maxvalue=100,parent=self)
        if chance is None:return None
        return {"set":set_no,"id":selected.rawcode,"chance":chance}

    def _placed_add_drop(self) -> None:
        row=self._placed_prompt_drop()
        if row:self.placed_drop_rows.append(row); self._placed_refresh_drop_tree(); self.placed_drop_mode_var.set("custom"); self._placed_sync_drop_display()

    def _placed_edit_drop(self) -> None:
        idx=self._placed_selected_drop_index()
        if idx is None or not (0<=idx<len(self.placed_drop_rows)):return
        row=self._placed_prompt_drop(self.placed_drop_rows[idx])
        if row:self.placed_drop_rows[idx]=row; self._placed_refresh_drop_tree()

    def _placed_remove_drop(self) -> None:
        idx=self._placed_selected_drop_index()
        if idx is None or not (0<=idx<len(self.placed_drop_rows)):return
        self.placed_drop_rows.pop(idx); self._placed_refresh_drop_tree()

    def _placed_collect_unit(self, base: UnitPlacement | None = None) -> UnitPlacement:
        if base is None:
            if self._placed_snapshot is None: raise ValueError("nenhuma unidade colocada selecionada")
            base=self._placed_snapshot
        unit=copy.deepcopy(base)
        owner_label=self.placed_owner_var.get()
        if owner_label not in self._placed_owner_lookup:
            raise ValueError(self._msg("Owner inválido","Invalid owner"))
        unit.player=int(self._placed_owner_lookup[owner_label])
        unit.angle=math.radians(float(self.placed_facing_var.get().replace(",",".")))
        unit.hitpoints=int(self.placed_hp_var.get())
        unit.mana=int(self.placed_mana_var.get())
        unit.target_acquisition=float(self.placed_acq_var.get().replace(",","."))
        if getattr(self, "_placed_is_hero", False):
            unit.hero_level=max(1,int(self.placed_level_var.get()))
            if self.placed_use_default_attrs.get():
                unit.hero_strength=unit.hero_agility=unit.hero_intelligence=0
            else:
                unit.hero_strength=max(0,int(self.placed_str_var.get()))
                unit.hero_agility=max(0,int(self.placed_agi_var.get()))
                unit.hero_intelligence=max(0,int(self.placed_int_var.get()))
        inv=[]
        for slot,var in enumerate(self.placed_inventory_vars):
            raw=var.get().strip()
            if not raw:continue
            if len(raw)!=4:raise ValueError(self._msg(f"Slot {slot+1}: rawcode do item precisa ter 4 caracteres",f"Slot {slot+1}: item rawcode must have 4 characters"))
            inv.append(InventoryItem(slot,raw))
        unit.inventory=inv
        mods=[]
        seen=set()
        for row in self.placed_ability_rows:
            if not row.get("persist"):continue
            aid=str(row.get("id","")).strip()
            if len(aid)!=4:raise ValueError(self._msg(f"Habilidade inválida: {aid}",f"Invalid ability: {aid}"))
            # Last visible row wins for duplicate IDs.
            if aid in seen:
                mods=[m for m in mods if m.ability_id!=aid]
            seen.add(aid)
            mods.append(ModifiedAbility(aid,1 if row.get("autocast") else 0,max(0,int(row.get("level",0)))))
        unit.modified_abilities=mods
        mode=self.placed_drop_mode_var.get()
        if mode=="map":
            unit.dropped_item_table=max(0,int(self.placed_drop_pointer_var.get()))
            unit.dropped_item_sets=[]
        elif mode=="custom":
            unit.dropped_item_table=-1
            grouped={}
            for row in self.placed_drop_rows:
                set_no=max(1,int(row["set"])); item=str(row["id"]).strip(); chance=int(row["chance"])
                if len(item)!=4:raise ValueError(self._msg(f"Item de drop inválido: {item}",f"Invalid drop item: {item}"))
                grouped.setdefault(set_no,[]).append(DroppedItem(item,chance))
            unit.dropped_item_sets=[DroppedItemSet(grouped[k]) for k in sorted(grouped)]
        else:
            unit.dropped_item_table=-1
            unit.dropped_item_sets=[]
        return unit

    def _placed_hero_ui_dirty(self) -> bool:
        # Backwards-compatible alias used by ObjectBrowserMixin.
        return self._placed_unit_ui_dirty()

    def force_sync_placed_runtime(self) -> bool:
        """Repair stale compiled JASS for the currently selected placement."""
        if not self.session or self._placed_index is None:
            return False
        # First apply the visible form so the runtime patch always reflects
        # exactly what the user is looking at, including the already-correct
        # DOO value from a map saved by an older Studio build.
        if not self.apply_placed_hero():
            return False
        try:
            self.session.force_placed_runtime_sync(self._placed_creation_number)
            self.status.set(self._msg(
                f"Instância #{self._placed_creation_number}: runtime marcado para sincronização completa no próximo SALVAR.",
                f"Instance #{self._placed_creation_number}: runtime marked for full synchronization on the next SAVE.",
            ))
            return True
        except Exception as exc:
            self._error_box(self._msg("Não foi possível sincronizar runtime", "Could not sync runtime"), "force_sync_placed_runtime", exc)
            return False

    def apply_placed_hero(self) -> bool:
        if not self.session or self._placed_index is None:
            return False
        target=self.session.placed_unit_at(self._placed_index)
        if target is None:
            messagebox.showerror(self._msg("Unidade no mapa","Placed unit"),self._msg("A instância selecionada não existe mais.","The selected instance no longer exists."),parent=self)
            return False
        try:
            updated=self._placed_collect_unit(target)
            # Mutate the existing placement so list/order/creation number stay byte-stable.
            target.__dict__.update(copy.deepcopy(updated.__dict__))
            self.session.mark_placed_units_dirty()
            self._placed_snapshot=copy.deepcopy(target)
            self.load_placed_hero_form(target)
            self.status.set(self._msg(
                f"Instância #{target.creation_number}: aplicada em memória. SALVAR grava o DOO e sincroniza no JASS apenas os campos de runtime que realmente mudaram.",
                f"Instance #{target.creation_number}: applied in memory. SAVE writes the DOO and syncs only runtime fields that actually changed into JASS.",
            ))
            self.refresh_placed_units_tree(select_id=self._placed_index)
            return True
        except Exception as exc:
            self._error_box(self._msg("Não foi possível aplicar na instância","Could not apply to instance"),"apply_placed_hero",exc)
            return False
