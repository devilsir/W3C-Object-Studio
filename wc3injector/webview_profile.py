from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import MutableMapping


def prepare_webview2_profile(
    *,
    pid: int | None = None,
    environ: MutableMapping[str, str] | None = None,
    base_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Create a writable, per-process WebView2 user-data directory.

    WebView2's default UDF is derived from the host executable.  When a Python
    script is launched by a system-wide interpreter, that can resolve beside
    ``python.exe`` and fail with E_ACCESSDENIED.  Keeping the UDF under the
    user's local app-data directory avoids that and also isolates concurrent
    Object Studio processes from each other's browser profile locks.
    """
    env = os.environ if environ is None else environ
    if base_dir is not None:
        base = Path(base_dir)
    elif os.name == "nt":
        base = Path(env.get("LOCALAPPDATA") or env.get("APPDATA") or tempfile.gettempdir())
    else:
        base = Path(tempfile.gettempdir())

    process_id = os.getpid() if pid is None else int(pid)
    profile = base / "WC3_Reforged_Object_Studio" / "WebView2" / f"session-{process_id}"
    profile.mkdir(parents=True, exist_ok=True)

    # Do a real write rather than assuming mkdir success means WebView2 can use
    # the directory.  Antivirus / Controlled Folder Access can make those differ.
    probe = profile / ".write_probe"
    probe.write_text("ok", encoding="ascii")
    probe.unlink(missing_ok=True)

    env["WEBVIEW2_USER_DATA_FOLDER"] = str(profile)
    return profile
