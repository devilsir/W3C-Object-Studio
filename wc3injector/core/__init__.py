"""Core map-editing services, independent from the Tkinter UI."""

from .map_edit_session import EditSession, EditorSaveReport

__all__ = ["EditSession", "EditorSaveReport"]
