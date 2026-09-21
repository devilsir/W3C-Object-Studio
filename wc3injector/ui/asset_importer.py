from __future__ import annotations

"""Integrated Warcraft III asset + placement workflow.

This UI intentionally reuses Object Studio's existing Python codecs/transaction
layer instead of embedding a second Java application.  The workflow is inspired
by Jaccouille's MIT-licensed War3AssetsImporter: select MDX/MDL + textures,
stage them into the MPQ/import table, optionally create custom unit definitions,
and optionally place those new unit types on the map.
"""

from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from ..debuglog import configure_logging
from ..injector import auto_assets, SUPPORTED_ASSETS
from ..objectmod import Modification, TYPE_REAL, TYPE_STRING
from ..unitsdoo import player_choices, player_label

LOGGER, _SESSION_LOG = configure_logging()


class AssetImporterMixin:
    ASSET_IMPORT_ROOT = r"war3mapImported\ObjectStudio"

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _asset_unit_name(self, rawcode: str) -> str:
        try:
            helper = getattr(self, "_placed_unit_display_name", None)
            if callable(helper):
                return helper(rawcode)
        except Exception:
            pass
        if self.session:
            try:
                return self.session.native_name("unit", rawcode) or rawcode
            except Exception:
                pass
        return rawcode

    def _map_unit_choice_rows(self) -> list[tuple[str, str]]:
        """Return display/rawcode rows for units already known by this map.

        Keep the chooser focused on what exists in the map instead of dumping
        thousands of native Warcraft objects. The combobox remains editable, so
        users can still type a native 4-char rawcode directly.
        """
        if not self.session:
            return []
        ids: set[str] = set()
        try:
            ids.update(u.unit_id for u in self.session.placed_units_list())
        except Exception:
            pass
        try:
            for _table, object_id, _base, _record in self.session.list_records("unit", include_native=False):
                if isinstance(object_id, str) and len(object_id) == 4:
                    ids.add(object_id)
        except Exception:
            pass
        rows = []
        for raw in ids:
            name = self._asset_unit_name(raw)
            rows.append((f"{name}  [{raw}]", raw))
        rows.sort(key=lambda x: (x[0].casefold(), x[1]))
        return rows

    def _next_custom_unit_rawcodes(self, count: int, base_rawcode: str) -> list[str]:
        if not self.session:
            return []
        used = set(self.session.native_ids("unit"))
        used.update(
            object_id
            for _table, object_id, _base, _record in self.session.list_records("unit", include_native=False)
            if isinstance(object_id, str) and len(object_id) == 4
        )
        # Uppercase first character is Warcraft's conventional hero family.
        prefix = "Z" if str(base_rawcode)[:1].isupper() else "z"
        digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        def enc3(value: int) -> str:
            return "".join((
                digits[(value // (36 * 36)) % 36],
                digits[(value // 36) % 36],
                digits[value % 36],
            ))

        out: list[str] = []
        for n in range(36 ** 3):
            raw = prefix + enc3(n)
            if raw in used:
                continue
            used.add(raw)
            out.append(raw)
            if len(out) >= count:
                return out
        raise RuntimeError("não há rawcodes custom disponíveis para este lote")

    @staticmethod
    def _clean_asset_unit_name(path: Path) -> str:
        text = path.stem
        text = re.sub(r"[_\-]+", " ", text)
        text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text or path.stem

    def _loose_texture_archive_path(self, path: Path) -> str:
        lower = path.name.casefold()
        if lower.startswith("disbtn"):
            return rf"ReplaceableTextures\CommandButtonsDisabled\{path.name}"
        if lower.startswith("btn"):
            return rf"ReplaceableTextures\CommandButtons\{path.name}"
        return rf"{self.ASSET_IMPORT_ROOT}\Textures\{path.name}"

    def _selected_placement_index(self) -> int | None:
        tree = getattr(self, "placed_tree", None)
        if tree is not None:
            sel = tree.selection()
            if sel:
                try:
                    return int(sel[0].split(":", 1)[1])
                except Exception:
                    pass
        return getattr(self, "_placed_index", None)

    def _selected_placement_creation(self) -> int | None:
        if not self.session:
            return None
        index = self._selected_placement_index()
        unit = self.session.placed_unit_at(index) if index is not None else None
        return int(unit.creation_number) if unit is not None else getattr(self, "_placed_creation_number", None)

    # ------------------------------------------------------------------
    # Add one existing unit type to the map
    # ------------------------------------------------------------------
    def open_add_unit_dialog(self) -> None:
        if not self.session:
            messagebox.showinfo(
                self._msg("Adicionar unidade", "Add unit"),
                self._msg("Abra um mapa primeiro.", "Open a map first."),
                parent=self,
            )
            return
        if self.session.placed_units is None and self.session.placed_units_error:
            messagebox.showerror(
                self._msg("Adicionar unidade", "Add unit"),
                self._msg(
                    "O war3mapUnits.doo existe, mas não pôde ser interpretado com segurança. O Studio não vai sobrescrevê-lo.",
                    "war3mapUnits.doo exists but could not be parsed safely. The Studio will not overwrite it.",
                ) + "\n\n" + self.session.placed_units_error,
                parent=self,
            )
            return

        win = tk.Toplevel(self)
        win.title(self._msg("Adicionar unidade ao mapa", "Add unit to map"))
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        choices = self._map_unit_choice_rows()
        choice_lookup = {label: raw for label, raw in choices}
        default_raw = choices[0][1] if choices else "hfoo"
        default_label = choices[0][0] if choices else default_raw

        selected = None
        creation = self._selected_placement_creation()
        if creation is not None:
            try:
                selected = self.session.placed_unit(creation)
            except Exception:
                selected = None
        if selected is not None:
            for label, raw in choices:
                if raw == selected.unit_id:
                    default_label = label
                    default_raw = raw
                    break

        unit_var = tk.StringVar(value=default_label)
        raw_hint_var = tk.StringVar(value=self._msg(
            "Escolha uma unidade já usada/definida no mapa ou digite um rawcode de 4 caracteres.",
            "Choose a unit already used/defined in the map or type a 4-character rawcode.",
        ))
        owner_rows = player_choices(self.language)
        owner_lookup = {label: value for label, value in owner_rows}
        owner_default = player_label(selected.player if selected is not None else 0, self.language)
        owner_var = tk.StringVar(value=owner_default)
        x_var = tk.StringVar(value=f"{(selected.x + 128.0) if selected else 0.0:.3f}")
        y_var = tk.StringVar(value=f"{selected.y if selected else 0.0:.3f}")
        facing_var = tk.StringVar(value="270")
        scale_var = tk.StringVar(value="1.0")
        level_var = tk.StringVar(value="1")

        def row(label: str, widget, index: int):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", padx=(0, 10), pady=5)
            widget.grid(row=index, column=1, sticky="ew", pady=5)

        unit_combo = ttk.Combobox(body, textvariable=unit_var, values=[x[0] for x in choices], width=42)
        row(self._msg("Unidade / rawcode:", "Unit / rawcode:"), unit_combo, 0)
        ttk.Label(body, textvariable=raw_hint_var, style="Source.TLabel", wraplength=390).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 7)
        )
        owner_combo = ttk.Combobox(body, textvariable=owner_var, values=[x[0] for x in owner_rows], state="readonly", width=38)
        row(self._msg("Dono / Player:", "Owner / Player:"), owner_combo, 2)

        xy = ttk.Frame(body)
        ttk.Label(xy, text="X").pack(side="left")
        ttk.Entry(xy, textvariable=x_var, width=12).pack(side="left", padx=(5, 12))
        ttk.Label(xy, text="Y").pack(side="left")
        ttk.Entry(xy, textvariable=y_var, width=12).pack(side="left", padx=(5, 0))
        row(self._msg("Posição:", "Position:"), xy, 3)
        row(self._msg("Facing (graus):", "Facing (degrees):"), ttk.Entry(body, textvariable=facing_var, width=14), 4)
        row(self._msg("Escala da instância:", "Instance scale:"), ttk.Entry(body, textvariable=scale_var, width=14), 5)
        row(self._msg("Nível do herói:", "Hero level:"), ttk.Entry(body, textvariable=level_var, width=14), 6)

        note = self._msg(
            "A unidade é adicionada ao war3mapUnits.doo e o Studio gera somente o bloco JASS marcado necessário para ela existir no jogo. O resto do war3map.j não é regenerado.",
            "The unit is added to war3mapUnits.doo and the Studio generates only its marked JASS creation block. The rest of war3map.j is not regenerated.",
        )
        ttk.Label(body, text=note, wraplength=480, justify="left").grid(row=7, column=0, columnspan=2, sticky="w", pady=(10, 6))

        actions = ttk.Frame(body)
        actions.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        def resolve_raw() -> str:
            text = unit_var.get().strip()
            raw = choice_lookup.get(text, text)
            m = re.search(r"\[([^\]]{4})\]\s*$", text)
            if m:
                raw = m.group(1)
            raw = raw.strip()
            if len(raw) != 4:
                raise ValueError(self._msg("Rawcode precisa ter exatamente 4 caracteres.", "Rawcode must be exactly 4 characters."))
            raw.encode("latin1", errors="strict")
            return raw

        def create_blank_custom():
            try:
                try:
                    initial_base = resolve_raw()
                except Exception:
                    initial_base = "hfoo"
                base = simpledialog.askstring(
                    self._msg("Nova unidade custom", "New custom unit"),
                    self._msg("Rawcode base (4 caracteres):", "Base rawcode (4 characters):"),
                    initialvalue=initial_base,
                    parent=win,
                )
                if not base:
                    return
                if len(base) != 4:
                    raise ValueError(self._msg("Rawcode base precisa ter 4 caracteres.", "Base rawcode must be 4 characters."))
                suggested = self._next_custom_unit_rawcodes(1, base)[0]
                raw = simpledialog.askstring(
                    self._msg("Nova unidade custom", "New custom unit"),
                    self._msg("Novo rawcode (4 caracteres):", "New rawcode (4 characters):"),
                    initialvalue=suggested, parent=win,
                )
                if not raw:
                    return
                raw = raw.strip()
                if len(raw) != 4:
                    raise ValueError(self._msg("Novo rawcode precisa ter 4 caracteres.", "New rawcode must be 4 characters."))
                raw.encode("latin1", errors="strict")
                used_ids = set(self.session.native_ids("unit"))
                used_ids.update(oid for _t, oid, _b, _r in self.session.list_records("unit", include_native=False))
                if raw in used_ids:
                    raise ValueError(self._msg(
                        f"O rawcode {raw} já existe. Escolha outro.",
                        f"Rawcode {raw} already exists. Choose another one.",
                    ))
                name = simpledialog.askstring(
                    self._msg("Nova unidade custom", "New custom unit"),
                    self._msg("Nome da unidade:", "Unit name:"),
                    initialvalue=self._msg("Nova Unidade", "New Unit"), parent=win,
                )
                if not name:
                    return
                self.session.new_custom("unit", base, raw, [Modification("unam", TYPE_STRING, name, 0, 0)])
                label = f"{name}  [{raw}]"
                choice_lookup[label] = raw
                vals = list(unit_combo.cget("values"))
                vals.append(label)
                unit_combo.configure(values=vals)
                unit_var.set(label)
                self.status.set(self._msg(
                    f"{raw} criado em memória a partir de {base}. Agora clique ADICIONAR para colocá-lo no mapa.",
                    f"{raw} created in memory from {base}. Now click ADD to place it on the map.",
                ))
            except Exception as exc:
                messagebox.showerror(self._msg("Nova unidade custom", "New custom unit"), str(exc), parent=win)

        def add_now():
            try:
                raw = resolve_raw()
                owner = owner_lookup[owner_var.get()]
                unit = self.session.add_placed_unit(
                    raw,
                    x=float(x_var.get().replace(",", ".")),
                    y=float(y_var.get().replace(",", ".")),
                    angle_degrees=float(facing_var.get().replace(",", ".")),
                    player=owner,
                    scale=float(scale_var.get().replace(",", ".")),
                    hero_level=max(1, int(level_var.get())),
                )
                if hasattr(self, "refresh_placed_units_tree"):
                    self.refresh_placed_units_tree(select_id=self.session.placed_unit_index(unit))
                self.status.set(self._msg(
                    f"{raw} adicionado como instância #{unit.creation_number}. SALVAR grava DOO + criação JASS mínima.",
                    f"{raw} added as instance #{unit.creation_number}. SAVE writes DOO + minimal JASS creation.",
                ))
                win.destroy()
            except Exception as exc:
                messagebox.showerror(self._msg("Adicionar unidade", "Add unit"), str(exc), parent=win)

        def place_on_map():
            try:
                raw = resolve_raw()
                owner = owner_lookup[owner_var.get()]
                facing = float(facing_var.get().replace(",", "."))
                scale = float(scale_var.get().replace(",", "."))
                level = max(1, int(level_var.get()))
                if scale <= 0:
                    raise ValueError(self._msg("Escala precisa ser maior que zero.", "Scale must be greater than zero."))
                win.destroy()
                self._begin_single_unit_map_placement(
                    raw, owner=owner, facing=facing, scale=scale, hero_level=level
                )
            except Exception as exc:
                messagebox.showerror(self._msg("Posicionar no mapa", "Place on map"), str(exc), parent=win)

        ttk.Button(
            actions,
            text=self._msg("POSICIONAR NO MAPA", "PLACE ON MAP"),
            command=place_on_map,
        ).pack(side="right", padx=(0, 7))

        ttk.Button(
            actions,
            text=self._msg("IMPORTAR MODELO…", "IMPORT MODEL…"),
            command=lambda: (win.destroy(), self.open_asset_importer(default_mode="create_place")),
        ).pack(side="left")
        ttk.Button(
            actions, text=self._msg("NOVA CUSTOM…", "NEW CUSTOM…"), command=create_blank_custom
        ).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text=self._msg("Cancelar", "Cancel"), command=win.destroy).pack(side="right")
        ttk.Button(actions, text=self._msg("ADICIONAR", "ADD"), command=add_now).pack(side="right", padx=(0, 7))

        win.bind("<Return>", lambda _e: add_now())
        win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        win.geometry(f"+{self.winfo_rootx()+110}+{self.winfo_rooty()+90}")

    def _begin_single_unit_map_placement(
        self, raw: str, *, owner: int, facing: float, scale: float, hero_level: int
    ) -> None:
        """Switch to the interactive map and place one unit with a click."""
        view = getattr(self, "placed_map_view", None)
        if view is None or not self.session:
            messagebox.showerror(
                self._msg("Posicionar no mapa", "Place on map"),
                self._msg("A visualização do mapa ainda não está disponível.", "The map view is not available yet."),
                parent=self,
            )
            return
        try:
            if hasattr(self, "placed_center_tabs"):
                self.placed_center_tabs.select(self.placed_map_page)
            if hasattr(self, "main_tabs") and hasattr(self, "placed_units_page"):
                self.main_tabs.select(self.placed_units_page)
        except Exception:
            pass
        name = self._asset_unit_name(raw)
        label = f"{name} [{raw}]"

        def commit(x: float, y: float):
            try:
                unit = self.session.add_placed_unit(
                    raw, x=x, y=y, angle_degrees=facing, player=owner,
                    scale=scale, hero_level=hero_level,
                )
                self.refresh_placed_units_tree(select_id=self.session.placed_unit_index(unit))
                try:
                    self._select_placement_from_map(self.session.placed_unit_index(unit))
                except Exception:
                    pass
                self.status.set(self._msg(
                    f"{raw} adicionado em X {x:.1f}, Y {y:.1f} como instância #{unit.creation_number}. Clique SALVAR para gravar.",
                    f"{raw} added at X {x:.1f}, Y {y:.1f} as instance #{unit.creation_number}. Click SAVE to write it.",
                ))
            except Exception as exc:
                LOGGER.exception("Visual unit placement failed")
                messagebox.showerror(self._msg("Posicionar no mapa", "Place on map"), str(exc), parent=self)

        view.begin_placement(label, commit)
        self.status.set(self._msg(
            f"POSICIONANDO {label}: clique no ponto desejado do mapa. Botão direito cancela.",
            f"PLACING {label}: click the desired point on the map. Right-click cancels.",
        ))

    def _begin_batch_map_placement(
        self, created: list[tuple[str, Path]], *, owner: int, facing: float, hero_level: int
    ) -> None:
        """Place imported unit types visually, one map click per created model."""
        if not created or not self.session:
            return
        view = getattr(self, "placed_map_view", None)
        if view is None:
            return
        queue = list(created)
        total = len(queue)
        placed_count = 0
        try:
            if hasattr(self, "main_tabs") and hasattr(self, "placed_units_page"):
                self.main_tabs.select(self.placed_units_page)
            if hasattr(self, "placed_center_tabs"):
                self.placed_center_tabs.select(self.placed_map_page)
        except Exception:
            pass

        def start_next():
            nonlocal placed_count
            if not queue:
                self.status.set(self._msg(
                    f"Posicionamento visual concluído: {placed_count}/{total} unidade(s). Clique SALVAR para gravar no mapa.",
                    f"Visual placement complete: {placed_count}/{total} unit(s). Click SAVE to write the map.",
                ))
                return
            raw, model = queue[0]
            label = f"{self._asset_unit_name(raw)} [{raw}] ({placed_count + 1}/{total})"

            def commit(x: float, y: float):
                nonlocal placed_count
                try:
                    unit = self.session.add_placed_unit(
                        raw, x=x, y=y, angle_degrees=facing, player=owner,
                        scale=1.0, hero_level=hero_level,
                    )
                    queue.pop(0)
                    placed_count += 1
                    self.refresh_placed_units_tree(select_id=self.session.placed_unit_index(unit))
                    self.status.set(self._msg(
                        f"{Path(model).name}: colocado em X {x:.1f}, Y {y:.1f}. Restam {len(queue)}.",
                        f"{Path(model).name}: placed at X {x:.1f}, Y {y:.1f}. {len(queue)} remaining.",
                    ))
                    self.after(80, start_next)
                except Exception as exc:
                    LOGGER.exception("Batch visual placement failed")
                    messagebox.showerror(self._msg("Posicionamento visual", "Visual placement"), str(exc), parent=self)

            view.begin_placement(label, commit)

        start_next()

    def remove_selected_studio_placement(self) -> None:
        """Remove the selected placement, including original World Editor units.

        Original placements are removed from war3mapUnits.doo and receive a
        minimal Studio-owned runtime RemoveUnit block.  We intentionally do not
        rewrite or delete the World Editor's original creation statement.
        """
        if not self.session:
            return
        placement_index = self._selected_placement_index()
        if placement_index is None:
            messagebox.showinfo(
                self._msg("Remover unidade", "Remove unit"),
                self._msg("Selecione uma unidade colocada primeiro.", "Select a placed unit first."),
                parent=self,
            )
            return
        unit = self.session.placed_unit_at(placement_index)
        if unit is None:
            return
        creation = int(unit.creation_number)
        name = self._asset_unit_name(unit.unit_id)
        was_studio = creation in self.session.studio_added_placements
        detail = self._msg(
            "Esta unidade foi adicionada pelo Studio; o bloco de criação dela será removido.",
            "This unit was added by the Studio; its creation block will be removed.",
        ) if was_studio else self._msg(
            "Esta é uma unidade original do mapa. O DOO será removido e o Studio adicionará somente um RemoveUnit marcado após CreateAllUnits, sem reescrever o JASS original.",
            "This is an original map unit. Its DOO record will be removed and the Studio will add only a marked RemoveUnit after CreateAllUnits, without rewriting original JASS.",
        )
        if not messagebox.askyesno(
            self._msg("Remover unidade", "Remove unit"),
            self._msg(
                f"Remover {name} (instância #{creation}) do mapa?",
                f"Remove {name} (instance #{creation}) from the map?",
            ) + "\n\n" + detail + "\n\n" + self._msg(
                "A alteração só é gravada quando você clicar SALVAR.",
                "The change is written only when you click SAVE.",
            ),
            parent=self,
        ):
            return
        try:
            self.session.remove_placed_unit_at(placement_index)
            self._placed_creation_number = None
            self._placed_index = None
            self._placed_snapshot = None
            if hasattr(self, "refresh_placed_units_tree"):
                self.refresh_placed_units_tree()
            try:
                if hasattr(self, "placed_map_view"):
                    self.placed_map_view.set_selected(None)
            except Exception:
                pass
            self.status.set(self._msg(
                f"Instância #{creation} removida em memória. SALVAR atualiza o mapa e o runtime.",
                f"Instance #{creation} removed in memory. SAVE updates the map and runtime.",
            ))
        except Exception as exc:
            LOGGER.exception("Placement removal failed")
            messagebox.showerror(self._msg("Remover unidade", "Remove unit"), str(exc), parent=self)

    # ------------------------------------------------------------------
    # Batch asset importer + optional custom unit generation/placement
    # ------------------------------------------------------------------
    def open_asset_importer(self, default_mode: str = "assets") -> None:
        if not self.session:
            messagebox.showinfo(
                self._msg("Importador de assets", "Asset importer"),
                self._msg("Abra um mapa primeiro.", "Open a map first."),
                parent=self,
            )
            return

        win = tk.Toplevel(self)
        win.title(self._msg("Importador de Assets + Unidades", "Assets + Units Importer"))
        win.transient(self)
        win.grab_set()
        win.geometry("860x650")
        win.minsize(740, 560)

        root = ttk.Frame(win, padding=12)
        root.pack(fill="both", expand=True)
        root.rowconfigure(1, weight=1)
        root.columnconfigure(0, weight=1)

        head = ttk.Frame(root)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(head, text=self._msg("ASSETS", "ASSETS"), font=("Segoe UI", 13, "bold")).pack(side="left")
        count_var = tk.StringVar(value=self._msg("0 arquivos", "0 files"))
        ttk.Label(head, textvariable=count_var, style="Source.TLabel").pack(side="left", padx=10)

        files_box = ttk.Frame(root)
        files_box.grid(row=1, column=0, sticky="nsew")
        files_box.rowconfigure(0, weight=1)
        files_box.columnconfigure(0, weight=1)
        asset_tree = ttk.Treeview(files_box, columns=("type", "path"), show="headings", selectmode="extended")
        asset_tree.heading("type", text=self._msg("Tipo", "Type"))
        asset_tree.heading("path", text=self._msg("Arquivo", "File"))
        asset_tree.column("type", width=90, anchor="center")
        asset_tree.column("path", width=650)
        vs = ttk.Scrollbar(files_box, orient="vertical", command=asset_tree.yview)
        asset_tree.configure(yscrollcommand=vs.set)
        asset_tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")

        selected: dict[str, Path] = {}

        def redraw():
            asset_tree.delete(*asset_tree.get_children())
            for key, path in sorted(selected.items(), key=lambda kv: kv[1].name.casefold()):
                ext = path.suffix.lower().lstrip(".").upper()
                asset_tree.insert("", "end", iid=key, values=(ext, str(path)))
            models = sum(p.suffix.lower() in {".mdx", ".mdl"} for p in selected.values())
            textures = len(selected) - models
            count_var.set(self._msg(
                f"{len(selected)} arquivos · {models} modelos · {textures} texturas",
                f"{len(selected)} files · {models} models · {textures} textures",
            ))

        def add_paths(paths):
            for raw in paths:
                p = Path(raw).resolve()
                if p.is_file() and p.suffix.lower() in SUPPORTED_ASSETS:
                    selected[str(p).casefold()] = p
            redraw()

        def pick_files():
            add_paths(filedialog.askopenfilenames(
                parent=win,
                title=self._msg("Modelos e texturas", "Models and textures"),
                filetypes=[
                    ("Warcraft assets", "*.mdx *.mdl *.blp *.tga *.dds"),
                    (self._msg("Todos", "All files"), "*.*"),
                ],
            ))

        def pick_folder():
            folder = filedialog.askdirectory(parent=win, title=self._msg("Pasta de assets", "Assets folder"))
            if not folder:
                return
            root_path = Path(folder)
            add_paths(p for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_ASSETS)

        def remove_selected():
            for iid in asset_tree.selection():
                selected.pop(iid, None)
            redraw()

        asset_actions = ttk.Frame(root)
        asset_actions.grid(row=2, column=0, sticky="ew", pady=(7, 10))
        ttk.Button(asset_actions, text=self._msg("+ ARQUIVOS…", "+ FILES…"), command=pick_files).pack(side="left")
        ttk.Button(asset_actions, text=self._msg("+ PASTA…", "+ FOLDER…"), command=pick_folder).pack(side="left", padx=6)
        ttk.Button(asset_actions, text=self._msg("REMOVER", "REMOVE"), command=remove_selected).pack(side="left")

        config = ttk.LabelFrame(root, text=self._msg("O que fazer", "What to do"), padding=10)
        config.grid(row=3, column=0, sticky="ew")
        for col in (1, 3, 5):
            config.columnconfigure(col, weight=1)

        mode_var = tk.StringVar(value=default_mode if default_mode in {"assets", "create", "create_place"} else "assets")
        ttk.Radiobutton(config, text=self._msg("Só importar assets", "Import assets only"), variable=mode_var, value="assets").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(config, text=self._msg("Importar + criar unidades custom", "Import + create custom units"), variable=mode_var, value="create").grid(row=0, column=2, columnspan=2, sticky="w", padx=(12, 0))
        ttk.Radiobutton(config, text=self._msg("Importar + criar + adicionar ao mapa", "Import + create + add to map"), variable=mode_var, value="create_place").grid(row=0, column=4, columnspan=2, sticky="w", padx=(12, 0))

        placement_mode_var = tk.StringVar(value="visual" if default_mode == "create_place" else "grid")
        placement_mode = ttk.Frame(config)
        placement_mode.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        ttk.Label(placement_mode, text=self._msg("Posicionamento:", "Placement:")).pack(side="left")
        ttk.Radiobutton(
            placement_mode, text=self._msg("Visual — clicar no mapa", "Visual — click the map"),
            variable=placement_mode_var, value="visual",
        ).pack(side="left", padx=(10, 0))
        ttk.Radiobutton(
            placement_mode, text=self._msg("Grade automática por X/Y", "Automatic X/Y grid"),
            variable=placement_mode_var, value="grid",
        ).pack(side="left", padx=(12, 0))

        base_var = tk.StringVar(value="hfoo")
        scale_var = tk.StringVar(value="1.0")
        owner_rows = player_choices(self.language)
        owner_lookup = {label: value for label, value in owner_rows}
        owner_var = tk.StringVar(value=player_label(0, self.language))
        start_x_var = tk.StringVar(value="0")
        start_y_var = tk.StringVar(value="0")
        facing_var = tk.StringVar(value="270")
        spacing_x_var = tk.StringVar(value="160")
        spacing_y_var = tk.StringVar(value="160")
        columns_var = tk.StringVar(value="8")
        hero_level_var = tk.StringVar(value="1")

        def add_field(label: str, var, row: int, col: int, width=12, combo_values=None):
            ttk.Label(config, text=label).grid(row=row, column=col, sticky="w", padx=(0, 5), pady=(8, 0))
            if combo_values is None:
                w = ttk.Entry(config, textvariable=var, width=width)
            else:
                w = ttk.Combobox(config, textvariable=var, values=combo_values, state="readonly", width=width)
            w.grid(row=row, column=col + 1, sticky="ew", padx=(0, 12), pady=(8, 0))
            return w

        add_field(self._msg("Unidade base:", "Base unit:"), base_var, 2, 0)
        add_field(self._msg("Art Scale:", "Art Scale:"), scale_var, 2, 2)
        add_field(self._msg("Dono:", "Owner:"), owner_var, 2, 4, width=24, combo_values=[x[0] for x in owner_rows])
        add_field("X:", start_x_var, 3, 0)
        add_field("Y:", start_y_var, 3, 2)
        add_field(self._msg("Facing:", "Facing:"), facing_var, 3, 4)
        add_field(self._msg("Espaço X:", "Spacing X:"), spacing_x_var, 4, 0)
        add_field(self._msg("Espaço Y:", "Spacing Y:"), spacing_y_var, 4, 2)
        add_field(self._msg("Colunas:", "Columns:"), columns_var, 4, 4)
        add_field(self._msg("Nível herói:", "Hero level:"), hero_level_var, 5, 0)

        credit = self._msg(
            "Fluxo inspirado no War3AssetsImporter de Jaccouille (MIT). Esta integração usa os codecs e o salvamento transacional do próprio Object Studio.",
            "Workflow inspired by Jaccouille's War3AssetsImporter (MIT). This integration uses Object Studio's own codecs and transactional save layer.",
        )
        ttk.Label(config, text=credit, style="Source.TLabel", wraplength=790, justify="left").grid(
            row=6, column=0, columnspan=6, sticky="w", pady=(10, 0)
        )

        foot = ttk.Frame(root)
        foot.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        status_var = tk.StringVar(value=self._msg("Selecione arquivos ou uma pasta.", "Select files or a folder."))
        ttk.Label(foot, textvariable=status_var, style="Source.TLabel", wraplength=570).pack(side="left", fill="x", expand=True)
        ttk.Button(foot, text=self._msg("Cancelar", "Cancel"), command=win.destroy).pack(side="right")

        def run_import():
            if not selected:
                messagebox.showinfo(self._msg("Importador", "Importer"), self._msg("Nenhum asset selecionado.", "No assets selected."), parent=win)
                return
            paths = list(selected.values())
            models = [p for p in paths if p.suffix.lower() in {".mdx", ".mdl"}]
            textures = [p for p in paths if p.suffix.lower() in {".blp", ".tga", ".dds"}]
            mode = mode_var.get()
            if mode != "assets" and not models:
                messagebox.showerror(self._msg("Importador", "Importer"), self._msg("Para criar unidades, selecione pelo menos um MDX/MDL.", "To create units, select at least one MDX/MDL."), parent=win)
                return
            try:
                base = base_var.get().strip()
                scale = float(scale_var.get().replace(",", "."))
                if scale <= 0:
                    raise ValueError(self._msg("Escala precisa ser maior que zero.", "Scale must be greater than zero."))
                if mode != "assets":
                    if len(base) != 4:
                        raise ValueError(self._msg("Rawcode da unidade base precisa ter 4 caracteres.", "Base unit rawcode must be 4 characters."))
                    base.encode("latin1", errors="strict")

                warnings: list[str] = []
                model_paths: dict[Path, str] = {}
                source_archive_paths: dict[Path, str] = {}
                staged_count = 0
                if models:
                    assets, model_paths, warnings = auto_assets(models, textures, self.ASSET_IMPORT_ROOT)
                    for asset in assets:
                        self.session.stage_asset(asset.source, asset.archive_path)
                        source_archive_paths[asset.source.resolve()] = asset.archive_path
                        staged_count += 1
                else:
                    for tex in textures:
                        self.session.stage_asset(tex, self._loose_texture_archive_path(tex))
                        staged_count += 1

                created: list[tuple[str, Path]] = []
                if mode != "assets":
                    rawcodes = self._next_custom_unit_rawcodes(len(models), base)
                    for raw, model in zip(rawcodes, models):
                        name = self._clean_asset_unit_name(model)
                        archive_model = model_paths[model]
                        mods = [
                            Modification("unam", TYPE_STRING, name, 0, 0),
                            Modification("umdl", TYPE_STRING, archive_model, 0, 0),
                            Modification("usca", TYPE_REAL, scale, 0, 0),
                        ]
                        # Same convenience as War3AssetsImporter: BTN<ModelName>
                        # is assigned as the unit icon when it is part of the batch.
                        model_key = model.stem.casefold()
                        icon = next((
                            tex for tex in textures
                            if tex.stem.casefold().startswith("btn")
                            and tex.stem[3:].casefold() == model_key
                        ), None)
                        if icon is not None:
                            icon_path = source_archive_paths.get(icon.resolve(), self._loose_texture_archive_path(icon))
                            mods.append(Modification("uico", TYPE_STRING, icon_path, 0, 0))
                        self.session.new_custom("unit", base, raw, mods)
                        created.append((raw, model))
                        # Keep local source available to the existing model viewer before save.
                        try:
                            sibling_textures = [p for p in textures if p.parent == model.parent]
                            self.local_models[archive_model.casefold()] = (model, sibling_textures)
                        except Exception:
                            pass

                placed = []
                visual_queue = False
                visual_owner = 0
                visual_facing = 270.0
                visual_level = 1
                if mode == "create_place":
                    if self.session.placed_units is None and self.session.placed_units_error:
                        raise ValueError(self.session.placed_units_error)
                    owner = owner_lookup[owner_var.get()]
                    facing = float(facing_var.get().replace(",", "."))
                    level = max(1, int(hero_level_var.get()))
                    if placement_mode_var.get() == "visual":
                        # Create/import everything first, then hand placement over
                        # to the main MAP tab one unit at a time. This keeps the
                        # MPQ/object operation transactional while making X/Y a
                        # literal click on the terrain instead of a guess.
                        visual_queue = True
                        visual_owner = owner
                        visual_facing = facing
                        visual_level = level
                    else:
                        sx = float(start_x_var.get().replace(",", "."))
                        sy = float(start_y_var.get().replace(",", "."))
                        dx = float(spacing_x_var.get().replace(",", "."))
                        dy = float(spacing_y_var.get().replace(",", "."))
                        cols = max(1, int(columns_var.get()))
                        for index, (raw, _model) in enumerate(created):
                            col = index % cols
                            row_idx = index // cols
                            unit = self.session.add_placed_unit(
                                raw,
                                x=sx + col * dx,
                                y=sy - row_idx * dy,
                                angle_degrees=facing,
                                player=owner,
                                # Art Scale already lives on the generated unit type.
                                # Keep placement scale at 1.0 to avoid multiplying it.
                                scale=1.0,
                                hero_level=level,
                            )
                            placed.append(unit)

                if hasattr(self, "refresh_tree") and created:
                    try:
                        self.kind_var.set("unit")
                        self.refresh_tree(created[0][0])
                    except Exception:
                        LOGGER.debug("Could not focus first imported custom unit", exc_info=True)
                if hasattr(self, "refresh_placed_units_tree") and placed:
                    self.refresh_placed_units_tree(select_id=self.session.placed_unit_index(placed[0]))

                if visual_queue:
                    summary = self._msg(
                        f"{staged_count} assets preparados · {len(created)} unidades custom criadas. Agora clique no mapa para posicionar cada uma ({len(created)} clique(s)).",
                        f"{staged_count} assets staged · {len(created)} custom units created. Now click the map to place each one ({len(created)} click(s)).",
                    )
                else:
                    summary = self._msg(
                        f"{staged_count} assets preparados · {len(created)} unidades custom criadas · {len(placed)} placements adicionados. Clique SALVAR para gravar tudo no mapa.",
                        f"{staged_count} assets staged · {len(created)} custom units created · {len(placed)} placements added. Click SAVE to write everything to the map.",
                    )
                self.status.set(summary)
                if warnings:
                    messagebox.showwarning(
                        self._msg("Importado com avisos", "Imported with warnings"),
                        summary + "\n\n" + "\n".join(warnings[:20]),
                        parent=win,
                    )
                elif not visual_queue:
                    messagebox.showinfo(self._msg("Importação preparada", "Import staged"), summary, parent=win)
                win.destroy()
                if visual_queue:
                    self.after(80, lambda: self._begin_batch_map_placement(
                        created, owner=visual_owner, facing=visual_facing, hero_level=visual_level
                    ))
            except Exception as exc:
                LOGGER.exception("Integrated asset import failed")
                messagebox.showerror(self._msg("Falha na importação", "Import failed"), str(exc), parent=win)

        ttk.Button(foot, text=self._msg("IMPORTAR / PREPARAR", "IMPORT / STAGE"), command=run_import).pack(side="right", padx=(0, 7))
        win.bind("<Escape>", lambda _e: win.destroy())

