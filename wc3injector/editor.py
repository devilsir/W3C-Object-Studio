"""Backward-compatible imports for the map edit session.

New code should import from :mod:`wc3injector.core.map_edit_session`.
"""

from .core.map_edit_session import EditSession, EditorSaveReport
from .core.map_integrity import OBJECT_FILES, EDITOR_PROTECTED

__all__ = ["EditSession", "EditorSaveReport", "OBJECT_FILES", "EDITOR_PROTECTED"]
