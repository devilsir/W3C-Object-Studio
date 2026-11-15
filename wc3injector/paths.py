from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
VIEWER_DIR = PROJECT_ROOT / "viewer"
NATIVE_DATA_DIR = PROJECT_ROOT / "native_data"
LOGS_DIR = PROJECT_ROOT / "logs"
