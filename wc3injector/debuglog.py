from __future__ import annotations

import ctypes
import faulthandler
import logging
import os
import platform
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

LOGGER_NAME = "wc3studio"

_FATAL_LOG_HANDLE = None

def process_resource_snapshot() -> dict[str, int | str]:
    """Best-effort process resource counters for diagnosing native Windows UI crashes.

    ``Failed to create the menu window`` is emitted below Python/Tk, so a normal
    traceback is often impossible.  USER/GDI/handle counters immediately before
    the failure are much more useful than a generic exit code.
    """
    data: dict[str, int | str] = {"threads": threading.active_count()}
    if os.name != "nt":
        return data
    try:
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        process = kernel32.GetCurrentProcess()

        count = ctypes.c_ulong()
        kernel32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetProcessHandleCount.restype = ctypes.c_int
        if kernel32.GetProcessHandleCount(process, ctypes.byref(count)):
            data["handles"] = int(count.value)

        user32.GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetGuiResources.restype = ctypes.c_uint
        data["gdi"] = int(user32.GetGuiResources(process, 0))
        data["user"] = int(user32.GetGuiResources(process, 1))
    except Exception as exc:
        data["resource_error"] = f"{type(exc).__name__}: {exc}"
    return data


def log_resource_snapshot(logger: logging.Logger, label: str, *, level: int = logging.DEBUG) -> dict[str, int | str]:
    data = process_resource_snapshot()
    logger.log(level, "RESOURCES %s %s", label, " ".join(f"{k}={v}" for k, v in data.items()))
    return data


def install_faulthandler(logger: logging.Logger) -> Path | None:
    """Write Python/Windows fatal faults to logs/fatal_native.log when possible."""
    global _FATAL_LOG_HANDLE
    try:
        path = log_dir() / "fatal_native.log"
        _FATAL_LOG_HANDLE = open(path, "a", encoding="utf-8", buffering=1)
        _FATAL_LOG_HANDLE.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} process={os.getpid()} ===\n")
        faulthandler.enable(file=_FATAL_LOG_HANDLE, all_threads=True)
        logger.info("Faulthandler enabled: %s", path)
        return path
    except Exception as exc:
        logger.warning("Could not enable faulthandler: %s", exc)
        return None


def app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def log_dir() -> Path:
    p = app_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def latest_log_path() -> Path:
    return log_dir() / "latest.log"


def configure_logging() -> tuple[logging.Logger, Path]:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Idempotent when app.py is imported more than once in tests/tools.
    if getattr(logger, "_wc3_configured", False):
        return logger, Path(getattr(logger, "_wc3_log_path", latest_log_path()))

    logs = log_dir()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    session_path = logs / f"wc3_object_studio_{stamp}.log"
    latest = latest_log_path()

    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # UTF-8 file log with complete DEBUG output.
    fh = logging.FileHandler(session_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Live console log. run_gui.bat uses python -u so this is immediate.
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Keep latest.log as a real independent file so it also works on Windows
    # without symlink permissions.
    lfh = logging.FileHandler(latest, mode="w", encoding="utf-8")
    lfh.setLevel(logging.DEBUG)
    lfh.setFormatter(fmt)
    logger.addHandler(lfh)

    logger._wc3_configured = True
    logger._wc3_log_path = str(session_path)

    logger.info("=" * 78)
    logger.info("WC3 Reforged Object Studio debug session started")
    logger.info("Session log : %s", session_path)
    logger.info("Latest log  : %s", latest)
    logger.info("Python      : %s", sys.version.replace("\n", " "))
    logger.info("Executable  : %s", sys.executable)
    logger.info("Platform    : %s", platform.platform())
    logger.info("Architecture: %s / pointer=%d-bit", platform.machine(), 8 * __import__('struct').calcsize('P'))
    logger.info("Working dir : %s", Path.cwd())
    logger.info("App root    : %s", app_root())
    logger.info("Command     : %r", sys.argv)
    logger.info("STORMLIB_DLL: %s", os.getenv("STORMLIB_DLL", "<not set>"))
    logger.info("=" * 78)

    return logger, session_path


def install_exception_hooks(logger: logging.Logger) -> None:
    def main_hook(exc_type, exc_value, exc_tb):
        logger.critical(
            "UNHANDLED MAIN-THREAD EXCEPTION\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        # Preserve normal stderr behavior too.
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = main_hook

    if hasattr(threading, "excepthook"):
        def thread_hook(args):
            logger.critical(
                "UNHANDLED THREAD EXCEPTION in %s\n%s",
                getattr(args.thread, "name", "<unknown>"),
                "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
            )
        threading.excepthook = thread_hook


def log_exception(logger: logging.Logger, context: str, exc: BaseException) -> None:
    logger.error("%s: %s: %s", context, type(exc).__name__, exc)
    logger.debug("FULL TRACEBACK for %s\n%s", context, traceback.format_exc())


def copy_latest_to_clipboard_text() -> str:
    return str(latest_log_path())
