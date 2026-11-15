from __future__ import annotations

import copy
import os
import re
import subprocess
import tempfile
import time
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


class EmbeddedViewerMixin:
    def _show_preview_loading(self, text: str | None = None):
            if text is None:
                text = self._msg("Carregando preview 3D…", "Loading 3D preview…")
            try:
                self.preview_fallback.configure(text=text)
                self.preview_fallback.place(relx=.5,rely=.5,anchor="center")
                self.preview_fallback.lift()
                self.preview_host.update_idletasks()
            except Exception:
                LOGGER.debug("Could not show preview loading overlay", exc_info=True)

    def _hide_preview_loading(self):
            try:
                self.preview_fallback.place_forget()
            except Exception:
                LOGGER.debug("Could not hide preview loading overlay", exc_info=True)

    def _init_embedded_viewer(self, force: bool = False):
            """Create the embedded WebView2 in the right visual pane.

            v2.9 keeps the v2.8 writable WebView2 profile and deliberately uses an explicit ``WebSession(data_directory=...)``
            under LOCALAPPDATA.  This is different from the previous ephemeral
            profile: the log from the affected Windows machine proves that the MDX
            and its textures are extracted correctly and the remaining failure is
            WebView2 E_ACCESSDENIED during native creation.
            """
            if self.embedded_view is not None and not force:
                return
            try:
                from tkwry import WebSession, WebView
                self.update_idletasks()
                # Creating a native child before Tk has mapped the host can fail on
                # some Windows setups.  Retry after layout instead of surfacing a
                # fake permanent error.
                if self.preview_host.winfo_width() <= 1 or self.preview_host.winfo_height() <= 1:
                    self.after(250, lambda: self._init_embedded_viewer(force=force))
                    return
                if force and self.embedded_view is not None:
                    try:
                        self.embedded_view.destroy()
                    except Exception:
                        LOGGER.debug("Could not destroy previous embedded WebView", exc_info=True)
                    self.embedded_view = None
                if self.web_session is None or force:
                    # Use a real, explicitly writable profile. tkwry documents
                    # data_directory as the persistent-profile API; WebView2 also
                    # honors WEBVIEW2_USER_DATA_FOLDER set during app startup.
                    session_dir = self.webview_data_dir
                    if force and self.webview_retry_count:
                        session_dir = session_dir.parent / f"session-{os.getpid()}-retry{self.webview_retry_count}"
                        session_dir.mkdir(parents=True, exist_ok=True)
                        os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(session_dir)
                    self.web_session = WebSession(data_directory=str(session_dir))
                    LOGGER.info("Created tkwry WebSession data_directory=%s", session_dir)
                url=prepare_viewer_url(compact=True)
                self._show_preview_loading(self._msg("Inicializando visualizador 3D…", "Initializing 3D viewer…"))
                self.embedded_view=WebView(
                    self.preview_host,
                    url=url,
                    session=self.web_session,
                    width=max(360,self.preview_host.winfo_width()),
                    height=max(360,self.preview_host.winfo_height()),
                    default_context_menus=False,
                    hotkeys_zoom=False,
                    focused=False,
                )
                self.embedded_view.pack(fill="both",expand=True)
                self.embedded_view.when_failed(self._preview_web_failed)
                self.embedded_view.when_ready(self._preview_web_ready)
                LOGGER.info("Embedded tkwry/WebView2 creation requested (explicit writable UDF)")
            except Exception as exc:
                log_exception(LOGGER,"embedded WebView2 init",exc)
                self.embedded_view=None
                self._set_preview_fallback(self._msg(
                    "Preview 3D embutido indisponível.\nVeja o log para o erro do WebView2.",
                    "Embedded 3D preview unavailable.\nSee the log for the WebView2 error.",
                ))
                self.preview_status_var.set(self._msg(
                    f"WebView2 indisponível: {type(exc).__name__}: {exc}",
                    f"WebView2 unavailable: {type(exc).__name__}: {self._localize_error_text(exc)}",
                ))

    def _preview_web_ready(self, *_):
            LOGGER.info("Embedded WebView2 ready")
            self._release_viewer_focus_after_navigation()
            self.webview_retry_count = 0
            self.webview_data_dir = self._prepare_webview2_profile()
            self._hide_preview_loading()
            if self.current_id:
                self.refresh_preview()
            else:
                self.preview_status_var.set(self._msg("Preview 3D pronto. Selecione uma unidade/doodad.", "3D preview ready. Select a unit/doodad."))

    def _set_preview_fallback(self, text: str):
            if text is None:
                text = self._msg("Carregando preview 3D…", "Loading 3D preview…")
            try:
                self.preview_fallback.configure(text=text)
                self.preview_fallback.place(relx=.5,rely=.5,anchor="center")
                self.preview_fallback.lift()
            except Exception:
                pass

    def _preview_web_failed(self, exc):
            log_exception(LOGGER,"embedded WebView2 create",exc)
            # One automatic retry gets a second per-process UDF.  The retry is not
            # meant to hide errors: both paths are logged so Access Denied remains
            # diagnosable.
            if self.webview_retry_count < 1:
                self.webview_retry_count += 1
                LOGGER.warning("WebView2 creation failed; retrying with a fresh writable UDF")
                try:
                    if self.embedded_view is not None:
                        self.embedded_view.destroy()
                except Exception:
                    LOGGER.debug("WebView destroy during retry failed", exc_info=True)
                self.embedded_view = None
                self.web_session = None
                self._set_preview_fallback(self._msg("WebView2 falhou; tentando novamente com outra pasta gravável…", "WebView2 failed; retrying with another writable folder…"))
                self.after(650, lambda: self._init_embedded_viewer(force=True))
                return
            self.embedded_view = None
            self._set_preview_fallback(self._msg(
                "WebView2 não abriu.\nO modelo foi resolvido, mas o preview 3D embutido falhou.\nVeja o log do WebView2.",
                "WebView2 did not open.\nThe model was resolved, but the embedded 3D preview failed.\nSee the WebView2 log.",
            ))
            self.preview_status_var.set(self._msg(
                f"Falha no WebView2: {exc}. Modelo/ícone continuam sendo resolvidos pelo editor.",
                f"WebView2 failed: {self._localize_error_text(exc)}. Model/icon resolution is still available in the editor.",
            ))

    def _load_preview_url(self,url: str):
            if self.embedded_view is None:
                return False
            previous=getattr(self,"preview_last_url",None)
            if previous==url:
                LOGGER.debug("Viewer navigation skipped: URL/session cache hit")
                return True
            same_document=bool(previous and previous.split("#",1)[0]==url.split("#",1)[0])
            started=time.perf_counter()
            try:
                self.embedded_view.load_url(url)
                self.preview_last_url=url
                elapsed=(time.perf_counter()-started)*1000.0
                if same_document:
                    # Hash-only hot swap keeps the HTML/JS/WebGL document alive.
                    # It normally does not steal keyboard focus, so avoid the three
                    # delayed focus callbacks used for a real WebView navigation.
                    LOGGER.debug("Viewer hot-swap requested in %.1f ms (same document / hash change)",elapsed)
                    self.after(20,lambda:self._return_keyboard_to_tk(None,reason="viewer-hot-swap"))
                else:
                    LOGGER.info("Viewer document navigation requested in %.1f ms",elapsed)
                    self._release_viewer_focus_after_navigation()
                return True
            except Exception as exc:
                log_exception(LOGGER,"embedded preview load_url",exc)
                self.preview_status_var.set(self._msg(
                    f"Falha ao atualizar preview: {exc}",
                    f"Failed to update preview: {self._localize_error_text(exc)}",
                ))
                return False

    def _clear_preview_view(self):
            if self.embedded_view is not None:
                try:
                    self._load_preview_url(prepare_viewer_url(compact=True))
                except Exception:
                    LOGGER.debug("Could not clear embedded preview",exc_info=True)
