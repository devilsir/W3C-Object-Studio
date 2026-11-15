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


class WebViewFocusMixin:
    def _prepare_webview2_profile(self) -> Path:
            profile = prepare_webview2_profile()
            LOGGER.info("WebView2 UDF prepared: %s", profile)
            LOGGER.info("WEBVIEW2_USER_DATA_FOLDER=%s", os.environ.get("WEBVIEW2_USER_DATA_FOLDER"))
            return profile

    def _install_keyboard_focus_bridge(self):
            """Return keyboard ownership from the native WebView2 child to Tk inputs.

            WebView2 is a real child HWND. On some Windows/Python/Tk combinations it
            can keep the keyboard focus even after the user clicks a ttk.Entry.
            tkwry exposes ``focus_parent()`` specifically for handing focus back to
            Tk; wire it to every editable Tk widget class, including widgets that
            are created later when tabs/dialogs are rebuilt.
            """
            classes = ("TEntry", "Entry", "TSpinbox", "Spinbox", "TCombobox", "Text")
            for cls in classes:
                try:
                    self.bind_class(cls, "<ButtonPress-1>", self._tk_editable_clicked, add="+")
                    self.bind_class(cls, "<FocusIn>", self._tk_editable_focused, add="+")
                except tk.TclError:
                    LOGGER.debug("Focus bridge: class not available: %s", cls)
            LOGGER.info("Keyboard focus bridge installed for Tk editable widgets")

    def _tk_editable_clicked(self, event):
            # Run after Tk's own class binding has selected the entry/caret.
            widget = getattr(event, "widget", None)
            if widget is not None:
                self.after_idle(lambda w=widget: self._return_keyboard_to_tk(w, reason="mouse"))

    def _tk_editable_focused(self, event):
            widget = getattr(event, "widget", None)
            if widget is not None:
                self.after_idle(lambda w=widget: self._return_keyboard_to_tk(w, reason="focus-in"))

    def _return_keyboard_to_tk(self, widget=None, *, reason: str = "manual"):
            """Relinquish WebView2 keyboard focus and restore the requested Tk widget."""
            if self._focus_bridge_active:
                return
            self._focus_bridge_active = True
            try:
                web = self.embedded_view
                if web is not None:
                    try:
                        web.focus_parent()
                        LOGGER.debug("Focus bridge: WebView2 -> Tk (%s)", reason)
                    except Exception:
                        # A not-yet-ready/destroyed webview must never make the editor
                        # inputs unusable. Tk focus restoration still runs below.
                        LOGGER.debug("Focus bridge: focus_parent failed (%s)", reason, exc_info=True)
                if widget is not None:
                    try:
                        if int(widget.winfo_exists()):
                            widget.focus_set()
                            # On the affected Windows setup focus_set() may update Tcl
                            # while the native child HWND still owns keyboard input.
                            # focus_force() is safe here because this only runs after
                            # an explicit click/focus on an editable control.
                            if self.focus_get() is not widget:
                                widget.focus_force()
                            LOGGER.debug("Focus bridge target=%s class=%s focus=%s", widget, widget.winfo_class(), self.focus_get())
                    except tk.TclError:
                        pass
            finally:
                self._focus_bridge_active = False

    def _release_viewer_focus_after_navigation(self):
            # WebView2 can reclaim focus during/after navigation. A couple of
            # deferred releases keep normal typing in the Object Editor without
            # disabling mouse interaction inside the 3D viewport.
            for delay in (0, 80, 220):
                self.after(delay, lambda d=delay: self._return_keyboard_to_tk(None, reason=f"viewer-navigation-{d}ms"))
