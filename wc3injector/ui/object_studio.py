from __future__ import annotations

from pathlib import Path
import tkinter as tk

from ..debuglog import configure_logging
from ..editor import EditSession
from ..objectmod import Modification
from ..paths import ASSETS_DIR
from .localization import LocalizationMixin
from .webview_focus import WebViewFocusMixin
from .workspace import WorkspaceMixin
from .embedded_viewer import EmbeddedViewerMixin
from .archive_asset_resolver import ArchiveAssetResolverMixin
from .icon_preview import IconPreviewMixin
from .ability_list import AbilityListMixin
from .spell_lab import SpellLabMixin
from .model_preview import ModelPreviewMixin
from .object_browser import ObjectBrowserMixin
from .object_form import ObjectFormMixin
from .object_commands import ObjectCommandsMixin
from .placed_hero import PlacedHeroMixin
from .asset_export import AssetExportMixin
from .asset_importer import AssetImporterMixin

LOGGER, _SESSION_LOG = configure_logging()


class ObjectStudio(
    LocalizationMixin,
    WebViewFocusMixin,
    WorkspaceMixin,
    EmbeddedViewerMixin,
    ArchiveAssetResolverMixin,
    IconPreviewMixin,
    AbilityListMixin,
    SpellLabMixin,
    ModelPreviewMixin,
    ObjectBrowserMixin,
    ObjectFormMixin,
    ObjectCommandsMixin,
    AssetImporterMixin,
    PlacedHeroMixin,
    AssetExportMixin,
    tk.Tk,
):
    def _apply_app_icon(self) -> None:
            """Use the WC3 Object Studio artwork instead of Tk's default icon."""
            png_path = ASSETS_DIR / "wc3_object_studio_icon.png"
            ico_path = ASSETS_DIR / "wc3_object_studio_icon.ico"
            self._app_icon_photo = None
            # On Windows .ico gives the most reliable title/taskbar icon, while
            # iconphoto also covers Tk/Alt-Tab and non-Windows environments.
            try:
                if ico_path.exists():
                    self.iconbitmap(default=str(ico_path))
            except Exception:
                LOGGER.debug("Could not apply ICO app icon: %s", ico_path, exc_info=True)
            try:
                if png_path.exists():
                    self._app_icon_photo = tk.PhotoImage(file=str(png_path))
                    self.iconphoto(True, self._app_icon_photo)
            except Exception:
                LOGGER.debug("Could not apply PNG app icon: %s", png_path, exc_info=True)

    def __init__(self):
            super().__init__()
            self.title("WC3 Reforged Object Studio v1.0 — Object Editor completo • MPQ + CASC")
            self._apply_app_icon()
            LOGGER.info("Tk UI initialized; Tk=%s Tcl=%s", tk.TkVersion, tk.TclVersion)
            self.report_callback_exception = self._tk_callback_exception
            self.geometry("1440x900"); self.minsize(1160,740)
            self.session: EditSession | None = None
            self.current_kind="unit"; self.current_table=None; self.current_id=None; self.current_base=None
            self.working_mods: list[Modification] = []
            self._selection_snapshot_mods: list[Modification] = []
            self._selection_snapshot_assets = []
            self._selected_tree_iid: str | None = None
            self._selection_change_guard = False
            self.form_vars: dict[str,tk.StringVar] = {}
            self.form_defs=[]
            self.form_state: dict[str, dict] = {}
            self.form_source_vars: dict[str, tk.StringVar] = {}
            self._form_signature = None
            self.embedded_view = None
            self.web_session = None
            self.webview_retry_count = 0
            self.webview_data_dir = self._prepare_webview2_profile()
            self.preview_status_var = tk.StringVar(value="Selecione uma unidade/doodad com modelo importado")
            self.preview_serial = 0
            self.preview_last_files: list[Path] = []
            self.preview_last_model: Path | None = None
            self.preview_last_scene: dict | None = None
            self.preview_last_url: str | None = None
            self.preview_cache: dict[str, tuple[list[Path], Path, str, list[str]]] = {}
            self.icon_serial = 0
            self.icon_photo = None
            self.icon_archive_path: str | None = None
            self.icon_variant_photos: dict[str, object] = {}
            self.icon_variant_labels: dict[str, tk.Label] = {}
            self.icon_variant_path_vars: dict[str, tk.StringVar] = {}
            self.icon_status_var = tk.StringVar(value="Ícones: selecione um objeto")
            self.visual_source_var = tk.StringVar(value="")
            self.ability_icon_serial = 0
            self.ability_icon_photos: dict[str, object] = {}
            self.ability_rows: list[dict] = []
            self.ability_list_host = None
            self.ability_test_candidates: list[dict] = []
            self.ability_test_lookup: dict[str, dict] = {}
            self.ability_test_hero_var = tk.StringVar(value="")
            self.ability_test_info_var = tk.StringVar(value="")
            self.ability_scene_cache: dict[tuple, tuple[list[Path], Path, dict, str, list[str]]] = {}
            # Preview-only asset mode. Reforged/HD stays the default, while
            # Classic/SD renders the pre-Reforged Warcraft III models from CASC.
            self.model_asset_mode_var = tk.StringVar(value="Reforged HD")
            self.current_level=1
            self.local_models: dict[str,tuple[Path,list[Path]]] = {}
            self.temp_dirs=[]
            self._focus_bridge_active = False
            self.language = "pt"
            self.tree_sort_column = "name"
            self.tree_sort_reverse = False
            self._load_flag_images()
            self._build()
            self._install_keyboard_focus_bridge()
